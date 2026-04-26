# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Personal AI backend — a FastAPI service that exposes an OpenAI-compatible API. LibreChat (chat UI) connects to this backend, which routes requests through LiteLLM to the actual LLM providers (Anthropic, OpenAI, etc.) and traces every call with Langfuse.

Full stack via Docker Compose: LibreChat → Backend → LiteLLM → LLM providers, alongside PostgreSQL/pgvector, MongoDB (for LibreChat), Meilisearch, and Mosquitto (MQTT).

## Development Commands

All Python commands use `uv`:

```bash
# Run backend locally (dev, with hot reload)
uv run uvicorn apps.backend.main:app --reload

# Lint / format
uv run ruff check .
uv run ruff format .

# Type check
uv run mypy apps/

# Tests
uv run pytest
uv run pytest path/to/test_file.py::test_name   # single test

# Full stack (Docker)
docker compose up -d
docker compose logs -f backend
```

## Environment Setup

Two `.env` files are required (see `.env.example` and `.env.librechat.example`):

- `.env` — backend secrets: `BACKEND_API_KEY`, `LANGFUSE_*`, `ANTHROPIC_API_KEY`, `DEFAULT_MODEL`
- `.env.librechat` — LibreChat secrets: `CREDS_KEY`, `CREDS_IV`, `JWT_SECRET`, `JWT_REFRESH_SECRET`, `BACKEND_API_KEY`

Both files must share the same `BACKEND_API_KEY` value.

## Architecture

```
apps/backend/
├── main.py              # FastAPI app, mounts routers
├── api/
│   ├── chat.py          # /v1/chat/completions + /v1/models (OpenAI-compatible)
│   └── health.py        # /health
├── core/
│   └── config.py        # Pydantic Settings (reads .env)
├── domain/
│   └── model_gateway.py # Protocol + Pydantic models (CompletionRequest/Response)
└── gateways/
    └── litellm_gateway.py  # Concrete ModelGateway using LiteLLM + Langfuse
```

**Request flow:** LibreChat sends OpenAI-format requests to `/v1/chat/completions` with a Bearer token. `chat.py` authenticates against `BACKEND_API_KEY`, maps the request to a `CompletionRequest`, and delegates to `LiteLLMGateway`. The gateway picks the model (`model_hint` or `DEFAULT_MODEL`), calls `litellm.acompletion`, and returns a `CompletionResponse`. Langfuse tracing is wired globally via `litellm.success_callback`/`failure_callback` — no per-call instrumentation needed.

**Model routing:** `model_hint` in the request overrides `DEFAULT_MODEL`. LibreChat sends `"personal-ai-default"` as the model name, which the API layer maps to `None` so the gateway falls back to `DEFAULT_MODEL`.

**Extensibility point:** `ModelGateway` in `domain/model_gateway.py` is a `Protocol`. New gateway implementations (e.g., with caching, RAG, tool routing) should satisfy that protocol without touching `api/chat.py`.

## Code Style

- `ruff` enforces rules `E, F, I, UP, B` with line-length 100, target Python 3.12.
- `mypy` for static typing; `pydantic-settings` for config.
- `pytest-asyncio` in `auto` mode — async test functions work without decorators.
