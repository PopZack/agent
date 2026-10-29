---
id: KB-002
title: 订单支付失败排查
tags: [payment, gateway]
source: 内部排障手册 / 支付域
updated_at: 2026-10-01
trust_level: knowledge_reference
---

# 订单支付失败排查

## 症状
- 订单状态为 `PAYMENT_FAILED`。
- 支付记录 `FAILED`，带有 `failure_code`。

## 可能原因
1. 支付网关超时（`GATEWAY_TIMEOUT`），重试后仍失败。
2. 卡被拒付 / 余额不足（网关拒付码）。
3. 网关返回格式异常，被判为失败。

## 排查步骤
1. 查询支付记录的 `failure_code` 与 `failure_reason`。
2. 查询支付网关服务状态是否 `HEALTHY`。
3. 查询支付相关日志中的超时记录。

## 处置建议
- 网关超时类失败：确认网关恢复后，引导用户重新发起支付。
- 拒付类失败：不要重复自动重试，需用户更换支付方式。
- 本平台不提供真实退款或代扣工具。

## 相关证据
- 支付记录查询、服务状态查询、日志查询。
