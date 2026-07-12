# 追踪记录：{journey_name}

<!-- 用法（01 篇第六节 / 02 篇第四节）：
     以流为锚点——一条端到端可观察旅程（一个请求 / 一条 SQL / 一个定时任务）。
     纪律：不得把预期输出当成真实输出写进记录。
     跑不起来就标"未运行"，推演就标"推演"。
     一份诚实标注的低等级追踪，价值远高于一份看似完整的编造记录。 -->

## 元信息

| 项 | 值 |
|---|---|
| 旅程 | POST /api/v1/orders → 订单落库 → 库存锁定 → MQ 通知 |
| artifact metadata | `trace-record.metadata.yaml` |
| commit / 部署版本 | `0123456789abcdef` / `order-service:2026.07.10` |
| 整体证据类型 | `test-observed`（第 1–3 步在测试环境观察；第 4 步仍为 `unverified`） |
| 环境 | 本地最小环境：order-service + 内存 H2 + mock inventory |
| 时间窗口 / 采样 | 2026-07-15 10:00–10:30 CST / 全量记录该次测试 |
| 获取方式 | 断点、测试日志与源码引用 |
| 日期 / 记录人 / 复查日 | 2026-07-15 / 张三 / 2026-08-15 |

<!-- 证据类型按 Claim 适配，不构成绝对强弱排序：source-verified、build-verified、
     test-observed、runtime-observed、production-observed、inferred、unverified。 -->

## 逐步记录

| # | 步骤 | 证据方式 | 真实输出（原样粘贴） | 证据类型 |
|---|---|---|---|---|
| 1 | Controller 入参校验 | 断点 | `args = CreateOrderReq(skuId=1001, qty=2)` | `test-observed` |
| 2 | OrderService#create 写 t_order | 测试日志 | `INSERT INTO t_order ... id=88213` | `test-observed` |
| 3 | 调用 InventoryService#lock | 断点（mock） | mock 返回 `LockResult(ok=true)`，真实下游未验证 | `test-observed`（仅 mock 边界内） |
| 4 | 发送 pay.result.notify | 源码阅读 | 本地无 MQ；从 OrderEventPublisher.java:31 推演 | `unverified` |

## 与静态推演的差异（本次最有价值的发现）

- 预期：create 失败时走 CancelHandler 补偿 —— **实际未发生**，异常被 GlobalExceptionAdvice 吞掉后直接返回 500，补偿逻辑三年前已被短路（OrderService.java:142 的 catch 块）。
- 已回填地图：order 模块卡片"未验证清单"减一，缺陷候选加一。

## 下一步验证手段

- 第 4 步：在测试环境接真实 MQ，或在 publisher 处加一行日志后重放请求。

## 不能推出

- 这次测试没有经过的路径并不因此不存在；
- 测试环境行为不能直接推广到生产；
- 只有给出生产环境、版本、时间窗口和采样口径后，才能标记为 `production-observed`。
