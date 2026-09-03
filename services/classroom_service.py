"""班级与师生关系：建班、邀请码入班、成员管理。

授权规则集中在这一层，不散落到路由里：

* 每次读写都必须给出调用者身份，仓库层的 SQL 一律带上归属条件；
* 无权访问与不存在返回同一种错误（``ClassroomNotFound``），因此调用方无法
  靠错误码区分"班级不存在"和"班级存在但不是你的"；
* 邀请码只有 8 位，因此按账号限速，避免被枚举。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets
import sqlite3
from typing import Any
from uuid import uuid4

from services.database import Database, default_database, utc_now


# NOTE: 去掉了 I/O/0/1 这类容易读错的字符，邀请码要能口头念给学生。
JOIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 8
JOIN_CODE_GENERATION_ATTEMPTS = 8

# 单账号在时间窗内最多试错多少次邀请码
MAX_JOIN_ATTEMPTS = 10
JOIN_ATTEMPT_WINDOW_MINUTES = 15

CLASSROOM_NAME_MAX_LENGTH = 80
CLASSROOM_DESCRIPTION_MAX_LENGTH = 500


class ClassroomError(Exception):
    """Base class for every expected classroom failure."""


class ValidationError(ClassroomError):
    """Raised when the submitted classroom fields cannot be accepted."""


class ClassroomNotFound(ClassroomError):
    """Raised when the classroom does not exist *or* is not visible to the caller."""


class AlreadyEnrolled(ClassroomError):
    """Raised when the student is already a member of the classroom."""


class NotEnrolled(ClassroomError):
    """Raised when the student is not a member of the classroom."""


class ClassroomArchived(ClassroomError):
    """Raised when joining or submitting into an archived classroom."""


class InvalidJoinCode(ClassroomError):
    """Raised when no active classroom matches the submitted join code."""


class TooManyJoinAttempts(ClassroomError):
    """Raised while a caller is rate-limited from trying more join codes."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("too many join attempts, please try again later")
        self.retry_after_seconds = retry_after_seconds


class CannotEnrollSelf(ClassroomError):
    """Raised when a teacher tries to also enrol as a student of their own class."""


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize_classroom_name(name: str) -> str:
    candidate = (name or "").strip()
    if not candidate:
        raise ValidationError("classroom name must not be empty")
    if len(candidate) > CLASSROOM_NAME_MAX_LENGTH:
        raise ValidationError(
            f"classroom name must be at most {CLASSROOM_NAME_MAX_LENGTH} characters"
        )
    return candidate


def normalize_description(description: str | None) -> str:
    candidate = (description or "").strip()
    if len(candidate) > CLASSROOM_DESCRIPTION_MAX_LENGTH:
        raise ValidationError(
            "classroom description must be at most "
            f"{CLASSROOM_DESCRIPTION_MAX_LENGTH} characters"
        )
    return candidate


def normalize_join_code(code: str) -> str:
    """邀请码大小写不敏感，统一收敛成大写后再比对。"""
    candidate = (code or "").strip().upper().replace("-", "").replace(" ", "")
    if len(candidate) != JOIN_CODE_LENGTH or any(
        ch not in JOIN_CODE_ALPHABET for ch in candidate
    ):
        raise InvalidJoinCode("join code is not valid")
    return candidate


def generate_join_code() -> str:
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))


@dataclass(frozen=True)
class Classroom:
    id: str
    teacher_id: str
    name: str
    description: str
    join_code: str
    is_archived: bool
    created_at: str
    updated_at: str
    teacher_display_name: str = ""
    member_count: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Classroom":
        keys = row.keys()
        return cls(
            id=row["id"],
            teacher_id=row["teacher_id"],
            name=row["name"],
            description=row["description"],
            join_code=row["join_code"],
            is_archived=bool(row["is_archived"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            teacher_display_name=(
                row["teacher_display_name"] if "teacher_display_name" in keys else ""
            ),
            member_count=int(row["member_count"]) if "member_count" in keys else 0,
        )

    def to_dict(self, *, include_join_code: bool) -> dict[str, Any]:
        """学生视图不返回邀请码——那是老师用来发放名额的凭据。"""
        payload: dict[str, Any] = {
            "id": self.id,
            "teacher_id": self.teacher_id,
            "teacher_display_name": self.teacher_display_name,
            "name": self.name,
            "description": self.description,
            "is_archived": self.is_archived,
            "member_count": self.member_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_join_code:
            payload["join_code"] = self.join_code
        return payload


@dataclass(frozen=True)
class ClassroomMember:
    student_id: str
    username: str
    display_name: str
    joined_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "username": self.username,
            "display_name": self.display_name,
            "joined_at": self.joined_at,
        }


_CLASSROOM_SELECT = """
    SELECT c.*,
           u.display_name AS teacher_display_name,
           (SELECT COUNT(*) FROM classroom_members m WHERE m.classroom_id = c.id)
               AS member_count
      FROM classrooms AS c
      JOIN users AS u ON u.id = c.teacher_id
"""


class ClassroomService:
    """SQLite-backed classrooms plus the teacher/student membership around them."""

    def __init__(self, database: Database | None = None) -> None:
        self.database = database or default_database

    # ------------------------------------------------------------------
    # 授权
    # ------------------------------------------------------------------
    def _row(self, connection: sqlite3.Connection, classroom_id: str) -> sqlite3.Row:
        row = connection.execute(
            f"{_CLASSROOM_SELECT} WHERE c.id = ?", (classroom_id,)
        ).fetchone()
        if row is None:
            raise ClassroomNotFound(classroom_id)
        return row

    def _assert_can_manage(
        self, connection: sqlite3.Connection, classroom_id: str, user_id: str, is_admin: bool
    ) -> sqlite3.Row:
        row = self._row(connection, classroom_id)
        if not is_admin and row["teacher_id"] != user_id:
            # 故意与"不存在"同错：不让调用方靠错误码探测别人的班级 id。
            raise ClassroomNotFound(classroom_id)
        return row

    def _assert_can_view(
        self, connection: sqlite3.Connection, classroom_id: str, user_id: str, is_admin: bool
    ) -> sqlite3.Row:
        row = self._row(connection, classroom_id)
        if is_admin or row["teacher_id"] == user_id:
            return row
        member = connection.execute(
            "SELECT 1 FROM classroom_members WHERE classroom_id = ? AND student_id = ?",
            (classroom_id, user_id),
        ).fetchone()
        if member is None:
            raise ClassroomNotFound(classroom_id)
        return row

    def get_for_manage(
        self, classroom_id: str, user_id: str, *, is_admin: bool = False
    ) -> Classroom:
        with self.database.connect() as connection:
            return Classroom.from_row(
                self._assert_can_manage(connection, classroom_id, user_id, is_admin)
            )

    def get_for_view(
        self, classroom_id: str, user_id: str, *, is_admin: bool = False
    ) -> Classroom:
        with self.database.connect() as connection:
            return Classroom.from_row(
                self._assert_can_view(connection, classroom_id, user_id, is_admin)
            )

    def can_manage(
        self, classroom_id: str, user_id: str, *, is_admin: bool = False
    ) -> bool:
        try:
            self.get_for_manage(classroom_id, user_id, is_admin=is_admin)
        except ClassroomNotFound:
            return False
        return True

    def is_member(self, classroom_id: str, student_id: str) -> bool:
        with self.database.connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM classroom_members "
                    "WHERE classroom_id = ? AND student_id = ?",
                    (classroom_id, student_id),
                ).fetchone()
                is not None
            )

    # ------------------------------------------------------------------
    # 班级生命周期
    # ------------------------------------------------------------------
    def create_classroom(
        self, teacher_id: str, name: str, description: str | None = None
    ) -> Classroom:
        name = normalize_classroom_name(name)
        description = normalize_description(description)
        now = utc_now()
        classroom_id = uuid4().hex

        with self.database.transaction() as connection:
            for _ in range(JOIN_CODE_GENERATION_ATTEMPTS):
                try:
                    connection.execute(
                        """
                        INSERT INTO classrooms (
                            id, teacher_id, name, description, join_code,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            classroom_id,
                            teacher_id,
                            name,
                            description,
                            generate_join_code(),
                            now,
                            now,
                        ),
                    )
                    break
                except sqlite3.IntegrityError as exc:
                    if "join_code" not in str(exc):
                        raise
                    continue  # 邀请码撞车，换一个再试
            else:  # pragma: no cover - 连续 8 次撞车实际不会发生
                raise ClassroomError("could not allocate a unique join code")
            return Classroom.from_row(self._row(connection, classroom_id))

    def update_classroom(
        self,
        classroom_id: str,
        user_id: str,
        *,
        is_admin: bool = False,
        name: str | None = None,
        description: str | None = None,
        is_archived: bool | None = None,
    ) -> Classroom:
        updates: dict[str, Any] = {}
        if name is not None:
            updates["name"] = normalize_classroom_name(name)
        if description is not None:
            updates["description"] = normalize_description(description)
        if is_archived is not None:
            updates["is_archived"] = 1 if is_archived else 0

        with self.database.transaction() as connection:
            self._assert_can_manage(connection, classroom_id, user_id, is_admin)
            if updates:
                updates["updated_at"] = utc_now()
                clause = ", ".join(f"{column} = ?" for column in updates)
                connection.execute(
                    f"UPDATE classrooms SET {clause} WHERE id = ?",
                    (*updates.values(), classroom_id),
                )
            return Classroom.from_row(self._row(connection, classroom_id))

    def rotate_join_code(
        self, classroom_id: str, user_id: str, *, is_admin: bool = False
    ) -> Classroom:
        """换一张新邀请码，旧码立即失效（用于码泄露后止损）。"""
        with self.database.transaction() as connection:
            self._assert_can_manage(connection, classroom_id, user_id, is_admin)
            for _ in range(JOIN_CODE_GENERATION_ATTEMPTS):
                try:
                    connection.execute(
                        "UPDATE classrooms SET join_code = ?, updated_at = ? WHERE id = ?",
                        (generate_join_code(), utc_now(), classroom_id),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:  # pragma: no cover
                raise ClassroomError("could not allocate a unique join code")
            return Classroom.from_row(self._row(connection, classroom_id))

    def delete_classroom(
        self, classroom_id: str, user_id: str, *, is_admin: bool = False
    ) -> None:
        """删班；成员关系、作业与提交都随外键级联清理。"""
        with self.database.transaction() as connection:
            self._assert_can_manage(connection, classroom_id, user_id, is_admin)
            connection.execute("DELETE FROM classrooms WHERE id = ?", (classroom_id,))

    # ------------------------------------------------------------------
    # 列表
    # ------------------------------------------------------------------
    def list_taught(self, teacher_id: str, *, include_archived: bool = True) -> list[Classroom]:
        query = f"{_CLASSROOM_SELECT} WHERE c.teacher_id = ?"
        if not include_archived:
            query += " AND c.is_archived = 0"
        query += " ORDER BY c.created_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, (teacher_id,)).fetchall()
        return [Classroom.from_row(row) for row in rows]

    def list_enrolled(self, student_id: str, *, include_archived: bool = True) -> list[Classroom]:
        query = (
            f"{_CLASSROOM_SELECT} "
            "JOIN classroom_members AS m ON m.classroom_id = c.id "
            "WHERE m.student_id = ?"
        )
        if not include_archived:
            query += " AND c.is_archived = 0"
        query += " ORDER BY m.joined_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, (student_id,)).fetchall()
        return [Classroom.from_row(row) for row in rows]

    def list_members(
        self, classroom_id: str, user_id: str, *, is_admin: bool = False
    ) -> list[ClassroomMember]:
        """列出成员。只有本班老师（或管理员）能看到完整名单。"""
        with self.database.connect() as connection:
            self._assert_can_manage(connection, classroom_id, user_id, is_admin)
            rows = connection.execute(
                """
                SELECT m.student_id, m.joined_at, u.username, u.display_name
                  FROM classroom_members AS m
                  JOIN users AS u ON u.id = m.student_id
                 WHERE m.classroom_id = ?
                 ORDER BY m.joined_at ASC
                """,
                (classroom_id,),
            ).fetchall()
        return [
            ClassroomMember(
                student_id=row["student_id"],
                username=row["username"],
                display_name=row["display_name"],
                joined_at=row["joined_at"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # 入班 / 退班
    # ------------------------------------------------------------------
    def _consume_join_attempt(self, user_id: str) -> None:
        """先记一次尝试，再判断是否超限 —— 两步必须在同一个写事务里。

        原先是"先查再记"，两次操作分处两个连接：并发打进来的一批请求会同时读到
        计数 0，于是全部放行，限速形同虚设。现在 ``BEGIN IMMEDIATE`` 持有写锁，
        自增与判断不可分割，第 11 个请求无论是串行还是并发到达都会被拒。
        """
        now = datetime.now(timezone.utc)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT attempts, window_started_at FROM classroom_join_attempts "
                "WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            started = _parse_timestamp(row["window_started_at"]) if row else None
            expired = (
                started is None
                or started + timedelta(minutes=JOIN_ATTEMPT_WINDOW_MINUTES) <= now
            )
            attempts = 1 if expired else int(row["attempts"]) + 1
            window_started = now if expired else started
            connection.execute(
                """
                INSERT INTO classroom_join_attempts (user_id, attempts, window_started_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    attempts = excluded.attempts,
                    window_started_at = excluded.window_started_at
                """,
                (user_id, attempts, window_started.isoformat()),
            )
            if attempts > MAX_JOIN_ATTEMPTS:
                window_end = window_started + timedelta(
                    minutes=JOIN_ATTEMPT_WINDOW_MINUTES
                )
                raise TooManyJoinAttempts(
                    max(1, int((window_end - now).total_seconds()) + 1)
                )

    def _clear_join_attempts(self, connection: sqlite3.Connection, user_id: str) -> None:
        connection.execute(
            "DELETE FROM classroom_join_attempts WHERE user_id = ?", (user_id,)
        )

    def join_by_code(self, student_id: str, code: str) -> Classroom:
        """凭邀请码入班。

        每次调用先消耗一个配额，**再**去比对邀请码：格式非法同样计入，
        否则用非法格式刷接口就能绕过限速。配额用尽时直接 429，
        此时不会去查库，也就问不出"这个码存不存在"。
        """
        self._consume_join_attempt(student_id)

        normalized = normalize_join_code(code)

        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, teacher_id, is_archived FROM classrooms WHERE join_code = ?",
                (normalized,),
            ).fetchone()

        if row is None:
            raise InvalidJoinCode("join code is not valid")
        if row["is_archived"]:
            # 码本身有效，不计入试错；这是一个明确的状态提示。
            raise ClassroomArchived("this classroom is archived")
        if row["teacher_id"] == student_id:
            raise CannotEnrollSelf("a teacher cannot enrol in their own classroom")

        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT 1 FROM classroom_members WHERE classroom_id = ? AND student_id = ?",
                (row["id"], student_id),
            ).fetchone()
            if existing is not None:
                raise AlreadyEnrolled("already a member of this classroom")
            try:
                connection.execute(
                    "INSERT INTO classroom_members (classroom_id, student_id, joined_at) "
                    "VALUES (?, ?, ?)",
                    (row["id"], student_id, utc_now()),
                )
            except sqlite3.IntegrityError as exc:
                # 老师刚好在这一刻删了班：这是"码已失效"，不是 500。
                raise InvalidJoinCode("join code is not valid") from exc
            self._clear_join_attempts(connection, student_id)
            return Classroom.from_row(self._row(connection, row["id"]))

    def leave_classroom(self, classroom_id: str, student_id: str) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM classroom_members WHERE classroom_id = ? AND student_id = ?",
                (classroom_id, student_id),
            )
            if cursor.rowcount == 0:
                raise NotEnrolled("not a member of this classroom")

    def remove_member(
        self, classroom_id: str, student_id: str, user_id: str, *, is_admin: bool = False
    ) -> None:
        with self.database.transaction() as connection:
            self._assert_can_manage(connection, classroom_id, user_id, is_admin)
            cursor = connection.execute(
                "DELETE FROM classroom_members WHERE classroom_id = ? AND student_id = ?",
                (classroom_id, student_id),
            )
            if cursor.rowcount == 0:
                raise NotEnrolled("not a member of this classroom")


# 全局默认班级服务（复用默认数据库实例）
classroom_service = ClassroomService()
