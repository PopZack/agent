# 模拟数据 / data/seed

本地模拟数据，**不连接任何真实系统**。用于阶段 1 建表初始化、阶段 2 只读工具、阶段 3+ 调查闭环。

## events.json —— 事件样本（数组）
每条事件字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 事件编号，如 `EVT-2026-001` |
| `event_type` | enum | 见 `docs/DOMAIN_DESIGN.md` 事件类型 |
| `severity` | enum | `LOW` / `MEDIUM` / `HIGH` |
| `status` | enum | 事件状态，见 `docs/DOMAIN_DESIGN.md` |
| `title` | string | 概述 |
| `description` | string | 详细描述 |
| `payload` | object | 业务对象引用（订单/支付/履约 id 等） |
| `created_at` | ISO8601 string | 带时区 |

## business_data.json —— 业务对象（对象）
按域分组：

- `orders`：订单
- `payments`：支付记录
- `payment_sync_events`：支付状态同步事件
- `fulfillment_records`：履约记录
- `webhook_subscriptions` / `webhook_deliveries`：Webhook 订阅与投递
- `service_statuses`：依赖服务状态
- `service_logs`：服务日志

示例事件 `EVT-2026-001`（订单已支付但履约未启动）与数据一致：
`ORD-1008` 为 `PAID` → `PAY-90001` 为 `SUCCEEDED` → 同步事件 `SYNC-6001` 为 `FAILED` → `FUL-5001` 为 `NOT_STARTED`。
