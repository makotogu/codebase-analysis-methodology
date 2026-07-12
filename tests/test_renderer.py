from code_loop.models import AnalysisDraft, AnalysisRevision, Claim, Evidence, EvidenceLevel, FinalCard, JourneyEdge, JourneyNode, MergeSuggestion, ReviewStatus, Session, StoredAnalysis, Synthesis, SynthesisCoverage
from code_loop.renderer import code_fence_language, render_html_report, render_journey_mermaid, render_report


def test_renderer_contains_evidence_anchor() -> None:
    analysis = StoredAnalysis(
        draft=AnalysisDraft(
            status="needs_user",
            summary="Summary",
            claims=[Claim(id="c1", statement="Fact", category="code_fact", evidence_ids=["e1"], evidence_level=EvidenceLevel.VERIFIED, confidence="high")],
            next_action="Review",
        ),
        evidence=[Evidence(id="e1", path="app.py", start_line=2, end_line=2, excerpt="2: call()", content_hash="abc", source="read_lines")],
    )
    report = render_report(Session(id="s1", repository="/repo", question="Question", scope_items=["app.py"], scope_mode="mixed", scope_expansions=["service.py"]), analysis)
    assert "`app.py:2-2`" in report
    assert "Fact" in report
    assert "`service.py`" in report
    assert "### `c1`" in report
    assert "证据等级：`verified`" in report
    assert "```python\n2: call()" in report


def test_code_fence_language_falls_back_for_unknown_files() -> None:
    assert code_fence_language("config/settings.yaml") == "yaml"
    assert code_fence_language("Dockerfile") == "text"


def test_mermaid_and_html_escape_untrusted_labels() -> None:
    analysis = StoredAnalysis(
        draft=AnalysisDraft(
            status="needs_user",
            summary="<script>alert(1)</script>",
            journey_nodes=[
                JourneyNode(id='bad"] --> hacked', label='<script>bad</script> "quoted"', kind="function"),
                JourneyNode(id="safe", label="Safe", kind="function"),
            ],
            journey_edges=[JourneyEdge(source_id='bad"] --> hacked', target_id="safe", relation="calls|break")],
            next_action="review",
        )
    )
    mermaid = render_journey_mermaid(analysis)
    html = render_html_report(Session(id="s", repository="/repo", question="<unsafe>"), analysis)

    assert "hacked" not in mermaid
    assert 'node_1 -- "calls|break" --> node_2' in mermaid
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert 'securityLevel:"strict"' in html


def test_mermaid_quotes_method_call_edge_labels() -> None:
    analysis = StoredAnalysis(
        draft=AnalysisDraft(
            status="needs_user",
            summary="Summary",
            journey_nodes=[
                JourneyNode(id="source", label="Service", kind="function"),
                JourneyNode(id="target", label="Store", kind="function"),
            ],
            journey_edges=[
                JourneyEdge(source_id="source", target_id="target", relation='调用 readAll() → read() 和 "decode"')
            ],
            next_action="review",
        )
    )

    mermaid = render_journey_mermaid(analysis)

    assert 'node_1 -- "调用 readAll() → read() 和 &quot;decode&quot;" --> node_2' in mermaid


def test_html_report_renders_independent_rounds_with_navigation() -> None:
    first = StoredAnalysis(
        draft=AnalysisDraft(
            status="needs_user",
            summary="首轮完整结构",
            journey_nodes=[JourneyNode(id="a", label="入口", kind="controller")],
            claims=[Claim(
                id="claim_1",
                statement="首轮结论",
                category="code_fact",
                evidence_ids=["ev_1"],
                evidence_level=EvidenceLevel.VERIFIED,
                confidence="high",
                review_status=ReviewStatus.CONFIRMED,
            )],
            next_action="review",
        ),
        evidence=[Evidence(id="ev_1", path="app.py", start_line=1, end_line=1, excerpt="1: entry()", content_hash="a", source="read_lines")],
    )
    second = StoredAnalysis(
        draft=AnalysisDraft(
            status="needs_user",
            summary="第二轮局部结构",
            journey_nodes=[JourneyNode(id="b", label="文件存储", kind="store")],
            claims=[Claim(
                id="claim_1",
                statement="第二轮同名 ID 结论",
                category="code_fact",
                evidence_ids=["ev_1"],
                evidence_level=EvidenceLevel.INFERRED,
                confidence="medium",
            )],
            next_action="review",
        ),
        evidence=first.evidence,
    )
    revisions = [
        AnalysisRevision(id="rev_0001", kind="model_analysis", user_message="", analysis=first),
        AnalysisRevision(id="rev_0002", parent_revision_id="rev_0001", kind="model_analysis", user_message="文件何时加载？", analysis=second),
    ]

    html = render_html_report(Session(id="s", repository="/repo", question="数据库如何启动？"), second, revisions=revisions)

    assert html.count("class='mermaid'") == 2
    assert "第 1 轮" in html and "第 2 轮" in html
    assert "数据库如何启动？" in html
    assert "文件何时加载？" in html
    assert "#round-rev_0001" in html and "#round-rev_0002" in html
    assert "首轮完整结构" in html and "第二轮局部结构" in html
    assert "claim-rev_0001-claim_1" in html and "claim-rev_0002-claim_1" in html
    assert "证据索引" in html


def test_reports_label_ai_merge_assessments_as_non_factual_advice() -> None:
    session = Session(id="s", repository="/repo", question="配置如何加载？")
    synthesis = Synthesis(
        cards=[FinalCard(id="card_0001", title="配置", statement="配置按需加载", source_claim_ids=["rev_0001::claim_1"])],
        suggestions=[MergeSuggestion(
            id="merge_0001",
            candidate_card_ids=["card_0001", "card_0002"],
            source_claim_ids=["rev_0001::claim_1", "rev_0002::claim_1"],
            relation="conflict",
            confidence="high",
            rationale="适用边界不同",
            conflict_hint="需要人工复查",
        )],
        coverage=SynthesisCoverage(confirmed=1, mapped=1),
    )

    markdown = render_report(session, None, synthesis=synthesis)
    html = render_html_report(session, None, synthesis=synthesis)

    assert "AI 合并评估记录" in markdown
    assert "不属于 Phase 0 事实源" in markdown
    assert "merge_0001" in html and "conflict" in html
    assert "AI 建议，人工裁决" in html
