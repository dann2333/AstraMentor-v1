"""旧 JSON 数据导入：键要拼对，坏文件要跳过，源目录要退休。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from services.database import ANONYMOUS_OWNER_ID, Database
from services.learning_store import LearningStore
from services.legacy_import import (
    IMPORTED_SUFFIX,
    import_legacy_data,
)
from services.user_data_repository import SessionNotFound, UserDataRepository


class LegacyImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # 导入器用的是相对路径（user_data/、test_data/），切到临时目录里跑。
        self.previous_cwd = Path.cwd()
        os.chdir(self.root)

        self.database = Database(self.root / "user_data" / "astramentor.db")
        self.store = LearningStore(self.database)
        self.repository = UserDataRepository(self.database)

        self.sessions_dir = self.root / "user_data" / "sessions"
        self.data_dir = self.root / "test_data"
        self.uploads_dir = self.data_dir / "uploads"
        for directory in (self.sessions_dir, self.uploads_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        os.chdir(self.previous_cwd)
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _seed(self) -> None:
        self._write(
            self.sessions_dir / "old_sess.json",
            {
                "session_id": "old_sess",
                "title": "我以前的学习",
                "mode": "course",
                "course_id": "agent-engineering",
                "selected_node": {"id": "n1", "name": "感知模块"},
                "step_progress": {"current": 2, "total": 4},
                "average_mastery": 0.5,
                "updated_at": "2025-01-02T00:00:00+00:00",
            },
        )
        # 索引可由快照重建，不该被当成一条会话导进来
        self._write(self.sessions_dir / "index.json", {"schema_version": 1, "sessions": []})

        self._write(
            self.data_dir / "knowledge_graph_agent-engineering_abc123.json",
            {"nodes": [{"id": "n1"}], "links": []},
        )
        self._write(self.data_dir / "learner_state.json", {"递归": {"name": "递归"}})
        self._write(
            self.data_dir / "learner_state_agent-engineering_abc123.json",
            {"感知模块": {"name": "感知模块", "actual_mastery": 0.6}},
        )

        self._write(
            self.uploads_dir / "deadbeef_context.json",
            {
                "doc_id": "deadbeef",
                "filename": "教材.pdf",
                "total_pages": 12,
                "chunks": [{"chunk_id": "c1", "content": "x", "page_start": 1, "page_end": 1}],
                "full_text": "x",
            },
        )
        (self.uploads_dir / "deadbeef.pdf").write_bytes(b"%PDF-1.4 legacy")

    # ------------------------------------------------------------------
    def test_imports_everything_into_the_guest_space(self) -> None:
        self._seed()
        summary = import_legacy_data(self.database)
        self.assertEqual(
            summary,
            {"sessions": 1, "graphs": 1, "learner_states": 2, "documents": 1},
        )

        session = self.repository.get(ANONYMOUS_OWNER_ID, "old_sess")
        self.assertEqual(session["title"], "我以前的学习")
        summary_row = self.repository.list(ANONYMOUS_OWNER_ID)[0]
        self.assertEqual(summary_row["last_node_name"], "感知模块")
        self.assertEqual(summary_row["current_step"], 2)

        # 存储键必须与 LearningService 现在生成的一致，否则导进来也读不到
        self.assertEqual(
            self.store.read_graph(
                ANONYMOUS_OWNER_ID, "graph:agent-engineering_abc123"
            ),
            {"nodes": [{"id": "n1"}], "links": []},
        )
        self.assertEqual(
            self.store.read_learner_state(ANONYMOUS_OWNER_ID, "state:default"),
            {"递归": {"name": "递归"}},
        )
        self.assertIn(
            "感知模块",
            self.store.read_learner_state(
                ANONYMOUS_OWNER_ID, "state:agent-engineering_abc123"
            ),
        )

        document = self.store.read_document(ANONYMOUS_OWNER_ID, "deadbeef")
        self.assertEqual(document["filename"], "教材.pdf")
        self.assertTrue(
            (self.root / "user_data" / "uploads" / ANONYMOUS_OWNER_ID / "deadbeef.pdf").exists()
        )

    def test_imported_keys_match_what_learning_service_reads(self) -> None:
        """把导入的键和真实服务生成的键对上，而不是各写一遍字符串。"""
        from unittest.mock import Mock, patch

        from services.learning_service import LearningService

        with patch("services.learning_service.APIClient", return_value=Mock()):
            service = LearningService(
                topic="感知模块",
                course_id="agent-engineering",
                owner_id=ANONYMOUS_OWNER_ID,
                store=self.store,
            )
            scope = service._graph_scope("感知模块").removeprefix("graph:")
            self._write(
                self.data_dir / f"knowledge_graph_{scope}.json", {"nodes": [], "links": []}
            )
            import_legacy_data(self.database)
            self.assertEqual(service.load_graph("感知模块"), {"nodes": [], "links": []})

    def test_a_corrupt_file_does_not_stop_the_rest(self) -> None:
        self._seed()
        (self.sessions_dir / "broken.json").write_text("{not-json", encoding="utf-8")
        (self.data_dir / "knowledge_graph_broken.json").write_text("[]", encoding="utf-8")

        summary = import_legacy_data(self.database)
        self.assertEqual(summary["sessions"], 1)
        self.assertEqual(summary["graphs"], 1)
        self.assertEqual(self.repository.get(ANONYMOUS_OWNER_ID, "old_sess")["title"], "我以前的学习")

    def test_sources_are_retired_so_it_only_runs_once(self) -> None:
        self._seed()
        import_legacy_data(self.database)

        self.assertFalse(self.sessions_dir.exists())
        self.assertTrue(self.sessions_dir.with_name("sessions" + IMPORTED_SUFFIX).exists())
        self.assertFalse(self.uploads_dir.exists())
        self.assertEqual(list(self.data_dir.glob("knowledge_graph_*.json")), [])

        # 第二次跑是干净的空操作
        self.assertEqual(
            import_legacy_data(self.database),
            {"sessions": 0, "graphs": 0, "learner_states": 0, "documents": 0},
        )

    def test_nothing_to_import_is_not_an_error(self) -> None:
        summary = import_legacy_data(self.database)
        self.assertEqual(
            summary, {"sessions": 0, "graphs": 0, "learner_states": 0, "documents": 0}
        )
        with self.assertRaises(SessionNotFound):
            self.repository.get(ANONYMOUS_OWNER_ID, "old_sess")

    def test_import_never_touches_a_real_account(self) -> None:
        from services.account_service import AccountService

        accounts = AccountService(self.database)
        alice = accounts.register("alice1", "correct-horse-battery")
        self._seed()
        import_legacy_data(self.database)

        self.assertEqual(self.repository.list(alice.id), [])
        self.assertIsNone(
            self.store.read_graph(alice.id, "graph:agent-engineering_abc123")
        )
        self.assertIsNone(self.store.read_document(alice.id, "deadbeef"))


if __name__ == "__main__":
    unittest.main()
