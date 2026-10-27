"""模型接入层：一个不依赖任何 SDK 的 OpenAI 兼容客户端。

这一层要解决 4 个问题，每个都对应下面的一段代码：
  1. 怎么把一个调用发出去             → chat()
  2. 什么时候该重试、什么时候重试没用  → RETRYABLE_STATUS + _map_client_error()
  3. 重试之间等多久                   → _retry_delay()（指数退避 + 抖动）
  4. 上游返回了不认识的格式怎么办      → _parse_response()

它对外只暴露两样东西：ChatMessage（输入）和 LLMResponse（输出）。
上层（未来是 Agent 执行循环）不需要知道 HTTP 的存在。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.exceptions import UpstreamError

logger = logging.getLogger(__name__)

# 这些状态码表示"现在不行，等会儿可能行"，重试有意义。
# 注意 429 在里面（限流），但 401/403/404 不在 —— 那是配置写错了，
# 重试一百次结果一样，只会白烧时间和钱。
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


@dataclass(slots=True)
class ChatMessage:
    """role 有四种：system / user / assistant / tool。

    这个层级顺序不是随便定的（对应 JD 里"理解 System / Developer / User 的优先级"）：
    system 是"你是谁、守什么规矩"，优先级最高、最稳定；
    user 是"这次要干什么"，每次都变。
    阶段 07 做 Rules 时，规则会落到 system 层，而用户输入永远进不了 system 层 ——
    这是防提示词注入的第一道墙。

    tool 是阶段 02b-2 新增的角色，专门用来把**工具执行结果**回传给模型。
    它带 tool_call_id 做配对，模型据此知道"这是我刚才要的那次调用"。
    """

    role: str
    content: str
    # 协议里"角色专属"的字段：assistant 请求调工具时带 tool_calls，
    # tool 回传结果时带 tool_call_id。**只有用到的角色才该带上它们。**
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict:
        payload: dict = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        return payload


@dataclass(slots=True)
class ToolCall:
    """模型请求的一次工具调用。

    arguments 是**字符串形式的 JSON** —— 这是协议规定的形状，不是图省事。
    这里刻意**不解析**它：模型生成的东西属于"不可信输入"，
    解析、校验、白名单检查都该在执行器里做，让那条边界清清楚楚。
    """

    id: str
    name: str
    arguments: str

    def to_history(self) -> dict:
        """还原成能塞回对话历史（assistant 消息）的原始形状。"""
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(slots=True)
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    latency_ms: float
    attempts: int  # 实际发了几次请求；>1 说明发生过重试

    # 请求调工具时 content 往往是空的 —— 内容在 tool_calls 里
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(slots=True)
class StructuredResult:
    """结构化调用的结果：解析好的对象 + 原始响应 + 修复次数。

    为什么要暴露 repairs：它是一个**质量信号**。如果线上 repairs 频繁 > 0，
    说明 Prompt 或 schema 有问题（模型总在猜我们的格式），
    阶段 08 的评测会把这个指标单独拎出来看。
    """

    data: BaseModel
    response: LLMResponse
    repairs: int


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        timeout = settings.llm_timeout_seconds
        # connect 单独设一个更短的上限：连不上通常意味着网络/DNS 问题，
        # 没必要和"模型慢慢生成"用同一个 30 秒的耐心。
        self._timeout = httpx.Timeout(timeout, connect=min(10.0, timeout))
        self._transport = transport  # 测试时注入假 transport，不打真网络
        self._client: httpx.AsyncClient | None = None

    # ── 连接管理 ────────────────────────────────────────
    def _get_client(self) -> httpx.AsyncClient:
        """懒建 + 复用同一个 AsyncClient。

        为什么复用而不是每次 new 一个：
        AsyncClient 内部维护连接池。每次新建都要重做 DNS 解析和 TCP/TLS 握手
        （TLS 一来一回就是几百毫秒），而我们一次任务里可能连着调十几次模型。
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.llm_base_url.rstrip("/"),
                headers={"Authorization": f"Bearer {self._settings.llm_api_key}"},
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── 对外主方法 ──────────────────────────────────────
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        if not self._settings.llm_base_url:
            # 配置缺失是"我们自己的问题"，但要如实、可操作地告诉调用方
            raise UpstreamError(
                "未配置 LLM_BASE_URL",
                details={"hint": "复制 .env.example 为 .env，填入模型服务地址"},
            )

        payload: dict = {
            "model": model or self._settings.llm_model,
            "messages": [m.to_dict() for m in messages],
            "temperature": (
                temperature if temperature is not None else self._settings.llm_temperature
            ),
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if tools:
            # 注意：不传工具时**不要**塞一个空数组进去。
            # 有些厂商会把"存在 tools 字段"理解成"必须调用工具"，
            # 于是模型硬要调一个不存在的工具，报出一堆莫名其妙的错。
            payload["tools"] = tools

        started = time.perf_counter()
        max_retries = self._settings.llm_max_retries

        for attempt in range(1, max_retries + 1):
            # ── 网络层异常：连不上、读超时 ──
            try:
                resp = await self._get_client().post("/chat/completions", json=payload)
            except httpx.TimeoutException as exc:
                if attempt >= max_retries:
                    raise UpstreamError(
                        "模型服务响应超时",
                        details={
                            "attempts": attempt,
                            "timeout_s": self._settings.llm_timeout_seconds,
                        },
                    ) from exc
                await self._sleep_before_retry(attempt, None, reason="timeout")
                continue
            except httpx.TransportError as exc:
                # 连接被拒、TLS 失败、连接中途断开都在这一类
                if attempt >= max_retries:
                    raise UpstreamError(
                        f"模型服务不可达：{type(exc).__name__}",
                        details={"attempts": attempt},
                    ) from exc
                await self._sleep_before_retry(attempt, None, reason=type(exc).__name__)
                continue

            # ── 成功 ──
            if resp.status_code == 200:
                parsed = self._parse_response(resp, started, attempt)
                logger.info(
                    "llm_call_ok",
                    extra={
                        "event": "llm_call_ok",
                        "model": parsed.model,
                        "attempt": attempt,
                        "finish_reason": parsed.finish_reason,
                    },
                )
                return parsed

            # ── 可重试的服务端错误 ──
            if resp.status_code in RETRYABLE_STATUS:
                if attempt >= max_retries:
                    raise UpstreamError(
                        f"模型服务持续返回 {resp.status_code}",
                        details={"status": resp.status_code, "attempts": attempt},
                    )
                await self._sleep_before_retry(
                    attempt,
                    resp.headers.get("retry-after"),
                    reason=f"http_{resp.status_code}",
                )
                continue

            # ── 不可重试的客户端错误：立刻失败，别浪费重试次数 ──
            raise self._map_client_error(resp)

        # 循环必然在上面 return 或 raise，这里只是给类型检查器一个兜底
        raise UpstreamError("重试次数耗尽", details={"attempts": max_retries})

    # ── 流式输出 ────────────────────────────────────────
    async def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """流式调用：模型吐一点，我们 yield 一点。

        ⚠️ 两个**结构性限制**，不是实现偷懒：

        1. **流式不能重试。** 一旦开始往客户端吐字，重发就等于把已发出去的内容
           作废重来（客户端会看到重复）。所以重试只可能发生在"首字节之前"，
           这里干脆不重试 —— 出错就把错误当成一个事件发给客户端。
        2. **流式不能做结构化修复。** 修复的前提是"拿到完整输出再校验"，
           而流式下你已经把半截内容吐出去了，"回灌重问"在语义上不成立。

        所以两条路要分开用：需要重试和校验走 chat_structured()，
        需要低首字延迟走这里。真实产品里常见的组合是
        **意图识别用结构化、最终回答用流式** —— 本项目也是这么设计的。
        """
        if not self._settings.llm_base_url:
            raise UpstreamError(
                "未配置 LLM_BASE_URL",
                details={"hint": "复制 .env.example 为 .env，填入模型服务地址"},
            )

        payload: dict = {
            "model": model or self._settings.llm_model,
            "messages": [m.to_dict() for m in messages],
            "temperature": (
                temperature if temperature is not None else self._settings.llm_temperature
            ),
            "stream": True,
        }

        skipped = 0
        async with self._get_client().stream("POST", "/chat/completions", json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:200]
                raise UpstreamError(
                    f"模型服务返回 {resp.status_code}",
                    details={"status": resp.status_code, "body": body},
                )
            async for line in resp.aiter_lines():
                # 空行是 SSE 的**帧分隔符**、": ping" 是心跳 —— 都是协议的一部分，
                # 不算坏帧。只有"长得像 data: 却解析不出内容"的行才是。
                if not line or line.startswith(":"):
                    continue
                delta = _sse_delta(line)
                if delta is None:
                    skipped += 1
                    continue
                if delta == _SSE_DONE:
                    break
                yield delta

        if skipped:
            # 坏帧要计数、要被看见，否则"流里少了几句"这种问题永远查不出来
            logger.warning(
                "stream_frames_skipped",
                extra={"event": "stream_frames_skipped", "skipped_frames": skipped},
            )

    # ── 结构化输出 ──────────────────────────────────────
    async def chat_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[BaseModel],
        *,
        max_repairs: int | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> StructuredResult:
        """要模型返回符合 schema 的结构化结果，失败时**有界地**自我修复。

        为什么修复必须有界：每次修复都是一次真实的模型调用，花时间和钱。
        允许无限修复，就等于给了模型一个可以无限烧钱的循环 ——
        这和 Agent 必须有 max_steps 是同一个道理（阶段 05 会再撞上它）。

        为什么修复有效：不是简单重问一遍，而是把**它自己的输出**和**确切的错误**
        一起回灌给它。只说"重试一次"，模型只会原样再错一遍。
        """
        repairs_limit = self._settings.llm_max_repairs if max_repairs is None else max_repairs
        response_format = {"type": "json_object"} if self._settings.llm_use_json_mode else None
        convo = list(messages)

        for repair in range(repairs_limit + 1):
            response = await self.chat(
                convo,
                model=model,
                temperature=temperature,
                response_format=response_format,
            )
            raw = _extract_json(response.content)
            try:
                data = schema.model_validate_json(raw)
            except (ValidationError, ValueError) as exc:
                last_error = _short_error(exc)
                if repair >= repairs_limit:
                    logger.warning(
                        "structured_output_failed",
                        extra={
                            "event": "structured_output_failed",
                            "error_code": schema.__name__,
                            "repairs": repair,
                            "attempt": response.attempts,
                        },
                    )
                    raise UpstreamError(
                        "模型输出无法解析为目标结构，修复次数已用尽",
                        details={
                            "schema": schema.__name__,
                            "repairs": repair,
                            "last_error": last_error,
                            "raw": response.content[:300],
                        },
                    ) from exc
                logger.warning(
                    "structured_output_repair",
                    extra={
                        "event": "structured_output_repair",
                        "error_code": schema.__name__,
                        "repairs": repair + 1,
                    },
                )
                convo = [
                    *convo,
                    ChatMessage("assistant", response.content),
                    ChatMessage("user", _repair_instruction(last_error, schema)),
                ]
                continue
            return StructuredResult(data=data, response=response, repairs=repair)

        raise UpstreamError("结构化输出失败")  # 兜底，逻辑上走不到

    # ── 重试节奏 ────────────────────────────────────────
    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        """算下次重试前等多久。

        两条规则，顺序有讲究：
        1. 服务端明确说了 Retry-After，就听它的 —— 它比我们更清楚自己什么时候缓过来。
        2. 没说就自己退避：第 1 次等 base，第 2 次等 2x base，第 3 次 4x base……
           再乘一个 0.5~1.0 的随机抖动。抖动不是装饰：如果一百个请求同时被限流，
           不加抖动它们会在同一毫秒一起重试，把下游再打死一次（惊群效应）。
        """
        if retry_after:
            try:
                return min(float(retry_after), self._settings.llm_retry_max_seconds)
            except ValueError:
                pass  # 有些服务端返回 HTTP-date 格式，解析不了就退回指数退避
        grew = self._settings.llm_retry_base_seconds * (2 ** (attempt - 1))
        capped = min(grew, self._settings.llm_retry_max_seconds)
        return capped * (0.5 + random.random() * 0.5)

    async def _sleep_before_retry(
        self, attempt: int, retry_after: str | None, *, reason: str
    ) -> None:
        delay = self._retry_delay(attempt, retry_after)
        logger.warning(
            "llm_retry",
            extra={
                "event": "llm_retry",
                "error_code": reason,
                "attempt": attempt,
                "delay_ms": int(delay * 1000),
            },
        )
        await asyncio.sleep(delay)

    # ── 错误映射与响应解析 ──────────────────────────────
    @staticmethod
    def _map_client_error(resp: httpx.Response) -> UpstreamError:
        """把 4xx 翻译成"人能照着修的"提示。

        注意 details 里只放**截断**的响应体（前 200 字）：
        完整响应体可能很大，也可能意外带上不该进日志的内容。
        """
        snippet = resp.text[:200]
        if resp.status_code in (401, 403):
            return UpstreamError(
                "模型服务鉴权失败，请检查 LLM_API_KEY",
                details={"status": resp.status_code, "body": snippet},
            )
        if resp.status_code == 404:
            return UpstreamError(
                "接口路径不存在，请检查 LLM_BASE_URL 是否带 /v1",
                details={"status": resp.status_code, "body": snippet},
            )
        return UpstreamError(
            f"模型服务返回错误 {resp.status_code}",
            details={"status": resp.status_code, "body": snippet},
        )

    @staticmethod
    def _parse_response(resp: httpx.Response, started: float, attempt: int) -> LLMResponse:
        """按 OpenAI 兼容协议解析。

        这里特意**不信任**上游结构：即使状态码是 200、即使对端自称兼容，
        字段也可能缺失或换名字。上游格式变了要把错误说清楚，
        而不是丢一个 KeyError 让调用方莫名其妙。
        """
        try:
            data = resp.json()
            choice = data["choices"][0]
            message = choice["message"]
            # content 可能是 null：请求调工具时它就是空的。
            # 这不是"格式错误"，是协议允许的正常情况。
            content = message.get("content") or ""
            usage = data.get("usage") or {}
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise UpstreamError(
                "模型响应格式不符合 OpenAI 兼容协议（缺 choices[0].message.content）",
                details={"body": resp.text[:200]},
            ) from exc

        tool_calls = [
            ToolCall(
                id=call.get("id", ""),
                name=(call.get("function") or {}).get("name", ""),
                arguments=(call.get("function") or {}).get("arguments", "") or "",
            )
            for call in (message.get("tool_calls") or [])
        ]

        return LLMResponse(
            content=content,
            model=data.get("model", ""),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", ""),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            attempts=attempt,
            tool_calls=tool_calls,
        )


# ── 流式输出的辅助（模块级）──────────────────────────────
_SSE_DONE = "[DONE]"


def _sse_delta(line: str) -> str | None:
    """把一行 SSE 解析成文本增量；返回 None 表示"这一行没有内容，跳过"。

    帧格式是 `data: {...}`，帧与帧之间用空行分隔，终止哨兵是 `data: [DONE]`。

    三种"不是错误但也不是内容"的情况，都要能跳过去而不是崩掉：
      · 空行 / 注释行（有些服务端会发 `: ping` 心跳）
      · **半截 JSON** —— 网络分片可能把一帧切开，aiter_lines 只保证"行"边界，
        不保证"帧"边界
      · 合法但没有内容的帧（比如只带 role、或只带 usage 的那种）

    让一个坏帧毁掉整条流，是流式实现里最常见的错误。
    """
    if not line or not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if not data:
        return None
    if data == _SSE_DONE:
        return _SSE_DONE
    try:
        chunk = json.loads(data)
        delta = chunk["choices"][0].get("delta") or {}
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    content = delta.get("content")
    return content if content else None


# ── 结构化输出的辅助函数（模块级：它们不依赖任何实例状态，纯输入输出）──


def _extract_json(text: str) -> str:
    """从模型输出里抠出 JSON 本体。

    模型极爱把 JSON 包在 ```json 围栏里，或者前面加一句"好的，这是结果："。
    直接 json.loads(原文) 会失败，然后你会误判成"这个模型不会输出 JSON"，
    去换模型、改 Prompt，折腾半天 —— 其实问题只在几个字符的清洗上。
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        newline = cleaned.find("\n")
        if newline != -1:
            cleaned = cleaned[newline + 1 :]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    return cleaned.strip()


def _short_error(exc: Exception) -> str:
    """把校验错误压成一句话。

    别把整个 pydantic 错误树甩给模型：又长又绕，模型反而抓不住重点。
    """
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:5]:
            loc = "/".join(str(p) for p in err["loc"])
            # pydantic 的 msg 里**不含**违规的那个值（只会说"Input should be ..."），
            # 实际值在 err["input"] 里。不把它带上，修复指令就是在说废话 ——
            # 模型没法知道"我到底写错成了什么"。
            got = repr(err.get("input"))[:40]
            parts.append(f"{loc}: {err['msg']}（实际写的是 {got}）")
        return "; ".join(parts)
    return str(exc)[:200]


def _repair_instruction(last_error: str, schema: type[BaseModel]) -> str:
    """构造修复指令。

    关键点：把 **JSON Schema 本身**贴给模型。schema 是机器可读的契约，
    贴上去比用自然语言复述一遍字段要求精确得多 —— 模型不用猜。
    """
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return (
        "你上一次的输出不是合法的目标 JSON，解析失败。具体错误：\n"
        f"{last_error}\n\n"
        "请**只输出一个 JSON 对象**，不要任何解释文字，不要 Markdown 代码围栏。\n"
        f"它必须满足这个 JSON Schema：\n{schema_json}"
    )
