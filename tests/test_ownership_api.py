"""归属隔离的 HTTP 层测试。

这里锁的是最容易回归的一条：同一个 URL、同一个 session_id / topic，
换一个 Authorization 头就必须看到完全不同的数据，而不是别人的数据。
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.app import app
from backend.dependencies import get_account_service
from backend.user_data_api import get_user_data_repository
from config import get_config
from services.account_service import AccountService
from services.database import ANONYMOUS_OWNER_ID, Database
from services.learning_service import LearningService
from services.learning_store import LearningStore
from services.user_data_repository import UserDataRepository


class OwnershipApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.accounts = AccountService(self.database, token_ttl_hours=1)
        self.store = LearningStore(self.database)
        self.repository = UserDataRepository(self.database)

        app.dependency_overrides[get_account_service] = lambda: self.accounts
        app.dependency_overrides[get_user_data_repository] = lambda: self.repository

        # LearningService 不是依赖注入出来的，这里把它整体换成走临时库的版本。
        self.api_patch = patch("services.learning_service.APIClient", return_value=Mock())
        self.api_patch.start()
        self.store_patch = patch(
            "services.learning_service.learning_store", self.store
        )
        self.store_patch.start()

        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.store_patch.stop()
        self.api_patch.stop()
        app.dependency_overrides.clear()
        self.temp.cleanup()

    def _register(self, username: str) -> dict:
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": "correct-horse-battery"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    @staticmethod
    def _snapshot(session_id: str, title: str) -> dict:
        return {"session_id": session_id, "title": title, "mode": "topic"}

    # ------------------------------------------------------------------
    # 会话历史
    # ------------------------------------------------------------------
    def test_sessions_are_private_per_account(self) -> None:
        alice = self._register("alice1")
        bob = self._register("bob1")

        for headers, title in ((alice, "Alice 的会话"), (bob, "Bob 的会话")):
            response = self.client.put(
                "/api/sessions/shared_id",
                json=self._snapshot("shared_id", title),
                headers=headers,
            )
            self.assertEqual(response.status_code, 200, response.text)

        self.assertEqual(
            self.client.get("/api/sessions/shared_id", headers=alice).json()["title"],
            "Alice 的会话",
        )
        self.assertEqual(
            self.client.get("/api/sessions/shared_id", headers=bob).json()["title"],
            "Bob 的会话",
        )

        # Alice 删除自己的那条，不影响 Bob
        self.assertEqual(
            self.client.delete("/api/sessions/shared_id", headers=alice).status_code,
            204,
        )
        self.assertEqual(
            self.client.get("/api/sessions/shared_id", headers=alice).status_code, 404
        )
        self.assertEqual(
            self.client.get("/api/sessions/shared_id", headers=bob).status_code, 200
        )

    def test_guest_sessions_never_leak_into_an_account(self) -> None:
        self.client.put(
            "/api/sessions/guest_session",
            json=self._snapshot("guest_session", "访客的会话"),
        )
        alice = self._register("alice1")
        self.assertEqual(
            self.client.get("/api/sessions", headers=alice).json()["sessions"], []
        )
        self.assertEqual(
            self.client.get("/api/sessions/guest_session", headers=alice).status_code,
            404,
        )
        # 访客自己仍然读得到
        self.assertEqual(
            self.client.get("/api/sessions/guest_session").json()["title"], "访客的会话"
        )

    def test_me_sessions_and_sessions_are_the_same_store(self) -> None:
        alice = self._register("alice1")
        self.client.put(
            "/api/sessions/via_sessions",
            json=self._snapshot("via_sessions", "统一存储"),
            headers=alice,
        )
        response = self.client.get("/api/me/sessions", headers=alice)
        self.assertEqual(
            [item["session_id"] for item in response.json()["sessions"]],
            ["via_sessions"],
        )

    def test_summary_fields_survive_the_round_trip(self) -> None:
        alice = self._register("alice1")
        self.client.put(
            "/api/sessions/rich",
            json={
                "session_id": "rich",
                "title": "带进度的会话",
                "selected_node": {"id": "n1", "name": "感知模块"},
                "step_progress": {"current": 2, "total": 5},
                "average_mastery": 0.42,
            },
            headers=alice,
        )
        summary = self.client.get("/api/sessions", headers=alice).json()["sessions"][0]
        self.assertEqual(summary["last_node_name"], "感知模块")
        self.assertEqual(summary["current_step"], 2)
        self.assertEqual(summary["total_steps"], 5)
        self.assertAlmostEqual(summary["average_mastery"], 0.42)

    def test_an_invalid_token_is_401_and_never_silently_downgraded_to_guest(self) -> None:
        """否则令牌过期后用户会以为自己还登录着，实际写进了访客空间。"""
        bogus = {"Authorization": "Bearer not-a-real-token"}
        self.assertEqual(self.client.get("/api/sessions", headers=bogus).status_code, 401)
        self.assertEqual(
            self.client.put(
                "/api/sessions/x", json=self._snapshot("x", "t"), headers=bogus
            ).status_code,
            401,
        )

    # ------------------------------------------------------------------
    # 星图
    # ------------------------------------------------------------------
    def test_graphs_are_private_per_account(self) -> None:
        alice = self._register("alice1")
        bob = self._register("bob1")

        for headers, marker in ((alice, "alice"), (bob, "bob")):
            response = self.client.post(
                "/api/graph/save",
                json={"topic": "递归", "graph_data": {"owner": marker}},
                headers=headers,
            )
            self.assertEqual(response.status_code, 200, response.text)

        alice_service = LearningService(topic="递归", owner_id=self._id(alice))
        bob_service = LearningService(topic="递归", owner_id=self._id(bob))
        self.assertEqual(alice_service.load_graph("递归"), {"owner": "alice"})
        self.assertEqual(bob_service.load_graph("递归"), {"owner": "bob"})

        # Alice 删除后 Bob 的仍在
        self.assertEqual(
            self.client.delete(
                "/api/graph/delete", params={"topic": "递归"}, headers=alice
            ).status_code,
            200,
        )
        self.assertIsNone(alice_service.load_graph("递归"))
        self.assertEqual(bob_service.load_graph("递归"), {"owner": "bob"})

    def test_guest_graph_is_isolated_from_accounts(self) -> None:
        self.client.post(
            "/api/graph/save",
            json={"topic": "递归", "graph_data": {"owner": "guest"}},
        )
        alice = self._register("alice1")
        self.assertIsNone(
            LearningService(topic="递归", owner_id=self._id(alice)).load_graph("递归")
        )
        self.assertEqual(
            LearningService(topic="递归", owner_id=ANONYMOUS_OWNER_ID).load_graph("递归"),
            {"owner": "guest"},
        )

    def test_oversized_graph_is_rejected_with_413(self) -> None:
        alice = self._register("alice1")
        response = self.client.post(
            "/api/graph/save",
            json={"topic": "巨图", "graph_data": {"blob": "x" * (5 * 1024 * 1024)}},
            headers=alice,
        )
        self.assertEqual(response.status_code, 413, response.text[:200])

    # ------------------------------------------------------------------
    # 强制登录开关
    # ------------------------------------------------------------------
    def test_disabling_anonymous_access_locks_out_guests(self) -> None:
        config = get_config()
        original = config.auth.allow_anonymous
        config.auth.allow_anonymous = False
        try:
            self.assertEqual(self.client.get("/api/sessions").status_code, 401)
            alice = self._register("alice1")
            self.assertEqual(
                self.client.get("/api/sessions", headers=alice).status_code, 200
            )
        finally:
            config.auth.allow_anonymous = original

    def _id(self, headers: dict) -> str:
        return self.client.get("/api/auth/me", headers=headers).json()["id"]


if __name__ == "__main__":
    unittest.main()
