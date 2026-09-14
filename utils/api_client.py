"""
AstraMentor API 客户端

支持多模型提供商（Gemini / 智谱 GLM），通过 ASTRA_PROVIDER
环境变量切换底层实现，对上层 Agent 暴露统一接口。
"""

import os
import json
import logging
from typing import Optional, Any, Dict, Iterator, List

from config import get_config
from utils.web_research import (
    SearchGroundedResponse,
    build_search_context,
)

logger = logging.getLogger(__name__)


class MissingAPIKey(RuntimeError):
    """没配 ASTRA_API_KEY。

    单独立一个异常类型，是为了让接口层能把它翻成一句人看得懂的提示。
    以前这里抛 ValueError，一路冒到 FastAPI 变成 500 Internal Server Error，
    第一次部署的人只会看到"服务器错误"，完全不知道是差一个环境变量。
    """


class APIClient:
    """
    统一 LLM API 客户端

    根据 config.provider 自动选择底层实现：
    - gemini：使用 google-genai SDK
    - zhipu：使用 OpenAI 兼容 SDK（智谱 GLM）
    """

    # 类属性做默认值，这样即使有人绕过 __init__（测试里就用 __new__ 直接造壳）
    # 拿到实例，这个开关也总是存在。真正被拒绝时写的是实例属性，不会串台。
    _temperature_unsupported: bool = False

    # 同理放类属性：绕过 __init__ 造出来的实例也得有个可用的默认强度。
    reasoning_effort: str = "low"

    def __init__(self, model_name: Optional[str] = None):
        config = get_config()
        self.provider = config.api.provider.lower()
        self.model_name = model_name or config.api.model_name
        self.reasoning_effort = config.api.reasoning_effort or "low"

        # 部分推理模型把 temperature 锁死为 1，第一次被拒后就不再传（见
        # _create_completion）。放在实例上而不是模块级，是因为同一进程里
        # 可能并存不同模型的客户端。
        self._temperature_unsupported = False

        # Kimi K3 官方文档明确：temperature/top_p 等为固定值，建议不要显式传入，
        # 且 K3 始终开启思考模式。对 moonshot 直接全程不传 temperature，
        # 思考强度用请求顶层 reasoning_effort（low/high/max）表达。
        if self.provider in ("moonshot", "kimi"):
            self._temperature_unsupported = True

        api_key = config.api.api_key or os.getenv("GEMINI_API_KEY") or os.getenv("ZHIPU_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise MissingAPIKey(
                f"还没配置模型 API Key，无法调用 {self.model_name}。"
                "请设置环境变量 ASTRA_API_KEY 后重启服务"
                "（Docker 用户：docker run -e ASTRA_API_KEY=sk-...）。"
            )

        if self.provider == "gemini":
            self._init_gemini(api_key, config)
        else:
            # NOTE: zhipu / qwen 等均使用 OpenAI 兼容接口
            self._init_openai_compatible(api_key, config)

        logger.info(f"API客户端初始化完成（provider={self.provider}），使用模型: {self.model_name}")

    # ─────────────────────────────────────────────
    # 初始化方法
    # ─────────────────────────────────────────────

    def _init_gemini(self, api_key: str, config: Any) -> None:
        """初始化 Gemini 原生 SDK 客户端"""
        from google import genai
        from google.genai import types

        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                base_url=config.api.api_endpoint,
                timeout=120_000,
            ),
        )

    def _init_openai_compatible(self, api_key: str, config: Any) -> None:
        """初始化 OpenAI 兼容客户端（智谱 GLM / 通义千问 Qwen 等）"""
        from openai import OpenAI

        base_url = config.api.api_endpoint.rstrip("/")
        # OpenAI SDK appends /chat/completions itself. Accept the common user
        # mistake of pasting the full completion URL without producing a
        # duplicated .../chat/completions/chat/completions request.
        suffix = "/chat/completions"
        if base_url.lower().endswith(suffix):
            base_url = base_url[: -len(suffix)]
            logger.warning("ASTRA_API_ENDPOINT 已自动规范化为 API 根地址: %s", base_url)
        # Kimi K3 始终开启思考模式，且思考长度不可关闭。非流式的结构化输出
        # （星图/教学计划等大段 JSON）思考+生成常超过 120s。这里把超时加大到
        # 600s，并把 SDK 自动重试降为 0——之前每次超时都会无上限重试，叠加成
        # 前端看到的"一直卡着不出图"。
        is_moonshot = self.provider in ("moonshot", "kimi")
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=600.0 if is_moonshot else 120.0,
            max_retries=0 if is_moonshot else 2,
        )

    def _apply_thinking(self, kwargs: Dict[str, Any], thinking: bool) -> None:
        """把 thinking 开关翻译成各家 OpenAI 兼容端点能懂的参数。

        Kimi K3 始终开启思考模式，强度用请求顶层 reasoning_effort
        （low / high / max）控制；智谱等则用 reasoning.enabled。

        强度取 config 里的 reasoning_effort（默认 low），界面上的 Thinking
        开关只决定思考过程要不要展示出来——K3 本来也关不掉思考，之前按开关
        切到 high 的结果是用户一勾就多等好几分钟，还以为是卡住了。
        """
        if self.provider in ("moonshot", "kimi"):
            kwargs["extra_body"] = {"reasoning_effort": self.reasoning_effort}
        elif thinking:
            kwargs["extra_body"] = {"reasoning": {"enabled": True}}

    # ─────────────────────────────────────────────
    # OpenAI 兼容端点的统一入口
    # ─────────────────────────────────────────────

    def _create_completion(self, **kwargs: Any) -> Any:
        """
        调用 OpenAI 兼容的 chat.completions，并兜住"temperature 不可改"这一类模型。

        Kimi K3、以及不少只做推理的模型，会把采样温度锁死，传任何自定义值都直接
        400。上层 Agent 各自带着 temperature 调用，逐个改既啰嗦又容易漏，所以在
        这里兜一次：第一次被拒就摘掉参数重试，并记在实例上，后续调用不再带。
        """
        if self._temperature_unsupported:
            kwargs.pop("temperature", None)
        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if "temperature" not in kwargs or "temperature" not in str(exc).lower():
                raise
            logger.warning(
                "模型 %s 不接受自定义 temperature，改用模型默认值重试", self.model_name
            )
            self._temperature_unsupported = True
            kwargs.pop("temperature", None)
            return self.client.chat.completions.create(**kwargs)

    # ─────────────────────────────────────────────
    # 公共接口 1：文本 / 多模态生成
    # ─────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        image: Optional[str] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        thinking: bool = False,
    ) -> str:
        if self.provider == "gemini":
            return self._generate_gemini(
                prompt, image, system_instruction, temperature, max_tokens, thinking
            )
        return self._generate_zhipu(
            prompt, image, system_instruction, temperature, max_tokens, thinking
        )

    def _generate_gemini(
        self,
        prompt: str,
        image: Optional[str],
        system_instruction: Optional[str],
        temperature: float,
        max_tokens: Optional[int] = None,
        thinking: bool = False,
    ) -> str:
        from google.genai import types

        try:
            config_kwargs: Dict[str, Any] = {"temperature": temperature}
            if max_tokens:
                config_kwargs["max_output_tokens"] = max_tokens
            if thinking and hasattr(types, "ThinkingConfig"):
                try:
                    config_kwargs["thinking_config"] = types.ThinkingConfig(
                        include_thoughts=True
                    )
                except TypeError:
                    logger.warning("当前 Gemini SDK 不支持 include_thoughts，已关闭 Thinking")
            cfg = types.GenerateContentConfig(**config_kwargs)
            if system_instruction:
                cfg.system_instruction = system_instruction

            contents = [prompt]

            if image:
                import base64
                try:
                    mime_type = "image/jpeg"
                    image_data = image
                    if "base64," in image:
                        header, image_data = image.split("base64,")
                        if "image/" in header:
                            mime_type = header.split(";")[0].split(":")[1]
                    decoded_data = base64.b64decode(image_data)
                    contents = [
                        types.Part(text=prompt),
                        types.Part(
                            inline_data=types.Blob(
                                mime_type=mime_type,
                                data=decoded_data,
                            )
                        ),
                    ]
                except Exception as e:
                    logger.error(f"Failed to process image: {e}")
                    contents = [prompt]

            resp = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=cfg,
            )
            return getattr(resp, "text", "") or ""

        except Exception as e:
            logger.error(f"内容生成失败 (Gemini): {e}")
            raise

    def _generate_zhipu(
        self,
        prompt: str,
        image: Optional[str],
        system_instruction: Optional[str],
        temperature: float,
        max_tokens: Optional[int] = None,
        thinking: bool = False,
    ) -> str:
        """通过 OpenAI 兼容接口调用智谱 GLM"""
        try:
            messages: List[Dict[str, Any]] = []

            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})

            # NOTE: 多模态支持——将 Base64 图片转为 OpenAI vision 格式
            if image:
                image_url = image
                if not image.startswith("data:"):
                    image_url = f"data:image/jpeg;base64,{image}"
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                })
            else:
                messages.append({"role": "user", "content": prompt})

            kwargs: Dict[str, Any] = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            self._apply_thinking(kwargs, thinking)

            try:
                resp = self._create_completion(**kwargs)
            except Exception:
                if not thinking:
                    raise
                logger.warning("当前兼容端点拒绝 Thinking 参数，已按普通模式重试")
                kwargs.pop("extra_body", None)
                resp = self._create_completion(**kwargs)
            return self._extract_zhipu_content(resp)

        except Exception as e:
            logger.error(f"内容生成失败 (GLM): {e}")
            raise

    # ─────────────────────────────────────────────
    # 公共接口：文本流式生成
    # ─────────────────────────────────────────────

    def stream_generate(
        self,
        prompt: str,
        image: Optional[str] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        thinking: bool = False,
    ) -> Iterator[Dict[str, Any]]:
        """Yield provider-neutral reasoning/content delta dictionaries."""
        if self.provider == "gemini":
            yield from self._stream_gemini(
                prompt=prompt,
                image=image,
                system_instruction=system_instruction,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
            )
            return
        yield from self._stream_openai_compatible(
            prompt=prompt,
            image=image,
            system_instruction=system_instruction,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
        )

    def _stream_gemini(
        self,
        prompt: str,
        image: Optional[str],
        system_instruction: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
        thinking: bool,
    ) -> Iterator[Dict[str, Any]]:
        from google.genai import types

        config_kwargs: Dict[str, Any] = {"temperature": temperature}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if max_tokens:
            config_kwargs["max_output_tokens"] = max_tokens

        thinking_enabled = False
        if thinking and hasattr(types, "ThinkingConfig"):
            try:
                config_kwargs["thinking_config"] = types.ThinkingConfig(
                    include_thoughts=True
                )
                thinking_enabled = True
            except TypeError:
                pass
        if thinking and not thinking_enabled:
            yield {
                "type": "warning",
                "message": "当前 Gemini SDK/模型不支持展示思考过程，已使用普通模式",
            }

        contents: Any = [prompt]
        if image:
            import base64

            mime_type = "image/jpeg"
            image_data = image
            if "base64," in image:
                header, image_data = image.split("base64,", 1)
                if "image/" in header:
                    mime_type = header.split(";", 1)[0].split(":", 1)[1]
            contents = [
                types.Part(text=prompt),
                types.Part(
                    inline_data=types.Blob(
                        mime_type=mime_type,
                        data=base64.b64decode(image_data),
                    )
                ),
            ]

        stream = self.client.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        for chunk in stream:
            emitted_part = False
            for candidate in getattr(chunk, "candidates", []) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", []) or []:
                    text = getattr(part, "text", None)
                    if not text:
                        continue
                    emitted_part = True
                    yield {
                        "type": "reasoning_delta" if getattr(part, "thought", False) else "content_delta",
                        "text": text,
                    }
            if not emitted_part:
                text = getattr(chunk, "text", None)
                if text:
                    yield {"type": "content_delta", "text": text}

    def _stream_openai_compatible(
        self,
        prompt: str,
        image: Optional[str],
        system_instruction: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
        thinking: bool,
    ) -> Iterator[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        if image:
            image_url = image if image.startswith("data:") else f"data:image/jpeg;base64,{image}"
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": prompt})

        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        self._apply_thinking(kwargs, thinking)

        try:
            stream = self._create_completion(**kwargs)
        except Exception:
            if not thinking:
                raise
            kwargs.pop("extra_body", None)
            yield {
                "type": "warning",
                "message": "当前模型不支持 Thinking，已自动切换为普通回答",
            }
            stream = self._create_completion(**kwargs)

        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            reasoning = (
                getattr(delta, "reasoning_content", None)
                or getattr(delta, "reasoning", None)
            )
            if reasoning:
                yield {"type": "reasoning_delta", "text": str(reasoning)}
            content = getattr(delta, "content", None)
            if content:
                yield {"type": "content_delta", "text": str(content)}

    # ─────────────────────────────────────────────
    # 公共接口 2：联网搜索 + 生成
    # ─────────────────────────────────────────────

    def generate_with_search(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        search_query: Optional[str] = None,
    ) -> SearchGroundedResponse:
        """
        带联网搜索的内容生成（DuckDuckGo + LLM 两阶段方案）

        NOTE: 该方法不依赖特定 SDK，内部调用 self.generate，
        Provider 切换自动传导，无需区分实现。
        """
        try:
            query = search_query or prompt[:120]
            logger.info(f"🔍 开始联网搜索: '{query[:60]}...'")
            search_context, sources = build_search_context(query, max_results=5)

            if search_context:
                logger.info(f"🔍 搜索完成，获得 {len(sources)} 个来源，注入 prompt")
                enhanced_prompt = f"""{prompt}

以下是通过联网搜索获取的最新参考资料，请在回答中充分利用这些信息：

{search_context}

请基于以上搜索结果，结合你的知识，生成准确、详细的回答。"""
            else:
                logger.warning("🔍 搜索未返回结果，使用原始 prompt")
                enhanced_prompt = prompt

            text = self.generate(
                prompt=enhanced_prompt,
                system_instruction=system_instruction,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            logger.info(f"✅ 联网搜索生成完成: {len(sources)} 个来源")

            return SearchGroundedResponse(
                content=text,
                sources=sources,
                search_queries=[query] if search_context else [],
            )

        except Exception as e:
            logger.error(f"❌ 联网搜索生成失败: {e}")
            raise

    # ─────────────────────────────────────────────
    # 公共接口 3：结构化 JSON 输出
    # ─────────────────────────────────────────────

    def generate_json(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        output_schema: Optional[Any] = None,
    ):
        """
        生成结构化 JSON

        output_schema: Pydantic 模型类
        返回：Pydantic 实例（有 schema 时）/ dict（无 schema 时）
        """
        if self.provider == "gemini":
            return self._generate_json_gemini(prompt, system_instruction, temperature, output_schema)
        return self._generate_json_zhipu(prompt, system_instruction, temperature, output_schema)

    def _generate_json_gemini(
        self,
        prompt: str,
        system_instruction: Optional[str],
        temperature: float,
        output_schema: Optional[Any],
    ):
        """Gemini 原生结构化输出——利用 response_schema 直接约束"""
        from google.genai import types

        if output_schema:
            try:
                cfg = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=output_schema,
                    temperature=temperature,
                )
                if system_instruction:
                    cfg.system_instruction = system_instruction

                resp = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=cfg,
                )
                return resp.parsed

            except Exception as e:
                logger.error(f"JSON生成失败 (Gemini): {e}")
                raise
        else:
            return self._parse_json_from_text(
                self.generate(prompt=prompt, system_instruction=system_instruction, temperature=temperature)
            )

    def _stream_collect_text(self, kwargs: Dict[str, Any]) -> str:
        """流式调用并拼接完整 content，跳过 reasoning 增量。

        用于 K3 的长 JSON 生成：流式让网关持续有数据可传，规避非流式的
        单请求超时（504）。只收集最终 content，丢弃思考过程。
        """
        kwargs = dict(kwargs)
        kwargs["stream"] = True
        parts: List[str] = []
        # 走 _create_completion：它会在模型锁死 temperature 时自动剥掉该参数
        stream = self._create_completion(**kwargs)
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                parts.append(str(content))
        return "".join(parts)

    def _generate_json_zhipu(
        self,
        prompt: str,
        system_instruction: Optional[str],
        temperature: float,
        output_schema: Optional[Any],
    ):
        """
        GLM 结构化输出——使用 json_object 模式 + Schema 引导

        NOTE: 智谱 GLM 不支持 Gemini 原生的 response_schema 参数，
        改为将 Pydantic 的 JSON Schema 注入 system prompt 中引导 AI 输出，
        配合 response_format=json_object 确保返回合法 JSON，
        最后通过 model_validate 校验和反序列化。
        """
        try:
            messages: List[Dict[str, Any]] = []

            # 构建 system prompt：加入 JSON Schema 约束
            sys_parts = []
            if system_instruction:
                sys_parts.append(system_instruction)

            if output_schema:
                schema_json = json.dumps(
                    output_schema.model_json_schema(),
                    ensure_ascii=False,
                    indent=2,
                )
                sys_parts.append(
                    f"\n\n你必须严格按照以下 JSON Schema 格式输出，不要输出任何额外文字：\n```json\n{schema_json}\n```"
                )

            if sys_parts:
                messages.append({"role": "system", "content": "".join(sys_parts)})

            messages.append({"role": "user", "content": prompt})

            kwargs: Dict[str, Any] = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }
            # NOTE: K3 始终思考，结构化输出用 low 档缩短推理、尽快返回 JSON
            self._apply_thinking(kwargs, thinking=False)

            # NOTE: 星图/教学计划等 JSON 输出很长，K3 非流式生成会超过 Kimi 网关
            # 约 2 分钟的单请求上限而 504。改用流式：只要持续有增量数据，网关就
            # 不会切断，最后再拼接完整 JSON 文本。
            if self.provider in ("moonshot", "kimi"):
                text = self._stream_collect_text(kwargs)
            else:
                resp = self._create_completion(**kwargs)
                text = self._extract_zhipu_content(resp)

            if output_schema:
                # 通过 Pydantic model_validate 校验并返回实例
                data = json.loads(text)
                return output_schema.model_validate(data)
            else:
                return self._parse_json_from_text(text)

        except Exception as e:
            logger.error(f"JSON生成失败 (GLM): {e}")
            raise

    # ─────────────────────────────────────────────
    # 公共接口 4：多轮对话
    # ─────────────────────────────────────────────

    def chat(
        self,
        messages: List[Dict[str, str]],
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
    ) -> str:
        if self.provider == "gemini":
            return self._chat_gemini(messages, system_instruction, temperature)
        return self._chat_zhipu(messages, system_instruction, temperature)

    def _chat_gemini(
        self,
        messages: List[Dict[str, str]],
        system_instruction: Optional[str],
        temperature: float,
    ) -> str:
        from google.genai import types

        try:
            contents = []
            for m in messages:
                role = m.get("role", "user")
                text = m.get("content", "")
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part(text=text)],
                    )
                )

            cfg = types.GenerateContentConfig(temperature=temperature)
            if system_instruction:
                cfg.system_instruction = system_instruction

            resp = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=cfg,
            )
            return getattr(resp, "text", "") or ""

        except Exception as e:
            logger.error(f"对话生成失败 (Gemini): {e}")
            raise

    def _chat_zhipu(
        self,
        messages: List[Dict[str, str]],
        system_instruction: Optional[str],
        temperature: float,
    ) -> str:
        """GLM 多轮对话——直接使用 OpenAI 标准 messages 格式"""
        try:
            api_messages: List[Dict[str, Any]] = []

            if system_instruction:
                api_messages.append({"role": "system", "content": system_instruction})

            for m in messages:
                api_messages.append({
                    "role": m.get("role", "user"),
                    "content": m.get("content", ""),
                })

            resp = self._create_completion(
                model=self.model_name,
                messages=api_messages,
                temperature=temperature,
            )
            return self._extract_zhipu_content(resp)

        except Exception as e:
            logger.error(f"对话生成失败 (GLM): {e}")
            raise

    # ─────────────────────────────────────────────
    # 公共接口 5：连接测试
    # ─────────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            if self.provider == "gemini":
                resp = self.client.models.generate_content(
                    model=self.model_name,
                    contents="Say 'OK' if you can hear me.",
                )
                text = getattr(resp, "text", "") or ""
            else:
                resp = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": "Say 'OK' if you can hear me."}],
                    max_tokens=500,
                )
                text = resp.choices[0].message.content or ""

            return ("OK" in text) or (len(text) > 0)

        except Exception as e:
            logger.error(f"API连接测试失败: {e}")
            return False

    # ─────────────────────────────────────────────
    # 内部工具方法（含 GLM 推理模型回退逻辑）
    # ─────────────────────────────────────────────

    def _extract_zhipu_content(self, resp: Any) -> str:
        """
        从 GLM 响应中提取文本内容

        NOTE: GLM-5 是推理模型，内部"思考"消耗 token 后 content 可能为 None，
        此时回退到 reasoning_content 字段获取内容。
        """
        msg = resp.choices[0].message
        content = msg.content

        if content:
            return content

        # HACK: GLM-5 推理模型回退——当 content 为空时尝试读取 reasoning_content
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            logger.warning("GLM content 为空，回退使用 reasoning_content")
            return reasoning

        logger.warning(f"GLM 响应内容为空 (finish_reason={resp.choices[0].finish_reason})")
        return ""

    @staticmethod
    def _parse_json_from_text(text: str) -> dict:
        """从可能包含 Markdown 代码块的文本中提取 JSON"""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}\n原始文本: {text}")
            return {
                "_error": f"JSON解析错误: {e}",
                "_raw": text,
            }
