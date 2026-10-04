"""
Safe terminal execution tool with process-group cleanup and confirmation gating.
Replaces dangerous shell=True blacklist patterns with strict risk-gated execution.
"""
from __future__ import annotations

import asyncio
import os
import signal

from app.tools.registry import BaseTool, RiskLevel, ToolResult

# Prohibited destructive substrings (hard safety invariants)
FORBIDDEN_PATTERNS = [
    "com.oplus.customize.coreapp",
    "com.google.android.devicelockcontroller",
    "rm -rf /",
    "mkfs",
    ":(){ :|:& };:",
]


class RunCommandTool(BaseTool):
    name = "terminal_run"
    description = "Execute a shell command with strict timeout and process group management. HIGH RISK."
    risk_level = RiskLevel.HIGH
    requires_confirmation = True

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float = 30.0,
    ) -> ToolResult:
        cmd_str = command.strip()
        for forbidden in FORBIDDEN_PATTERNS:
            if forbidden in cmd_str:
                return ToolResult(
                    success=False,
                    error=f"Execution blocked: Command contains forbidden critical pattern: '{forbidden}'",
                )

        work_dir = os.path.expanduser(cwd) if cwd else os.getcwd()
        try:
            # Launch in separate process group so timeout can cleanly reap all child processes
            proc = await asyncio.create_subprocess_shell(
                cmd_str,
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
                # Escalating termination: SIGTERM -> SIGKILL to process group
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
