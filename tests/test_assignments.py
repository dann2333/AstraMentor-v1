"""作业、提交与批改的服务层测试：授权边界、越权、逾期与分数篡改。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from services.account_service import AccountService
from services.assignment_service import (
    AssignmentNotFound,
    AssignmentService,
    STATUS_GRADED,
    STATUS_SUBMITTED,
    SubmissionNotFound,
    ValidationError,
)
from services.classroom_service import ClassroomNotFound, ClassroomService
from services.database import Database, ROLE_TEACHER


def _iso(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


class AssignmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.accounts = AccountService(self.database)
        self.classrooms = ClassroomService(self.database)
        self.service = AssignmentService(self.database, self.classrooms)

        self.teacher = self.accounts.register(
            "teacher1", "correct-horse-1", role=ROLE_TEACHER
        )
        self.other_teacher = self.accounts.register(
            "teacher2", "correct-horse-2", role=ROLE_TEACHER
        )
        self.student = self.accounts.register("student1", "correct-horse-3")
        self.classmate = self.accounts.register("student2", "correct-horse-4")
        self.outsider = self.accounts.register("student3", "correct-horse-5")

        self.classroom = self.classrooms.create_classroom(self.teacher.id, "算法入门")
        self.classrooms.join_by_code(self.student.id, self.classroom.join_code)
        self.classrooms.join_by_code(self.classmate.id, self.classroom.join_code)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _assignment(self, **overrides):
        kwargs = {
            "title": "第一次作业",
            "instructions": "完成递归练习",
            "is_published": True,
        }
        kwargs.update(overrides)
        return self.service.create_assignment(
            self.classroom.id, self.teacher.id, **kwargs
        )

    # ------------------------------------------------------------------
    # 布置与校验
    # ------------------------------------------------------------------
    def test_create_and_read_back(self) -> None:
        assignment = self._assignment(
            target_kind="node", target_topic="递归", target_node="尾递归", max_score=50
        )
        self.assertEqual(assignment.classroom_id, self.classroom.id)
        self.assertEqual(assignment.classroom_name, "算法入门")
        self.assertEqual(assignment.max_score, 50.0)
        fetched = self.service.get_for_teacher(assignment.id, self.teacher.id)
        self.assertEqual(fetched.title, "第一次作业")

    def test_target_fields_must_match_the_target_kind(self) -> None:
        cases = [
            {"target_kind": "topic"},
            {"target_kind": "node", "target_topic": "递归"},
            {"target_kind": "course"},
            {"target_kind": "document"},
        ]
        for case in cases:
            with self.subTest(**case):
                with self.assertRaises(ValidationError):
                    self._assignment(**case)

    def test_field_limits_are_enforced(self) -> None:
        for case in (
            {"title": ""},
            {"title": "x" * 121},
            {"instructions": "x" * 8001},
            {"max_score": 0},
            {"max_score": -5},
            {"due_at": "not-a-timestamp"},
            {"target_kind": "nonsense"},
        ):
            with self.subTest(**case):
                with self.assertRaises(ValidationError):
                    self._assignment(**case)

    def test_teacher_cannot_create_in_someone_elses_classroom(self) -> None:
        with self.assertRaises(ClassroomNotFound):
            self.service.create_assignment(
                self.classroom.id, self.other_teacher.id, title="偷偷布置"
            )
        self.assertEqual(
            self.service.list_for_teacher(self.classroom.id, self.teacher.id), []
        )

    def test_update_preserves_untouched_fields_and_can_clear_the_due_date(self) -> None:
        assignment = self._assignment(due_at=_iso(days=3), max_score=80)
        updated = self.service.update_assignment(
            assignment.id, self.teacher.id, title="改了标题"
        )
        self.assertEqual(updated.title, "改了标题")
        self.assertEqual(updated.instructions, "完成递归练习")
        self.assertEqual(updated.max_score, 80.0)
        self.assertIsNotNone(updated.due_at)

        cleared = self.service.update_assignment(
            assignment.id, self.teacher.id, clear_due_at=True
        )
        self.assertIsNone(cleared.due_at)

    def test_another_teacher_cannot_touch_the_assignment(self) -> None:
        assignment = self._assignment()
        for action in (
            lambda: self.service.get_for_teacher(assignment.id, self.other_teacher.id),
            lambda: self.service.update_assignment(
                assignment.id, self.other_teacher.id, title="改标题"
            ),
            lambda: self.service.delete_assignment(assignment.id, self.other_teacher.id),
            lambda: self.service.list_submissions(assignment.id, self.other_teacher.id),
            lambda: self.service.grade(
                assignment.id, self.student.id, self.other_teacher.id, score=100
            ),
        ):
            with self.subTest(action=action):
                with self.assertRaises(AssignmentNotFound):
                    action()
        self.assertEqual(
            self.service.get_for_teacher(assignment.id, self.teacher.id).title,
            "第一次作业",
        )

    # ------------------------------------------------------------------
    # 学生可见性
    # ------------------------------------------------------------------
    def test_draft_assignments_are_invisible_to_students(self) -> None:
        draft = self._assignment(is_published=False)
        self.assertEqual(self.service.list_for_student(self.student.id), [])
        with self.assertRaises(AssignmentNotFound):
            self.service.get_for_student(draft.id, self.student.id)
        with self.assertRaises(AssignmentNotFound):
            self.service.submit(draft.id, self.student.id, content="想抢先交")

        # 老师侧仍然看得到草稿
        self.assertEqual(
            [a.id for a in self.service.list_for_teacher(self.classroom.id, self.teacher.id)],
            [draft.id],
        )

    def test_outsider_cannot_see_or_submit(self) -> None:
        assignment = self._assignment()
        self.assertEqual(self.service.list_for_student(self.outsider.id), [])
        with self.assertRaises(AssignmentNotFound):
            self.service.get_for_student(assignment.id, self.outsider.id)
        with self.assertRaises(AssignmentNotFound):
            self.service.submit(assignment.id, self.outsider.id, content="外人提交")

    def test_removed_student_loses_access(self) -> None:
        assignment = self._assignment()
        self.service.submit(assignment.id, self.student.id, content="交了")
        self.classrooms.remove_member(
            self.classroom.id, self.student.id, self.teacher.id
        )
        with self.assertRaises(AssignmentNotFound):
            self.service.get_for_student(assignment.id, self.student.id)
        self.assertEqual(self.service.list_for_student(self.student.id), [])

    def test_student_list_includes_only_their_own_submission(self) -> None:
        assignment = self._assignment()
        self.service.submit(assignment.id, self.student.id, content="我的答案")
        self.service.submit(assignment.id, self.classmate.id, content="同学的答案")

        items = self.service.list_for_student(self.student.id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["my_submission"]["content"], "我的答案")
        self.assertEqual(items[0]["my_submission"]["student_id"], self.student.id)

    def test_student_cannot_read_a_classmates_submission(self) -> None:
        assignment = self._assignment()
        self.service.submit(assignment.id, self.classmate.id, content="同学的答案")
        with self.assertRaises(SubmissionNotFound):
            self.service.get_my_submission(assignment.id, self.student.id)
        # 学生也拿不到全班清单
        with self.assertRaises(AssignmentNotFound):
            self.service.list_submissions(assignment.id, self.student.id)

    # ------------------------------------------------------------------
    # 提交
    # ------------------------------------------------------------------
    def test_submit_then_resubmit_overwrites_and_clears_the_grade(self) -> None:
        assignment = self._assignment()
        first = self.service.submit(assignment.id, self.student.id, content="初稿")
        graded = self.service.grade(
            assignment.id, self.student.id, self.teacher.id, score=70, feedback="还行"
        )
        self.assertEqual(graded.status, STATUS_GRADED)
        self.assertEqual(graded.score, 70.0)

        again = self.service.submit(assignment.id, self.student.id, content="改进稿")
        self.assertEqual(again.id, first.id)
        self.assertEqual(again.content, "改进稿")
        # 旧分数针对旧答案，必须一起作废
        self.assertIsNone(again.score)
        self.assertEqual(again.feedback, "")
        self.assertIsNone(again.graded_by)
        self.assertIsNone(again.graded_at)
        self.assertEqual(again.status, STATUS_SUBMITTED)
        self.assertEqual(again.created_at, first.created_at)

    def test_empty_or_oversize_content_is_rejected(self) -> None:
        assignment = self._assignment()
        for bad in ("", "   ", "x" * 40001):
            with self.subTest(length=len(bad)):
                with self.assertRaises(ValidationError):
                    self.service.submit(assignment.id, self.student.id, content=bad)

    def test_late_submission_is_accepted_but_flagged(self) -> None:
        assignment = self._assignment(due_at=_iso(minutes=-5))
        submission = self.service.submit(
            assignment.id, self.student.id, content="迟交"
        )
        self.assertTrue(submission.is_late)

    def test_on_time_submission_is_not_flagged(self) -> None:
        assignment = self._assignment(due_at=_iso(days=1))
        submission = self.service.submit(assignment.id, self.student.id, content="按时")
        self.assertFalse(submission.is_late)

    def test_assignment_without_due_date_is_never_late(self) -> None:
        assignment = self._assignment()
        submission = self.service.submit(assignment.id, self.student.id, content="随时")
        self.assertFalse(submission.is_late)

    # ------------------------------------------------------------------
    # 批改
    # ------------------------------------------------------------------
    def test_grade_and_score_bounds(self) -> None:
        assignment = self._assignment(max_score=50)
        self.service.submit(assignment.id, self.student.id, content="答案")

        graded = self.service.grade(
            assignment.id, self.student.id, self.teacher.id, score=49.5, feedback="好"
        )
        self.assertEqual(graded.score, 49.5)
        self.assertEqual(graded.graded_by, self.teacher.id)
        self.assertIsNotNone(graded.graded_at)

        for bad in (-1, 50.1, 1000):
            with self.subTest(score=bad):
                with self.assertRaises(ValidationError):
                    self.service.grade(
                        assignment.id, self.student.id, self.teacher.id, score=bad
                    )
        # 失败的批改不能留下副作用
        self.assertEqual(
            self.service.get_my_submission(assignment.id, self.student.id).score, 49.5
        )

    def test_grading_a_missing_submission_is_404(self) -> None:
        assignment = self._assignment()
        with self.assertRaises(SubmissionNotFound):
            self.service.grade(
                assignment.id, self.student.id, self.teacher.id, score=10
            )

    def test_score_can_be_withdrawn_leaving_feedback(self) -> None:
        assignment = self._assignment()
        self.service.submit(assignment.id, self.student.id, content="答案")
        self.service.grade(assignment.id, self.student.id, self.teacher.id, score=80)
        withdrawn = self.service.grade(
            assignment.id, self.student.id, self.teacher.id, score=None, feedback="重做"
        )
        self.assertIsNone(withdrawn.score)
        self.assertEqual(withdrawn.status, STATUS_SUBMITTED)
        self.assertEqual(withdrawn.feedback, "重做")

    def test_students_see_their_grade_but_only_teacher_sees_the_class(self) -> None:
        assignment = self._assignment()
        self.service.submit(assignment.id, self.student.id, content="A")
        self.service.submit(assignment.id, self.classmate.id, content="B")
        self.service.grade(assignment.id, self.student.id, self.teacher.id, score=90)

        mine = self.service.get_my_submission(assignment.id, self.student.id)
        self.assertEqual(mine.score, 90.0)

        everyone = self.service.list_submissions(assignment.id, self.teacher.id)
        self.assertEqual(len(everyone), 2)
        self.assertEqual(
            {item.student_username for item in everyone}, {"student1", "student2"}
        )

    # ------------------------------------------------------------------
    # 汇总与级联
    # ------------------------------------------------------------------
    def test_classroom_progress_counts_only_published_assignments(self) -> None:
        published = self._assignment(due_at=_iso(minutes=-1))
        self._assignment(title="草稿", is_published=False)
        self.service.submit(published.id, self.student.id, content="迟交")
        self.service.grade(published.id, self.student.id, self.teacher.id, score=60)

        rows = {
            row["student_id"]: row
            for row in self.service.classroom_progress(
                self.classroom.id, self.teacher.id
            )
        }
        self.assertEqual(rows[self.student.id]["published_assignments"], 1)
        self.assertEqual(rows[self.student.id]["submitted_count"], 1)
        self.assertEqual(rows[self.student.id]["graded_count"], 1)
        self.assertEqual(rows[self.student.id]["late_count"], 1)
        self.assertEqual(rows[self.student.id]["average_score"], 60.0)

        self.assertEqual(rows[self.classmate.id]["submitted_count"], 0)
        self.assertIsNone(rows[self.classmate.id]["average_score"])

    def test_progress_is_teacher_only(self) -> None:
        self._assignment()
        with self.assertRaises(ClassroomNotFound):
            self.service.classroom_progress(self.classroom.id, self.student.id)
        with self.assertRaises(ClassroomNotFound):
            self.service.classroom_progress(self.classroom.id, self.other_teacher.id)

    def test_deleting_the_assignment_cascades_submissions(self) -> None:
        assignment = self._assignment()
        self.service.submit(assignment.id, self.student.id, content="答案")
        self.service.delete_assignment(assignment.id, self.teacher.id)
        with self.assertRaises(AssignmentNotFound):
            self.service.get_for_student(assignment.id, self.student.id)
        self.assertEqual(self.service.list_for_student(self.student.id), [])

    def test_deleting_the_classroom_cascades_assignments(self) -> None:
        assignment = self._assignment()
        self.service.submit(assignment.id, self.student.id, content="答案")
        self.classrooms.delete_classroom(self.classroom.id, self.teacher.id)
        with self.assertRaises(AssignmentNotFound):
            self.service.get_for_teacher(assignment.id, self.teacher.id)

    def test_deleting_a_student_keeps_the_grader_reference_sane(self) -> None:
        assignment = self._assignment()
        self.service.submit(assignment.id, self.student.id, content="答案")
        self.service.grade(assignment.id, self.student.id, self.teacher.id, score=75)
        self.accounts.delete_user(self.student.id)
        self.assertEqual(self.service.list_submissions(assignment.id, self.teacher.id), [])


if __name__ == "__main__":
    unittest.main()
