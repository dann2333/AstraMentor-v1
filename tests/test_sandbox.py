"""在线 IDE 的沙箱到底关住了什么。

这些用例是对着"如果沙箱失效会发生什么"写的，不是对着实现写的：每一条都
对应一件在沙箱之前真的做得到的事 —— 读走整个账号库、读走模型密钥、连外网、
把内存写满。所以它们即便实现换掉（比如将来从 bubblewrap 换成别的方案）
也应该继续成立。

沙箱依赖内核的非特权 user namespace。跑不起来的环境（某些 CI、老内核、
带限制性 seccomp 的容器）会整体跳过，而不是假装通过 —— 假装通过比红更糟。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services import sandbox
from services.code_runner import CodeRunner

_SANDBOX_OK, _SANDBOX_DETAIL = sandbox.probe()

requires_sandbox = unittest.skipUnless(
    _SANDBOX_OK, f"当前环境跑不了 bubblewrap: {_SANDBOX_DETAIL}"
)


def run_python(code: str) -> dict:
    return CodeRunner.run_code("python", code)


@requires_sandbox
class EscapeAttemptTests(unittest.TestCase):
    """沙箱之前做得到、现在必须做不到的事。"""

    def test_baseline_code_still_runs(self) -> None:
        """先确认沙箱没把正常功能一起关掉。"""
        result = run_python("print('hello', 1 + 1)")
        self.assertEqual(result["exit_code"], 0, result["error"])
        self.assertIn("hello 2", result["output"])

    def test_cannot_read_the_database(self) -> None:
        """之前一行 open() 就能把全部账号和密码散列读走。"""
        result = run_python(
            "print(open('/data/astramentor.db','rb').read(32))"
        )
        self.assertNotEqual(result["exit_code"], 0)
        self.assertNotIn("astramentor", result["output"])

    def test_cannot_read_application_code(self) -> None:
        result = run_python("print(open('/app/config.py').read())")
        self.assertNotEqual(result["exit_code"], 0)

    def test_api_key_is_not_in_the_environment(self) -> None:
        """--clearenv 之后模型密钥不该出现在沙箱里。"""
        marker = "sk-sandbox-test-must-not-leak"
        previous = os.environ.get("ASTRA_API_KEY")
        os.environ["ASTRA_API_KEY"] = marker
        try:
            result = run_python(
                "import os\n"
                "print('ASTRA_API_KEY=' + repr(os.environ.get('ASTRA_API_KEY')))\n"
                "print('ALL=' + repr(sorted(os.environ)))"
            )
        finally:
            if previous is None:
                os.environ.pop("ASTRA_API_KEY", None)
            else:
                os.environ["ASTRA_API_KEY"] = previous

        self.assertEqual(result["exit_code"], 0, result["error"])
        self.assertIn("ASTRA_API_KEY=None", result["output"])
        self.assertNotIn(marker, result["output"])
        # 顺带确认没有别的 ASTRA_* 漏进去
        self.assertNotIn("ASTRA_", result["output"].split("ALL=")[-1])

    def test_no_network(self) -> None:
        result = run_python(
            "import socket\n"
            "socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
            "print('connected')"
        )
        self.assertNotEqual(result["exit_code"], 0)
        self.assertNotIn("connected", result["output"])

    def test_no_dns(self) -> None:
        result = run_python(
            "import socket; print(socket.gethostbyname('example.com'))"
        )
        self.assertNotEqual(result["exit_code"], 0)

    def test_sandbox_root_is_read_only(self) -> None:
        """bwrap 默认给的是可写 tmpfs 根目录；不 remount-ro 的话疯狂建文件
        就是一条吃光内存的路。"""
        result = run_python("open('/pwned','w').write('x'); print('wrote')")
        self.assertNotEqual(result["exit_code"], 0)
        self.assertNotIn("wrote", result["output"])

    def test_workdir_is_writable(self) -> None:
        """工作目录必须可写，否则编译型语言直接没法用。"""
        result = run_python(
            "open('out.txt','w').write('x')\n"
            "print(open('out.txt').read())"
        )
        self.assertEqual(result["exit_code"], 0, result["error"])
        self.assertIn("x", result["output"])


@requires_sandbox
class ResourceLimitTests(unittest.TestCase):
    def test_busy_loop_is_killed(self) -> None:
        result = run_python("while True: pass")
        self.assertNotEqual(result["exit_code"], 0)

    def test_runaway_output_is_capped(self) -> None:
        """输出走真实文件的 fd，所以 RLIMIT_FSIZE 能把它拦住。"""
        result = run_python(
            "import sys\nwhile True: sys.stdout.write('A' * 4096)"
        )
        self.assertLessEqual(
            len(result["output"]),
            sandbox.MAX_OUTPUT_BYTES + 64,
            "回给前端的输出没有被截断",
        )

    def test_memory_hog_is_refused(self) -> None:
        result = run_python("x = bytearray(4 * 1024**3); print(len(x))")
        self.assertNotEqual(result["exit_code"], 0)

    def test_fork_bomb_leaves_nothing_behind(self) -> None:
        result = run_python("import os\nwhile True: os.fork()")
        self.assertNotEqual(result["exit_code"], 0)
        # bwrap 在新的 pid 命名空间里当 1 号进程，它一退，里面的后代全消失
        leftovers = [
            name
            for name in os.listdir("/proc")
            if name.isdigit() and _cmdline(name).startswith("bwrap")
        ]
        self.assertEqual(leftovers, [], f"有残留的沙箱进程: {leftovers}")


def _cmdline(pid: str) -> str:
    try:
        raw = Path("/proc", pid, "cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()


class FailClosedTests(unittest.TestCase):
    """沙箱不可用时必须拒绝执行，而不是退回裸 subprocess。

    这条不需要真的有沙箱，所以不加 skip —— 恰恰是在没有沙箱的环境里
    最该成立。
    """

    def test_run_code_refuses_when_sandbox_is_unavailable(self) -> None:
        original = sandbox.probe

        def broken_probe() -> tuple[bool, str]:
            return False, "测试用：假装内核不支持 user namespace"

        sandbox.probe = broken_probe  # type: ignore[assignment]
        try:
            result = CodeRunner.run_code("python", "print('should not run')")
        finally:
            sandbox.probe = original  # type: ignore[assignment]

        self.assertNotEqual(result["exit_code"], 0)
        self.assertNotIn("should not run", result["output"])
        self.assertIn("沙箱不可用", result["error"])

    def test_unknown_language_is_rejected(self) -> None:
        result = CodeRunner.run_code("ruby", "puts 1")
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("不支持", result["error"])

    def test_missing_toolchain_reports_clearly(self) -> None:
        """精简镜像里没有 go/java，报错要说清是缺工具链。"""
        from services import code_runner

        original = code_runner.shutil.which
        code_runner.shutil.which = lambda _name: None  # type: ignore[assignment]
        try:
            result = CodeRunner.run_code("go", "func main() {}")
        finally:
            code_runner.shutil.which = original  # type: ignore[assignment]

        self.assertEqual(result["exit_code"], -1)
        self.assertIn("没有找到", result["error"])


@requires_sandbox
class LanguageCoverageTests(unittest.TestCase):
    """前端列了六种语言，逐个确认真的能在沙箱里跑通。

    缺工具链的环境（比如精简镜像）会按语言单独跳过。
    """

    CASES = {
        "python": ("print('py ok')", "py ok"),
        "javascript": ("console.log('js ok')", "js ok"),
        "c": (
            '#include <stdio.h>\nint main(){printf("c ok\\n");return 0;}',
            "c ok",
        ),
        "cpp": (
            '#include <iostream>\nint main(){std::cout << "cpp ok\\n";return 0;}',
            "cpp ok",
        ),
        "go": (
            'import "fmt"\nfunc main(){fmt.Println("go ok")}',
            "go ok",
        ),
        "java": (
            'public class Main{public static void main(String[] a){'
            'System.out.println("java ok");}}',
            "java ok",
        ),
    }

    def test_every_advertised_language_runs(self) -> None:
        for language, (code, expected) in self.CASES.items():
            with self.subTest(language=language):
                result = CodeRunner.run_code(language, code)
                if "没有找到" in result["error"]:
                    self.skipTest(f"本环境没装 {language} 的工具链")
                self.assertEqual(result["exit_code"], 0, result["error"])
                self.assertIn(expected, result["output"])


@requires_sandbox
class SandboxApiTests(unittest.TestCase):
    def test_env_is_exactly_what_we_pass(self) -> None:
        with TemporaryDirectory() as work:
            result = sandbox.run(
                ["/bin/sh", "-c", "env | sort"],
                workdir=work,
                env={"PATH": "/usr/bin:/bin", "ONLY_THIS": "yes"},
            )
        self.assertEqual(result.exit_code, 0, result.error)
        names = {
            line.split("=", 1)[0]
            for line in result.output.splitlines()
            if "=" in line
        }
        # PWD / SHLVL 之类由 shell 自己加，其余不该有
        self.assertEqual(names - {"PWD", "SHLVL", "_"}, {"PATH", "ONLY_THIS"})


if __name__ == "__main__":
    unittest.main()
