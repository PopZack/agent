"""接口层验收测试（阶段 01 的链路 + 阶段 02b-1 的意图识别）。

关于"改测试"这件事，这一版正好是个例子：它**改掉了阶段 01 的两条断言** ——
`stage` 从 "01-skeleton" 变成 "02-intent"、`model` 从 None 变成有值。
这是正当的，因为**响应契约在 02b-1 有意演进了**（新增了 intent 等字段）。

判据很简单，值得记住：
  · 代码行为变了、契约跟着变 → 改测试是对的，测试描述的就是新契约；
  · 代码行为没变、只是测试红了 → 改测试是作弊，你在抹掉证据。
第二种情况下要改的是代码，不是测试。
"""

import logging

from fastapi.testclient import TestClient

from app.api.deps import get_chat_service
from app.core.exceptions import InvalidRequestError
from app.core.logging import RequestIdFilter
from tests.fakes import chat_service_with, intent_client, text_client


def use_llm(app, llm):
    """把链路最外层的 ChatService 换成"网络是假的、其余全真"的版本。

    为什么覆盖 get_chat_service 而不是 get_llm_client ——
    见 tests/fakes.py 里 chat_service_with 的说明（FastAPI 的依赖覆盖只覆盖
    它自己解析的那一层，内部直接函数调用绕不过去）。
    """
    app.dependency_overrides[get_chat_service] = lambda: chat_service_with(llm)
    return TestClient(app)


# ── 1. 服务活着 ────────────────────────────────────────
def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── 2. 意图识别跑通：结构化结果真的进了响应 ──────────────────
def test_chat_returns_structured_intent(app):
    client = use_llm(app, intent_client("knowledge_query", 0.91))

    resp = client.post("/chat", json={"message": "帮我查一下公司的差旅报销制度"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "knowledge_query"
    assert body["confidence"] == 0.91
    assert body["reason"]
    assert body["repairs"] == 0
    assert body["total_tokens"] > 0  # token 统计从这一阶段起有了
    assert body["stage"] == "02b-2-tools"
    assert body["model"] == "mock-model"
    assert body["request_id"]


# ── 3. 意图真的被用来分流了，不是一个装饰性的字段 ──────────────
def test_intent_changes_the_answer(app):
    chatty = (
        use_llm(app, intent_client("chitchat", 0.95, answer="你好，我是企业智能助手，有事请说。"))
        .post("/chat", json={"message": "你好"})
        .json()["answer"]
    )

    knowledge = (
        use_llm(app, intent_client("knowledge_query", 0.95, answer="关于差旅制度：见阶段 04。"))
        .post("/chat", json={"message": "差旅报销标准是多少"})
        .json()["answer"]
    )

    assert "企业智能助手" in chatty  # 闲聊的回答来自回答轮
    assert "阶段 04" in knowledge  # 知识查询的回答同样来自回答轮，但内容不同
    assert chatty != knowledge  # 意图不同 → 回答不同，这才是本测试的真正断言


# ── 4. 置信度低于门槛 → 转为向用户澄清，而不是硬猜 ────────────
def test_low_confidence_turns_into_clarification(app):
    client = use_llm(app, intent_client("out_of_scope", 0.55))

    body = client.post("/chat", json={"message": "随便问一句"}).json()

    assert "不太确定" in body["answer"]
    assert body["confidence"] == 0.55  # 判不准也要如实报告，别藏起来


# ── 5. 模型不守规矩时，对外是 502，不是 500 ─────────────────
def test_model_misbehaving_becomes_502(app):
    """修复次数用尽 → UpstreamError → 502。

    这个区分很重要：**502 说"别人的错"，500 说"我们的错"**。
    把上游故障报成 500，会让运维半夜爬起来查自己的代码。
    """
    client = use_llm(app, text_client("这不是 JSON，我不听你的"))  # 永远给散文

    resp = client.post("/chat", json={"message": "帮我查制度"})

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["code"] == "upstream_error"
    assert "修复次数已用尽" in body["error"]["message"]
    assert body["error"]["details"]["schema"] == "IntentResult"


# ── 6. request_id 契约没变（阶段 01 建立的，02b-1 不能破坏它）──
def test_request_id_is_honoured_and_echoed(app):
    client = use_llm(app, intent_client())

    resp = client.post(
        "/chat",
        json={"message": "hi"},
        headers={"X-Request-ID": "trace-from-gateway-001"},
    )

    assert resp.headers["X-Request-ID"] == "trace-from-gateway-001"
    assert resp.json()["request_id"] == "trace-from-gateway-001"


def test_request_id_is_generated_when_absent(app):
    client = use_llm(app, intent_client())
    resp = client.post("/chat", json={"message": "hi"})
    assert len(resp.headers["X-Request-ID"]) == 12


# ── 7. 参数校验：错误体形状与阶段 01 完全一致 ─────────────────
def test_empty_message_rejected_with_stable_error_body(client):
    resp = client.post("/chat", json={"message": ""})

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "invalid_request"
    fields = body["error"]["details"]["fields"]
    assert fields and fields[0]["loc"] == ["body", "message"]


# ── 8. 业务异常被翻译成对应状态码 ────────────────────────────
class _RejectingService:
    async def answer(self, payload):
        raise InvalidRequestError("问题超长", details={"limit": 4000})


def test_app_error_translated(app):
    app.dependency_overrides[get_chat_service] = lambda: _RejectingService()
    resp = TestClient(app).post("/chat", json={"message": "hi"})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"
    assert resp.json()["error"]["details"] == {"limit": 4000}


# ── 9. 未捕获异常：对外只有一句话，堆栈不外泄 ────────────────
class _CrashingService:
    async def answer(self, payload):
        raise RuntimeError("数据库密码是 xxx —— 这种内容绝不能被客户端看到")


def test_unhandled_error_hides_internals(app):
    app.dependency_overrides[get_chat_service] = lambda: _CrashingService()
    resp = TestClient(app, raise_server_exceptions=False).post("/chat", json={"message": "hi"})

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"

    raw = resp.text
    assert "数据库密码" not in raw
    assert "RuntimeError" not in raw
    assert "Traceback" not in raw


# ── 10. 最关键的一条：异常路径上 request_id 也不断链 ──────────
class _CapturingHandler(logging.Handler):
    """把日志抓下来，并挂上和线上一样的那只 Filter。"""

    def __init__(self):
        super().__init__()
        self.records = []
        self.addFilter(RequestIdFilter())

    def emit(self, record):
        self.records.append(record)


def test_request_id_survives_unhandled_error(app):
    """验证 middleware.py 里"异常路径故意不 reset token"那个决定的正确性。

    如果那段写成 reset，这条测试就会红 —— 因为外层 ServerErrorMiddleware
    的兜底 handler 打日志时，ContextVar 已经被清空了。
    """
    app.dependency_overrides[get_chat_service] = lambda: _CrashingService()

    handler = _CapturingHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        TestClient(app, raise_server_exceptions=False).post(
            "/chat",
            json={"message": "hi"},
            headers={"X-Request-ID": "trace-broken-task"},
        )
    finally:
        root.removeHandler(handler)

    fallback = [r for r in handler.records if r.getMessage() == "unhandled_error"]
    assert fallback, "兜底 handler 没有记录日志"
    assert fallback[0].request_id == "trace-broken-task"
