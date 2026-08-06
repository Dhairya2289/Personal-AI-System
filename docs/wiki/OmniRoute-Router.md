# OmniRoute API Router

OmniRoute is a lightweight OpenAI-compatible local API router listening at `http://127.0.0.1:20128/v1`.

## Provider Pools & Model Combos

OmniRoute aggregates account credentials into fallback model combos:

* **auto/best-chat**: `antigravity/gemini-3.6-flash-high` -> `antigravity/gemini-3.5-flash-low` -> `kimchi/deepseek-v4-flash`
* **auto/best-coding**: `antigravity/gemini-3.6-flash-high` -> `antigravity/gemini-3.5-flash-low` -> `kimchi/deepseek-v4-flash`
* **auto/best-reasoning**: `antigravity/gemini-3.6-flash-high` -> `antigravity/gemini-3.1-pro-low` -> `kimchi/minimax-m3`
* **auto/best-fast**: `antigravity/gemini-3.6-flash-high` -> `antigravity/gemini-3.5-flash-low`

## Health Verification

Verify router health via HTTP GET:
```bash
curl -s http://127.0.0.1:20128/v1/models
```
