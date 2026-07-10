from code_loop.models import AnalysisDraft, Claim, Evidence, EvidenceLevel, JourneyEdge, JourneyNode, Session, StoredAnalysis
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
    assert "node_1 -->|calls break| node_2" in mermaid
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert 'securityLevel:"strict"' in html
