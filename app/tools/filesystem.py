"""
Filesystem tools with risk differentiation (reads vs writes vs deletions).
"""
from __future__ import annotations

from pathlib import Path

from app.tools.registry import BaseTool, RiskLevel, ToolResult


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read text content from a specified file path."
    risk_level = RiskLevel.LOW

    async def execute(self, path: str, max_lines: int = 500) -> ToolResult:
        p = Path(path).expanduser()
        if not p.is_file():
            return ToolResult(success=False, error=f"File not found: {path}")

        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = [f.readline() for _ in range(max_lines)]
                content = "".join(lines)
            return ToolResult(success=True, output=content)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class ListDirTool(BaseTool):
    name = "list_dir"
    description = "List files and directories within a target directory."
    risk_level = RiskLevel.LOW

    async def execute(self, path: str = ".") -> ToolResult:
        p = Path(path).expanduser()
        if not p.is_dir():
            return ToolResult(success=False, error=f"Directory not found: {path}")

        try:
            entries = []
            for item in sorted(p.iterdir()):
                entries.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size_bytes": item.stat().st_size if item.is_file() else None,
                })
            return ToolResult(success=True, output=entries)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Create or overwrite a file with specified content."
    risk_level = RiskLevel.MEDIUM

    async def execute(self, path: str, content: str) -> ToolResult:
        p = Path(path).expanduser()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return ToolResult(success=True, output=f"Successfully wrote {len(content)} chars to {path}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class DeleteFileTool(BaseTool):
    name = "delete_file"
    description = "Delete a file from the filesystem. HIGH RISK operation requiring confirmation."
    risk_level = RiskLevel.HIGH
    requires_confirmation = True

    async def execute(self, path: str) -> ToolResult:
        p = Path(path).expanduser()
        if not p.exists():
            return ToolResult(success=False, error=f"Path not found: {path}")

        try:
            if p.is_file() or p.is_symlink():
                p.unlink()
                return ToolResult(success=True, output=f"Deleted file {path}")
            elif p.is_dir():
                p.rmdir()
                return ToolResult(success=True, output=f"Deleted empty directory {path}")
            return ToolResult(success=False, error=f"Cannot delete special file: {path}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
