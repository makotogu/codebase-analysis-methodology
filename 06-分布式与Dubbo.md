# 06 分布式与 Dubbo：跨服务证据与 trace 边界

本篇适用于 Phase 1–3 的 Dubbo/分布式工作区。输入是 provider/consumer 声明、配置、注册中心快照和可用 telemetry；产物是跨服务候选边、限定环境对账、trace 观察和断点清单。

## 一、静态 RPC 边

扫描 `@DubboService`/XML provider 与 `@DubboReference`/XML consumer，以接口 FQCN + version + group + protocol/config scope 作为 join key。配置占位符无法解析、泛化调用、跨语言、自定义 proxy 和动态路由标为 `inferred` 或 `unverified`，不得伪造成精确边。

## 二、注册中心对账是环境快照

注册中心 dump 只能证明某注册中心、namespace/cluster、时间点和权限范围内观察到的 provider/consumer。静态有而快照无可能是环境差异、临时下线、延迟或死引用候选；快照有而静态无可能是扫描盲区、其他版本、泛化/跨语言调用。只有记录环境、版本、窗口和获取方式后，匹配边才可标 `runtime-observed` 或 `production-observed`；它不能升级为无作用域的“真相”。

## 三、trace 与自动埋点

OTel Java agent 可为受支持框架自动创建 span 和传播上下文，但能力受 agent/Dubbo 版本、类加载、配置和自定义协议影响。先做版本兼容矩阵和灰度验证；自定义 serialization、网关、异步线程、消息桥接和 generic invocation 都要列为断点。参考 [OpenTelemetry Java agent](https://opentelemetry.io/docs/zero-code/java/agent/)。

采样必须进入证据元数据：head/tail 策略、比例、错误/慢请求规则、Collector 丢弃和保留周期。trace 未出现某边不能证明边不存在。业务键进入 attributes 前先做数据分级、脱敏、基数和保留评估；用户 ID、订单号和 payload 可能受隐私与安全策略限制。

## 四、收口点与降级方案

Dubbo provider/consumer Filter 可生成 entry ID、耗时、结果和调用方观测。correlation ID 的生成、透传、冲突、重试和日志脱敏要定义清楚。自动 agent 验证成功后可减少自定义插桩，但不能假定上下文传播在所有协议上自动成立。

## 五、跨仓库信号的边界

- 工单 ID 聚合的共同变更仍是 `change-coupling signal`，受提交规范影响；
- 发布时间窗口只是低置信近似，需保留窗口、作者/发布批次过滤和验证任务；
- 共享表读写是所有权调查信号，动态 SQL 和跨 schema 访问可能漏检。

“总是一起改”不能单独证明服务边界错误，必须结合需求语义、运行暴露度和至少一个独立信号。

## 六、进入下一阶段

Phase 1 的退出条件是静态 join、注册快照、差异和协议断点都可审计；Phase 2 的退出条件是关键边有适配 observed 证据或明确验证任务；Phase 3 只输出候选。注册中心、部署版本、agent 配置、采样或隐私策略变化时产物过期，由平台维护人和服务维护人共同复查。
