"""测试用的假件（fakes）。

单独一个模块，而不是塞进 conftest.py：
conftest 的定位是"夹具注册"，把它当工具箱到处 import，
会让读代码的人分不清哪些是自动生效的夹具、哪些是普通函数。
"""

import json

import httpx

from app.core.config import Settings
from app.llm.client import LLMClient
from app.services.chat_service import ChatService
from app.services.intent_service import IntentService
from app.services.tool_service import ToolRunner
from app.tools.business import build_default_tools
from app.tools.registry import ToolRegistry


def make_settings(**overrides) -> Settings:
    """测试配置：退避压到毫秒级。

    测试不该真的睡几秒 —— 否则一套测试跑下来要几分钟，
    而"真的等了几秒"对验证逻辑毫无帮助。
    """
    base = {
        "llm_base_url": "http://mock.local/v1",
        "llm_api_key": "test-key",
        "llm_model": "mock-model",
        "llm_temperature": 0.0,
        "llm_timeout_seconds": 2.0,
        "llm_max_retries": 3,
        "llm_retry_base_seconds": 0.001,
        "llm_retry_max_seconds": 0.01,
    }
    base.update(overrides)
    return Settings(**base)


def ok_body(
    content: str = "好的",
    *,
    prompt_tokens: int = 11,
    completion_tokens: int = 22,
    model: str = "mock-model",
) -> dict:
    """一个合法的 OpenAI 兼容响应体。"""
    return {
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def make_client(handler, **overrides) -> LLMClient:
    """用假 transport 造一个真客户端：**只有网络是假的，其余代码全真**。"""
    return LLMClient(make_settings(**overrides), transport=httpx.MockTransport(handler))


def text_client(content: str, *, calls: list[int] | None = None, **overrides) -> LLMClient:
    """永远返回同一段文本的假模型。"""
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if calls is not None:
            calls.append(counter["n"])
        return httpx.Response(200, json=ok_body(content))

    return make_client(handler, **overrides)


def scripted_client(
    contents: list[str], *, calls: list[int] | None = None, **overrides
) -> LLMClient:
    """按顺序返回多段文本的假模型；用完后一直返回最后一段。

    为什么要"脚本化"：修复路径必须验证"第一次给错的、第二次给对的"，
    一个固定回复做不到这件事。
    """
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(counter["n"], len(contents) - 1)
        counter["n"] += 1
        if calls is not None:
            calls.append(counter["n"])
        return httpx.Response(200, json=ok_body(contents[index]))

    return make_client(handler, **overrides)


def tool_call_json(name: str, arguments: dict | str) -> str:
    """构造一个 tool_calls 响应体（给 scripted_client 用）。"""
    args = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return json.dumps(
        {
            "model": "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call_{name}",
                                "type": "function",
                                "function": {"name": name, "arguments": args},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
        }
    )


def intent_json(
    intent: str = "knowledge_query",
    confidence: float = 0.91,
    reason: str = "测试用判断依据",
    slots: dict | None = None,
) -> str:
    return json.dumps(
        {"intent": intent, "confidence": confidence, "reason": reason, "slots": slots or {}},
        ensure_ascii=False,
    )


def intent_client(
    intent: str = "knowledge_query",
    confidence: float = 0.91,
    *,
    answer: str = "（mock 回答）这是回答轮的输出。",
    **overrides,
) -> LLMClient:
    """按调用类型分流的假模型。

    真实链路里，意图轮带 response_format=json_object，回答轮不带。
    假件必须按这个特征分流 —— 否则回答轮会把意图 JSON 当回答吐回来
    （这正是本函数第一版踩过的坑：闲聊的回答变成了一段原始 JSON）。

    分流依据是**请求自身的特征**，不是调用顺序：顺序会随实现微调，
    特征是协议层面的、稳定的。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if (payload.get("response_format") or {}).get("type") == "json_object":
            return httpx.Response(200, json=ok_body(intent_json(intent, confidence)))
        return httpx.Response(200, json=ok_body(answer))

    return make_client(handler, **overrides)


def chat_service_with(
    llm: LLMClient,
    settings: Settings | None = None,
    *,
    tools: list | None = None,
) -> ChatService:
    """组装一条"只有网络是假的、其余代码全真"的链路。

    ⚠️ 这里为什么不直接 override get_llm_client —— 这是个真实的坑：

    FastAPI 的 dependency_overrides **只对它自己解析的依赖生效**，
    也就是只覆盖路由函数签名里那些 `Depends(...)`。
    而 `get_intent_service()` 内部是**直接调用** `get_llm_client()` 的普通 Python 调用，
    不经过 FastAPI 的解析流程。所以你覆盖 get_llm_client，
    get_intent_service 里拿到的**仍然是真客户端** —— 覆盖看起来"没生效"，却没有任何报错。

    要换掉链路上某一层，只有两条路：
      1. 覆盖最外层被 Depends 解析的那个（这里是 get_chat_service）—— 本函数走这条；
      2. 把内部调用也改成依赖注入，让每一层都由 FastAPI 解析。
    覆盖一个点，链路其余部分保持真实，是性价比最高的做法。
    """
    settings = settings or make_settings()
    registry = ToolRegistry(tools if tools is not None else build_default_tools())
    return ChatService(
        IntentService(llm, settings),
        llm,
        registry,
        ToolRunner(registry, settings),
        settings,
    )
