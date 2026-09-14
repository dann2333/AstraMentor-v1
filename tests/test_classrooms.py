"""班级与师生关系的服务层测试：授权、隔离、邀请码限速。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from services.account_service import AccountService
from services.database import Database, ROLE_TEACHER
from services.classroom_service import (
    AlreadyEnrolled,
    CannotEnrollSelf,
    ClassroomArchived,
    ClassroomNotFound,
    ClassroomService,
    InvalidJoinCode,
    JOIN_ATTEMPT_WINDOW_MINUTES,
    JOIN_CODE_ALPHABET,
    JOIN_CODE_LENGTH,
    MAX_JOIN_ATTEMPTS,
    NotEnrolled,
    TooManyJoinAttempts,
    ValidationError,
    normalize_join_code,
)


class ClassroomServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.accounts = AccountService(self.database)
        self.service = ClassroomService(self.database)

        self.teacher = self.accounts.register(
            "teacher1", "correct-horse-1", role=ROLE_TEACHER
        )
        self.other_teacher = self.accounts.register(
            "teacher2", "correct-horse-2", role=ROLE_TEACHER
        )
        self.student = self.accounts.register("student1", "correct-horse-3")
        self.outsider = self.accounts.register("student2", "correct-horse-4")
        self.admin = self.accounts.register(
            "root1", "correct-horse-5", role="admin", allow_admin_role=True
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _classroom(self):
        return self.service.create_classroom(self.teacher.id, "算法入门", "每周一次")

    # ------------------------------------------------------------------
    # 建班与邀请码
    # ------------------------------------------------------------------
    def test_created_classroom_has_a_well_formed_unique_join_code(self) -> None:
        codes = {self._classroom().join_code for _ in range(12)}
        self.assertEqual(len(codes), 12)
        for code in codes:
            self.assertEqual(len(code), JOIN_CODE_LENGTH)
            self.assertTrue(all(ch in JOIN_CODE_ALPHABET for ch in code))

    def test_classroom_name_is_validated(self) -> None:
        for bad in ("", "   ", "x" * 81):
            with self.subTest(name=bad):
                with self.assertRaises(ValidationError):
                    self.service.create_classroom(self.teacher.id, bad)

    def test_join_code_is_case_and_separator_insensitive(self) -> None:
        classroom = self._classroom()
        code = classroom.join_code
        spaced = f"{code[:4]}-{code[4:]}".lower()
        joined = self.service.join_by_code(self.student.id, spaced)
        self.assertEqual(joined.id, classroom.id)

    def test_rotating_the_join_code_invalidates_the_old_one(self) -> None:
        classroom = self._classroom()
        old_code = classroom.join_code
        rotated = self.service.rotate_join_code(classroom.id, self.teacher.id)
        self.assertNotEqual(rotated.join_code, old_code)
        with self.assertRaises(InvalidJoinCode):
            self.service.join_by_code(self.student.id, old_code)
        self.assertEqual(
            self.service.join_by_code(self.student.id, rotated.join_code).id,
            classroom.id,
        )

    # ------------------------------------------------------------------
    # 跨老师授权
    # ------------------------------------------------------------------
    def test_another_teacher_cannot_read_or_touch_the_classroom(self) -> None:
        classroom = self._classroom()
        for action in (
            lambda: self.service.get_for_manage(classroom.id, self.other_teacher.id),
            lambda: self.service.get_for_view(classroom.id, self.other_teacher.id),
            lambda: self.service.update_classroom(
                classroom.id, self.other_teacher.id, name="被改名了"
            ),
            lambda: self.service.rotate_join_code(classroom.id, self.other_teacher.id),
            lambda: self.service.list_members(classroom.id, self.other_teacher.id),
            lambda: self.service.remove_member(
                classroom.id, self.student.id, self.other_teacher.id
            ),
            lambda: self.service.delete_classroom(classroom.id, self.other_teacher.id),
        ):
            with self.subTest(action=action):
                # 与"不存在"同错，避免靠错误码探测别人的班级
                with self.assertRaises(ClassroomNotFound):
                    action()
        # 确认什么都没被改动
        self.assertEqual(
            self.service.get_for_manage(classroom.id, self.teacher.id).name, "算法入门"
        )

    def test_student_cannot_manage_the_classroom_they_joined(self) -> None:
        classroom = self._classroom()
        self.service.join_by_code(self.student.id, classroom.join_code)

        # 能看，但不能管
        self.assertEqual(
            self.service.get_for_view(classroom.id, self.student.id).id, classroom.id
        )
        with self.assertRaises(ClassroomNotFound):
            self.service.get_for_manage(classroom.id, self.student.id)
        with self.assertRaises(ClassroomNotFound):
            self.service.list_members(classroom.id, self.student.id)
        with self.assertRaises(ClassroomNotFound):
            self.service.update_classroom(
                classroom.id, self.student.id, name="学生改的"
            )

    def test_non_member_cannot_even_view_the_classroom(self) -> None:
        classroom = self._classroom()
        with self.assertRaises(ClassroomNotFound):
            self.service.get_for_view(classroom.id, self.outsider.id)

    def test_admin_can_manage_any_classroom(self) -> None:
        classroom = self._classroom()
        updated = self.service.update_classroom(
            classroom.id, self.admin.id, is_admin=True, name="管理员改名"
        )
        self.assertEqual(updated.name, "管理员改名")

    def test_join_code_is_never_exposed_to_students(self) -> None:
        classroom = self._classroom()
        student_view = classroom.to_dict(include_join_code=False)
        self.assertNotIn("join_code", student_view)
        self.assertIn("join_code", classroom.to_dict(include_join_code=True))

    # ------------------------------------------------------------------
    # 入班 / 退班
    # ------------------------------------------------------------------
    def test_join_leave_and_membership_listing(self) -> None:
        classroom = self._classroom()
        self.service.join_by_code(self.student.id, classroom.join_code)

        members = self.service.list_members(classroom.id, self.teacher.id)
        self.assertEqual([m.student_id for m in members], [self.student.id])
        self.assertEqual(
            [c.id for c in self.service.list_enrolled(self.student.id)], [classroom.id]
        )
        self.assertEqual(
            self.service.get_for_manage(classroom.id, self.teacher.id).member_count, 1
        )

        self.service.leave_classroom(classroom.id, self.student.id)
        self.assertEqual(self.service.list_enrolled(self.student.id), [])
        with self.assertRaises(NotEnrolled):
            self.service.leave_classroom(classroom.id, self.student.id)

    def test_joining_twice_is_rejected(self) -> None:
        classroom = self._classroom()
        self.service.join_by_code(self.student.id, classroom.join_code)
        with self.assertRaises(AlreadyEnrolled):
            self.service.join_by_code(self.student.id, classroom.join_code)

    def test_teacher_cannot_enrol_in_their_own_classroom(self) -> None:
        classroom = self._classroom()
        with self.assertRaises(CannotEnrollSelf):
            self.service.join_by_code(self.teacher.id, classroom.join_code)

    def test_archived_classroom_rejects_new_members(self) -> None:
        classroom = self._classroom()
        self.service.update_classroom(classroom.id, self.teacher.id, is_archived=True)
        with self.assertRaises(ClassroomArchived):
            self.service.join_by_code(self.student.id, classroom.join_code)

    def test_teacher_can_remove_a_member(self) -> None:
        classroom = self._classroom()
        self.service.join_by_code(self.student.id, classroom.join_code)
        self.service.remove_member(classroom.id, self.student.id, self.teacher.id)
        self.assertEqual(self.service.list_members(classroom.id, self.teacher.id), [])
        with self.assertRaises(NotEnrolled):
            self.service.remove_member(classroom.id, self.student.id, self.teacher.id)

    def test_deleting_the_classroom_cascades_membership(self) -> None:
        classroom = self._classroom()
        self.service.join_by_code(self.student.id, classroom.join_code)
        self.service.delete_classroom(classroom.id, self.teacher.id)
        self.assertEqual(self.service.list_enrolled(self.student.id), [])
        self.assertFalse(self.service.is_member(classroom.id, self.student.id))

    def test_deleting_the_teacher_cascades_the_classroom(self) -> None:
        classroom = self._classroom()
        self.service.join_by_code(self.student.id, classroom.join_code)
        self.accounts.delete_user(self.teacher.id)
        self.assertEqual(self.service.list_enrolled(self.student.id), [])
        with self.assertRaises(ClassroomNotFound):
            self.service.get_for_view(classroom.id, self.student.id)

    # ------------------------------------------------------------------
    # 邀请码限速
    # ------------------------------------------------------------------
    def test_join_code_guessing_is_rate_limited(self) -> None:
        self._classroom()
        # 用一串格式合法但不存在的码去撞
        for index in range(MAX_JOIN_ATTEMPTS):
            with self.assertRaises(InvalidJoinCode):
                self.service.join_by_code(self.student.id, f"ZZZZZZ{index:02d}")
        with self.assertRaises(TooManyJoinAttempts) as caught:
            self.service.join_by_code(self.student.id, "ZZZZZZ99")
        self.assertGreater(caught.exception.retry_after_seconds, 0)

    def test_malformed_codes_also_count_towards_the_limit(self) -> None:
        """否则用非法格式刷接口就能绕过限速。"""
        for _ in range(MAX_JOIN_ATTEMPTS):
            with self.assertRaises(InvalidJoinCode):
                self.service.join_by_code(self.student.id, "!!")
        with self.assertRaises(TooManyJoinAttempts):
            self.service.join_by_code(self.student.id, "!!")

    def test_rate_limit_only_applies_to_the_offending_account(self) -> None:
        classroom = self._classroom()
        for index in range(MAX_JOIN_ATTEMPTS):
            with self.assertRaises(InvalidJoinCode):
                self.service.join_by_code(self.student.id, f"ZZZZZZ{index:02d}")
        # 另一个学生不受影响
        self.assertEqual(
            self.service.join_by_code(self.outsider.id, classroom.join_code).id,
            classroom.id,
        )

    def test_expired_window_resets_the_counter(self) -> None:
        classroom = self._classroom()
        stale = (
            datetime.now(timezone.utc)
            - timedelta(minutes=JOIN_ATTEMPT_WINDOW_MINUTES + 1)
        ).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO classroom_join_attempts (user_id, attempts, window_started_at) "
                "VALUES (?, ?, ?)",
                (self.student.id, MAX_JOIN_ATTEMPTS, stale),
            )
        self.assertEqual(
            self.service.join_by_code(self.student.id, classroom.join_code).id,
            classroom.id,
        )

    def test_successful_join_clears_the_counter(self) -> None:
        classroom = self._classroom()
        for index in range(MAX_JOIN_ATTEMPTS - 1):
            with self.assertRaises(InvalidJoinCode):
                self.service.join_by_code(self.student.id, f"ZZZZZZ{index:02d}")
        self.service.join_by_code(self.student.id, classroom.join_code)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM classroom_join_attempts WHERE user_id = ?",
                (self.student.id,),
            ).fetchone()
        self.assertIsNone(row)

    def test_normalize_join_code_rejects_ambiguous_characters(self) -> None:
        for bad in ("", "SHORT", "TOOLONGCODE", "AAAAAAA0", "AAAAAAAI", "AAAA AAA"):
            with self.subTest(code=bad):
                with self.assertRaises(InvalidJoinCode):
                    normalize_join_code(bad)


if __name__ == "__main__":
    unittest.main()
