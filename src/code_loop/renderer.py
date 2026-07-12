from __future__ import annotations

from html import escape
import json
from pathlib import Path
import re

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from .models import AnalysisRevision, InvestigationAgenda, StoredAnalysis, Session, Synthesis
from .time_utils import format_local_timestamp


_CODE_FENCE_LANGUAGES = {
    ".bash": "bash",
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "jsx",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".md": "markdown",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "bash",
    ".sql": "sql",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zsh": "bash",
}


def code_fence_language(path: str) -> str:
    """Return a Markdown lexer name suitable for a repository file."""

    return _CODE_FENCE_LANGUAGES.get(Path(path).suffix.lower(), "text")


def _mermaid_label(value: str, limit: int) -> str:
    """Return a quoted Mermaid string safe for nodes and edge labels."""

    normalized = re.sub(r"[\r\n]+", " ", value).strip()[:limit]
    return json.dumps(escape(normalized, quote=True), ensure_ascii=False)


def render_journey_mermaid(analysis: StoredAnalysis | None) -> str:
    """Render a safe, stable Mermaid projection from journey nodes and edges."""

    if analysis is None or not analysis.draft.journey_nodes:
        return "flowchart LR\n    empty[\"尚未生成链路\"]\n"
    aliases = {node.id: f"node_{index}" for index, node in enumerate(analysis.draft.journey_nodes, start=1)}
    direction = "LR" if len(analysis.draft.journey_nodes) <= 8 else "TB"
    lines = [f"flowchart {direction}"]
    for node in analysis.draft.journey_nodes:
        lines.append(f"    {aliases[node.id]}[{_mermaid_label(node.label, 240)}]")
    for edge in analysis.draft.journey_edges:
        source = aliases.get(edge.source_id)
        target = aliases.get(edge.target_id)
        if source is None or target is None:
            continue
        relation = edge.relation.strip() or "关联"
        lines.append(f"    {source} -- {_mermaid_label(relation, 120)} --> {target}")
    return "\n".join(lines) + "\n"


def render_html_report(
    session: Session,
    analysis: StoredAnalysis | None,
    *,
    mermaid_asset: str = "../_assets/mermaid.min.js",
    agenda: InvestigationAgenda | None = None,
    related_sessions: dict[str, Session] | None = None,
    revisions: list[AnalysisRevision] | None = None,
    synthesis: Synthesis | None = None,
) -> str:
    """Render a navigable local report with one independent graph per model round."""

    focus = (agenda.primary_focus if agenda else "") or (analysis.draft.primary_focus if analysis else "") or session.question
    title = escape(focus.strip().splitlines()[0][:180] or "Code Loop 分析")
    full_question = escape(session.question)
    formatter = HtmlFormatter(cssclass="source", style="github-dark")
    model_revisions = [item for item in (revisions or []) if item.kind == "model_analysis"]
    rounds: list[tuple[str, str, str, StoredAnalysis]] = [
        (
            item.id,
            item.user_message.strip() or (session.question if index == 1 else "继续分析"),
            item.created_at,
            item.analysis,
        )
        for index, item in enumerate(model_revisions, start=1)
    ]
    if not rounds and analysis is not None:
        rounds.append(("current", session.question, analysis.updated_at, analysis))

    confirmed_count = sum(
        1
        for _, _, _, round_analysis in rounds
        for claim in round_analysis.draft.claims
        if str(claim.review_status) in {"confirmed", "amended"}
    )
    navigation: list[str] = []
    synthesis_sections: list[str] = []
    if synthesis is not None:
        active_cards = [item for item in synthesis.cards if item.status == "active"]
        card_parts: list[str] = []
        for card in active_cards:
            source_links = []
            for source_claim_id in card.source_claim_ids:
                revision_id, _, claim_id = source_claim_id.partition("::")
                source_links.append(
                    f"<a href='#claim-{escape(revision_id)}-{escape(claim_id)}'>{escape(source_claim_id)}</a>"
                )
            evidence_links = " ".join(
                f"<a href='#evidence-{escape(item)}'>{escape(item)}</a>" for item in card.evidence_ids
            ) or "无"
            card_parts.append(
                f"<article id='final-{escape(card.id)}' class='final-card'><span class='eyebrow'>{escape(card.id)}</span>"
                f"<h3>{escape(card.title)}</h3><p>{escape(card.statement)}</p>"
                f"<div class='meta'>来源 {' · '.join(source_links)}<br>证据 {evidence_links}</div></article>"
            )
        label = "最终卡片" if synthesis.status == "sealed" else "提炼草稿"
        synthesis_sections.append(
            f"<section id='final-cards'><span class='eyebrow'>{escape(synthesis.status)}</span><h2>{label}</h2>"
            f"{''.join(card_parts) or '<p>尚无可展示卡片。</p>'}</section>"
        )
        coverage = synthesis.coverage
        model_error_html = (
            f'<p class="render-error">{escape(synthesis.model_error)}</p>'
            if synthesis.model_error
            else ""
        )
        synthesis_sections.append(
            f"<section id='coverage'><h2>覆盖检查</h2><div class='stats'>"
            f"<div class='stat'><strong>{coverage.confirmed}</strong><br><span class='meta'>confirmed</span></div>"
            f"<div class='stat'><strong>{coverage.mapped}</strong><br><span class='meta'>mapped</span></div>"
            f"<div class='stat'><strong>{coverage.deferred}</strong><br><span class='meta'>deferred</span></div>"
            f"<div class='stat'><strong>{coverage.disputed}</strong><br><span class='meta'>disputed</span></div>"
            f"<div class='stat'><strong>{coverage.missing}</strong><br><span class='meta'>missing</span></div>"
            f"</div>{model_error_html}</section>"
        )
        assessment_parts: list[str] = []
        for item in synthesis.suggestions:
            preview = (
                f"<p><strong>合并预览：</strong>{escape(item.suggested_statement)}</p>"
                if item.suggested_statement else ""
            )
            assessment_parts.append(
                f"<article class='claim'><h3>{escape(item.id)} "
                f"<span class='badge'>{escape(item.status)}</span></h3>"
                f"<p><strong>{escape(item.relation)}</strong> · {escape(item.confidence)} · "
                f"候选 {escape(', '.join(item.candidate_card_ids) or '历史建议')}</p>"
                f"<p>{escape(item.rationale or '无评估理由')}</p>{preview}"
                f"<div class='meta'>冲突提示：{escape(item.conflict_hint or '无')} · AI 建议，人工裁决</div></article>"
            )
        if assessment_parts:
            synthesis_sections.append(
                "<section id='merge-assessments'><details open><summary><strong>AI 合并评估记录</strong></summary>"
                "<p class='meta'>这些内容是操作建议，不属于 Phase 0 事实源。</p>"
                f"{''.join(assessment_parts)}</details></section>"
            )
        navigation.extend(["<a href='#final-cards'>最终卡片</a>", "<a href='#coverage'>覆盖检查</a>"])
        if assessment_parts:
            navigation.append("<a href='#merge-assessments'>AI 合并评估</a>")
    navigation.append("<a href='#overview'>会话概览</a>")
    round_sections: list[str] = []
    for index, (round_id, round_question, created_at, round_analysis) in enumerate(rounds, start=1):
        anchor = f"round-{escape(round_id, quote=True)}"
        short_question = " ".join(round_question.split())[:42]
        navigation.append(f"<a href='#{anchor}'><span>第 {index} 轮</span>{escape(short_question)}</a>")
        draft = round_analysis.draft
        graph = escape(render_journey_mermaid(round_analysis))
        claim_parts: list[str] = []
        for claim in draft.claims:
            statement = escape(claim.human_text or claim.statement)
            refs = " ".join(f"<a href='#evidence-{escape(item)}'>{escape(item)}</a>" for item in claim.evidence_ids) or "无"
            status = escape(str(claim.review_status))
            claim_parts.append(
                f"<article id='claim-{escape(round_id)}-{escape(claim.id)}' class='claim {status}'>"
                f"<h3>{escape(claim.id)} <span class='badge'>{status}</span></h3><p>{statement}</p>"
                f"<div class='meta'>证据等级 {escape(str(claim.evidence_level))} · "
                f"置信度 {escape(claim.confidence)} · 证据 {refs}</div></article>"
            )
        questions = "".join(
            f"<li><strong>{'阻塞' if item.blocking else '待确认'}</strong> {escape(item.question)}"
            f"<br><span class='meta'>{escape(item.suggested_verification)}</span></li>"
            for item in draft.open_questions
        )
        claims_html = "".join(claim_parts) or '<p class="meta">本轮没有结论卡片。</p>'
        questions_html = f'<div class="questions"><h3>未决问题</h3><ul>{questions}</ul></div>' if questions else ""
        round_sections.append(
            f"<section id='{anchor}' class='round'><div class='round-head'><div>"
            f"<span class='eyebrow'>第 {index} 轮 · {escape(round_id)}</span><h2>{escape(round_question)}</h2>"
            f"</div><span class='meta'>{escape(format_local_timestamp(created_at))}</span></div>"
            f"<div class='summary'><h3>本轮摘要</h3><p>{escape(draft.summary)}</p></div>"
            f"<div class='graph'><h3>本轮链路</h3><pre class='mermaid'>{graph}</pre>"
            f"<details><summary>查看 Mermaid 源码</summary><pre class='mermaid-source'>{graph}</pre></details></div>"
            f"<div class='claims'><h3>本轮结论</h3>{claims_html}</div>"
            f"{questions_html}</section>"
        )

    evidence_parts: list[str] = []
    evidence_source = analysis or (rounds[-1][3] if rounds else None)
    if evidence_source is not None:
        for item in evidence_source.evidence:
            language = code_fence_language(item.path)
            try:
                lexer = get_lexer_by_name(language)
            except ClassNotFound:
                lexer = TextLexer()
            code = highlight(item.excerpt, lexer, formatter)
            evidence_parts.append(
                f"<details id='evidence-{escape(item.id)}' class='evidence'><summary>"
                f"<strong>{escape(item.id)}</strong> · {escape(item.path)}:{item.start_line}-{item.end_line}</summary>{code}</details>"
            )
    navigation.append("<a href='#evidence-index'>证据索引</a>")

    related_section = ""
    if agenda and agenda.topics:
        related_sessions = related_sessions or {}
        topics = []
        for topic in agenda.topics:
            child = related_sessions.get(topic.child_session_id or "")
            link = f"<a href='../{escape(child.id, quote=True)}/report.html'>{escape(topic.question)}</a>" if child else escape(topic.question)
            topics.append(f"<li><strong>{escape(topic.status)}</strong> {link}</li>")
        related_section = f"<section id='investigations'><h2>相关调查</h2><ul>{''.join(topics)}</ul></section>"
        navigation.append("<a href='#investigations'>相关调查</a>")
    revision_items = "".join(
        f"<li><code>{escape(item.id)}</code> · {escape(item.kind)} · "
        f"{escape(format_local_timestamp(item.created_at))}</li>"
        for item in (revisions or [])
    ) or "<li>尚无版本</li>"
    audit_section = (
        f"<section id='versions-cost'><h2>版本与成本</h2><p>累计估算 API 成本："
        f"<strong>${session.usage.estimated_cost_usd:.6f}</strong></p><ul>{revision_items}</ul></section>"
    )
    navigation.append("<a href='#versions-cost'>版本与成本</a>")
    pygments_css = formatter.get_style_defs(".source")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Code Loop · {title}</title>
<style>
:root{{--bg:#0b0f14;--panel:#121820;--panel2:#18212b;--line:#2a3542;--text:#e6edf3;--muted:#8b9aaa;--accent:#58a6ff;--ok:#3fb950}}
*{{box-sizing:border-box}} html{{scroll-behavior:smooth}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.layout{{display:grid;grid-template-columns:260px minmax(0,1fr);max-width:1500px;margin:auto;min-height:100vh}} aside{{position:sticky;top:0;height:100vh;padding:28px 18px;border-right:1px solid var(--line);background:#0d131a;overflow:auto}}
.brand{{font-weight:700;font-size:18px;margin:0 10px 22px}} nav{{display:grid;gap:5px}} nav a{{display:grid;color:var(--muted);text-decoration:none;padding:9px 10px;border-radius:7px}} nav a span{{color:var(--text);font-weight:600}} nav a:hover{{background:var(--panel2);color:var(--text)}}
main{{min-width:0;padding:32px 40px 80px}} header,section{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 26px;margin-bottom:22px;scroll-margin-top:20px}}
h1{{font-size:26px;margin:0 0 10px}} h2{{font-size:21px;margin:4px 0 16px}} h3{{font-size:15px;margin:16px 0 8px}} a{{color:var(--accent)}} .meta{{color:var(--muted);font-size:13px}} .eyebrow{{color:var(--accent);font-size:12px;text-transform:uppercase;letter-spacing:.08em}}
.stats{{display:flex;gap:18px;flex-wrap:wrap;margin-top:16px}} .stat{{background:var(--panel2);padding:10px 14px;border-radius:8px}} .round-head{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;border-bottom:1px solid var(--line);margin-bottom:18px}} .round-head h2{{margin-top:5px}}
.summary,.graph,.claims,.questions{{margin-top:18px}} article.claim{{border-top:1px solid var(--line);padding:12px 0}} .badge{{color:var(--muted);font-size:11px;border:1px solid var(--line);border-radius:999px;padding:2px 7px}} .confirmed .badge,.amended .badge{{color:var(--ok);border-color:#2b6b3c}} .final-card{{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:16px;margin:12px 0}}
pre.mermaid{{overflow:auto;text-align:center;background:#0d131a;border:1px solid var(--line);border-radius:8px;padding:18px}} pre.mermaid svg{{max-width:none!important}} .mermaid-source{{overflow:auto;white-space:pre-wrap;color:var(--muted)}} details{{margin-top:10px}} details.evidence{{border-top:1px solid var(--line);padding:12px 0}}
.source{{overflow:auto;border:1px solid var(--line);border-radius:7px;padding:14px;background:#0d1117}} .render-error{{color:#ff7b72;border:1px solid #7d312d;padding:10px;border-radius:7px}} {pygments_css}
@media(max-width:900px){{.layout{{display:block}} aside{{position:sticky;height:auto;z-index:5;border-right:0;border-bottom:1px solid var(--line);padding:10px 14px}} .brand{{display:none}} nav{{display:flex;overflow:auto}} nav a{{min-width:max-content}} main{{padding:20px 14px 60px}} header,section{{padding:18px}} .round-head{{display:block}}}}
</style></head><body><div class="layout"><aside><div class="brand">Code Loop</div><nav>{''.join(navigation)}</nav></aside><main>
<header id="overview"><span class="eyebrow">调查报告</span><h1>{title}</h1><div class="meta">会话 {escape(session.id)} · 状态 {escape(session.status)} · 当前版本 {escape(session.current_revision_id or '尚无')} · 成本 ${session.usage.estimated_cost_usd:.6f}</div>
<p>{full_question}</p><div class="stats"><div class="stat"><strong>{len(rounds)}</strong><br><span class="meta">独立分析轮次</span></div><div class="stat"><strong>{confirmed_count}</strong><br><span class="meta">已确认结论</span></div><div class="stat"><strong>{len(evidence_source.evidence) if evidence_source else 0}</strong><br><span class="meta">证据片段</span></div></div></header>
{''.join(synthesis_sections)}
{''.join(round_sections) or '<section><h2>分析状态</h2><p>尚未生成分析。</p></section>'}
<section id="evidence-index"><h2>证据索引</h2><p class="meta">各轮 Claim 共用这份证据目录；展开后查看源码片段。</p>{''.join(evidence_parts) or '<p>尚无证据。</p>'}</section>
{related_section}
{audit_section}
</main></div><script src="{escape(mermaid_asset, quote=True)}"></script>
<script>mermaid.initialize({{startOnLoad:false,securityLevel:"strict",theme:"dark",flowchart:{{useMaxWidth:false}}}});mermaid.run({{querySelector:".mermaid"}}).catch(function(error){{document.querySelectorAll("pre.mermaid").forEach(function(node){{if(!node.querySelector("svg")){{node.classList.add("render-error");node.insertAdjacentHTML("beforebegin","<p class='render-error'>Mermaid 渲染失败，源码已保留在下方。</p>")}}}});console.error(error)}});</script></body></html>"""


def render_report(
    session: Session,
    analysis: StoredAnalysis | None,
    *,
    agenda: InvestigationAgenda | None = None,
    related_sessions: dict[str, Session] | None = None,
    synthesis: Synthesis | None = None,
    revisions: list[AnalysisRevision] | None = None,
) -> str:
    focus = (agenda.primary_focus if agenda else "") or (analysis.draft.primary_focus if analysis else "") or session.question
    lines = [
        f"# Code Loop: {focus}",
        "",
        f"- 会话：`{session.id}`",
        f"- 仓库：`{session.repository}`",
        f"- Git HEAD：`{session.git_head or 'not a git repository'}`",
        f"- 状态：`{session.status}`",
        f"- 当前版本：`{session.current_revision_id or '尚无'}`",
        f"- 估算 API 成本：`${session.usage.estimated_cost_usd:.6f}`",
        f"- 分析起点：`{', '.join(session.scope_items) if session.scope_items else '自然语言定位'}`",
        "",
    ]
    if focus.strip() != session.question.strip():
        lines.extend(["<details>", "<summary>查看原始问题</summary>", "", session.question, "", "</details>", ""])
    if session.scope_expansions:
        lines.extend(["## 分析范围扩展", ""])
        lines.extend(f"- `{path}`" for path in session.scope_expansions)
        lines.append("")
    if synthesis is not None:
        lines.extend([
            "## 最终卡片" if synthesis.status == "sealed" else "## 提炼草稿",
            "",
            f"覆盖：confirmed={synthesis.coverage.confirmed} / mapped={synthesis.coverage.mapped} / "
            f"deferred={synthesis.coverage.deferred} / disputed={synthesis.coverage.disputed} / missing={synthesis.coverage.missing}",
            "",
        ])
        for card in synthesis.cards:
            if card.status != "active":
                continue
            lines.extend([
                f"### `{card.id}` {card.title}",
                "",
                card.statement,
                "",
                f"- 来源 Claim：{', '.join(f'`{item}`' for item in card.source_claim_ids)}",
                f"- Evidence：{', '.join(f'`{item}`' for item in card.evidence_ids) or '无'}",
                "",
            ])
        if synthesis.suggestions:
            lines.extend([
                "<details>",
                "<summary>AI 合并评估记录</summary>",
                "",
                "> 以下内容是模型提供的操作建议，不属于 Phase 0 事实源。",
                "",
            ])
            for item in synthesis.suggestions:
                lines.extend([
                    f"### `{item.id}` · `{item.relation}` · `{item.status}`",
                    "",
                    f"- 候选 Card：{', '.join(f'`{card_id}`' for card_id in item.candidate_card_ids) or '历史建议'}",
                    f"- 置信度：`{item.confidence}`",
                    f"- 理由：{item.rationale or '无'}",
                    f"- 冲突提示：{item.conflict_hint or '无'}",
                    f"- 合并预览：{item.suggested_statement or '不建议直接合并'}",
                    "",
                ])
            lines.extend(["</details>", ""])
    if analysis is None:
        return "\n".join(lines + ["尚未生成分析。", ""])
    draft = analysis.draft
    lines.extend(["## 摘要", "", draft.summary, "", "## 链路", ""])
    if draft.journey_nodes:
        lines.extend(["```mermaid", render_journey_mermaid(analysis).rstrip(), "```", ""])
    else:
        lines.extend(["尚未确认链路节点。", ""])
    lines.extend(["## 结论卡片", ""])
    for claim in draft.claims:
        statement = claim.human_text or claim.statement
        lines.extend([
            f"### `{claim.id}`",
            "",
            statement,
            "",
            f"- 证据等级：`{claim.evidence_level}`",
            f"- 置信度：`{claim.confidence}`",
            f"- 裁决状态：`{claim.review_status}`",
            f"- 证据引用：{', '.join(f'`{item}`' for item in claim.evidence_ids) or '无'}",
            "",
        ])
    lines.extend(["", "## 证据", ""])
    for evidence in analysis.evidence:
        language = code_fence_language(evidence.path)
        lines.extend([
            f"### {evidence.id}: `{evidence.path}:{evidence.start_line}-{evidence.end_line}`",
            "",
            f"```{language}",
            evidence.excerpt,
            "```",
            "",
        ])
    lines.extend(["## 未决问题", ""])
    for question in draft.open_questions:
        marker = "阻塞" if question.blocking else "待确认"
        lines.append(f"- **{marker}**：{question.question}（建议：{question.suggested_verification}）")
    lines.append("")
    model_revisions = [item for item in (revisions or []) if item.kind == "model_analysis"]
    if len(model_revisions) > 1:
        lines.extend(["## 各轮独立分析", ""])
        for index, revision in enumerate(model_revisions, start=1):
            round_draft = revision.analysis.draft
            lines.extend([
                f"### 第 {index} 轮 `{revision.id}`",
                "",
                f"**本轮问题**：{revision.user_message or session.question}",
                "",
                round_draft.summary,
                "",
                "```mermaid",
                render_journey_mermaid(revision.analysis).rstrip(),
                "```",
                "",
            ])
            for claim in round_draft.claims:
                lines.append(
                    f"- `{revision.id}::{claim.id}` · `{claim.review_status}` · "
                    f"{claim.human_text or claim.statement} · Evidence: "
                    f"{', '.join(f'`{item}`' for item in claim.evidence_ids) or '无'}"
                )
            lines.append("")
    if agenda and agenda.topics:
        lines.extend(["## 相关调查", ""])
        related_sessions = related_sessions or {}
        for topic in agenda.topics:
            child = related_sessions.get(topic.child_session_id or "")
            label = topic.question.replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
            target = f"[{label}](../{child.id}/report.md)" if child else label
            lines.append(f"- **{topic.status}** · {target}")
        lines.append("")
    return "\n".join(lines)
