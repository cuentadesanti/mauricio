# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Overview

Personal AI backend — FastAPI service exposing an OpenAI-compatible API. LibreChat (web UI), a Raspberry Pi voice satellite, and WhatsApp all connect to this backend, which routes requests through LiteLLM to Anthropic/OpenAI and traces every call with Langfuse.

Full stack via Docker Compose: LibreChat → Backend → LiteLLM → LLM providers, alongside PostgreSQL/pgvector, MongoDB, Meilisearch, and Mosquitto (MQTT).

## Development Commands

```bash
# Run backend locally (dev, hot reload)
uv run uvicorn apps.backend.main:app --reload

# Lint / format
uv run ruff check .
uv run ruff format .

# Type check
uv run mypy apps/

# Tests
uv run pytest
uv run pytest path/to/test_file.py::test_name

# Full stack (Docker)
docker compose up -d
docker compose logs -f backend

# Rebuild after dependency changes
docker compose build backend && docker compose up -d backend

# Run evals (needs live DB + API keys)
docker compose exec backend python -m apps.backend.eval.runner
```

## Environment Setup

Two `.env` files required (see `.env.example` and `.env.librechat.example`):

- `.env` — backend: `BACKEND_API_KEY`, `LANGFUSE_*`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TAVILY_API_KEY`, `KASA_*`, `LAMP_HOST`, `EVOLUTION_*`, `REPO_ROOT`, `GITHUB_REPO`
- `.env.librechat` — LibreChat: `CREDS_KEY`, `CREDS_IV`, `JWT_SECRET`, `JWT_REFRESH_SECRET`, `BACKEND_API_KEY`

Both files must share the same `BACKEND_API_KEY`.

## Architecture

```
apps/backend/
├── main.py
├── api/
│   ├── chat.py                 # /v1/chat/completions, /v1/responses, /v1/models
│   ├── voice.py                # /v1/voice/turn, /v1/voice/turn/stream (NDJSON), /v1/voice/satellite/{id}/state
│   ├── whatsapp.py             # /v1/whatsapp/webhook (Evolution API)
│   ├── admin.py                # /admin/sync-knowledge, /admin/memory-list, /admin/feature-request
│   └── health.py
├── adapters/
│   └── whatsapp_evolution.py   # parse_evolution_webhook + send_whatsapp_text
├── core/
│   ├── config.py               # Pydantic Settings
│   ├── prompts.py              # load_prompt(name) — reads prompts/*.md, lru_cache
│   └── json_utils.py           # parse_json_lenient — handles markdown-fenced LLM JSON
├── domain/
│   ├── chat.py                 # ChatMode enum (PERSISTENT, MEMORYLESS, HOME_ASSISTANT)
│   └── model_gateway.py        # CompletionRequest/Response
├── db/
│   ├── models.py               # SQLAlchemy ORM (11 tables)
│   ├── repository.py           # All DB access; memory min_score=0.3 for cross-language queries
│   └── session.py              # AsyncSession factory
├── eval/
│   ├── runner.py               # YAML eval runner — runs cases against live ChatService
│   ├── cron.py                 # Periodic runner: logs pass rate to events table, alerts on drop
│   └── cases/                  # memory_recall.yaml, tool_selection.yaml
├── gateways/
│   ├── litellm_gateway.py      # LLM calls via LiteLLM + Langfuse
│   └── embeddings_gateway.py   # OpenAI text-embedding-3-small
├── services/
│   ├── chat_service.py         # Main orchestrator; also: collect_response_streaming, build_voice_system_blocks
│   ├── voice_orchestrator.py   # stream_voice_turn + VoiceOrchestrator (home_assistant / voice_chat)
│   ├── feature_request_service.py  # LLM triage (viable/clarify/not_viable)
│   ├── improvement_orchestrator.py # git worktree → claude --print → pytest → gh pr create
│   ├── knowledge_service.py
│   ├── memory_service.py
│   ├── memory_extractor.py     # Uses parse_json_lenient for robust LLM JSON parsing
│   ├── summarizer.py
│   └── router.py
└── tools/
    ├── registry.py             # REGISTRY dict; propose_new_tool gated on repo_root+github_repo
    ├── base.py
    ├── time_now.py
    ├── web_search.py
    ├── note_add.py / note_list.py / note_read.py
    ├── memory_edit.py / memory_list.py
    ├── chat_search.py
    ├── propose_new_tool.py     # contexts=("web",) only
    ├── schedule_create.py      # queue one-shot reminders; naive datetimes → Europe/Madrid
    ├── start_voice_chat.py / end_voice_chat.py
    └── lamp.py                 # Tapo L510 via python-kasa

scheduler/
└── main.py                     # sidecar: polls schedules table every SCHEDULER_INTERVAL_S,
                                # dispatches by kind (reminder → log event; more kinds TBD)

apps/voice-satellite/
├── satellite.py                # Pi client: local OWW wake word → Deepgram STT → /v1/voice/turn/stream → Piper TTS (pw-cat)
└── satellite.service           # systemd unit template

prompts/                        # Editable system prompts (load_prompt reads these)
├── home_assistant.md
├── voice_chat.md
├── whatsapp.md
├── memory_extraction.md
└── summarization.md

scripts/
└── deploy-satellite.sh         # One-command Mac→Pi deploy over SSH
```

## Request Flow

### Chat (LibreChat)
`ChatService.handle()` — PERSISTENT mode:
1. Parallel: memories + knowledge chunks + summary
2. Build enriched system prompt (base_system + memory/knowledge blocks)
3. Tool loop (up to 5 iterations)
4. SSE stream to LibreChat
5. Background: MemoryExtractor + Summarizer

### Voice (Satellite)
Pi runs `satellite.py`: local openwakeword → VAD recording → Deepgram STT (fallback: wyoming-whisper) → `POST /v1/voice/turn/stream` → Piper TTS via `pw-cat` (streaming, first audio before full response).

`/v1/voice/turn/stream` returns NDJSON `{type, text}` events: `prelim` (filler while tool runs) → `final` → `done`.

`stream_voice_turn` uses `collect_response_streaming` which yields `(kind, text)` tuples — prelim is emitted before tool calls execute, so TTS can start immediately.

Voice system prompt uses Anthropic prompt caching: `build_voice_system_blocks` splits into a cacheable base block + uncached dynamic block (time + memories + knowledge).

`VoiceOrchestrator` modes:
- **home_assistant** (default): one-off commands, `ChatMode.HOME_ASSISTANT`
- **voice_chat**: persistent chat, 90s timeout, `ChatMode.PERSISTENT`

### WhatsApp
Evolution API → `POST /v1/whatsapp/webhook` → `_process_inbound` (background task) → `ChatService` PERSISTENT with `external_id=chat_jid` → one eternal chat per contact JID → `send_whatsapp_text`. Only `is_from_me=True` messages processed (Opción C). Optional `WHATSAPP_ONLY_JID` filter.

### Self-improvement
`propose_new_tool` (web-only) → `FeatureRequestService._triage` → if viable: `ImprovementOrchestrator` creates git worktree, runs `claude --print --dangerously-skip-permissions`, runs `uv run pytest`, commits, pushes, `gh pr create`. Title sanitized via regex before use in branch/path names.

## Memory System

- `valid_until IS NULL` → active
- `superseded_by` → links to replacement
- `parse_json_lenient` handles markdown-fenced or prose-wrapped JSON from the extractor LLM
- `min_score=0.3` in `search_memories` (lowered from 0.5) — cross-language queries (Spanish user, English memories) land in 0.3–0.5 cosine similarity range

## Audio Events

`POST /v1/audio-event` — satellite posts structured non-speech detector events. Currently: `double_clap` → lamp toggle (no LLM round-trip). Future events add a handler in `api/audio_events.py:_dispatch`.

## Scheduler

`scheduler/main.py` runs as a sidecar process (separate `docker-compose` service or systemd timer). Polls `schedules` table for `status=pending` rows past `run_at`, dispatches by `kind`, marks `done`/`failed`. `SCHEDULER_INTERVAL_S` env var (default 60). `schedule_create` tool lets the LLM queue jobs directly.

## DB Schema

12 tables across 6 migrations:
- `0001`: users, chats, messages, events
- `0002`: memories, chat_summaries, knowledge_docs, knowledge_chunks
- `0003`: valid_from, valid_until, superseded_by, confidence on memories
- `0004`: satellites (id, user_id, mode, active_chat_id, mode_until, last_seen_at)
- `0005`: chats.external_id + ix_chats_channel_external_id (WhatsApp chat mapping)
- `0006`: schedules (id, user_id, kind, payload, run_at, status, last_error, completed_at)

## Code Style

- `ruff` rules `E, F, I, UP, B`, line-length 100, target Python 3.12
- `per-file-ignores` E501 on: feature_request_service, improvement_orchestrator, config, propose_new_tool, test_memory_extractor
- `mypy` for static typing; `pydantic-settings` for config
- `pytest-asyncio` in `auto` mode
- `tests/conftest.py` sets minimum env vars so `Settings()` doesn't fail in test environments with empty `.env`
- 64 tests (unit + integration)
