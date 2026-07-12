from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class WorkspaceError(ValueError):
    """A safe error caused by an invalid workspace or selected path."""


@dataclass(frozen=True)
class WorkspaceChoice:
    root: Path
    seed_items: tuple[str, ...] = ()


def resolve_workspace(target: Path) -> WorkspaceChoice:
    """Resolve a directory to itself, or a file to its nearest Git workspace."""
    target = target.expanduser().resolve()
    if not target.exists():
        raise WorkspaceError(f"Path does not exist: {target}")
    if target.is_dir():
        return WorkspaceChoice(root=target)
    for parent in (target.parent, *target.parents):
        if (parent / ".git").exists():
            return WorkspaceChoice(root=parent, seed_items=(target.relative_to(parent).as_posix(),))
    return WorkspaceChoice(root=target.parent, seed_items=(target.name,))


def normalize_scope_item(root: Path, value: str | Path) -> str:
    raw = Path(value).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not candidate.exists():
        raise WorkspaceError(f"Selected path does not exist: {candidate}")
    try:
        return candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise WorkspaceError("Selected path must remain inside the workspace") from exc


def scope_mode(items: list[str]) -> str:
    return "mixed" if items else "natural_language"


def is_in_seed_scope(path: str, items: list[str]) -> bool:
    if not items:
        return False
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in items)
