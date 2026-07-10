import asyncio
from pathlib import Path

from textual.widgets import Input, Static, TabbedContent, TextArea

from code_loop.tui import LauncherApp, WorkspaceSetupApp
from code_loop.workspace import WorkspaceChoice


def test_launcher_and_workspace_setup_mount_without_creating_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        launcher = LauncherApp(tmp_path)
        async with launcher.run_test():
            assert launcher.query_one("#workspace-path", Input).value == str(tmp_path)

        setup = WorkspaceSetupApp(WorkspaceChoice(root=tmp_path, seed_items=("seed.py",)))
        async with setup.run_test() as pilot:
            await pilot.pause()
            assert "seed.py" in str(setup.query_one("#scope-selection").render())
            assert setup.query_one("#goal", TextArea).text == ""
            assert setup.query_one("#setup-tabs", TabbedContent).active == "goal-step"
            assert setup.query_one("#goal", TextArea).has_focus

    asyncio.run(scenario())
    assert not (tmp_path / "notes").exists()


def test_setup_selection_is_explicit_and_optional(tmp_path: Path) -> None:
    async def scenario() -> None:
        target = tmp_path / "seed.py"
        target.write_text("print('x')\n", encoding="utf-8")
        setup = WorkspaceSetupApp(WorkspaceChoice(root=tmp_path, seed_items=()))
        async with setup.run_test() as pilot:
            setup._select_candidate(target)
            assert setup.scope_items == set()
            setup._toggle_path(target)
            assert setup.scope_items == {"seed.py"}
            setup.query_one("#setup-tabs", TabbedContent).active = "scope-step"
            await pilot.pause()
            await pilot.click("#setup-next")
            await pilot.pause()
            assert setup.query_one("#setup-tabs", TabbedContent).active == "goal-step"
            assert "seed.py" in str(setup.query_one("#goal-summary", Static).render())

    asyncio.run(scenario())


def test_setup_creates_natural_language_session_without_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        setup = WorkspaceSetupApp(WorkspaceChoice(root=tmp_path, seed_items=()))
        async with setup.run_test() as pilot:
            setup.query_one("#goal", TextArea).load_text("点击继续分析后为什么会卡住？")
            setup._start_analysis()
            await pilot.pause()

    asyncio.run(scenario())
    sessions = list((tmp_path / "notes" / "code-loop").glob("*/session.json"))
    assert len(sessions) == 1
    assert '"scope_mode": "natural_language"' in sessions[0].read_text(encoding="utf-8")
