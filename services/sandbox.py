"""用 bubblewrap 把使用者提交的代码关起来跑。

## 为什么需要这个

在线 IDE 之前是直接 `subprocess.run(["python", "-c", code])`。也就是说：
提交上来的代码和后端进程同权限、同网络、同文件系统。一行
`open("/data/astramentor.db").read()` 就能拿走全部账号和密码散列，
一行 `os.environ["ASTRA_API_KEY"]` 就能拿走模型密钥，还能顺手扫内网。

## 为什么是 bubblewrap

它是 Flatpak 用的那套沙箱，Debian 一个 apt 包就装上，而且**不需要**给容器
加任何 capability —— 靠的是内核的非特权 user namespace。这意味着沙箱能和
应用装在同一个镜像里，不用起第二个容器、不用挂 docker socket（挂 socket
等于把宿主机 root 交出去）、也不用 --privileged。

代价是它依赖"能建出非特权 user namespace"，而 Docker 的默认 seccomp 策略正好
挡住这一步（bwrap 报 "No permissions to create new namespace"），AppArmor 的
docker-default 再挡住后面的 mount。实测下来 capability 不是变量 —— 加
NET_ADMIN / SYS_ADMIN 乃至 --privileged 都一样，所以 cap_drop: ALL 可以留着；
真正分档的是 seccomp 和 apparmor 这两项。哪一档够用取决于宿主内核和发行版，
只能实测：scripts/check-sandbox.sh 会逐档跑一遍并给出结论。

起不来时 probe() 返回不可用，调用方必须拒绝执行 —— 见下面「失败就拒绝」。

隔离掉的东西：

  网络    --unshare-net，沙箱里没有任何网络接口，连 DNS 都没有
  文件    只挂进只读的工具链（/usr、/opt/venv 等）和一个临时工作目录；
          /app 和 /data 根本不在视野里
  环境    --clearenv，只塞回运行必需的几个变量，API Key 不在其中
  进程    --unshare-pid，fork 炸弹困在自己的 pid 命名空间里，bwrap 一退全清
  资源    RLIMIT_CPU / FSIZE / NOFILE / NPROC，外加墙钟超时

## 失败就拒绝，不降级

`ensure_available()` 探测不通时，调用方必须直接拒绝执行，**不能**退回到裸
subprocess。"沙箱装不上就先凑合跑着"是这类功能出事的标准路径。
"""

from __future__ import annotations

import logging
import os
import resource
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

BWRAP = shutil.which("bwrap") or "/usr/bin/bwrap"

#: 沙箱里工作目录的挂载点
WORKDIR = "/work"

#: 单次执行的墙钟上限（秒）。编译型语言要算上编译时间。
DEFAULT_TIMEOUT = 10

#: 回传给前端的输出上限。RLIMIT_FSIZE 会在更高一层拦住疯狂写文件，
#: 这里只是别把几 MB 的日志塞进一个 JSON 响应。
MAX_OUTPUT_BYTES = 64 * 1024

#: 进程数上限。设在子进程上，不影响后端自己。
MAX_PROCESSES = 192

#: 单个文件写入上限。也顺带限制了 stdout/stderr 能写多少 ——
#: 它们是真实文件的 fd，超了会收到 SIGXFSZ。
MAX_FILE_BYTES = 8 * 1024 * 1024


class SandboxUnavailable(RuntimeError):
    """沙箱不可用。调用方应当拒绝执行，而不是绕过它。"""


@dataclass(frozen=True)
class SandboxResult:
    output: str
    error: str
    exit_code: int


def _existing(*candidates: str) -> list[str]:
    """挑出真实存在的路径，保持顺序并去重。"""
    seen: set[str] = set()
    out: list[str] = []
    for path in candidates:
        if path and path not in seen and os.path.exists(path):
            seen.add(path)
            out.append(path)
    return out


def _base_argv(workdir: str, extra_ro_binds: Sequence[str] = ()) -> list[str]:
    """拼出 bwrap 的公共参数。

    extra_ro_binds 用来补挂工具链所在的目录。不能假设解释器一定在 /usr 下：
    比如某些镜像把 node 装在 /opt/node22，不挂进来就是一句
    `bwrap: execvp /opt/node22/bin/node: No such file or directory`。
    """
    argv = [
        BWRAP,
        # user / ipc / pid / net / uts / cgroup 全部新建，mount 命名空间 bwrap 总是新建
        "--unshare-all",
        # 父进程一死，沙箱里所有东西跟着死，不留孤儿
        "--die-with-parent",
        # 新会话：否则沙箱里能用 TIOCSTI 往父终端塞按键
        "--new-session",
        # 环境变量一律清空，下面再按语言塞回必需的那几个
        "--clearenv",
        "--proc", "/proc",
        "--dev", "/dev",
        # /run 给个小 tmpfs：有些工具链会往里写东西，但不需要多大
        "--size", str(8 * 1024 * 1024), "--tmpfs", "/run",
    ]

    # 只读挂进工具链。注意这里没有 /app 和 /data —— 沙箱里看不见它们，
    # 也就谈不上读数据库或上传的文件。
    for path in _existing(
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/lib32",
        "/lib64",
        "/libx32",
        "/opt/venv",
        "/etc/alternatives",  # Debian 的 java 走 alternatives 软链
        *extra_ro_binds,
    ):
        argv += ["--ro-bind", path, path]

    # /etc 不整个挂进去（里面可能有别的配置），只给几个解析用户名/时区要用的文件
    for path in _existing(
        "/etc/passwd",
        "/etc/group",
        "/etc/nsswitch.conf",
        "/etc/localtime",
        "/etc/java-17-openjdk",
        "/etc/java-21-openjdk",
    ):
        argv += ["--ro-bind", path, path]

    argv += [
        "--bind", workdir, WORKDIR,
        "--chdir", WORKDIR,
        # 把沙箱自己的根挂成只读。bwrap 默认给新命名空间一个可写的 tmpfs
        # 根目录，不管的话 `open('/x','w')` 是能成功的 —— 写的是内存，
        # 于是"疯狂建文件"就变成了一条吃光宿主内存的路。
        #
        # 这一行必须排在所有 bind 之后：先 remount-ro 的话，后面连
        # /work 的挂载点都建不出来（Can't mkdir /work: Read-only file system）。
        "--remount-ro", "/",
    ]
    return argv


def _apply_limits(address_space: int | None, cpu_seconds: int, file_bytes: int):
    """返回给 subprocess 的 preexec_fn：在 exec 之前把 rlimit 压下来。

    限制设在 bwrap 这个子进程上，沙箱里的所有后代继承它，后端自己不受影响。
    """

    def _preexec() -> None:  # pragma: no cover - 只在子进程里执行
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))
        if address_space is not None:
            resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))

    return _preexec


@lru_cache(maxsize=1)
def probe() -> tuple[bool, str]:
    """探一次沙箱能不能用，结果缓存下来。

    返回 (可用, 说明)。说明会写进启动日志，也会在拒绝执行时回给前端 ——
    "沙箱起不来"必须是一句能照着查的话，不能是个 500。
    """
    if not os.path.exists(BWRAP):
        return False, (
            f"找不到 bubblewrap（{BWRAP}）。镜像里应当已经装好；"
            "自建镜像请安装 bubblewrap 包。"
        )

    with tempfile.TemporaryDirectory() as probe_dir:
        argv = _base_argv(probe_dir) + ["--setenv", "PATH", "/usr/bin:/bin", "/bin/true"]
        try:
            done = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"bubblewrap 启动失败：{exc}"

    if done.returncode == 0:
        return True, "bubblewrap 可用"

    detail = (done.stderr or done.stdout or "").strip().splitlines()
    hint = detail[-1] if detail else f"退出码 {done.returncode}"
    return False, (
        f"bubblewrap 无法建立命名空间：{hint}。"
        "在 Docker 里最常见的原因是默认的 seccomp / AppArmor 策略挡住了"
        "建立非特权 user namespace。跑 scripts/check-sandbox.sh 能测出这台"
        "机器最少需要放开哪一项（不需要任何 capability）；"
        "详见 README「宿主机策略这一关」。"
    )


def ensure_available() -> None:
    """沙箱不可用就抛 SandboxUnavailable。"""
    ok, detail = probe()
    if not ok:
        raise SandboxUnavailable(detail)


def run(
    argv: list[str],
    *,
    workdir: str | Path,
    env: dict[str, str],
    timeout: int = DEFAULT_TIMEOUT,
    address_space: int | None = 1024 * 1024 * 1024,
    extra_ro_binds: Sequence[str] = (),
    max_file_bytes: int = MAX_FILE_BYTES,
) -> SandboxResult:
    """在沙箱里跑一条命令。

    argv         沙箱内要执行的命令
    workdir      宿主机上的目录，会挂到沙箱的 /work，是沙箱里唯一可写的地方
    env          塞进沙箱的环境变量（在 --clearenv 之后）
    timeout      墙钟上限
    address_space  RLIMIT_AS。Go 和 JVM 会预留巨量虚拟地址空间，给它们传
                   None，靠容器的内存上限兜底。
    extra_ro_binds 额外只读挂载的目录（工具链不在 /usr 下时要用）
    max_file_bytes RLIMIT_FSIZE。编译阶段的中间产物比脚本大得多，
                   所以编译时要放宽（Go 的 _pkg_.a 就会超过 8MB）。
    """
    ensure_available()

    full = _base_argv(str(workdir), extra_ro_binds)
    for key, value in env.items():
        full += ["--setenv", key, value]
    full += argv

    # stdout/stderr 写到真实临时文件而不是管道：这样 RLIMIT_FSIZE 能直接
    # 限住输出体积（管道不受它约束），一个 `while True: print(...)` 不会把
    # 后端的内存吃光。
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        try:
            proc = subprocess.Popen(
                full,
                stdin=subprocess.DEVNULL,
                stdout=out_file,
                stderr=err_file,
                preexec_fn=_apply_limits(address_space, timeout, max_file_bytes),
                close_fds=True,
            )
        except OSError as exc:
            raise SandboxUnavailable(f"沙箱进程起不来：{exc}") from exc

        timed_out = False
        try:
            proc.wait(timeout=timeout + 2)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()          # --die-with-parent 会连带清掉里面的所有后代
            proc.wait(timeout=5)

        out_file.seek(0)
        err_file.seek(0)
        output = _decode(out_file.read(MAX_OUTPUT_BYTES + 1))
        error = _decode(err_file.read(MAX_OUTPUT_BYTES + 1))

    if timed_out:
        return SandboxResult(
            output=output,
            error=(error + f"\n运行超时（上限 {timeout} 秒），已终止。").strip(),
            exit_code=-1,
        )
    return SandboxResult(output=output, error=error, exit_code=proc.returncode)


def _decode(raw: bytes) -> str:
    truncated = len(raw) > MAX_OUTPUT_BYTES
    text = raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += "\n…输出过长，已截断。"
    return text
