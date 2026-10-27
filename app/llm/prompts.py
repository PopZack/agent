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
