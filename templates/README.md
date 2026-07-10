# 模板索引：梳理阶段的产出物格式

这个目录把 [00 总控篇](../00-方法论边界与阶段门.md) 与专题篇中的产出物固化为可直接复制使用的模板。每个长期产物必须内联或引用 provenance，并区分机器段与人工段。

## 使用顺序

按照接手项目的时间线：

| 顺序 | 模板 | 环节 | 维护方 |
|---|---|---|---|
| 分析前 | [analysis-policy.yaml](analysis-policy.yaml) | 数据分级、远程模型、脱敏与审计策略 | 安全/负责人 |
| 每个产物 | [artifact-metadata.yaml](artifact-metadata.yaml) | 工具、commit、范围、窗口和过期条件 | 机器生成 + 人工定责 |
| Phase 1 | [coverage-ledger.yaml](coverage-ledger.yaml) | 声明分母与静态/运行对账 | 机器段 + 人工声明 |
| Phase 3–4 | [demand-signals.yaml](demand-signals.yaml) | 历史需求、路线图和监管信号 | 产品/领域负责人 |
| 第 1 周 | [metrics-baseline.md](metrics-baseline.md) | 决策前立基线 | 人工 |
| 第 1 周 | [glossary.yaml](glossary.yaml) | 证据体系（02） | 人工段，机器不得覆盖 |
| 第 1–2 周 | [entries.yaml](entries.yaml) | 入口普查 T2（04） | 机器段 + human_note 人工段 |
| 第 2–3 周 | [interview-questions.md](interview-questions.md) | 错位探测 T7（02） | 机器生成，人工回答 |
| 第 2–3 周 | [trace-record.md](trace-record.md) | 验证（01 第六节） | 人工/Agent 共同 |
| 梳理启动 | [labels.yaml](labels.yaml) | 标签体系（08） | 人工，干系人共同确认 |
| 梳理期 | [asset-inventory.csv](asset-inventory.csv) | 全量分类标注（08） | 机器提议 + 人工仲裁 |
| 梳理期 | [asset-inventory.metadata.yaml](asset-inventory.metadata.yaml) | asset inventory 的 CSV 侧车 provenance | 机器生成 + 人工定责 |
| 地图成型 | [module-card.md](module-card.md) | 地图卡片 T6（04/07） | 机器段 + 人工段分离 |
| 立项时 | [refactor-proposal.md](refactor-proposal.md) | 重构提案（03） | 人工，三道证据门槛 |

多仓库工作区（09）追加两个模板：

| 时机 | 模板 | 环节 | 维护方 |
|---|---|---|---|
| 多仓库分析第一步 | [repos.yaml](repos.yaml) | 工作区清单（09） | 人工段 |
| API 边提取后 | [api-edges.yaml](api-edges.yaml) | 前端→后端边（09） | 机器段 + human_note 人工段 |

## asset-inventory.csv 列说明

CSV 无法内嵌注释，列语义如下：

| 列 | 说明 |
|---|---|
| `unit_id` | 稳定 ID，建议用类/文件的全限定名 |
| `aliases` / `previous_id` / `lineage` | 移动、拆分或迁移时的稳定 ID 沿革 |
| `path` | 文件路径（相对仓库根） |
| `unit_type` | class / file / package |
| `proposed_label` | 机器提议的标签，必须来自 labels.yaml，禁止发明新标签 |
| `confidence` | high / medium / low（判据见 asset-labeling skill） |
| `evidence_type` / `evidence_reference` | 新证据枚举与具体引用；历史 `verified` 只兼容解释为 `source-verified` |
| `change_coupling_cluster` | 来自 T1 change-coupling signal 的聚类编号，用于一致性校验 |
| `status` | `machine-proposed` / `needs-arbitration` / `human-confirmed` |
| `final_label` | 人工仲裁后的最终标签；仅人可写 |
| `arbitrated_by` / `arbitrated_date` | 仲裁人与日期，具名可审计 |
| `note` | 争议说明、备注 |

## 两条铁律

1. **机器段与人工段分离**：所有模板中标注"人工段"的字段（`human_note`、`final_label`、glossary 全文、module-card 的 HUMAN SECTION），机器重跑任何分析都不得覆盖。
2. **每条论断带适配证据**：使用 `source-verified`、`build-verified`、`test-observed`、`runtime-observed`、`production-observed`、`inferred` 或 `unverified`，并记录环境、commit、时间窗口、引用、获取方式和新鲜度。
3. **负面证据有分母**：未扫描或未观察到默认不能证明不存在；负面结论必须同时声明分母、覆盖方法、环境、窗口和盲区。
4. **CSV 使用侧车 metadata**：例如 `asset-inventory.csv` 配套 `asset-inventory.metadata.yaml`，结构复制自 `artifact-metadata.yaml`。

配套的 Agent 技能见 `.cursor/skills/`：`git-hotspot-mining`、`entry-census`、`asset-labeling`、`mismatch-detection`、`cross-repo-mapping`（多仓库）。
