# 模板索引：梳理阶段的产出物格式

这个目录把方法论（01–08 篇）中的产出物固化为可直接复制使用的模板。每个模板都标注了它属于哪个环节、由谁维护（机器段/人工段）。

## 使用顺序

按照接手项目的时间线：

| 顺序 | 模板 | 环节 | 维护方 |
|---|---|---|---|
| 第 1 周 | [metrics-baseline.md](metrics-baseline.md) | 开工前立基线 | 人工 |
| 第 1 周 | [glossary.yaml](glossary.yaml) | 证据体系（02） | 人工段，机器不得覆盖 |
| 第 1–2 周 | [entries.yaml](entries.yaml) | 入口普查 T2（04） | 机器段 + human_note 人工段 |
| 第 2–3 周 | [interview-questions.md](interview-questions.md) | 错位探测 T7（02） | 机器生成，人工回答 |
| 第 2–3 周 | [trace-record.md](trace-record.md) | 验证（01 第六节） | 人工/Agent 共同 |
| 梳理启动 | [labels.yaml](labels.yaml) | 标签体系（08） | 人工，干系人共同确认 |
| 梳理期 | [asset-inventory.csv](asset-inventory.csv) | 全量分类标注（08） | 机器提议 + 人工仲裁 |
| 地图成型 | [module-card.md](module-card.md) | 地图卡片 T6（04/07） | 机器段 + 人工段分离 |
| 立项时 | [refactor-proposal.md](refactor-proposal.md) | 重构提案（03） | 人工，三道证据门槛 |

## asset-inventory.csv 列说明

CSV 无法内嵌注释，列语义如下：

| 列 | 说明 |
|---|---|
| `unit_id` | 稳定 ID，建议用类/文件的全限定名 |
| `path` | 文件路径（相对仓库根） |
| `unit_type` | class / file / package |
| `proposed_label` | 机器提议的标签，必须来自 labels.yaml，禁止发明新标签 |
| `confidence` | high / medium / low（判据见 asset-labeling skill） |
| `evidence` | 提议依据：调用了谁、被谁调用、操作哪些表（分号分隔） |
| `cochange_cluster` | 来自 T1 cochange.csv 的聚类编号，用于一致性校验 |
| `status` | `machine-proposed` / `needs-arbitration` / `human-confirmed` |
| `final_label` | 人工仲裁后的最终标签；仅人可写 |
| `arbitrated_by` / `arbitrated_date` | 仲裁人与日期，具名可审计 |
| `note` | 争议说明、备注 |

## 两条铁律

1. **机器段与人工段分离**：所有模板中标注"人工段"的字段（`human_note`、`final_label`、glossary 全文、module-card 的 HUMAN SECTION），机器重跑任何分析都不得覆盖。
2. **每条论断带证据**：没有 file:line 或命令输出支撑的内容，一律标 `inferred` 或 `unverified`，不得写成结论口吻。

配套的 Agent 技能见 `.cursor/skills/`：`git-hotspot-mining`、`entry-census`、`asset-labeling`、`mismatch-detection`。
