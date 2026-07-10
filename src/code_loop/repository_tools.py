from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import hashlib
import os
from pathlib import Path
import subprocess
from typing import Any

from .config import RepositoryConfig
from .models import Evidence


class ToolError(ValueError):
    """A safe, user-displayable failure from a repository tool."""


@dataclass
class RepositoryTools:
    root: Path
    config: RepositoryConfig

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        if not self.root.is_dir():
            raise ToolError(f"Repository does not exist: {self.root}")

    def _relative(self, candidate: str) -> tuple[Path, str]:
        path = (self.root / candidate).resolve()
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise ToolError("Path must remain inside the repository") from exc
        text = relative.as_posix()
        if self._excluded(text):
            raise ToolError("Path is excluded by repository policy")
        return path, text

    def _excluded(self, relative: str) -> bool:
        pieces = relative.split("/")
        for pattern in self.config.exclude:
            if pattern in pieces or any(fnmatch.fnmatch(part, pattern) for part in pieces):
                return True
            if pattern.startswith(".") and any(part.startswith(pattern) for part in pieces):
                return True
        return False

    def list_files(self, glob: str = "**/*", limit: int = 100) -> dict[str, Any]:
        limit = min(max(1, limit), self.config.max_search_results)
        paths: list[str] = []
        for path in self.root.glob(glob):
            if not path.is_file():
                continue
            try:
                relative = path.resolve().relative_to(self.root).as_posix()
            except ValueError:
                continue
            if self._excluded(relative) or path.stat().st_size > self.config.max_file_bytes:
                continue
            paths.append(relative)
            if len(paths) >= limit:
                break
        return {"files": sorted(paths), "truncated": len(paths) >= limit}

    def search_text(self, query: str, glob: str = "**/*", limit: int = 30) -> dict[str, Any]:
        if not query.strip():
            raise ToolError("Search query cannot be empty")
        limit = min(max(1, limit), self.config.max_search_results)
        matches: list[dict[str, Any]] = []
        for path in self.root.glob(glob):
            if not path.is_file():
                continue
            try:
                relative = path.resolve().relative_to(self.root).as_posix()
                if self._excluded(relative) or path.stat().st_size > self.config.max_file_bytes:
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeError):
                continue
            for number, line in enumerate(content.splitlines(), start=1):
                if query.lower() in line.lower():
                    matches.append({"path": relative, "line": number, "text": line[:500]})
                    if len(matches) >= limit:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def read_lines(self, path: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:
        absolute, relative = self._relative(path)
        if not absolute.is_file():
            raise ToolError("Path is not a readable file")
        if absolute.stat().st_size > self.config.max_file_bytes:
            raise ToolError("File exceeds configured safe read size")
        start_line = max(1, start_line)
        end_line = min(max(start_line, end_line), start_line + self.config.max_read_lines - 1)
        content = absolute.read_text(encoding="utf-8", errors="replace").splitlines()
        excerpt_lines = content[start_line - 1 : end_line]
        excerpt = "\n".join(f"{number}: {line}" for number, line in enumerate(excerpt_lines, start=start_line))
        digest = hashlib.sha256("\n".join(excerpt_lines).encode()).hexdigest()
        return {
            "path": relative,
            "start_line": start_line,
            "end_line": min(end_line, len(content)),
            "excerpt": excerpt,
            "content_hash": digest,
        }

    def git_log(self, path: str, limit: int = 8) -> dict[str, Any]:
        _, relative = self._relative(path)
        return self._git(["log", f"--max-count={min(max(1, limit), 30)}", "--format=%H|%ad|%an|%s", "--date=short", "--", relative])

    def git_show(self, commit: str, path: str) -> dict[str, Any]:
        _, relative = self._relative(path)
        if not all(char.isalnum() or char in "_-" for char in commit):
            raise ToolError("Invalid commit identifier")
        return self._git(["show", "--no-ext-diff", "--format=", f"{commit}:{relative}"])

    def git_head(self) -> str | None:
        try:
            return self._git_raw(["rev-parse", "HEAD"]).strip() or None
        except ToolError:
            return None

    def _git(self, arguments: list[str]) -> dict[str, Any]:
        return {"output": self._git_raw(arguments)[:16_000]}

    def _git_raw(self, arguments: list[str]) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), *arguments],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except OSError as exc:
            raise ToolError(f"git unavailable: {exc}") from exc
        if result.returncode != 0:
            raise ToolError(result.stderr.strip() or "git command failed")
        return result.stdout

    def evidence_from_read(self, payload: dict[str, Any], evidence_id: str) -> Evidence:
        return Evidence(
            id=evidence_id,
            path=payload["path"],
            start_line=payload["start_line"],
            end_line=max(payload["start_line"], payload["end_line"]),
            excerpt=payload["excerpt"],
            content_hash=payload["content_hash"],
            source="read_lines",
        )


TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": "list_files", "description": "List safe repository files by glob.", "parameters": {"type": "object", "properties": {"glob": {"type": "string"}, "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "search_text", "description": "Search safe repository text case-insensitively.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "glob": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "read_lines", "description": "Read a substantial bounded line range from a safe repository file. Omit line bounds to read the first 200 lines; larger explicit ranges are allowed up to repository policy.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "git_log", "description": "Read recent commit history for a safe file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "git_show", "description": "Read a version of a safe file from git.", "parameters": {"type": "object", "properties": {"commit": {"type": "string"}, "path": {"type": "string"}}, "required": ["commit", "path"]}}},
]
