# 访谈问题清单：{module_or_topic}

<!-- 用法（02 篇第三节，错位探测器）：
     本清单由 AI 从热点模块中筛出，人拿去访谈领域专家。
     问题质量三要求：
       1. 具体——指向某个文件某一行，不问"给我讲讲这个系统"；
       2. 可回答——业务专家不用看代码也能回答；
       3. 高杠杆——答案能纠正一类命名/语义错位，不只是一个点。
     访谈后：answer 列如实记录（具名+日期），并回填 glossary.yaml，
     然后把 backfilled 标为 yes。这个闭环是清单存在的意义。 -->

| # | 问题 | 探测到的错位模式 | 证据 | 访谈对象 | 回答（具名+日期） | backfilled |
|---|---|---|---|---|---|---|
| 1 | `SettleService` 处理的是财务口中的"结算"还是"对账"？ | 命名疑似漂移：类名 Settle，但读的全是渠道流水表 | SettleService.java:66 读 t_channel_flow | 李四 | 是对账；结算在 ClearingJob（李四，2026-07） | yes |
| 2 | 订单"取消"和客服说的"关单"是一回事吗？ | 同一业务词在 order 与 cs 模块行为不同 | OrderService.java:201 vs CsTicketService.java:88 | 张三 |  | no |
| 3 | `validateCoupon` 里顺手更新了优惠券状态，这是刻意设计吗？ | 名为 validate 却有写操作 | CouponService.java:130 UPDATE t_coupon | 王五 |  | no |
| 4 | 注释说"仅 VIP 走此分支"，但条件是 `level >= 2`，普通会员 level=2 也会进入——注释过时还是 bug？ | 注释与代码打架 | PriceCalculator.java:77 | 张三 |  | no |

## 访谈纪律

- 一次访谈 30 分钟以内，问题不超过 8 个——按预期杠杆排序，问不完的下次再约；
- 记录原话，不要当场翻译成自己的理解；歧义当场追问；
- 访谈结束 24 小时内回填 glossary，否则记忆衰减、上下文丢失。
