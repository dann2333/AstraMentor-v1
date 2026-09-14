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

SCHEMA_VERSION = 3

# NOTE: 未登录访客的数据也必须有归属，否则无法隔离，也无法随账号级联删除。
# 这里预留一行系统用户：用户名以下划线开头，永远无法通过注册接口占用；
# password_hash 故意写成无法解析的值，is_active=0，因此既不能登录也不能签发令牌。
ANONYMOUS_OWNER_ID = "anonymous"
ANONYMOUS_USERNAME = "__anonymous__"
SYSTEM_USER_IDS = frozenset({ANONYMOUS_OWNER_ID})

ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
ROLE_ADMIN = "admin"
VALID_ROLES = (ROLE_STUDENT, ROLE_TEACHER, ROLE_ADMIN)

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
    # v2: 角色字段 + 访客归属行 + 按账号隔离的学习数据
    (
        f"ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT '{ROLE_STUDENT}'",
        # 固定时间戳标记这是系统行，而不是某次真实注册。
        f"""
        INSERT OR IGNORE INTO users (
            id, username, username_lower, email, email_lower, display_name,
            password_hash, is_active, role, created_at, updated_at
        ) VALUES (
            '{ANONYMOUS_OWNER_ID}', '{ANONYMOUS_USERNAME}', '{ANONYMOUS_USERNAME}',
            NULL, NULL, '访客', '!', 0, '{ROLE_STUDENT}',
            '1970-01-01T00:00:00+00:00', '1970-01-01T00:00:00+00:00'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS knowledge_graphs (
            owner_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            scope_key  TEXT NOT NULL,
            topic      TEXT NOT NULL DEFAULT '',
            course_id  TEXT,
            payload    TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_id, scope_key)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_knowledge_graphs_updated "
        "ON knowledge_graphs(owner_id, updated_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS learner_states (
            owner_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            scope_key  TEXT NOT NULL,
            payload    TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_id, scope_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS documents (
            owner_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            doc_id      TEXT NOT NULL,
            filename    TEXT NOT NULL DEFAULT '',
            total_pages INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            payload     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (owner_id, doc_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_documents_updated "
        "ON documents(owner_id, updated_at DESC)",
        # 首页历史栏需要这些字段。放成列而不是每次去解 payload，
        # 否则列出 20 条会话就要读 20 份最大 4MB 的 JSON。
        "ALTER TABLE user_sessions ADD COLUMN last_node_id TEXT",
        "ALTER TABLE user_sessions ADD COLUMN last_node_name TEXT",
        "ALTER TABLE user_sessions ADD COLUMN current_step INTEGER",
        "ALTER TABLE user_sessions ADD COLUMN total_steps INTEGER",
        "ALTER TABLE user_sessions ADD COLUMN average_mastery REAL NOT NULL DEFAULT 0.0",
    ),
    # v3: 班级、师生关系与作业
    (
        """
        CREATE TABLE IF NOT EXISTS classrooms (
            id          TEXT PRIMARY KEY,
            teacher_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            join_code   TEXT NOT NULL UNIQUE,
            is_archived INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_classrooms_teacher "
        "ON classrooms(teacher_id, created_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS classroom_members (
            classroom_id TEXT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
            student_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            joined_at    TEXT NOT NULL,
            PRIMARY KEY (classroom_id, student_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_classroom_members_student "
        "ON classroom_members(student_id, joined_at DESC)",
        # 邀请码只有 8 位，必须限制单账号的试错速率，否则可被枚举。
        """
        CREATE TABLE IF NOT EXISTS classroom_join_attempts (
            user_id           TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            attempts          INTEGER NOT NULL DEFAULT 0,
            window_started_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS assignments (
            id               TEXT PRIMARY KEY,
            classroom_id     TEXT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
            title            TEXT NOT NULL,
            instructions     TEXT NOT NULL DEFAULT '',
            target_kind      TEXT NOT NULL DEFAULT 'free',
            target_topic     TEXT NOT NULL DEFAULT '',
            target_course_id TEXT,
            target_node      TEXT NOT NULL DEFAULT '',
            due_at           TEXT,
            max_score        REAL NOT NULL DEFAULT 100.0,
            is_published     INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_assignments_classroom "
        "ON assignments(classroom_id, created_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS assignment_submissions (
            id            TEXT PRIMARY KEY,
            assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
            student_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            content       TEXT NOT NULL DEFAULT '',
            session_id    TEXT,
            status        TEXT NOT NULL DEFAULT 'submitted',
            is_late       INTEGER NOT NULL DEFAULT 0,
            submitted_at  TEXT NOT NULL,
            score         REAL,
            feedback      TEXT NOT NULL DEFAULT '',
            graded_by     TEXT REFERENCES users(id) ON DELETE SET NULL,
            graded_at     TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            UNIQUE (assignment_id, student_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_submissions_student "
        "ON assignment_submissions(student_id, updated_at DESC)",
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
        """Create or upgrade the schema exactly once per process.

        ``self._lock`` 只挡得住同一进程内的线程。多进程（gunicorn -w 4、
        多副本共享一个卷、服务器起来的同时跑了一个管理脚本）会同时走到这里，
        因此真正的判断必须在 ``BEGIN IMMEDIATE`` 拿到写锁**之后**再做一次：
        输的那个进程醒来时 user_version 已经是最新的，直接退出即可。
        少了这一步，v2 里那几条 ``ALTER TABLE ADD COLUMN`` 会被重复执行，
        抛出 "duplicate column name" —— 而且是在一个正在处理的请求里抛。
        """
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
                        # 拿到写锁后重新读一次：期间可能已被别的进程升级完。
                        current = connection.execute(
                            "PRAGMA user_version"
                        ).fetchone()[0]
                        if current < SCHEMA_VERSION:
                            for statements in MIGRATIONS[current:SCHEMA_VERSION]:
                                for statement in statements:
                                    connection.execute(statement)
                            connection.execute(
                                f"PRAGMA user_version = {SCHEMA_VERSION}"
                            )
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
