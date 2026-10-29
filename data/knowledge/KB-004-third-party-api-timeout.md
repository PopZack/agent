---
id: KB-004
title: 第三方 API 超时处置
tags: [third-party, timeout, service-status]
source: 内部排障手册 / 依赖治理
updated_at: 2026-10-01
trust_level: knowledge_reference
---

# 第三方 API 超时处置

## 症状
- 依赖服务状态 `DEGRADED`，P95 延迟显著升高。
- 调用日志出现 `timed out after ...ms`。
- 上游业务卡在中间状态（如 `PENDING_REVIEW`）。

## 可能原因
1. 依赖服务自身过载或故障。
2. 网络链路抖动 / DNS 问题。
3. 调用方超时阈值过短。

## 排查步骤
1. 查询依赖服务状态（可用性、延迟、错误率）。
2. 查询调用日志，确认超时分布与 `trace_id`。
3. 判断是否为区域性故障。

## 处置建议
- 短期：切换到**模拟**备用通道（有副作用，需审批）。
- 中期：评估超时阈值与熔断策略。
- 不盲目重试对端不稳定的写请求。

## 相关证据
- 服务状态查询、日志查询、历史事件。
