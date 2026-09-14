"""对外暴露相关的几个收紧开关。

代码执行本身已经关在 bubblewrap 沙箱里了（见 tests/test_sandbox.py），
这个开关是给"干脆不想提供在线 IDE"的部署留的总闸。另外两个（强制登录、
CORS 白名单）则是开到公网时才需要拧紧的。

既然都是"忘了设或者设了不生效就出事"的那类配置，就得有测试钉住它们真的
起作用。
"""

from __future__ import annotations

import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import config


class _Isolated(unittest.TestCase):
    """按不同配置重建一次应用，并给它一套独立的数据库。

    services.database 的 default_database 是首次 import 时就按当时的
    ASTRA_DB_PATH 建好的单例，之后改环境变量换不动它 —— 所以账号相关的
    用例必须走 dependency_overrides，把 get_account_service 换成绑在本用例
    自己那个库上的实例。否则测试会依赖整个套件的执行顺序（单独跑绿、
    跟着全量跑红）。
    """

    env: dict[str, str] = {}

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)

        env = {
            "ASTRA_API_KEY": "dummy-for-tests",
            "ASTRA_DB_PATH": str(root / "test.db"),
            "ASTRA_UPLOAD_ROOT": str(root / "uploads"),
            "ASTRA_SKIP_LEGACY_IMPORT": "true",
            # 没有 dist，静态挂载不会生效；写死这个是为了不受开发机上
            # 是否构建过前端的影响。
            "ASTRA_STATIC_DIR": str(root / "no-such-dist"),
            **self.env,
        }
        patcher = unittest.mock.patch.dict("os.environ", env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

        # config.config 是 import 时就建好的单例，get_config() 每次返回它，
        # 所以换掉这个模块级变量就等于换掉全局配置。
        singleton = unittest.mock.patch.object(config, "config", config.Config())
        singleton.start()
        self.addCleanup(singleton.stop)

        # backend.app 在 import 时就读配置装好了 CORS 中间件、也挂好了前端，
        # 所以换完配置还得重新 import 一遍，否则拿到的是上一个用例的 app。
        import importlib

        import backend.app as backend_app

        importlib.reload(backend_app)
        self._module = backend_app
        self.app = backend_app.app

        # 账号接口全部走 get_account_service，这里换成本用例自己的库。
        from backend.dependencies import get_account_service
        from services.account_service import AccountService
        from services.database import Database

        self.accounts = AccountService(Database(root / "accounts.db"))
        self.app.dependency_overrides[get_account_service] = lambda: self.accounts
        self.addCleanup(self.app.dependency_overrides.clear)

        self.client = TestClient(self.app)


class CodeRunnerDisabledTests(_Isolated):
    env = {"ASTRA_CODE_RUNNER_ENABLED": "false"}

    def test_run_code_is_refused(self) -> None:
        response = self.client.post(
            "/api/run-code",
            json={"language": "python", "code": "print(1)"},
        )
        self.assertEqual(response.status_code, 403)
        # 提示里要点名开关，运维看到才知道是自己关的、不是坏了
        self.assertIn("ASTRA_CODE_RUNNER_ENABLED", response.json()["detail"])

    def test_nothing_is_executed(self) -> None:
        """403 要在进沙箱之前就返回，别白起一趟进程。"""
        with unittest.mock.patch(
            "services.code_runner.CodeRunner.run_code"
        ) as run_code:
            self.client.post(
                "/api/run-code", json={"language": "python", "code": "print(1)"}
            )
        run_code.assert_not_called()


class CodeRunnerEnabledTests(_Isolated):
    env = {"ASTRA_CODE_RUNNER_ENABLED": "true"}

    def test_default_deployment_still_runs_code(self) -> None:
        """本机自己用的默认行为不能被这次改动带坏。"""
        response = self.client.post(
            "/api/run-code",
            json={"language": "python", "code": "print('hi')"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("hi", response.json()["output"])


class RegistrationDisabledTests(_Isolated):
    env = {"ASTRA_REGISTRATION_ENABLED": "false"}

    def test_register_is_refused(self) -> None:
        response = self.client.post(
            "/api/auth/register",
            json={"username": "someone", "password": "correct-horse-battery"},
        )
        self.assertEqual(response.status_code, 403)

    def test_login_still_works_for_existing_accounts(self) -> None:
        """关注册不能把已有账号一起挡在门外。"""
        self.accounts.register("earlybird", "correct-horse-battery")

        response = self.client.post(
            "/api/auth/login",
            json={"username": "earlybird", "password": "correct-horse-battery"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.json())


class CorsTests(_Isolated):
    env = {"ASTRA_CORS_ORIGINS": "https://astra.example.com"}

    def test_configured_origin_is_allowed(self) -> None:
        response = self.client.get(
            "/api/courses", headers={"Origin": "https://astra.example.com"}
        )
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://astra.example.com",
        )

    def test_other_origins_get_no_cors_header(self) -> None:
        response = self.client.get(
            "/api/courses", headers={"Origin": "https://evil.example.com"}
        )
        self.assertIsNone(response.headers.get("access-control-allow-origin"))


class AnonymousDisabledTests(_Isolated):
    env = {"ASTRA_ALLOW_ANONYMOUS": "false"}

    def test_learning_endpoints_require_login(self) -> None:
        for path in ("/api/state", "/api/sessions"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_course_catalog_stays_public(self) -> None:
        """课程目录是登录页之外的门面，不该被一起锁掉。"""
        self.assertEqual(self.client.get("/api/courses").status_code, 200)


if __name__ == "__main__":
    unittest.main()
