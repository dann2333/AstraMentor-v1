"""星图生成任务：把"生成"从一条长连接变成服务端的一件事。

## 为什么要有这个

原来星图生成是一条 SSE 长连接：浏览器发请求，然后等几十秒到几分钟，中途
只有两三条进度标签，其余时间连接上**一个字节都不发**。这有两个后果：

1. 任何一层有读超时的中间件都会把这条静默连接掐掉（nginx 的
   proxy_read_timeout 默认就是 60 秒）。前端只看到一句 network error；而后端
   那个工作线程还在照常跑、跑完照常返回，日志里一条错都没有。
2. 刷新一下页面，这条连接就没了。生成还在服务端跑着，但界面上什么都不剩，
   也看不到进度。

改成任务之后：请求立刻返回一个 job_id，生成在后台跑，进度和模型的输出都往
这个任务的事件流里追加。前端按 job_id 轮询，刷新、切标签、换设备都能接回去，
反代的读超时也就不相关了。

## 事件流

每个任务带一条**只增不改**的事件流，seq 从 1 开始。前端带 ``since`` 拉增量，
所以轮询很便宜。事件有几种：

  progress   阶段变化（正在检索教材 / AI 正在规划 / 正在保存）
  reasoning  模型的思考增量 —— K3 关不掉思考，这段往往是最长的等待，
             不把它透出去用户就会以为卡死了
  content    模型正式输出的增量（星图的 JSON 正在一点点吐出来）
  error      失败原因

连续的同类增量会合并进上一条事件，避免几千条小事件把内存和响应体撑爆。

## 生命周期

任务放在内存里：进程重启就没了。这是有意的取舍 —— 它要解决的是"刷新页面
不丢"，而不是"重启不丢"；而且**生成成功的星图本身是落盘的**
（``LearningService._persist_graph``），所以重启后丢掉的只是这张进度条。
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

#: 每个使用者最多留多少条任务记录（超了先丢最老的已结束任务）
MAX_JOBS_PER_OWNER = 20

#: 单个任务最多保留多少条事件。超了从最老的开始丢，并在快照里如实报告丢了多少 ——
#: 悄悄截断会让前端以为自己已经看全了。
MAX_EVENTS_PER_JOB = 600

#: 单条增量事件最多攒多少字符。攒够就另起一条，前端才好逐段渲染。
MAX_DELTA_CHARS = 240

#: 已结束的任务留多久
FINISHED_TTL = timedelta(hours=6)

#: 可以合并的增量类型
_MERGEABLE = frozenset({"reasoning", "content"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobEvent:
    seq: int
    at: str
    kind: str
    text: str
    step: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "at": self.at,
            "kind": self.kind,
            "text": self.text,
            "step": self.step,
        }


@dataclass
class GraphJob:
    id: str
    owner_id: str
    title: str
    mode: str
    course_id: Optional[str] = None
    course_title: Optional[str] = None
    status: str = STATUS_RUNNING
    step: str = "init"
    message: str = "正在准备生成星图…"
    error: Optional[str] = None
    graph: Optional[Dict[str, Any]] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    events: List[JobEvent] = field(default_factory=list)
    #: 因为超出 MAX_EVENTS_PER_JOB 而丢掉的事件条数
    dropped_events: int = 0
    _next_seq: int = 1

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_RUNNING

    def snapshot(
        self,
        *,
        since: int = 0,
        include_graph: bool = True,
        include_events: bool = True,
    ) -> Dict[str, Any]:
        """给前端的快照。

        since 之后的事件才会带上，所以轮询只传增量。列表接口用
        include_events=False + include_graph=False：它只需要状态和一句进度，
        一张星图几十 KB、事件流几百条，没必要跟着列表一起回。
        """
        events = [e for e in self.events if e.seq > since] if include_events else []
        payload: Dict[str, Any] = {
            "job_id": self.id,
            "title": self.title,
            "mode": self.mode,
            "course_id": self.course_id,
            "course_title": self.course_title,
            "status": self.status,
            "step": self.step,
            "message": self.message,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "events": [e.to_dict() for e in events],
            "last_seq": self.events[-1].seq if self.events else 0,
            "dropped_events": self.dropped_events,
        }
        if include_graph and self.status == STATUS_DONE:
            payload["graph"] = self.graph
        return payload


class GraphJobStore:
    """按 owner 隔离的任务表。所有方法都可以并发调用。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, GraphJob] = {}

    # ── 写 ────────────────────────────────────────────────────────

    def create(
        self,
        owner_id: str,
        *,
        title: str,
        mode: str,
        course_id: Optional[str] = None,
        course_title: Optional[str] = None,
    ) -> GraphJob:
        job = GraphJob(
            id=uuid.uuid4().hex,
            owner_id=owner_id,
            title=title or "未命名星图",
            mode=mode,
            course_id=course_id,
            course_title=course_title,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._prune_locked(owner_id)
        self.append(job.id, "progress", job.message, step=job.step)
        return job

    def append(self, job_id: str, kind: str, text: str, *, step: str = "") -> None:
        """往事件流里追加一条。连续的同类增量会并进上一条。"""
        if not text:
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.updated_at = _now()

            if kind in _MERGEABLE and job.events:
                last = job.events[-1]
                if last.kind == kind and len(last.text) + len(text) <= MAX_DELTA_CHARS:
                    last.text += text
                    last.at = job.updated_at
                    return

            job.events.append(
                JobEvent(seq=job._next_seq, at=job.updated_at, kind=kind, text=text, step=step)
            )
            job._next_seq += 1
            overflow = len(job.events) - MAX_EVENTS_PER_JOB
            if overflow > 0:
                del job.events[:overflow]
                job.dropped_events += overflow

    def set_progress(self, job_id: str, step: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.step = step
            job.message = message
        self.append(job_id, "progress", message, step=step)

    def finish(self, job_id: str, graph: Dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = STATUS_DONE
            job.graph = graph
            job.step = "done"
            job.message = "星图已生成"
            job.finished_at = job.updated_at = _now()
        self.append(job_id, "progress", "星图已生成", step="done")

    def fail(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = STATUS_FAILED
            job.error = message
            job.step = "error"
            job.message = message
            job.finished_at = job.updated_at = _now()
        self.append(job_id, "error", message, step="error")

    def dismiss(self, owner_id: str, job_id: str) -> bool:
        """从列表里去掉一条任务。还在跑的也允许去掉（后台线程自己会跑完）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner_id != owner_id:
                return False
            del self._jobs[job_id]
            return True

    # ── 读 ────────────────────────────────────────────────────────

    def get(self, owner_id: str, job_id: str) -> Optional[GraphJob]:
        with self._lock:
            job = self._jobs.get(job_id)
            # owner 不匹配就当不存在 —— 不泄露"这个 id 存在但不是你的"。
            if job is None or job.owner_id != owner_id:
                return None
            return job

    def list(self, owner_id: str) -> List[GraphJob]:
        with self._lock:
            self._prune_locked(owner_id)
            jobs = [j for j in self._jobs.values() if j.owner_id == owner_id]
        # 先按时间新的在前，再把还在跑的整体提到前面。
        # 两趟靠 sort 的稳定性，组内顺序不会被第二趟打乱。
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        jobs.sort(key=lambda j: not j.is_active)
        return jobs

    # ── 清理 ──────────────────────────────────────────────────────

    def _prune_locked(self, owner_id: str) -> None:
        """丢掉过期和超量的已结束任务。调用方必须已持有锁。"""
        cutoff = datetime.now(timezone.utc) - FINISHED_TTL
        mine = [j for j in self._jobs.values() if j.owner_id == owner_id]

        for job in mine:
            if job.is_active or not job.finished_at:
                continue
            try:
                finished = datetime.fromisoformat(job.finished_at)
            except ValueError:
                continue
            if finished < cutoff:
                self._jobs.pop(job.id, None)

        mine = [j for j in self._jobs.values() if j.owner_id == owner_id]
        if len(mine) <= MAX_JOBS_PER_OWNER:
            return
        # 只丢已结束的；还在跑的一条都不能丢，否则前端就永远等不到结果了。
        finished = sorted(
            (j for j in mine if not j.is_active),
            key=lambda j: j.finished_at or j.created_at,
        )
        for job in finished[: len(mine) - MAX_JOBS_PER_OWNER]:
            self._jobs.pop(job.id, None)


#: 进程级单例。任务本来就是进程内的东西，不需要按请求构造。
graph_jobs = GraphJobStore()


def start_graph_job(
    owner_id: str,
    *,
    mode: str,
    title: str,
    course_id: Optional[str] = None,
    course_title: Optional[str] = None,
    build_service,
    **generate_kwargs: Any,
) -> GraphJob:
    """建一条任务并在后台线程里跑生成。

    build_service 是个无参工厂，在**工作线程里**才调用 —— LearningService 的
    构造会连数据库、可能抛 MissingAPIKey，放在线程里跑可以让这些失败也变成
    任务上的一条 error，而不是让请求直接 500（那样前端连任务都拿不到，也就
    看不到失败原因）。
    """
    job = graph_jobs.create(
        owner_id,
        title=title,
        mode=mode,
        course_id=course_id,
        course_title=course_title,
    )

    def _run() -> None:
        try:
            service = build_service()
            graph = service.run_generation(
                mode=mode,
                on_progress=lambda step, msg: graph_jobs.set_progress(job.id, step, msg),
                on_delta=lambda kind, text: graph_jobs.append(job.id, kind, text),
                **generate_kwargs,
            )
            if not graph:
                graph_jobs.fail(job.id, "星图生成失败，请重试")
            else:
                graph_jobs.finish(job.id, graph)
        except BaseException as exc:  # noqa: BLE001 - 线程里任何异常都要落到任务上
            logger.exception("星图任务失败 job=%s", job.id)
            graph_jobs.fail(job.id, f"星图生成出错：{exc}")

    threading.Thread(target=_run, daemon=True, name=f"graph-job-{job.id[:8]}").start()
    return job
