---
id: KB-001
title: 支付成功但履约未启动
tags: [payment, fulfillment, event-sync]
source: 内部排障手册 / 履约域
updated_at: 2026-10-01
trust_level: knowledge_reference
---

# 支付成功但履约未启动

## 症状
- 订单状态为 `PAID`，支付记录 `SUCCEEDED`。
- 履约记录为 `NOT_STARTED` 或根本不存在。
- 支付状态同步事件 `PAYMENT_SUCCEEDED` 可能为 `FAILED`。

## 可能原因
1. 支付成功事件已发出，但履约消费者处理失败（`consumer handler returned 500`）。
2. 事件重复投递时被幂等层误判为已处理，实际未创建履约任务。
3. 订单与履约之间的映射缺失（`fulfillment_id` 为空）。

## 排查步骤
1. 查询订单与支付记录，确认订单确为已支付。
2. 查询 `payment_sync_events`，确认 `PAYMENT_SUCCEEDED` 事件的状态与错误。
3. 查询履约记录，确认是否存在对应 workflow。
4. 查询履约服务日志，查找 "no fulfillment job found"。

## 处置建议
- 确认同步事件失败 → 重新触发一次**模拟**履约同步（有副作用，需人工审批）。
- 处置后再查询履约状态，验证是否变为 `IN_PROGRESS`。

## 相关证据
- 订单查询、支付记录查询、履约状态查询、日志查询。
