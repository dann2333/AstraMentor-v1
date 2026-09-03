"""SQLite-backed learning-session snapshots owned by a signed-in account.

Mirrors the summary shape of the file-backed :class:`SessionRepository` so the
frontend can reuse the same types, but every row is scoped to a ``user_id`` and
disappears together with the account.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from services.database import Database, default_database, utc_now
from services.session_repository import (
    SESSION_ID_PATTERN,
    InvalidSessionId,
    SessionNotFound,
)


MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024


class SnapshotTooLarge(ValueError):
    """Raised when a snapshot exceeds the per-row storage budget."""


def validate_session_id(session_id: str) -> str:
    if not SESSION_ID_PATTERN.fullmatch(session_id or ""):
        raise InvalidSessionId(
            "session_id must contain only letters, numbers, hyphens or underscores"
        )
    return session_id


class UserDataRepository:
    """Store, list and delete one account's learning-session snapshots."""

    def __init__(self, database: Database | None = None) -> None:
        self.database = database or default_database

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "session_id": row["session_id"],
            "title": row["title"],
            "mode": row["mode"],
            "course_id": row["course_id"],
            "course_title": row["course_title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _decode(self, row: sqlite3.Row) -> dict[str, Any]:
        snapshot = json.loads(row["payload"])
        snapshot.update(
            {
                "session_id": row["session_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return snapshot

    def list(self, user_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT session_id, title, mode, course_id, course_title, created_at, updated_at
              FROM user_sessions
             WHERE user_id = ?
             ORDER BY updated_at DESC
        """
        parameters: tuple[Any, ...] = (user_id,)
        if limit is not None:
            query += " LIMIT ?"
            parameters = (user_id, limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def get(self, user_id: str, session_id: str) -> dict[str, Any]:
        validate_session_id(session_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            ).fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        return self._decode(row)

    def save(
        self, user_id: str, session_id: str, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        """Upsert a snapshot, preserving the original ``created_at``."""
        validate_session_id(session_id)
        stored = {**snapshot, "session_id": session_id}
        payload = json.dumps(stored, ensure_ascii=False)
        if len(payload.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise SnapshotTooLarge(
                f"snapshot exceeds {MAX_SNAPSHOT_BYTES} bytes"
            )

        now = utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT created_at FROM user_sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            ).fetchone()
            created_at = (
                existing["created_at"]
                if existing
                else (snapshot.get("created_at") or now)
            )
            connection.execute(
                """
                INSERT INTO user_sessions (
                    user_id, session_id, title, mode, course_id, course_title,
                    payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, session_id) DO UPDATE SET
                    title = excluded.title,
                    mode = excluded.mode,
                    course_id = excluded.course_id,
                    course_title = excluded.course_title,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    session_id,
                    str(snapshot.get("title") or "未命名学习")[:200],
                    str(snapshot.get("mode") or "topic")[:32],
                    snapshot.get("course_id"),
                    snapshot.get("course_title"),
                    payload,
                    created_at,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM user_sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            ).fetchone()
            return self._decode(row)

    def delete(self, user_id: str, session_id: str) -> None:
        validate_session_id(session_id)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM user_sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            )
            if cursor.rowcount == 0:
                raise SessionNotFound(session_id)

    def count(self, user_id: str) -> int:
        with self.database.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM user_sessions WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
            )


# 全局默认用户数据仓库（复用默认数据库实例）
user_data_repository = UserDataRepository()
