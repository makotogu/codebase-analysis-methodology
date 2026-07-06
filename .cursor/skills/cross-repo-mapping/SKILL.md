---
name: cross-repo-mapping
description: Analyze a multi-repository workspace (X frontend repos + Y backend repos) as one unit - build the workspace manifest, extract frontend-to-backend API edges by joining HTTP client calls against backend route tables with match confidence, merge RPC edges and shared-table matrices, and mine cross-repo co-change. Use when the system spans multiple repositories and single-repo analysis is not enough. 对应方法论 09 篇多仓库联合分析。
---

# cross-repo-mapping（09 篇：多仓库工作区）

分析的单位是**工作区**，不是仓库。单仓库技能（git-hotspot-mining / entry-census / asset-labeling / mismatch-detection）照常在每个仓库内跑；本技能负责单仓库做不了的部分：**三类跨仓库边的提取与合并**。

## 第一步：工作区清单（分析的分母）

按 `templates/repos.yaml` 建清单：每仓库的 name / path / kind（frontend | backend | bff | lib）/ service / api_client_dirs。所有仓库 clone 到同一个工作区目录。此后一切 stable ID 带仓库前缀：`web-portal::src/api/order.ts`。

## 第二步：跨仓库 T1

```bash
python3 scripts/git_miner.py <repo1> <repo2> ... --ticket-regex '[A-Z]{2,10}-\d+' --out ./t1-output
```

（脚本在 `git-hotspot-mining` 技能目录下。）多仓库模式额外产出：

- `cross-cochange-files.csv`：按工单 ID 聚合的跨仓库文件对；
- `cross-cochange-repos.csv`：仓库级耦合，`via` 列区分 ticket（可信）/ window（兜底，需 `--window-hours`，噪音大只看趋势）。

先确认 commit message 里的工单号规范并调整 `--ticket-regex`；完全没有工单规范时才启用时间窗。

## 第三步：三类跨仓库边

| 边 | 做法 | 证据等级上限 |
|---|---|---|
| RPC（后端↔后端） | `entry-census` 扫出各仓库 rpc-in/rpc-out，按 FQCN:version:group join，再与注册中心 dump 对账 | 对账后 verified |
| API（前端→后端） | 见下节 | exact 匹配 verified；fuzzy 一律 inferred |
| 共享表 | T2.5 在所有后端仓库跑，合并"表 × 服务"读写矩阵 | verified |

### API 边提取流程（本技能的核心工作）

1. **前端出口普查**：优先扫 repos.yaml 里 `api_client_dirs` 指定的 API 封装层（`src/api/`、`services/`）；再全量扫散落的裸 fetch/axios 调用——它们是"前端普查漏洞"候选，单独列出；
2. **路径归一化**：模板串与字符串拼接归一成占位符（`` `/orders/${id}` `` → `/orders/{*}`）；动态拼出整段路径的调用点标 `unverified`，列入人工验证清单；
3. **网关前缀**：先解析 nginx/网关路由配置拿到前缀剥离与转发规则——这是 join 的权威依据，不要猜；
4. **join**：与所有后端 entries.yaml 的 http 条目按 method + 归一化路径匹配，按 `templates/api-edges.yaml` 输出，match 分四级：exact / prefix / fuzzy / unmatched；
5. **对账**：有 OpenAPI/Swagger 用它做基准（前端版 actuator 对账）。双向排查：unmatched 的前端调用 → 后端已下线或漏扫；无任何前端引用的后端 http 入口 → 死接口候选（先排除 App、第三方回调、内部调用等其他调用方，未排除前只能标 dead-candidate 不能下结论）。

**BFF 仓库两侧都扫**：对前端是 provider（它的路由表参与被 join），对后端是 consumer（它的出口参与 join 别人）。

## 第四步：工作区服务地图

- 节点 = 仓库/服务；边 = 三类边 + 跨仓库 co-change，每条边带证据等级；
- glossary 是**工作区级唯一一份**——前后端术语漂移（前端"套餐" vs 后端"bundle"）是错位探测高发区，把前后端对同一 API 字段的命名差异喂给 `mismatch-detection`；
- 双读格式：服务级图通常 <50 节点，直接生成 mermaid 进 `notes/00-workspace-map.md`。

## 解读要点（AI 的归因职责）

- 前端仓库与"它页面域对应的后端"强耦合是**正常的**，不要报告为问题；
- 一个工单总要同时改 ≥2 个**后端**仓库 → 服务边界切错的铁证，进重构决策输入；
- 前端仓库总和"不属于它页面域"的后端耦合 → 接口契约混乱或 BFF 缺位信号；
- 共享表矩阵里"一张表被多个服务写" → 最高优先级的假拆证据，硬约束级别。
