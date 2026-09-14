from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from services.account_service import (
    AccountDisabled,
    AccountLocked,
    AccountService,
    EmailTaken,
    InvalidCredentials,
    InvalidToken,
    UsernameTaken,
    UserNotFound,
    ValidationError,
)
from services.database import Database
from services.security import hash_password, hash_token, verify_password


class PasswordHashingTests(unittest.TestCase):
    def test_hash_is_salted_and_verifies(self) -> None:
        first = hash_password("correct horse", iterations=1_000)
        second = hash_password("correct horse", iterations=1_000)
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("correct horse", first))
        self.assertFalse(verify_password("wrong horse", first))

    def test_malformed_hash_is_rejected_without_raising(self) -> None:
        self.assertFalse(verify_password("anything", "not-a-hash"))
        self.assertFalse(verify_password("anything", "bcrypt$1$a$b"))


class AccountServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.service = AccountService(
            self.database, token_ttl_hours=1, max_failed_attempts=3, lockout_minutes=10
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _register(self, **overrides) -> "object":
        payload = {"username": "alice", "password": "password123"}
        payload.update(overrides)
        return self.service.register(payload.pop("username"), payload.pop("password"), **payload)

    def test_register_stores_hash_not_password(self) -> None:
        user = self._register(email="Alice@Example.com", display_name="爱丽丝")
        self.assertEqual(user.display_name, "爱丽丝")
        self.assertTrue(user.is_active)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT password_hash, email_lower FROM users WHERE id = ?", (user.id,)
            ).fetchone()
        self.assertNotIn("password123", row["password_hash"])
        self.assertEqual(row["email_lower"], "alice@example.com")

    def test_display_name_defaults_to_username(self) -> None:
        self.assertEqual(self._register().display_name, "alice")

    def test_duplicate_username_and_email_are_rejected_case_insensitively(self) -> None:
        self._register(email="a@example.com")
        with self.assertRaises(UsernameTaken):
            self.service.register("ALICE", "password123")
        with self.assertRaises(EmailTaken):
            self.service.register("bob", "password123", email="A@EXAMPLE.COM")

    def test_invalid_input_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.register("ab", "password123")
        with self.assertRaises(ValidationError):
            self.service.register("alice", "short")
        with self.assertRaises(ValidationError):
            self.service.register("alice", "password123", email="not-an-email")

    def test_login_accepts_username_or_email(self) -> None:
        user = self._register(email="alice@example.com")
        by_username = self.service.login("Alice", "password123")
        by_email = self.service.login("ALICE@example.com", "password123")
        self.assertEqual(by_username.user.id, user.id)
        self.assertNotEqual(by_username.token, by_email.token)
        self.assertIsNotNone(self.service.get_user(user.id).last_login_at)

    def test_wrong_password_is_rejected_and_unknown_user_looks_the_same(self) -> None:
        self._register()
        with self.assertRaises(InvalidCredentials):
            self.service.login("alice", "not-my-password")
        with self.assertRaises(InvalidCredentials):
            self.service.login("nobody", "not-my-password")

    def test_repeated_failures_lock_the_account_then_release(self) -> None:
        user = self._register()
        for _ in range(3):
            with self.assertRaises(InvalidCredentials):
                self.service.login("alice", "wrong-password")
        with self.assertRaises(AccountLocked) as locked:
            self.service.login("alice", "password123")
        self.assertGreater(locked.exception.retry_after_seconds, 0)

        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE users SET locked_until = ? WHERE id = ?", (past, user.id)
            )
        self.assertEqual(self.service.login("alice", "password123").user.id, user.id)

    def test_token_round_trip_and_revocation(self) -> None:
        user = self._register()
        issued = self.service.login("alice", "password123")
        self.assertEqual(self.service.resolve_token(issued.token).id, user.id)

        with self.database.connect() as connection:
            stored = connection.execute(
                "SELECT token_hash FROM auth_tokens"
            ).fetchone()["token_hash"]
        self.assertNotEqual(stored, issued.token)
        self.assertEqual(stored, hash_token(issued.token))

        self.assertTrue(self.service.revoke_token(issued.token))
        with self.assertRaises(InvalidToken):
            self.service.resolve_token(issued.token)

    def test_expired_token_is_rejected_and_purged(self) -> None:
        self._register()
        issued = self.service.login("alice", "password123")
        expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE auth_tokens SET expires_at = ? WHERE token_hash = ?",
                (expired, hash_token(issued.token)),
            )
        with self.assertRaises(InvalidToken):
            self.service.resolve_token(issued.token)
        self.assertEqual(self.service.purge_expired_tokens(), 1)

    def test_logout_all_keeps_the_current_token(self) -> None:
        user = self._register()
        keep = self.service.login("alice", "password123")
        other = self.service.login("alice", "password123")
        self.assertEqual(self.service.revoke_all_tokens(user.id, keep_token=keep.token), 1)
        self.assertEqual(self.service.resolve_token(keep.token).id, user.id)
        with self.assertRaises(InvalidToken):
            self.service.resolve_token(other.token)

    def test_change_password_requires_the_old_one_and_revokes_tokens(self) -> None:
        user = self._register()
        issued = self.service.login("alice", "password123")
        with self.assertRaises(InvalidCredentials):
            self.service.change_password(user.id, "wrong", "newpassword1")
        self.service.change_password(user.id, "password123", "newpassword1")
        with self.assertRaises(InvalidToken):
            self.service.resolve_token(issued.token)
        self.assertEqual(self.service.login("alice", "newpassword1").user.id, user.id)

    def test_update_profile_validates_and_can_clear_email(self) -> None:
        user = self._register(email="alice@example.com")
        updated = self.service.update_profile(user.id, display_name="  新名字  ")
        self.assertEqual(updated.display_name, "新名字")
        self.assertEqual(updated.email, "alice@example.com")

        with self.assertRaises(ValidationError):
            self.service.update_profile(user.id, email="broken@")

        self.service.register("bob", "password123", email="bob@example.com")
        with self.assertRaises(EmailTaken):
            self.service.update_profile(user.id, email="BOB@example.com")

        self.assertIsNone(self.service.update_profile(user.id, clear_email=True).email)

    def test_deactivating_blocks_login_and_existing_tokens(self) -> None:
        user = self._register()
        issued = self.service.login("alice", "password123")
        self.service.set_active(user.id, False)
        with self.assertRaises(InvalidToken):
            self.service.resolve_token(issued.token)
        with self.assertRaises(AccountDisabled):
            self.service.login("alice", "password123")
        self.service.set_active(user.id, True)
        self.assertEqual(self.service.login("alice", "password123").user.id, user.id)

    def test_delete_user_removes_tokens(self) -> None:
        user = self._register()
        issued = self.service.login("alice", "password123")
        self.service.delete_user(user.id)
        with self.assertRaises(UserNotFound):
            self.service.get_user(user.id)
        with self.assertRaises(InvalidToken):
            self.service.resolve_token(issued.token)
        with self.database.connect() as connection:
            remaining = connection.execute("SELECT COUNT(*) FROM auth_tokens").fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_list_tokens_hides_the_secret(self) -> None:
        user = self._register()
        issued = self.service.login("alice", "password123", label="chrome")
        summaries = self.service.list_tokens(user.id)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["label"], "chrome")
        self.assertNotIn(issued.token, str(summaries))

    def test_list_and_count_users(self) -> None:
        self._register()
        self.service.register("bob", "password123")
        self.assertEqual(self.service.count_users(), 2)
        self.assertEqual([user.username for user in self.service.list_users()], ["alice", "bob"])


if __name__ == "__main__":
    unittest.main()
