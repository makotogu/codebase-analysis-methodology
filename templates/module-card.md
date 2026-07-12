# 模块卡片：{module_id}

> Artifact metadata：`module-card.metadata.yaml`；当前 commit：`{commit}`；有效期至：`{expires_at}`。

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
| change-frequency hotspot signal（T1） | 87.5 | hotspot.csv 第 3 行；窗口 12 个月 |
| error-signal density（T3） | 42 条 / 10 KLOC | 原始分子 84 条、去重后 42 条；窗口 30 天 |
| ownership concentration proxy（T1） | top contributor 82% | ownership.csv 第 3 行；窗口 12 个月 |
| reachability-weighted exposure estimate（T5） | 12k/日 | 入口观测权重 × 静态可达关系；非真实模块流量 |
| change-coupling signal（T1） | 0.71 | 共同变更 10 次 / order 变更 14 次；窗口 12 个月 |

以上指标必须保留分子、分母、窗口、过滤规则和不确定性；不得由单项分数直接生成重构优先级。

## 入口（来自 entries.yaml）

- `http-order-create` POST /api/v1/orders `source-verified`
- `mq-payment-callback` topic=pay.result.notify `production-observed`（production-cn-east，2026-06）

## 依赖边（显式四元组，不用箭头图）

| from | to | type | evidence |
|---|---|---|---|
| order | inventory | rpc-out | OrderService.java:88 |
| order | t_order（写） | table-write | OrderMapper.xml:12 |
| order | reconciliation | cochange(0.71) | cochange.csv 第 9 行 |

Stable ID：`{module_id}`；aliases：`[]`；previous_id：`null`；lineage：`initial`。

## 未验证 / 断点清单

- PayRouter 存在反射分发，静态图在此断裂 `unverified`——建议日志验证

<!-- ===== /MACHINE SECTION ===== -->

<!-- ===== HUMAN SECTION（人工段，重跑保留）===== -->

## business-confirmed 论断

- t_order_ext 的 biz_type=3 分支已于 2025 年下线，表数据仅存量（据 张三，2026-07）
- 本模块的"取消"与客服口中的"关单"是同一件事（据 王五，2026-07）

适用范围：`order-service`；确认人：`[张三, 王五]`；复查日期：`2026-10-01`；状态：`business-confirmed`。

## 备注与警告

- 月底清算高峰期间不要在本模块做灰度发布（据 运维老刘，2026-07）

<!-- ===== /HUMAN SECTION ===== -->
