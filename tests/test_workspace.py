from pathlib import Path

import pytest

from code_loop.workspace import WorkspaceError, normalize_scope_item, resolve_workspace, scope_mode


def test_file_selection_uses_nearest_git_workspace(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    target = root / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("pass\n", encoding="utf-8")

    choice = resolve_workspace(target)
    assert choice.root == root
    assert choice.seed_items == ("src/app.py",)
    assert normalize_scope_item(root, "src/app.py") == "src/app.py"
    assert scope_mode(["src/app.py"]) == "mixed"


def test_scope_rejects_path_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    other = tmp_path / "other.py"
    other.write_text("pass\n", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        normalize_scope_item(root, other)
