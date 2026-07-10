# 05 Java/Spring 落地指南

本篇适用于 Java/Spring 仓库的 Phase 1–3。输入是仓库、构建配置、Spring 配置和获批运行环境；产物是入口/表映射、覆盖差异、观测记录与可达性估计。它不能仅凭注解扫描或 profiler 样本推出生产完整覆盖。

## 一、T2 入口普查：扫描 + 限定环境对账

静态扫描 HTTP 注解与 `RouterFunction`、`@Scheduled`/Quartz/外部任务配置、MQ consumer、事件监听和 servlet/XML 注册。每个适配器把支持机制和盲区写入 `coverage-ledger.yaml`。

`@Profile`、`@Conditional*`、动态 Bean 和配置中心会让静态声明与目标环境不同。Actuator 的 mappings endpoint 只有在依赖、endpoint exposure、安全授权和目标配置均启用时才可使用；它反映该实例当时的 handler mappings，不代表所有部署。对账结果使用 static-only、runtime-only、matched 和 unknown，不称“完整路由真相”。参考 [Spring Boot Actuator endpoints](https://docs.spring.io/spring-boot/reference/actuator/endpoints.html)。

## 二、T4：先盘点已有指标，再决定插桩

Micrometer/Actuator 是否提供 `http.server.requests` 取决于 Spring Boot 版本、依赖、观测配置和 endpoint 暴露。先查询目标实例和配置，再决定是否需要 web filter、`@Scheduled` 切面或 MQ interceptor。新增观测必须遵守 `analysis-policy.yaml`，评估标签基数、隐私、性能和回滚。

OpenTelemetry Java agent 的 zero-code instrumentation 可以覆盖许多受支持框架和库，但自动埋点通常集中在库/框架边界，并不自动解释应用内部业务步骤或所有自定义协议。参考 [OpenTelemetry zero-code instrumentation](https://opentelemetry.io/docs/concepts/instrumentation/zero-code/) 与 [Java agent 文档](https://opentelemetry.io/docs/zero-code/java/agent/)。

## 三、T5：字节码图仍是静态过近似

`jdeps` 可生成 jar/package/class 依赖，但不是运行调用图。单实现接口可生成高置信候选，多实现接口保守全连并标 `inferred`，反射、AOP、ServiceLoader、JNI 和动态代理列为断点。CI/test 运行采集的边标 `test-observed`，不能推广到未覆盖测试或生产。

## 四、JFR 与 async-profiler 的边界

JFR 是事件记录框架；能获得哪些事件取决于 JDK、配置和事件启用情况。async-profiler 是采样 profiler。二者都可提供运行信号，但**不等同于代码覆盖率**，样本缺失不证明方法未执行。参考 [JFR API](https://docs.oracle.com/en/java/javase/17/docs/api/jdk.jfr/jdk/jfr/package-summary.html) 与 [async-profiler](https://github.com/async-profiler/async-profiler)。

类加载记录或长窗口采样未出现的类只能称“未观察代码候选”。删除前至少检查：明确分母和窗口、静态/反射引用、条件配置、构建与测试、目标环境观测、负责人和回滚方案。

## 五、T2.5：表映射是候选数据边

MyBatis XML、JPA `@Entity/@Table` 和 SQL 字符串可形成“表 ↔ 代码”候选矩阵。动态 SQL、存储过程、同义词、ORM 命名策略和运行时 schema 会造成断点。共享写表是数据所有权调查信号，不自动证明服务边界错误。

## 六、进入下一阶段

Phase 1 退出前，扫描器、环境、配置、差异与未知项要进入 coverage ledger；Phase 2 退出前，关键观察要记录部署版本、时间窗口、采样和原始引用；Phase 3 只消费保留口径与不确定性的信号。产物维护人和 stale 条件写入 metadata，Spring/JDK/agent 版本或配置变化后复查。
