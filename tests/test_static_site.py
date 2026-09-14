"""单容器部署依赖的静态托管行为。

镜像里前端和后端共用一个端口，靠的是 backend/static_site.py 把
frontend/dist 挂到 `/`。这层挂载有两个容易踩坏的点：

1. `/` 上的 Mount 会匹配一切路径，顺序一错就把 /api 和 /docs 全盖掉——
   那是"镜像能启动、但所有接口 404"的经典事故。
2. 单页应用需要 404 回退到 index.html，但不能把 403 之类也一起兜掉。

这两条都在下面锁住。
"""

from __future__ import annotations

import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.static_site import SinglePageApp, mount_frontend, resolve_static_dir

INDEX_HTML = "<!doctype html><title>AstraMentor</title><div id=root></div>"


class _Site:
    """造一个最小的前端产物目录。"""

    def __init__(self, stack: TemporaryDirectory) -> None:
        self.root = Path(stack.name)
        (self.root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")


class StaticSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.site = _Site(self._tmp)

        self.app = FastAPI()

        @self.app.get("/api/ping")
        def ping() -> dict[str, str]:
            return {"pong": "yes"}

        self.app.mount("/", SinglePageApp(directory=self.site.root), name="frontend")
        self.client = TestClient(self.app)

    def test_serves_index_at_root(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AstraMentor", response.text)

    def test_serves_hashed_assets(self) -> None:
        response = self.client.get("/assets/index-abc123.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("console.log", response.text)

    def test_api_routes_still_win(self) -> None:
        """最关键的一条：挂了前端之后接口必须还在。"""
        response = self.client.get("/api/ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"pong": "yes"})

    def test_unknown_path_falls_back_to_index(self) -> None:
        response = self.client.get("/some/deep/frontend/path")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AstraMentor", response.text)
        # 回退出来的 index.html 不能被缓存，否则下次更新后浏览器会拿着旧的
        # index 去要已经被 hash 掉的旧资源。
        self.assertIn("no-cache", response.headers.get("cache-control", ""))

    def test_missing_asset_is_not_masked_as_html(self) -> None:
        """静态资源请求走的是同一套回退，但至少不能返回 5xx。"""
        response = self.client.get("/assets/does-not-exist.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AstraMentor", response.text)


class ResolveStaticDirTests(unittest.TestCase):
    def test_returns_none_without_a_build(self) -> None:
        with TemporaryDirectory() as empty:
            with unittest.mock.patch.dict(
                "os.environ", {"ASTRA_STATIC_DIR": empty}, clear=False
            ):
                self.assertIsNone(resolve_static_dir())

    def test_honours_explicit_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "index.html").write_text(INDEX_HTML, encoding="utf-8")
            with unittest.mock.patch.dict(
                "os.environ", {"ASTRA_STATIC_DIR": tmp}, clear=False
            ):
                self.assertEqual(resolve_static_dir(), Path(tmp).resolve())

    def test_mount_is_a_noop_without_a_build(self) -> None:
        """开发和测试环境没有 dist，这段逻辑必须完全不生效。"""
        app = FastAPI()

        @app.get("/api/ping")
        def ping() -> dict[str, str]:
            return {"pong": "yes"}

        with TemporaryDirectory() as empty:
            with unittest.mock.patch.dict(
                "os.environ", {"ASTRA_STATIC_DIR": empty}, clear=False
            ):
                self.assertIsNone(mount_frontend(app))

        client = TestClient(app)
        self.assertEqual(client.get("/api/ping").status_code, 200)
        # 没有前端时访问根路径应该是 404，而不是被什么东西静默接管。
        self.assertEqual(client.get("/").status_code, 404)


if __name__ == "__main__":
    unittest.main()
