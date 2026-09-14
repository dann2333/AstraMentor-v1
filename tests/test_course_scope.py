from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from services.account_service import AccountService
from services.database import ANONYMOUS_OWNER_ID, Database
from services.learning_service import LearningService
from services.learning_store import LearningStore, OwnerRequired


class CourseScopeTests(unittest.TestCase):
    """星图与学习状态的隔离维度：课程、主题、以及归属账号。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.store = LearningStore(self.database)
        self.accounts = AccountService(self.database)
        self.api_patch = patch(
            "services.learning_service.APIClient", return_value=Mock()
        )
        self.api_patch.start()

    def tearDown(self) -> None:
        self.api_patch.stop()
        self.temp.cleanup()

    def _service(self, topic: str, course_id: str = "", owner_id: str | None = None):
        return LearningService(
            topic=topic,
            course_id=course_id,
            owner_id=owner_id or ANONYMOUS_OWNER_ID,
            store=self.store,
        )

    def test_same_topic_is_isolated_by_course_for_graph_and_state(self) -> None:
        first = self._service("共享主题", "course-a")
        second = self._service("共享主题", "course-b")
        self.assertNotEqual(
            first._graph_scope("共享主题"), second._graph_scope("共享主题")
        )
        self.assertNotEqual(
            first._state_scope("共享主题"), second._state_scope("共享主题")
        )

        self.assertTrue(first.save_graph("共享主题", {"course": "A"}))
        self.assertTrue(second.save_graph("共享主题", {"course": "B"}))
        self.assertEqual(first.load_graph("共享主题"), {"course": "A"})
        self.assertEqual(second.load_graph("共享主题"), {"course": "B"})

        first.delete_graph("共享主题")
        self.assertIsNone(first.load_graph("共享主题"))
        self.assertEqual(second.load_graph("共享主题"), {"course": "B"})

    def test_same_topic_and_course_is_isolated_by_owner(self) -> None:
        alice = self.accounts.register("alice", "correct-horse-1")
        bob = self.accounts.register("bob", "correct-horse-2")
        mine = self._service("共享主题", "course-a", owner_id=alice.id)
        theirs = self._service("共享主题", "course-a", owner_id=bob.id)

        # 存储键相同 —— 隔离完全靠 owner_id，这正是要锁住的行为
        self.assertEqual(
            mine._graph_scope("共享主题"), theirs._graph_scope("共享主题")
        )

        self.assertTrue(mine.save_graph("共享主题", {"owner": "alice"}))
        self.assertTrue(theirs.save_graph("共享主题", {"owner": "bob"}))
        self.assertEqual(mine.load_graph("共享主题"), {"owner": "alice"})
        self.assertEqual(theirs.load_graph("共享主题"), {"owner": "bob"})

        # 访客既看不到也删不掉任何真实账号的星图
        guest = self._service("共享主题", "course-a")
        self.assertIsNone(guest.load_graph("共享主题"))
        guest.delete_graph("共享主题")
        self.assertEqual(mine.load_graph("共享主题"), {"owner": "alice"})

        mine.delete_graph("共享主题")
        self.assertIsNone(mine.load_graph("共享主题"))
        self.assertEqual(theirs.load_graph("共享主题"), {"owner": "bob"})

    def test_learner_state_is_isolated_by_owner(self) -> None:
        alice = self.accounts.register("alice", "correct-horse-1")
        bob = self.accounts.register("bob", "correct-horse-2")

        mine = self._service("递归", owner_id=alice.id)
        mine.learner_state.add_knowledge_point("尾递归", target_mastery=0.95)

        theirs = self._service("递归", owner_id=bob.id)
        self.assertIsNone(theirs.learner_state.get_knowledge_point("尾递归"))

        # 重新构造后仍能读回自己的状态（说明确实落库了）
        reloaded = self._service("递归", owner_id=alice.id)
        point = reloaded.learner_state.get_knowledge_point("尾递归")
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point.target_mastery, 0.95)

    def test_learning_data_is_removed_with_the_account(self) -> None:
        carol = self.accounts.register("carol", "correct-horse-3")
        service = self._service("图论", owner_id=carol.id)
        service.save_graph("图论", {"nodes": []})
        service.learner_state.add_knowledge_point("最短路")

        self.accounts.delete_user(carol.id)

        self.assertIsNone(self.store.read_graph(carol.id, service._graph_scope("图论")))
        self.assertEqual(
            self.store.read_learner_state(carol.id, service._state_scope("图论")), {}
        )

    def test_hostile_topic_stays_inside_its_own_scope_key(self) -> None:
        service = self._service("../../outside\\drive:C:", "course-a")
        scope = service._graph_scope("../../outside\\drive:C:")
        # 存储键是纯标量，不再被当作路径，也不会撞上别的 topic
        self.assertNotIn("/", scope.removeprefix("graph:"))
        self.assertTrue(service.save_graph("../../outside\\drive:C:", {"safe": True}))
        self.assertIsNone(service.load_graph("另一个主题"))
        self.assertEqual(
            service.load_graph("../../outside\\drive:C:"), {"safe": True}
        )

    def test_invalid_course_id_is_rejected_before_any_write(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid course id"):
            self._service("topic", "../escape")
        self.assertEqual(self.store.list_graphs(ANONYMOUS_OWNER_ID), [])

    def test_missing_or_malformed_owner_is_rejected(self) -> None:
        for bad in ("", "   ", "../escape", "owner/with/slash", "a" * 65):
            with self.subTest(owner_id=bad):
                with self.assertRaises(OwnerRequired):
                    LearningService(topic="topic", owner_id=bad, store=self.store)


if __name__ == "__main__":
    unittest.main()
