"""阶段 01 的验收测试。

这些断言对应 项目.txt 里阶段 01 的交付标准：
「一个可以接收问题、返回 JSON、记录请求日志的后端服务」
"""

import logging

from fastapi.testclient import TestClient

from app.api.deps import get_chat_service
from app.core.exceptions import InvalidRequestError
from app.core.logging import RequestIdFilter


# ── 1. 服务活着 ────────────────────────────────────────
def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── 2. 能接收问题、返回 JSON ────────────────────────────
def test_chat_returns_json_answer(client):
    resp = client.post("/chat", json={"message": "公司差旅住宿标准是多少？"})
    assert resp.status_code == 200

    body = resp.json()
    assert "公司差旅住宿标准" in body["answer"]  # 问题被接住了
    assert body["request_id"]  # 链路标识盖了章
    assert body["stage"] == "01-skeleton"
    assert body["model"] is None  # 阶段 01 还没接模型


# ── 3. request_id 能被外部沿用，让跨服务 trace 串起来 ──
def test_request_id_is_honoured_and_echoed(client):
    resp = client.post(
        "/chat",
        json={"message": "hi"},
        headers={"X-Request-ID": "trace-from-gateway-001"},
    )
    assert resp.headers["X-Request-ID"] == "trace-from-gateway-001"
    assert resp.json()["request_id"] == "trace-from-gateway-001"


def test_request_id_is_generated_when_absent(client):
    resp = client.post("/chat", json={"message": "hi"})
    assert len(resp.headers["X-Request-ID"]) == 12  # uuid4().hex[:12]


# ── 4. 参数校验的错误体是统一形状，且不会炸在序列化上 ──
def test_empty_message_rejected_with_stable_error_body(client):
    resp = client.post("/chat", json={"message": ""})
    assert resp.status_code == 422

    body = resp.json()
    # 前端只认这一种形状，且只靠 code 分支
    assert body["error"]["code"] == "invalid_request"
    fields = body["error"]["details"]["fields"]
    assert fields and fields[0]["loc"] == ["body", "message"]


# ── 5. 业务异常被翻译成对应状态码，而不是 500 ──────────
class _RejectingService:
    async def answer(self, payload):
        raise InvalidRequestError("问题超长", details={"limit": 4000})


def test_app_error_translated(app):
    app.dependency_overrides[get_chat_service] = lambda: _RejectingService()
    resp = TestClient(app).post("/chat", json={"message": "hi"})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"
    assert resp.json()["error"]["details"] == {"limit": 4000}


# ── 6. 未捕获异常：对外只有一句"服务内部错误"，堆栈不外泄 ──
class _CrashingService:
    async def answer(self, payload):
        raise RuntimeError("数据库密码是 xxx —— 这种内容绝不能被客户端看到")


def test_unhandled_error_hides_internals(app):
    app.dependency_overrides[get_chat_service] = lambda: _CrashingService()
    # raise_server_exceptions=False：让 TestClient 不要把异常再抛回给测试
    resp = TestClient(app, raise_server_exceptions=False).post("/chat", json={"message": "hi"})

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"

    raw = resp.text
    assert "数据库密码" not in raw  # 异常原文没泄漏
    assert "RuntimeError" not in raw  # 异常类型名也没泄漏
    assert "Traceback" not in raw  # 堆栈没泄漏


# ── 7. 最关键的一条：异常路径上 request_id 也不断链 ──────
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

    # 核心断言：兜底日志仍然带着这次请求的 request_id，链路没断
    assert fallback[0].request_id == "trace-broken-task"
