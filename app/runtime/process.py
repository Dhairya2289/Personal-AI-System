"""Shared subprocess lifecycle helpers used by dashboard and agents."""

from __future__ import annotations

import asyncio
import os
import signal


def signal_process_group(proc: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    """Signal a subprocess and its child process group."""
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return
    except Exception:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def communicate_with_timeout(
    proc: asyncio.subprocess.Process,
    timeout: float | None,
) -> tuple[bytes, bytes, bool]:
    """Collect subprocess output, terminating the whole group on timeout."""
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return stdout_bytes, stderr_bytes, False
    except asyncio.TimeoutError:
        signal_process_group(proc, signal.SIGTERM)
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=2.0
            )
        except asyncio.TimeoutError:
            signal_process_group(proc, signal.SIGKILL)
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=2.0
            )
        return stdout_bytes, stderr_bytes, True
