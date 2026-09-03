"""Account lifecycle: registration, login, profile updates and API tokens.

Every public method validates its input and raises a specific ``AccountError``
subclass, so the HTTP layer can map failures to status codes without having to
re-implement any of the rules here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
import sqlite3
from typing import Any
from uuid import uuid4

from services.database import Database, default_database, utc_now
from services.security import (
    generate_token,
    hash_password,
    hash_token,
    verify_password,
    waste_password_comparison,
)


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
DISPLAY_NAME_MAX_LENGTH = 64


class AccountError(Exception):
    """Base class for every expected account failure."""


class ValidationError(AccountError):
    """Raised when user input cannot form a valid account field."""


class UsernameTaken(AccountError):
    """Raised when the requested username already exists."""


class EmailTaken(AccountError):
    """Raised when the requested email already belongs to another account."""


class UserNotFound(AccountError):
    """Raised when no account matches the given identifier."""


class InvalidCredentials(AccountError):
    """Raised for a wrong password or an unknown login identifier."""


class AccountLocked(AccountError):
    """Raised while an account is temporarily locked after failed logins."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("account temporarily locked after too many failed logins")
        self.retry_after_seconds = retry_after_seconds


class AccountDisabled(AccountError):
    """Raised when an account exists but has been deactivated."""


class InvalidToken(AccountError):
    """Raised when a bearer token is unknown, revoked or expired."""


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class User:
    """The account fields safe to return over the API — never the password."""

    id: str
    username: str
    email: str | None
    display_name: str
    is_active: bool
    created_at: str
    updated_at: str
    last_login_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        return cls(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            display_name=row["display_name"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_login_at=row["last_login_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "display_name": self.display_name,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_login_at": self.last_login_at,
        }


@dataclass(frozen=True)
class IssuedToken:
    """A freshly minted token; ``token`` is only ever available right here."""

    token: str
    expires_at: str
    user: User = field(repr=False)


def normalize_username(username: str) -> str:
    candidate = (username or "").strip()
    if not USERNAME_PATTERN.fullmatch(candidate):
        raise ValidationError(
            "username must be 3-32 characters, start with a letter or digit and "
            "contain only letters, digits, '.', '_' or '-'"
        )
    return candidate


def normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    candidate = email.strip()
    if not candidate:
        return None
    if len(candidate) > 254 or not EMAIL_PATTERN.fullmatch(candidate):
        raise ValidationError("email address is not valid")
    return candidate


def validate_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        raise ValidationError(
            f"password must be at least {PASSWORD_MIN_LENGTH} characters long"
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValidationError(
            f"password must be at most {PASSWORD_MAX_LENGTH} characters long"
        )
    return password


def normalize_display_name(display_name: str | None) -> str:
    candidate = (display_name or "").strip()
    if len(candidate) > DISPLAY_NAME_MAX_LENGTH:
        raise ValidationError(
            f"display_name must be at most {DISPLAY_NAME_MAX_LENGTH} characters long"
        )
    return candidate


class AccountService:
    """SQLite-backed account store plus the bearer tokens that authenticate it."""

    def __init__(
        self,
        database: Database | None = None,
        *,
        token_ttl_hours: int = 168,
        max_failed_attempts: int = 8,
        lockout_minutes: int = 15,
    ) -> None:
        self.database = database or default_database
        self.token_ttl_hours = token_ttl_hours
        self.max_failed_attempts = max_failed_attempts
        self.lockout_minutes = lockout_minutes

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def _row_by_id(self, connection: sqlite3.Connection, user_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise UserNotFound(user_id)
        return row

    def get_user(self, user_id: str) -> User:
        with self.database.connect() as connection:
            return User.from_row(self._row_by_id(connection, user_id))

    def find_by_username(self, username: str) -> User | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_lower = ?",
                ((username or "").strip().lower(),),
            ).fetchone()
        return User.from_row(row) if row else None

    def list_users(self, *, limit: int = 50, offset: int = 0) -> list[User]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [User.from_row(row) for row in rows]

    def count_users(self) -> int:
        with self.database.connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    # ------------------------------------------------------------------
    # Registration and profile
    # ------------------------------------------------------------------
    def register(
        self,
        username: str,
        password: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> User:
        username = normalize_username(username)
        email = normalize_email(email)
        validate_password(password)
        display_name = normalize_display_name(display_name) or username

        now = utc_now()
        user_id = uuid4().hex
        record = (
            user_id,
            username,
            username.lower(),
            email,
            email.lower() if email else None,
            display_name,
            hash_password(password),
            now,
            now,
        )
        with self.database.transaction() as connection:
            self._assert_identifiers_free(connection, username, email)
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        id, username, username_lower, email, email_lower,
                        display_name, password_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    record,
                )
            except sqlite3.IntegrityError as exc:  # 并发注册时的兜底
                self._raise_for_conflict(exc, username, email)
                raise
            row = self._row_by_id(connection, user_id)
            return User.from_row(row)

    def update_profile(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        email: str | None = None,
        clear_email: bool = False,
    ) -> User:
        updates: dict[str, Any] = {}
        if display_name is not None:
            updates["display_name"] = normalize_display_name(display_name)
        if clear_email:
            updates["email"] = None
            updates["email_lower"] = None
        elif email is not None:
            normalized = normalize_email(email)
            updates["email"] = normalized
            updates["email_lower"] = normalized.lower() if normalized else None

        with self.database.transaction() as connection:
            row = self._row_by_id(connection, user_id)
            if not updates:
                return User.from_row(row)
            new_email = updates.get("email", row["email"])
            if new_email and new_email.lower() != (row["email_lower"] or ""):
                self._assert_identifiers_free(connection, None, new_email)
            updates["updated_at"] = utc_now()
            assignments = ", ".join(f"{column} = ?" for column in updates)
            connection.execute(
                f"UPDATE users SET {assignments} WHERE id = ?",
                (*updates.values(), user_id),
            )
            return User.from_row(self._row_by_id(connection, user_id))

    def change_password(
        self, user_id: str, current_password: str, new_password: str
    ) -> None:
        validate_password(new_password)
        with self.database.transaction() as connection:
            row = self._row_by_id(connection, user_id)
            if not verify_password(current_password, row["password_hash"]):
                raise InvalidCredentials("current password is incorrect")
            connection.execute(
                """
                UPDATE users
                   SET password_hash = ?, updated_at = ?,
                       failed_login_attempts = 0, locked_until = NULL
                 WHERE id = ?
                """,
                (hash_password(new_password), utc_now(), user_id),
            )
            # NOTE: 改密后吊销全部令牌，旧会话必须重新登录。
            connection.execute(
                "UPDATE auth_tokens SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (utc_now(), user_id),
            )

    def set_active(self, user_id: str, is_active: bool) -> User:
        with self.database.transaction() as connection:
            self._row_by_id(connection, user_id)
            connection.execute(
                "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
                (1 if is_active else 0, utc_now(), user_id),
            )
            if not is_active:
                connection.execute(
                    "UPDATE auth_tokens SET revoked_at = ? "
                    "WHERE user_id = ? AND revoked_at IS NULL",
                    (utc_now(), user_id),
                )
            return User.from_row(self._row_by_id(connection, user_id))

    def delete_user(self, user_id: str) -> None:
        """Remove the account; tokens and stored data cascade away with it."""
        with self.database.transaction() as connection:
            self._row_by_id(connection, user_id)
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def authenticate(self, identifier: str, password: str) -> User:
        """Verify a username/email and password, applying the lockout policy.

        Each failure is recorded in its own transaction: a rejection raises, and
        a rollback would otherwise discard the attempt counter it just bumped.
        """
        key = (identifier or "").strip().lower()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_lower = ? OR email_lower = ?",
                (key, key),
            ).fetchone()
        if row is None:
            waste_password_comparison(password)
            raise InvalidCredentials("invalid username or password")

        locked_until = _parse_timestamp(row["locked_until"])
        now = datetime.now(timezone.utc)
        if locked_until and locked_until > now:
            raise AccountLocked(int((locked_until - now).total_seconds()) + 1)

        if not verify_password(password, row["password_hash"]):
            self._register_failed_attempt(row["id"])
            raise InvalidCredentials("invalid username or password")

        if not row["is_active"]:
            raise AccountDisabled("account is deactivated")

        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE users
                   SET failed_login_attempts = 0, locked_until = NULL,
                       last_login_at = ?
                 WHERE id = ?
                """,
                (utc_now(), row["id"]),
            )
            return User.from_row(self._row_by_id(connection, row["id"]))

    def login(self, identifier: str, password: str, *, label: str = "") -> IssuedToken:
        user = self.authenticate(identifier, password)
        return self.issue_token(user.id, label=label)

    def _register_failed_attempt(self, user_id: str) -> None:
        """Bump the failure counter, locking the account once it hits the limit."""
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT failed_login_attempts FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if row is None:
                return
            attempts = int(row["failed_login_attempts"]) + 1
            locked_until: str | None = None
            if self.max_failed_attempts and attempts >= self.max_failed_attempts:
                locked_until = (
                    datetime.now(timezone.utc) + timedelta(minutes=self.lockout_minutes)
                ).isoformat()
                attempts = 0
            connection.execute(
                "UPDATE users SET failed_login_attempts = ?, locked_until = ? WHERE id = ?",
                (attempts, locked_until, user_id),
            )

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------
    def issue_token(self, user_id: str, *, label: str = "") -> IssuedToken:
        token = generate_token()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=self.token_ttl_hours)
        ).isoformat()
        with self.database.transaction() as connection:
            row = self._row_by_id(connection, user_id)
            if not row["is_active"]:
                raise AccountDisabled("account is deactivated")
            connection.execute(
                """
                INSERT INTO auth_tokens (token_hash, user_id, label, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (hash_token(token), user_id, (label or "")[:64], utc_now(), expires_at),
            )
            return IssuedToken(
                token=token, expires_at=expires_at, user=User.from_row(row)
            )

    def resolve_token(self, token: str) -> User:
        """Return the account behind a bearer token, or raise ``InvalidToken``."""
        if not token:
            raise InvalidToken("missing token")
        digest = hash_token(token)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT t.expires_at, t.revoked_at, u.*
                  FROM auth_tokens AS t
                  JOIN users AS u ON u.id = t.user_id
                 WHERE t.token_hash = ?
                """,
                (digest,),
            ).fetchone()
        if row is None or row["revoked_at"]:
            raise InvalidToken("token is not valid")
        expires_at = _parse_timestamp(row["expires_at"])
        if expires_at is None or expires_at <= datetime.now(timezone.utc):
            raise InvalidToken("token has expired")
        if not row["is_active"]:
            raise AccountDisabled("account is deactivated")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE auth_tokens SET last_used_at = ? WHERE token_hash = ?",
                (utc_now(), digest),
            )
        return User.from_row(row)

    def revoke_token(self, token: str) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE auth_tokens SET revoked_at = ? "
                "WHERE token_hash = ? AND revoked_at IS NULL",
                (utc_now(), hash_token(token)),
            )
            return cursor.rowcount > 0

    def revoke_all_tokens(self, user_id: str, *, keep_token: str | None = None) -> int:
        keep_hash = hash_token(keep_token) if keep_token else ""
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE auth_tokens SET revoked_at = ?
                 WHERE user_id = ? AND revoked_at IS NULL AND token_hash != ?
                """,
                (utc_now(), user_id, keep_hash),
            )
            return cursor.rowcount

    def list_tokens(self, user_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
        query = """
            SELECT token_hash, label, created_at, expires_at, last_used_at, revoked_at
              FROM auth_tokens
             WHERE user_id = ?
        """
        if active_only:
            query += " AND revoked_at IS NULL AND expires_at > ?"
            parameters: tuple[Any, ...] = (user_id, utc_now())
        else:
            parameters = (user_id,)
        query += " ORDER BY created_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                # NOTE: 只暴露摘要前缀，用于在界面上区分会话，不足以还原令牌。
                "id": row["token_hash"][:12],
                "label": row["label"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "last_used_at": row["last_used_at"],
                "revoked_at": row["revoked_at"],
            }
            for row in rows
        ]

    def purge_expired_tokens(self) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM auth_tokens WHERE expires_at <= ?", (utc_now(),)
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _assert_identifiers_free(
        self,
        connection: sqlite3.Connection,
        username: str | None,
        email: str | None,
    ) -> None:
        if username and connection.execute(
            "SELECT 1 FROM users WHERE username_lower = ?", (username.lower(),)
        ).fetchone():
            raise UsernameTaken("username is already registered")
        if email and connection.execute(
            "SELECT 1 FROM users WHERE email_lower = ?", (email.lower(),)
        ).fetchone():
            raise EmailTaken("email is already registered")

    @staticmethod
    def _raise_for_conflict(
        exc: sqlite3.IntegrityError, username: str | None, email: str | None
    ) -> None:
        message = str(exc)
        if "username_lower" in message:
            raise UsernameTaken("username is already registered") from exc
        if "email_lower" in message:
            raise EmailTaken("email is already registered") from exc


def _build_default_service() -> AccountService:
    """Apply the auth settings from config, falling back to the defaults."""
    try:
        from config import get_config
    except ImportError:  # pragma: no cover - 独立使用本模块时的兜底
        return AccountService()
    auth = get_config().auth
    return AccountService(
        token_ttl_hours=auth.token_ttl_hours,
        max_failed_attempts=auth.max_failed_attempts,
        lockout_minutes=auth.lockout_minutes,
    )


# 全局默认账号服务（复用默认数据库实例）
account_service = _build_default_service()
