# ADR 001: SQLite and FTS5 for Persistent Local Memory

## Context
The system requires persistent, low-latency storage for agent memory items, study tracking, and chat sessions. The solution must run reliably on a single Linux machine without background daemon overhead or external network dependencies.

## Decision
We use SQLite with the FTS5 (Full-Text Search) extension and the `TRUNCATE` journal mode:
- Schema: Base `memory_items` table indexed by `kind`, `mem_type`, `created_at`, with an FTS5 external content virtual table kept in sync via SQLite triggers.
- Ranking: Hybrid scoring combining FTS5 BM25 lexical relevance, per-tier half-life time decay, confidence scoring, and salience.
- Journal Mode: `PRAGMA journal_mode=TRUNCATE` with a 5000ms `busy_timeout` to ensure stable cross-process operation on non-native or shared filesystem mounts.

## Consequences
- **Pros**: Zero infrastructure overhead, fast (<2ms) lexical queries, ACID compliance, trivially backable as single files.
- **Cons**: Single-writer concurrency constraints, handled via async mutex locks in Python (`asyncio.Lock`).
