"""在线 IDE 的代码执行。

所有语言都在 services/sandbox.py 的 bubblewrap 沙箱里跑：无网络、看不到
/app 和 /data、环境变量清空、CPU 与文件写入都有上限。沙箱探测不通时直接
拒绝执行，不会退回到裸 subprocess —— 那是这类功能出事的标准路径。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from services.sandbox import (
    DEFAULT_TIMEOUT,
    WORKDIR,
    SandboxResult,
    SandboxUnavailable,
    run,
)

logger = logging.getLogger(__name__)

#: 沙箱里的基础环境。注意没有 ASTRA_* 任何一项 —— 模型密钥不进沙箱。
_BASE_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/venv/bin",
    "HOME": WORKDIR,
    "TMPDIR": f"{WORKDIR}/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    # 写 .pyc 只会往工作目录里堆垃圾
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}


#: 编译阶段放宽的单文件上限。脚本语言仍用 sandbox 的默认值（8MB）。
_COMPILE_FILE_BYTES = 128 * 1024 * 1024


class ToolchainMissing(RuntimeError):
    """这个镜像里没装该语言的工具链。"""


def _bind_root(executable: str) -> tuple[str, ...]:
    """工具链不在 /usr 下时，算出该额外只读挂载哪个目录。

    /usr 及其子路径已经默认挂进沙箱了，不用管。但有些环境把运行时装在
    别处（/opt/node22/bin/node、/usr/local 之外的自建前缀），不挂进去
    就只会得到一句 bwrap 的 execvp 报错。这里取实际路径的第一层目录，
    比如 /opt/node22/bin/node -> /opt/node22。
    """
    real = os.path.realpath(executable)
    parts = Path(real).parts
    if len(parts) < 3 or parts[1] in {"usr", "bin", "sbin", "lib", "lib64"}:
        return ()
    # /opt/node22/bin/node -> ('/', 'opt', 'node22', ...) 取到第三段
    return (str(Path(parts[0], parts[1], parts[2])),)


def _resolve(*names: str) -> str:
    """按顺序找可执行文件，返回绝对路径。

    不把 /opt/venv/bin/python 这类路径写死：镜像里是 venv，源码跑起来是
    系统 python，WITH_IDE_TOOLCHAIN=false 的精简镜像里干脆没有。写死的话
    第一种之外的环境都会得到一句 bwrap 的 execvp 报错，看不出是缺什么。
    """
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    raise ToolchainMissing(
        f"这个部署里没有找到 {names[0]}，该语言用不了。"
        "（精简镜像用 --build-arg WITH_IDE_TOOLCHAIN=false 构建时不含工具链）"
    )


class CodeRunner:
    """把代码丢进沙箱跑，返回 {output, error, exit_code}。"""

    TIMEOUT = DEFAULT_TIMEOUT

    @staticmethod
    def run_code(language: str, code: str) -> dict:
        language = (language or "").strip().lower()
        handler = _HANDLERS.get(language)
        if handler is None:
            return {
                "output": "",
                "error": f"不支持的语言: {language}",
                "exit_code": -1,
            }

        try:
            with TemporaryDirectory(prefix="astra-run-") as tmp:
                work = Path(tmp)
                # 沙箱里 TMPDIR 指向 /work/tmp，得先建出来
                (work / "tmp").mkdir(exist_ok=True)
                result = handler(work, code)
        except ToolchainMissing as exc:
            return {"output": "", "error": str(exc), "exit_code": -1}
        except SandboxUnavailable as exc:
            # 沙箱不可用 = 不执行。日志里留下原因，前端给一句能照着查的话。
            logger.error("沙箱不可用，已拒绝执行代码: %s", exc)
            return {
                "output": "",
                "error": f"代码沙箱不可用，已拒绝执行。{exc}",
                "exit_code": -1,
            }
        except Exception:
            logger.exception("代码执行出错")
            return {
                "output": "",
                "error": "执行过程中出现意外错误，请查看服务端日志。",
                "exit_code": -1,
            }

        return {
            "output": result.output,
            "error": result.error,
            "exit_code": result.exit_code,
        }


def _write(work: Path, name: str, code: str) -> None:
    (work / name).write_text(code, encoding="utf-8")


def _run_python(work: Path, code: str) -> SandboxResult:
    _write(work, "main.py", code)
    exe = _resolve("python3", "python")
    return run([exe, "main.py"], workdir=work, env=_BASE_ENV,
               extra_ro_binds=_bind_root(exe))


def _run_javascript(work: Path, code: str) -> SandboxResult:
    _write(work, "main.js", code)
    exe = _resolve("node", "nodejs")
    return run([exe, "main.js"], workdir=work, env=_BASE_ENV,
               extra_ro_binds=_bind_root(exe))


def _run_c(work: Path, code: str) -> SandboxResult:
    _write(work, "main.c", code)
    exe = _resolve("gcc", "cc")
    binds = _bind_root(exe)
    compiled = run(
        [exe, "main.c", "-o", "main", "-O0", "-std=c17"],
        workdir=work,
        env=_BASE_ENV,
        extra_ro_binds=binds,
        max_file_bytes=_COMPILE_FILE_BYTES,
    )
    if compiled.exit_code != 0:
        return compiled
    return run([f"{WORKDIR}/main"], workdir=work, env=_BASE_ENV)


def _run_cpp(work: Path, code: str) -> SandboxResult:
    _write(work, "main.cpp", code)
    exe = _resolve("g++", "c++")
    binds = _bind_root(exe)
    compiled = run(
        [exe, "main.cpp", "-o", "main", "-O0", "-std=c++17"],
        workdir=work,
        env=_BASE_ENV,
        extra_ro_binds=binds,
        max_file_bytes=_COMPILE_FILE_BYTES,
    )
    if compiled.exit_code != 0:
        return compiled
    return run([f"{WORKDIR}/main"], workdir=work, env=_BASE_ENV)


def _run_go(work: Path, code: str) -> SandboxResult:
    # 之前 run_code 的分支里漏了 go，前端选了 Go 只会得到"不支持的语言"。
    if "package main" not in code:
        code = "package main\n\n" + code
    _write(work, "main.go", code)

    env = {
        **_BASE_ENV,
        # 构建缓存必须落在可写的地方，默认在 HOME 下，而 HOME 就是 /work
        "GOCACHE": f"{WORKDIR}/.gocache",
        "GOPATH": f"{WORKDIR}/.gopath",
        # 沙箱里没网，顺手关掉 cgo（也快一些）
        "CGO_ENABLED": "0",
        "GOFLAGS": "-mod=mod",
        "GOTELEMETRY": "off",
    }
    # Go 的工具链会预留很大的虚拟地址空间，RLIMIT_AS 会把它直接打死，
    # 这里交给容器的内存上限兜底。
    exe = _resolve("go")
    return run(
        [exe, "run", "main.go"],
        workdir=work,
        env=env,
        timeout=30,
        address_space=None,
        extra_ro_binds=_bind_root(exe),
        # Go 编译出来的 _pkg_.a 轻易超过 8MB，卡在 RLIMIT_FSIZE 上会报
        # "compile: writing output: file too large"，看不出是限额问题。
        max_file_bytes=_COMPILE_FILE_BYTES,
    )


def _run_java(work: Path, code: str) -> SandboxResult:
    _write(work, "Main.java", code)
    javac = _resolve("javac")
    compiled = run(
        [javac, "-nowarn", "Main.java"],
        workdir=work,
        env={**_BASE_ENV, "JAVA_TOOL_OPTIONS": "-Xshare:auto"},
        timeout=30,
        address_space=None,  # JVM 同样会预留巨量虚拟地址空间
        extra_ro_binds=_bind_root(javac),
        max_file_bytes=_COMPILE_FILE_BYTES,
    )
    if compiled.exit_code != 0:
        return compiled
    java = _resolve("java")
    return run(
        # 堆上限直接用 JVM 参数表达，比 RLIMIT_AS 精确得多
        [java, "-Xmx256m", "-Xss8m", "-XX:+UseSerialGC", "-cp", ".", "Main"],
        workdir=work,
        env=_BASE_ENV,
        timeout=30,
        address_space=None,
        extra_ro_binds=_bind_root(java),
    )


_HANDLERS = {
    "python": _run_python,
    "python3": _run_python,
    "javascript": _run_javascript,
    "js": _run_javascript,
    "node": _run_javascript,
    "c": _run_c,
    "cpp": _run_cpp,
    "c++": _run_cpp,
    "go": _run_go,
    "golang": _run_go,
    "java": _run_java,
}
