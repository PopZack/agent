"""跨层横切的请求上下文。

request_id 是"一次请求"的唯一标识，它需要在
中间件 → 路由 → service → 未来阶段的 Agent/工具/检索/模型调用
之间被共享，但**不应该**作为参数一层层往下传 —— 那会污染每一层的函数签名。

ContextVar 解决的正是这个问题：
  - 读的时候直接 context.get()，不用传参；
  - 写的时候只影响"当前执行上下文"，asyncio 里每个请求跑在独立的 Task 中，
    每个 Task 有自己的一份 context 拷贝，所以并发请求之间不会串号。

这也是为什么不能用普通全局变量：全局变量在多请求并发时会被互相覆盖。
"""

from contextvars import ContextVar

# 默认值 "-" 表示"没有请求上下文"，例如启动阶段、后台任务里打日志时
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
