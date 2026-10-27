"""日志：让每一条日志都自带 request_id，并支持结构化输出。

这里只用标准库 logging，不用 loguru —— 目的是把机制本身讲清楚：
logging 的三大件是 Logger（记录）→ Filter（加工）→ Formatter（成文）。
我们需要的两件事各自落在哪一件上：

  · "每条日志都带上 request_id"  →  Filter 的职责（往 record 上挂字段）
  · "输出成一行 JSON"            →  Formatter 的职责

搞清这个分工，以后换 loguru 或接 OpenTelemetry 时，就知道该在哪里插。
"""

import json
import logging
import sys
import time

from app.core.context import request_id_var

# 这些 extra 字段会被 JSON Formatter 提取出来作为结构化字段。
# 阶段 08 的 Tracing 会把 trace_id / span_id / token 用量也加进这个名单。
STRUCTURED_KEYS = (
    "event",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "error_code",
    "session_id",
    "task_id",
    "tool_name",
    "model",
    "attempt",
    "delay_ms",
    "finish_reason",
    "intent",
    "confidence",
    "repairs",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
)


class RequestIdFilter(logging.Filter):
    """把当前上下文的 request_id 挂到每条 record 上。

    写成 Filter 而不是在每个 logger.info 里手工传，是因为：
    一旦漏传，那条日志就断链了 —— 日志的价值恰恰在于"不会漏"。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True  # 返回 False 会丢弃这条日志


class TextFormatter(logging.Formatter):
    """人类可读格式，本地开发用。"""

    def format(self, record: logging.LogRecord) -> str:
        head = (
            f"{time.strftime('%H:%M:%S', time.localtime(record.created))} "
            f"{record.levelname:<7} "
            f"[{getattr(record, 'request_id', '-')}] "
            f"{record.name}: {record.getMessage()}"
        )
        extras = {k: getattr(record, k) for k in STRUCTURED_KEYS if hasattr(record, k)}
        if extras:
            head += " | " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            head += "\n" + self.formatException(record.exc_info)
        return head


class JsonFormatter(logging.Formatter):
    """单行 JSON，被采集/检索时用。ensure_ascii=False 让中文保持可读，
    而不是变成 \u4e2d\u6587 这种没法 grep 的东西。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        for key in STRUCTURED_KEYS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# 给自己装的 handler 打个标记，方便只摘掉"自己上次装的那个"
_OWN_HANDLER_FLAG = "_enterprise_agent_handler"


def setup_logging(level: str = "INFO", as_json: bool = False) -> None:
    root = logging.getLogger()

    # 自我纠偏记录：这里最初写的是 root.handlers.clear()，很暴力。
    # 它会把别人挂上来的 handler 一起干掉 —— pytest 的 caplog、
    # 生产里的日志采集 agent、APM 探针，全都挂 root。
    # 所以改成"只移除我自己上次装的那一个"，别人的一律不动。
    for existing in list(root.handlers):
        if getattr(existing, _OWN_HANDLER_FLAG, False):
            root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else TextFormatter())
    handler.addFilter(RequestIdFilter())
    setattr(handler, _OWN_HANDLER_FLAG, True)  # 打标记，下次好认

    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn 自带的访问日志没有 request_id、也没有耗时，和我们的访问日志重复。
    # 关掉它，统一走 RequestContextMiddleware 里那条 —— 一份日志只有一个来源。
    logging.getLogger("uvicorn.access").disabled = True
