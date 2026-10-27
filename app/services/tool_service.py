"""工具执行器：模型说"调这个工具"，这里把它变成一次**受控的**执行。

模型给出的东西是**不可信输入**（本质上是"用户输入经过模型转述"），所以要过四道关，
每一道都对应一类真实存在、且很常见的模型犯错方式：

  1. 白名单  —— 工具名可能根本不存在（模型会"发明"工具名，尤其是在它记不清的时候）
  2. 解析    —— arguments 是字符串形式的 JSON，可能压根不是合法 JSON
  3. 校验    —— 是 JSON 也不等于参数对：少给必填、给了格式不符的值、把数字写成字符串
  4. 超时    —— 工具可能是网络调用，没有超时就会把一个请求挂死

关键设计：**四道关中任何一道没过，都返回 ToolResult(ok=False)，并把原因讲给模型听。**
不是抛异常、更不是让整个请求 500。原因是模型需要知道"我哪儿做错了"，
它才可能换参数重试、换个工具、或者如实告诉用户做不到。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from pydantic import ValidationError

from app.core.config import Settings
from app.llm.client import ToolCall
from app.tools.base import ToolError, ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolRunner:
    def __init__(self, registry: ToolRegistry, settings: Settings) -> None:
        self._registry = registry
        self._settings = settings

    async def run(self, call: ToolCall) -> ToolResult:
        started = time.perf_counter()

        # ── 第 1 关：白名单 ──
        tool = self._registry.get(call.name)
        if tool is None:
            # 只说"工具不存在"没用 —— 模型猜不出正确名字，会再犯同样的错。
            # 把可用清单给它，它下一轮才有可能选对。
            available = ", ".join(self._registry.names())
            return self._fail(
                "unknown_tool",
                f"没有名为「{call.name}」的工具。可用工具：{available}",
                started,
                tool_name=call.name,
            )

        # ── 第 2 关：解析 arguments ──
        try:
            raw = json.loads(call.arguments or "{}")
        except ValueError as exc:
            return self._fail(
                "invalid_arguments_json",
                f"arguments 不是合法 JSON（{exc}）。收到的原文：{call.arguments[:120]}",
                started,
                tool_name=tool.name,
            )
        if not isinstance(raw, dict):
            return self._fail(
                "invalid_arguments_json",
                f"arguments 必须是 JSON 对象，收到的是 {type(raw).__name__}",
                started,
                tool_name=tool.name,
            )

        # ── 第 3 关：参数校验 ──
        if tool.args_model is None:  # pragma: no cover - 注册时已保证
            return self._fail("tool_misconfigured", "工具没有定义参数模型", started)
        try:
            args = tool.args_model.model_validate(raw)
        except ValidationError as exc:
            return self._fail(
                "invalid_arguments",
                f"参数校验失败：{_short_validation_error(exc)}",
                started,
                tool_name=tool.name,
            )

        # ── 第 4 关：带超时地执行 ──
        timeout = self._settings.tool_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                result = await tool.run(args)
        except TimeoutError:
            return self._fail(
                "tool_timeout",
                f"工具「{tool.name}」超过 {timeout:g} 秒没有返回",
                started,
                tool_name=tool.name,
            )
        except ToolError as exc:
            # 工具自己声明的失败 —— 它是有话要说的，原样传给模型
            return self._fail(exc.code, exc.message, started, tool_name=tool.name)
        except Exception:
            # 兜底：工具的 bug 绝不能让整条链路崩掉。
            # 这里 logger.exception 记完整堆栈（给我们看），但给模型的只有一句话
            # （不给它看堆栈 —— 既占 token，也可能泄漏内部结构）。
            logger.exception(
                "tool_crashed",
                extra={"event": "tool_crashed", "tool_name": tool.name},
            )
            return self._fail(
                "tool_crashed",
                f"工具「{tool.name}」内部错误，已记录",
                started,
                tool_name=tool.name,
            )

        result.duration_ms = _elapsed_ms(started)
        logger.info(
            "tool_called",
            extra={
                "event": "tool_called",
                "tool_name": tool.name,
                "tool_call_id": call.id,
                "tool_ok": result.ok,
            },
        )
        return result

    def _fail(
        self,
        code: str,
        message: str,
        started: float,
        *,
        tool_name: str | None = None,
    ) -> ToolResult:
        logger.warning(
            "tool_failed",
            extra={
                "event": "tool_failed",
                "error_code": code,
                "tool_name": tool_name,
                "tool_ok": False,
            },
        )
        return ToolResult(
            ok=False,
            content=f"[工具调用失败] {message}",
            error_code=code,
            duration_ms=_elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _short_validation_error(exc: ValidationError) -> str:
    """和结构化输出那边同一个道理：pydantic 的 msg 里不含违规值，要补上 err['input']。"""
    parts = []
    for err in exc.errors()[:3]:
        loc = "/".join(str(p) for p in err["loc"]) or "arguments"
        got = repr(err.get("input"))[:40]
        parts.append(f"{loc}: {err['msg']}（实际给的是 {got}）")
    return "; ".join(parts)
