"""对抗式复核发现的问题的回归测试。

每一条都对应一个已经复现过的具体攻击或故障，去掉修复就会失败。
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import backend.doc_api as doc_api
from backend.app import app
from backend.classroom_api import get_assignment_service, get_classroom_service
from backend.dependencies import get_account_service
from core.learner_state import LearnerState
from services.account_service import AccountService
from services.assignment_service import AssignmentService, ClassroomArchived
from services.classroom_service import (
    AlreadyEnrolled,
    CannotEnrollSelf,
    ClassroomArchived as ClassroomArchivedError,
    ClassroomService,
    InvalidJoinCode,
    MAX_JOIN_ATTEMPTS,
    TooManyJoinAttempts,
)
from services.database import ANONYMOUS_OWNER_ID, Database, ROLE_TEACHER
from services.learning_store import (
    InvalidDocumentId,
    LearningStore,
    SqlLearnerStateStore,
    owner_upload_path,
    validate_doc_id,
)
from services.pdf_parser import DocumentChunk, DocumentContext


SHARED_DOC_ID = "a" * 32


class DocumentPathTraversalTests(unittest.TestCase):
    """doc_id 同时是存储主键和磁盘文件名，必须挡住 ``../``。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = Database(self.root / "astramentor.db")
        self.accounts = AccountService(self.database, token_ttl_hours=1)
        self.store = LearningStore(self.database)

        app.dependency_overrides[get_account_service] = lambda: self.accounts
        self.patches = [
            patch.object(doc_api, "learning_store", self.store),
            patch.object(doc_api, "_UPLOAD_ROOT", self.root / "uploads"),
            patch.object(
                doc_api,
                "parse_pdf",
                lambda data, name: DocumentContext(
                    doc_id=SHARED_DOC_ID,
                    filename=name,
                    total_pages=1,
                    chunks=[
                        DocumentChunk(
                            chunk_id="c1", content="x", page_start=1, page_end=1
                        )
                    ],
                    full_text="x",
                ),
            ),
            patch("services.learning_service.learning_store", self.store),
            patch("services.learning_service.APIClient", return_value=Mock()),
        ]
        for item in self.patches:
            item.start()
        doc_api._doc_cache.clear()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        doc_api._doc_cache.clear()
        app.dependency_overrides.clear()
        self.temp.cleanup()

    def _register(self, username: str) -> tuple[dict, str]:
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": "correct-horse-battery"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        return (
            {"Authorization": f"Bearer {payload['access_token']}"},
            payload["user"]["id"],
        )

    def _upload(self, headers: dict, name: str) -> None:
        response = self.client.post(
            "/api/doc/upload",
            files={"file": (name, b"%PDF-1.4", "application/pdf")},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_traversal_in_doc_id_cannot_delete_another_accounts_pdf(self) -> None:
        victim, victim_id = self._register("victim1")
        attacker, _ = self._register("attack1")
        self._upload(victim, "victim.pdf")
        self._upload(attacker, "attacker.pdf")

        victim_pdf = self.root / "uploads" / victim_id / f"{SHARED_DOC_ID}.pdf"
        self.assertTrue(victim_pdf.exists())

        response = self.client.delete(
            "/api/doc/graph/delete",
            params={"doc_id": f"../{victim_id}/{SHARED_DOC_ID}"},
            headers=attacker,
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertTrue(victim_pdf.exists(), "受害者的 PDF 被删掉了")

    def test_traversal_in_doc_id_is_rejected_on_every_route(self) -> None:
        attacker, _ = self._register("attack1")
        self._upload(attacker, "attacker.pdf")
        for doc_id in ("../etc/passwd", "..", "a/b", "a\\b", "", " "):
            with self.subTest(doc_id=doc_id):
                response = self.client.post(
                    "/api/doc/learning/start",
                    json={"doc_id": doc_id, "node_name": "递归"},
                    headers=attacker,
                )
                self.assertEqual(response.status_code, 404, response.text)

    def test_upload_path_helper_refuses_to_escape(self) -> None:
        root = self.root / "uploads"
        safe = owner_upload_path(ANONYMOUS_OWNER_ID, SHARED_DOC_ID, root)
        self.assertEqual(safe.parent.name, ANONYMOUS_OWNER_ID)
        for bad in ("../x", "a/b", "..", ""):
            with self.subTest(doc_id=bad):
                with self.assertRaises(InvalidDocumentId):
                    owner_upload_path(ANONYMOUS_OWNER_ID, bad, root)
        with self.assertRaises(InvalidDocumentId):
            validate_doc_id("../escape")


class AuthorizationSchemeTests(unittest.TestCase):
    """带了 Authorization 头就必须是合法 Bearer，不能悄悄降级成访客。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.accounts = AccountService(self.database, token_ttl_hours=1)
        app.dependency_overrides[get_account_service] = lambda: self.accounts
        from backend.user_data_api import get_user_data_repository
        from services.user_data_repository import UserDataRepository

        app.dependency_overrides[get_user_data_repository] = (
            lambda: UserDataRepository(self.database)
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.temp.cleanup()

    def test_non_bearer_scheme_is_401_not_guest(self) -> None:
        registered = self.client.post(
            "/api/auth/register",
            json={"username": "alice1", "password": "correct-horse-battery"},
        ).json()
        token = registered["access_token"]

        # 正常路径仍然可用
        self.assertEqual(
            self.client.get(
                "/api/sessions", headers={"Authorization": f"Bearer {token}"}
            ).status_code,
            200,
        )
        # 完全不带头 = 访客
        self.assertEqual(self.client.get("/api/sessions").status_code, 200)

        for header in (f"Token {token}", f"Basic {token}", token, "Bearer"):
            with self.subTest(header=header):
                response = self.client.get(
                    "/api/sessions", headers={"Authorization": header}
                )
                self.assertEqual(response.status_code, 401, response.text)


class JoinRateLimitConcurrencyTests(unittest.TestCase):
    """限速必须在一个写事务里自增并判断，否则并发一批就全放过去了。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.accounts = AccountService(self.database)
        self.service = ClassroomService(self.database)
        self.teacher = self.accounts.register(
            "teacher1", "correct-horse-1", role=ROLE_TEACHER
        )
        self.student = self.accounts.register("student1", "correct-horse-2")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_a_parallel_burst_is_still_rate_limited(self) -> None:
        self.service.create_classroom(self.teacher.id, "算法入门")
        attempts = 40
        barrier = threading.Barrier(attempts)
        outcomes: list[str] = []
        lock = threading.Lock()

        def guess() -> None:
            barrier.wait()
            try:
                self.service.join_by_code(self.student.id, "ZZZZZZZZ")
                result = "joined"
            except TooManyJoinAttempts:
                result = "limited"
            except InvalidJoinCode:
                result = "invalid"
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=guess) for _ in range(attempts)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertNotIn("joined", outcomes)
        self.assertLessEqual(outcomes.count("invalid"), MAX_JOIN_ATTEMPTS)
        self.assertGreaterEqual(
            outcomes.count("limited"), attempts - MAX_JOIN_ATTEMPTS
        )


class ArchivedClassroomTests(unittest.TestCase):
    """结课后学生不能再提交 —— 重交会作废老师已经给出的分数。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.accounts = AccountService(self.database, token_ttl_hours=1)
        self.classrooms = ClassroomService(self.database)
        self.assignments = AssignmentService(self.database, self.classrooms)

        app.dependency_overrides[get_account_service] = lambda: self.accounts
        app.dependency_overrides[get_classroom_service] = lambda: self.classrooms
        app.dependency_overrides[get_assignment_service] = lambda: self.assignments
        self.client = TestClient(app)

        self.teacher = self.accounts.register(
            "teacher1", "correct-horse-1", role=ROLE_TEACHER
        )
        self.student = self.accounts.register("student1", "correct-horse-2")
        self.classroom = self.classrooms.create_classroom(self.teacher.id, "算法入门")
        self.classrooms.join_by_code(self.student.id, self.classroom.join_code)
        self.assignment = self.assignments.create_assignment(
            self.classroom.id, self.teacher.id, title="期末", is_published=True
        )

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.temp.cleanup()

    def test_grades_survive_after_the_classroom_is_archived(self) -> None:
        self.assignments.submit(self.assignment.id, self.student.id, content="答案")
        self.assignments.grade(
            self.assignment.id, self.student.id, self.teacher.id, score=95
        )
        self.classrooms.update_classroom(
            self.classroom.id, self.teacher.id, is_archived=True
        )

        with self.assertRaises(ClassroomArchived):
            self.assignments.submit(
                self.assignment.id, self.student.id, content="偷偷改掉"
            )

        kept = self.assignments.get_my_submission(self.assignment.id, self.student.id)
        self.assertEqual(kept.score, 95.0)
        self.assertEqual(kept.content, "答案")

    def test_the_route_reports_409_rather_than_accepting_it(self) -> None:
        token = self.client.post(
            "/api/auth/login",
            json={"username": "student1", "password": "correct-horse-2"},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        self.assertEqual(
            self.client.put(
                f"/api/me/assignments/{self.assignment.id}/submission",
                json={"content": "开课期间交的"},
                headers=headers,
            ).status_code,
            200,
        )
        self.classrooms.update_classroom(
            self.classroom.id, self.teacher.id, is_archived=True
        )
        response = self.client.put(
            f"/api/me/assignments/{self.assignment.id}/submission",
            json={"content": "结课后再交"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 409, response.text)


class LearnerStatePreservationTests(unittest.TestCase):
    """本版本解析不了的条目要原样保留，不能被下一次自动保存抹掉。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = LearningStore(Database(Path(self.temp.name) / "astramentor.db"))
        self.scope = "state:default"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _state(self) -> LearnerState:
        return LearnerState(
            store=SqlLearnerStateStore(ANONYMOUS_OWNER_ID, self.scope, self.store)
        )

    def test_unknown_fields_survive_an_unrelated_update(self) -> None:
        """回滚到旧版本的 pod 不该把新版本写入的数据抹掉。"""
        self.store.write_learner_state(
            ANONYMOUS_OWNER_ID,
            self.scope,
            {
                "递归": {
                    "name": "递归",
                    "actual_mastery": 0.9,
                    "review_due_at": "2026-10-01",
                },
                "排序": {"name": "排序", "actual_mastery": 0.4},
                "坏数据": "根本不是对象",
            },
        )
        state = self._state()
        # 解析不了的不进内存，也就不会参与计算
        self.assertEqual(sorted(state.knowledge_points), ["排序"])

        state.update_mastery("排序", 0.5, 0.5, "做了一题")

        stored = self.store.read_learner_state(ANONYMOUS_OWNER_ID, self.scope)
        self.assertEqual(sorted(stored), ["坏数据", "排序", "递归"])
        self.assertEqual(stored["递归"]["review_due_at"], "2026-10-01")
        self.assertEqual(stored["排序"]["actual_mastery"], 0.5)

    def test_rewriting_the_same_point_takes_over_the_preserved_copy(self) -> None:
        self.store.write_learner_state(
            ANONYMOUS_OWNER_ID,
            self.scope,
            {"递归": {"name": "递归", "unknown_field": 1}},
        )
        state = self._state()
        state.add_knowledge_point("递归", target_mastery=0.7)

        stored = self.store.read_learner_state(ANONYMOUS_OWNER_ID, self.scope)
        self.assertEqual(stored["递归"]["target_mastery"], 0.7)
        self.assertNotIn("unknown_field", stored["递归"])


class JoinQuotaFairnessTests(unittest.TestCase):
    """只有猜错的码才烧配额。正确的码重复提交不该把学生锁在门外。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "astramentor.db")
        self.accounts = AccountService(self.database)
        self.service = ClassroomService(self.database)
        self.teacher = self.accounts.register(
            "teacher1", "correct-horse-1", role=ROLE_TEACHER
        )
        self.student = self.accounts.register("student1", "correct-horse-2")
        self.classroom = self.service.create_classroom(self.teacher.id, "算法入门")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resubmitting_a_code_you_already_used_never_locks_you_out(self) -> None:
        self.service.join_by_code(self.student.id, self.classroom.join_code)
        for index in range(MAX_JOIN_ATTEMPTS * 3):
            with self.subTest(attempt=index):
                with self.assertRaises(AlreadyEnrolled):
                    self.service.join_by_code(
                        self.student.id, self.classroom.join_code
                    )

    def test_an_archived_classrooms_code_does_not_burn_quota(self) -> None:
        self.service.update_classroom(
            self.classroom.id, self.teacher.id, is_archived=True
        )
        for index in range(MAX_JOIN_ATTEMPTS * 2):
            with self.subTest(attempt=index):
                with self.assertRaises(ClassroomArchivedError):
                    self.service.join_by_code(
                        self.student.id, self.classroom.join_code
                    )

    def test_a_teacher_retrying_their_own_code_does_not_burn_quota(self) -> None:
        for index in range(MAX_JOIN_ATTEMPTS * 2):
            with self.subTest(attempt=index):
                with self.assertRaises(CannotEnrollSelf):
                    self.service.join_by_code(
                        self.teacher.id, self.classroom.join_code
                    )

    def test_wrong_codes_are_still_rate_limited(self) -> None:
        for index in range(MAX_JOIN_ATTEMPTS):
            with self.assertRaises(InvalidJoinCode):
                self.service.join_by_code(self.student.id, f"ZZZZZZ{index:02d}")
        with self.assertRaises(TooManyJoinAttempts):
            self.service.join_by_code(self.student.id, "ZZZZZZ99")

    def test_refunds_do_not_open_a_hole_in_the_limiter(self) -> None:
        """交替提交正确与错误的码，也不能靠退款把猜测配额刷回来。

        配额一旦被真正的猜测耗尽，连正确的码都会被拒 —— 这是对的：
        限速拦的是"这个账号在猜"，不是"这一次猜得对不对"。
        """
        self.service.join_by_code(self.student.id, self.classroom.join_code)
        wrong = 0
        limited = False
        for index in range(MAX_JOIN_ATTEMPTS * 3):
            for code, expected in (
                (self.classroom.join_code, AlreadyEnrolled),
                (f"ZZZZZZ{index:02d}", InvalidJoinCode),
            ):
                try:
                    self.service.join_by_code(self.student.id, code)
                except TooManyJoinAttempts:
                    limited = True
                except expected:
                    if expected is InvalidJoinCode:
                        wrong += 1
                if limited:
                    break
            if limited:
                break
        self.assertTrue(limited, "交替提交没有触发限速")
        self.assertLessEqual(wrong, MAX_JOIN_ATTEMPTS)


class AccountDeletionCleanupTests(unittest.TestCase):
    """删号后磁盘上不该留下访问不到的孤儿 PDF。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = Database(self.root / "astramentor.db")
        self.accounts = AccountService(self.database, token_ttl_hours=1)
        self.store = LearningStore(self.database)
        app.dependency_overrides[get_account_service] = lambda: self.accounts
        self.patches = [
            patch("backend.auth_api.UPLOAD_ROOT", self.root / "uploads"),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        app.dependency_overrides.clear()
        self.temp.cleanup()

    def test_deleting_the_account_removes_its_uploaded_files(self) -> None:
        registered = self.client.post(
            "/api/auth/register",
            json={"username": "alice1", "password": "correct-horse-battery"},
        ).json()
        headers = {"Authorization": f"Bearer {registered['access_token']}"}
        owner_id = registered["user"]["id"]

        pdf = owner_upload_path(owner_id, SHARED_DOC_ID, self.root / "uploads")
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4")
        self.store.write_document(owner_id, SHARED_DOC_ID, {"doc_id": SHARED_DOC_ID})

        response = self.client.request(
            "DELETE",
            "/api/auth/me",
            json={"password": "correct-horse-battery"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 204, response.text)

        self.assertIsNone(self.store.read_document(owner_id, SHARED_DOC_ID))
        self.assertFalse(pdf.exists(), "删号后 PDF 仍留在磁盘上")
        self.assertFalse(pdf.parent.exists())


if __name__ == "__main__":
    unittest.main()
