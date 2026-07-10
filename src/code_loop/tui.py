from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
from time import monotonic
import webbrowser

from rich.syntax import Syntax
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.widgets import (
    Button,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input,
    MarkdownViewer,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)
from textual.worker import Worker, get_current_worker

from .config import Config, load_config
from .engine import AnalysisEngine
from .llm import DeepSeekClient
from .models import Claim, Correction, ReviewStatus, Session
from .renderer import code_fence_language
from .repository_tools import RepositoryTools
from .storage import SessionStore
from .time_utils import format_local_timestamp, local_now_text
from .workspace import WorkspaceChoice, WorkspaceError, normalize_scope_item, resolve_workspace, scope_mode


@dataclass(frozen=True)
class SessionLaunch:
    repository: Path
    session_id: str


@dataclass(frozen=True)
class DashboardAction:
    kind: str
    session_id: str | None = None


class DashboardApp(App[DashboardAction | None]):
    """Workspace home for starting and resuming analysis sessions."""

    TITLE = "Code Loop · Sessions"

    CSS = """
    #dashboard-title { height: 4; padding: 1 2; }
    #session-filter { margin: 0 1; }
    #recent-sessions { height: 1fr; margin: 1; }
    #dashboard-actions { height: 3; }
    """
    BINDINGS = [
        ("n", "new_analysis", "新建"),
        ("enter", "resume_selected", "恢复"),
        ("space", "toggle_children", "展开/折叠"),
        ("q", "quit", "退出"),
    ]

    def __init__(self, repository: Path, config: Config):
        super().__init__()
        self.repository = repository.resolve()
        self.config = config
        self.selected_session_id: str | None = None
        self.sessions = SessionStore.list_sessions(self.repository, self.config.repository)
        self.collapsed_session_ids: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(f"Code Loop\nWorkspace: {self.repository}", id="dashboard-title", markup=False)
        yield Input(placeholder="筛选最近会话…", id="session-filter")
        yield DataTable(id="recent-sessions", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="dashboard-actions"):
            yield Button("新建分析 n", id="dashboard-new", variant="primary")
            yield Button("恢复 Enter", id="dashboard-resume")
            yield Button("打开报告", id="dashboard-report")
            yield Button("退出 q", id="dashboard-quit")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#recent-sessions", DataTable)
        table.add_columns("状态", "更新时间（本地）", "成本", "问题")
        self._refresh_sessions()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "session-filter":
            self._refresh_sessions(event.value)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "recent-sessions":
            self.selected_session_id = str(event.row_key.value)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "recent-sessions":
            self.action_resume_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dashboard-new":
            self.action_new_analysis()
        elif event.button.id == "dashboard-resume":
            self.action_resume_selected()
        elif event.button.id == "dashboard-report":
            self._open_selected_report()
        elif event.button.id == "dashboard-quit":
            self.action_quit()

    def _refresh_sessions(self, query: str = "") -> None:
        table = self.query_one("#recent-sessions", DataTable)
        table.clear()
        lowered = query.strip().lower()
        visible = self._visible_sessions(lowered)
        for session, depth, has_children in visible:
            updated = format_local_timestamp(session.updated_at)
            if depth:
                prefix = "  " * (depth - 1) + "└─ "
            elif has_children:
                prefix = ("▸ " if session.id in self.collapsed_session_ids else "▾ ")
            elif session.parent_session_id:
                prefix = "◌ "
            else:
                prefix = ""
            table.add_row(
                session.status,
                updated,
                f"${session.usage.estimated_cost_usd:.6f}",
                prefix + session.question.replace("\n", " ")[:120],
                key=session.id,
            )
        self.selected_session_id = visible[0][0].id if visible else None

    def _visible_sessions(self, query: str) -> list[tuple[Session, int, bool]]:
        by_id = {session.id: session for session in self.sessions}
        children: dict[str, list[Session]] = {}
        for session in self.sessions:
            if session.parent_session_id in by_id:
                children.setdefault(session.parent_session_id or "", []).append(session)
        if query:
            return [
                (session, 1 if session.parent_session_id in by_id else 0, bool(children.get(session.id)))
                for session in self.sessions
                if query in session.question.lower() or query in session.id.lower()
            ]
        roots = [session for session in self.sessions if session.parent_session_id not in by_id]
        ordered: list[tuple[Session, int, bool]] = []

        def visit(session: Session, depth: int) -> None:
            descendants = children.get(session.id, [])
            ordered.append((session, depth, bool(descendants)))
            if session.id in self.collapsed_session_ids:
                return
            for child in descendants:
                visit(child, depth + 1)

        for root in roots:
            visit(root, 0)
        return ordered

    def action_toggle_children(self) -> None:
        if not self.selected_session_id:
            return
        has_children = any(item.parent_session_id == self.selected_session_id for item in self.sessions)
        if not has_children:
            return
        if self.selected_session_id in self.collapsed_session_ids:
            self.collapsed_session_ids.remove(self.selected_session_id)
        else:
            self.collapsed_session_ids.add(self.selected_session_id)
        self._refresh_sessions(self.query_one("#session-filter", Input).value)

    def action_new_analysis(self) -> None:
        self.exit(DashboardAction("new"))

    def action_resume_selected(self) -> None:
        if not self.selected_session_id:
            self.notify("尚无可恢复会话。", severity="warning")
            return
        self.exit(DashboardAction("resume", self.selected_session_id))

    def action_quit(self) -> None:
        self.exit(None)

    def _open_selected_report(self) -> None:
        if not self.selected_session_id:
            return
        store = SessionStore(self.repository, self.config.repository, self.selected_session_id)
        store.render_artifacts()
        try:
            opened = webbrowser.open(store.html_report_path.as_uri())
        except Exception:
            opened = False
        if not opened:
            self.notify(f"报告位于 {store.html_report_path}", severity="warning")


class LauncherApp(App[WorkspaceChoice | None]):
    """Choose a workspace or a file before any analysis session is created."""

    TITLE = "Code Loop · Workspace"

    CSS = """
    #launcher-body { height: 1fr; }
    #browser, #launcher-help { width: 1fr; padding: 1; border: solid $primary; }
    #workspace-path { dock: bottom; }
    #launcher-actions { height: 3; }
    """

    def __init__(self, initial_path: Path | None = None):
        super().__init__()
        self.initial_path = (initial_path or Path.cwd()).expanduser().resolve()
        self.selected_path = self.initial_path

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="launcher-body"):
            yield DirectoryTree(self.initial_path if self.initial_path.is_dir() else self.initial_path.parent, id="launcher-tree")
            yield Static(
                "[b]选择 workspace[/b]\n\n"
                "- 在左侧树中定位目录或文件。\n"
                "- 选择目录：该目录就是 workspace。\n"
                "- 选择文件：自动寻找最近的 Git 根目录，并将文件作为初始分析范围。\n"
                "- 也可以直接在下方粘贴路径。\n\n"
                "此时不会创建会话或写入任何 notes。",
                id="launcher-help",
            )
        yield Input(value=str(self.initial_path), placeholder="workspace 或文件路径", id="workspace-path")
        with Horizontal(id="launcher-actions"):
            yield Button("打开 workspace", id="open-workspace", variant="primary")
            yield Button("退出", id="quit")
        yield Footer()

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self._set_selected(event.path)

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self._set_selected(event.path)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "workspace-path":
            self._open_workspace()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "open-workspace":
            self._open_workspace()
        elif event.button.id == "quit":
            self.exit(None)

    def _set_selected(self, path: Path) -> None:
        self.selected_path = path
        self.query_one("#workspace-path", Input).value = str(path)

    def _open_workspace(self) -> None:
        raw = self.query_one("#workspace-path", Input).value.strip()
        try:
            choice = resolve_workspace(Path(raw))
        except WorkspaceError as exc:
            self.notify(str(exc), severity="error")
            return
        self.exit(choice)

class WorkspaceSetupApp(App[SessionLaunch | None]):
    """Select optional seed files and describe the concrete analysis goal."""

    TITLE = "Code Loop · New Analysis"

    CSS = """
    #setup-tabs { height: 1fr; }
    #scope-step-body { height: 1fr; }
    #scope-tree { width: 60%; }
    #scope-selection { width: 40%; padding: 1; border-left: solid $primary; overflow-y: auto; }
    #goal-step-body { height: 1fr; }
    #goal-summary { width: 30%; padding: 1; border-right: solid $primary; overflow-y: auto; }
    #goal-panel { width: 1fr; padding: 2; }
    #goal { height: 1fr; min-height: 8; }
    #scope-path { dock: bottom; }
    #setup-actions { height: 3; }
    """

    def __init__(self, choice: WorkspaceChoice):
        super().__init__()
        self.choice = choice
        self.scope_items: set[str] = set(choice.seed_items)
        self.candidate_path: Path | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="goal-step", id="setup-tabs"):
            with TabPane("1 你想了解什么？", id="goal-step"):
                with Horizontal(id="goal-step-body"):
                    yield Static(id="goal-summary", markup=False)
                    with Vertical(id="goal-panel"):
                        yield Static(
                            "[b]一句话即可，文件范围是可选的。[/b]\n\n"
                            "例如：\n"
                            "• 这个项目怎么启动？\n"
                            "• 点击继续分析后为什么会卡住？\n"
                            "• run_analysis_worker 的数据是怎么传递的？\n"
                            "• 我准备修改完成后的跳转逻辑，需要先了解哪些代码？"
                        )
                        yield TextArea(placeholder="你想了解什么？一句话即可", id="goal")
            with TabPane("2 可选：缩小分析范围", id="scope-step"):
                with Horizontal(id="scope-step-body"):
                    yield DirectoryTree(self.choice.root, id="scope-tree")
                    yield Static(id="scope-selection")
        yield Input(placeholder="输入相对或绝对路径后按 Enter 加入分析起点", id="scope-path")
        with Horizontal(id="setup-actions"):
            yield Button("加入/移除选中项 Space", id="toggle-scope")
            yield Button("清空已选范围", id="clear-scope")
            yield Button("设置范围（可选）", id="setup-back")
            yield Button("返回问题", id="setup-next", variant="primary")
            yield Button("开始分析", id="start-analysis", variant="primary")
            yield Button("取消", id="cancel")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_scope()
        self._sync_setup_controls("goal-step")
        self.query_one("#goal", TextArea).focus()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.tabbed_content.id == "setup-tabs":
            self._sync_setup_controls(event.pane.id or "scope-step")

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self._select_candidate(event.path)

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self._select_candidate(event.path)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "scope-path":
            self._add_scope_path(event.value)
            event.input.value = ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "toggle-scope":
            tree = self.query_one("#scope-tree", DirectoryTree)
            if tree.cursor_node is None:
                self.notify("请先在目录树中选择一个文件或目录。", severity="warning")
                return
            self._toggle_path(tree.cursor_node.data.path)
        elif event.button.id == "clear-scope":
            self.scope_items.clear()
            self._refresh_scope()
        elif event.button.id == "setup-next":
            self.query_one("#setup-tabs", TabbedContent).active = "goal-step"
            self._refresh_scope()
        elif event.button.id == "setup-back":
            self.query_one("#setup-tabs", TabbedContent).active = "scope-step"
        elif event.button.id == "start-analysis":
            self._start_analysis()
        elif event.button.id == "cancel":
            self.exit(None)

    def _select_candidate(self, path: Path) -> None:
        self.candidate_path = path
        self.query_one("#scope-path", Input).value = str(path)

    def _sync_setup_controls(self, pane_id: str) -> None:
        scope_step = pane_id == "scope-step"
        for selector in ("#toggle-scope", "#clear-scope", "#scope-path"):
            self.query_one(selector).display = scope_step
        self.query_one("#setup-next").display = scope_step
        self.query_one("#setup-back").display = not scope_step
        self.query_one("#start-analysis").display = not scope_step
        if not scope_step:
            self.query_one("#goal", TextArea).focus()

    def _toggle_path(self, path: Path) -> None:
        try:
            relative = normalize_scope_item(self.choice.root, path)
        except WorkspaceError as exc:
            self.notify(str(exc), severity="error")
            return
        if relative in self.scope_items:
            self.scope_items.remove(relative)
        else:
            self.scope_items.add(relative)
        self._refresh_scope()

    def _add_scope_path(self, raw: str) -> None:
        if not raw.strip():
            return
        try:
            self.scope_items.add(normalize_scope_item(self.choice.root, raw.strip()))
        except WorkspaceError as exc:
            self.notify(str(exc), severity="error")
            return
        self._refresh_scope()

    def _refresh_scope(self) -> None:
        content = ["[b]分析起点（可选）[/b]", "", "模型会优先读取这些内容；后续沿证据扩展的文件会单独记录。", ""]
        content.extend(f"- {item}" for item in sorted(self.scope_items))
        if not self.scope_items:
            content.append("未选择文件：模型将根据自然语言问题从 workspace 搜索。")
        self.query_one("#scope-selection", Static).update("\n".join(content))
        self.query_one("#goal-summary", Static).update(
            "分析范围摘要\n\n"
            + ("\n".join(f"• {item}" for item in sorted(self.scope_items)) if self.scope_items else "自然语言定位，未预选文件")
        )

    def _start_analysis(self) -> None:
        question = self.query_one("#goal", TextArea).text.strip()
        if not question:
            self.notify("一句话即可：请输入你想了解的问题。", severity="warning")
            self.query_one("#goal", TextArea).focus()
            return
        config = load_config(self.choice.root)
        tools = RepositoryTools(self.choice.root, config.repository)
        session_id = SessionStore.new_id(question)
        store = SessionStore(self.choice.root, config.repository, session_id)
        store.create(question, tools.git_head(), scope_items=sorted(self.scope_items), scope_mode=scope_mode(sorted(self.scope_items)))
        self.exit(SessionLaunch(repository=self.choice.root, session_id=session_id))


class WorkbenchCommands(Provider):
    """Searchable commands for Code Loop's task-oriented workbench."""

    def _commands(self):
        app = self.app
        commands = [
            ("切换到 Overview", lambda: app.action_show_tab("overview"), "查看 Markdown 报告与摘要"),
            ("切换到 Graph", lambda: app.action_show_tab("graph"), "查看链路节点与关系"),
            ("切换到 Claims", lambda: app.action_show_tab("claims"), "逐条裁决结论"),
            ("切换到 Evidence", lambda: app.action_show_tab("evidence"), "查看源码证据"),
            ("切换到 Activity", lambda: app.action_show_tab("activity"), "查看完整调用事件"),
            ("切换到 Revisions", lambda: app.action_show_tab("revisions"), "查看不可变分析版本"),
            ("继续分析", app.action_run_analysis, "调用模型继续当前会话"),
            ("将当前输入作为子调查", app.action_branch_from_input, "创建独立报告，不并入当前主题"),
            ("从当前 Claim 创建子调查", app.action_branch_from_claim, "继承 Claim 引用的证据"),
            ("从当前 Graph 节点创建子调查", app.action_branch_from_node, "继承节点引用的证据"),
            ("打开本地 Mermaid 报告", app.action_open_mermaid, "在默认浏览器打开 report.html"),
            ("完成会话", app.action_complete_session, "所有 Claim 裁决后完成"),
            ("过滤当前视图", app.action_filter_view, "按关键词筛选当前内容"),
        ]
        agenda = app.store.load_agenda(app.session)
        for topic in agenda.topics:
            if topic.status == "queued":
                commands.append(
                    (
                        f"开始待调查项 {topic.id}：{topic.question[:60]}",
                        lambda topic_id=topic.id: app.action_start_topic(topic_id),
                        "创建关联子会话",
                    )
                )
        if app.selected_revision_id:
            commands.append(
                (
                    f"从版本 {app.selected_revision_id} 创建分支",
                    lambda: app.action_branch_from_revision(app.selected_revision_id),
                    "保留当前状态，从旧版本建立子调查",
                )
            )
        return tuple(commands)

    async def discover(self) -> Hits:
        for name, command, help_text in self._commands():
            yield DiscoveryHit(name, command, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, command, help_text in self._commands():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), command, help=help_text)


@dataclass
class WorkbenchState:
    selected_claim_id: str | None = None
    selected_evidence_id: str | None = None
    selected_node_id: str | None = None
    amending_claim_id: str | None = None
    filter_mode: bool = False
    view_filter: str = ""
    focus_mode: bool = False
    branch_mode: bool = False
    branch_evidence_ids: list[str] | None = None
    branch_source_topic_id: str | None = None
    branch_parent_revision_id: str | None = None
    selected_revision_id: str | None = None

class CodeLoopApp(App[SessionLaunch | None]):
    """Review and continue an existing evidence-driven analysis session."""

    TITLE = "Code Loop · Workbench"

    CSS = """
    #top-status { height: 3; padding: 0 1; border-bottom: solid $primary; }
    #size-warning { display: none; height: 2; padding: 0 1; color: $warning; }
    #workbench-tabs { height: 1fr; }
    TabPane { padding: 0; }
    .split { height: 1fr; }
    .list-pane { width: 42%; min-width: 36; border-right: solid $primary; }
    .detail-pane { width: 1fr; padding: 1 2; overflow-y: auto; }
    #overview-sidebar { width: 32%; min-width: 34; max-width: 54; padding: 1; border-right: solid $primary; overflow-y: auto; }
    #overview-report { width: 1fr; }
    #clarification { display: none; margin-bottom: 1; padding: 1; border: round $warning; color: $warning; }
    #activity-log { margin-top: 1; padding: 1; border: round $secondary; height: auto; max-height: 12; }
    #claim-select { display: none; }
    #graph-nodes, #claim-table, #evidence-table, #activity-table, #revision-table { height: 1fr; }
    #graph-edges { height: 40%; border-top: solid $primary; }
    #evidence-code { width: 1fr; height: 1fr; overflow: auto; }
    #claim-actions { height: 3; }
    #input { dock: bottom; }
    #progress { height: 2; padding: 0 1; background: $surface; color: $text-muted; }
    #global-actions { height: 3; }
    .compact .split { layout: vertical; }
    .compact .list-pane { width: 1fr; height: 42%; border-right: none; border-bottom: solid $primary; }
    .compact .detail-pane { width: 1fr; height: 1fr; }
    .compact #overview-sidebar { display: none; }
    .narrow #size-warning { display: block; }
    .narrow #global-actions { display: none; }
    .focus-mode #top-status, .focus-mode #size-warning, .focus-mode #input,
    .focus-mode #progress, .focus-mode #global-actions, .focus-mode Footer { display: none; }
    """

    BINDINGS = [
        Binding("1", "show_tab('overview')", "Overview", show=False),
        Binding("2", "show_tab('graph')", "Graph", show=False),
        Binding("3", "show_tab('claims')", "Claims", show=False),
        Binding("4", "show_tab('evidence')", "Evidence", show=False),
        Binding("5", "show_tab('activity')", "Activity", show=False),
        Binding("6", "show_tab('revisions')", "Revisions", show=False),
        Binding("ctrl+r", "run_analysis", "继续分析"),
        Binding("m", "open_mermaid", "Mermaid"),
        Binding("f10", "toggle_focus", "全屏"),
        Binding("question_mark", "show_help", "帮助"),
        Binding("slash", "filter_view", "过滤", show=False),
        Binding("escape", "cancel_context", "取消", show=False),
        Binding("q", "quit_workbench", "退出"),
        Binding("c", "confirm_claim", "确认", show=False),
        Binding("x", "reject_claim", "否定", show=False),
        Binding("e", "amend_claim", "改写", show=False),
        Binding("y", "copy_evidence", "复制引用", show=False),
        Binding("o", "open_evidence", "编辑器打开", show=False),
    ]
    COMMANDS = App.COMMANDS | {WorkbenchCommands}

    def __init__(self, repository: Path, config: Config, store: SessionStore, session: Session):
        super().__init__()
        self.repository, self.config, self.store, self.session = repository, config, store, session
        self.state = WorkbenchState()
        self.analysis_worker: Worker[None] | None = None
        self.progress_text = "就绪。"
        self._progress_base = self.progress_text
        self._progress_started: float | None = None
        self._spinner_index = 0
        self._activity: deque[str] = deque(maxlen=8)

    selected_claim_id = property(lambda self: self.state.selected_claim_id, lambda self, value: setattr(self.state, "selected_claim_id", value))
    selected_evidence_id = property(lambda self: self.state.selected_evidence_id, lambda self, value: setattr(self.state, "selected_evidence_id", value))
    selected_node_id = property(lambda self: self.state.selected_node_id, lambda self, value: setattr(self.state, "selected_node_id", value))
    amending_claim_id = property(lambda self: self.state.amending_claim_id, lambda self, value: setattr(self.state, "amending_claim_id", value))
    filter_mode = property(lambda self: self.state.filter_mode, lambda self, value: setattr(self.state, "filter_mode", value))
    view_filter = property(lambda self: self.state.view_filter, lambda self, value: setattr(self.state, "view_filter", value))
    selected_revision_id = property(lambda self: self.state.selected_revision_id, lambda self, value: setattr(self.state, "selected_revision_id", value))

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="top-status", markup=False)
        yield Static("终端宽度较小：已启用紧凑模式，可用 1–5 切换视图。", id="size-warning", markup=False)
        with TabbedContent(initial="overview", id="workbench-tabs"):
            with TabPane("1 Overview", id="overview"):
                with Horizontal(classes="split"):
                    with VerticalScroll(id="overview-sidebar"):
                        yield Static(id="session-summary", markup=False)
                        yield Static(id="clarification", markup=False)
                        yield Static("最近活动\n尚未开始分析。", id="activity-log", markup=False)
                    yield MarkdownViewer("尚未生成报告。", show_table_of_contents=True, id="overview-report", open_links=False)
            with TabPane("2 Graph", id="graph"):
                with Horizontal(classes="split"):
                    with Vertical(classes="list-pane"):
                        yield DataTable(id="graph-nodes", cursor_type="row", zebra_stripes=True)
                        yield DataTable(id="graph-edges", cursor_type="row", zebra_stripes=True)
                    yield Static(id="graph-detail", classes="detail-pane", markup=False)
            with TabPane("3 Claims", id="claims"):
                with Horizontal(classes="split"):
                    with Vertical(classes="list-pane"):
                        yield DataTable(id="claim-table", cursor_type="row", zebra_stripes=True)
                        yield Select([], prompt="选择要裁决的结论", id="claim-select", allow_blank=True)
                    with Vertical(classes="detail-pane"):
                        yield Static(id="claim-detail", markup=False)
                        with Horizontal(id="claim-actions"):
                            yield Button("确认 c", id="confirm", variant="success")
                            yield Button("否定 x", id="reject", variant="error")
                            yield Button("改写 e", id="amend")
            with TabPane("4 Evidence", id="evidence"):
                with Horizontal(classes="split"):
                    yield DataTable(id="evidence-table", classes="list-pane", cursor_type="row", zebra_stripes=True)
                    yield Static(id="evidence-code", classes="detail-pane")
            with TabPane("5 Activity", id="activity"):
                yield DataTable(id="activity-table", cursor_type="row", zebra_stripes=True)
            with TabPane("6 Revisions", id="revisions"):
                with Horizontal(classes="split"):
                    yield DataTable(id="revision-table", classes="list-pane", cursor_type="row", zebra_stripes=True)
                    yield Static(id="revision-detail", classes="detail-pane", markup=False)
        yield Input(placeholder="追问当前分析后按 Enter；Ctrl+R 可直接继续", id="input")
        yield Static(self.progress_text, id="progress")
        with Horizontal(id="global-actions"):
            yield Button("继续分析", id="analyze", variant="primary")
            yield Button("取消分析", id="cancel-analysis", disabled=True)
            yield Button("完成", id="complete", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick_progress)
        self._configure_tables()
        self._apply_responsive_classes(self.size.width)
        self._refresh()
        analysis = self.store.load_analysis_or_none()
        if analysis is not None:
            self._apply_analysis_outcome(analysis.draft)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.filter_mode:
            self.view_filter = event.value.strip().lower()
            self.filter_mode = False
            event.input.value = ""
            event.input.placeholder = "追问当前分析后按 Enter；Ctrl+R 可直接继续"
            self._refresh()
        elif self.amending_claim_id:
            if event.value.strip():
                self._record_review("amend")
        elif self.state.branch_mode:
            if event.value.strip():
                self._create_child_session(
                    event.value.strip(),
                    evidence_ids=self.state.branch_evidence_ids or [],
                    source_topic_id=self.state.branch_source_topic_id,
                    parent_revision_id=self.state.branch_parent_revision_id,
                )
        elif event.value.strip():
            self.action_run_analysis()

    def on_resize(self, event: Resize) -> None:
        self._apply_responsive_classes(event.size.width)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "analyze":
            self.action_run_analysis()
        elif event.button.id == "cancel-analysis":
            self.action_cancel_analysis()
        elif event.button.id == "complete":
            self.action_complete_session()
        elif event.button.id in {"confirm", "reject"}:
            self._record_review(event.button.id)
        elif event.button.id == "amend":
            self.action_amend_claim()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "claim-select" or event.value is Select.NULL:
            return
        self.selected_claim_id = str(event.value)
        self._refresh_claim_detail()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        value = str(event.row_key.value)
        if event.data_table.id == "claim-table":
            self.selected_claim_id = value
            self._sync_claim_selector()
            self._refresh_claim_detail()
        elif event.data_table.id == "evidence-table":
            self.selected_evidence_id = value
            self._refresh_evidence_detail()
        elif event.data_table.id == "graph-nodes":
            self.selected_node_id = value
            self._refresh_graph_detail()
        elif event.data_table.id == "revision-table":
            self.selected_revision_id = value
            self._refresh_revision_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "graph-nodes":
            return
        analysis = self.store.load_analysis_or_none()
        node = next((item for item in (analysis.draft.journey_nodes if analysis else []) if item.id == str(event.row_key.value)), None)
        if node and node.evidence_ids:
            self.selected_evidence_id = node.evidence_ids[0]
            self.action_show_tab("evidence")
            self._refresh_evidence_detail()

    def _configure_tables(self) -> None:
        self.query_one("#graph-nodes", DataTable).add_columns("ID", "类型", "节点")
        self.query_one("#graph-edges", DataTable).add_columns("From", "关系", "To", "证据")
        self.query_one("#claim-table", DataTable).add_columns("状态", "ID", "等级", "置信度", "结论")
        self.query_one("#evidence-table", DataTable).add_columns("ID", "文件", "行号")
        self.query_one("#activity-table", DataTable).add_columns("时间（本地）", "类型", "步骤", "详情")
        self.query_one("#revision-table", DataTable).add_columns("版本", "类型", "时间（本地）", "摘要")

    def _apply_responsive_classes(self, width: int) -> None:
        self.set_class(width < 120, "compact")
        self.set_class(width < 80, "narrow")

    def action_run_analysis(self) -> None:
        if self.analysis_worker and self.analysis_worker.is_running:
            self.notify("分析仍在进行中。", severity="warning")
            return
        user_input = self.query_one("#input", Input)
        note = user_input.value
        user_input.value = ""
        self._clear_activity()
        self._set_busy(True, "正在启动分析…")
        self.analysis_worker = self.run_analysis_worker(note, self._context_evidence_ids())

    def _context_evidence_ids(self) -> list[str]:
        analysis = self.store.load_analysis_or_none()
        if analysis is None:
            return []
        active = self.query_one("#workbench-tabs", TabbedContent).active
        if active == "evidence" and self.selected_evidence_id:
            return [self.selected_evidence_id]
        if active == "claims" and self.selected_claim_id:
            claim = next((item for item in analysis.draft.claims if item.id == self.selected_claim_id), None)
            return claim.evidence_ids if claim else []
        if active == "graph" and self.selected_node_id:
            node = next((item for item in analysis.draft.journey_nodes if item.id == self.selected_node_id), None)
            return node.evidence_ids if node else []
        return []

    def action_branch_from_input(self) -> None:
        input_widget = self.query_one("#input", Input)
        if input_widget.value.strip():
            self._create_child_session(input_widget.value.strip())
            return
        self._begin_branch("")

    def action_branch_from_claim(self) -> None:
        analysis = self.store.load_analysis_or_none()
        claim = next((item for item in (analysis.draft.claims if analysis else []) if item.id == self.selected_claim_id), None)
        if claim is None:
            self.notify("请先选择一条 Claim。", severity="warning")
            return
        self._begin_branch(f"深入调查：{claim.human_text or claim.statement}", evidence_ids=claim.evidence_ids)

    def action_branch_from_node(self) -> None:
        analysis = self.store.load_analysis_or_none()
        node = next((item for item in (analysis.draft.journey_nodes if analysis else []) if item.id == self.selected_node_id), None)
        if node is None:
            self.notify("请先选择一个 Graph 节点。", severity="warning")
            return
        self._begin_branch(f"深入调查 {node.label} 的实现与影响。", evidence_ids=node.evidence_ids)

    def action_start_topic(self, topic_id: str) -> None:
        agenda = self.store.load_agenda(self.session)
        topic = next((item for item in agenda.topics if item.id == topic_id and item.status == "queued"), None)
        if topic is None:
            self.notify("该待调查项已不可用。", severity="warning")
            return
        self._create_child_session(topic.question, evidence_ids=topic.evidence_ids, source_topic_id=topic.id)

    def action_branch_from_revision(self, revision_id: str | None) -> None:
        if not revision_id:
            return
        try:
            revision = self.store.load_revision(revision_id)
        except (OSError, ValueError) as exc:
            self.notify(f"无法读取版本：{exc}", severity="error")
            return
        focus = revision.analysis.draft.primary_focus or self.session.question
        evidence_ids = list(dict.fromkeys(item for claim in revision.analysis.draft.claims for item in claim.evidence_ids))
        self._begin_branch(
            f"基于 {revision_id} 继续调查：{focus}",
            evidence_ids=evidence_ids,
            parent_revision_id=revision_id,
        )

    def _begin_branch(
        self,
        question: str,
        *,
        evidence_ids: list[str] | None = None,
        source_topic_id: str | None = None,
        parent_revision_id: str | None = None,
    ) -> None:
        self.state.branch_mode = True
        self.state.branch_evidence_ids = evidence_ids or []
        self.state.branch_source_topic_id = source_topic_id
        self.state.branch_parent_revision_id = parent_revision_id
        input_widget = self.query_one("#input", Input)
        input_widget.value = question
        input_widget.placeholder = "编辑子调查问题后按 Enter，Esc 取消"
        input_widget.focus()

    def _reset_branch_mode(self) -> None:
        self.state.branch_mode = False
        self.state.branch_evidence_ids = None
        self.state.branch_source_topic_id = None
        self.state.branch_parent_revision_id = None

    def _create_child_session(
        self,
        question: str,
        *,
        evidence_ids: list[str] | None = None,
        source_topic_id: str | None = None,
        parent_revision_id: str | None = None,
    ) -> None:
        question = question.strip()
        if not question:
            self.notify("请输入子调查问题。", severity="warning")
            return
        tools = RepositoryTools(self.repository, self.config.repository)
        _child_store, child = self.store.create_child_session(
            question,
            tools.git_head(),
            evidence_ids=evidence_ids,
            source_topic_id=source_topic_id,
            parent_revision_id=parent_revision_id,
        )
        self._reset_branch_mode()
        self.exit(SessionLaunch(repository=self.repository, session_id=child.id))

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one("#workbench-tabs", TabbedContent).active = tab_id

    def action_toggle_focus(self) -> None:
        self.state.focus_mode = not self.state.focus_mode
        self.set_class(self.state.focus_mode, "focus-mode")

    def action_show_help(self) -> None:
        self.notify("1–6 切换视图 · Ctrl+P 命令 · Ctrl+R 分析 · m Mermaid · F10 全屏 · Claims: c/x/e · Evidence: y/o", timeout=12)

    def action_filter_view(self) -> None:
        active = self.query_one("#workbench-tabs", TabbedContent).active
        self.filter_mode = True
        input_widget = self.query_one("#input", Input)
        input_widget.value = ""
        input_widget.placeholder = f"过滤 {active}；输入关键词后按 Enter，Esc 取消"
        input_widget.focus()

    def action_cancel_context(self) -> None:
        if not self.filter_mode and not self.amending_claim_id and not self.state.branch_mode:
            return
        self.filter_mode = False
        self.amending_claim_id = None
        self._reset_branch_mode()
        input_widget = self.query_one("#input", Input)
        input_widget.value = ""
        input_widget.placeholder = "追问当前分析后按 Enter；Ctrl+R 可直接继续"

    def action_quit_workbench(self) -> None:
        if self.analysis_worker and self.analysis_worker.is_running:
            self.notify("分析仍在进行；请先取消分析。", severity="warning")
            return
        self.exit()

    def action_confirm_claim(self) -> None:
        if self.query_one("#workbench-tabs", TabbedContent).active == "claims":
            self._record_review("confirm")

    def action_reject_claim(self) -> None:
        if self.query_one("#workbench-tabs", TabbedContent).active == "claims":
            self._record_review("reject")

    def action_amend_claim(self) -> None:
        if self.query_one("#workbench-tabs", TabbedContent).active != "claims" or not self.selected_claim_id:
            return
        self.amending_claim_id = self.selected_claim_id
        input_widget = self.query_one("#input", Input)
        input_widget.placeholder = f"正在改写 {self.selected_claim_id}；输入新结论后按 Enter，Esc 取消"
        input_widget.focus()

    def action_open_mermaid(self) -> None:
        self.store.render_artifacts()
        try:
            opened = webbrowser.open(self.store.html_report_path.as_uri())
        except Exception as exc:
            opened = False
            self._append_activity(f"打开 Mermaid 报告失败：{str(exc)[:160]}")
        if opened:
            self._append_activity(f"已打开本地 Mermaid 报告：{self.store.html_report_path}")
        else:
            self.notify(f"无法自动打开浏览器；报告位于 {self.store.html_report_path}", severity="warning", timeout=12)

    def action_copy_evidence(self) -> None:
        analysis = self.store.load_analysis_or_none()
        item = next((entry for entry in (analysis.evidence if analysis else []) if entry.id == self.selected_evidence_id), None)
        if item is None:
            return
        reference = f"{item.path}:{item.start_line}-{item.end_line}"
        self.copy_to_clipboard(reference)
        self.notify(f"已复制 {reference}")

    def action_open_evidence(self) -> None:
        analysis = self.store.load_analysis_or_none()
        item = next((entry for entry in (analysis.evidence if analysis else []) if entry.id == self.selected_evidence_id), None)
        if item is None:
            return
        editor = os.environ.get("EDITOR")
        if not editor:
            self.notify("请先设置 $EDITOR 后再打开证据文件。", severity="warning")
            return
        path = self.repository / item.path
        try:
            subprocess.Popen([*shlex.split(editor), f"+{item.start_line}", str(path)])
        except OSError as exc:
            self.notify(f"无法启动编辑器：{exc}", severity="error")

    def action_cancel_analysis(self) -> None:
        if not self.analysis_worker or not self.analysis_worker.is_running:
            self.notify("当前没有正在运行的分析。", severity="warning")
            return
        self.analysis_worker.cancel()
        self._set_progress("正在取消；当前 API 请求返回后会停止。")
        self._append_activity("用户请求取消分析")

    @work(thread=True, group="analysis", exclusive=True, exit_on_error=False)
    def run_analysis_worker(self, note: str, context_evidence_ids: list[str]) -> None:
        worker = get_current_worker()
        try:
            engine = self._new_engine(
                progress=self._progress_from_worker,
                cancelled=lambda: worker.is_cancelled,
            )
            draft = engine.analyze(self.session, note, context_evidence_ids=context_evidence_ids)
            if worker.is_cancelled:
                self.call_from_thread(self._analysis_cancelled)
                return
            self.call_from_thread(self._analysis_finished, draft, engine.evidence)
        except Exception as exc:
            if worker.is_cancelled:
                self.call_from_thread(self._analysis_cancelled)
            else:
                self.store.event({"type": "analysis_failed", "error": str(exc)[:1_000]})
                self.call_from_thread(self._analysis_failed, str(exc))

    def _new_engine(self, *, progress, cancelled) -> AnalysisEngine:
        return AnalysisEngine(
            config=self.config,
            tools=RepositoryTools(self.repository, self.config.repository),
            store=self.store,
            client=DeepSeekClient(self.config.model),
            progress=progress,
            cancelled=cancelled,
        )

    def _progress_from_worker(self, kind: str, payload: dict) -> None:
        timed = False
        if kind == "analysis_started":
            message = "正在准备证据与历史上下文…"
            activity = "开始准备证据与历史上下文"
        elif kind == "model_call_started":
            model = self._model_display_name(payload["model"])
            phase = self._phase_label(payload.get("phase", "analyzing_tools"))
            message = f"{model} 正在{phase} · 第 {payload['step']}/{payload['max_steps']} 步"
            activity = f"{model}：{phase}（第 {payload['step']}/{payload['max_steps']} 步）"
            timed = True
        elif kind == "model_call_finished":
            cumulative_cost = payload.get("cumulative_cost_usd", self.session.usage.estimated_cost_usd)
            tool_count = payload.get("tool_calls", 0)
            message = f"模型响应已收到：本次 ${payload['cost_usd']:.6f}，累计 ${cumulative_cost:.6f}"
            activity = f"收到模型响应 · {tool_count} 个工具调用 · 累计 ${cumulative_cost:.6f}"
        elif kind == "tool_call_started":
            action = self._tool_action(payload["tool"], payload.get("target", ""))
            message = action
            activity = action
            timed = True
        elif kind == "tool_call_finished":
            message, activity = self._tool_result_message(payload)
        elif kind == "validation_failed":
            message = f"结构化结果校验失败，正在重试（第 {payload['attempt']} 次）。"
            activity = f"结构化结果校验失败 · 第 {payload['attempt']} 次"
        elif kind == "model_upgraded":
            message = f"已升级至 {self._model_display_name(payload['model'])}：{payload['reason']}。"
            activity = message
        elif kind == "convergence_requested":
            message = f"正在要求 {self._model_display_name(payload['model'])} 基于现有证据组织最终结论。"
            activity = f"触发收敛：{payload['reason']}"
        elif kind == "analysis_stalled":
            message = f"分析未能收敛：{payload['reason']}"
            activity = message
        elif kind == "budget_paused":
            message = f"已达到会话预算，暂停分析（累计 ${payload['cost_usd']:.6f}）。"
            activity = message
        elif kind == "analysis_completed":
            message = f"分析完成：生成 {payload['claims']} 条结论。"
            activity = f"分析完成 · {payload['claims']} 条结论 · {payload.get('evidence', 0)} 份证据"
        elif kind == "model_call_failed":
            message = f"模型调用失败：{payload['error']}"
            activity = message
        elif kind == "analysis_failed":
            message = payload["error"]
            activity = f"分析失败：{message}"
        else:
            message = kind
            activity = kind
        self.call_from_thread(self._apply_progress_event, kind, message, activity, payload, timed)

    def _apply_progress_event(self, kind: str, message: str, activity: str, payload: dict, timed: bool) -> None:
        if kind == "model_call_finished":
            self._apply_usage(payload)
        self._set_progress(message, timed=timed)
        self._append_activity(activity)

    def _apply_usage(self, payload: dict) -> None:
        usage = self.session.usage
        usage.estimated_cost_usd = payload.get("cumulative_cost_usd", usage.estimated_cost_usd)
        usage.prompt_cache_hit_tokens = payload.get("prompt_cache_hit_tokens", usage.prompt_cache_hit_tokens)
        usage.prompt_cache_miss_tokens = payload.get("prompt_cache_miss_tokens", usage.prompt_cache_miss_tokens)
        usage.completion_tokens = payload.get("completion_tokens", usage.completion_tokens)
        self._refresh_session_summary()

    def _model_display_name(self, model: str) -> str:
        if model == self.config.model.flash_model:
            return "DeepSeek Flash"
        if model == self.config.model.pro_model:
            return "DeepSeek Pro"
        return model

    @staticmethod
    def _phase_label(phase: str) -> str:
        return {
            "locating_entry": "定位入口",
            "analyzing_tools": "分析工具结果",
            "repairing_output": "修正结构化输出",
            "deep_reasoning": "进行深度分析",
            "finalizing": "组织最终结论",
        }.get(phase, "分析已有证据")

    @staticmethod
    def _tool_action(tool: str, target: str) -> str:
        verb = {
            "read_lines": "正在读取",
            "search_text": "正在搜索",
            "list_files": "正在扫描",
            "git_log": "正在读取提交历史",
            "git_show": "正在查看历史文件",
        }.get(tool, f"正在执行 {tool}")
        return f"{verb} {target}".strip()

    def _tool_result_message(self, payload: dict) -> tuple[str, str]:
        tool = payload["tool"]
        target = payload.get("target", "")
        status = payload.get("status", "success")
        if status == "duplicate":
            text = f"已跳过重复工具调用：{self._tool_action(tool, target).removeprefix('正在')}"
        elif status == "error":
            error = str(payload.get("error") or "未知错误")[:180]
            text = f"工具执行失败：{target} · {error}"
        else:
            completed = {
                "read_lines": "已读取",
                "search_text": "已完成搜索",
                "list_files": "已完成扫描",
                "git_log": "已读取提交历史",
                "git_show": "已读取历史文件",
            }.get(tool, f"已完成 {tool}")
            text = f"{completed} {target}".strip()
            if payload.get("evidence_id"):
                text += f"，生成 {payload['evidence_id']}"
        return text, text

    def _analysis_finished(self, draft, evidence) -> None:
        self.store.save_analysis(draft, evidence)
        self.session = self.store.load_session()
        self._render_report()
        message = self._apply_analysis_outcome(draft)
        self._refresh()
        # Refresh may rebuild tables, so apply navigation and focus once more.
        self._apply_analysis_outcome(draft)
        self._set_busy(False, message)
        self.notify(message)

    def _apply_analysis_outcome(self, draft) -> str:
        """Select the next task and make a blocking clarification actionable."""

        blocking = next((item for item in draft.open_questions if item.blocking), None)
        proposed = next((claim for claim in draft.claims if claim.review_status == ReviewStatus.PROPOSED), None)
        if blocking is not None:
            self.action_show_tab("overview")
            input_widget = self.query_one("#input", Input)
            input_widget.placeholder = f"请回答：{blocking.question[:160]}"
            input_widget.focus()
            message = "需要你补充一条信息后继续分析。"
        elif proposed is not None:
            self.selected_claim_id = proposed.id
            self.action_show_tab("claims")
            self.query_one("#input", Input).placeholder = "追问当前分析后按 Enter；Ctrl+R 可直接继续"
            message = "分析已更新，请裁决待确认结论。"
        else:
            self.action_show_tab("overview")
            self.query_one("#input", Input).placeholder = "追问当前分析后按 Enter；Ctrl+R 可直接继续"
            message = "分析完成，可以查看报告或完成会话。"
        return message

    def _analysis_failed(self, error: str) -> None:
        self._set_busy(False, f"分析失败：{error}")
        if not self._activity or error not in self._activity[-1]:
            self._append_activity(f"分析失败：{error[:180]}")
        self.notify(error, severity="error", timeout=12)
        self._refresh()

    def _analysis_cancelled(self) -> None:
        self._set_busy(False, "分析已取消。")
        self._append_activity("分析已取消")
        self.notify("分析已取消。", severity="warning")
        self._refresh()

    def _set_busy(self, busy: bool, message: str) -> None:
        self.query_one("#analyze", Button).disabled = busy
        self.query_one("#cancel-analysis", Button).disabled = not busy
        self._set_progress(message)

    def _set_progress(self, message: str, *, timed: bool = False) -> None:
        self.progress_text = message
        self._progress_base = message
        self._progress_started = monotonic() if timed else None
        self._spinner_index = 0
        self._render_progress()
        if self.is_mounted:
            self._refresh_top_status()

    def _tick_progress(self) -> None:
        if self._progress_started is None:
            return
        self._spinner_index += 1
        self._render_progress()

    def _render_progress(self) -> None:
        if self._progress_started is None:
            rendered = self._progress_base
        else:
            spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[self._spinner_index % 10]
            elapsed = int(monotonic() - self._progress_started)
            rendered = f"{spinner} {self._progress_base} · {elapsed}s"
        self.query_one("#progress", Static).update(rendered)

    def _clear_activity(self) -> None:
        self._activity.clear()
        self.query_one("#activity-log", Static).update("最近活动\n正在开始新一轮分析…")

    def _append_activity(self, message: str) -> None:
        timestamp = local_now_text()
        self._activity.append(f"{timestamp}  {message}")
        content = "最近活动\n" + "\n".join(self._activity)
        self.query_one("#activity-log", Static).update(content)

    def action_complete_session(self) -> None:
        analysis = self.store.load_analysis_or_none()
        blocking = [question for question in (analysis.draft.open_questions if analysis else []) if question.blocking]
        if blocking:
            self.action_show_tab("overview")
            input_widget = self.query_one("#input", Input)
            input_widget.placeholder = f"请回答：{blocking[0].question[:160]}"
            input_widget.focus()
            self.notify("尚有需要补充的问题，回答后再完成会话。", severity="warning")
            return
        unresolved = [claim for claim in (analysis.draft.claims if analysis else []) if claim.review_status == ReviewStatus.PROPOSED]
        if unresolved:
            self.notify("仍有待裁决结论，完成前请确认、否定或改写。", severity="warning")
            return
        self.session.status = "completed"
        self.store.save_session(self.session)
        if analysis is not None:
            self.store.create_revision(kind="completion")
            self.session = self.store.load_session()
        self.store.mark_parent_topic_completed(self.session)
        self._render_report()
        self.exit()

    def _record_review(self, action: str) -> None:
        analysis = self.store.load_analysis_or_none()
        if not analysis or not analysis.draft.claims:
            self.notify("尚无可裁决的结论。", severity="warning")
            return
        claim = next((item for item in analysis.draft.claims if item.id == self.selected_claim_id), analysis.draft.claims[0])
        note = self.query_one("#input", Input).value
        amended = note if action == "amend" else None
        if action == "amend" and not amended:
            self.notify("请在底部输入框填写改写后的结论。", severity="warning")
            return
        self.store.append_correction(Correction(claim_id=claim.id, verdict=action, amended_statement=amended, note=note if action != "amend" else ""))
        claim.review_status = {"confirm": ReviewStatus.CONFIRMED, "reject": ReviewStatus.REJECTED, "amend": ReviewStatus.AMENDED}[action]
        if amended:
            claim.human_text = amended
        stored = self.store.save_analysis(analysis.draft, analysis.evidence)
        next_claim_id = self._next_pending_claim_id(stored.draft.claims, claim.id)
        self.selected_claim_id = next_claim_id or claim.id
        input_widget = self.query_one("#input", Input)
        input_widget.value = ""
        input_widget.placeholder = "追问当前分析后按 Enter；Ctrl+R 可直接继续"
        self.amending_claim_id = None
        self._render_report()
        self._refresh()
        if next_claim_id:
            self.notify(f"已裁决 {claim.id}，已切换到 {next_claim_id}。")
        else:
            self.notify("所有结论均已裁决。")

    @staticmethod
    def _next_pending_claim_id(claims: list[Claim], current_id: str) -> str | None:
        """Find the next proposed claim, wrapping once at the end of the list."""

        current_index = next((index for index, item in enumerate(claims) if item.id == current_id), -1)
        ordered = claims[current_index + 1 :] + claims[: current_index + 1]
        return next((item.id for item in ordered if item.review_status == ReviewStatus.PROPOSED), None)

    def _refresh(self) -> None:
        analysis = self.store.load_analysis_or_none()
        self._refresh_session_summary()
        self._refresh_clarification()
        claims = analysis.draft.claims if analysis else []
        selector = self.query_one("#claim-select", Select)
        selector.set_options([
            (f"{claim.id} · {claim.review_status}", claim.id)
            for claim in claims
        ])
        claim_ids = {claim.id for claim in claims}
        if self.selected_claim_id not in claim_ids:
            self.selected_claim_id = claims[0].id if claims else None
        if self.selected_claim_id:
            selector.value = self.selected_claim_id
        evidence_ids = {item.id for item in (analysis.evidence if analysis else [])}
        if self.selected_evidence_id not in evidence_ids:
            self.selected_evidence_id = analysis.evidence[0].id if analysis and analysis.evidence else None
        node_ids = {item.id for item in (analysis.draft.journey_nodes if analysis else [])}
        if self.selected_node_id not in node_ids:
            self.selected_node_id = analysis.draft.journey_nodes[0].id if analysis and analysis.draft.journey_nodes else None
        self._refresh_claim_table()
        self._refresh_claim_detail()
        self._refresh_graph_tables()
        self._refresh_graph_detail()
        self._refresh_evidence_table()
        self._refresh_evidence_detail()
        self._refresh_activity_table()
        self._refresh_revision_table()
        self._refresh_revision_detail()
        self._refresh_report()

    def _refresh_clarification(self) -> None:
        analysis = self.store.load_analysis_or_none()
        blocking = next((item for item in (analysis.draft.open_questions if analysis else []) if item.blocking), None)
        panel = self.query_one("#clarification", Static)
        panel.display = blocking is not None
        if blocking is not None:
            panel.update(
                "需要你补充\n\n"
                f"{blocking.question}\n\n"
                f"建议：{blocking.suggested_verification}"
            )

    def _refresh_session_summary(self) -> None:
        summary = self.query_one("#session-summary", Static)
        question = self.session.question.strip().replace("\n\n", "\n")[:520]
        agenda = self.store.load_agenda(self.session)
        queued = [item for item in agenda.topics if item.status == "queued"]
        related = [item for item in agenda.topics if item.child_session_id]
        scope = "\n".join(f"• {item}" for item in self.session.scope_items[:5]) if self.session.scope_items else "自然语言定位"
        if len(self.session.scope_items) > 5:
            scope += f"\n…另有 {len(self.session.scope_items) - 5} 项"
        expansions = "\n".join(f"• {path}" for path in self.session.scope_expansions[-3:]) or "尚无扩展"
        usage = self.session.usage
        summary.update(
            f"当前主问题\n{agenda.primary_focus[:520]}\n\n"
            + (f"原始问题\n{question}\n\n" if agenda.primary_focus.strip() != self.session.question.strip() else "")
            + f"待调查 {len(queued)} · 相关子调查 {len(related)}\n"
            + "\n".join(f"• {item.id} {item.question[:80]}" for item in queued[:4])
            + ("\n…" if len(queued) > 4 else "")
            + "\n\n"
            f"分析起点\n{scope}\n\n"
            f"新增范围（{len(self.session.scope_expansions)}）\n{expansions}\n\n"
            f"成本\n${usage.estimated_cost_usd:.6f}\n"
            f"缓存命中 {usage.prompt_cache_hit_tokens:,} · 新输入 {usage.prompt_cache_miss_tokens:,} · 输出 {usage.completion_tokens:,}\n\n"
            f"当前版本\n{self.session.current_revision_id or '尚无'}\n\n"
            "按 m 打开本地 Mermaid 报告"
        )
        self._refresh_top_status()

    def _refresh_top_status(self) -> None:
        self.query_one("#top-status", Static).update(
            f"{self.repository.name}  ·  {self.session.status}  ·  {self.session.current_revision_id or '尚无版本'}  ·  ${self.session.usage.estimated_cost_usd:.6f}  ·  {self.progress_text}"
        )

    def _refresh_claim_table(self) -> None:
        analysis = self.store.load_analysis_or_none()
        table = self.query_one("#claim-table", DataTable)
        table.clear()
        selected_row = 0
        visible_row = 0
        for claim in (analysis.draft.claims if analysis else []):
            statement = (claim.human_text or claim.statement).replace("\n", " ")
            if self.view_filter and self.view_filter not in f"{claim.id} {claim.review_status} {statement}".lower():
                continue
            table.add_row(str(claim.review_status), claim.id, str(claim.evidence_level), claim.confidence, statement[:100], key=claim.id)
            if claim.id == self.selected_claim_id:
                selected_row = visible_row
            visible_row += 1
        if table.row_count:
            table.move_cursor(row=selected_row, animate=False)

    def _sync_claim_selector(self) -> None:
        if self.selected_claim_id:
            self.query_one("#claim-select", Select).value = self.selected_claim_id

    def _refresh_claim_detail(self) -> None:
        analysis = self.store.load_analysis_or_none()
        detail = self.query_one("#claim-detail", Static)
        claim = next(
            (item for item in (analysis.draft.claims if analysis else []) if item.id == self.selected_claim_id),
            None,
        )
        if claim is None:
            detail.update("结论裁决\n尚无结论。")
            return
        statement = claim.human_text or claim.statement
        detail.update(
            f"{claim.id} · {claim.review_status}\n\n"
            f"{statement}\n\n"
            f"证据：{', '.join(claim.evidence_ids) or '无'}\n\n"
            "快捷键：c 确认 · x 否定 · e 改写"
        )

    def _refresh_graph_tables(self) -> None:
        analysis = self.store.load_analysis_or_none()
        nodes = self.query_one("#graph-nodes", DataTable)
        edges = self.query_one("#graph-edges", DataTable)
        nodes.clear()
        edges.clear()
        if analysis is None:
            return
        for node in analysis.draft.journey_nodes:
            if self.view_filter and self.view_filter not in f"{node.id} {node.kind} {node.label}".lower():
                continue
            nodes.add_row(node.id, node.kind, node.label, key=node.id)
        for index, edge in enumerate(analysis.draft.journey_edges):
            refs = ", ".join(edge.evidence_ids) or str(edge.evidence_level)
            if self.view_filter and self.view_filter not in f"{edge.source_id} {edge.relation} {edge.target_id} {refs}".lower():
                continue
            edges.add_row(edge.source_id, edge.relation, edge.target_id, refs, key=f"edge-{index}")

    def _refresh_graph_detail(self) -> None:
        analysis = self.store.load_analysis_or_none()
        detail = self.query_one("#graph-detail", Static)
        node = next((item for item in (analysis.draft.journey_nodes if analysis else []) if item.id == self.selected_node_id), None)
        if node is None:
            detail.update("尚未生成链路。\n\n按 m 可打开本地 Mermaid 报告。")
            return
        incoming = [edge for edge in analysis.draft.journey_edges if edge.target_id == node.id] if analysis else []
        outgoing = [edge for edge in analysis.draft.journey_edges if edge.source_id == node.id] if analysis else []
        lines = [node.label, "", f"ID: {node.id}", f"类型: {node.kind}", f"证据: {', '.join(node.evidence_ids) or '无'}", "", "入边:"]
        lines.extend(f"  {edge.source_id} --{edge.relation}--> {node.id}" for edge in incoming)
        lines.append("\n出边:")
        lines.extend(f"  {node.id} --{edge.relation}--> {edge.target_id}" for edge in outgoing)
        lines.append("\n按 m 在浏览器打开真正的 Mermaid 图。")
        detail.update("\n".join(lines))

    def _refresh_evidence_table(self) -> None:
        analysis = self.store.load_analysis_or_none()
        table = self.query_one("#evidence-table", DataTable)
        table.clear()
        for item in (analysis.evidence if analysis else []):
            if self.view_filter and self.view_filter not in f"{item.id} {item.path}".lower():
                continue
            table.add_row(item.id, item.path, f"{item.start_line}-{item.end_line}", key=item.id)

    def _refresh_evidence_detail(self) -> None:
        analysis = self.store.load_analysis_or_none()
        detail = self.query_one("#evidence-code", Static)
        item = next((entry for entry in (analysis.evidence if analysis else []) if entry.id == self.selected_evidence_id), None)
        if item is None:
            detail.update("尚无证据。")
            return
        detail.update(Syntax(item.excerpt, code_fence_language(item.path), theme="github-dark", line_numbers=False, word_wrap=False))

    def _refresh_activity_table(self) -> None:
        table = self.query_one("#activity-table", DataTable)
        table.clear()
        for index, event in enumerate(self.store.load_events()):
            timestamp = format_local_timestamp(str(event.get("timestamp", "")), format="%H:%M:%S")
            kind = str(event.get("type", "unknown"))
            step = str(event.get("step", ""))
            details = {key: value for key, value in event.items() if key not in {"timestamp", "type", "response_preview", "arguments"}}
            summary = json.dumps(details, ensure_ascii=False)[:240]
            if self.view_filter and self.view_filter not in f"{kind} {step} {summary}".lower():
                continue
            table.add_row(timestamp, kind, step, summary, key=f"event-{index}")

    def _refresh_revision_table(self) -> None:
        revisions = self.store.list_revisions()
        table = self.query_one("#revision-table", DataTable)
        table.clear()
        revision_ids = {item.id for item in revisions}
        if self.selected_revision_id not in revision_ids:
            self.selected_revision_id = revisions[-1].id if revisions else None
        selected_row = 0
        for row, revision in enumerate(revisions):
            summary = revision.analysis.draft.summary.replace("\n", " ")[:100]
            if self.view_filter and self.view_filter not in f"{revision.id} {revision.kind} {summary}".lower():
                continue
            table.add_row(
                revision.id,
                revision.kind,
                format_local_timestamp(revision.created_at),
                summary,
                key=revision.id,
            )
            if revision.id == self.selected_revision_id:
                selected_row = row
        if table.row_count:
            table.move_cursor(row=min(selected_row, table.row_count - 1), animate=False)

    def _refresh_revision_detail(self) -> None:
        detail = self.query_one("#revision-detail", Static)
        if not self.selected_revision_id:
            detail.update("尚无分析版本。\n\n模型成功形成分析后会自动创建不可变版本。")
            return
        try:
            revision = self.store.load_revision(self.selected_revision_id)
        except (OSError, ValueError) as exc:
            detail.update(f"无法读取版本：{exc}")
            return
        prior_claims: dict[str, str] = {}
        if revision.parent_revision_id:
            try:
                parent = self.store.load_revision(revision.parent_revision_id)
                prior_claims = {item.id: str(item.review_status) for item in parent.analysis.draft.claims}
            except (OSError, ValueError):
                pass
        changes = []
        for claim in revision.analysis.draft.claims:
            old = prior_claims.get(claim.id)
            current = str(claim.review_status)
            if old != current:
                changes.append(f"• {claim.id}: {old or '新增'} → {current}")
        detail.update(
            f"{revision.id} · {revision.kind}\n"
            f"时间：{format_local_timestamp(revision.created_at)}\n"
            f"父版本：{revision.parent_revision_id or '无'}\n\n"
            f"本轮输入\n{revision.user_message or '首次分析/直接继续'}\n\n"
            f"摘要\n{revision.analysis.draft.summary}\n\n"
            f"Claim 变化\n{chr(10).join(changes) or '无状态变化'}\n\n"
            "可在 Ctrl+P 中选择“从版本创建分支”。"
        )

    def _refresh_report(self) -> None:
        self._render_report()
        markdown = self.store.report_path.read_text(encoding="utf-8")
        self.query_one("#overview-report", MarkdownViewer).document.update(markdown)

    def _render_report(self) -> None:
        self.store.render_artifacts()
