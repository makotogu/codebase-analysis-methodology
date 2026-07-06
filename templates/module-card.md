# 模块卡片：{module_id}

<!-- 用法（T6 map-builder，04/07 篇）：
     每模块一张卡片，是 Agent 会话的标准起始上下文。
     stable ID 贯穿一切：本卡片的 module_id 必须与 hotspot.csv、
     entries.yaml、依赖边中的 ID 一致，人和模型才能对上同一个东西。
     MACHINE 段随时重跑覆盖；HUMAN 段永久保留。 -->

<!-- ===== MACHINE SECTION（重跑覆盖）===== -->

## 职责（推断）

一句话职责描述。 `inferred`

## 热度与风险

| 指标 | 值 | 证据 |
|---|---|---|
| 热点分（T1） | 87.5 | hotspot.csv 第 3 行 |
| 缺陷密度（T3 error 聚合） | 高 | logs/error-clusters.md#c12 |
| bus factor（T1 ownership） | 1（知识孤岛） | ownership.csv 第 3 行 |
| 承载流量 | 入口权重合计 12k/日 | entry_weights.csv |

## 入口（来自 entries.yaml）

- `http-order-create` POST /api/v1/orders `verified`
- `mq-payment-callback` topic=pay.result.notify `verified`

## 依赖边（显式四元组，不用箭头图）

| from | to | type | evidence |
|---|---|---|---|
| order | inventory | rpc-out | OrderService.java:88 |
| order | t_order（写） | table-write | OrderMapper.xml:12 |
| order | reconciliation | cochange(0.71) | cochange.csv 第 9 行 |

## 未验证 / 断点清单

- PayRouter 存在反射分发，静态图在此断裂 `unverified`——建议日志验证

<!-- ===== /MACHINE SECTION ===== -->

<!-- ===== HUMAN SECTION（人工段，重跑保留）===== -->

## business-confirmed 论断

- t_order_ext 的 biz_type=3 分支已于 2025 年下线，表数据仅存量（据 张三，2026-07）
- 本模块的"取消"与客服口中的"关单"是同一件事（据 王五，2026-07）

## 备注与警告

- 月底清算高峰期间不要在本模块做灰度发布（据 运维老刘，2026-07）

<!-- ===== /HUMAN SECTION ===== -->
