"""按账号隔离的学习数据存储（星图、学习者状态、文档上下文）。

原先这些数据以 JSON 文件的形式散落在 ``test_data/`` 下，只用 topic 做键，
因此所有账号共享同一份状态：任何人都能读写别人的星图。这里把它们统一收进
SQLite，主键一律带上 ``owner_id``，并通过外键随账号级联删除。

写入使用单行 UPSERT，因此不再有"写一半被打断留下半个 JSON 文件"的问题。
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from services.database import (
    ANONYMOUS_OWNER_ID,
    Database,
    default_database,
    utc_now,
)


# NOTE: 单行体积上限。星图与文档来自外部输入，必须挡在写入之前。
MAX_GRAPH_BYTES = 4 * 1024 * 1024
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024

# 原始上传文件的根目录，其下按账号分子目录。
UPLOAD_ROOT = Path("user_data") / "uploads"

# owner_id 与 doc_id 都会被拼进上传目录路径，必须先收敛成不含分隔符、
# 不含 ".." 的安全片段，否则一个 doc_id=../<别人的id>/<hash> 就能删掉别人的文件。
OWNER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DOC_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class PayloadTooLarge(ValueError):
    """存储的单行 JSON 超出预算。"""


class OwnerRequired(ValueError):
    """调用方没有给出合法的归属账号。"""


class InvalidDocumentId(ValueError):
    """文档 id 含有路径分隔符或其他不安全字符。"""


def validate_owner_id(owner_id: str | None) -> str:
    """校验归属 id，防止它被当作路径片段时逃出上传目录。"""
    candidate = (owner_id or "").strip()
    if not OWNER_ID_PATTERN.fullmatch(candidate):
        raise OwnerRequired("owner id is missing or malformed")
    return candidate


def validate_doc_id(doc_id: str | None) -> str:
    """校验文档 id。它同时是存储主键和磁盘文件名，必须两处都安全。"""
    candidate = (doc_id or "").strip()
    if not DOC_ID_PATTERN.fullmatch(candidate):
        raise InvalidDocumentId("document id is missing or malformed")
    return candidate


def _dump(payload: Any, limit: int) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text.encode("utf-8")) > limit:
        raise PayloadTooLarge(f"payload exceeds {limit} bytes")
    return text


class LearningStore:
    """星图 / 学习者状态 / 文档上下文的账号级仓库。"""

    def __init__(self, database: Database | None = None) -> None:
        self.database = database or default_database

    # ------------------------------------------------------------------
    # 星图
    # ------------------------------------------------------------------
    def read_graph(self, owner_id: str, scope_key: str) -> dict[str, Any] | None:
        owner_id = validate_owner_id(owner_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM knowledge_graphs WHERE owner_id = ? AND scope_key = ?",
                (owner_id, scope_key),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["payload"])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def write_graph(
        self,
        owner_id: str,
        scope_key: str,
        payload: dict[str, Any],
        *,
        topic: str = "",
        course_id: str | None = None,
    ) -> None:
        owner_id = validate_owner_id(owner_id)
        encoded = _dump(payload, MAX_GRAPH_BYTES)
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_graphs (
                    owner_id, scope_key, topic, course_id, payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, scope_key) DO UPDATE SET
                    topic = excluded.topic,
                    course_id = excluded.course_id,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (owner_id, scope_key, topic[:200], course_id, encoded, now, now),
            )

    def delete_graph(self, owner_id: str, scope_key: str) -> bool:
        owner_id = validate_owner_id(owner_id)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM knowledge_graphs WHERE owner_id = ? AND scope_key = ?",
                (owner_id, scope_key),
            )
            return cursor.rowcount > 0

    def list_graphs(self, owner_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        owner_id = validate_owner_id(owner_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT scope_key, topic, course_id, created_at, updated_at
                  FROM knowledge_graphs
                 WHERE owner_id = ?
                 ORDER BY updated_at DESC
                 LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # 学习者状态
    # ------------------------------------------------------------------
    def read_learner_state(self, owner_id: str, scope_key: str) -> dict[str, Any]:
        owner_id = validate_owner_id(owner_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM learner_states WHERE owner_id = ? AND scope_key = ?",
                (owner_id, scope_key),
            ).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(row["payload"])
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def write_learner_state(
        self, owner_id: str, scope_key: str, payload: dict[str, Any]
    ) -> None:
        owner_id = validate_owner_id(owner_id)
        encoded = _dump(payload, MAX_STATE_BYTES)
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO learner_states (
                    owner_id, scope_key, payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, scope_key) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (owner_id, scope_key, encoded, now, now),
            )

    def delete_learner_state(self, owner_id: str, scope_key: str) -> bool:
        owner_id = validate_owner_id(owner_id)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM learner_states WHERE owner_id = ? AND scope_key = ?",
                (owner_id, scope_key),
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # 文档上下文
    # ------------------------------------------------------------------
    def read_document(self, owner_id: str, doc_id: str) -> dict[str, Any] | None:
        owner_id = validate_owner_id(owner_id)
        doc_id = validate_doc_id(doc_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM documents WHERE owner_id = ? AND doc_id = ?",
                (owner_id, doc_id),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["payload"])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def write_document(
        self,
        owner_id: str,
        doc_id: str,
        payload: dict[str, Any],
        *,
        filename: str = "",
        total_pages: int = 0,
        chunk_count: int = 0,
    ) -> None:
        owner_id = validate_owner_id(owner_id)
        doc_id = validate_doc_id(doc_id)
        encoded = _dump(payload, MAX_DOCUMENT_BYTES)
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                    owner_id, doc_id, filename, total_pages, chunk_count,
                    payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, doc_id) DO UPDATE SET
                    filename = excluded.filename,
                    total_pages = excluded.total_pages,
                    chunk_count = excluded.chunk_count,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    owner_id,
                    doc_id,
                    filename[:255],
                    int(total_pages),
                    int(chunk_count),
                    encoded,
                    now,
                    now,
                ),
            )

    def delete_document(self, owner_id: str, doc_id: str) -> bool:
        owner_id = validate_owner_id(owner_id)
        doc_id = validate_doc_id(doc_id)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM documents WHERE owner_id = ? AND doc_id = ?",
                (owner_id, doc_id),
            )
            return cursor.rowcount > 0

    def list_documents(self, owner_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        owner_id = validate_owner_id(owner_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT doc_id, filename, total_pages, chunk_count, created_at, updated_at
                  FROM documents
                 WHERE owner_id = ?
                 ORDER BY updated_at DESC
                 LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]


class SqlLearnerStateStore:
    """把 :class:`~core.learner_state.LearnerState` 的持久化接到 SQLite 上。

    :class:`LearnerState` 只要求 ``read()`` / ``write()``，因此那 20 多处
    ``_auto_save()`` 调用点一行都不用改。
    """

    def __init__(
        self,
        owner_id: str,
        scope_key: str,
        store: LearningStore | None = None,
    ) -> None:
        self.owner_id = validate_owner_id(owner_id)
        self.scope_key = scope_key
        self.store = store or learning_store

    def read(self) -> dict[str, Any]:
        return self.store.read_learner_state(self.owner_id, self.scope_key)

    def write(self, data: dict[str, Any]) -> None:
        self.store.write_learner_state(self.owner_id, self.scope_key, data)

    def __repr__(self) -> str:  # pragma: no cover - 仅用于日志可读性
        return f"SqlLearnerStateStore(owner={self.owner_id}, scope={self.scope_key})"


def owner_upload_dir(owner_id: str, root: Path) -> Path:
    """返回某个账号的上传目录，并确认它没有逃出根目录。"""
    owner_id = validate_owner_id(owner_id)
    resolved_root = root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    target = (resolved_root / owner_id).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:  # pragma: no cover - OWNER_ID_PATTERN 已挡住
        raise OwnerRequired("owner upload path escapes the upload root") from exc
    return target


def purge_owner_uploads(owner_id: str, root: Path) -> int:
    """删掉某个账号上传目录下的全部文件，返回删除的文件数。

    数据库里的行随外键级联清理，磁盘上的原始 PDF 不会 —— 删号后它们会变成
    没有任何行指向、也没有任何接口能访问的孤儿文件，一直占着空间。
    """
    owner_id = validate_owner_id(owner_id)
    resolved_root = root.resolve() if root.exists() else root
    directory = resolved_root / owner_id
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.iterdir():
        if path.is_file():
            try:
                path.unlink()
                removed += 1
            except OSError:  # pragma: no cover - 权限问题不该挡住删号
                pass
    try:
        directory.rmdir()
    except OSError:  # pragma: no cover - 目录非空时保留
        pass
    return removed


def owner_upload_path(owner_id: str, doc_id: str, root: Path, suffix: str = ".pdf") -> Path:
    """某个账号名下某份文档的磁盘路径。

    这是拼接上传路径的**唯一**入口：owner_id 与 doc_id 都在这里过一遍白名单，
    最后再确认结果确实落在该账号自己的目录内。少了 doc_id 那一步，
    ``doc_id=../<别人的账号 id>/<hash>`` 就能读写、删除别人的文件。
    """
    directory = owner_upload_dir(owner_id, root)
    target = (directory / f"{validate_doc_id(doc_id)}{suffix}").resolve()
    try:
        target.relative_to(directory)
    except ValueError as exc:  # pragma: no cover - DOC_ID_PATTERN 已挡住
        raise InvalidDocumentId("document path escapes the owner directory") from exc
    return target


# 全局默认学习数据仓库（复用默认数据库实例）
learning_store = LearningStore()

__all__ = [
    "ANONYMOUS_OWNER_ID",
    "UPLOAD_ROOT",
    "InvalidDocumentId",
    "LearningStore",
    "MAX_DOCUMENT_BYTES",
    "MAX_GRAPH_BYTES",
    "MAX_STATE_BYTES",
    "OwnerRequired",
    "PayloadTooLarge",
    "SqlLearnerStateStore",
    "learning_store",
    "owner_upload_dir",
    "owner_upload_path",
    "purge_owner_uploads",
    "validate_doc_id",
    "validate_owner_id",
]
