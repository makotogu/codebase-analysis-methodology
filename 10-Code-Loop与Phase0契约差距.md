# 10 Code Loop 与 Phase 0 契约差距评估

本篇是本轮方法论文档改造后的独立差距评估，不修改 Code Loop 实现。评估基于当前 `src/code_loop` 的模型、引擎、存储和六视图工作台。

## 一、当前已经满足

| Phase 0 契约 | 当前能力 |
|---|---|
| 一个主问题 | `primary_focus` 锁定主线，独立问题进入 investigation agenda |
| 源码引用 | Claim 引用 Evidence ID，Evidence 记录 file:line 和内容 hash |
| 未决项 | open questions、阻塞澄清和后续继续分析 |
| 可审计迭代 | `analysis.json` 当前快照、不可变 revisions、`events.jsonl` |
| 范围可扩展 | 可从可选 scope 起步并记录 `scope_expansions` |
| 人工裁决 | Claim confirm/reject/amend，裁决状态进入报告 |
| 可阅读投影 | Markdown、Mermaid 源码和本地 HTML；六视图为 Overview/Graph/Claims/Evidence/Activity/Revisions |

这些能力足以支持“围绕一个问题形成局部源码候选和后续调查输入”。

## 二、仍存在的契约差距

1. **证据枚举较粗**：当前 `verified/inferred/unverified` 只能在方法论侧把 `verified` 兼容解释为 `source-verified`，不能表达 build/test/runtime/production observed。
2. **provenance 不完整**：Evidence 有路径、行号和 hash，会话有 workspace，但尚未对每份长期产物统一记录工具版本、固定 commit、扫描配置、环境、时间窗口、新鲜度和 owner。
3. **负面证据没有结构化约束**：模型提示可表达未知，但数据模型尚不能强制负面 Claim 携带分母、覆盖方法、环境、窗口和盲区。
4. **业务轴未独立建模**：人工裁决不等同于 `business-confirmed/disputed/stale`；缺少多确认人、适用范围和复查日期。
5. **正式交接仍靠人工**：没有把 session/revision 导出为 coverage ledger 差异、运行验证任务或 glossary 访谈候选的中间合同。
6. **analysis policy 尚未成为运行前门**：远程模型的数据分级、排除路径、脱敏、保留与审计未由统一 policy 契约驱动。

## 三、方法论上的处理

在实现升级前，Code Loop 报告必须加以下解释：

- `verified` 只代表当前 workspace 中文件引用成立，按 `source-verified` 使用；
- 线上执行、全量覆盖、业务含义和不存在性仍需后续阶段验证；
- 导入长期事实源前固定 commit、重查 file:line/hash，并补 artifact metadata；
- 子调查和 revision 只用于局部调查审计，不自动合并为综合事实。

## 四、未来实现优先级（不在本轮执行）

1. 先增加 session-level provenance 与 analysis policy 前置检查；
2. 再增加 Phase 0 export manifest，把 Claim 映射为 source candidate / runtime task / business question；
3. 最后在保持旧 JSON 兼容的前提下增加双轴证据模型。

不建议先实现 observed 类型的 UI 标签而没有采集合同；那只会把新词汇变成新的误导。进入下一阶段的条件是导出候选已由相应工具重新验证。该差距文档由 Code Loop owner 维护，数据模型或导出能力变化时复查。
