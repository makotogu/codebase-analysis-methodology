from pathlib import Path

import pytest

from code_loop.config import Config
from code_loop.llm import Completion
from code_loop.models import (
    AnalysisDraft,
    Claim,
    Evidence,
    EvidenceLevel,
    ReviewStatus,
    Usage,
)
from code_loop.storage import SessionStore
from code_loop.synthesis import SynthesisEngine


class SuggestionClient:
    def __init__(self, content: str, usage: Usage | None = None):
        self.content = content
        self.usage = usage or Usage()
        self.calls = 0
        self.tools: list[list[dict]] = []
        self.messages: list[list[dict]] = []

    def complete(self, *, model, messages, tools):
        self.calls += 1
        self.tools.append(tools)
        self.messages.append(messages)
        return Completion(self.content, [], self.usage)


def _analysis(summary: str, statement: str, evidence_id: str):
    return AnalysisDraft(
        status="needs_user",
        summary=summary,
        claims=[Claim(
            id="claim_1",
            statement=statement,
            category="code_fact",
            evidence_ids=[evidence_id],
            evidence_level=EvidenceLevel.VERIFIED,
            confidence="high",
            review_status=ReviewStatus.CONFIRMED,
        )],
        next_action="review",
    )


def _store_with_two_rounds(tmp_path: Path):
    config = Config()
    store = SessionStore(tmp_path, config.repository, "synthesis-session")
    session = store.create("分析加载链路", None)
    evidence = [
        Evidence(id="ev_1", path="a.py", start_line=1, end_line=1, excerpt="1: a", content_hash="a", source="read_lines"),
        Evidence(id="ev_2", path="b.py", start_line=1, end_line=1, excerpt="1: b", content_hash="b", source="read_lines"),
    ]
    store.save_analysis(_analysis("第一轮", "配置按需加载", "ev_1"), evidence, apply_corrections=False)
    store.create_revision(kind="model_analysis", user_message="首次")
    store.save_analysis(_analysis("第二轮", "首次访问时读取配置", "ev_2"), evidence, apply_corrections=False)
    store.create_revision(kind="model_analysis", user_message="什么时候加载？")
    return config, store, store.load_session()


def test_deterministic_synthesis_covers_every_confirmed_claim(tmp_path: Path) -> None:
    _config, store, _session = _store_with_two_rounds(tmp_path)

    synthesis = store.build_synthesis()

    assert synthesis.coverage.model_dump() == {
        "confirmed": 2, "mapped": 2, "deferred": 0, "disputed": 0, "missing": 0,
    }
    assert [item.source_claim_ids for item in synthesis.cards] == [
        ["rev_0001::claim_1"], ["rev_0002::claim_1"],
    ]
    assert store.synthesis_path.exists()


def test_model_only_proposes_and_user_accepts_merge(tmp_path: Path) -> None:
    config, store, session = _store_with_two_rounds(tmp_path)
    synthesis = store.build_synthesis()
    client = SuggestionClient(
        '{"assessments":[{"candidate_card_ids":["card_0001","card_0002"],"relation":"duplicate",'
        '"confidence":"high","suggested_title":"配置按需加载",'
        '"suggested_statement":"配置在首次访问时读取。","rationale":"同一事实"}]}',
        Usage(prompt_cache_miss_tokens=100, completion_tokens=20),
    )

    suggested = SynthesisEngine(config, store, client).assess_cards(session, synthesis, ["card_0001", "card_0002"])

    assert client.tools == [[]]
    request = client.messages[0][1]["content"]
    assert '"selected_cards"' in request
    assert '"card_id": "card_0001"' in request
    assert '"card_id": "card_0002"' in request
    assert "journey_nodes" not in request
    assert suggested.suggestions[0].status == "proposed"
    assert len([item for item in suggested.cards if item.status == "active"]) == 2
    accepted = store.accept_merge(suggested.suggestions[0].id)
    active = [item for item in accepted.cards if item.status == "active"]
    assert len(active) == 1
    assert active[0].source_claim_ids == ["rev_0001::claim_1", "rev_0002::claim_1"]
    assert active[0].evidence_ids == ["ev_1", "ev_2"]
    assert accepted.coverage.missing == 0
    assert session.usage.estimated_cost_usd > 0


def test_invalid_model_output_keeps_base_cards(tmp_path: Path) -> None:
    config, store, session = _store_with_two_rounds(tmp_path)
    synthesis = store.build_synthesis()
    client = SuggestionClient("not json")

    result = SynthesisEngine(config, store, client).assess_cards(session, synthesis, ["card_0001", "card_0002"])

    assert client.calls == 2
    assert result.model_error
    assert result.coverage.missing == 0
    assert len([item for item in result.cards if item.status == "active"]) == 2


def test_seal_ignores_unresolved_assessments_and_creates_snapshot(tmp_path: Path) -> None:
    config, store, session = _store_with_two_rounds(tmp_path)
    synthesis = store.build_synthesis()
    client = SuggestionClient(
        '{"assessments":[{"candidate_card_ids":["card_0001","card_0002"],"relation":"duplicate",'
        '"confidence":"medium","suggested_title":"合并","suggested_statement":"合并内容","rationale":"重复"}]}'
    )
    SynthesisEngine(config, store, client).assess_cards(session, synthesis, ["card_0001", "card_0002"])

    sealed = store.seal_synthesis()
    assert sealed.status == "sealed"
    assert sealed.suggestions[0].status == "ignored"
    assert (store.syntheses_directory / "syn_0001.json").exists()
    assert store.load_session().status == "completed"


def test_new_round_marks_existing_synthesis_stale(tmp_path: Path) -> None:
    _config, store, _session = _store_with_two_rounds(tmp_path)
    store.build_synthesis()

    store.save_analysis(_analysis("第三轮", "新结论", "ev_2"), [], apply_corrections=False, new_round=True)

    assert store.load_synthesis_or_none().status == "stale"


def test_accepting_merge_invalidates_overlapping_suggestion(tmp_path: Path) -> None:
    _config, store, _session = _store_with_two_rounds(tmp_path)
    synthesis = store.build_synthesis()
    from code_loop.models import MergeSuggestion
    synthesis.suggestions = [
        MergeSuggestion(id="merge_0001", source_claim_ids=["rev_0001::claim_1", "rev_0002::claim_1"], suggested_title="A", suggested_statement="A"),
        MergeSuggestion(id="merge_0002", source_claim_ids=["rev_0001::claim_1", "rev_0002::claim_1"], suggested_title="B", suggested_statement="B"),
    ]
    store.save_synthesis(synthesis)

    result = store.accept_merge("merge_0001")

    assert next(item for item in result.suggestions if item.id == "merge_0002").status == "invalidated"


def test_deferred_claim_requires_reason_and_remains_covered(tmp_path: Path) -> None:
    _config, store, _session = _store_with_two_rounds(tmp_path)
    store.build_synthesis()
    with pytest.raises(ValueError, match="reason"):
        store.set_claim_disposition("rev_0001::claim_1", "deferred", "")

    result = store.set_claim_disposition("rev_0001::claim_1", "deferred", "需要运行时验证")
    assert result.coverage.deferred == 1
    assert result.coverage.missing == 0


def test_seal_rejects_tampered_evidence(tmp_path: Path) -> None:
    _config, store, _session = _store_with_two_rounds(tmp_path)
    synthesis = store.build_synthesis()
    synthesis.cards[0].evidence_ids.append("ev_unknown")
    store.save_synthesis(synthesis)

    with pytest.raises(ValueError, match="Evidence"):
        store.seal_synthesis()


def test_assessment_requires_two_to_eight_active_cards(tmp_path: Path) -> None:
    config, store, session = _store_with_two_rounds(tmp_path)
    synthesis = store.build_synthesis()
    engine = SynthesisEngine(config, store, SuggestionClient('{"assessments":[]}'))

    with pytest.raises(ValueError, match="2–8"):
        engine.assess_cards(session, synthesis, ["card_0001"])
    synthesis.cards[1].status = "superseded"
    with pytest.raises(ValueError, match="有效卡片"):
        engine.assess_cards(session, synthesis, ["card_0001", "card_0002"])


def test_conflict_assessment_cannot_be_accepted(tmp_path: Path) -> None:
    config, store, session = _store_with_two_rounds(tmp_path)
    synthesis = store.build_synthesis()
    client = SuggestionClient(
        '{"assessments":[{"candidate_card_ids":["card_0001","card_0002"],"relation":"conflict",'
        '"confidence":"high","rationale":"适用边界相反","conflict_hint":"需要人工复查"}]}'
    )
    result = SynthesisEngine(config, store, client).assess_cards(session, synthesis, ["card_0001", "card_0002"])

    assert result.suggestions[0].relation == "conflict"
    with pytest.raises(ValueError, match="冲突"):
        store.accept_merge(result.suggestions[0].id)


def test_card_edit_invalidates_pending_assessment(tmp_path: Path) -> None:
    config, store, session = _store_with_two_rounds(tmp_path)
    synthesis = store.build_synthesis()
    client = SuggestionClient(
        '{"assessments":[{"candidate_card_ids":["card_0001","card_0002"],"relation":"complementary",'
        '"confidence":"medium","suggested_title":"组合","suggested_statement":"组合结论","rationale":"互补"}]}'
    )
    result = SynthesisEngine(config, store, client).assess_cards(session, synthesis, ["card_0001", "card_0002"])

    edited = store.edit_card("card_0001", title="新标题", statement="新内容")
    assert next(item for item in edited.suggestions if item.id == result.suggestions[0].id).status == "invalidated"


def test_merged_card_can_be_assessed_and_merged_again(tmp_path: Path) -> None:
    config, store, session = _store_with_two_rounds(tmp_path)
    evidence = store.load_analysis_or_none().evidence
    store.save_analysis(_analysis("第三轮", "配置没有内存缓存", "ev_2"), evidence, apply_corrections=False)
    store.create_revision(kind="model_analysis", user_message="是否缓存？")
    synthesis = store.build_synthesis(force=True)
    first_client = SuggestionClient(
        '{"assessments":[{"candidate_card_ids":["card_0001","card_0002"],"relation":"duplicate",'
        '"confidence":"high","suggested_title":"按需加载","suggested_statement":"配置首次访问时加载。","rationale":"重复"}]}'
    )
    first = SynthesisEngine(config, store, first_client).assess_cards(session, synthesis, ["card_0001", "card_0002"])
    merged_once = store.accept_merge(first.suggestions[0].id)
    assert next(item for item in merged_once.cards if item.id == "card_0004").status == "active"

    second_client = SuggestionClient(
        '{"assessments":[{"candidate_card_ids":["card_0003","card_0004"],"relation":"complementary",'
        '"confidence":"medium","suggested_title":"配置加载策略","suggested_statement":"配置按需加载且不使用内存缓存。","rationale":"互补"}]}'
    )
    second = SynthesisEngine(config, store, second_client).assess_cards(
        session, merged_once, ["card_0003", "card_0004"]
    )
    merged_twice = store.accept_merge(second.suggestions[-1].id)
    active = [item for item in merged_twice.cards if item.status == "active"]

    assert len(active) == 1
    assert active[0].source_claim_ids == [
        "rev_0003::claim_1", "rev_0001::claim_1", "rev_0002::claim_1",
    ]
