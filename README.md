# Enterprise Agent · 企业智能Agent助手

从零实现一个 **Agent Harness / Runtime** 的学习项目。目标不是"跑通一个 Demo"，
而是把每一层为什么这么设计讲清楚、写出来、测出来。

> 硬约束：**只用模拟数据、模拟审批接口，不接任何真实企业系统。**

## 这是什么

模拟企业内部智能助手：能查公司制度、分析业务数据、调用业务工具，
并执行**有权限限制的多步骤任务**。所有涉及真实修改数据的操作，
都必须先过权限检查 + 人工确认。

## 目标架构（7 层）

```
用户交互层      聊天界面 / 会话管理 / 流式响应 / 任务状态
     │
Agent Harness   执行循环 / 规划 / 状态机 / 上下文压缩 / 重试 / 权限 / 日志 / 恢复
     │
RAG 知识库      文档解析 / Embedding / 检索 / Rerank / 引用
     │
工具系统        Function Calling / MCP / SQL 查询 / 业务 API
     │
数据层          PostgreSQL / Redis / 向量检索 / 任务状态
     │
安全与质量      沙箱 / 权限 / 评测 / Tracing / Token 成本
     │
部署与运维      Docker / 自动化测试 / CI-CD / 监控 / 故障恢复
```

## 当前进度

### 阶段 01 · 后端基础与项目骨架 ✅

交付标准（来自项目规格）：*「一个可以接收问题、返回 JSON、记录请求日志的后端服务」*

```
POST /chat  ──→  RequestContextMiddleware （生成/沿用 request_id，写入 ContextVar）
                     │
                     ├─→ ChatRequest        （Pydantic 校验入参）
                     ├─→ ChatService.answer （业务逻辑）
                     └─→ ChatResponse       （拼装对外契约，盖 request_id）
                     │
                     └─→ 访问日志（自带 request_id + 耗时） + 响应头 X-Request-ID
```

### 阶段 02a · 模型接入层 ✅

交付标准：*能发出模型调用、扛住限流与超时、并把上游故障讲清楚的一层*

```
LLMClient.chat()
   ├─ 发请求   POST {LLM_BASE_URL}/chat/completions   （原生 httpx，不用 SDK）
   ├─ 超时     connect 与 read 分开设上限
   ├─ 重试     429 / 5xx / 超时 → 指数退避 + 抖动，优先听 Retry-After
   │           401 / 403 / 404 → 一次就放弃（重试没意义，只是白烧钱）
   └─ 解析     choices[0].message.content + usage；上游格式漂移要说清坏在哪
```

配套的 `scripts/mock_llm_server.py` 是一个模拟模型服务，能按指令返回 429 / 401 /
畸形响应 —— 重试、超时这类机制**不在真跑一遍的情况下无法验证**。

### 阶段 02b-1 · 意图识别与结构化输出 ✅

交付标准：*模型输出能被程序可靠消费，且模型"不听话"时不会拖垮链路*

```
POST /chat
  │
  ├─ ChatService.answer()                    业务层：不知道 HTTP 存在
  │    └─ IntentService.classify()           一次结构化模型调用
  │         └─ LLMClient.chat_structured()
  │              ├─ 带 response_format={"type":"json_object"}
  │              ├─ _extract_json()          剥掉 ```json 围栏 / 散文前后缀
  │              ├─ Pydantic 校验 IntentResult（宽进严出：92 → 0.92）
  │              └─ 失败 → 回灌「它的输出 + 确切错误 + JSON Schema」修复
  │                       **有界**：默认只修 1 次，再不行对外就是 502
  │
  └─ 置信度 < 阈值(0.6) → 转为向用户澄清，而不是硬猜
```

### 阶段 02b-2 · Function Calling + 流式 SSE ✅

交付标准：*模型能选择并调用工具拿到真实数据；回答能以 SSE 流式吐出*

```
POST /chat            非流式：意图 → 工具轮（最多 N 轮）→ 回答轮
POST /chat/stream     流式：  同一条链路，回答轮改为 SSE 逐段吐出

工具执行器四道关（模型输出是不可信输入）：
  1 白名单   工具名不存在 → 把可用清单回传，模型下一轮才可能选对
  2 解析     arguments 不是合法 JSON → 原文截断回传
  3 校验     Pydantic 校验参数 → 错误里带"实际给的是什么"
  4 超时     asyncio.timeout 包住执行 → 挂死的工具不能挂死请求
任何一关失败都返回 ToolResult(ok=False) 讲给模型听 —— 工具失败 ≠ 请求失败。
```

**阶段 02 完结。** 下一站：阶段 03 数据库与会话记忆。

## 快速开始

```bash
# 1. 建虚拟环境并安装（含开发依赖）
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows
# source .venv/bin/activate && pip install -e ".[dev]"   # macOS / Linux

# 2. 配置
cp .env.example .env

# 3. 启动（Windows 下加 PYTHONIOENCODING 避免中文日志乱码）
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m uvicorn app.main:app --reload
```

接口文档 http://127.0.0.1:8000/docs

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: trace-demo-001" \
  -d '{"message":"帮我查一下公司的差旅报销制度，并告诉我住宿标准。"}'
```

```json
{
  "request_id": "trace-demo-001",
  "session_id": null,
  "answer": "[阶段01 骨架] 已收到你的问题：帮我查一下公司的差旅报销制度，并告诉我住宿标准。",
  "stage": "01-skeleton",
  "model": null
}
```

## 开发命令

```bash
.venv/Scripts/python -m pytest          # 52 条测试
.venv/Scripts/python -m ruff check .    # lint
.venv/Scripts/python -m ruff format .   # 格式化（已采纳，请勿手工对齐行内注释）

# 模拟模型服务 + 实跑演示（学习用，不接真实模型）
.venv/Scripts/python scripts/mock_llm_server.py    # 终端 1：默认监听 127.0.0.1:8200
.venv/Scripts/python scripts/demo_llm_layer.py     # 终端 2：实跑正常/限流/鉴权/格式漂移
```

## 目录结构

```
app/
├─ main.py              应用入口（create_app 工厂）
├─ core/                跨层基础设施，被所有人依赖，自己不依赖别人
│  ├─ config.py         pydantic-settings 配置（启动即校验）
│  ├─ context.py        request_id 的 ContextVar —— 全链路串联的地基
│  ├─ logging.py        Filter 补 request_id / Formatter 出文本或 JSON
│  └─ exceptions.py     与协议无关的业务异常体系
├─ api/                 对外接口层：只做校验、装配、翻译，不写业务
│  ├─ middleware.py     request_id 中间件 + 访问日志
│  ├─ errors.py         异常 → HTTP 的唯一翻译点
│  ├─ deps.py           依赖装配点
│  └─ routers/          health / chat
├─ schemas/             对外契约（请求严、响应稳）
│  ├─ chat.py           /chat 的出入参
│  └─ intent.py         意图契约：枚举类别 + 置信度 + 宽进严出的归一化
├─ llm/                 模型接入层：原生 httpx，超时 / 重试 / 错误映射
│  ├─ client.py         chat / chat_structured / chat_stream + SSE 解析
│  └─ prompts.py        提示词集中管理（带版本号，便于回归对比）
├─ tools/               工具系统
│  ├─ base.py           Tool 基类：schema 从 Pydantic 自动生成（剥 title 省 token）
│  ├─ registry.py       注册表：重名启动即炸 + 按意图的白名单
│  └─ business.py       模拟业务工具（部门费用 / 年假余额）
└─ services/            业务逻辑（不许 import fastapi）
   ├─ chat_service.py   意图分流 → 工具轮 → 回答轮（含流式）
   ├─ intent_service.py 意图识别（结构化输出）
   └─ tool_service.py   执行器四道关
tests/                  验收测试 + fakes（假 transport / 假模型）
scripts/                模拟模型服务 + 实跑演示（不属于 app 包，不参与打包）
```

**依赖方向只能向下：** `api → services → llm →（阶段 05 起：runtime/tools/rag/db）`，
`core` 谁都能用、自己谁也不依赖。

## 学习路线

| 阶段 | 主题 | 状态 |
|---|---|---|
| 01 | 后端基础与项目骨架 | ✅ 已完成 |
| 02 | LLM 应用与 Prompt Engineering | ✅ 02a / 02b-1 / 02b-2 |
| 03 | 数据库与会话记忆 | ⬜ |
| 04 | RAG 企业知识库 | ⬜ |
| 05 | Agent 核心执行循环（ReAct / Plan-and-Execute） | ⬜ |
| 06 | MCP 与业务工具生态 | ⬜ |
| 07 | Agent Harness 工程化（Skills/Hooks/Subagents/Rules） | ⬜ |
| 08 | 评测、可观测性与成本优化 | ⬜ |
| 09 | Docker 与生产部署 | ⬜ |
| 10 | 综合项目与面试准备 | ⬜ |

每阶段都在上一阶段代码上迭代，不重写 Demo。
