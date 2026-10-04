#!/usr/bin/env python3
"""
Open-Source System Packager
Creates sanitized, template-driven system files (systemd units, CLI tools, config templates)
in ~/Projects/dashboard so the full architecture is 100% reproducible without personal PII.
"""

import os
import shutil

DASHBOARD_DIR = "/home/dhairya/Projects/dashboard"

# 1. Systemd Templates
SYSTEMD_DIR = os.path.join(DASHBOARD_DIR, "systemd")
os.makedirs(SYSTEMD_DIR, exist_ok=True)

OMNIROUTE_SERVICE = """[Unit]
Description=OmniRoute AI Central Router
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/omniroute --port 20128
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""

HERMES_GATEWAY_SERVICE = """[Unit]
Description=Hermes Multi-Agent Gateway Service
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/hermes gateway run
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""

HERMES_MEMORY_SYNC_SERVICE = """[Unit]
Description=Universal Multi-Agent Memory Synchronization Service
After=network.target

[Service]
Type=oneshot
ExecStart=%h/bin/hermes-memory-sync
"""

HERMES_MEMORY_SYNC_TIMER = """[Unit]
Description=Universal Multi-Agent Memory Synchronization Timer

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
"""

MISSION_CONTROL_SERVICE = """[Unit]
Description=Mission Control Dashboard (FastAPI, Tailscale-only)
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/dashboard
ExecStart=%h/dashboard/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 51763
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""

with open(os.path.join(SYSTEMD_DIR, "omniroute.service.example"), "w") as f:
    f.write(OMNIROUTE_SERVICE)

with open(os.path.join(SYSTEMD_DIR, "hermes-gateway.service.example"), "w") as f:
    f.write(HERMES_GATEWAY_SERVICE)

with open(os.path.join(SYSTEMD_DIR, "hermes-memory-sync.service.example"), "w") as f:
    f.write(HERMES_MEMORY_SYNC_SERVICE)

with open(os.path.join(SYSTEMD_DIR, "hermes-memory-sync.timer.example"), "w") as f:
    f.write(HERMES_MEMORY_SYNC_TIMER)

with open(os.path.join(SYSTEMD_DIR, "mission-control.service.example"), "w") as f:
    f.write(MISSION_CONTROL_SERVICE)

# 2. Open-Source CLI Tools Directory
CLI_DIR = os.path.join(DASHBOARD_DIR, "cli")
os.makedirs(CLI_DIR, exist_ok=True)

HERMES_MEMORY_SYNC_CLI = """#!/usr/bin/env python3
\"\"\"
Universal Multi-Agent Memory Sync Engine
Synchronizes memory facts across Claude, Codex, Gemini/Antigravity, Hermes, and Mission Control.
\"\"\"
import os
import sqlite3

def sync():
    home = os.path.expanduser("~")
    hermes_db = os.path.join(home, ".hermes", "memory_store.db")
    if not os.path.exists(hermes_db):
        print("Hermes database not found.")
        return

    conn = sqlite3.connect(hermes_db)
    conn.row_factory = sqlite3.Row
    facts = conn.execute("SELECT fact_id, category, content, created_at FROM facts ORDER BY fact_id DESC").fetchall()
    conn.close()

    lines = ["# 🧠 Universal Multi-Agent Memory Index\\n"]
    for f in facts[:100]:
        lines.append(f"- **[{f['category'].upper()}]**: {f['content']} *({f['created_at']})*")

    content = "\\n".join(lines)

    targets = [
        os.path.join(home, ".claude", "MEMORY.md"),
        os.path.join(home, ".codex", "MEMORY.md"),
        os.path.join(home, ".gemini", "GEMINI.md"),
        os.path.join(home, ".config", "ai-workspace", "memory", "MEMORY_INDEX.md")
    ]

    for t in targets:
        os.makedirs(os.path.dirname(t), exist_ok=True)
        with open(t, "w") as fp:
            fp.write(content)

    print("✅ Universal memory sync complete!")

if __name__ == "__main__":
    sync()
"""

with open(os.path.join(CLI_DIR, "hermes-memory-sync"), "w") as f:
    f.write(HERMES_MEMORY_SYNC_CLI)
os.chmod(os.path.join(CLI_DIR, "hermes-memory-sync"), 0o755)

print("✅ Packaged sanitized systemd templates and open-source CLI scripts!")
