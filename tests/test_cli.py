from pathlib import Path

from code_loop import cli
from code_loop.config import load_config
from code_loop.storage import SessionStore
from code_loop.tui import DashboardAction, SessionLaunch


def test_path_argument_uses_workspace_launcher(monkeypatch, tmp_path: Path) -> None:
    seen: list[Path | None] = []

    def fake_launch(target: Path | None = None) -> int:
        seen.append(target)
        return 7

    monkeypatch.setattr(cli, "cmd_launch", fake_launch)
    assert cli.main([str(tmp_path)]) == 7
    assert seen == [Path(tmp_path)]


def test_empty_invocation_uses_launcher(monkeypatch) -> None:
    seen: list[Path | None] = []

    def fake_launch(target: Path | None = None) -> int:
        seen.append(target)
        return 0

    monkeypatch.setattr(cli, "cmd_launch", fake_launch)
    assert cli.main([]) == 0
    assert seen == [None]


def test_interactive_launch_returns_to_dashboard_after_workbench(monkeypatch, tmp_path: Path) -> None:
    config = load_config(tmp_path)
    store = SessionStore(tmp_path, config.repository, "resume-me")
    store.create("恢复测试", None)
    actions = iter([DashboardAction("resume", "resume-me"), None])
    opened: list[str] = []

    class FakeDashboard:
        def __init__(self, repository, config):
            pass

        def run(self):
            return next(actions)

    class FakeWorkbench:
        def __init__(self, repository, config, store, session, *, auto_start=False):
            opened.append(session.id)

        def run(self):
            return None

    monkeypatch.setattr(cli, "DashboardApp", FakeDashboard)
    monkeypatch.setattr(cli, "CodeLoopApp", FakeWorkbench)

    assert cli.cmd_launch(tmp_path) == 0
    assert opened == ["resume-me"]


def test_workbench_chain_follows_child_session_handoff(monkeypatch, tmp_path: Path) -> None:
    config = load_config(tmp_path)
    parent_store = SessionStore(tmp_path, config.repository, "parent")
    parent = parent_store.create("父调查", None)
    child_store = SessionStore(tmp_path, config.repository, "child")
    child_store.create("子调查", None, parent_session_id=parent.id)
    opened: list[str] = []

    class FakeWorkbench:
        def __init__(self, repository, config, store, session, *, auto_start=False):
            self.session = session

        def run(self):
            opened.append(self.session.id)
            if self.session.id == "parent":
                return SessionLaunch(repository=tmp_path, session_id="child")
            return None

    monkeypatch.setattr(cli, "CodeLoopApp", FakeWorkbench)
    cli._run_workbench_chain(tmp_path, config, parent_store, parent)

    assert opened == ["parent", "child"]


def test_synthesize_defaults_to_deterministic_draft_without_model(monkeypatch, tmp_path: Path, capsys) -> None:
    config = load_config(tmp_path)
    store = SessionStore(tmp_path, config.repository, "manual-close")
    store.create("确认启动链路", None)
    from code_loop.models import AnalysisDraft, Claim, EvidenceLevel, ReviewStatus

    store.save_analysis(AnalysisDraft(
        status="ready_to_complete",
        summary="摘要",
        claims=[Claim(
            id="claim_1", statement="入口调用服务", category="code_fact", evidence_level=EvidenceLevel.VERIFIED,
            confidence="high", review_status=ReviewStatus.CONFIRMED,
        )],
        next_action="review",
    ), [])

    monkeypatch.setattr(cli, "DeepSeekClient", lambda _config: (_ for _ in ()).throw(AssertionError("model called")))
    assert cli.main(["synthesize", str(tmp_path), "manual-close"]) == 0
    assert store.load_synthesis_or_none().coverage.missing == 0
    assert "suggestions=0" in capsys.readouterr().out


def test_synthesize_assess_calls_flash_for_selected_cards(monkeypatch, tmp_path: Path) -> None:
    from code_loop.llm import Completion
    from code_loop.models import AnalysisDraft, Claim, EvidenceLevel, ReviewStatus, Usage

    config = load_config(tmp_path)
    store = SessionStore(tmp_path, config.repository, "assess-cards")
    store.create("评估卡片", None)
    store.save_analysis(AnalysisDraft(
        status="ready_to_complete",
        summary="摘要",
        claims=[
            Claim(id="claim_1", statement="配置按需读取", category="code_fact", evidence_level=EvidenceLevel.UNVERIFIED, confidence="low", review_status=ReviewStatus.CONFIRMED),
            Claim(id="claim_2", statement="首次访问加载配置", category="code_fact", evidence_level=EvidenceLevel.UNVERIFIED, confidence="low", review_status=ReviewStatus.CONFIRMED),
        ],
        next_action="review",
    ), [])

    class Client:
        def complete(self, *, model, messages, tools):
            return Completion(
                '{"assessments":[{"candidate_card_ids":["card_0001","card_0002"],'
                '"relation":"duplicate","confidence":"high","suggested_title":"按需配置",'
                '"suggested_statement":"配置首次访问时加载。","rationale":"同一事实"}]}',
                [], Usage(),
            )

    monkeypatch.setattr(cli, "DeepSeekClient", lambda _config: Client())
    assert cli.main([
        "synthesize", str(tmp_path), "assess-cards", "--assess", "card_0001", "card_0002",
    ]) == 0
    assert store.load_synthesis_or_none().suggestions[0].relation == "duplicate"
