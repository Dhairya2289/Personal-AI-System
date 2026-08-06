# System Architecture

Personal AI System is organized into four distinct operational layers: Client Experience, FastAPI Backend Engine, Universal Memory Layer, and OmniRoute API Router.

## Component Breakdown

### 1. Client Experience Layer
* **Browser SPA**: Built using Alpine.js and vanilla CSS (`static/style.css`), served by FastAPI on loopback/Tailscale interface (`127.0.0.1:51763`).
* **Discord AI Gateways**: Two distinct Discord bots (Personal Assistant & Study Companion) managed by `hermes-gateway.service`.
* **Terminal CLI**: Support for Claude CLI, OpenAI Codex CLI, and Gemini / Antigravity CLI.

### 2. FastAPI Backend Engine
* **main.py**: Central application entrypoint routing traffic across modular domain endpoints.
* **system_health.py**: Non-blocking diagnostic module monitoring unit files and listeners.
* **tracker.py**: Syllabus plan and daily study block engine.
* **research.py**: Markdown renderer for technical research papers.

### 3. Universal Memory Layer
* **~/.hermes/memory_store.db**: SQLite database storing structured knowledge facts.
* **sys-engine memory**: Automated sync script distributing facts to provider files (`MEMORY.md`, `GEMINI.md`, `MEMORY_INDEX.md`).

### 4. OmniRoute Router
* **127.0.0.1:20128**: Local OpenAI-compatible server load-balancing model requests across provider pools.
