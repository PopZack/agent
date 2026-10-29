---
id: KB-003
title: Webhook 投递失败排查
tags: [webhook, callback, retry]
source: 内部排障手册 / 集成域
updated_at: 2026-10-01
trust_level: knowledge_reference
---

# Webhook 投递失败排查

## 症状
- `webhook_deliveries` 中连续多次 `FAILED`，最终 `EXHAUSTED`。
- 常见 `http_status` 为 500 或超时。

## 可能原因
1. 商户回调地址返回 5xx 或不可达。
2. 重试策略已耗尽（3 次）。
3. 签名 / 鉴权配置错误导致对方拒绝。

## 排查步骤
1. 查询投递历史，确认失败次数、HTTP 状态与错误。
2. 查询 Webhook 订阅状态与回调地址。
3. 查询分发服务日志，确认重试耗尽原因。

## 处置建议
- 将失败事件放入**模拟**重试队列（有副作用，需审批），并不代表真实重投。
- 建议商户核对回调地址可用性与签名配置。

## 相关证据
- 投递历史查询、订阅查询、日志查询。
