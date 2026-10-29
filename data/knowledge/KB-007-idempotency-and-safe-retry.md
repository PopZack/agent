---
id: KB-007
title: 幂等键与安全重试原则
tags: [idempotency, retry, reliability]
source: 系统设计说明 / 可靠性
updated_at: 2026-10-01
trust_level: knowledge_reference
---

# 幂等键与安全重试原则

## 原则
- 所有写操作使用稳定的幂等键（如 `plan_id + action_version` 的哈希）。
- 执行状态区分 `PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED` / `UNKNOWN`。
- `UNKNOWN` 表示无法确认是否生效：先查询实际业务状态，**不可盲目重试**。

## 重试分类
- 只读工具超时：预算内可有限重试。
- 写操作超时：先查状态，再决定。
- 参数错误 / 权限拒绝：不重试。

## 相关证据
- 动作执行记录、幂等键、业务状态查询。
