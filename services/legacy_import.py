"""把改造前留在磁盘上的 JSON 数据一次性导入 SQLite。

改造之前，学习数据以文件形式散落在两处，且**没有任何归属信息**：

```text
user_data/sessions/<session_id>.json          学习会话快照
user_data/sessions/index.json                 首页历史索引（可由快照重建，不导入）
test_data/knowledge_graph_<scope>.json        星图
test_data/learner_state.json                  默认学习者状态
test_data/learner_state_<scope>.json          按主题隔离的学习者状态
test_data/uploads/<doc_id>_context.json       解析后的 PDF 上下文
test_data/uploads/<doc_id>.pdf                原始 PDF
```

既然这些数据本来就不属于任何账号，就只能导入到预留的访客账号下 —— 把它们
分给某个真实账号是猜测，而猜错等于把一个人的学习记录送给另一个人。导入后
访客仍可看到全部旧数据，需要归属到自己名下的用户可以再手工另存。

文件名里的 ``<scope>`` 正是当年 ``LearningService._scoped_topic()`` 的输出，
因此存储键可以逐字重建，不需要反推原始 topic。

导入完成后源目录会被改名（加 ``.imported`` 后缀），因此这件事只会做一次；
任何一个文件出问题都只跳过它自己，不会中断整体导入。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from typing import Any

from services.database import ANONYMOUS_OWNER_ID, Database, default_database
from services.learning_store import (
    LearningStore,
    PayloadTooLarge,
    owner_upload_dir,
)
from services.user_data_repository import (
    InvalidSessionId,
    SnapshotTooLarge,
    UserDataRepository,
)


logger = logging.getLogger(__name__)

LEGACY_SESSION_DIR = Path("user_data") / "sessions"
LEGACY_DATA_DIR = Path("test_data")
LEGACY_UPLOAD_DIR = LEGACY_DATA_DIR / "uploads"
UPLOAD_ROOT = Path("user_data") / "uploads"

IMPORTED_SUFFIX = ".imported"


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("跳过无法解析的旧数据文件 %s: %s", path, exc)
        return None


def _retire(directory: Path) -> None:
    """把导入过的目录改名，避免重复导入，同时保留原始文件以备核对。"""
    if not directory.exists():
        return
    target = directory.with_name(directory.name + IMPORTED_SUFFIX)
    if target.exists():
        # 之前已经导入过一次；这次的源目录换个带序号的名字，别覆盖上一份。
        index = 2
        while target.with_name(f"{target.name}{index}").exists():
            index += 1
        target = target.with_name(f"{target.name}{index}")
    try:
        directory.rename(target)
        logger.info("旧数据目录已改名: %s -> %s", directory, target)
    except OSError as exc:
        logger.warning("旧数据目录改名失败 %s: %s", directory, exc)


def import_legacy_sessions(
    repository: UserDataRepository, root: Path = LEGACY_SESSION_DIR
) -> int:
    if not root.is_dir():
        return 0
    imported = 0
    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue  # 索引可以由快照重建，没有导入价值
        snapshot = _read_json(path)
        if not isinstance(snapshot, dict):
            continue
        session_id = snapshot.get("session_id") or path.stem
        try:
            repository.save(ANONYMOUS_OWNER_ID, session_id, snapshot)
        except (InvalidSessionId, SnapshotTooLarge) as exc:
            logger.warning("跳过会话 %s: %s", path.name, exc)
            continue
        imported += 1
    return imported


def import_legacy_learning_data(
    store: LearningStore, root: Path = LEGACY_DATA_DIR
) -> tuple[int, int]:
    """导入星图与学习者状态，返回 (星图数, 状态数)。"""
    if not root.is_dir():
        return (0, 0)

    graphs = 0
    for path in sorted(root.glob("knowledge_graph_*.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        # 文件名尾部就是当年的 scoped_topic，直接拼回存储键。
        scope = path.stem[len("knowledge_graph_") :]
        if not scope:
            continue
        try:
            store.write_graph(ANONYMOUS_OWNER_ID, f"graph:{scope}", payload)
        except PayloadTooLarge as exc:
            logger.warning("跳过过大的星图 %s: %s", path.name, exc)
            continue
        graphs += 1

    states = 0
    for path in sorted(root.glob("learner_state*.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        stem = path.stem
        if stem == "learner_state":
            scope = "state:default"
        elif stem.startswith("learner_state_"):
            scope = f"state:{stem[len('learner_state_'):]}"
        else:
            continue
        try:
            store.write_learner_state(ANONYMOUS_OWNER_ID, scope, payload)
        except PayloadTooLarge as exc:
            logger.warning("跳过过大的学习者状态 %s: %s", path.name, exc)
            continue
        states += 1

    return (graphs, states)


def import_legacy_documents(
    store: LearningStore,
    root: Path = LEGACY_UPLOAD_DIR,
    upload_root: Path = UPLOAD_ROOT,
) -> int:
    if not root.is_dir():
        return 0
    target_dir = owner_upload_dir(ANONYMOUS_OWNER_ID, upload_root)
    target_dir.mkdir(parents=True, exist_ok=True)

    imported = 0
    for path in sorted(root.glob("*_context.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        doc_id = payload.get("doc_id") or path.stem[: -len("_context")]
        if not doc_id:
            continue
        try:
            store.write_document(
                ANONYMOUS_OWNER_ID,
                doc_id,
                payload,
                filename=str(payload.get("filename") or ""),
                total_pages=int(payload.get("total_pages") or 0),
                chunk_count=len(payload.get("chunks") or []),
            )
        except (PayloadTooLarge, TypeError, ValueError) as exc:
            logger.warning("跳过文档 %s: %s", path.name, exc)
            continue

        pdf = root / f"{doc_id}.pdf"
        if pdf.exists():
            try:
                shutil.copy2(pdf, target_dir / f"{doc_id}.pdf")
            except OSError as exc:
                logger.warning("原始 PDF 复制失败 %s: %s", pdf, exc)
        imported += 1
    return imported


EMPTY_SUMMARY: dict[str, int] = {
    "sessions": 0,
    "graphs": 0,
    "learner_states": 0,
    "documents": 0,
}


def has_legacy_data() -> bool:
    """是否存在待导入的旧文件。先判断再连库，免得空跑一次也去建库。"""
    if LEGACY_SESSION_DIR.is_dir() and any(
        path.name != "index.json" for path in LEGACY_SESSION_DIR.glob("*.json")
    ):
        return True
    if LEGACY_DATA_DIR.is_dir() and (
        any(LEGACY_DATA_DIR.glob("knowledge_graph_*.json"))
        or any(LEGACY_DATA_DIR.glob("learner_state*.json"))
    ):
        return True
    return LEGACY_UPLOAD_DIR.is_dir() and any(LEGACY_UPLOAD_DIR.glob("*_context.json"))


def import_legacy_data(
    database: Database | None = None, *, retire_sources: bool = True
) -> dict[str, int]:
    """把所有旧文件导入访客空间，返回各类数据的导入条数。"""
    if not has_legacy_data():
        return dict(EMPTY_SUMMARY)
    database = database or default_database
    database.initialize()
    store = LearningStore(database)
    repository = UserDataRepository(database)

    sessions = import_legacy_sessions(repository)
    graphs, states = import_legacy_learning_data(store)
    documents = import_legacy_documents(store)

    summary = {
        "sessions": sessions,
        "graphs": graphs,
        "learner_states": states,
        "documents": documents,
    }
    if retire_sources and any(summary.values()):
        _retire(LEGACY_SESSION_DIR)
        _retire(LEGACY_UPLOAD_DIR)
        # test_data 根目录还放着课程等非学习数据，只清掉导入过的文件本身。
        for pattern in ("knowledge_graph_*.json", "learner_state*.json"):
            for path in LEGACY_DATA_DIR.glob(pattern):
                try:
                    path.rename(path.with_suffix(f".json{IMPORTED_SUFFIX}"))
                except OSError as exc:  # pragma: no cover - 只影响清理
                    logger.warning("旧文件改名失败 %s: %s", path, exc)
    return summary


def main() -> None:  # pragma: no cover - 命令行入口
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    summary = import_legacy_data()
    if not any(summary.values()):
        print("没有找到需要导入的旧数据。")
        return
    print(
        "已导入到访客空间："
        f"会话 {summary['sessions']} 条、"
        f"星图 {summary['graphs']} 个、"
        f"学习状态 {summary['learner_states']} 份、"
        f"文档 {summary['documents']} 个。"
    )
    print("源文件已加 .imported 后缀保留，确认无误后可自行删除。")


if __name__ == "__main__":  # pragma: no cover
    main()
