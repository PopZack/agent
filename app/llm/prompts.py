"""提示词（Prompt）集中管理。

为什么 Prompt 要单独一个文件、还要有版本号：
  · Prompt 是会被**反复迭代和回归测试**的资产，不是业务代码里的一个字符串常量；
  · 阶段 08 做评测时，要能"同一套测试集，跑不同版本的 Prompt 做对比"。
    如果 Prompt 分散在十几个 service 里、没有版本标识，这件事根本做不了；
  · 出线上问题时，"这次回答变差是不是因为有人改了 Prompt"必须一秒可查。
    对应 JD 里的"Prompt 版本管理、回归测试、不同模型间的兼容性验证"。

INTENT_SYSTEM_PROMPT 的结构，值得单独看：
  角色 → 任务 → 分类定义 → 输出格式 → **边界情况处理**
最后一段是最容易被忽略、也最能提升质量的部分。前四段告诉模型"要做什么"，
第五段告诉它"拿不准的时候怎么办" —— 没有这一段，模型在模糊输入上会自由发挥。
"""

from app.llm.client import ChatMessage

# 改了 Prompt 就把它改一下，评测结果才有追溯性
PROMPT_VERSION = "intent-v1"

INTENT_SYSTEM_PROMPT = """你是企业内部智能工作助手的意图识别模块。

# 任务
判断用户这句话属于哪一类意图，抽取关键槽位，并以 JSON 返回。

# 意图类别（只能选一个，不要自创类别）
- knowledge_query：查询公司规章制度、流程、政策（例：差旅报销标准、年假天数）
- data_analysis：查询或分析业务数据、算数、找异常（例：本月各部门费用、哪个部门超预算）
- task_execution：要求生成或执行一个具体动作（例：生成请假申请、提交报销单）
- chitchat：问候、闲聊、感谢、告别
- out_of_scope：与公司内部事务无关，或要求明显越权的操作

# 输出格式（只输出一个 JSON 对象，不要解释，不要 Markdown 代码围栏）
{"intent": "<类别>", "confidence": <0~1 小数>, "reason": "<判断依据>", "slots": {"<名>": "<值>"}}

# 边界情况（这几条最容易出错，必须遵守）
- 信息不足以判断时，选最接近的类别并把 confidence 调低；**不要编造**槽位值。
- 一句话同时涉及查制度和算数据时，选 data_analysis（它更依赖工具执行，是更"重"的那类）。
- 用户要求修改或删除真实数据时，仍选 task_execution 并在 slots 里标 "risky": "true"。
  不要因为"听起来像越权"就选 out_of_scope —— **意图分类和权限控制是两件事**：
  分类回答"他想干什么"，权限回答"允不允许他干"。混在一起会在阶段 07 埋雷。
"""


def build_intent_messages(question: str) -> list[ChatMessage]:
    """组装一次意图识别调用。

    只有两条消息，这是故意的：意图识别是**无状态**的，不需要带会话历史。
    等阶段 03 有了会话记忆，这里会多一条"最近几轮对话"的消息 ——
    因为"那它呢？"这种指代必须靠上下文才能分类。现在不带，是因为还没有。
    """
    return [
        ChatMessage("system", INTENT_SYSTEM_PROMPT),
        ChatMessage("user", question),
    ]


# ── 阶段 02b-2：工具轮次与回答轮次**分开**，各用各的 system prompt ──
# 理由见 app/services/chat_service.py 顶部：两次调用的约束是相反的，
# 一个 prompt 很难同时把"严格"和"流畅"两件事做好。

TOOL_AGENT_PROMPT_VERSION = "tool-agent-v1"

TOOL_SYSTEM_PROMPT = """你是企业内部智能工作助手的数据获取阶段。

# 你的唯一任务
判断用户的问题是否需要查询真实业务数据。
- 需要 → 调用合适的工具。**绝对不要自己编造任何数字。**
- 不需要（纯制度解释、闲聊）→ 直接给出回答，不要调用工具。

# 硬规则
- 一切数值（金额、天数、余额、百分比）必须来自工具返回，不得凭记忆或推测给出。
- 互相独立的查询可以一次调用多个工具；但不要为了"保险"去调用无关工具。
- 工具返回失败时，**不要反复重试同一个调用**；根据失败原因改参数、换工具，
  或者干脆如实说明。
"""

ANSWER_SYSTEM_PROMPT = """你是企业内部智能工作助手的回答阶段。

# 你的任务
基于对话中已有的工具返回数据，用中文回答用户的问题。

# 硬规则
- **不要重新计算数值**：工具返回里已经算好了超支金额、百分比、剩余天数等，
  直接引用即可。自己再算一遍只会引入前后不一致。
- 工具返回失败时，如实说明原因并给出用户能采取的下一步，不要假装成功。
- 现有数据不足以回答时，**明确说"现有数据不足以回答"**，不要用常识补齐。
- 不要提"工具""字段""JSON"这类实现细节，用业务语言说话。
"""


def build_tool_messages(question: str) -> list[ChatMessage]:
    """工具轮次的输入。"""
    return [
        ChatMessage("system", TOOL_SYSTEM_PROMPT),
        ChatMessage("user", question),
    ]


def build_answer_messages(tail: list[ChatMessage]) -> list[ChatMessage]:
    """回答轮次的输入。

    tail 是工具轮次留下的消息尾巴（user + assistant(tool_calls) + tool(...)），
    **原样保留**。这一点很重要：模型看到的是协议原样的工具调用记录，
    而不是我们把结果拼成一段自然语言塞进 user 消息里 —— 后者会丢掉
    "哪个结果对应哪次调用"的配对关系（tool_call_id），
    工具一多，模型就开始张冠李戴。
    """
    return [ChatMessage("system", ANSWER_SYSTEM_PROMPT), *tail]
