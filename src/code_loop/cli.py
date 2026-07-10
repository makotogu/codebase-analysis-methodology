from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import load_config
from .repository_tools import RepositoryTools
from .storage import SessionStore
from .tui import CodeLoopApp, DashboardAction, DashboardApp, LauncherApp, SessionLaunch, WorkspaceSetupApp
from .workspace import WorkspaceChoice, WorkspaceError, resolve_workspace


def _repository(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"Not a directory: {path}")
    return path


def _store(repository: Path, session_id: str) -> SessionStore:
    return SessionStore(repository, load_config(repository).repository, session_id)


def cmd_start(args: argparse.Namespace) -> int:
    repository = args.repository
    config = load_config(repository)
    tools = RepositoryTools(repository, config.repository)
    session_id = SessionStore.new_id(args.question)
    store = SessionStore(repository, config.repository, session_id)
    session = store.create(args.question, tools.git_head())
    _run_workbench_chain(repository, config, store, session)
    return 0


def cmd_launch(target: Path | None = None) -> int:
    """Open the workspace dashboard and keep returning to it after sessions."""
    if target is None:
        choice = LauncherApp().run()
    else:
        try:
            choice = resolve_workspace(target)
        except WorkspaceError as exc:
            raise SystemExit(str(exc)) from exc
    if not isinstance(choice, WorkspaceChoice):
        return 0
    repository = choice.root
    config = load_config(repository)
    while True:
        action = DashboardApp(repository, config).run()
        if not isinstance(action, DashboardAction):
            return 0
        if action.kind == "new":
            launch = WorkspaceSetupApp(choice).run()
            if not isinstance(launch, SessionLaunch):
                continue
            session_id = launch.session_id
        elif action.kind == "resume" and action.session_id:
            session_id = action.session_id
        else:
            continue
        store = SessionStore(repository, config.repository, session_id)
        result = _run_workbench_chain(repository, config, store, store.load_session())
        if result is not None:
            # A child chain ends back at the workspace dashboard.
            continue


def cmd_resume(args: argparse.Namespace) -> int:
    repository = args.repository
    config = load_config(repository)
    store = SessionStore(repository, config.repository, args.session_id)
    if not store.session_path.exists():
        raise SystemExit(f"Session not found: {store.session_path}")
    _run_workbench_chain(repository, config, store, store.load_session())
    return 0


def _run_workbench_chain(repository: Path, config, store: SessionStore, session):
    """Follow child-session handoffs without nesting Textual applications."""

    while True:
        result = CodeLoopApp(repository, config, store, session).run()
        if not isinstance(result, SessionLaunch):
            return result
        store = SessionStore(repository, config.repository, result.session_id)
        session = store.load_session()


def cmd_list(args: argparse.Namespace) -> int:
    config = load_config(args.repository)
    root = args.repository / config.repository.output_dir
    if not root.exists():
        return 0
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        store = SessionStore(args.repository, config.repository, directory.name)
        try:
            session = store.load_session()
        except Exception:
            continue
        print(f"{session.id}\t{session.status}\t${session.usage.estimated_cost_usd:.6f}\t{session.question}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    store = _store(args.repository, args.session_id)
    store.render_artifacts()
    print(store.report_path)
    print(store.journey_path)
    print(store.html_report_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="code-loop", description="Evidence-driven, human-correctable code journey analysis")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start", help="Start a new analysis TUI")
    start.add_argument("repository", type=_repository)
    start.add_argument("question", help="One concrete journey or code question")
    start.set_defaults(handler=cmd_start)
    resume = subparsers.add_parser("resume", help="Resume an analysis TUI")
    resume.add_argument("repository", type=_repository)
    resume.add_argument("session_id")
    resume.set_defaults(handler=cmd_resume)
    listing = subparsers.add_parser("list", help="List sessions")
    listing.add_argument("repository", type=_repository)
    listing.set_defaults(handler=cmd_list)
    render = subparsers.add_parser("render", help="Regenerate Markdown, Mermaid, and HTML reports")
    render.add_argument("repository", type=_repository)
    render.add_argument("session_id")
    render.set_defaults(handler=cmd_render)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    commands = {"start", "resume", "list", "render", "-h", "--help"}
    if not arguments:
        return cmd_launch()
    if arguments[0] not in commands:
        return cmd_launch(Path(arguments[0]))
    args = build_parser().parse_args(arguments)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
