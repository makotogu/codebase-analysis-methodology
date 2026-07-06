---
name: asset-labeling
description: Batch-classify every code unit of a legacy codebase into business-concept labels with confidence and evidence, run co-change consistency checks, and produce an arbitration queue plus burndown metrics. The core skill of the untangling phase (梳理即分类，对应 08 篇 Shopify 案例). Use when starting componentization or module-boundary cleanup of a monolith.
---

# asset-labeling（梳理阶段核心：全量分类标注）

梳理不是"读懂全部代码"，而是"完成一次全量分类"：给每个代码单元回答一个问题——**它属于哪个业务概念**。有分母（进度可算）、可并行、有完成态。本技能是 AI 侧的批量标注流程；人只仲裁低置信度和有争议的部分。

## 前置条件（缺一不启动）

1. **labels.yaml 已由领域干系人确认**（见 `templates/labels.yaml`）：标签来自业务概念而非技术层次，数量几十个以内。没有标签体系就开始标注 = 边分类边发明类别，结果不可用；
2. **T1 数据已产出**（用 `git-hotspot-mining` 技能）：cochange.csv 用于一致性校验；
3. 可选但强烈建议：T2.5 表映射（哪个类读写哪张表）——数据所有权是最强的归属信号。

## 单元粒度

- Java：类（FQCN）；其他语言：文件。不要用函数级（太碎）也不要用包级（太粗，一个包经常混多个概念）；
- 先过滤：生成代码、vendored 依赖、构建产物不进分母。

## 标注流程

对每个单元，按以下信号顺序推断标签，并**写出证据**：

1. **数据所有权**：读写哪些表 → 表属于哪个业务概念（最强信号）；
2. **调用关系**：被谁调用 / 调用谁（多数邻居同标签 → 大概率同标签）；
3. **入口归属**：从哪些入口可达（entries.yaml 的 claimed_purpose）；
4. **co-change 聚类**：总和谁一起变更（cochange.csv）；
5. **命名与路径**：最弱信号，只做佐证——存量系统命名经常漂移（见 glossary）。

置信度判据：

| 置信度 | 判据 |
|---|---|
| high | ≥2 个强信号（表/调用/入口）指向同一标签，且无矛盾信号 |
| medium | 单一强信号，或信号间轻微矛盾 |
| low | 只有命名/路径信号，或信号互相矛盾，或候选标签 ≥2 个 |

## 输出与状态机

按 `templates/asset-inventory.csv` 的 schema 输出。状态流转：

```
machine-proposed ──(low 置信度 / 争议)──> needs-arbitration ──(人工裁决)──> human-confirmed
```

铁律：

- `proposed_label` 只能来自 labels.yaml，**禁止发明新标签**——发现"哪个都不合适"时在 note 列写 `label-gap: 建议新增 XX`，交人裁决；
- `final_label` / `arbitrated_by` / `arbitrated_date` 是人工段，机器重跑**不得覆盖**；
- 一个单元疑似属于多个概念（如巨型 service）→ 不硬选，标 needs-arbitration 并在 note 写明是"多轴线拆分候选"——这本身就是重构信号。

## co-change 一致性校验（标注质量的自动化检查）

对 cochange.csv 中 confidence ≥ 0.5 的文件对检查标签：

- 同标签 → 一致，通过；
- 不同标签 → 二选一：**要么标签标错了，要么是真实的跨概念耦合**。两种都值得看：前者修标签，后者记入"跨组件耦合清单"（后续物理归位和棘轮的输入）。

把违反清单排序输出（confidence 降序），这是人工仲裁队列的一部分。

## 进度与燃尽

每轮标注后更新（进 `templates/metrics-baseline.md` 的棘轮表）：

- 未归类单元数（分母 - human-confirmed - 高置信 machine-proposed）；
- needs-arbitration 队列长度；
- 一致性校验违反数。

**没有数字，梳理就永远处于"快梳理完了"的状态。**

## 人机分工

- AI：全量提议 + 置信度 + 证据；一致性校验；仲裁队列排序（按热点分优先——热的先仲裁）；
- 人：只看 needs-arbitration 队列和 label-gap 提案；归属争议由 labels.yaml 里该标签的 owner 裁决。
