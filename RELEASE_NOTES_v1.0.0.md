# Personal AI System v1.0.0

Initial release of Personal AI System (Hermes Mission Control), a self-hosted multi-agent operating environment and daily workflow dashboard.

## Module Breakdown

* **Universal Multi-Agent Memory Engine (`sys-engine memory`)**: Synchronizes structured facts across Claude CLI, OpenAI Codex, Gemini / Antigravity CLI, Hermes Agents, and the local FastAPI backend.
* **OmniRoute API Gateway (`:20128`)**: Local OpenAI-compatible API router that load-balances requests across credentials and provider pools with fallback strategies.
* **Academic Goal & Syllabus Engine**: Integrates NTA Physics, Chemistry, and Mathematics syllabi weightages to compute daily study blocks, practice question targets, and adherence stats.
* **Research Paper Module**: Rendered Markdown viewer for technical documents, paper summaries, and derivations.
* **System Diagnostics (`/api/system/health`)**: Aggregates systemd user service states, process locks, disk free space, and active TCP listeners without blocking or connection timeouts.

## Installation & Deployment

Refer to [README.md](https://github.com/Dhairya2289/Personal-AI-System#readme) for installation steps and systemd user service setup.
