from pathlib import Path

import json

import pytest

from code_loop.config import Config, ModelConfig, RepositoryConfig
from code_loop.engine import AnalysisEngine
from code_loop.llm import Completion, ToolCall
from code_loop.models import AnalysisDraft, Correction, Evidence, OpenQuestion, Usage
from code_loop.repository_tools import RepositoryTools
from code_loop.storage import SessionStore


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, model, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Completion(None, [ToolCall("call_1", "read_lines", {"path": "app.py", "start_line": 1, "end_line": 3})], Usage())
        return Completion(
            '{"status":"needs_user","summary":"入口调用服务。","journey_nodes":[{"id":"entry","label":"entry","kind":"function","evidence_ids":["ev_1"]}],"journey_edges":[],"claims":[{"id":"claim_1","statement":"entry 调用了 service。","category":"code_fact","evidence_ids":["ev_1"],"evidence_level":"verified","confidence":"high"}],"open_questions":[],"next_action":"请确认结论"}',
            [],
            Usage(),
        )


FINAL_EMPTY_ANALYSIS = (
    '{"status":"needs_user","summary":"已基于现有信息完成。","journey_nodes":[],"journey_edges":[],'
    '"claims":[],"open_questions":[],"next_action":"review"}'
)


class SequenceClient:
    def __init__(self, responses: list[Completion]) -> None:
        self.responses = responses
        self.models: list[str] = []
        self.messages: list[list[dict]] = []

    def complete(self, *, model, messages, tools):
        self.models.append(model)
        self.messages.append(messages.copy())
        return self.responses[len(self.models) - 1]


def test_engine_persists_evidence_and_human_amendment(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def entry():\n    return service()\n", encoding="utf-8")
    config = Config()
    store = SessionStore(tmp_path, config.repository, "test-session")
    session = store.create("entry 的调用链是什么？", None)
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=FakeClient())
    draft = engine.analyze(session)
    stored = store.save_analysis(draft, engine.evidence)
    assert stored.evidence[0].path == "app.py"
    assert [item.id for item in store.list_revisions()] == ["rev_0001"]

    store.append_correction(Correction(claim_id="claim_1", verdict="amend", amended_statement="entry 通过 service 完成处理"))
    stored = store.save_analysis(draft, stored.evidence)
    assert stored.draft.claims[0].human_text == "entry 通过 service 完成处理"


def test_changed_evidence_is_not_reused_as_verified(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def entry():\n    return service()\n", encoding="utf-8")
    config = Config()
    store = SessionStore(tmp_path, config.repository, "stale-session")
    session = store.create("entry 的调用链是什么？", None)
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=FakeClient())
    store.save_analysis(engine.analyze(session), engine.evidence)
    (tmp_path / "app.py").write_text("def entry():\n    return other_service()\n", encoding="utf-8")

    resumed = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=FakeClient())
    previous = store.load_analysis_or_none()
    assert previous is not None
    fresh = resumed._fresh_evidence(previous)
    assert fresh == []
    assert previous.draft.claims[0].review_status.value == "stale"


def test_reading_outside_selected_seed_is_recorded(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def entry():\n    return service()\n", encoding="utf-8")
    (tmp_path / "selected.py").write_text("# start here\n", encoding="utf-8")
    config = Config()
    store = SessionStore(tmp_path, config.repository, "scope-session")
    session = store.create("entry 的调用链是什么？", None, scope_items=["selected.py"], scope_mode="mixed")
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=FakeClient())
    engine.analyze(session)
    assert session.scope_expansions == ["app.py"]


def test_engine_emits_persisted_progress_events(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def entry():\n    return service()\n", encoding="utf-8")
    config = Config()
    store = SessionStore(tmp_path, config.repository, "progress-session")
    session = store.create("entry 的调用链是什么？", None)
    progress: list[str] = []
    engine = AnalysisEngine(
        config=config,
        tools=RepositoryTools(tmp_path, config.repository),
        store=store,
        client=FakeClient(),
        progress=lambda kind, payload: progress.append(kind),
    )
    engine.analyze(session)
    logged = store.events_path.read_text(encoding="utf-8")
    assert "analysis_started" in progress
    assert "tool_call_started" in progress
    assert "analysis_completed" in progress
    assert "model_call_started" in logged
    assert "tool_call_finished" in logged


def test_duplicate_tool_call_is_skipped_within_one_run(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def entry():\n    return 1\n", encoding="utf-8")
    call = lambda call_id: ToolCall(call_id, "read_lines", {"path": "app.py", "start_line": 1, "end_line": 2})
    client = SequenceClient([
        Completion(None, [call("call_1")], Usage()),
        Completion(None, [call("call_2")], Usage()),
        Completion(
            '{"status":"needs_user","summary":"完成","journey_nodes":[],"journey_edges":[],'
            '"claims":[{"id":"c1","statement":"存在入口","category":"code_fact","evidence_ids":["ev_1"],'
            '"evidence_level":"verified","confidence":"high"}],"open_questions":[],"next_action":"review"}',
            [],
            Usage(),
        ),
    ])
    config = Config(model=ModelConfig(stagnation_threshold=3))
    store = SessionStore(tmp_path, config.repository, "duplicate-session")
    session = store.create("入口在哪里？", None)
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=client)

    engine.analyze(session)

    assert [item.id for item in engine.evidence] == ["ev_1"]
    events = [json.loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines()]
    finished = [event for event in events if event["type"] == "tool_call_finished"]
    assert [event["status"] for event in finished] == ["success", "duplicate"]


def test_flash_stagnation_upgrades_to_pro(tmp_path: Path) -> None:
    client = SequenceClient([
        Completion(None, [ToolCall("c1", "read_lines", {"path": "missing-1.py"})], Usage()),
        Completion(None, [ToolCall("c2", "read_lines", {"path": "missing-2.py"})], Usage()),
        Completion(FINAL_EMPTY_ANALYSIS, [], Usage()),
    ])
    model = ModelConfig(max_agent_steps=6, flash_max_steps=5, stagnation_threshold=2)
    config = Config(model=model)
    store = SessionStore(tmp_path, config.repository, "stagnation-upgrade")
    session = store.create("分析缺失入口", None)
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=client)

    engine.analyze(session)

    assert client.models == [model.flash_model, model.flash_model, model.pro_model]
    logged = store.events_path.read_text(encoding="utf-8")
    assert '"reason_code": "stagnation"' in logged


def test_flash_step_limit_upgrades_before_next_request(tmp_path: Path) -> None:
    client = SequenceClient([
        Completion(None, [ToolCall("c1", "list_files", {"glob": "*.py"})], Usage()),
        Completion(None, [ToolCall("c2", "list_files", {"glob": "src/*.py"})], Usage()),
        Completion(FINAL_EMPTY_ANALYSIS, [], Usage()),
    ])
    model = ModelConfig(max_agent_steps=5, flash_max_steps=2, stagnation_threshold=4)
    config = Config(model=model)
    store = SessionStore(tmp_path, config.repository, "step-upgrade")
    session = store.create("分析项目", None)
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=client)

    engine.analyze(session)

    assert client.models == [model.flash_model, model.flash_model, model.pro_model]
    assert '"reason_code": "flash_step_limit"' in store.events_path.read_text(encoding="utf-8")


def test_multiple_tools_in_one_response_count_as_one_step(tmp_path: Path) -> None:
    client = SequenceClient([
        Completion(
            None,
            [
                ToolCall("c1", "list_files", {"glob": "*.py"}),
                ToolCall("c2", "search_text", {"query": "entry"}),
            ],
            Usage(),
        ),
        Completion(FINAL_EMPTY_ANALYSIS, [], Usage()),
    ])
    config = Config()
    store = SessionStore(tmp_path, config.repository, "multi-tool-step")
    session = store.create("分析项目", None)
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=client)

    engine.analyze(session)

    events = [json.loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines()]
    starts = [event for event in events if event["type"] == "model_call_started"]
    assert [event["step"] for event in starts] == [1, 2]


def test_pro_stall_requests_convergence_then_stops(tmp_path: Path) -> None:
    client = SequenceClient([
        Completion(None, [ToolCall("c1", "read_lines", {"path": "missing-1.py"})], Usage()),
        Completion(None, [ToolCall("c2", "read_lines", {"path": "missing-2.py"})], Usage()),
        Completion(None, [ToolCall("c3", "list_files", {"glob": "**/*"})], Usage()),
    ])
    model = ModelConfig(max_agent_steps=6, flash_max_steps=5, stagnation_threshold=1)
    config = Config(model=model)
    store = SessionStore(tmp_path, config.repository, "pro-stall")
    session = store.create("分析项目", None)
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=client)

    with pytest.raises(RuntimeError, match="分析未能收敛"):
        engine.analyze(session)

    logged = store.events_path.read_text(encoding="utf-8")
    assert '"type": "convergence_requested"' in logged
    assert '"type": "analysis_stalled"' in logged
    assert any("Stop calling tools" in message.get("content", "") for message in client.messages[-1])


def test_analysis_save_generates_local_report_artifacts(tmp_path: Path) -> None:
    config = Config()
    store = SessionStore(tmp_path, config.repository, "artifact-session")
    session = store.create("生成本地图", None)
    store.save_analysis(
        AnalysisDraft(
            status="needs_user",
            summary="摘要",
            journey_nodes=[],
            next_action="review",
        ),
        [],
    )

    assert store.report_path.exists()
    assert store.journey_path.exists()
    assert store.html_report_path.exists()
    assert "../_assets/mermaid.min.js" in store.html_report_path.read_text(encoding="utf-8")
    assert (store.directory.parent / "_assets" / "mermaid.min.js").stat().st_size > 3_000_000
    assert SessionStore.list_sessions(tmp_path, config.repository)[0].id == session.id


def test_recent_evidence_context_uses_configurable_larger_excerpt(tmp_path: Path) -> None:
    repository_config = RepositoryConfig(max_evidence_context_chars=40)
    config = Config(repository=repository_config)
    store = SessionStore(tmp_path, repository_config, "context-session")
    session = store.create("上下文测试", None)
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, repository_config), store=store, client=FakeClient())
    engine.evidence = [
        Evidence(
            id="ev_1",
            path="large.py",
            start_line=1,
            end_line=100,
            excerpt="x" * 200,
            content_hash="abc",
            source="read_lines",
        )
    ]

    messages = engine._messages(session, None, [], "继续")
    context = json.loads(messages[1]["content"])

    assert context["available_evidence"][0]["excerpt"] == "x" * 40


def test_question_and_priority_clue_policy_are_sent_without_preflight_call(tmp_path: Path) -> None:
    config = Config()
    store = SessionStore(tmp_path, config.repository, "question-clues")
    question = "tui.py 里 run_analysis_worker 遇到 TimeoutError 时为什么会卡住？"
    session = store.create(question, None)
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=FakeClient())

    messages = engine._messages(session, None, [], "")
    context = json.loads(messages[1]["content"])

    assert context["question"] == question
    assert "file names, paths, functions, classes, error messages" in messages[0]["content"]
    assert "No files were preselected" in context["scope_instruction"]


def test_more_than_one_blocking_question_is_rejected(tmp_path: Path) -> None:
    config = Config()
    store = SessionStore(tmp_path, config.repository, "blocking-questions")
    engine = AnalysisEngine(config=config, tools=RepositoryTools(tmp_path, config.repository), store=store, client=FakeClient())
    draft = AnalysisDraft(
        status="needs_user",
        summary="需要补充",
        open_questions=[
            OpenQuestion(id="q1", question="问题 1", blocking=True, suggested_verification="回答 1"),
            OpenQuestion(id="q2", question="问题 2", blocking=True, suggested_verification="回答 2"),
        ],
        next_action="answer",
    )

    with pytest.raises(ValueError, match="at most one blocking"):
        engine._validate_evidence_references(draft)
