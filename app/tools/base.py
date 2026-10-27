"""工具系统的地基：一个工具长什么样，以及怎么把它的参数契约交给模型。

这里最重要的一个概念，先写在最前面：

    工具失败 ≠ 请求失败。

工具报错（参数不对、下游超时、数据不存在）时，正确做法**不是**让整个请求 500，
而是把失败作为一条"观察结果"回给模型，让它自己决定怎么办 ——
换个参数重试、换个工具、还是告诉用户"我做不到"。

这是"程序"和"Agent"的分界线：程序遇到错误就中断，Agent 遇到错误要能调整。
所以工具自己抛 ToolError，而执行器负责把它翻译成给模型看的内容。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass(slots=True)
class ToolResult:
    """工具执行的结果。

    content 是**给模型看的**（进上下文、花 token），所以它要紧凑、无歧义；
    不是你内部日志想怎么写就怎么写。
    """

    ok: bool
    content: str
    error_code: str | None = None
    duration_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    """工具自身的失败。

    注意它和 AppError 的区别：AppError 会一路冒到 api 层变成 HTTP 状态码；
    ToolError 会在执行器里被**就地拦下**，转成 ToolResult(ok=False) 回给模型。
    """

    code = "tool_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.message = message
        if code:
            self.code = code
        super().__init__(message)


class Tool(ABC):
    """所有工具的基类。

    三个必须提供的属性，各自解决一个具体问题：
      · name        —— 模型用它来"选择"工具。重名是最危险的 bug（见 registry）
      · description —— 这是**写给模型看的 prompt**，不是写给同事看的注释。
                        描述写得含糊，模型就不选它或者选错它 ——
                        JD 里"设计工具描述、调用条件"讲的就是这个。
      · args_model  —— Pydantic 模型，参数的**唯一事实来源**：
                       既做运行时校验，又自动生成给模型的 JSON Schema。
                        手写一份 JSON Schema 一定会和代码漂移。
    """

    name: str = ""
    description: str = ""
    # 子类必须覆盖；不覆盖会在 to_openai_schema() 里得到明确的报错，而不是神秘的 AttributeError
    args_model: type[BaseModel] | None = None

    @abstractmethod
    async def run(self, args: BaseModel) -> ToolResult:
        """执行。只在**真正的失败**时抛 ToolError；正常返回（包括"查无数据"）走 ToolResult。"""

    def to_openai_schema(self) -> dict:
        if not self.name or not self.description or self.args_model is None:
            raise NotImplementedError(
                f"工具 {type(self).__name__} 没有定义完整的 name / description / args_model"
            )
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                # 从 Pydantic 自动生成：改参数只需改模型，schema 不可能忘记同步
                "parameters": _strip_titles(self.args_model.model_json_schema()),
            },
        }


def _strip_titles(node: Any) -> Any:
    """递归删掉 JSON Schema 里的 title 字段。

    为什么值得专门写个函数：pydantic 生成 schema 时会满地撒 "title"
    （每个字段一个、嵌套模型再来一个），这些是给**文档**看的，
    模型完全不需要 —— 而它们实打实地占 prompt token，每次调用都要付钱。
    这是最省事的一种 token 优化：不改变语义，纯赚。
    """
    if isinstance(node, dict):
        return {key: _strip_titles(value) for key, value in node.items() if key != "title"}
    if isinstance(node, list):
        return [_strip_titles(item) for item in node]
    return node


def now_ms() -> float:
    return time.perf_counter() * 1000
