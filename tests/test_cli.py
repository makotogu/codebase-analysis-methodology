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
        def __init__(self, repository, config, store, session):
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
        def __init__(self, repository, config, store, session):
            self.session = session

        def run(self):
            opened.append(self.session.id)
            if self.session.id == "parent":
                return SessionLaunch(repository=tmp_path, session_id="child")
            return None

    monkeypatch.setattr(cli, "CodeLoopApp", FakeWorkbench)
    cli._run_workbench_chain(tmp_path, config, parent_store, parent)

    assert opened == ["parent", "child"]
