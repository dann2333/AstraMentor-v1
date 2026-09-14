"""班级 / 作业 / 角色接口的 HTTP 层测试。

服务层测试覆盖了授权规则本身，这里锁的是"规则真的挂在了路由上"：
状态码、字段可见性、以及请求体里多塞字段会不会被静默接受。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from backend.app import app
from backend.classroom_api import get_assignment_service, get_classroom_service
from backend.dependencies import get_account_service
from backend.user_data_api import get_user_data_repository
from services.account_service import AccountService
from services.assignment_service import AssignmentService
from services.classroom_service import ClassroomService, MAX_JOIN_ATTEMPTS
from services.database import Database, ROLE_TEACHER
from services.user_data_repository import UserDataRepository


def _iso(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


class ClassroomApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.accounts = AccountService(self.database, token_ttl_hours=1)
        self.classrooms = ClassroomService(self.database)
        self.assignments = AssignmentService(self.database, self.classrooms)

        app.dependency_overrides[get_account_service] = lambda: self.accounts
        app.dependency_overrides[get_classroom_service] = lambda: self.classrooms
        app.dependency_overrides[get_assignment_service] = lambda: self.assignments
        app.dependency_overrides[get_user_data_repository] = lambda: UserDataRepository(
            self.database
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.temp.cleanup()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _register(self, username: str, role: str = "student") -> dict:
        response = self.client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": "correct-horse-battery",
                "role": role,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    @staticmethod
    def _auth(token: dict) -> dict:
        return {"Authorization": f"Bearer {token['access_token']}"}

    def _teacher_with_class(self) -> tuple[dict, dict]:
        teacher = self._register("teacher1", ROLE_TEACHER)
        response = self.client.post(
            "/api/classrooms",
            json={"name": "算法入门", "description": "每周一次"},
            headers=self._auth(teacher),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return teacher, response.json()

    # ------------------------------------------------------------------
    # 注册与角色
    # ------------------------------------------------------------------
    def test_registration_defaults_to_student_and_reports_the_role(self) -> None:
        token = self._register("plain")
        self.assertEqual(token["user"]["role"], "student")
        me = self.client.get("/api/auth/me", headers=self._auth(token))
        self.assertEqual(me.json()["role"], "student")

    def test_teacher_role_can_be_chosen_at_registration(self) -> None:
        self.assertEqual(self._register("teacher1", ROLE_TEACHER)["user"]["role"], "teacher")

    def test_admin_role_cannot_be_self_assigned(self) -> None:
        response = self.client.post(
            "/api/auth/register",
            json={
                "username": "wannabe",
                "password": "correct-horse-battery",
                "role": "admin",
            },
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_unknown_registration_fields_are_rejected(self) -> None:
        """否则请求体里塞 is_active / role 之类的字段会被静默忽略。"""
        response = self.client.post(
            "/api/auth/register",
            json={
                "username": "sneaky",
                "password": "correct-horse-battery",
                "is_active": True,
                "id": "chosen-by-me",
            },
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_reserved_guest_username_cannot_be_registered(self) -> None:
        response = self.client.post(
            "/api/auth/register",
            json={"username": "__anonymous__", "password": "correct-horse-battery"},
        )
        # 下划线开头本身就不符合用户名规则
        self.assertEqual(response.status_code, 422, response.text)

    def test_only_admin_can_change_roles(self) -> None:
        teacher = self._register("teacher1", ROLE_TEACHER)
        victim = self._register("victim")
        response = self.client.put(
            f"/api/admin/users/{victim['user']['id']}/role",
            json={"role": "admin"},
            headers=self._auth(teacher),
        )
        self.assertEqual(response.status_code, 403, response.text)

        self.accounts.set_role(teacher["user"]["id"], "admin")
        promoted = self.client.put(
            f"/api/admin/users/{victim['user']['id']}/role",
            json={"role": "teacher"},
            headers=self._auth(teacher),
        )
        self.assertEqual(promoted.status_code, 200, promoted.text)
        self.assertEqual(promoted.json()["role"], "teacher")

    def test_guest_owner_row_is_not_listed_to_admins(self) -> None:
        admin = self._register("root1")
        self.accounts.set_role(admin["user"]["id"], "admin")
        response = self.client.get("/api/admin/users", headers=self._auth(admin))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(
            "__anonymous__", {item["username"] for item in response.json()}
        )

    def test_system_account_role_cannot_be_changed(self) -> None:
        admin = self._register("root1")
        self.accounts.set_role(admin["user"]["id"], "admin")
        response = self.client.put(
            "/api/admin/users/anonymous/role",
            json={"role": "admin"},
            headers=self._auth(admin),
        )
        self.assertEqual(response.status_code, 409, response.text)

    # ------------------------------------------------------------------
    # 班级
    # ------------------------------------------------------------------
    def test_students_cannot_create_classrooms(self) -> None:
        student = self._register("student1")
        response = self.client.post(
            "/api/classrooms", json={"name": "我要建班"}, headers=self._auth(student)
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_classroom_endpoints_require_authentication(self) -> None:
        for method, path in (
            ("post", "/api/classrooms"),
            ("get", "/api/classrooms/taught"),
            ("get", "/api/classrooms/enrolled"),
            ("post", "/api/classrooms/join"),
            ("get", "/api/me/assignments"),
            ("get", "/api/me/sessions"),
        ):
            with self.subTest(method=method, path=path):
                kwargs = {"json": {}} if method == "post" else {}
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 401, response.text)

    def test_join_flow_hides_the_code_from_students(self) -> None:
        _, classroom = self._teacher_with_class()
        student = self._register("student1")
        joined = self.client.post(
            "/api/classrooms/join",
            json={"join_code": classroom["join_code"]},
            headers=self._auth(student),
        )
        self.assertEqual(joined.status_code, 200, joined.text)
        self.assertNotIn("join_code", joined.json())

        detail = self.client.get(
            f"/api/classrooms/{classroom['id']}", headers=self._auth(student)
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertNotIn("join_code", detail.json())

        enrolled = self.client.get(
            "/api/classrooms/enrolled", headers=self._auth(student)
        )
        self.assertEqual(
            [item["id"] for item in enrolled.json()["classrooms"]], [classroom["id"]]
        )

    def test_wrong_join_code_is_404_and_then_rate_limited(self) -> None:
        self._teacher_with_class()
        student = self._register("student1")
        for index in range(MAX_JOIN_ATTEMPTS):
            response = self.client.post(
                "/api/classrooms/join",
                json={"join_code": f"ZZZZZZ{index:02d}"},
                headers=self._auth(student),
            )
            self.assertEqual(response.status_code, 404, response.text)
        limited = self.client.post(
            "/api/classrooms/join",
            json={"join_code": "ZZZZZZ99"},
            headers=self._auth(student),
        )
        self.assertEqual(limited.status_code, 429, limited.text)
        self.assertIn("Retry-After", limited.headers)

    def test_cross_teacher_access_is_404_not_403(self) -> None:
        _, classroom = self._teacher_with_class()
        intruder = self._register("teacher2", ROLE_TEACHER)
        for method, path, body in (
            ("get", f"/api/classrooms/{classroom['id']}/members", None),
            ("get", f"/api/classrooms/{classroom['id']}/progress", None),
            ("patch", f"/api/classrooms/{classroom['id']}", {"name": "改名"}),
            ("post", f"/api/classrooms/{classroom['id']}/join-code/rotate", None),
            ("delete", f"/api/classrooms/{classroom['id']}", None),
            (
                "post",
                f"/api/classrooms/{classroom['id']}/assignments",
                {"title": "偷偷布置"},
            ),
        ):
            with self.subTest(path=path, method=method):
                kwargs = {"headers": self._auth(intruder)}
                if body is not None:
                    kwargs["json"] = body
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 404, response.text)

    def test_student_cannot_list_members(self) -> None:
        _, classroom = self._teacher_with_class()
        student = self._register("student1")
        self.client.post(
            "/api/classrooms/join",
            json={"join_code": classroom["join_code"]},
            headers=self._auth(student),
        )
        response = self.client.get(
            f"/api/classrooms/{classroom['id']}/members", headers=self._auth(student)
        )
        # 学生根本不是老师，先被角色守卫拦下
        self.assertEqual(response.status_code, 403, response.text)

    def test_rotating_the_code_locks_out_the_old_one(self) -> None:
        teacher, classroom = self._teacher_with_class()
        rotated = self.client.post(
            f"/api/classrooms/{classroom['id']}/join-code/rotate",
            headers=self._auth(teacher),
        )
        self.assertEqual(rotated.status_code, 200, rotated.text)
        new_code = rotated.json()["join_code"]
        self.assertNotEqual(new_code, classroom["join_code"])

        student = self._register("student1")
        stale = self.client.post(
            "/api/classrooms/join",
            json={"join_code": classroom["join_code"]},
            headers=self._auth(student),
        )
        self.assertEqual(stale.status_code, 404, stale.text)

    # ------------------------------------------------------------------
    # 作业与批改
    # ------------------------------------------------------------------
    def _classroom_with_student(self):
        teacher, classroom = self._teacher_with_class()
        student = self._register("student1")
        self.client.post(
            "/api/classrooms/join",
            json={"join_code": classroom["join_code"]},
            headers=self._auth(student),
        )
        return teacher, classroom, student

    def _publish(self, teacher, classroom, **overrides) -> dict:
        body = {"title": "第一次作业", "instructions": "做完递归", "is_published": True}
        body.update(overrides)
        response = self.client.post(
            f"/api/classrooms/{classroom['id']}/assignments",
            json=body,
            headers=self._auth(teacher),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_full_assign_submit_grade_round_trip(self) -> None:
        teacher, classroom, student = self._classroom_with_student()
        assignment = self._publish(teacher, classroom, max_score=50, due_at=_iso(days=2))

        listing = self.client.get("/api/me/assignments", headers=self._auth(student))
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(
            [item["id"] for item in listing.json()["assignments"]], [assignment["id"]]
        )
        self.assertIsNone(listing.json()["assignments"][0]["my_submission"])

        submitted = self.client.put(
            f"/api/me/assignments/{assignment['id']}/submission",
            json={"content": "我的答案"},
            headers=self._auth(student),
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertFalse(submitted.json()["is_late"])
        self.assertIsNone(submitted.json()["score"])

        submissions = self.client.get(
            f"/api/assignments/{assignment['id']}/submissions",
            headers=self._auth(teacher),
        )
        self.assertEqual(submissions.status_code, 200, submissions.text)
        self.assertEqual(len(submissions.json()["submissions"]), 1)
        self.assertEqual(
            submissions.json()["submissions"][0]["student_username"], "student1"
        )

        graded = self.client.put(
            f"/api/assignments/{assignment['id']}/submissions/"
            f"{student['user']['id']}/grade",
            json={"score": 45, "feedback": "不错"},
            headers=self._auth(teacher),
        )
        self.assertEqual(graded.status_code, 200, graded.text)
        self.assertEqual(graded.json()["score"], 45.0)

        mine = self.client.get(
            f"/api/me/assignments/{assignment['id']}/submission",
            headers=self._auth(student),
        )
        self.assertEqual(mine.json()["score"], 45.0)
        self.assertEqual(mine.json()["feedback"], "不错")

    def test_student_cannot_smuggle_a_score_into_their_submission(self) -> None:
        teacher, classroom, student = self._classroom_with_student()
        assignment = self._publish(teacher, classroom)
        response = self.client.put(
            f"/api/me/assignments/{assignment['id']}/submission",
            json={"content": "答案", "score": 100, "status": "graded"},
            headers=self._auth(student),
        )
        # 多余字段直接 422，不会被静默丢弃
        self.assertEqual(response.status_code, 422, response.text)

        clean = self.client.put(
            f"/api/me/assignments/{assignment['id']}/submission",
            json={"content": "答案"},
            headers=self._auth(student),
        )
        self.assertIsNone(clean.json()["score"])
        self.assertEqual(clean.json()["status"], "submitted")

    def test_student_cannot_grade_anything(self) -> None:
        teacher, classroom, student = self._classroom_with_student()
        assignment = self._publish(teacher, classroom)
        self.client.put(
            f"/api/me/assignments/{assignment['id']}/submission",
            json={"content": "答案"},
            headers=self._auth(student),
        )
        response = self.client.put(
            f"/api/assignments/{assignment['id']}/submissions/"
            f"{student['user']['id']}/grade",
            json={"score": 100},
            headers=self._auth(student),
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_out_of_range_score_is_rejected(self) -> None:
        teacher, classroom, student = self._classroom_with_student()
        assignment = self._publish(teacher, classroom, max_score=50)
        self.client.put(
            f"/api/me/assignments/{assignment['id']}/submission",
            json={"content": "答案"},
            headers=self._auth(student),
        )
        response = self.client.put(
            f"/api/assignments/{assignment['id']}/submissions/"
            f"{student['user']['id']}/grade",
            json={"score": 51},
            headers=self._auth(teacher),
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_draft_assignment_is_404_for_students(self) -> None:
        teacher, classroom, student = self._classroom_with_student()
        draft = self._publish(teacher, classroom, is_published=False)
        for method, path, body in (
            ("get", f"/api/me/assignments/{draft['id']}", None),
            (
                "put",
                f"/api/me/assignments/{draft['id']}/submission",
                {"content": "抢先交"},
            ),
        ):
            with self.subTest(path=path):
                kwargs = {"headers": self._auth(student)}
                if body is not None:
                    kwargs["json"] = body
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(
            self.client.get(
                "/api/me/assignments", headers=self._auth(student)
            ).json()["assignments"],
            [],
        )

    def test_non_member_gets_404_on_every_assignment_route(self) -> None:
        teacher, classroom, _ = self._classroom_with_student()
        assignment = self._publish(teacher, classroom)
        outsider = self._register("outsider")
        for method, path, body in (
            ("get", f"/api/me/assignments/{assignment['id']}", None),
            (
                "put",
                f"/api/me/assignments/{assignment['id']}/submission",
                {"content": "外人"},
            ),
            ("get", f"/api/me/assignments/{assignment['id']}/submission", None),
        ):
            with self.subTest(path=path):
                kwargs = {"headers": self._auth(outsider)}
                if body is not None:
                    kwargs["json"] = body
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 404, response.text)

    def test_teacher_from_another_class_cannot_read_submissions(self) -> None:
        teacher, classroom, student = self._classroom_with_student()
        assignment = self._publish(teacher, classroom)
        self.client.put(
            f"/api/me/assignments/{assignment['id']}/submission",
            json={"content": "答案"},
            headers=self._auth(student),
        )
        intruder = self._register("teacher2", ROLE_TEACHER)
        for method, path, body in (
            ("get", f"/api/assignments/{assignment['id']}/submissions", None),
            ("patch", f"/api/assignments/{assignment['id']}", {"title": "改"}),
            ("delete", f"/api/assignments/{assignment['id']}", None),
            (
                "put",
                f"/api/assignments/{assignment['id']}/submissions/"
                f"{student['user']['id']}/grade",
                {"score": 0},
            ),
        ):
            with self.subTest(path=path, method=method):
                kwargs = {"headers": self._auth(intruder)}
                if body is not None:
                    kwargs["json"] = body
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 404, response.text)

    def test_late_submission_is_flagged(self) -> None:
        teacher, classroom, student = self._classroom_with_student()
        assignment = self._publish(teacher, classroom, due_at=_iso(minutes=-1))
        response = self.client.put(
            f"/api/me/assignments/{assignment['id']}/submission",
            json={"content": "迟交"},
            headers=self._auth(student),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["is_late"])

    def test_teacher_progress_panel(self) -> None:
        teacher, classroom, student = self._classroom_with_student()
        assignment = self._publish(teacher, classroom)
        self.client.put(
            f"/api/me/assignments/{assignment['id']}/submission",
            json={"content": "答案"},
            headers=self._auth(student),
        )
        self.client.put(
            f"/api/assignments/{assignment['id']}/submissions/"
            f"{student['user']['id']}/grade",
            json={"score": 88},
            headers=self._auth(teacher),
        )
        response = self.client.get(
            f"/api/classrooms/{classroom['id']}/progress", headers=self._auth(teacher)
        )
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["students"][0]
        self.assertEqual(row["submitted_count"], 1)
        self.assertEqual(row["graded_count"], 1)
        self.assertEqual(row["average_score"], 88.0)

    def test_unknown_assignment_fields_are_rejected(self) -> None:
        teacher, classroom = self._teacher_with_class()
        response = self.client.post(
            f"/api/classrooms/{classroom['id']}/assignments",
            json={"title": "作业", "classroom_id": "somewhere-else"},
            headers=self._auth(teacher),
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
