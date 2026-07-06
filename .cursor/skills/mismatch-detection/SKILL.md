---
name: mismatch-detection
description: Scan hot modules for mismatches between naming/comments and actual behavior, and produce an interview question list for domain experts instead of conclusions; backfill confirmed answers into glossary.yaml. AI as mismatch detector, not explainer. Use when code names look untrustworthy, before writing business-level documentation, or to prepare expert interviews. 对应方法论 T7 错位探测器。
---

# mismatch-detection（T7 错位探测器）

角色反转：不要问 AI"这段代码的业务含义是什么"——它只会顺着命名编。让 AI 做它能做好的事：**标记命名与行为的疑似错位**，产出一份给领域专家的访谈问题清单。业务语义的确认永远由人完成。

## 何时使用

- 热点模块的命名可疑（准备重构或写文档之前）；
- 准备领域专家访谈，需要高杠杆的问题清单；
- glossary.yaml 里 pending 条目积压，需要批量生成待确认问题。

## 成本控制（先做这个，再开始扫）

**只跑热点模块。** 从 T1 的 hotspot.csv 取 top-N（建议 10–20 个文件），不要全库扫——全库扫的产出 90% 是无人会读的噪音。冷代码的错位不值得占用专家时间。

## 四类探测模式

逐文件阅读，找以下四类，每发现一处记录 file:line：

| 模式 | 示例 |
|---|---|
| 命名与行为矛盾 | 名为 `queryXxx` 却有 INSERT/UPDATE；名为 `validate` 却顺手改了状态 |
| 注释与代码打架 | 注释说"仅 VIP 走此分支"，实际条件是 `level >= 2` |
| 职责溢出 | 名为 X 却顺手干了 Y（发消息、写另一张表、改缓存） |
| 术语冲突候选 | 同一个业务词（如"结算"）在不同模块的代码行为不同 |

判断时参考已有的 `glossary.yaml`：已澄清的术语不再提问；与 glossary 矛盾的代码行为是**高优先级发现**（要么代码错、要么 glossary 过时，都必须问）。

## 输出：访谈问题清单，不是结论

按 `templates/interview-questions.md` 的格式输出。问题质量三要求：

1. **具体**——指向 file:line，附代码行为的客观描述（"读的是 t_channel_flow 表"），不带推测性结论；
2. **可回答**——业务专家不用看代码就能回答（"X 和 Y 是同一件事吗？"），不问技术问题；
3. **高杠杆**——答案能纠正一类错位（一个术语、一个分支语义），优先于孤立细节。

每个问题标注建议的访谈对象（从 labels.yaml 的 owner、ownership.csv 的 main_author 推荐）。

## 禁止事项

- 禁止把探测结果写成结论口吻（"SettleService 实际是对账服务"❌ →"SettleService 读的全是渠道流水表，它处理的是结算还是对账？"✅）；
- 禁止跳过访谈直接回填 glossary——glossary 的每个条目必须有具名的人和日期；
- 禁止在访谈清单里混入代码改进建议（那是重构提案的事，见 `templates/refactor-proposal.md`）。

## 闭环（清单存在的意义）

```
热点模块 → AI 探测 → 访谈清单 → 人工访谈 → 回填 glossary → 注入后续所有会话
```

- 访谈答案回填 `glossary.yaml`（具名 + 日期），对应问题的 `backfilled` 标 yes；
- 下一轮探测开始前重新加载 glossary——已澄清的术语上 AI 不再犯错，这是复利的来源；
- 与 glossary 矛盾的新代码行为，作为高优先级发现进入下一轮清单。
