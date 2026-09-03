"""按归属账号隔离的学习会话快照（SQLite）。

这里是学习会话唯一的存储实现。此前还并行存在一份按文件存储、完全匿名的
``SessionRepository``：两套后端保存同一批数据，而文件版本没有任何归属概念，
所有人共享同一份历史。现在只保留这一份，主键一律带 ``owner_id``：

* 登录用户的 ``owner_id`` 就是账号 id，删号时随外键级联清理；
* 未登录访客统一落在预留的访客账号下，与任何真实账号互相隔离。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from services.database import Database, default_database, utc_now


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024


class InvalidSessionId(ValueError):
    """Raised when a session id could escape or confuse the storage layout."""


class SessionNotFound(KeyError):
    """Raised when a requested learning session does not exist."""


class SnapshotTooLarge(ValueError):
    """Raised when a snapshot exceeds the per-row storage budget."""


def validate_session_id(session_id: str) -> str:
    if not SESSION_ID_PATTERN.fullmatch(session_id or ""):
        raise InvalidSessionId(
            "session_id must contain only letters, numbers, hyphens or underscores"
        )
    return session_id


def _coerce_step(value: Any) -> int | None:
    """步骤进度来自前端 JSON，可能是字符串甚至 null，写库前先收敛成整数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class UserDataRepository:
    """Store, list and delete one owner's learning-session snapshots."""

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
            "last_node_id": row["last_node_id"],
            "last_node_name": row["last_node_name"],
            "current_step": row["current_step"],
            "total_steps": row["total_steps"],
            "average_mastery": float(row["average_mastery"] or 0.0),
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

    def list(self, owner_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT session_id, title, mode, course_id, course_title,
                   last_node_id, last_node_name, current_step, total_steps,
                   average_mastery, created_at, updated_at
              FROM user_sessions
             WHERE user_id = ?
             ORDER BY updated_at DESC
        """
        parameters: tuple[Any, ...] = (owner_id,)
        if limit is not None:
            query += " LIMIT ?"
            parameters = (owner_id, limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def get(self, owner_id: str, session_id: str) -> dict[str, Any]:
        validate_session_id(session_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM user_sessions WHERE user_id = ? AND session_id = ?",
                (owner_id, session_id),
            ).fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        return self._decode(row)

    def save(
        self, owner_id: str, session_id: str, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        """Upsert a snapshot, preserving the original ``created_at``."""
        validate_session_id(session_id)
        stored = {**snapshot, "session_id": session_id}
        payload = json.dumps(stored, ensure_ascii=False)
        if len(payload.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise SnapshotTooLarge(f"snapshot exceeds {MAX_SNAPSHOT_BYTES} bytes")

        selected = snapshot.get("selected_node") or {}
        progress = snapshot.get("step_progress") or {}
        if not isinstance(selected, dict):
            selected = {}
        if not isinstance(progress, dict):
            progress = {}
        try:
            average_mastery = float(snapshot.get("average_mastery") or 0.0)
        except (TypeError, ValueError):
            average_mastery = 0.0

        now = utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT created_at FROM user_sessions WHERE user_id = ? AND session_id = ?",
                (owner_id, session_id),
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
                    last_node_id, last_node_name, current_step, total_steps,
                    average_mastery, payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, session_id) DO UPDATE SET
                    title = excluded.title,
                    mode = excluded.mode,
                    course_id = excluded.course_id,
                    course_title = excluded.course_title,
                    last_node_id = excluded.last_node_id,
                    last_node_name = excluded.last_node_name,
                    current_step = excluded.current_step,
                    total_steps = excluded.total_steps,
                    average_mastery = excluded.average_mastery,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    owner_id,
                    session_id,
                    str(
                        snapshot.get("title")
                        or snapshot.get("internal_topic")
                        or "未命名学习"
                    )[:200],
                    str(snapshot.get("mode") or "topic")[:32],
                    snapshot.get("course_id"),
                    snapshot.get("course_title"),
                    selected.get("id"),
                    selected.get("name"),
                    _coerce_step(progress.get("current")),
                    _coerce_step(progress.get("total")),
                    average_mastery,
                    payload,
                    created_at,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM user_sessions WHERE user_id = ? AND session_id = ?",
                (owner_id, session_id),
            ).fetchone()
            return self._decode(row)

    def delete(self, owner_id: str, session_id: str) -> None:
        validate_session_id(session_id)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM user_sessions WHERE user_id = ? AND session_id = ?",
                (owner_id, session_id),
            )
            if cursor.rowcount == 0:
                raise SessionNotFound(session_id)

    def count(self, owner_id: str) -> int:
        with self.database.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM user_sessions WHERE user_id = ?", (owner_id,)
                ).fetchone()[0]
            )


# 全局默认用户数据仓库（复用默认数据库实例）
user_data_repository = UserDataRepository()
