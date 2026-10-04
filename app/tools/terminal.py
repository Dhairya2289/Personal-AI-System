"""
Safe terminal execution tool with process-group cleanup and confirmation gating.

Commands are tokenized with shlex and executed directly without a shell.
That means shell metacharacters (pipes, redirects, command substitution, &&, etc.)
are treated as arguments instead of being interpreted.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import signal

from app.tools.registry import BaseTool, RiskLevel, ToolResult

FORBIDDEN_PATTERNS = [
    "com.oplus.customize.coreapp",
    "com.google.android.devicelockcontroller",
    "rm -rf /",
    "mkfs",
    ":(){ :|:& };:",
]


class RunCommandTool(BaseTool):
    name = "terminal_run"
    description = (
        "Execute a tokenized local command without shell interpretation, with strict timeout "
        "and process-group management. HIGH RISK."
    )
    risk_level = RiskLevel.HIGH
    requires_confirmation = True

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float = 30.0,
    ) -> ToolResult:
        cmd_str = command.strip()
        if not cmd_str:
            return ToolResult(success=False, error="Command cannot be empty.")

        for forbidden in FORBIDDEN_PATTERNS:
            if forbidden in cmd_str:
                return ToolResult(
                    success=False,
                    error=f"Execution blocked: Command contains forbidden critical pattern: '{forbidden}'",
                )

        try:
            argv = shlex.split(cmd_str, posix=True)
        except ValueError as exc:
            return ToolResult(success=False, error=f"Invalid command quoting: {exc}")

        if not argv:
            return ToolResult(success=False, error="Command cannot be empty.")

        work_dir = os.path.expanduser(cwd) if cwd else os.getcwd()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                preexec_fn=os.setsid,
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    await asyncio.sleep(0.5)
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                return ToolResult(
                    success=False,
                    error=f"Command timed out after {timeout} seconds and was killed.",
                )

            stdout = stdout_data.decode("utf-8", errors="replace").strip()
            stderr = stderr_data.decode("utf-8", errors="replace").strip()

            return ToolResult(
                success=(proc.returncode == 0),
                output={
                    "returncode": proc.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                },
                error=stderr if proc.returncode != 0 else None,
            )

        except Exception as exc:
            return ToolResult(success=False, error=f"Process launch failed: {exc}")
