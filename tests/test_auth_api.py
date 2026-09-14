from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from backend.app import app
from backend.dependencies import get_account_service
from backend.user_data_api import get_user_data_repository
from services.account_service import AccountService
from services.database import Database
from services.user_data_repository import UserDataRepository


class AuthApiTestCase(unittest.TestCase):
    """Shared fixture: the app wired to a throwaway SQLite file."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.service = AccountService(
            self.database, token_ttl_hours=1, max_failed_attempts=3, lockout_minutes=10
        )
        self.repository = UserDataRepository(self.database)
        app.dependency_overrides[get_account_service] = lambda: self.service
        app.dependency_overrides[get_user_data_repository] = lambda: self.repository
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.temp.cleanup()

    def register(self, username: str = "alice", password: str = "password123", **extra) -> dict:
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": password, **extra},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    @staticmethod
    def auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


class RegistrationAndLoginTests(AuthApiTestCase):
    def test_register_returns_token_and_profile_without_secrets(self) -> None:
        payload = self.register(email="alice@example.com", display_name="爱丽丝")
        self.assertEqual(payload["token_type"], "bearer")
        self.assertTrue(payload["access_token"])
        self.assertEqual(payload["user"]["display_name"], "爱丽丝")
        self.assertNotIn("password", str(payload["user"]))

    def test_duplicate_registration_conflicts(self) -> None:
        self.register()
        conflict = self.client.post(
            "/api/auth/register", json={"username": "ALICE", "password": "password123"}
        )
        self.assertEqual(conflict.status_code, 409)

    def test_weak_password_and_bad_username_are_rejected(self) -> None:
        short = self.client.post(
            "/api/auth/register", json={"username": "alice", "password": "short"}
        )
        self.assertEqual(short.status_code, 422)
        bad = self.client.post(
            "/api/auth/register", json={"username": "a b", "password": "password123"}
        )
        self.assertEqual(bad.status_code, 422)

    def test_login_with_username_or_email(self) -> None:
        self.register(email="alice@example.com")
        for identifier in ("alice", "ALICE@example.com"):
            response = self.client.post(
                "/api/auth/login", json={"username": identifier, "password": "password123"}
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["access_token"])

    def test_bad_password_is_401_and_lockout_is_429(self) -> None:
        self.register()
        for _ in range(3):
            failed = self.client.post(
                "/api/auth/login", json={"username": "alice", "password": "nope-nope"}
            )
            self.assertEqual(failed.status_code, 401)
        locked = self.client.post(
            "/api/auth/login", json={"username": "alice", "password": "password123"}
        )
        self.assertEqual(locked.status_code, 429)
        self.assertIn("Retry-After", locked.headers)


class ProfileTests(AuthApiTestCase):
    def test_me_requires_a_valid_bearer_token(self) -> None:
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(
            self.client.get("/api/auth/me", headers=self.auth("nonsense")).status_code, 401
        )

    def test_me_returns_the_signed_in_account(self) -> None:
        session = self.register()
        response = self.client.get("/api/auth/me", headers=self.auth(session["access_token"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "alice")

    def test_profile_update_and_email_conflict(self) -> None:
        session = self.register()
        self.register(username="bob", email="bob@example.com")
        headers = self.auth(session["access_token"])

        updated = self.client.patch(
            "/api/auth/me", json={"display_name": "新名字", "email": "alice@example.com"},
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["display_name"], "新名字")

        conflict = self.client.patch(
            "/api/auth/me", json={"email": "BOB@example.com"}, headers=headers
        )
        self.assertEqual(conflict.status_code, 409)

        invalid = self.client.patch("/api/auth/me", json={"email": "broken@"}, headers=headers)
        self.assertEqual(invalid.status_code, 422)

        cleared = self.client.patch("/api/auth/me", json={"clear_email": True}, headers=headers)
        self.assertIsNone(cleared.json()["email"])

    def test_change_password_revokes_tokens(self) -> None:
        session = self.register()
        headers = self.auth(session["access_token"])

        wrong = self.client.post(
            "/api/auth/me/password",
            json={"current_password": "bad-password", "new_password": "newpassword1"},
            headers=headers,
        )
        self.assertEqual(wrong.status_code, 401)

        changed = self.client.post(
            "/api/auth/me/password",
            json={"current_password": "password123", "new_password": "newpassword1"},
            headers=headers,
        )
        self.assertEqual(changed.status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)
        again = self.client.post(
            "/api/auth/login", json={"username": "alice", "password": "newpassword1"}
        )
        self.assertEqual(again.status_code, 200)

    def test_logout_revokes_only_the_current_token(self) -> None:
        first = self.register()["access_token"]
        second = self.client.post(
            "/api/auth/login", json={"username": "alice", "password": "password123"}
        ).json()["access_token"]

        self.assertEqual(
            self.client.post("/api/auth/logout", headers=self.auth(first)).status_code, 204
        )
        self.assertEqual(self.client.get("/api/auth/me", headers=self.auth(first)).status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me", headers=self.auth(second)).status_code, 200)

    def test_logout_all_keeps_the_caller_signed_in(self) -> None:
        first = self.register()["access_token"]
        second = self.client.post(
            "/api/auth/login", json={"username": "alice", "password": "password123"}
        ).json()["access_token"]

        self.assertEqual(
            self.client.post("/api/auth/logout-all", headers=self.auth(second)).status_code, 204
        )
        self.assertEqual(self.client.get("/api/auth/me", headers=self.auth(second)).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=self.auth(first)).status_code, 401)

    def test_token_listing_never_leaks_the_secret(self) -> None:
        token = self.register()["access_token"]
        response = self.client.get("/api/auth/me/tokens", headers=self.auth(token))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertNotIn(token, response.text)

    def test_delete_account_needs_the_password_and_removes_data(self) -> None:
        token = self.register()["access_token"]
        headers = self.auth(token)
        self.client.put(
            "/api/me/sessions/s1",
            json={"session_id": "s1", "title": "学习"},
            headers=headers,
        )

        wrong = self.client.request(
            "DELETE", "/api/auth/me", json={"password": "bad-password"}, headers=headers
        )
        self.assertEqual(wrong.status_code, 401)

        deleted = self.client.request(
            "DELETE", "/api/auth/me", json={"password": "password123"}, headers=headers
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)
        self.assertEqual(self.service.count_users(), 0)


class UserDataTests(AuthApiTestCase):
    def test_snapshots_are_stored_listed_and_deleted(self) -> None:
        headers = self.auth(self.register()["access_token"])
        saved = self.client.put(
            "/api/me/sessions/session-1",
            json={
                "session_id": "session-1",
                "title": "Agent 课程",
                "mode": "course",
                "course_id": "agent-engineering",
                "graph_data": {"nodes": [{"id": "n1"}]},
            },
            headers=headers,
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["created_at"])

        listed = self.client.get("/api/me/sessions", headers=headers).json()["sessions"]
        self.assertEqual([item["session_id"] for item in listed], ["session-1"])
        self.assertEqual(listed[0]["course_id"], "agent-engineering")

        fetched = self.client.get("/api/me/sessions/session-1", headers=headers)
        self.assertEqual(fetched.json()["graph_data"]["nodes"][0]["id"], "n1")

        removed = self.client.delete("/api/me/sessions/session-1", headers=headers)
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(
            self.client.get("/api/me/sessions/session-1", headers=headers).status_code, 404
        )

    def test_snapshots_are_private_to_their_owner(self) -> None:
        alice = self.auth(self.register()["access_token"])
        bob = self.auth(self.register(username="bob")["access_token"])
        self.client.put(
            "/api/me/sessions/private",
            json={"session_id": "private", "title": "只属于 alice"},
            headers=alice,
        )
        self.assertEqual(self.client.get("/api/me/sessions", headers=bob).json()["sessions"], [])
        self.assertEqual(
            self.client.get("/api/me/sessions/private", headers=bob).status_code, 404
        )

    def test_anonymous_access_is_rejected(self) -> None:
        self.assertEqual(self.client.get("/api/me/sessions").status_code, 401)

    def test_mismatched_and_unsafe_session_ids_are_rejected(self) -> None:
        headers = self.auth(self.register()["access_token"])
        mismatch = self.client.put(
            "/api/me/sessions/one",
            json={"session_id": "two", "title": "x"},
            headers=headers,
        )
        self.assertEqual(mismatch.status_code, 422)
        unsafe = self.client.put(
            "/api/me/sessions/bad!id",
            json={"session_id": "bad!id", "title": "x"},
            headers=headers,
        )
        self.assertEqual(unsafe.status_code, 422)
        # NOTE: 路径穿越在路由层就无法匹配，绝不会落到存储层。
        escape = self.client.put(
            "/api/me/sessions/..%2Fescape",
            json={"session_id": "../escape", "title": "x"},
            headers=headers,
        )
        self.assertNotEqual(escape.status_code, 200)
        self.assertEqual(self.repository.count(self.service.find_by_username("alice").id), 0)

    def test_repeated_save_updates_in_place_and_keeps_created_at(self) -> None:
        headers = self.auth(self.register()["access_token"])
        first = self.client.put(
            "/api/me/sessions/s1", json={"session_id": "s1", "title": "旧"}, headers=headers
        ).json()
        second = self.client.put(
            "/api/me/sessions/s1", json={"session_id": "s1", "title": "新"}, headers=headers
        ).json()
        self.assertEqual(second["created_at"], first["created_at"])
        self.assertEqual(second["title"], "新")
        self.assertEqual(len(self.client.get("/api/me/sessions", headers=headers).json()["sessions"]), 1)


if __name__ == "__main__":
    unittest.main()
