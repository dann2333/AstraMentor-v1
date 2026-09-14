"""星图生成任务的测试。

这批用例是对着"用户看到的那个故障"写的，不是对着实现写的：

  生成任何课程的星图都没有记录，"最近在学"永远是空的，等很久之后一个
  network error，日志里什么错都没有。

根因是生成走一条 SSE 长连接，而它在模型静默期（K3 关不掉思考，几十秒到几分钟）
**一个字节都不发**。反代的读超时（nginx 默认 60s）把这条静默连接掐掉，前端
拿不到 done 事件，星图不落盘，历史就永远是空的；后端那个工作线程照常跑完、
照常返回，所以日志干净得看不出问题。

所以这里钉两件事：
  1. SSE 生成器空闲时必须有心跳，连接不能静默。
  2. 生成可以完全脱离请求存活 —— 任务在服务端跑，刷新页面能接回来。
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("ASTRA_DB_PATH", tempfile.mktemp(suffix=".db"))
os.environ.setdefault("ASTRA_API_KEY", "dummy-for-tests")

from fastapi.testclient import TestClient  # noqa: E402

from services import graph_jobs as jobs_module  # noqa: E402
from services.graph_jobs import (  # noqa: E402
    MAX_DELTA_CHARS,
    MAX_EVENTS_PER_JOB,
    GraphJobStore,
    start_graph_job,
)
from services.streaming_service import encode_sse, with_heartbeat  # noqa: E402


def _wait_until_done(store: GraphJobStore, owner: str, job_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get(owner, job_id)
        if job is None or not job.is_active:
            return job
        time.sleep(0.01)
    raise AssertionError("任务没有在预期时间内结束")


class HeartbeatTests(unittest.TestCase):
    """SSE 流在空闲期必须持续有字节，否则反代会把它掐掉。"""

    def test_idle_generator_still_emits_bytes(self) -> None:
        def slow():
            yield encode_sse("progress", {"message": "开始"})
            time.sleep(0.35)  # 模拟模型静默期
            yield encode_sse("done", {"ok": True})

        chunks = list(with_heartbeat(slow(), interval=0.1))
        heartbeats = [c for c in chunks if c.startswith(": ")]
        self.assertGreaterEqual(
            len(heartbeats), 2, f"空闲期没有心跳，连接会被读超时掐掉：{chunks}"
        )

    def test_events_still_arrive_in_order(self) -> None:
        def stream():
            yield encode_sse("progress", {"n": 1})
            yield encode_sse("progress", {"n": 2})
            yield encode_sse("done", {"n": 3})

        chunks = [c for c in with_heartbeat(stream(), interval=5) if not c.startswith(": ")]
        self.assertEqual(len(chunks), 3)
        self.assertIn('"n":1', chunks[0])
        self.assertIn("event: done", chunks[2])

    def test_heartbeat_is_an_sse_comment(self) -> None:
        """心跳必须是注释（无 data: 行），否则前端会把它当成一个事件。"""
        def slow():
            time.sleep(0.25)
            yield encode_sse("done", {})

        beat = next(c for c in with_heartbeat(slow(), interval=0.05) if c.startswith(": "))
        self.assertTrue(beat.startswith(":"))
        self.assertNotIn("data:", beat)
        self.assertTrue(beat.endswith("\n\n"))

    def test_generator_failure_becomes_an_error_event(self) -> None:
        """事件流已经开始了就改不了 HTTP 状态码，异常必须变成 error 事件。"""
        def boom():
            yield encode_sse("progress", {})
            raise RuntimeError("生成器炸了")

        chunks = list(with_heartbeat(boom(), interval=5))
        self.assertIn("event: error", chunks[-1])
        self.assertIn("生成器炸了", chunks[-1])

    def test_client_disconnect_does_not_raise(self) -> None:
        def slow():
            yield encode_sse("progress", {})
            time.sleep(5)
            yield encode_sse("done", {})

        gen = with_heartbeat(slow(), interval=5)
        next(gen)
        gen.close()  # 不应抛


class _FakeService:
    """假的 LearningService：按顺序回调，不打真模型。"""

    def __init__(self, *, fail: str = "", graph=None, delay: float = 0.0) -> None:
        self.fail = fail
        self.graph = graph if graph is not None else {"nodes": [{"id": "a"}], "links": []}
        self.delay = delay

    def run_generation(self, *, mode, on_progress, on_delta, **kwargs):
        if self.fail:
            raise RuntimeError(self.fail)
        on_progress("retrieve", "正在检索课程教材证据…")
        on_delta("reasoning", "先看目录")
        on_delta("reasoning", "，再定层级。")
        on_progress("generate", "AI 正在规划知识结构…")
        on_delta("content", '{"nodes":[')
        on_delta("content", "]}")
        if self.delay:
            time.sleep(self.delay)
        return self.graph


class JobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = GraphJobStore()

    def test_progress_and_model_output_both_land_in_the_event_log(self) -> None:
        """光有阶段标签不够 —— 模型的实时输出也要透出去，否则界面上看着像卡死。"""
        job = self.store.create("o1", title="RAG", mode="topic")
        self.store.set_progress(job.id, "generate", "AI 正在规划…")
        self.store.append(job.id, "reasoning", "想一想")
        self.store.append(job.id, "content", '{"nodes":')

        kinds = [e.kind for e in self.store.get("o1", job.id).events]
        self.assertIn("progress", kinds)
        self.assertIn("reasoning", kinds)
        self.assertIn("content", kinds)

    def test_consecutive_deltas_of_one_kind_are_merged(self) -> None:
        job = self.store.create("o1", title="T", mode="topic")
        before = len(self.store.get("o1", job.id).events)
        for _ in range(20):
            self.store.append(job.id, "reasoning", "字")
        events = self.store.get("o1", job.id).events
        self.assertEqual(len(events) - before, 1, "同类增量应当并成一条")
        self.assertEqual(events[-1].text, "字" * 20)

    def test_merging_stops_at_the_size_cap(self) -> None:
        job = self.store.create("o1", title="T", mode="topic")
        self.store.append(job.id, "content", "x" * (MAX_DELTA_CHARS - 1))
        self.store.append(job.id, "content", "yy")
        tail = self.store.get("o1", job.id).events[-2:]
        self.assertEqual([e.kind for e in tail], ["content", "content"])
        self.assertLessEqual(len(tail[0].text), MAX_DELTA_CHARS)

    def test_different_kinds_are_never_merged(self) -> None:
        job = self.store.create("o1", title="T", mode="topic")
        self.store.append(job.id, "reasoning", "思考")
        self.store.append(job.id, "content", "输出")
        self.assertEqual(
            [e.kind for e in self.store.get("o1", job.id).events[-2:]],
            ["reasoning", "content"],
        )

    def test_since_returns_only_new_events(self) -> None:
        job = self.store.create("o1", title="T", mode="topic")
        self.store.set_progress(job.id, "a", "第一步")
        first = self.store.get("o1", job.id).snapshot()
        cursor = first["last_seq"]

        self.store.set_progress(job.id, "b", "第二步")
        delta = self.store.get("o1", job.id).snapshot(since=cursor)

        self.assertEqual([e["text"] for e in delta["events"]], ["第二步"])

    def test_event_log_is_bounded_and_reports_what_it_dropped(self) -> None:
        """悄悄截断会让前端以为自己看全了，所以丢了多少必须如实报告。"""
        job = self.store.create("o1", title="T", mode="topic")
        for i in range(MAX_EVENTS_PER_JOB + 50):
            self.store.set_progress(job.id, f"s{i}", f"第 {i} 步")
        snap = self.store.get("o1", job.id).snapshot()
        self.assertLessEqual(len(snap["events"]), MAX_EVENTS_PER_JOB)
        self.assertGreater(snap["dropped_events"], 0)

    def test_jobs_are_scoped_to_their_owner(self) -> None:
        mine = self.store.create("o1", title="我的", mode="topic")
        self.store.create("o2", title="别人的", mode="topic")

        self.assertIsNone(self.store.get("o2", mine.id), "不该看到别人的任务")
        self.assertFalse(self.store.dismiss("o2", mine.id), "不该能删别人的任务")
        self.assertEqual([j.title for j in self.store.list("o1")], ["我的"])

    def test_running_jobs_sort_first(self) -> None:
        done = self.store.create("o1", title="已完成", mode="topic")
        self.store.finish(done.id, {"nodes": []})
        self.store.create("o1", title="在跑", mode="topic")
        self.assertEqual([j.title for j in self.store.list("o1")][0], "在跑")

    def test_list_snapshot_carries_neither_events_nor_graph(self) -> None:
        """列表要轻：一张星图几十 KB，事件流几百条，都不该跟着列表回。"""
        job = self.store.create("o1", title="T", mode="topic")
        self.store.append(job.id, "content", "x" * 100)
        self.store.finish(job.id, {"nodes": [{"id": "a"}]})
        snap = self.store.get("o1", job.id).snapshot(
            include_events=False, include_graph=False
        )
        self.assertEqual(snap["events"], [])
        self.assertNotIn("graph", snap)
        self.assertGreater(snap["last_seq"], 0)

    def test_concurrent_appends_do_not_lose_events(self) -> None:
        job = self.store.create("o1", title="T", mode="topic")
        before = len(self.store.get("o1", job.id).events)

        def hammer(tag: str) -> None:
            for i in range(40):
                self.store.set_progress(job.id, f"{tag}{i}", f"{tag}-{i}")

        threads = [threading.Thread(target=hammer, args=(t,)) for t in "abcd"]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        events = self.store.get("o1", job.id).events
        self.assertEqual(len(events) - before, 160)
        seqs = [e.seq for e in events]
        self.assertEqual(len(set(seqs)), len(seqs), "seq 不能重复，否则前端会丢事件")
        self.assertEqual(seqs, sorted(seqs))


class JobRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = GraphJobStore()
        patcher = patch.object(jobs_module, "graph_jobs", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_finished_job_carries_the_graph(self) -> None:
        job = start_graph_job(
            "o1", mode="topic", title="RAG", build_service=lambda: _FakeService()
        )
        done = _wait_until_done(self.store, "o1", job.id)
        self.assertEqual(done.status, "done")
        self.assertEqual(done.graph, {"nodes": [{"id": "a"}], "links": []})

    def test_the_model_output_is_visible_while_it_runs(self) -> None:
        """这是"不给人无响应错觉"那条要求的落点。"""
        job = start_graph_job(
            "o1", mode="topic", title="RAG", build_service=lambda: _FakeService()
        )
        _wait_until_done(self.store, "o1", job.id)
        texts = {e.kind: e.text for e in self.store.get("o1", job.id).events}
        self.assertEqual(texts["reasoning"], "先看目录，再定层级。")
        self.assertIn("nodes", texts["content"])

    def test_a_worker_exception_becomes_a_failed_job_not_a_500(self) -> None:
        job = start_graph_job(
            "o1", mode="topic", title="X",
            build_service=lambda: _FakeService(fail="模型欠费"),
        )
        failed = _wait_until_done(self.store, "o1", job.id)
        self.assertEqual(failed.status, "failed")
        self.assertIn("模型欠费", failed.error or "")

    def test_service_construction_failure_also_lands_on_the_job(self) -> None:
        """构造 LearningService 会连库、可能缺 API Key —— 那些失败也得看得见。"""
        def broken():
            raise RuntimeError("还没配 ASTRA_API_KEY")

        job = start_graph_job("o1", mode="topic", title="X", build_service=broken)
        failed = _wait_until_done(self.store, "o1", job.id)
        self.assertEqual(failed.status, "failed")
        self.assertIn("ASTRA_API_KEY", failed.error or "")

    def test_the_real_reason_reaches_the_job_not_just_please_retry(self) -> None:
        """generate_knowledge_graph 把异常吞成 None，于是任务只能说"请重试"。

        而真实原因常常是重试永远不会好的那一类 —— 401 Key 不对、余额不足、
        端点写错。这条钉住原因必须一路传到任务上。
        """
        from services.learning_service import GraphGenerationFailed

        class SwallowsTheError:
            """复刻那个吞异常的行为：返回 None，原因放在 last_error 上。"""

            last_error = "Error code: 401 - Incorrect API key provided"

            def run_generation(self, **kwargs):
                raise GraphGenerationFailed(self.last_error)

        job = start_graph_job(
            "o1", mode="topic", title="X", build_service=lambda: SwallowsTheError()
        )
        failed = _wait_until_done(self.store, "o1", job.id)
        self.assertEqual(failed.status, "failed")
        self.assertIn("401", failed.error or "")
        self.assertNotIn("请重试", failed.error or "")

    def test_an_empty_graph_is_reported_as_a_failure(self) -> None:
        job = start_graph_job(
            "o1", mode="topic", title="X",
            build_service=lambda: _FakeService(graph={}),
        )
        failed = _wait_until_done(self.store, "o1", job.id)
        self.assertEqual(failed.status, "failed")


class JobApiTests(unittest.TestCase):
    """端点层：这条路必须完全脱离长连接存活。"""

    def setUp(self) -> None:
        import backend.app as app_module

        self.client = TestClient(app_module.app)
        self.store = GraphJobStore()
        patcher = patch.object(jobs_module, "graph_jobs", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

        api_patcher = patch("backend.api.graph_jobs", self.store)
        api_patcher.start()
        self.addCleanup(api_patcher.stop)

    def test_create_returns_immediately_instead_of_holding_the_request(self) -> None:
        with patch("backend.api.get_service", lambda *a, **k: _FakeService(delay=0.5)):
            started = time.monotonic()
            response = self.client.post(
                "/api/graph/jobs", json={"topic": "RAG", "complexity": 1}
            )
            took = time.monotonic() - started

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "running")
        self.assertLess(took, 0.4, "创建任务不该等生成跑完")

    def test_polling_with_since_walks_the_event_log(self) -> None:
        with patch("backend.api.get_service", lambda *a, **k: _FakeService()):
            job_id = self.client.post(
                "/api/graph/jobs", json={"topic": "RAG"}
            ).json()["job_id"]
            _wait_until_done(self.store, "anonymous", job_id)

            full = self.client.get(f"/api/graph/jobs/{job_id}").json()
            self.assertEqual(full["status"], "done")
            self.assertTrue(full["graph"])

            tail = self.client.get(
                f"/api/graph/jobs/{job_id}", params={"since": full["last_seq"]}
            ).json()
            self.assertEqual(tail["events"], [], "since 之后不该再回旧事件")

    def test_a_refresh_can_pick_the_job_back_up(self) -> None:
        """这是"刷新之后什么都没了"那条抱怨的落点：任务不属于任何一次请求。"""
        with patch("backend.api.get_service", lambda *a, **k: _FakeService(delay=0.3)):
            job_id = self.client.post(
                "/api/graph/jobs", json={"topic": "RAG"}
            ).json()["job_id"]

            # 模拟刷新：换一个全新的客户端，只凭列表接口就要能找回来
            fresh = TestClient(self.client.app)
            listed = fresh.get("/api/graph/jobs").json()["jobs"]
            self.assertEqual([j["job_id"] for j in listed], [job_id])
            self.assertEqual(listed[0]["status"], "running")

            _wait_until_done(self.store, "anonymous", job_id)
            self.assertTrue(fresh.get(f"/api/graph/jobs/{job_id}").json()["graph"])

    def test_unknown_job_is_404(self) -> None:
        self.assertEqual(self.client.get("/api/graph/jobs/nope").status_code, 404)
        self.assertEqual(self.client.delete("/api/graph/jobs/nope").status_code, 404)

    def test_dismiss_removes_it_from_the_list(self) -> None:
        with patch("backend.api.get_service", lambda *a, **k: _FakeService()):
            job_id = self.client.post(
                "/api/graph/jobs", json={"topic": "RAG"}
            ).json()["job_id"]
        self.assertEqual(self.client.delete(f"/api/graph/jobs/{job_id}").status_code, 204)
        self.assertEqual(self.client.get("/api/graph/jobs").json()["jobs"], [])


if __name__ == "__main__":
    unittest.main()
