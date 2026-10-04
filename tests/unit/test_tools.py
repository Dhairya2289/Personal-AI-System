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

    # 3. Delete file without a confirmation token (High risk - BLOCKED)
    d_res = await clean_registry.execute("delete_file", {"path": str(test_file)})
    assert not d_res.success
    assert d_res.requires_confirmation
    assert test_file.exists()

    # 4. Issue an action-bound confirmation and execute exactly that action.
    token, _ = clean_registry.issue_confirmation(
        "delete_file", {"path": str(test_file)}
    )
    d_confirmed = await clean_registry.execute(
        "delete_file",
        {"path": str(test_file)},
        confirmation_token=token,
    )
    assert d_confirmed.success
    assert not test_file.exists()


@pytest.mark.asyncio
async def test_terminal_tool_safety(clean_registry: ToolRegistry):
    # 1. Safe command without confirmation -> blocked because HIGH risk
    res_unconfirmed = await clean_registry.execute(
        "terminal_run", {"command": "echo 'safe'"}
    )
    assert not res_unconfirmed.success
    assert res_unconfirmed.requires_confirmation

    # 2. Confirm the exact command and execute it.
    token, _ = clean_registry.issue_confirmation(
        "terminal_run", {"command": "echo 'safe'"}
    )
    res_confirmed = await clean_registry.execute(
        "terminal_run",
        {"command": "echo 'safe'"},
        confirmation_token=token,
    )
    assert res_confirmed.success
    assert "safe" in res_confirmed.output["stdout"]

    # 3. Forbidden command is blocked even when confirmed.
    forbidden_args = {"command": "pm uninstall com.oplus.customize.coreapp"}
    forbidden_token, _ = clean_registry.issue_confirmation(
        "terminal_run", forbidden_args
    )
    res_forbidden = await clean_registry.execute(
        "terminal_run",
        forbidden_args,
        confirmation_token=forbidden_token,
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
        confirmation_token=clean_registry.issue_confirmation(
            "terminal_run", {"command": f"echo safe; touch {marker}"}
        )[0],
    )

    assert result.success
    assert result.output["stdout"] == f"safe; touch {marker}"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_terminal_preserves_quoted_arguments(clean_registry: ToolRegistry):
    result = await clean_registry.execute(
        "terminal_run",
        {"command": "printf '%s' 'hello world'"},
        confirmation_token=clean_registry.issue_confirmation(
            "terminal_run", {"command": "printf '%s' 'hello world'"}
        )[0],
    )

    assert result.success
    assert result.output["stdout"] == "hello world"



@pytest.mark.asyncio
async def test_high_risk_confirmation_is_bound_and_single_use(tmp_path: Path):
    reg = ToolRegistry()
    reg.register(DeleteFileTool())

    target = tmp_path / "target.txt"
    other = tmp_path / "other.txt"
    target.write_text("target", encoding="utf-8")
    other.write_text("other", encoding="utf-8")

    token, _ = reg.issue_confirmation("delete_file", {"path": str(target)})

    # The token cannot be redirected to a different action.
    wrong_target = await reg.execute(
        "delete_file",
        {"path": str(other)},
        confirmation_token=token,
    )
    assert not wrong_target.success
    assert wrong_target.requires_confirmation
    assert other.exists()

    # The original action succeeds with the same token.
    deleted = await reg.execute(
        "delete_file",
        {"path": str(target)},
        confirmation_token=token,
    )
    assert deleted.success
    assert not target.exists()

    # Confirmation is single-use and cannot be replayed.
    replay = await reg.execute(
        "delete_file",
        {"path": str(target)},
        confirmation_token=token,
    )
    assert not replay.success
    assert replay.requires_confirmation
