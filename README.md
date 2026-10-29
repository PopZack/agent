# AegisFlow

**企业级 AI 事件调查与自动处置平台 —— 从 0 到 1 的受控 Agent 工程**

AegisFlow 面向企业业务异常（订单支付失败、Webhook 投递失败、第三方 API 超时、订单与履约状态不一致）。
用户创建事件后，系统通过 LangGraph 编排调查流程，让 Agent 使用**受控工具**收集业务证据、检索排障知识、
分析可能原因、生成处置计划；涉及**有副作用**的操作时暂停等待人工审批，批准后执行**模拟**动作，再独立查询验证，最终生成可追溯报告。

> 核心原则：**模型可以提出"要做什么"，但不能单独决定"是否有权做、是否允许做、是否已经做过、做完是否成功"。**
> 这些判断由确定性的代码、业务状态与权限策略执行。

本项目是学习 / 作品集级工程：**只用模拟数据、模拟审批与模拟写操作，不连接任何真实支付、客户生产数据或外部消息系统。**

---

## 它不是什么

- 不是只会聊天的 Chatbot。
- 不允许模型直接执行 SQL / Shell / 任意 Python 代码。
- 不允许模型动态构造任意 URL 访问互联网。
- 首版不做微服务、不做复杂多 Agent、不为"技术栈完整"强行引入 Redis / MCP / 向量数据库。
- 不把模型生成的根因直接认定为事实。

---

## 当前进度

### ✅ 阶段 0 · 准备阶段（需求 / 环境 / 项目骨架）

本阶段已完成，交付物：

- `pyproject.toml`（Python 3.12，依赖按阶段规划）
- `.env.example`（真实密钥不入库）+ `.gitignore`
- `docker-compose.yml`（本地 MySQL 8）
- 最小可运行骨架：`GET /health` + 冒烟测试
- 四类模拟事件 `data/seed/events.json` + 业务数据 `data/seed/business_data.json`
- 8 份排障手册 `data/knowledge/*.md`
- 领域模型与状态定义 `docs/DOMAIN_DESIGN.md`
- MVP 验收清单 `docs/MVP_CHECKLIST.md`

> 阶段 0 只做"可验证的最小地基"，不实现业务 API / Agent / RAG。

后续阶段见 [阶段路线图](#阶段路线图)。

---

## 架构概览（逻辑架构）

```
用户 / Web 工作台
      |
      v
FastAPI API  ->  Application Service  ->  Agent Harness 入口
                                              |
                  +---------------------------+---------------------------+
                  |                                                       |
                  v                                                       v
          LangGraph 工作流                                        Harness 控制能力
      State / Nodes / Edges                              工具注册 · 参数校验
      条件路由 / 调查循环                                 权限与风险策略
      人工中断 / 恢复                                     调用预算 · 超时
                  |                                       上下文与证据管理
                  |                                       错误与重试 · 输出验证
                  |                                       日志 · Trace · 审计
                  +-------------------------+-----------------------------+
                                            |
                                            v
                             LLM + 受控业务工具 + RAG
                                            |
                                            v
                        MySQL / Checkpointer / 本地知识库
```

Harness 不是单个类，而是一组明确的职责与控制机制（Runtime、Tool Registry/Executor、Context Manager、
Policy Engine、Budget Manager、Error Handler、Validator、Audit），与 LangGraph 协同工作。

数据库分层（不要混淆）：
- **MySQL**：业务事实与审计（事件、审批、动作执行记录）。
- **LangGraph Checkpointer**：图执行检查点，用于恢复工作流。
- **本地知识库**：排障手册 / 系统说明 / 历史案例。
- **评测集文件**：固定测试输入与标注。

---

## 目录结构

当前（阶段 0）：

```
.
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI 入口 + /health
│   └── infrastructure/
│       └── settings.py             # 集中配置（pydantic-settings）
├── data/
│   ├── seed/                       # 模拟事件与业务数据
│   │   ├── events.json
│   │   ├── business_data.json
│   │   └── README.md
│   ├── knowledge/                  # 8 份排障手册（Markdown + frontmatter）
│   └── evals/                      # 评测集（阶段 9 填充）
├── docs/
│   ├── DOMAIN_DESIGN.md            # 事件/运行/动作状态、风险等级、模式、错误分类
│   └── MVP_CHECKLIST.md            # 验收清单
├── tests/
│   └── test_health.py              # 冒烟测试
├── .env.example
├── docker-compose.yml              # 本地 MySQL 8
├── pyproject.toml
└── README.md
```

目标形态（随阶段逐步长出来，见 `AegisFlow.md` 第 3.3 节）：
`app/api`、`app/schemas`、`app/services`、`app/agent/{harness,graph}`、`app/tools`、`app/knowledge`、
`app/policies`、`app/models`、`app/repositories`、`app/observability`、`frontend/` 等。

> 原则：职责明确后再拆分，不为符合目录图提前创建大量空模块。

---

## 快速开始

### 1. 环境

- Python **3.12+**
- （阶段 1 起）Docker，用于本地 MySQL

### 2. 安装依赖

PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Bash / Git Bash：

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -e ".[dev]"
```

### 3. 配置

```powershell
Copy-Item .env.example .env
```

默认 `RUN_MODE=mock`，无需 API Key 即可运行骨架与测试。

### 4. 启动数据库（阶段 1 起）

```bash
docker compose up -d
```

### 5. 运行服务

```bash
uvicorn app.main:app --reload
```

访问：

- 健康检查：<http://127.0.0.1:8000/health>
- 接口文档：<http://127.0.0.1:8000/docs>

`/health` 会回显当前 `environment` 与 `run_mode`（mock / real）。

### 6. 测试

```bash
pytest
```

---

## 运行模式

| 模式 | 含义 | 要求 |
|---|---|---|
| `real` | 调用真实模型执行分类、工具选择、原因分析、报告生成 | 配置 `LLM_*` |
| `mock` | 确定性模拟响应，验证图路径、审批、错误与恢复 | 无需 API Key / 网络 |

Mock 模式不得伪装成真实模型结果；界面与日志都标记当前模式。没有 API Key 时，单元/集成测试与确定性演示仍可运行。

---

## 数据与知识

- 事件与业务数据：`data/seed/`（格式见 `data/seed/README.md`）。
- 排障手册：`data/knowledge/`（统一 frontmatter：`id`/`title`/`tags`/`source`/`updated_at`/`trust_level`）。
- 评测集：`data/evals/`（阶段 9 填充）。

四类事件与示例故事：

| 事件编号 | 类型 | 故事 |
|---|---|---|
| `EVT-2026-001` | 订单与履约状态不一致 | 订单 `ORD-1008` 已支付，但履约未启动（同步事件失败） |
| `EVT-2026-002` | 订单支付失败 | `ORD-1009` 网关超时后放弃 |
| `EVT-2026-003` | Webhook 投递失败 | `ORD-1010` 支付成功回调重试耗尽 |
| `EVT-2026-004` | 第三方 API 超时 | 风控 `fraud-check-api` 连续超时 |

---

## 阶段路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 | 准备：需求 / 环境 / 项目骨架 | ✅ |
| 1 | 普通后端骨架（FastAPI + MySQL + CRUD + 测试） | ⏳ |
| 2 | 工具层与 Harness 最小内核（Registry/Executor/Policy/Budget/Audit） | ⏳ |
| 3 | LangGraph 最小调查流程 + Checkpointer | ⏳ |
| 4 | 真实模型与结构化 Tool Calling | ⏳ |
| 5 | RAG 与证据链 | ⏳ |
| 6 | Harness 完整控制能力（Context/Policy/Budget/Error/Validator） | ⏳ |
| 7 | 处置计划、人工审批、中断与恢复、幂等写操作 | ⏳ |
| 8 | 事件工作台与报告 | ⏳ |
| 9 | 自动化测试、Agent/RAG 评测、安全验证、可观测性 | ⏳ |
| 10 | 作品集交付 | ⏳ |

---

## 三条验收路径（MVP 终态）

1. **成功路径**：创建事件 → 调查 → 证据 → 处置计划 → 审批 → 执行 → 验证 → 报告。
2. **拒绝路径**：调查完成 → 需要审批 → 审批拒绝 → 不执行写操作 → 保存拒绝记录。
3. **异常恢复路径**：工具失败 / 工作流中断 → 保存状态 → 恢复运行 → 不重复执行已成功的副作用。

详见 `docs/MVP_CHECKLIST.md`。

---

## 关于本仓库历史

本仓库此前是一个不同方向的项目（"企业智能 Agent 助手"聊天应用）。按新方案 `AegisFlow.md` **重做**为
AegisFlow。旧代码保留在两处：

- 旧版工作副本：本地 `legacy_prev_project/`（已加入 `.gitignore`，仅本地保留）。
- 历史提交：`git log` / `git show 1194f66` 仍可追溯。

---

## 已知限制

- 阶段 0 仅骨架：只有一个健康检查接口，无业务 API / Agent / RAG。
- 所有处置均为**模拟**动作；`R3`（真实退款、删除生产数据、发真实消息）不提供执行工具。
- MySQL 驱动、LangGraph 等依赖已列入 `pyproject.toml`，但阶段 0 尚未接入。
