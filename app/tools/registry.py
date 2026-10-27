"""工具注册表。

为什么要有这一层，而不是在 service 里直接写个 dict：
  1. **唯一的注册点**。阶段 06 要从 MCP Server 动态发现工具，塞进来的地方就是这里；
  2. **白名单能力**。不能让模型看见全部工具 —— 见下面 schema(names=...) 的说明；
  3. **重名检查**。工具名是模型选择的唯一键，重名会导致静默地调用到"另一个"工具，
     这种 bug 极难查（代码看起来完全正常，行为却不对）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.tools.base import Tool


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError(f"{type(tool).__name__} 没有 name，无法注册")
        if tool.name in self._tools:
            # 宁可启动时炸，也不要运行时悄悄用错工具
            raise ValueError(f"工具名重复：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        # 注意返回 None 而不是抛异常：工具名是**模型生成的**，
        # "查不到"是完全正常的流量，不是程序错误。
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schema(self, names: Iterable[str] | None = None) -> list[dict]:
        """生成给模型的 tools 参数。

        names 是**工具白名单**：只暴露该场景下允许用的工具。

        为什么白名单比"全给"更好：
          · 模型的选择空间小，选错的概率就小（这是最直接的准确率提升）；
          · 缩小 prompt，省 token；
          · 权限控制的第一道闸 —— 阶段 07 会在此基础上叠加
            "用户有没有权限用这个工具"，那是**另一个维度**（能不能用 vs 该不该给看）。
        """
        if names is None:
            selected = list(self._tools.values())
        else:
            selected = [self._tools[n] for n in names if n in self._tools]
        return [tool.to_openai_schema() for tool in selected]
