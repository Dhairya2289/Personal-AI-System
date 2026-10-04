"""
Unit tests for the Tool Registry, permission gating, and tool execution.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.tools.filesystem import DeleteFileTool, ListDirTool, ReadFileTool, WriteFileTool
from app.tools.registry import ToolRegistry
from app.tools.terminal import RunCommandTool


@pytest.fixture
def clean_registry():
    reg = ToolRegistry()
    reg.register(ReadFileTool())
    reg.register(WriteFileTool())
    reg.register(ListDirTool())
    reg.register(DeleteFileTool())
    reg.register(RunCommandTool())
    return reg


@pytest.mark.asyncio
async def test_file_tools_and_risk_gating(tmp_path: Path, clean_registry: ToolRegistry):
    test_file = tmp_path / "greeting.txt"

    # 1. Write file (Medium risk - permitted)
    w_res = await clean_registry.execute("write_file", {"path": str(test_file), "content": "Hello Dhairya!"})
    assert w_res.success
    assert test_file.exists()

    # 2. Read file (Low risk - permitted)
    r_res = await clean_registry.execute("read_file", {"path": str(test_file)})
    assert r_res.success
    assert "Hello Dhairya!" in r_res.output

    # 3. Delete file without confirmation (High risk - BLOCKED)
    d_res = await clean_registry.execute("delete_file", {"path": str(test_file)}, user_confirmed=False)
    assert not d_res.success
    assert d_res.requires_confirmation
    assert test_file.exists()  # File still exists!

    # 4. Delete file WITH confirmation (High risk - EXECUTED)
    d_confirmed = await clean_registry.execute("delete_file", {"path": str(test_file)}, user_confirmed=True)
    assert d_confirmed.success
    assert not test_file.exists()


@pytest.mark.asyncio
async def test_terminal_tool_safety(clean_registry: ToolRegistry):
    # 1. Safe command without confirmation -> blocked because HIGH risk
    res_unconfirmed = await clean_registry.execute("terminal_run", {"command": "echo 'safe'"}, user_confirmed=False)
    assert not res_unconfirmed.success
    assert res_unconfirmed.requires_confirmation

    # 2. Safe command with confirmation -> executed
    res_confirmed = await clean_registry.execute("terminal_run", {"command": "echo 'safe'"}, user_confirmed=True)
    assert res_confirmed.success
    assert "safe" in res_confirmed.output["stdout"]

    # 3. Forbidden command (even if confirmed) -> hard blocked!
    res_forbidden = await clean_registry.execute(
        "terminal_run",
        {"command": "pm uninstall com.oplus.customize.coreapp"},
        user_confirmed=True,
    )
    assert not res_forbidden.success
    assert "forbidden critical pattern" in res_forbidden.error


@pytest.mark.asyncio
async def test_terminal_does_not_interpret_shell_operators(
    tmp_path: Path, clean_registry: ToolRegistry
):
    marker = tmp_path / "should_not_exist.txt"

    result = await clean_registry.execute(
        "terminal_run",
        {"command": f"echo safe; touch {marker}"},
        user_confirmed=True,
    )

    assert result.success
    assert result.output["stdout"] == f"safe; touch {marker}"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_terminal_preserves_quoted_arguments(clean_registry: ToolRegistry):
    result = await clean_registry.execute(
        "terminal_run",
        {"command": "printf '%s' 'hello world'"},
        user_confirmed=True,
    )

    assert result.success
    assert result.output["stdout"] == "hello world"
