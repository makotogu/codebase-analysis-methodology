from __future__ import annotations

import json
from pathlib import Path

from code_loop.config import Config
from code_loop.engine import AnalysisEngine
from code_loop.models import (
    AnalysisDraft,
    Claim,
    Evidence,
    EvidenceLevel,
    JourneyEdge,
    JourneyNode,
    OpenQuestion,
    StoredAnalysis,
    SuggestedInvestigation,
)
from code_loop.repository_tools import RepositoryTools
from code_loop.storage import SessionStore


class NoopClient:
    def complete(self, **_kwargs):  # pragma: no cover - these tests inspect context only
        raise AssertionError("model call was not expected")


def _draft(summary: str = "摘要") -> AnalysisDraft:
    return AnalysisDraft(status="needs_user", summary=summary, next_action="review")


def test_agenda_locks_primary_focus_and_deduplicates_deferred_topics(tmp_path: Path) -> None:
    config = Config()
    store = SessionStore(tmp_path, config.repository, "agenda-session")
    store.create("分析启动、成本和输入法问题", None)
    draft = AnalysisDraft(
        status="needs_user",
        summary="先分析启动链路",
        primary_focus="工具是如何启动并进入工作台的？",
        suggested_investigations=[
            SuggestedInvestigation(question="成本为什么不更新？", rationale="独立计费链路"),
            SuggestedInvestigation(question="成本为什么不更新？", rationale="重复建议"),
        ],
        next_action="review",
    )

    store.save_analysis(draft, [])
    agenda = store.load_agenda()
    assert agenda.primary_focus == "工具是如何启动并进入工作台的？"
    assert [(item.id, item.question, item.status) for item in agenda.topics] == [
        ("topic_1", "成本为什么不更新？", "queued")
    ]

    replacement = _draft("后续轮次")
    replacement.primary_focus = "模型试图改变主问题"
    replacement.suggested_investigations = [SuggestedInvestigation(question="成本为什么不更新？")]
    store.save_analysis(replacement, [])
    assert replacement.primary_focus == agenda.primary_focus
    assert len(store.load_agenda().topics) == 1


def test_revisions_are_immutable_and_working_save_does_not_create_one(tmp_path: Path) -> None:
    config = Config()
    store = SessionStore(tmp_path, config.repository, "revision-session")
    store.create("版本测试", None)
    store.save_analysis(_draft("第一版"), [])
    assert store.list_revisions() == []

    first = store.create_revision(kind="model_analysis", user_message="首次")
    store.save_analysis(_draft("第二版"), [])
    second = store.create_revision(kind="model_analysis", user_message="继续")

    assert first.id == "rev_0001"
    assert second.parent_revision_id == first.id
    assert store.load_revision(first.id).analysis.draft.summary == "第一版"
    assert store.load_revision(second.id).analysis.draft.summary == "第二版"
    assert store.load_session().current_revision_id == second.id


def test_child_session_links_topic_and_revalidates_inherited_evidence(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("def entry():\n    return service()\n", encoding="utf-8")
    config = Config()
    parent = SessionStore(tmp_path, config.repository, "parent")
    parent.create("宽问题", None)
    payload = RepositoryTools(tmp_path, config.repository).read_lines("app.py", 1, 2)
    evidence = RepositoryTools(tmp_path, config.repository).evidence_from_read(payload, "ev_1")
    draft = _draft()
    draft.primary_focus = "主线"
    draft.suggested_investigations = [SuggestedInvestigation(question="深入 service", evidence_ids=["ev_1"])]
    parent.save_analysis(draft, [evidence])
    revision = parent.create_revision(kind="model_analysis")

    child_store, child = parent.create_child_session(
        "深入 service",
        None,
        evidence_ids=["ev_1"],
        source_topic_id="topic_1",
        parent_revision_id=revision.id,
    )
    agenda = parent.load_agenda()
    assert agenda.topics[0].status == "started"
    assert agenda.topics[0].child_session_id == child.id
    assert f"../{child.id}/report.md" in parent.report_path.read_text(encoding="utf-8")

    engine = AnalysisEngine(
        config=config,
        tools=RepositoryTools(tmp_path, config.repository),
        store=child_store,
        client=NoopClient(),
    )
    assert [item.id for item in engine._load_inherited_evidence(child)] == ["ev_1"]

    source.write_text("def entry():\n    return other()\n", encoding="utf-8")
    assert engine._load_inherited_evidence(child) == []

    child.status = "completed"
    child_store.save_session(child)
    child_store.mark_parent_topic_completed(child)
    assert parent.load_agenda().topics[0].status == "completed"


def test_working_memory_is_bounded_and_excludes_full_previous_analysis(tmp_path: Path) -> None:
    config = Config()
    store = SessionStore(tmp_path, config.repository, "memory-session")
    session = store.create("宽问题", None)
    evidence = [
        Evidence(
            id=f"ev_{number}",
            path=f"file_{number}.py",
            start_line=1,
            end_line=20,
            excerpt=(f"evidence-{number} " * 500),
            content_hash=f"hash-{number}",
            source="read_lines",
        )
        for number in range(1, 56)
    ]
    claims = [
        Claim(
            id=f"claim_{number}",
            statement=(f"claim {number} " * 100),
            category="code_fact",
            evidence_ids=[f"ev_{number}"],
            evidence_level=EvidenceLevel.INFERRED,
            confidence="medium",
        )
        for number in range(1, 26)
    ]
    nodes = [JourneyNode(id=f"node_{number}", label=f"node {number}", kind="function") for number in range(35)]
    edges = [
        JourneyEdge(source_id=f"node_{number % 35}", target_id=f"node_{(number + 1) % 35}", relation="calls")
        for number in range(45)
    ]
    questions = [OpenQuestion(id=f"q{number}", question=f"question {number}", suggested_verification="check") for number in range(12)]
    previous = StoredAnalysis(
        draft=AnalysisDraft(
            status="needs_user",
            summary="s" * 10_000,
            claims=claims,
            journey_nodes=nodes,
            journey_edges=edges,
            open_questions=questions,
            next_action="review",
        ),
        evidence=evidence,
    )
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=NoopClient())
    engine.evidence = evidence

    messages = engine._messages(session, previous, [], "继续", context_evidence_ids=["ev_25"])
    context = json.loads(messages[1]["content"])

    assert "previous_analysis" not in context
    assert len(context["working_memory"]["summary"]) == 2_000
    assert len(context["working_memory"]["claims"]) == 20
    assert len(context["working_memory"]["journey_nodes"]) == 30
    assert len(context["working_memory"]["journey_edges"]) == 40
    assert len(context["working_memory"]["open_questions"]) == 8
    assert len(context["evidence_catalog"]) == 50
    assert len(context["available_evidence"]) == 4
    assert context["available_evidence"][0]["id"] == "ev_25"
    assert "revisions" not in context
    legacy_context = {
        "previous_analysis": previous.draft.model_dump(mode="json"),
        "available_evidence": [item.model_dump(mode="json") for item in evidence[-8:]],
    }
    assert len(json.dumps(context, ensure_ascii=False)) < len(json.dumps(legacy_context, ensure_ascii=False))


def test_orphan_child_remains_usable_without_parent(tmp_path: Path) -> None:
    config = Config()
    child_store = SessionStore(tmp_path, config.repository, "orphan")
    child = child_store.create(
        "孤立子调查",
        None,
        parent_session_id="missing-parent",
        parent_revision_id="rev_0001",
        seed_evidence_ids=["ev_1"],
    )
    engine = AnalysisEngine(
        config=config,
        tools=RepositoryTools(tmp_path, config.repository),
        store=child_store,
        client=NoopClient(),
    )

    assert engine._load_inherited_evidence(child) == []
    assert child_store.load_session().question == "孤立子调查"
    assert "inherited_evidence_unavailable" in child_store.events_path.read_text(encoding="utf-8")
