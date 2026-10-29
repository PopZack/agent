---
id: KB-006
title: 支付状态同步事件机制说明
tags: [event-sync, architecture, reference]
source: 系统设计说明
updated_at: 2026-10-01
trust_level: knowledge_reference
---

# 支付状态同步事件机制说明

## 概要
支付网关在支付成功后发出 `PAYMENT_SUCCEEDED` 事件，履约消费者订阅该事件并创建履约任务。

## 关键点
- 事件状态：`PENDING` → `CONSUMED` / `FAILED`。
- 事件投递为**至少一次**语义，消费方需用幂等键去重。
- 事件失败不会自动补偿，需要显式重新触发。

## 排查含义
- `FAILED` 的同步事件是"支付成功但履约未启动"的高概率原因。
- 重新触发同步前，先确认事件确实未成功消费（查业务状态），避免重复副作用。

## 相关证据
- `payment_sync_events` 表、履约服务日志。
