"""意图识别的输出契约。

这个文件回答一个问题：**我们怎么让模型吐出一份程序能可靠消费的东西。**

三个设计决定，每个都有代价，逐个说：

1. `intent` 用 Enum 而不是自由字符串。
   如果写 `intent: str`，模型总有一天会发明一个新类别（"refund_inquiry"），
   你的 if/elif 分支就会静默走进 else 分支，而且**不会有任何报错**。
   用 Enum，非法值会在校验时直接暴露出来。

2. `extra="ignore"` 而不是 `extra="forbid"`。
   模型经常"顺手多给一个字段"（比如加个 "note"）。用 forbid，这种无害的多余字段
   会触发一次修复重试 —— 白花一次模型调用的钱。这里选择**对冗余宽容、对语义严格**：
   多余的字段丢掉，但 intent 必须是合法的、confidence 必须在 0~1。

3. confidence 用 field_validator 做"宽进严出"。
   模型有时把 0.9 写成 90（当成百分比）。与其为这个浪费一次修复调用，
   不如在入口就地归一化 —— 这是"宽容解析"模式：**接受多种合理写法，产出唯一规范形式**。
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Intent(StrEnum):
    """只能选一个，不要自创类别。

    用 StrEnum（Python 3.11+）：成员本身就是 str，JSON 里就是 "knowledge_query"。
    老写法 `class Intent(str, Enum)` 也能跑，但 str()、格式化、JSON 序列化
    三处行为不一致（str(Intent.X) 会给出 "Intent.X"），是个专坑人的历史遗留 ——
    StrEnum 把这三者统一了。ruff 的 UP042 报的就是这件事，顺着改是对的。
    """

    KNOWLEDGE_QUERY = "knowledge_query"  # 查公司制度、流程、政策
    DATA_ANALYSIS = "data_analysis"  # 查/算业务数据、找异常
    TASK_EXECUTION = "task_execution"  # 生成或执行一个具体动作
    CHITCHAT = "chitchat"  # 问候、闲聊、感谢
    OUT_OF_SCOPE = "out_of_scope"  # 与公司内部事务无关


class IntentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0, description="0~1；低于阈值会转为向用户澄清")
    reason: str = Field(max_length=200, description="一句话判断依据，用于排查误判")
    slots: dict[str, str] = Field(
        default_factory=dict,
        description="抽取到的关键槽位（部门、时间范围等），阶段 04 的 RAG 与 SQL 查询会用它",
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: object) -> object:
        """把百分比写法归一化成 0~1。

        容忍**有边界**：只有落在 (1, 100] 才当作百分比（90 → 0.9）。
        更大的数（比如 150、5e9）不是"换了个写法"，是模型在胡说 ——
        必须让它校验失败，而不是悄悄除以 100 变成一个小数混过去。
        无限容忍等于没有校验。
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if 1 < value <= 100:
                return value / 100
        return value
