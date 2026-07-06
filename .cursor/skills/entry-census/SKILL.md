---
name: entry-census
description: Build a complete inventory of system entry points (HTTP routes, scheduled jobs, MQ consumers, event listeners, Dubbo provider/consumer edges) as entries.yaml, then reconcile against runtime truth (Spring actuator, registry dump). Use when mapping a legacy system, before reachability analysis, or when hunting dead endpoints. 对应方法论 T2/T2.6 入口普查。
---

# entry-census（T2 / T2.6）

入口普查产出系统地图的骨架：一张**完备的**入口清单。它的完备性来自一个事实——入口必然在某处注册，注册机制就那么几种，全部可静态扫描。

## 何时使用

- 建图阶段，画端到端旅程图之前（先知道有哪些入口，再选旅程）；
- 可达性传播（T5）之前（入口是权重传播的源头）;
- 排查死代码（静态有、运行时没有的入口）。

## 输出

按 `templates/entries.yaml` 的 schema 输出，关键要求：

- `id` 全局唯一且稳定（与 T4 收口点日志的 entry_id 一致，两边才能 join）；
- 每条必须有 `evidence:`（file:line）——扫不出证据的条目标 `inferred`；
- `human_note` 是人工段，重跑普查时**必须保留原值**。

## 扫描清单（Java/Spring 栈，按四 + 二类）

| 类型 | 扫什么 |
|---|---|
| http | `@RequestMapping` / `@GetMapping` 系列；WebFlux `RouterFunction`；老工程的 web.xml、Servlet/Filter 注册 |
| cron | `@Scheduled`；Quartz JobDetail/Trigger bean；xxl-job `@XxlJob`；k8s CronJob manifest；crontab |
| mq | `@KafkaListener` / `@RabbitListener` / `@RocketMQMessageListener`；手工 consumer 注册代码 |
| event | `@EventListener`；`ApplicationListener` 实现类 |
| rpc-in | Dubbo `@DubboService`（老版本 `@Service`）、`<dubbo:service>` XML |
| rpc-out | Dubbo `@DubboReference`、`<dubbo:reference>` —— 出口普查，"我依赖谁"的静态边 |

实现要点：

- 用文本/AST 扫描（grep、JavaParser、tree-sitter 均可），**不要求工程能编译通过**；
- Dubbo 的 version/group 经常写在 properties/yaml/配置中心，**配置文件必须纳入解析**，否则跨服务 join 会连错边；
- 自研框架的奇怪注册方式允许用 LLM 兜底分类，但必须回填 file:line 证据。

## 对账（把 inferred 升级为 verified 的关键步骤）

静态清单做完不算完，必须和运行时真相 diff：

1. **HTTP**：有 spring-boot-actuator 则拉 `/actuator/mappings`（运行时真实生效的完整路由表）；
2. **Dubbo**：dump 注册中心（Zookeeper/Nacos，或 Dubbo Admin）的 provider/consumer 注册关系；
3. **对账规则**：
   - 静态有、运行时没有 → `runtime_status: dead-candidate`（条件装配未激活或死代码。Spring 的 `@Profile` / `@ConditionalOn...` 是常见原因）；
   - 运行时有、静态没有 → `runtime_status: gap`，**普查漏洞**：动态注册、泛化调用、扫描器没覆盖的机制——补扫描规则；
   - 两边都有 → `active`。

## 完备性自检

- 拿 T4 收口点日志（或 access log）反查：日志里出现过、清单里没有的入口 = 漏洞；
- 泛化调用（Dubbo generic invocation）、反射路由是静态扫描的已知断点，显式列出并标 `unverified`，不要假装完备。

## 产出去向

- `entries.yaml` 提交进仓库（建议 `notes/t2/`）；
- 跨服务场景：所有仓库的 rpc-in/rpc-out 按 `接口FQCN:version:group` join，产出 `service-call-graph.yaml`（T2.6），再与注册中心 dump 对账；
- 下一步：入口清单 + 收口点权重 → 可达性传播（T5）→ 模块热力图。
