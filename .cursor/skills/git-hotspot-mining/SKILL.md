---
name: git-hotspot-mining
description: Mine git history to rank refactoring hotspots (change frequency × complexity), co-change coupling, and knowledge islands (bus factor). Use when starting analysis of a large legacy codebase, prioritizing refactoring targets, finding hidden coupling, or validating asset labels. 对应方法论 T1 git-miner：热点分析、变更耦合、知识孤岛。
---

# git-hotspot-mining（T1）

git 历史挖掘是整套方法论中**普适性最强、成本最低**的工具：不依赖代码语言、对 commit message 质量没要求（只看文件集合）、零依赖、当天出结果。它是接手任何存量项目的第一步。

## 何时使用

- 接手一个陌生的存量代码库，需要回答"哪里值得先看"；
- 为重构立项收集热点/耦合证据（03 篇的三道门槛之一）；
- 为资产梳理（asset-labeling 技能）提供 co-change 一致性校验数据。

## 运行

```bash
python3 scripts/git_miner.py <repo_path> --months 12 --out ./t1-output
```

可调参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--months` | 12 | 挖掘窗口 |
| `--half-life` | 90 | 时间衰减半衰期（天）：上月的变更权重约为一年前的 8 倍 |
| `--mega-threshold` | 50 | 单 commit 涉及文件数超过此值视为巨型 commit |
| `--min-changes` | 3 | co-change 只统计变更次数 ≥ 此值的文件 |
| `--exclude` | 无 | 排除路径子串，可多次传（如 `--exclude generated --exclude vendor`） |

## 产出三张 CSV

| 文件 | 内容 | 喂给谁 |
|---|---|---|
| `hotspot.csv` | 文件 × 加权变更次数 × LOC × 热点分 | 重构候选清单、错位探测的目标模块 |
| `cochange.csv` | 总在同一 commit 出现的文件对 + confidence | 变更轴线分析、标签一致性校验 |
| `ownership.csv` | 文件 × 作者分布 × bus factor | 知识孤岛清单、访谈对象定位 |

## 三个坑（脚本已处理，但要理解）

1. **时间衰减**：一年前的变更权重应低于上月，否则历史久的文件永远霸榜。脚本用指数衰减（可调半衰期）。
2. **巨型 commit 过滤**：一次动 200 个文件的格式化/迁移 commit 会污染耦合分析。脚本对巨型 commit 在 co-change 中剔除、在热点中降权（×0.2）。
3. **rename 追踪**：脚本解析 numstat 的 `old => new` 记号并把历史归并到当前文件名。**注意**：如果项目经历过"物理归位"式的大迁移（08 篇 Shopify 案例），务必确认迁移 commit 被正确识别为巨型 commit，否则耦合数据会被迁移污染。

## 结果解读（这是 AI 的归因职责，不只是出表）

拿到三张表后，按以下模式分析并给出**带证据的归因**：

- **hotspot 高分文件** → 问"为什么频繁变更"：需求本身多变（不该动）还是抽象切错了（重构目标）。逐个查最近 5–10 个 commit 的语义再下结论；
- **cochange 高 confidence 对**：
  - 相距很远的文件对 → 隐含概念被切碎，抽象候选；
  - 一个文件与多组无关文件耦合 → 多轴线巨型模块，拆分候选；
- **ownership 中 bus factor = 1 且热点分高** → 双重风险模块，优先安排访谈和结对；
- 每条结论都引用 CSV 行号或 commit hash 作为证据，标注 `verified`（数据）与 `inferred`（归因）。

## 产出去向

- 结果 CSV 提交进仓库（纯文本进 git 原则），路径建议 `notes/t1/`；
- 热点 top-20 以 markdown 表格形式写进 `notes/00-source-map.md`（双读格式）；
- 下一步：热点模块喂给 `mismatch-detection`，co-change 喂给 `asset-labeling`。
