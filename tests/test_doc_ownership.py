"""文档模式的归属隔离测试。

``doc_id`` 是 PDF 文件内容的 MD5：两个账号上传**同一份**教材会算出同一个
``doc_id``。这正是最容易出错的地方 —— 只用 doc_id 做键，两人就会共用一份
上下文缓存与磁盘文件，其中一人删除还会连带删掉另一人的。
这里用同一份字节流分别以两个身份上传，把这条边界钉死。
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import backend.doc_api as doc_api
from backend.app import app
from backend.dependencies import get_account_service
from services.account_service import AccountService
from services.database import ANONYMOUS_OWNER_ID, Database
from services.learning_store import LearningStore
from services.pdf_parser import DocumentChunk, DocumentContext


SHARED_PDF = b"%PDF-1.4 identical bytes for every uploader"
SHARED_DOC_ID = "0" * 32


def _fake_context(filename: str) -> DocumentContext:
    return DocumentContext(
        doc_id=SHARED_DOC_ID,
        filename=filename,
        total_pages=2,
        chunks=[
            DocumentChunk(
                chunk_id="c1", content="递归的定义", page_start=1, page_end=1, heading="第一章"
            )
        ],
        full_text="递归的定义与例子",
    )


class DocumentOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = Database(root / "astramentor.db")
        self.accounts = AccountService(self.database, token_ttl_hours=1)
        self.store = LearningStore(self.database)

        app.dependency_overrides[get_account_service] = lambda: self.accounts

        self.patches = [
            patch.object(doc_api, "learning_store", self.store),
            patch.object(doc_api, "_UPLOAD_ROOT", root / "uploads"),
            patch.object(doc_api, "parse_pdf", lambda data, name: _fake_context(name)),
            patch("services.learning_service.learning_store", self.store),
            patch("services.learning_service.APIClient", return_value=Mock()),
        ]
        for item in self.patches:
            item.start()
        doc_api._doc_cache.clear()

        self.upload_root = root / "uploads"
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        doc_api._doc_cache.clear()
        app.dependency_overrides.clear()
        self.temp.cleanup()

    def _register(self, username: str) -> dict:
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": "correct-horse-battery"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def _upload(self, headers: dict | None, filename: str) -> dict:
        response = self.client.post(
            "/api/doc/upload",
            files={"file": (filename, SHARED_PDF, "application/pdf")},
            headers=headers or {},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _owner_id(self, headers: dict) -> str:
        return self.client.get("/api/auth/me", headers=headers).json()["id"]

    # ------------------------------------------------------------------
    def test_same_pdf_uploaded_twice_yields_two_independent_documents(self) -> None:
        alice = self._register("alice1")
        bob = self._register("bob1")

        self.assertEqual(self._upload(alice, "alice.pdf")["doc_id"], SHARED_DOC_ID)
        self.assertEqual(self._upload(bob, "bob.pdf")["doc_id"], SHARED_DOC_ID)

        alice_id, bob_id = self._owner_id(alice), self._owner_id(bob)
        self.assertEqual(
            self.store.read_document(alice_id, SHARED_DOC_ID)["filename"], "alice.pdf"
        )
        self.assertEqual(
            self.store.read_document(bob_id, SHARED_DOC_ID)["filename"], "bob.pdf"
        )

        # 原始 PDF 落在各自的目录下，互不覆盖
        self.assertTrue((self.upload_root / alice_id / f"{SHARED_DOC_ID}.pdf").exists())
        self.assertTrue((self.upload_root / bob_id / f"{SHARED_DOC_ID}.pdf").exists())

    def test_a_document_you_never_uploaded_is_404(self) -> None:
        alice = self._register("alice1")
        self._upload(alice, "alice.pdf")

        bob = self._register("bob1")
        response = self.client.post(
            "/api/doc/learning/start",
            json={"doc_id": SHARED_DOC_ID, "node_name": "递归"},
            headers=bob,
        )
        self.assertEqual(response.status_code, 404, response.text)

        # 访客同样看不到
        guest = self.client.post(
            "/api/doc/learning/start",
            json={"doc_id": SHARED_DOC_ID, "node_name": "递归"},
        )
        self.assertEqual(guest.status_code, 404, guest.text)

    def test_deleting_your_copy_leaves_the_other_owner_untouched(self) -> None:
        alice = self._register("alice1")
        bob = self._register("bob1")
        self._upload(alice, "alice.pdf")
        self._upload(bob, "bob.pdf")
        alice_id, bob_id = self._owner_id(alice), self._owner_id(bob)

        response = self.client.delete(
            "/api/doc/graph/delete", params={"doc_id": SHARED_DOC_ID}, headers=alice
        )
        self.assertEqual(response.status_code, 200, response.text)

        self.assertIsNone(self.store.read_document(alice_id, SHARED_DOC_ID))
        self.assertFalse((self.upload_root / alice_id / f"{SHARED_DOC_ID}.pdf").exists())

        self.assertIsNotNone(self.store.read_document(bob_id, SHARED_DOC_ID))
        self.assertTrue((self.upload_root / bob_id / f"{SHARED_DOC_ID}.pdf").exists())

    def test_doc_graph_save_requires_owning_the_document(self) -> None:
        alice = self._register("alice1")
        self._upload(alice, "alice.pdf")
        bob = self._register("bob1")

        intruder = self.client.post(
            "/api/doc/graph/save",
            json={"doc_id": SHARED_DOC_ID, "graph_data": {"nodes": [{"id": "x"}]}},
            headers=bob,
        )
        self.assertEqual(intruder.status_code, 404, intruder.text)

        mine = self.client.post(
            "/api/doc/graph/save",
            json={"doc_id": SHARED_DOC_ID, "graph_data": {"nodes": [{"id": "x"}]}},
            headers=alice,
        )
        self.assertEqual(mine.status_code, 200, mine.text)
        self.assertEqual(
            self.store.read_graph(
                self._owner_id(alice), f"graph:doc_{SHARED_DOC_ID}"
            ),
            {"nodes": [{"id": "x"}]},
        )

    def test_cache_is_keyed_by_owner_and_bounded(self) -> None:
        """缓存键必须带 owner，否则先上传的人会把内容"借"给后来的人。"""
        alice = self._register("alice1")
        bob = self._register("bob1")
        self._upload(alice, "alice.pdf")
        self._upload(bob, "bob.pdf")
        alice_id, bob_id = self._owner_id(alice), self._owner_id(bob)

        self.assertEqual(
            doc_api._get_doc_context(alice_id, SHARED_DOC_ID).filename, "alice.pdf"
        )
        self.assertEqual(
            doc_api._get_doc_context(bob_id, SHARED_DOC_ID).filename, "bob.pdf"
        )

        # 缓存有上限，不会随上传量无限增长
        for index in range(doc_api._DOC_CACHE_MAX_ENTRIES + 5):
            doc_api._cache_put(alice_id, _fake_context(f"f{index}.pdf"))
        self.assertLessEqual(len(doc_api._doc_cache), doc_api._DOC_CACHE_MAX_ENTRIES)

    def test_guest_uploads_land_in_the_reserved_guest_space(self) -> None:
        self._upload(None, "guest.pdf")
        self.assertEqual(
            self.store.read_document(ANONYMOUS_OWNER_ID, SHARED_DOC_ID)["filename"],
            "guest.pdf",
        )
        alice = self._register("alice1")
        self.assertIsNone(self.store.read_document(self._owner_id(alice), SHARED_DOC_ID))


if __name__ == "__main__":
    unittest.main()
