# ADR 002: Process-Group Isolation for Local Tool Execution

## Context
When an autonomous agent invokes terminal commands, long-running processes or hung subshells risk leaking background processes, consuming CPU, and keeping sockets open. Standard `subprocess.Popen.terminate()` only kills the parent shell, leaving child processes orphaned.

## Decision
We execute terminal commands inside an isolated session and process group using `preexec_fn=os.setsid`:
- Subprocess execution: Asynchronous execution via `asyncio.create_subprocess_shell` with strict timeout bounds (default 30s).
- Escalated Termination: On timeout, `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` is issued to the entire process group. If the process does not terminate within 500ms, `signal.SIGKILL` is sent.

## Consequences
- **Pros**: Clean process reaping with zero orphaned child processes.
- **Cons**: POSIX-specific (`setsid` and `killpg`), suitable for the target Linux operating environment.
