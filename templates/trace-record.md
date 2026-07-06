# 追踪记录：{journey_name}

<!-- 用法（01 篇第六节 / 02 篇第四节）：
     以流为锚点——一条端到端可观察旅程（一个请求 / 一条 SQL / 一个定时任务）。
     纪律：永远不要把预期输出当成真实输出写进记录。
     跑不起来就标"未运行"，推演就标"推演"。
     一份诚实标注的低等级追踪，价值远高于一份看似完整的编造记录。 -->

## 元信息

| 项 | 值 |
|---|---|
| 旅程 | POST /api/v1/orders → 订单落库 → 库存锁定 → MQ 通知 |
| 整体证据等级 | 部分追踪（第 1–3 步已验证，第 4 步被阻塞） |
| 环境 | 本地最小环境：order-service + 内存 H2 + mock inventory |
| 日期 / 记录人 | 2026-07-15 / 张三 |

<!-- 整体等级取值（从高到低）：
     真实调试追踪 > 日志插桩追踪 > 源码推演追踪 > 部分追踪 -->

## 逐步记录

| # | 步骤 | 证据方式 | 真实输出（原样粘贴） | 等级 |
|---|---|---|---|---|
| 1 | Controller 入参校验 | 断点 | `args = CreateOrderReq(skuId=1001, qty=2)` | 真实调试 |
| 2 | OrderService#create 写 t_order | 日志插桩 | `INSERT INTO t_order ... id=88213` | 日志插桩 |
| 3 | 调用 InventoryService#lock | 断点（mock） | mock 返回 `LockResult(ok=true)`，真实下游未验证 | 部分 |
| 4 | 发送 pay.result.notify | 源码推演 | 本地无 MQ；从 OrderEventPublisher.java:31 推演 | 推演·unverified |

## 与静态推演的差异（本次最有价值的发现）

- 预期：create 失败时走 CancelHandler 补偿 —— **实际未发生**，异常被 GlobalExceptionAdvice 吞掉后直接返回 500，补偿逻辑三年前已被短路（OrderService.java:142 的 catch 块）。
- 已回填地图：order 模块卡片"未验证清单"减一，缺陷候选加一。

## 下一步验证手段

- 第 4 步：在测试环境接真实 MQ，或在 publisher 处加一行日志后重放请求。
