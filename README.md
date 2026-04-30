<h1 align="center">Mauricio</h1>
<p align="center">Self-hosted personal AI — voice, chat, WhatsApp, memory, and a self-improvement loop.</p>

---

## What it is

A FastAPI backend that acts as the brain for multiple interfaces: [LibreChat](https://github.com/danny-avila/LibreChat) (web UI), a Raspberry Pi voice satellite, and WhatsApp — all sharing the same long-term memory and knowledge base.

| | |
|:---|:---|
| **Memory** | Facts and preferences extracted after every turn, recalled across channels. Full temporality: supersession, expiry, audit trail. |
| **Knowledge (RAG)** | Markdown files in `knowledge/` chunked, embedded, and searched via pgvector. |
| **Voice** | Local openwakeword → Deepgram STT → NDJSON streaming backend → Piper TTS via PipeWire. Plays prelim audio while tools run. |
| **WhatsApp** | Responds to your own messages via Evolution API (Baileys). Same memory and tools as web. |
| **Tools** | Web search, notes, time, memory editing, smart lamp (Tapo), reminders, `propose_new_tool`. |
| **Scheduler** | `schedule_create` tool lets the assistant queue one-shot reminders. Sidecar process polls every minute and dispatches due jobs. |
| **Audio events** | Satellite posts non-speech events (double clap → lamp toggle) without LLM round-trip. |
| **Self-improvement** | Tell the assistant to add a tool → triage LLM → Claude Code in a git worktree → PR opened automatically. |
| **Evals** | YAML eval cases run on every PR. Periodic cron runner tracks pass rate over time. |
| **Observability** | Every LLM call traced in Langfuse with nested spans. |

---

## Architecture

```
LibreChat  ──┐
Satellite    ├──▶  FastAPI :8000  ──▶  Anthropic / OpenAI
WhatsApp  ───┘         │
                  ┌────┴──────────────┐
                  ▼                   ▼
            PostgreSQL           Langfuse
            + pgvector             Cloud
```

**Per-turn flow:**
1. Retrieve memories + knowledge chunks + summary (parallel)
2. Build enriched system prompt (Anthropic prompt caching on base block)
3. Tool loop — up to 5 × (LLM → tool → LLM)
4. Stream response: SSE for web, NDJSON for voice
5. Background: memory extraction + summarization

---

## Quick Start

**Prerequisites:** Docker, API keys for Anthropic, OpenAI (embeddings), Langfuse.

```bash
git clone https://github.com/cuentadesanti/mauricio.git
cd mauricio
cp .env.example .env && cp .env.librechat.example .env.librechat
# fill in keys
docker compose up -d
```

Open **http://localhost:3080** → create account → choose:
- `personal-ai` — full memory + retrieval + tools
- `personal-ai-quick` — stateless, no DB

---

## Voice Satellite (Raspberry Pi)

**Pipeline:**
```
openwakeword (local) → VAD → Deepgram STT → /v1/voice/turn/stream → Piper TTS (pw-cat)
```

`/v1/voice/turn/stream` returns NDJSON `{type, text}` events so the satellite plays `prelim` audio while tool calls run in parallel on the backend.

### Deploy from Mac

```bash
# SSH key required first: ssh-copy-id pi@<ip>
./scripts/deploy-satellite.sh pi@192.168.1.x [satellite-id]
```

Installs system deps, creates venv, writes `.env`, installs and starts a `systemd` service.

### Pi dependencies

```bash
sudo apt-get install -y portaudio19-dev python3 pipewire pipewire-audio-client-libraries
pip install openwakeword
```

### Key satellite env vars

| Var | Default | Notes |
|:---|:---|:---|
| `SERVER_HOST` | `192.168.1.100` | Backend host |
| `BACKEND_API_KEY` | — | Must match backend |
| `WAKE_WORD` | `alexa` | openwakeword model |
| `WAKE_THRESHOLD` | `0.55` | |
| `DEEPGRAM_API_KEY` | — | Falls back to wyoming-whisper if unset |
| `AUDIO_DEVICE` | system default | sounddevice index |

### Manage

```bash
ssh pi@<ip> journalctl -u mauricio-satellite -f
ssh pi@<ip> sudo systemctl restart mauricio-satellite
```

---

## WhatsApp

Uses [Evolution API](https://github.com/EvolutionAPI/evolution-api) (Baileys). Only responds to messages *you* send (`is_from_me=True`). Set `WHATSAPP_ONLY_JID` to lock to a single chat.

```bash
# Add to .env
EVOLUTION_API_URL=http://evolution:8080
EVOLUTION_API_KEY=your-key
EVOLUTION_INSTANCE=mauricio
EVOLUTION_WEBHOOK_TOKEN=random-long-token

# Setup
docker compose exec postgres createdb -U ai evolution
docker compose up -d evolution

# Create instance and scan QR
curl -X POST http://localhost:8080/instance/create \
  -H "apikey: $EVOLUTION_API_KEY" -H "Content-Type: application/json" \
  -d '{"instanceName":"mauricio","qrcode":true,"integration":"WHATSAPP-BAILEYS"}'

curl http://localhost:8080/instance/connect/mauricio -H "apikey: $EVOLUTION_API_KEY"
# Scan QR from WhatsApp → Settings → Linked Devices
```

---

## Self-improvement Loop

Say in LibreChat: *"I want a tool that controls my Spotify"*

1. `propose_new_tool` logs the request and fires triage async
2. Triage LLM: **viable** / **clarify_needed** / **not_viable**
3. If viable: Claude Code implements in a git worktree → `uv run pytest` → `gh pr create`

Requires:
```
REPO_ROOT=/path/to/mauricio
GITHUB_REPO=cuentadesanti/mauricio
```

Also triggerable via GitHub Issues (label `feature-request`) or HTTP:
```bash
curl -X POST http://localhost:8000/admin/feature-request \
  -H "Authorization: Bearer $BACKEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"spotify","summary":"Control Spotify","use_cases":["play","pause"]}'
```

---

## Prompts

All system prompts are in `prompts/*.md` — edit without touching code.

| File | Used by |
|:---|:---|
| `home_assistant.md` | Voice (command mode) |
| `voice_chat.md` | Voice (conversation mode) |
| `whatsapp.md` | WhatsApp |
| `memory_extraction.md` | Memory extractor |
| `summarization.md` | Summarizer |

Takes effect on `docker compose restart backend`.

---

## Admin API

All endpoints require `Authorization: Bearer $BACKEND_API_KEY`.

| Endpoint | Method | Description |
|:---|:---|:---|
| `/admin/sync-knowledge` | POST | Re-index `knowledge/` |
| `/admin/memory-list` | GET | List memories (`?include_expired=true`) |
| `/admin/memory/{id}/expire` | POST | Expire a memory |
| `/admin/feature-request` | POST | Trigger self-improvement |
| `/health` | GET | No auth |

---

## Environment Variables

### Required

| Var | Description |
|:---|:---|
| `BACKEND_API_KEY` | Shared auth (LibreChat ↔ backend) |
| `ANTHROPIC_API_KEY` | Claude models |
| `OPENAI_API_KEY` | Embeddings |
| `LANGFUSE_PUBLIC_KEY` | Tracing |
| `LANGFUSE_SECRET_KEY` | Tracing |
| `LANGFUSE_HOST` | Langfuse endpoint |

### Optional

| Var | Default | Description |
|:---|:---|:---|
| `TAVILY_API_KEY` | — | Web search |
| `KASA_USERNAME` / `KASA_PASSWORD` | — | Tapo smart lamp |
| `LAMP_HOST` | — | Lamp IP |
| `DEFAULT_MODEL` | `anthropic/claude-haiku-4-5` | |
| `STRONG_MODEL` | `anthropic/claude-opus-4-7` | Complex queries |
| `EVOLUTION_API_URL` / `EVOLUTION_API_KEY` | — | WhatsApp |
| `EVOLUTION_WEBHOOK_TOKEN` | — | Webhook auth |
| `WHATSAPP_ONLY_JID` | — | Lock WhatsApp to one chat JID |
| `REPO_ROOT` / `GITHUB_REPO` | — | Self-improvement loop |

---

## Development

```bash
uv pip install -e ".[dev]"
uv run pytest
uv run ruff check . && uv run ruff format .
uv run mypy apps/

docker compose build backend && docker compose up -d backend

# Evals
docker compose exec backend python -m apps.backend.eval.runner
docker compose exec backend python -m apps.backend.eval.cron
```

---

## Roadmap

- [x] Phase 0 — OpenAI-compatible bridge + LibreChat
- [x] Phase 1 — Persistent chat, model routing, tools
- [x] Phase 2 — Semantic memory, knowledge RAG, summarization, temporality
- [x] Phase 3 — Voice satellite (Raspberry Pi, Deepgram, NDJSON streaming, prompt caching)
- [x] Phase 4 — WhatsApp (Evolution API)
- [x] Phase 5 — Self-improvement loop, eval framework, prompt externalization
- [x] Phase 5.5 — Scheduler sidecar + schedule_create tool + audio events (double clap)
- [ ] Phase 6 — Per-contact knowledge (WhatsApp)
