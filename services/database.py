"""SQLite storage backend shared by the account and user-data repositories.

One database file holds every persisted record. Connections are created per
operation so the repositories stay usable from FastAPI's worker threads, and
the schema is applied on first use through ``PRAGMA user_version``.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import threading
from typing import Iterator


DEFAULT_DATABASE_PATH = Path("user_data") / "astramentor.db"

SCHEMA_VERSION = 1

# NOTE: 每个迁移只追加，不修改历史条目；user_version 决定从哪一步继续执行。
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        """
        CREATE TABLE IF NOT EXISTS users (
            id                    TEXT PRIMARY KEY,
            username              TEXT NOT NULL,
            username_lower        TEXT NOT NULL UNIQUE,
            email                 TEXT,
            email_lower           TEXT UNIQUE,
            display_name          TEXT NOT NULL DEFAULT '',
            password_hash         TEXT NOT NULL,
            is_active             INTEGER NOT NULL DEFAULT 1,
            failed_login_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until          TEXT,
            last_login_at         TEXT,
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS auth_tokens (
            token_hash   TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label        TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL,
            expires_at   TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at   TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id)",
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id    TEXT NOT NULL,
            title         TEXT NOT NULL DEFAULT '',
            mode          TEXT NOT NULL DEFAULT 'topic',
            course_id     TEXT,
            course_title  TEXT,
            payload       TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_user_sessions_updated "
        "ON user_sessions(user_id, updated_at DESC)",
    ),
)


def utc_now() -> str:
    """Timestamp helper shared by every repository, so ordering stays sortable."""
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Owns the SQLite file: connection settings, schema creation, transactions."""

    def __init__(self, path: Path | str | None = None, *, timeout: float = 15.0) -> None:
        self.path = Path(path or os.getenv("ASTRA_DB_PATH") or DEFAULT_DATABASE_PATH)
        self.timeout = timeout
        self._initialized = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    def _new_connection(self) -> sqlite3.Connection:
        if self.path.parent and str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=self.timeout,
            isolation_level=None,  # explicit transactions via transaction()
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = %d" % int(self.timeout * 1000))
        return connection

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a ready-to-use connection, applying migrations on first use."""
        self.initialize()
        connection = self._new_connection()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a write batch; the whole batch rolls back when the body raises."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """Create or upgrade the schema exactly once per process."""
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            connection = self._new_connection()
            try:
                current = connection.execute("PRAGMA user_version").fetchone()[0]
                if current < SCHEMA_VERSION:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        for statements in MIGRATIONS[current:SCHEMA_VERSION]:
                            for statement in statements:
                                connection.execute(statement)
                        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                    connection.execute("COMMIT")
            finally:
                connection.close()
            self._initialized = True


def _configured_database_path() -> Path | None:
    """Read the configured path, tolerating imports outside the project root."""
    try:
        from config import get_config
    except ImportError:  # pragma: no cover - 独立使用本模块时的兜底
        return None
    return Path(get_config().storage.database_path)


# 全局默认数据库实例（路径可通过 ASTRA_DB_PATH 覆盖）
default_database = Database(_configured_database_path())
