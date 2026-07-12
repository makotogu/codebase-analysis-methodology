import asyncio
from pathlib import Path

from textual.widgets import DataTable

from code_loop.config import Config
from code_loop.storage import SessionStore
from code_loop.tui import DashboardApp


def test_dashboard_lists_and_filters_recent_sessions(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = Config()
        first = SessionStore(tmp_path, config.repository, "first")
        first.create("订单创建链路", None)
        second = SessionStore(tmp_path, config.repository, "second")
        second.create("支付回调链路", None)
        app = DashboardApp(tmp_path, config)
        async with app.run_test() as pilot:
            table = app.query_one("#recent-sessions", DataTable)
            assert table.row_count == 2
            await pilot.click("#session-filter")
            await pilot.press(*"订单")
            await pilot.pause()
            assert table.row_count == 1

    asyncio.run(scenario())


def test_dashboard_groups_and_collapses_child_sessions(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = Config()
        parent_store = SessionStore(tmp_path, config.repository, "parent")
        parent = parent_store.create("父调查", None)
        child_store = SessionStore(tmp_path, config.repository, "child")
        child_store.create("子调查", None, parent_session_id=parent.id)
        app = DashboardApp(tmp_path, config)
        async with app.run_test() as pilot:
            table = app.query_one("#recent-sessions", DataTable)
            assert table.row_count == 2
            app.selected_session_id = parent.id
            app.action_toggle_children()
            await pilot.pause()
            assert table.row_count == 1
            app.action_toggle_children()
            await pilot.pause()
            assert table.row_count == 2

    asyncio.run(scenario())
