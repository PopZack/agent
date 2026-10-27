"""阶段 02a 实跑演示：用模拟模型服务验证模型接入层。

为什么不在 /chat 里演示：02a 只做"模型接入层"这一层，
/chat 还没接上它（那是 02b 的事）。这里直接驱动这一层，
把正常 / 限流重试 / 认证失败 / 格式漂移四种情况各跑一遍。

跑法（需要先起模拟服务）：
    终端 1: python scripts/mock_llm_server.py
    终端 2: python scripts/demo_llm_layer.py
"""

import asyncio
import json

from app.core.config import Settings
from app.core.exceptions import UpstreamError
from app.core.logging import setup_logging
from app.llm.client import ChatMessage, LLMClient

CASES = [
    ("① 正常调用", "帮我查一下公司的差旅报销制度，并告诉我住宿标准。"),
    ("② 限流两次后成功（看重试日志）", "[[FLAKY_429_2]] 查询本月各部门费用情况"),
    ("③ 认证失败（不该重试，一次就放弃）", "[[AUTH]] 随便问一句"),
    ("④ 上游格式漂移（要讲清是什么坏了）", "[[BADBODY]] 随便问一句"),
    ("⑤ 持续限流（重试耗尽）", "[[ALWAYS_429]] 随便问一句"),
]


async def main() -> None:
    setup_logging(level="INFO")
    settings = Settings(
        llm_base_url="http://127.0.0.1:8200/v1",
        llm_api_key="mock-key",
        llm_model="mock-model",
        # 把退避压到很短，否则演示要等几秒
        llm_retry_base_seconds=0.05,
        llm_retry_max_seconds=0.1,
        llm_max_retries=3,
    )
    client = LLMClient(settings)
    try:
        for label, text in CASES:
            print("\n" + "=" * 64)
            print(f"{label}")
            print(f"  输入: {text}")
            try:
                resp = await client.chat([ChatMessage("user", text)])
                print(f"  ✅ content      = {resp.content!r}")
                print(
                    f"     尝试 {resp.attempts} 次 | 耗时 {resp.latency_ms}ms | "
                    f"token {resp.total_tokens} | finish={resp.finish_reason}"
                )
            except UpstreamError as exc:
                print(f"  ❌ {exc.code}: {exc.message}")
                print(f"     details = {json.dumps(exc.details, ensure_ascii=False)[:150]}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
