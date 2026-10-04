# ADR 004: Action-Bound Confirmation Tokens

## Status

Accepted

## Context

A high-risk tool must not execute merely because an agent or client supplies a generic boolean confirmation. A confirmation must identify the exact action the user approved.

## Decision

High-risk tools use short-lived, single-use confirmation tokens.

A token is bound to:

1. The tool name.
2. The exact JSON arguments supplied for that invocation.
3. A short expiration window (120 seconds by default).

The registry validates the token before execution and consumes it immediately before the tool runs. Reusing a token, changing its arguments, changing the target tool, or waiting past expiration causes the action to be rejected.

## Consequences

- A client cannot redirect a valid confirmation to a different file, command, or tool.
- Confirmation replay is prevented.
- Confirmation state is intentionally in-memory; pending confirmations disappear if the process restarts.
- The confirmation layer is independent of the LLM prompt, so prompt injection cannot manufacture a valid approval token.
