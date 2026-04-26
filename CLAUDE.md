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

# Rebuild after dependency changes
docker compose build backend && docker compose up -d backend
```

## Environment Setup

Two `.env` files are required (see `.env.example` and `.env.librechat.example`):

- `.env` — backend secrets: `BACKEND_API_KEY`, `LANGFUSE_*`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TAVILY_API_KEY`, `KASA_USERNAME`, `KASA_PASSWORD`, `DEFAULT_MODEL`
- `.env.librechat` — LibreChat secrets: `CREDS_KEY`, `CREDS_IV`, `JWT_SECRET`, `JWT_REFRESH_SECRET`, `BACKEND_API_KEY`

Both files must share the same `BACKEND_API_KEY` value.

## Architecture

```
apps/backend/
├── main.py                     # FastAPI app, mounts routers
├── api/
│   ├── chat.py                 # /v1/chat/completions, /v1/responses, /v1/models
│   ├── admin.py                # /admin/sync-knowledge, /admin/memory-list, /admin/memory/{id}/expire
│   └── health.py               # /health
├── core/
│   └── config.py               # Pydantic Settings (reads .env)
├── domain/
│   ├── chat.py                 # ChatMode enum, InboundMessage
│   ├── knowledge.py            # Knowledge domain models
│   ├── memory.py               # Memory domain models
│   └── model_gateway.py        # Protocol + CompletionRequest/Response
├── db/
│   ├── models.py               # SQLAlchemy ORM models
│   ├── repository.py           # All DB access (users, chats, messages, memories, knowledge, summaries)
│   └── session.py              # AsyncSession factory
├── gateways/
│   ├── litellm_gateway.py      # LLM calls via LiteLLM + Langfuse tracing
│   ├── embeddings_gateway.py   # OpenAI text-embedding-3-small via LiteLLM
│   └── s3_storage.py           # Optional S3 sync for knowledge files
├── services/
│   ├── chat_service.py         # Main orchestrator: retrieval, tool loop, post-turn jobs
│   ├── knowledge_service.py    # Chunk + index markdown files; semantic search
│   ├── memory_service.py       # store_unique (dedup by cosine), retrieve_relevant
│   ├── memory_extractor.py     # Background LLM pass: extract facts/prefs/entities, handle supersession
│   ├── summarizer.py           # Compress long chats into rolling summary (>20 msgs)
│   └── router.py               # pick_model: haiku default, opus for complex queries
└── tools/
    ├── registry.py             # REGISTRY dict + openai_tool_specs()
    ├── base.py                 # ToolSpec (Pydantic) + Tool protocol
    ├── time_now.py             # Current time in any IANA timezone
    ├── web_search.py           # Tavily search
    ├── note_add.py             # Save/update markdown note in knowledge/
    ├── note_list.py            # List notes with snippets
    ├── note_read.py            # Read full note content
    ├── memory_edit.py          # expire / correct / add memories explicitly
    └── lamp.py                 # Tapo L510 smart lamp (on/off/toggle/status)
```

## Request Flow

LibreChat sends OpenAI-format requests to `/v1/chat/completions` (or `/v1/responses` for Agents). `chat.py` authenticates via Bearer token, then `ChatService.handle()` orchestrates:

1. **Retrieve context** (parallel): relevant memories + knowledge chunks + chat summary
2. **Build system prompt** with current facts, knowledge excerpts, and summary
3. **LLM tool loop** (up to 5 iterations): model can call any registered tool
4. **Stream response** to LibreChat via fake-SSE
5. **Post-turn jobs** (async, fire-and-forget): `MemoryExtractor` + `Summarizer`

## Model Routing

Two LibreChat models map to two modes:
- `personal-ai` → `ChatMode.PERSISTENT` (full retrieval + memory)
- `personal-ai-quick` → `ChatMode.MEMORYLESS` (no DB, no memory)

`router.pick_model` selects `STRONG_MODEL` (Opus) for complex queries (keywords, length > 800 chars, context > 8000 chars), otherwise `DEFAULT_MODEL` (Haiku). `EXTRACTOR_MODEL` (Haiku) is used for background memory extraction.

## Memory System

Memories live in the `memories` table with temporality fields:
- `valid_until IS NULL` → currently active; filtered in all retrieval queries
- `superseded_by` → links expired memory to its replacement
- `confidence`, `valid_from` → for future use

`MemoryExtractor` receives the active memory list (with IDs) and the latest exchange, then outputs JSON with `{facts, preferences, entities, expire}`. Each item is `{content, valid_from, supersedes: [ids]}`. The extractor chains supersession automatically.

The `memory_edit` tool allows explicit user-driven corrections without waiting for background extraction.

## Knowledge Base

Markdown files in `knowledge/` are chunked (~1500 chars, 200 overlap), embedded, and stored in `knowledge_chunks`. Auto-synced at boot; manual re-sync via `POST /admin/sync-knowledge`. Semantic search uses pgvector cosine distance.

## Smart Home

`lamp` tool controls a Tapo L510 at `192.168.1.26` via `python-kasa`. Requires `KASA_USERNAME` and `KASA_PASSWORD`. Uses `Discover.discover_single` (auto-detects KLAP/port-80 protocol) with 3-attempt retry.

## DB Schema

9 tables across 3 migrations:
- `0001`: users, chats, messages, events
- `0002`: memories, chat_summaries, knowledge_docs, knowledge_chunks
- `0003`: adds valid_from, valid_until, superseded_by, confidence to memories + `ix_memories_active` partial index

## Code Style

- `ruff` enforces rules `E, F, I, UP, B` with line-length 100, target Python 3.12.
- `mypy` for static typing; `pydantic-settings` for config.
- `pytest-asyncio` in `auto` mode — async test functions work without decorators.
- 38 unit tests covering tools, chat service, memory extractor, knowledge utils, and router.
