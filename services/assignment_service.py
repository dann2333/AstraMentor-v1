"""作业布置、提交与批改。

授权分工：

* 布置 / 改题 / 删题 / 看全班提交 / 打分 —— 只有本班老师（或管理员）；
* 提交 / 看自己的成绩 —— 只有本班在册学生；
* 学生提交的字段与老师批改的字段是两套模型，学生的请求体里根本没有
  ``score``/``feedback``/``status``，因此改不了自己的分数。

一切"看不见"的情况都返回 ``AssignmentNotFound``，不区分"不存在"与"不是你的"。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any
from uuid import uuid4

from services.classroom_service import (
    ClassroomNotFound,
    ClassroomService,
    classroom_service,
)
from services.database import Database, default_database, utc_now


TITLE_MAX_LENGTH = 120
INSTRUCTIONS_MAX_LENGTH = 8000
CONTENT_MAX_LENGTH = 40000
FEEDBACK_MAX_LENGTH = 4000

TARGET_FREE = "free"
TARGET_TOPIC = "topic"
TARGET_COURSE = "course"
TARGET_NODE = "node"
TARGET_DOCUMENT = "document"
VALID_TARGET_KINDS = (
    TARGET_FREE,
    TARGET_TOPIC,
    TARGET_COURSE,
    TARGET_NODE,
    TARGET_DOCUMENT,
)

STATUS_SUBMITTED = "submitted"
STATUS_GRADED = "graded"

MAX_SCORE_LIMIT = 10_000.0


class AssignmentError(Exception):
    """Base class for every expected assignment failure."""


class ValidationError(AssignmentError):
    """Raised when the submitted assignment fields cannot be accepted."""


class AssignmentNotFound(AssignmentError):
    """Raised when the assignment does not exist *or* is not visible to the caller."""


class SubmissionNotFound(AssignmentError):
    """Raised when the submission does not exist or is not visible to the caller."""


class AssignmentNotPublished(AssignmentError):
    """Raised when a student acts on a draft assignment."""


class NotEnrolled(AssignmentError):
    """Raised when the caller is not a student of the assignment's classroom."""


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize_title(title: str) -> str:
    candidate = (title or "").strip()
    if not candidate:
        raise ValidationError("assignment title must not be empty")
    if len(candidate) > TITLE_MAX_LENGTH:
        raise ValidationError(
            f"assignment title must be at most {TITLE_MAX_LENGTH} characters"
        )
    return candidate


def normalize_text(value: str | None, limit: int, field: str) -> str:
    candidate = (value or "").strip()
    if len(candidate) > limit:
        raise ValidationError(f"{field} must be at most {limit} characters")
    return candidate


def normalize_target_kind(kind: str | None) -> str:
    candidate = (kind or TARGET_FREE).strip().lower()
    if candidate not in VALID_TARGET_KINDS:
        raise ValidationError(
            f"target_kind must be one of: {', '.join(VALID_TARGET_KINDS)}"
        )
    return candidate


def normalize_due_at(due_at: str | None) -> str | None:
    if not due_at:
        return None
    parsed = _parse_timestamp(due_at)
    if parsed is None:
        raise ValidationError("due_at must be an ISO-8601 timestamp")
    return parsed.astimezone(timezone.utc).isoformat()


def normalize_max_score(max_score: float | None) -> float:
    value = 100.0 if max_score is None else float(max_score)
    if value <= 0 or value > MAX_SCORE_LIMIT:
        raise ValidationError(f"max_score must be between 0 and {MAX_SCORE_LIMIT}")
    return value


@dataclass(frozen=True)
class Assignment:
    id: str
    classroom_id: str
    title: str
    instructions: str
    target_kind: str
    target_topic: str
    target_course_id: str | None
    target_node: str
    due_at: str | None
    max_score: float
    is_published: bool
    created_at: str
    updated_at: str
    classroom_name: str = ""
    submission_count: int = 0
    graded_count: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Assignment":
        keys = row.keys()

        def optional(name: str, default: Any) -> Any:
            return row[name] if name in keys else default

        return cls(
            id=row["id"],
            classroom_id=row["classroom_id"],
            title=row["title"],
            instructions=row["instructions"],
            target_kind=row["target_kind"],
            target_topic=row["target_topic"],
            target_course_id=row["target_course_id"],
            target_node=row["target_node"],
            due_at=row["due_at"],
            max_score=float(row["max_score"]),
            is_published=bool(row["is_published"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            classroom_name=optional("classroom_name", "") or "",
            submission_count=int(optional("submission_count", 0) or 0),
            graded_count=int(optional("graded_count", 0) or 0),
        )

    def to_dict(self, *, include_teacher_stats: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "classroom_id": self.classroom_id,
            "classroom_name": self.classroom_name,
            "title": self.title,
            "instructions": self.instructions,
            "target_kind": self.target_kind,
            "target_topic": self.target_topic,
            "target_course_id": self.target_course_id,
            "target_node": self.target_node,
            "due_at": self.due_at,
            "max_score": self.max_score,
            "is_published": self.is_published,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_teacher_stats:
            payload["submission_count"] = self.submission_count
            payload["graded_count"] = self.graded_count
        return payload


@dataclass(frozen=True)
class Submission:
    id: str
    assignment_id: str
    student_id: str
    content: str
    session_id: str | None
    status: str
    is_late: bool
    submitted_at: str
    score: float | None
    feedback: str
    graded_by: str | None
    graded_at: str | None
    created_at: str
    updated_at: str
    student_username: str = ""
    student_display_name: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Submission":
        keys = row.keys()
        return cls(
            id=row["id"],
            assignment_id=row["assignment_id"],
            student_id=row["student_id"],
            content=row["content"],
            session_id=row["session_id"],
            status=row["status"],
            is_late=bool(row["is_late"]),
            submitted_at=row["submitted_at"],
            score=None if row["score"] is None else float(row["score"]),
            feedback=row["feedback"],
            graded_by=row["graded_by"],
            graded_at=row["graded_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            student_username=(
                row["student_username"] if "student_username" in keys else ""
            ),
            student_display_name=(
                row["student_display_name"] if "student_display_name" in keys else ""
            ),
        )

    def to_dict(self, *, include_student: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "assignment_id": self.assignment_id,
            "student_id": self.student_id,
            "content": self.content,
            "session_id": self.session_id,
            "status": self.status,
            "is_late": self.is_late,
            "submitted_at": self.submitted_at,
            "score": self.score,
            "feedback": self.feedback,
            "graded_by": self.graded_by,
            "graded_at": self.graded_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_student:
            payload["student_username"] = self.student_username
            payload["student_display_name"] = self.student_display_name
        return payload


_ASSIGNMENT_SELECT = """
    SELECT a.*,
           c.name AS classroom_name,
           c.teacher_id AS teacher_id,
           (SELECT COUNT(*) FROM assignment_submissions s
             WHERE s.assignment_id = a.id) AS submission_count,
           (SELECT COUNT(*) FROM assignment_submissions s
             WHERE s.assignment_id = a.id AND s.status = 'graded') AS graded_count
      FROM assignments AS a
      JOIN classrooms AS c ON c.id = a.classroom_id
"""

_SUBMISSION_SELECT = """
    SELECT s.*,
           u.username AS student_username,
           u.display_name AS student_display_name
      FROM assignment_submissions AS s
      JOIN users AS u ON u.id = s.student_id
"""


class AssignmentService:
    """SQLite-backed assignments, student submissions and teacher grading."""

    def __init__(
        self,
        database: Database | None = None,
        classrooms: ClassroomService | None = None,
    ) -> None:
        self.database = database or default_database
        self.classrooms = classrooms or classroom_service

    # ------------------------------------------------------------------
    # 授权
    # ------------------------------------------------------------------
    def _row(self, connection: sqlite3.Connection, assignment_id: str) -> sqlite3.Row:
        row = connection.execute(
            f"{_ASSIGNMENT_SELECT} WHERE a.id = ?", (assignment_id,)
        ).fetchone()
        if row is None:
            raise AssignmentNotFound(assignment_id)
        return row

    def _row_for_teacher(
        self,
        connection: sqlite3.Connection,
        assignment_id: str,
        user_id: str,
        is_admin: bool,
    ) -> sqlite3.Row:
        row = self._row(connection, assignment_id)
        if not is_admin and row["teacher_id"] != user_id:
            raise AssignmentNotFound(assignment_id)
        return row

    def _row_for_student(
        self, connection: sqlite3.Connection, assignment_id: str, student_id: str
    ) -> sqlite3.Row:
        row = self._row(connection, assignment_id)
        member = connection.execute(
            "SELECT 1 FROM classroom_members WHERE classroom_id = ? AND student_id = ?",
            (row["classroom_id"], student_id),
        ).fetchone()
        if member is None:
            raise AssignmentNotFound(assignment_id)
        if not row["is_published"]:
            # 草稿对学生完全不存在，连"有一份未发布作业"都不该泄露。
            raise AssignmentNotFound(assignment_id)
        return row

    # ------------------------------------------------------------------
    # 老师：布置与维护
    # ------------------------------------------------------------------
    def create_assignment(
        self,
        classroom_id: str,
        user_id: str,
        *,
        is_admin: bool = False,
        title: str,
        instructions: str | None = None,
        target_kind: str = TARGET_FREE,
        target_topic: str | None = None,
        target_course_id: str | None = None,
        target_node: str | None = None,
        due_at: str | None = None,
        max_score: float | None = None,
        is_published: bool = False,
    ) -> Assignment:
        # 先确认这个班是调用者的；不是就当班级不存在。
        if not self.classrooms.can_manage(classroom_id, user_id, is_admin=is_admin):
            raise ClassroomNotFound(classroom_id)

        fields = self._normalize_assignment_fields(
            title=title,
            instructions=instructions,
            target_kind=target_kind,
            target_topic=target_topic,
            target_course_id=target_course_id,
            target_node=target_node,
            due_at=due_at,
            max_score=max_score,
        )
        now = utc_now()
        assignment_id = uuid4().hex
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO assignments (
                    id, classroom_id, title, instructions, target_kind,
                    target_topic, target_course_id, target_node, due_at,
                    max_score, is_published, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment_id,
                    classroom_id,
                    fields["title"],
                    fields["instructions"],
                    fields["target_kind"],
                    fields["target_topic"],
                    fields["target_course_id"],
                    fields["target_node"],
                    fields["due_at"],
                    fields["max_score"],
                    1 if is_published else 0,
                    now,
                    now,
                ),
            )
            return Assignment.from_row(self._row(connection, assignment_id))

    @staticmethod
    def _normalize_assignment_fields(**raw: Any) -> dict[str, Any]:
        target_kind = normalize_target_kind(raw.get("target_kind"))
        target_topic = normalize_text(raw.get("target_topic"), 200, "target_topic")
        target_node = normalize_text(raw.get("target_node"), 200, "target_node")
        target_course_id = (raw.get("target_course_id") or "").strip() or None

        # 目标类型与目标字段必须自洽，否则前端拿到的作业无法定位到星图节点。
        if target_kind in (TARGET_TOPIC, TARGET_NODE) and not target_topic:
            raise ValidationError(f"target_topic is required for target_kind={target_kind}")
        if target_kind == TARGET_NODE and not target_node:
            raise ValidationError("target_node is required for target_kind=node")
        if target_kind == TARGET_COURSE and not target_course_id:
            raise ValidationError("target_course_id is required for target_kind=course")
        if target_kind == TARGET_DOCUMENT and not target_topic:
            raise ValidationError("target_topic (doc id) is required for target_kind=document")

        return {
            "title": normalize_title(raw.get("title")),
            "instructions": normalize_text(
                raw.get("instructions"), INSTRUCTIONS_MAX_LENGTH, "instructions"
            ),
            "target_kind": target_kind,
            "target_topic": target_topic,
            "target_course_id": target_course_id,
            "target_node": target_node,
            "due_at": normalize_due_at(raw.get("due_at")),
            "max_score": normalize_max_score(raw.get("max_score")),
        }

    def update_assignment(
        self,
        assignment_id: str,
        user_id: str,
        *,
        is_admin: bool = False,
        **changes: Any,
    ) -> Assignment:
        with self.database.transaction() as connection:
            row = self._row_for_teacher(connection, assignment_id, user_id, is_admin)

            merged = {
                "title": row["title"],
                "instructions": row["instructions"],
                "target_kind": row["target_kind"],
                "target_topic": row["target_topic"],
                "target_course_id": row["target_course_id"],
                "target_node": row["target_node"],
                "due_at": row["due_at"],
                "max_score": row["max_score"],
            }
            for key in list(merged):
                if changes.get(key) is not None:
                    merged[key] = changes[key]
            # 单独的开关：due_at 要能被清空，而 None 在这里表示"不改"。
            if changes.get("clear_due_at"):
                merged["due_at"] = None
            fields = self._normalize_assignment_fields(**merged)

            is_published = changes.get("is_published")
            fields["is_published"] = (
                bool(row["is_published"]) if is_published is None else bool(is_published)
            )
            fields["updated_at"] = utc_now()

            clause = ", ".join(f"{column} = ?" for column in fields)
            connection.execute(
                f"UPDATE assignments SET {clause} WHERE id = ?",
                (*fields.values(), assignment_id),
            )
            return Assignment.from_row(self._row(connection, assignment_id))

    def delete_assignment(
        self, assignment_id: str, user_id: str, *, is_admin: bool = False
    ) -> None:
        with self.database.transaction() as connection:
            self._row_for_teacher(connection, assignment_id, user_id, is_admin)
            connection.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))

    # ------------------------------------------------------------------
    # 列表与详情
    # ------------------------------------------------------------------
    def list_for_teacher(
        self, classroom_id: str, user_id: str, *, is_admin: bool = False
    ) -> list[Assignment]:
        if not self.classrooms.can_manage(classroom_id, user_id, is_admin=is_admin):
            raise ClassroomNotFound(classroom_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"{_ASSIGNMENT_SELECT} WHERE a.classroom_id = ? "
                "ORDER BY a.created_at DESC",
                (classroom_id,),
            ).fetchall()
        return [Assignment.from_row(row) for row in rows]

    def list_for_student(self, student_id: str) -> list[dict[str, Any]]:
        """学生视角的作业清单：只含已发布的，并带上自己的提交状态。"""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""{_ASSIGNMENT_SELECT}
                  JOIN classroom_members AS m ON m.classroom_id = a.classroom_id
                 WHERE m.student_id = ? AND a.is_published = 1
                 ORDER BY (a.due_at IS NULL), a.due_at ASC, a.created_at DESC
                """,
                (student_id,),
            ).fetchall()
            submissions = {
                row["assignment_id"]: Submission.from_row(row)
                for row in connection.execute(
                    f"{_SUBMISSION_SELECT} WHERE s.student_id = ?", (student_id,)
                ).fetchall()
            }
        result = []
        for row in rows:
            assignment = Assignment.from_row(row)
            mine = submissions.get(assignment.id)
            result.append(
                {
                    **assignment.to_dict(),
                    "my_submission": mine.to_dict() if mine else None,
                }
            )
        return result

    def get_for_teacher(
        self, assignment_id: str, user_id: str, *, is_admin: bool = False
    ) -> Assignment:
        with self.database.connect() as connection:
            return Assignment.from_row(
                self._row_for_teacher(connection, assignment_id, user_id, is_admin)
            )

    def get_for_student(self, assignment_id: str, student_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._row_for_student(connection, assignment_id, student_id)
            mine = connection.execute(
                f"{_SUBMISSION_SELECT} WHERE s.assignment_id = ? AND s.student_id = ?",
                (assignment_id, student_id),
            ).fetchone()
        assignment = Assignment.from_row(row)
        return {
            **assignment.to_dict(),
            "my_submission": Submission.from_row(mine).to_dict() if mine else None,
        }

    def list_submissions(
        self, assignment_id: str, user_id: str, *, is_admin: bool = False
    ) -> list[Submission]:
        """全班提交清单，仅本班老师可见。"""
        with self.database.connect() as connection:
            self._row_for_teacher(connection, assignment_id, user_id, is_admin)
            rows = connection.execute(
                f"{_SUBMISSION_SELECT} WHERE s.assignment_id = ? "
                "ORDER BY s.submitted_at ASC",
                (assignment_id,),
            ).fetchall()
        return [Submission.from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # 学生：提交
    # ------------------------------------------------------------------
    def submit(
        self,
        assignment_id: str,
        student_id: str,
        *,
        content: str,
        session_id: str | None = None,
    ) -> Submission:
        """新建或覆盖自己的提交。逾期不拒收，只打上 ``is_late`` 标记交给老师判断。"""
        content = normalize_text(content, CONTENT_MAX_LENGTH, "content")
        if not content:
            raise ValidationError("submission content must not be empty")

        now = datetime.now(timezone.utc)
        now_text = now.isoformat()
        with self.database.transaction() as connection:
            row = self._row_for_student(connection, assignment_id, student_id)
            due_at = _parse_timestamp(row["due_at"])
            is_late = 1 if (due_at is not None and now > due_at) else 0

            existing = connection.execute(
                "SELECT id, created_at FROM assignment_submissions "
                "WHERE assignment_id = ? AND student_id = ?",
                (assignment_id, student_id),
            ).fetchone()
            submission_id = existing["id"] if existing else uuid4().hex
            created_at = existing["created_at"] if existing else now_text

            # 重新提交会清空既有分数：那份评分针对的是旧答案。
            connection.execute(
                """
                INSERT INTO assignment_submissions (
                    id, assignment_id, student_id, content, session_id, status,
                    is_late, submitted_at, score, feedback, graded_by, graded_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, '', NULL, NULL, ?, ?)
                ON CONFLICT(assignment_id, student_id) DO UPDATE SET
                    content = excluded.content,
                    session_id = excluded.session_id,
                    status = excluded.status,
                    is_late = excluded.is_late,
                    submitted_at = excluded.submitted_at,
                    score = NULL,
                    feedback = '',
                    graded_by = NULL,
                    graded_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    submission_id,
                    assignment_id,
                    student_id,
                    content,
                    (session_id or None),
                    STATUS_SUBMITTED,
                    is_late,
                    now_text,
                    created_at,
                    now_text,
                ),
            )
            saved = connection.execute(
                f"{_SUBMISSION_SELECT} WHERE s.assignment_id = ? AND s.student_id = ?",
                (assignment_id, student_id),
            ).fetchone()
            return Submission.from_row(saved)

    def get_my_submission(self, assignment_id: str, student_id: str) -> Submission:
        with self.database.connect() as connection:
            self._row_for_student(connection, assignment_id, student_id)
            row = connection.execute(
                f"{_SUBMISSION_SELECT} WHERE s.assignment_id = ? AND s.student_id = ?",
                (assignment_id, student_id),
            ).fetchone()
        if row is None:
            raise SubmissionNotFound(assignment_id)
        return Submission.from_row(row)

    # ------------------------------------------------------------------
    # 老师：批改
    # ------------------------------------------------------------------
    def grade(
        self,
        assignment_id: str,
        student_id: str,
        user_id: str,
        *,
        is_admin: bool = False,
        score: float | None,
        feedback: str | None = None,
    ) -> Submission:
        feedback = normalize_text(feedback, FEEDBACK_MAX_LENGTH, "feedback")
        now = utc_now()
        with self.database.transaction() as connection:
            row = self._row_for_teacher(connection, assignment_id, user_id, is_admin)
            max_score = float(row["max_score"])
            if score is not None:
                score = float(score)
                if score < 0 or score > max_score:
                    raise ValidationError(f"score must be between 0 and {max_score}")

            cursor = connection.execute(
                """
                UPDATE assignment_submissions
                   SET score = ?, feedback = ?, status = ?, graded_by = ?,
                       graded_at = ?, updated_at = ?
                 WHERE assignment_id = ? AND student_id = ?
                """,
                (
                    score,
                    feedback,
                    STATUS_GRADED if score is not None else STATUS_SUBMITTED,
                    user_id,
                    now if score is not None else None,
                    now,
                    assignment_id,
                    student_id,
                ),
            )
            if cursor.rowcount == 0:
                raise SubmissionNotFound(f"{assignment_id}/{student_id}")
            saved = connection.execute(
                f"{_SUBMISSION_SELECT} WHERE s.assignment_id = ? AND s.student_id = ?",
                (assignment_id, student_id),
            ).fetchone()
            return Submission.from_row(saved)

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    def classroom_progress(
        self, classroom_id: str, user_id: str, *, is_admin: bool = False
    ) -> list[dict[str, Any]]:
        """本班每个学生的完成情况，供老师的班级面板使用。"""
        if not self.classrooms.can_manage(classroom_id, user_id, is_admin=is_admin):
            raise ClassroomNotFound(classroom_id)
        with self.database.connect() as connection:
            published = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assignments "
                    "WHERE classroom_id = ? AND is_published = 1",
                    (classroom_id,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT m.student_id,
                       u.username,
                       u.display_name,
                       m.joined_at,
                       COUNT(s.id) AS submitted_count,
                       SUM(CASE WHEN s.status = 'graded' THEN 1 ELSE 0 END)
                           AS graded_count,
                       SUM(CASE WHEN s.is_late = 1 THEN 1 ELSE 0 END) AS late_count,
                       AVG(s.score) AS average_score
                  FROM classroom_members AS m
                  JOIN users AS u ON u.id = m.student_id
                  LEFT JOIN assignments AS a
                         ON a.classroom_id = m.classroom_id AND a.is_published = 1
                  LEFT JOIN assignment_submissions AS s
                         ON s.assignment_id = a.id AND s.student_id = m.student_id
                 WHERE m.classroom_id = ?
                 GROUP BY m.student_id
                 ORDER BY m.joined_at ASC
                """,
                (classroom_id,),
            ).fetchall()
        return [
            {
                "student_id": row["student_id"],
                "username": row["username"],
                "display_name": row["display_name"],
                "joined_at": row["joined_at"],
                "published_assignments": published,
                "submitted_count": int(row["submitted_count"] or 0),
                "graded_count": int(row["graded_count"] or 0),
                "late_count": int(row["late_count"] or 0),
                "average_score": (
                    None
                    if row["average_score"] is None
                    else round(float(row["average_score"]), 2)
                ),
            }
            for row in rows
        ]


# 全局默认作业服务（复用默认数据库实例）
assignment_service = AssignmentService()
