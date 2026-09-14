from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from services.account_service import AccountService
from services.database import ANONYMOUS_OWNER_ID, Database
from services.user_data_repository import (
    InvalidSessionId,
    MAX_SNAPSHOT_BYTES,
    SessionNotFound,
    SnapshotTooLarge,
    UserDataRepository,
)


class SessionRepositoryTests(unittest.TestCase):
    """会话快照仓库：摘要字段、排序、输入校验与跨账号隔离。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.accounts = AccountService(self.database)
        self.repository = UserDataRepository(self.database)
        self.owner = ANONYMOUS_OWNER_ID

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_save_list_get_and_delete(self) -> None:
        self.repository.save(
            self.owner,
            "session_1",
            {
                "session_id": "session_1",
                "mode": "course",
                "title": "智能体课程",
                "selected_node": {"id": "node_2", "name": "感知模块"},
                "step_progress": {"current": 2, "total": 4},
                "average_mastery": 0.5,
            },
        )
        snapshot = self.repository.get(self.owner, "session_1")
        self.assertEqual(snapshot["title"], "智能体课程")

        summary = self.repository.list(self.owner, limit=1)[0]
        self.assertEqual(summary["last_node_name"], "感知模块")
        self.assertEqual(summary["last_node_id"], "node_2")
        self.assertEqual(summary["current_step"], 2)
        self.assertEqual(summary["total_steps"], 4)
        self.assertAlmostEqual(summary["average_mastery"], 0.5)

        self.repository.delete(self.owner, "session_1")
        with self.assertRaises(SessionNotFound):
            self.repository.get(self.owner, "session_1")

    def test_latest_update_is_first(self) -> None:
        self.repository.save(self.owner, "older", {"session_id": "older", "title": "旧"})
        self.repository.save(self.owner, "newer", {"session_id": "newer", "title": "新"})
        self.assertEqual(
            self.repository.list(self.owner)[0]["session_id"], "newer"
        )

    def test_invalid_id_is_rejected(self) -> None:
        for bad in ("../escape", "", "a" * 129, "has space", "sql'inject"):
            with self.subTest(session_id=bad):
                with self.assertRaises(InvalidSessionId):
                    self.repository.save(self.owner, bad, {"session_id": bad})

    def test_oversized_snapshot_is_rejected(self) -> None:
        with self.assertRaises(SnapshotTooLarge):
            self.repository.save(
                self.owner,
                "huge",
                {"session_id": "huge", "blob": "x" * (MAX_SNAPSHOT_BYTES + 1)},
            )

    def test_missing_session_delete_raises(self) -> None:
        with self.assertRaises(SessionNotFound):
            self.repository.delete(self.owner, "never_saved")

    def test_untitled_snapshot_falls_back_to_internal_topic(self) -> None:
        self.repository.save(
            self.owner,
            "fallback",
            {"session_id": "fallback", "internal_topic": "递归入门"},
        )
        self.assertEqual(
            self.repository.list(self.owner)[0]["title"], "递归入门"
        )

    def test_malformed_progress_does_not_break_the_summary(self) -> None:
        self.repository.save(
            self.owner,
            "messy",
            {
                "session_id": "messy",
                "title": "脏数据",
                "selected_node": "not-an-object",
                "step_progress": {"current": "三", "total": None},
                "average_mastery": "不是数字",
            },
        )
        summary = self.repository.list(self.owner)[0]
        self.assertIsNone(summary["current_step"])
        self.assertIsNone(summary["total_steps"])
        self.assertIsNone(summary["last_node_name"])
        self.assertEqual(summary["average_mastery"], 0.0)

    def test_course_identity_survives_snapshot_update_and_summary(self) -> None:
        first = self.repository.save(
            self.owner,
            "course_session",
            {
                "session_id": "course_session",
                "mode": "course",
                "title": "Agent 开发工程师",
                "course_id": "agent-engineering",
                "course_title": "Agent 开发工程师",
            },
        )
        updated = self.repository.save(
            self.owner,
            "course_session",
            {**first, "selected_node": {"id": "agent-loop", "name": "Agent 执行循环"}},
        )
        self.assertEqual(updated["course_id"], "agent-engineering")
        self.assertEqual(updated["course_title"], "Agent 开发工程师")
        summary = self.repository.list(self.owner)[0]
        self.assertEqual(summary["course_id"], "agent-engineering")
        self.assertEqual(summary["course_title"], "Agent 开发工程师")

    def test_created_at_is_preserved_across_updates(self) -> None:
        first = self.repository.save(
            self.owner, "stable", {"session_id": "stable", "title": "一"}
        )
        second = self.repository.save(
            self.owner, "stable", {"session_id": "stable", "title": "二"}
        )
        self.assertEqual(first["created_at"], second["created_at"])

    # ------------------------------------------------------------------
    # 跨账号隔离
    # ------------------------------------------------------------------
    def test_owners_cannot_see_or_touch_each_others_sessions(self) -> None:
        alice = self.accounts.register("alice", "correct-horse-1")
        bob = self.accounts.register("bob", "correct-horse-2")

        self.repository.save(
            alice.id, "shared_id", {"session_id": "shared_id", "title": "Alice 的"}
        )
        self.repository.save(
            bob.id, "shared_id", {"session_id": "shared_id", "title": "Bob 的"}
        )

        # 同一个 session_id 在两个账号下是两条互不干扰的记录
        self.assertEqual(
            self.repository.get(alice.id, "shared_id")["title"], "Alice 的"
        )
        self.assertEqual(self.repository.get(bob.id, "shared_id")["title"], "Bob 的")
        self.assertEqual(self.repository.count(alice.id), 1)

        # 访客看不到任何真实账号的会话
        self.assertEqual(self.repository.list(ANONYMOUS_OWNER_ID), [])

        # 删除只作用于自己那一行
        self.repository.delete(alice.id, "shared_id")
        with self.assertRaises(SessionNotFound):
            self.repository.get(alice.id, "shared_id")
        self.assertEqual(self.repository.get(bob.id, "shared_id")["title"], "Bob 的")

    def test_sessions_are_removed_with_the_account(self) -> None:
        carol = self.accounts.register("carol", "correct-horse-3")
        self.repository.save(carol.id, "s1", {"session_id": "s1", "title": "会话"})
        self.accounts.delete_user(carol.id)
        self.assertEqual(self.repository.count(carol.id), 0)


if __name__ == "__main__":
    unittest.main()
