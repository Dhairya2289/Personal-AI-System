"""
Agent tools package with standardized registration and permissions.
"""
from app.tools.filesystem import DeleteFileTool, ListDirTool, ReadFileTool, WriteFileTool
from app.tools.registry import (
    BaseTool,
    RiskLevel,
    ToolDefinition,
    ToolParameter,
    ToolRegistry,
    ToolResult,
    get_tool_registry,
)
from app.tools.research import MemoryRecordTool, MemorySearchTool
from app.tools.terminal import RunCommandTool

# Auto-register core tool suite
_registry = get_tool_registry()
_registry.register(ReadFileTool())
_registry.register(ListDirTool())
_registry.register(WriteFileTool())
_registry.register(DeleteFileTool())
_registry.register(RunCommandTool())
_registry.register(MemorySearchTool())
_registry.register(MemoryRecordTool())

__all__ = [
    "BaseTool",
    "DeleteFileTool",
    "ListDirTool",
    "MemoryRecordTool",
    "MemorySearchTool",
    "ReadFileTool",
    "RiskLevel",
    "RunCommandTool",
    "ToolDefinition",
    "ToolParameter",
    "ToolRegistry",
    "ToolResult",
    "WriteFileTool",
    "get_tool_registry",
]
