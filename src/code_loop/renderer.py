from __future__ import annotations

from html import escape
import json
from pathlib import Path
import re

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from .models import InvestigationAgenda, StoredAnalysis, Session


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


def render_journey_mermaid(analysis: StoredAnalysis | None) -> str:
    """Render a safe, stable Mermaid projection from journey nodes and edges."""

    if analysis is None or not analysis.draft.journey_nodes:
        return "flowchart LR\n    empty[\"尚未生成链路\"]\n"
    aliases = {node.id: f"node_{index}" for index, node in enumerate(analysis.draft.journey_nodes, start=1)}
    direction = "LR" if len(analysis.draft.journey_nodes) <= 8 else "TB"
    lines = [f"flowchart {direction}"]
    for node in analysis.draft.journey_nodes:
        label = escape(node.label, quote=True).replace("\n", " ")[:240]
        lines.append(f"    {aliases[node.id]}[{json.dumps(label, ensure_ascii=False)}]")
    for edge in analysis.draft.journey_edges:
        source = aliases.get(edge.source_id)
        target = aliases.get(edge.target_id)
        if source is None or target is None:
            continue
        relation = re.sub(r"[|<>\r\n]", " ", edge.relation).strip()[:120] or "关联"
        lines.append(f"    {source} -->|{relation}| {target}")
    return "\n".join(lines) + "\n"


def render_html_report(
    session: Session,
    analysis: StoredAnalysis | None,
    *,
    mermaid_asset: str = "../_assets/mermaid.min.js",
    agenda: InvestigationAgenda | None = None,
    related_sessions: dict[str, Session] | None = None,
) -> str:
    """Render a standalone local HTML report; repository content is always escaped."""

    mermaid = escape(render_journey_mermaid(analysis))
    focus = (agenda.primary_focus if agenda else "") or (analysis.draft.primary_focus if analysis else "") or session.question
    title = escape(focus.strip().splitlines()[0][:180] or "Code Loop 分析")
    full_question = escape(session.question)
    formatter = HtmlFormatter(cssclass="source", style="github-dark")
    sections: list[str] = []
    if analysis is None:
        sections.append("<section><h2>分析状态</h2><p>尚未生成分析。</p></section>")
    else:
        draft = analysis.draft
        sections.append(f"<section id='summary'><h2>摘要</h2><p>{escape(draft.summary)}</p></section>")
        claim_parts = ["<section id='claims'><h2>结论卡片</h2>"]
        for claim in draft.claims:
            statement = escape(claim.human_text or claim.statement)
            refs = " ".join(f"<a href='#evidence-{escape(item)}'>{escape(item)}</a>" for item in claim.evidence_ids) or "无"
            claim_parts.append(
                f"<article id='claim-{escape(claim.id)}'><h3>{escape(claim.id)} · {escape(str(claim.review_status))}</h3>"
                f"<p>{statement}</p><div class='meta'>证据等级 {escape(str(claim.evidence_level))} · "
                f"置信度 {escape(claim.confidence)} · 证据 {refs}</div></article>"
            )
        claim_parts.append("</section>")
        sections.append("".join(claim_parts))
        evidence_parts = ["<section id='evidence'><h2>证据</h2>"]
        for item in analysis.evidence:
            language = code_fence_language(item.path)
            try:
                lexer = get_lexer_by_name(language)
            except ClassNotFound:
                lexer = TextLexer()
            code = highlight(item.excerpt, lexer, formatter)
            evidence_parts.append(
                f"<article id='evidence-{escape(item.id)}'><h3>{escape(item.id)} · "
                f"{escape(item.path)}:{item.start_line}-{item.end_line}</h3>{code}</article>"
            )
        evidence_parts.append("</section>")
        sections.append("".join(evidence_parts))
        if draft.open_questions:
            questions = "".join(
                f"<li><strong>{'阻塞' if item.blocking else '待确认'}</strong> {escape(item.question)}"
                f"<br><span class='meta'>{escape(item.suggested_verification)}</span></li>"
                for item in draft.open_questions
            )
            sections.append(f"<section id='questions'><h2>未决问题</h2><ul>{questions}</ul></section>")
    if agenda and agenda.topics:
        related_sessions = related_sessions or {}
        topics = []
        for topic in agenda.topics:
            child = related_sessions.get(topic.child_session_id or "")
            link = f"<a href='../{escape(child.id, quote=True)}/report.html'>{escape(topic.question)}</a>" if child else escape(topic.question)
            topics.append(f"<li><strong>{escape(topic.status)}</strong> {link}</li>")
        sections.append(f"<section id='investigations'><h2>相关调查</h2><ul>{''.join(topics)}</ul></section>")
    pygments_css = formatter.get_style_defs(".source")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Code Loop · {title}</title>
<style>
:root{{--bg:#0d1117;--panel:#161b22;--line:#30363d;--text:#e6edf3;--muted:#8b949e;--accent:#58a6ff}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1180px;margin:auto;padding:32px}} header,section{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:20px 24px;margin-bottom:18px}}
h1{{font-size:24px;margin:0 0 10px}} h2{{font-size:19px;border-bottom:1px solid var(--line);padding-bottom:8px}} h3{{font-size:15px}} a{{color:var(--accent)}}
.meta{{color:var(--muted);font-size:13px}} article{{border-top:1px solid var(--line);padding:10px 0}} pre.mermaid{{overflow:auto;text-align:center}} pre.mermaid svg{{max-width:none!important}}
.source{{overflow:auto;border:1px solid var(--line);border-radius:7px;padding:14px;background:#0d1117}} {pygments_css}
</style></head><body><main>
<header><h1>{title}</h1><div class="meta">会话 {escape(session.id)} · 状态 {escape(session.status)} · 版本 {escape(session.current_revision_id or '尚无')} · 成本 ${session.usage.estimated_cost_usd:.6f}</div>
<details><summary>查看完整分析问题</summary><p>{full_question}</p></details></header>
<section id="journey"><h2>链路图</h2><pre class="mermaid">{mermaid}</pre><noscript><p>需要启用 JavaScript 才能渲染 Mermaid 图。</p></noscript></section>
{''.join(sections)}
</main><script src="{escape(mermaid_asset, quote=True)}"></script>
<script>mermaid.initialize({{startOnLoad:true,securityLevel:"strict",theme:"dark",flowchart:{{useMaxWidth:false}}}});</script></body></html>"""


def render_report(
    session: Session,
    analysis: StoredAnalysis | None,
    *,
    agenda: InvestigationAgenda | None = None,
    related_sessions: dict[str, Session] | None = None,
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
