from pathlib import Path

import pytest

from code_loop.config import RepositoryConfig
from code_loop.repository_tools import RepositoryTools, ToolError


def test_read_search_and_escape_guard(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def entry():\n    return service()\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=not-for-model", encoding="utf-8")
    tools = RepositoryTools(tmp_path, RepositoryConfig())

    result = tools.search_text("service")
    assert result["matches"] == [{"path": "src/main.py", "line": 2, "text": "    return service()"}]
    read = tools.read_lines("src/main.py", 1, 2)
    assert read["path"] == "src/main.py"
    assert "1: def entry" in read["excerpt"]
    with pytest.raises(ToolError):
        tools.read_lines("../outside.txt")
    with pytest.raises(ToolError):
        tools.read_lines(".env")


def test_read_lines_uses_larger_default_and_configured_cap(tmp_path: Path) -> None:
    content = "\n".join(f"line {number}" for number in range(1, 1001)) + "\n"
    (tmp_path / "large.py").write_text(content, encoding="utf-8")
    tools = RepositoryTools(tmp_path, RepositoryConfig(max_read_lines=800))

    default_read = tools.read_lines("large.py")
    capped_read = tools.read_lines("large.py", 1, 10_000)

    assert default_read["end_line"] == 200
    assert capped_read["end_line"] == 800
    assert "800: line 800" in capped_read["excerpt"]
