# Universal Memory Engine

The Universal Memory Engine provides persistent context compounding across all AI agent runtimes on the system.

## Database Schema (`~/.hermes/memory_store.db`)

The primary memory store is a SQLite database containing the `facts` table:

```sql
CREATE TABLE facts (
    fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,
    trust_score REAL DEFAULT 0.5,
    retrieval_count INTEGER DEFAULT 0,
    helpful_count INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    hrr_vector BLOB,
    scope TEXT
);
```

## Memory Synchronization Targets

Executing `sys-engine memory sync` distributes facts to:

1. `~/.claude/MEMORY.md` (Claude CLI)
2. `~/.codex/MEMORY.md` (OpenAI Codex CLI)
3. `~/.gemini/GEMINI.md` (Gemini / Antigravity CLI)
4. `~/.config/ai-workspace/memory/MEMORY_INDEX.md` (Workspace Index)
5. `memory_core.db` (Mission Control Dashboard)
