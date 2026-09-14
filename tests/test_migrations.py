"""迁移的两个方向：升级不丢数据，并发初始化不炸。"""

from __future__ import annotations

import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import services.database as database_module
from services.database import (
    ANONYMOUS_OWNER_ID,
    Database,
    MIGRATIONS,
    SCHEMA_VERSION,
)


def _initialize(path: str, results) -> None:
    """在独立进程里初始化同一个库；用于并发迁移测试。"""
    try:
        Database(path).initialize()
        results.put(("ok", ""))
    except Exception as exc:  # noqa: BLE001 - 要的就是把任何异常带回来
        results.put(("fail", f"{type(exc).__name__}: {exc}"))


def _build_v1(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        for statement in MIGRATIONS[0]:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO users (id, username, username_lower, email, email_lower, "
            "display_name, password_hash, created_at, updated_at) "
            "VALUES ('u1','alice','alice',NULL,NULL,'Alice','x','t0','t0')"
        )
        connection.execute(
            "INSERT INTO user_sessions (user_id, session_id, title, mode, payload, "
            "created_at, updated_at) VALUES ('u1','s1','旧会话','topic','{}','t0','t0')"
        )
        connection.commit()
    finally:
        connection.close()


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "astramentor.db"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_fresh_database_lands_on_the_current_version(self) -> None:
        with Database(self.path).connect() as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
            )
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertLessEqual(
            {
                "users",
                "auth_tokens",
                "user_sessions",
                "knowledge_graphs",
                "learner_states",
                "documents",
                "classrooms",
                "classroom_members",
                "classroom_join_attempts",
                "assignments",
                "assignment_submissions",
            },
            tables,
        )

    def test_upgrade_from_v1_keeps_existing_rows(self) -> None:
        _build_v1(self.path)
        with Database(self.path).connect() as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
            )
            self.assertEqual(
                connection.execute(
                    "SELECT display_name, role FROM users WHERE id = 'u1'"
                ).fetchone()[:],
                ("Alice", "student"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT title, average_mastery FROM user_sessions WHERE session_id = 's1'"
                ).fetchone()[:],
                ("旧会话", 0.0),
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM users WHERE id = ?", (ANONYMOUS_OWNER_ID,)
                ).fetchone()
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_concurrent_processes_do_not_re_run_the_alters(self) -> None:
        """v2 里的 ALTER TABLE 不是幂等的，必须在拿到写锁后再判一次版本。

        少了那一步，多进程部署（gunicorn -w 4、多副本共享一个卷）在首次启动时
        会有进程抛 "duplicate column name: role"，而且是在处理请求的过程中抛。
        """
        for label, prepare in (("fresh", None), ("from-v1", _build_v1)):
            with self.subTest(start=label):
                temp = tempfile.TemporaryDirectory()
                try:
                    path = Path(temp.name) / "astramentor.db"
                    if prepare:
                        prepare(path)
                    context = multiprocessing.get_context("fork")
                    results = context.Queue()
                    processes = [
                        context.Process(target=_initialize, args=(str(path), results))
                        for _ in range(4)
                    ]
                    for process in processes:
                        process.start()
                    for process in processes:
                        process.join(timeout=60)
                    outcomes = [results.get() for _ in processes]
                    failures = [message for status, message in outcomes if status != "ok"]
                    self.assertEqual(failures, [])
                    with Database(path).connect() as connection:
                        self.assertEqual(
                            connection.execute("PRAGMA user_version").fetchone()[0],
                            SCHEMA_VERSION,
                        )
                        self.assertEqual(
                            connection.execute(
                                "SELECT COUNT(*) FROM users WHERE id = ?",
                                (ANONYMOUS_OWNER_ID,),
                            ).fetchone()[0],
                            1,
                        )
                finally:
                    temp.cleanup()

    def test_migrations_tuple_matches_the_declared_version(self) -> None:
        """迁移只能追加：条目数必须与 SCHEMA_VERSION 一致。"""
        self.assertEqual(len(MIGRATIONS), SCHEMA_VERSION)
        self.assertIs(database_module.MIGRATIONS, MIGRATIONS)


if __name__ == "__main__":
    unittest.main()
