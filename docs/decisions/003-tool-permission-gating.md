# ADR 003: Multi-Tier Permission Model for Agent Tools

## Context
Early agent prototypes relied on prompt instructions or string-based blacklists to prevent dangerous actions (e.g. `rm -rf`, unexpected system reboots). Prompt-based guardrails are vulnerable to injection, hallucinations, and bypasses.

## Decision
We categorize all tools into deterministic risk tiers enforced at the application registry level before tool execution:
- `LOW` (Read-only): File reading, directory listing, memory search, calculation. Automatically executed.
- `MEDIUM` (State-creating): Note creation, file modification. Permitted with audit logging.
- `HIGH` (Destructive / System-altering): File deletion, terminal execution, system modifications. Blocked by default; execution requires an explicit user confirmation token.

Additionally, critical boot invariants (e.g. system package debloat protection) are hardcoded and non-overridable.

## Consequences
- **Pros**: Deterministic runtime safety independent of LLM temperature or system prompts.
- **Cons**: Interactive workflows require user confirmation round-trips for high-risk commands.
