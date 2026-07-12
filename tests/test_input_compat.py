import os
import sys
import asyncio
from pathlib import Path

import code_loop
from textual.widgets import Input

from code_loop.config import Config
from code_loop.models import AnalysisDraft, Claim, EvidenceLevel, ReviewStatus
from code_loop.storage import SessionStore
from code_loop.tui import CodeLoopApp


def test_macos_disables_textual_kitty_keyboard_protocol_by_default() -> None:
    if sys.platform == "darwin":
        assert os.environ["TEXTUAL_DISABLE_KITTY_KEY"] == "1"


def test_composer_text_does_not_trigger_single_key_workbench_actions(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = Config()
        store = SessionStore(tmp_path, config.repository, "ime-session")
        session = store.create("输入兼容", None)
        store.save_analysis(
            AnalysisDraft(
                status="needs_user",
                summary="摘要",
                claims=[Claim(id="c1", statement="结论", category="code_fact", evidence_level=EvidenceLevel.VERIFIED, confidence="high")],
                next_action="review",
            ),
            [],
        )
        app = CodeLoopApp(tmp_path, config, store, session)
        async with app.run_test() as pilot:
            app.action_show_tab("claims")
            composer = app.query_one("#input", Input)
            composer.focus()
            await pilot.press("c", "q", "中", "文")
            await pilot.pause()
            assert composer.value == "cq中文"
            analysis = store.load_analysis_or_none()
            assert analysis is not None
            assert analysis.draft.claims[0].review_status == ReviewStatus.PROPOSED

    asyncio.run(scenario())
