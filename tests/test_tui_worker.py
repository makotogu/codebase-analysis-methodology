import asyncio
import time
from pathlib import Path

import pytest
from textual.widgets import Button, Input, MarkdownViewer, Select, Static

from code_loop.config import Config
from code_loop.models import AnalysisDraft, Claim, EvidenceLevel, OpenQuestion, ReviewStatus
from code_loop.storage import SessionStore
from code_loop.tui import CodeLoopApp


class FakeEngine:
    evidence = []

    def __init__(self, progress, cancelled):
        self.progress = progress
        self.cancelled = cancelled

    def analyze(self, session, note, context_evidence_ids=None):
        self.progress("analysis_started", {})
        time.sleep(0.05)
        return AnalysisDraft(status="needs_user", summary="后台完成", next_action="review")


class UsageFakeEngine(FakeEngine):
    def __init__(self, progress, cancelled, store):
        super().__init__(progress, cancelled)
        self.store = store

    def analyze(self, session, note, context_evidence_ids=None):
        self.progress(
            "model_call_finished",
            {
                "model": "deepseek-v4-flash",
                "cost_usd": 0.001234,
                "cumulative_cost_usd": 0.012345,
                "prompt_cache_hit_tokens": 1_024,
                "prompt_cache_miss_tokens": 256,
                "completion_tokens": 128,
                "tool_calls": 1,
            },
        )
        time.sleep(0.05)
        session.usage.estimated_cost_usd = 0.012345
        session.usage.prompt_cache_hit_tokens = 1_024
        session.usage.prompt_cache_miss_tokens = 256
        session.usage.completion_tokens = 128
        self.store.save_session(session)
        return AnalysisDraft(status="needs_user", summary="后台完成", next_action="review")


def test_analysis_runs_in_background_worker(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = Config()
        store = SessionStore(tmp_path, config.repository, "worker-session")
        session = store.create("问题", None)
        app = CodeLoopApp(tmp_path, config, store, session)
        app._new_engine = lambda *, progress, cancelled: FakeEngine(progress, cancelled)  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            app.action_run_analysis()
            assert app.query_one("#analyze", Button).disabled
            await asyncio.sleep(0.15)
            await pilot.pause()
            assert not app.query_one("#analyze", Button).disabled
            assert "分析完成" in str(app.query_one("#progress", Static).render())
            assert app.store.report_path.exists()
            assert "后台完成" in app.store.report_path.read_text(encoding="utf-8")
            assert app.query_one("#overview-report", MarkdownViewer).document.can_focus

    asyncio.run(scenario())


def test_model_usage_repaints_during_background_analysis(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = Config()
        store = SessionStore(tmp_path, config.repository, "usage-session")
        session = store.create("问题", None)
        app = CodeLoopApp(tmp_path, config, store, session)
        app._new_engine = lambda *, progress, cancelled: UsageFakeEngine(progress, cancelled, store)  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            app.action_run_analysis()
            await asyncio.sleep(0.02)
            summary = str(app.query_one("#session-summary", Static).render())
            progress = str(app.query_one("#progress", Static).render())
            assert "$0.012345" in summary
            assert "缓存命中 1,024" in summary
            assert "本次 $0.001234，累计 $0.012345" in progress

    asyncio.run(scenario())


def test_tool_progress_descriptions_are_specific() -> None:
    assert CodeLoopApp._tool_action("read_lines", "tui.py:120-220") == "正在读取 tui.py:120-220"
    assert CodeLoopApp._tool_action("search_text", "“run_analysis_worker” · **/*") == "正在搜索 “run_analysis_worker” · **/*"
    assert CodeLoopApp._tool_action("list_files", "src/code_loop/**") == "正在扫描 src/code_loop/**"
    assert CodeLoopApp._tool_action("git_log", "engine.py") == "正在读取提交历史 engine.py"
    assert CodeLoopApp._tool_action("git_show", "abc123:tui.py") == "正在查看历史文件 abc123:tui.py"


def test_activity_history_is_bounded_and_progress_shows_elapsed_time(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        config = Config()
        store = SessionStore(tmp_path, config.repository, "activity-session")
        session = store.create("问题", None)
        app = CodeLoopApp(tmp_path, config, store, session)
        now = [100.0]
        monkeypatch.setattr("code_loop.tui.monotonic", lambda: now[0])
        async with app.run_test():
            for number in range(10):
                app._append_activity(f"事件 {number}")
            activity = str(app.query_one("#activity-log", Static).render())
            assert "事件 0" not in activity
            assert "事件 1" not in activity
            assert "事件 2" in activity
            assert "事件 9" in activity

            app._set_progress("DeepSeek Flash 正在分析工具结果 · 第 4/20 步", timed=True)
            now[0] = 112.0
            app._tick_progress()
            progress = str(app.query_one("#progress", Static).render())
            assert "第 4/20 步" in progress
            assert "12s" in progress

    asyncio.run(scenario())


def test_review_advances_to_next_proposed_claim(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = Config()
        store = SessionStore(tmp_path, config.repository, "review-session")
        session = store.create("问题", None)
        store.save_analysis(
            AnalysisDraft(
                status="needs_user",
                summary="摘要",
                claims=[
                    Claim(
                        id="claim_1",
                        statement="第一条",
                        category="code_fact",
                        evidence_level=EvidenceLevel.VERIFIED,
                        confidence="high",
                    ),
                    Claim(
                        id="claim_2",
                        statement="第二条",
                        category="code_fact",
                        evidence_level=EvidenceLevel.VERIFIED,
                        confidence="high",
                    ),
                ],
                next_action="review",
            ),
            [],
        )
        app = CodeLoopApp(tmp_path, config, store, session)
        async with app.run_test() as pilot:
            assert app.selected_claim_id == "claim_1"
            app.action_show_tab("claims")
            await pilot.pause()
            app.action_confirm_claim()
            await pilot.pause()
            assert app.selected_claim_id == "claim_2"
            assert app.query_one("#claim-select", Select).value == "claim_2"
            analysis = store.load_analysis_or_none()
            assert analysis is not None
            assert analysis.draft.claims[0].review_status == ReviewStatus.CONFIRMED

    asyncio.run(scenario())


def test_analysis_outcome_routes_blocking_claim_and_complete_states(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = Config()
        store = SessionStore(tmp_path, config.repository, "outcome-session")
        session = store.create("问题", None)
        app = CodeLoopApp(tmp_path, config, store, session)
        async with app.run_test() as pilot:
            blocking = AnalysisDraft(
                status="needs_user",
                summary="需要补充",
                open_questions=[
                    OpenQuestion(id="q1", question="你指的继续按钮在哪个页面？", blocking=True, suggested_verification="说明页面名")
                ],
                next_action="answer",
            )
            store.save_analysis(blocking, [])
            app._refresh()
            app._apply_analysis_outcome(blocking)
            await pilot.pause()
            assert app.query_one("#workbench-tabs").active == "overview"
            assert app.query_one("#clarification", Static).display
            composer = app.query_one("#input", Input)
            assert "你指的继续按钮" in composer.placeholder
            assert composer.has_focus

            app.action_complete_session()
            await pilot.pause()
            assert app.is_running
            assert store.load_session().status == "active"

            proposed = AnalysisDraft(
                status="needs_user",
                summary="待裁决",
                claims=[Claim(id="c1", statement="结论", category="code_fact", evidence_level=EvidenceLevel.INFERRED, confidence="medium")],
                next_action="review",
            )
            assert "裁决" in app._apply_analysis_outcome(proposed)
            assert app.query_one("#workbench-tabs").active == "claims"
            assert app.selected_claim_id == "c1"

            complete = AnalysisDraft(status="ready_to_complete", summary="完成", next_action="complete")
            assert "完成" in app._apply_analysis_outcome(complete)
            assert app.query_one("#workbench-tabs").active == "overview"

    asyncio.run(scenario())


def test_complete_session_saves_report_and_exits_analysis_page(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = Config()
        store = SessionStore(tmp_path, config.repository, "complete-session")
        session = store.create("已完成的问题", None)
        store.save_analysis(
            AnalysisDraft(
                status="ready_to_complete",
                summary="最终摘要",
                claims=[
                    Claim(
                        id="claim_1",
                        statement="已确认结论",
                        category="code_fact",
                        evidence_level=EvidenceLevel.VERIFIED,
                        confidence="high",
                        review_status=ReviewStatus.CONFIRMED,
                    )
                ],
                next_action="complete",
            ),
            [],
        )
        app = CodeLoopApp(tmp_path, config, store, session)
        async with app.run_test() as pilot:
            app.action_complete_session()
            await pilot.pause()

        assert store.load_session().status == "completed"
        report = store.report_path.read_text(encoding="utf-8")
        assert "状态：`completed`" in report
        revisions = store.list_revisions()
        assert [(item.id, item.kind) for item in revisions] == [("rev_0001", "completion")]
        assert not app.is_running

    asyncio.run(scenario())


def test_revision_view_and_claim_branch_prefill(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = Config()
        store = SessionStore(tmp_path, config.repository, "revision-ui")
        session = store.create("主问题", None)
        store.save_analysis(
            AnalysisDraft(
                status="needs_user",
                summary="版本摘要",
                claims=[
                    Claim(
                        id="claim_1",
                        statement="需要深入的结论",
                        category="code_fact",
                        evidence_ids=["ev_1"],
                        evidence_level=EvidenceLevel.INFERRED,
                        confidence="medium",
                    )
                ],
                next_action="review",
            ),
            [],
        )
        store.create_revision(kind="model_analysis", user_message="继续")
        session = store.load_session()
        app = CodeLoopApp(tmp_path, config, store, session)
        async with app.run_test() as pilot:
            app.action_show_tab("revisions")
            await pilot.pause()
            assert app.query_one("#revision-table").row_count == 1
            assert app.selected_revision_id == "rev_0001"
            assert "版本摘要" in str(app.query_one("#revision-detail", Static).render())

            app.selected_claim_id = "claim_1"
            app.action_branch_from_claim()
            composer = app.query_one("#input", Input)
            assert app.state.branch_mode
            assert "需要深入的结论" in composer.value
            assert app.state.branch_evidence_ids == ["ev_1"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("size", "expected_classes"),
    [((160, 50), set()), ((100, 35), {"compact"}), ((79, 24), {"compact", "narrow"})],
)
def test_workbench_views_reflow_across_terminal_sizes(tmp_path: Path, size: tuple[int, int], expected_classes: set[str]) -> None:
    async def scenario() -> None:
        config = Config()
        store = SessionStore(tmp_path, config.repository, f"size-{size[0]}")
        session = store.create("尺寸测试", None)
        app = CodeLoopApp(tmp_path, config, store, session)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for class_name in expected_classes:
                assert app.has_class(class_name)
            if "compact" not in expected_classes:
                assert not app.has_class("compact")
            for tab_id in ("overview", "graph", "claims", "evidence", "activity", "revisions"):
                app.action_show_tab(tab_id)
                await pilot.pause()
                assert app.query_one("#workbench-tabs").active == tab_id

    asyncio.run(scenario())
