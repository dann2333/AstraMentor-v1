"""APIClient 对"温度锁死"型模型的兜底。

Kimi K3、以及不少只做推理的模型会把采样温度写死为 1，收到任何自定义
temperature 就直接 400。上层十来个 Agent 各自带着 temperature 调用，逐个
去改既啰嗦又一定会漏，所以兜底做在 APIClient._create_completion 里。

这几条用例锁住三件事：被拒之后要摘掉参数重试、要记住、以及不能把别的错误
也当成温度问题吞掉。
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from utils.api_client import APIClient


class _Completions:
    """记录每次调用，并按需模拟"只接受默认温度"的服务端。"""

    def __init__(self, *, reject_temperature: bool = False, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.reject_temperature = reject_temperature
        self.error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.reject_temperature and "temperature" in kwargs:
            # 真实报文：{"error":{"message":"invalid temperature: only 1 is allowed for this model"}}
            raise RuntimeError("invalid temperature: only 1 is allowed for this model")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")]
        )


def _client(completions: _Completions) -> APIClient:
    client = APIClient.__new__(APIClient)
    client.provider = "moonshot"
    client.model_name = "kimi-k3"
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client


class TemperatureFallbackTests(unittest.TestCase):
    def test_retries_without_temperature_when_rejected(self) -> None:
        completions = _Completions(reject_temperature=True)
        text = _client(completions).generate("问题", temperature=0.7)

        self.assertEqual(text, "ok")
        self.assertEqual(len(completions.calls), 2)
        self.assertEqual(completions.calls[0]["temperature"], 0.7)
        self.assertNotIn("temperature", completions.calls[1])

    def test_remembers_and_stops_sending_temperature(self) -> None:
        completions = _Completions(reject_temperature=True)
        client = _client(completions)

        client.generate("第一次", temperature=0.7)
        client.generate("第二次", temperature=0.2)

        # 第一次是"试探 + 重试"两发，第二次应该直接不带温度，只有一发。
        self.assertEqual(len(completions.calls), 3)
        self.assertNotIn("temperature", completions.calls[2])
        self.assertTrue(client._temperature_unsupported)

    def test_flag_does_not_leak_between_clients(self) -> None:
        rejecting = _Completions(reject_temperature=True)
        client_a = _client(rejecting)
        client_a.generate("问题", temperature=0.7)

        normal = _Completions()
        client_b = _client(normal)
        client_b.generate("问题", temperature=0.7)

        self.assertFalse(client_b._temperature_unsupported)
        self.assertEqual(normal.calls[0]["temperature"], 0.7)

    def test_unrelated_error_is_not_swallowed(self) -> None:
        completions = _Completions(error=RuntimeError("rate limit exceeded"))
        with self.assertRaises(RuntimeError) as caught:
            _client(completions).generate("问题", temperature=0.7)

        self.assertIn("rate limit", str(caught.exception))
        # 只发了一次：不该拿别的错误当温度问题去重试。
        self.assertEqual(len(completions.calls), 1)

    def test_json_and_chat_paths_also_covered(self) -> None:
        for label, call in (
            ("chat", lambda c: c.chat([{"role": "user", "content": "hi"}], temperature=0.7)),
            ("generate_json", lambda c: c.generate_json("hi", temperature=0.7)),
        ):
            with self.subTest(path=label):
                completions = _Completions(reject_temperature=True)
                client = _client(completions)
                try:
                    call(client)
                except Exception:
                    # generate_json 会去解析 "ok"，解析失败不影响本用例要验的东西：
                    # 关键是第二发请求确实摘掉了 temperature。
                    pass
                self.assertGreaterEqual(len(completions.calls), 2)
                self.assertNotIn("temperature", completions.calls[1])


if __name__ == "__main__":
    unittest.main()
