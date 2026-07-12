# 重构提案：{proposal_name}

<!-- 用法（03-重构决策）：
     三道证据门槛缺一，提案退回补证据。三道门槛通过只表示值得立项，
     生产迁移还必须通过 readiness。
     "让代码更干净"不是目标，不能作为立项理由。 -->

## 一、三道证据门槛（缺一退回）

### 1. 技术问题：适配证据 + 两个独立信号

| 论断 | 数据 | 来源 |
|---|---|---|
| OrderService 是声明仓库范围内第 2 change-frequency hotspot | 近 12 月加权变更 47 次，2100 行 | hotspot.csv 第 2 行；`inferred` |
| 它与 CouponService 有 change-coupling signal | 共同变更 10 次 / OrderService 变更 14 次 | cochange.csv 第 9 行；`inferred` |
| 工单回归集中在同一链路（独立信号） | 近 6 月 9/14 个相关回归 | issue filter + trace；`production-observed` |

### 2. 业务语义与需求信号 `business-confirmed`

- "优惠计算属于营销域，不属于订单域"（据 营销负责人 王五，2026-07）
- 相关 glossary 条目：coupon / promo-rule（均已 business-confirmed）
- 需求侧来源：`demand-signals.yaml#demand-order-promo-001`；适用范围与复查日期见该记录。

### 3. 收益假设（明确赌哪个指标）

> 把优惠计算从 OrderService 拆出后，预计"促销类需求的平均改动文件数"
> 从 7.2 降到 ≤3（基线见 metrics-baseline.md），上线后三个月复测；若连续两个月无改善则停止扩展范围。

## 二、执行方案（03 篇第五节四步）

1. **表征测试**：AI 对 OrderService#calcPrice 现有行为批量生成表征测试，锁定现状（含已知的错误行为，如实记录）；
2. **绞杀者路径**：新建 promo-engine 模块，流量按订单尾号 5% → 50% → 100% 灰度，每步可回退；
3. **可观测性顺手生长**：新链路埋 span + 业务键（订单号）进 attributes；
4. **适应度断言**：`order 模块不得 import promo-engine 内部包`、`OrderService ≤ 800 行`，进 CI。

## 三、风险与失效模式预判

| 失效模式 | 概率 | 缓解 |
|---|---|---|
| 表征测试覆盖不住隐式分支 | 中 | 灰度期间双跑 diff 新旧结果 |
| 促销高峰撞上灰度窗口 | 低 | 冻结窗口：大促前两周不推进度 |
| 资源被抽走烂尾 | 中 | 每步独立可收尾；棘轮断言先上线，即使中止也不回退 |

## 四、执行 readiness（生产迁移前逐项通过）

| 检查项 | 负责人 | 证据 / 方案 | 状态 |
|---|---|---|---|
| 负责人和干系人 |  |  | pending |
| 表征测试或行为 Oracle |  |  | pending |
| 可观测性 |  |  | pending |
| 发布与回滚 |  |  | pending |
| 数据迁移 |  |  | pending / not-applicable |
| 安全与隐私 |  |  | pending |
| 停止条件 |  |  | pending |

readiness 未全部通过时，可以批准探索和补证，不得进入生产迁移。

## 五、裁决记录（人工段）

- 裁决：通过 / 退回（原因）
- 裁决人 / 日期：
- readiness 裁决 / 日期：
