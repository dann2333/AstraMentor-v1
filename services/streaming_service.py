"""Helpers for stable JSON-encoded Server-Sent Events."""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)

#: 空闲多久就补一次心跳（秒）。取 15 秒是为了在常见的 60 秒读超时下有 4 次
#: 机会，同时又不至于把日志和流量搞得很吵。
HEARTBEAT_SECONDS = 15


def encode_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def encode_heartbeat() -> str:
    """一条 SSE 注释。

    以 ``:`` 开头的行在 SSE 规范里就是注释，浏览器和 EventSource 都会丢掉它，
    前端的解析器也不会当成事件（没有 data: 行）。它唯一的作用是让这条连接
    在字节层面上不空闲。
    """
    return ": ping\n\n"


def with_heartbeat(
    events: Iterable[str],
    *,
    interval: float = HEARTBEAT_SECONDS,
) -> Iterator[str]:
    """给一个同步 SSE 生成器套上心跳。

    ## 为什么必须有

    星图生成这条流在发出"AI 正在规划知识结构…"之后，就只剩一次几十秒到几分钟
    的模型调用，期间**一个字节都不发**。中间任何一层有读超时的东西都会把这条
    静默连接掐掉：nginx 的 proxy_read_timeout 默认就是 60 秒。

    掐掉之后的表现极其难查：浏览器只说一句 network error；后端那个工作线程还在
    照常跑、跑完照常返回，日志里一条错都没有。前端因此永远收不到 done 事件，
    星图不落盘，"最近在学"就永远是空的。

    所以不能只靠使用者去调反代的超时 —— 流自己要保证不空闲。

    ## 做法

    真正的生成器在后台线程里跑，本函数从队列取；取不到就补一条注释。这样
    不管阻塞发生在生成器的哪一步（等模型、等检索、等队列），外面看到的都是
    一条持续有字节的连接。
    """
    q: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=64)
    _CHUNK, _DONE, _FAIL = "chunk", "done", "fail"

    def _pump() -> None:
        try:
            for chunk in events:
                q.put((_CHUNK, chunk))
        except BaseException as exc:  # noqa: BLE001 - 线程里不能让异常无声消失
            logger.exception("SSE 生成器抛异常")
            q.put((_FAIL, str(exc)))
        finally:
            q.put((_DONE, ""))

    worker = threading.Thread(target=_pump, daemon=True, name="sse-pump")
    worker.start()

    failure: str | None = None
    try:
        while True:
            try:
                kind, payload = q.get(timeout=interval)
            except queue.Empty:
                # 生成器还在忙。补一条注释，让连接在字节层面上活着。
                yield encode_heartbeat()
                continue
            if kind == _CHUNK:
                yield payload
            elif kind == _FAIL:
                failure = payload
            else:
                break
    except GeneratorExit:
        # 客户端断开。后台线程是 daemon，且下游生成器自己会收到 GeneratorExit，
        # 这里不再往外 yield 任何东西。
        raise

    if failure is not None:
        # 异常发生在后台线程里，事件流已经开始了，改不了 HTTP 状态码，
        # 只能作为一条 error 事件送出去 —— 这也是端点自己的兜底做法。
        yield encode_sse("error", {"message": failure})
