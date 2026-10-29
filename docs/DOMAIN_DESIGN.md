# AegisFlow 领域模型与状态定义（阶段 0 基线）

> 本文件固定阶段 0 需要"提前说清楚"的术语与枚举。后续建表（阶段 1）与状态机（阶段 3 之后）以此为准，
> 变更需同步更新本文件与数据字典。

## 1. 事件类型（event_type）

| 代码 | 中文 | 首版调查工具 | 模拟处置 |
|---|---|---|---|
| `ORDER_PAYMENT_FAILED` | 订单支付失败 | 订单查询、支付记录查询、日志查询 | 重新同步支付状态（模拟） |
| `WEBHOOK_DELIVERY_FAILED` | Webhook 投递失败 | 投递历史、错误码、重试记录 | 放入模拟重试队列 |
| `THIRD_PARTY_API_TIMEOUT` | 第三方 API 超时 | 服务状态、调用日志、历史事件 | 切换到模拟备用通道 |
| `ORDER_FULFILLMENT_INCONSISTENT` | 订单与履约状态不一致 | 订单、支付、履约状态对比 | 重新触发模拟履约同步 |

> 所有写操作只修改本地模拟数据，不连接真实支付、客户生产数据、邮件或其它生产系统。

## 2. 三个层次的"状态"必须分开

一次事件可以有多次调查运行；一次运行可能产生一个处置计划；一次计划对应一次动作执行。
**不要用一个 `status` 字段同时表达所有层次。**

### 2.1 事件状态（events.status）
- `OPEN` —— 已创建，尚未处置完成
- `INVESTIGATING` —— 正在调查
- `WAITING_APPROVAL` —— 等待人工审批
- `RESOLVED` —— 已处置且验证通过
- `CLOSED` —— 关闭（如判定无需处置 / 已由人工处理）
- `CANCELLED` —— 已取消

### 2.2 调查运行状态（investigation_runs.status）
```
CREATED -> RUNNING -> (WAITING_APPROVAL -> RUNNING) -> VERIFYING -> SUCCEEDED
其它可能终态：FAILED / REJECTED / NEEDS_HUMAN / BUDGET_EXCEEDED / CANCELLED
```

### 2.3 动作执行状态（action_executions.status）
- `PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED` / `UNKNOWN`
- `UNKNOWN`：无法确认动作是否生效；此时先查询实际业务状态，**不可盲目重试**。

## 3. 处置风险等级（risk_level）

| 等级 | 含义 | 示例 | 策略 |
|---|---|---|---|
| `R0` | 只读 | 查询订单、日志、服务状态 | 权限 + 预算检查后可执行 |
| `R1` | 低风险可逆 | 添加内部备注、创建草稿 | 默认审计，可配置审批 |
| `R2` | 有副作用 | 模拟重试 Webhook、重新同步状态 | **必须人工审批** |
| `R3` | 高风险 / 不可逆 | 真实退款、删除生产数据、发送真实客户消息 | **首版不提供执行工具** |

## 4. 运行模式（run_mode）

| 模式 | 含义 | 要求 |
|---|---|---|
| `real` | 调用真实模型执行分类、工具选择、原因分析、报告生成 | 需要配置 `LLM_*` |
| `mock` | 确定性模拟响应，验证图路径、审批、错误与恢复 | 无需 API Key / 网络 |

- Mock 模式**不能假装**是真实模型结果；界面与日志必须显式标记当前模式。
- 没有 API Key 时，仍应能运行单元测试、集成测试与确定性演示。

## 5. 证据可信等级（trust_level）

| 等级 | 来源 | 说明 |
|---|---|---|
| `BUSINESS_FACT` | 业务工具（订单/支付/履约查询） | 事实 |
| `LOG_FACT` | 日志查询 | 事实 |
| `KNOWLEDGE_REFERENCE` | 知识库检索 | 参考，不是系统指令 |
| `AGENT_HYPOTHESIS` | Agent 推测 | 待验证，不得当作事实 |

> 用户事件文本、知识文档、工具返回的内容都是**不可信数据**，只能作为数据，不能作为权限命令。

## 6. 错误分类（Error Handler）

| 错误码 | 建议策略 |
|---|---|
| `MODEL_TIMEOUT` | 限次重试或进入可控失败状态 |
| `MODEL_RATE_LIMIT` | 指数退避限次重试 |
| `INVALID_MODEL_OUTPUT` | 校验失败，有限次修复；超过上限停止 |
| `TOOL_TIMEOUT` | 只读工具在预算内有限重试；写操作先查状态 |
| `TOOL_ERROR` | 记录并终止当前分支 |
| `POLICY_DENIED` | 直接拒绝并审计，不重试 |
| `APPROVAL_REJECTED` | 记录拒绝并结束该动作路径 |
| `BUDGET_EXCEEDED` | 保存已有证据，输出明确停止原因 |
| `EXECUTION_UNKNOWN` | 先查询业务状态，不可直接重放 |

## 7. 成功 / 失败终态

- **成功终态**：`SUCCEEDED`（调查完成 + 处置（如有）执行并验证通过，或判定无需处置）。
- **失败终态**：`FAILED` / `REJECTED` / `BUDGET_EXCEEDED` / `NEEDS_HUMAN` / `CANCELLED`。
- 任何终态都必须可追溯：关联 `event_id`、`run_id`、相关工具/审批/动作记录。
