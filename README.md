<h1 align="center">Mauricio</h1>
<p align="center">Self-hosted personal AI — voice, chat, WhatsApp, memory, and a self-improvement loop.</p>

---

## What it is

A FastAPI backend that acts as the brain for multiple interfaces: [LibreChat](https://github.com/danny-avila/LibreChat) (web UI), a Raspberry Pi voice satellite, and WhatsApp — all sharing the same long-term memory and knowledge base.

| | |
|:---|:---|
| **Memory** | Facts and preferences extracted after every turn, recalled across channels. Full temporality: supersession, expiry, audit trail. |
| **Knowledge (RAG)** | Markdown files in `knowledge/` chunked, embedded, and searched via pgvector. |
| **Voice** | Wake word (local openwakeword) → Deepgram STT → backend → Piper TTS streaming via PipeWire. Sub-second first-token latency. |
| **WhatsApp** | Responds to your own messages via Evolution API (Baileys). Same memory and tools as web. |
| **Tools** | Web search, notes, time, memory editing, smart lamp (Tapo), and `propose_new_tool`. |
| **Self-improvement** | Tell the assistant to add a tool → triage LLM → Claude Code in a git worktree → PR opened automatically. |
| **Evals** | YAML eval cases run on every PR via GitHub Actions. |
| **Observability** | Every LLM call traced in Langfuse with nested spans. |

---

## Architecture

```
LibreChat  ──┐
Satellite    ├──▶  FastAPI :8000  ──▶  Anthropic / OpenAI
WhatsApp  ───┘         │
                  ┌────┴────────────────┐
                  ▼                     ▼
            PostgreSQL             Langfuse
            + pgvector              Cloud
```

**Per-turn flow:**
1. Retrieve memories + knowledge chunks + summary (parallel)
2. Build enriched system prompt
3. Tool loop — up to 5 × (LLM → tool → LLM)
4. Stream response (SSE for web, NDJSON for voice)
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

## Voice Satellite

Runs on a Raspberry Pi. The full stack (backend, Piper, openwakeword) can run on the Pi itself or on a separate server.

**Pipeline:**
```
wake word (openwakeword) → VAD → Deepgram STT → /v1/voice/turn/stream → Piper TTS (pw-cat)
```

The `/v1/voice/turn/stream` endpoint returns NDJSON `{type, text}` events so the satellite can play `prelim` audio while tool calls run in parallel.

### Deploy from Mac

```bash
# One command — SSH key required (ssh-copy-id pi@<ip> first)
./scripts/deploy-satellite.sh pi@192.168.1.x [satellite-id]
```

Installs system deps, creates venv, writes `.env`, installs systemd service with auto-restart.

### Pi deps (installed automatically by deploy script)

```
portaudio19-dev  python3  uv
```

Install manually on the Pi if needed:

```bash
sudo apt-get install -y openwakeword pipewire pipewire-audio-client-libraries
pip install openwakeword
```

### Satellite env vars

| Var | Default | Description |
|:---|:---|:---|
| `SATELLITE_ID` | `living-room` | Unique ID per satellite |
| `SERVER_HOST` | `192.168.1.100` | Backend host |
| `BACKEND_URL` | `http://$SERVER_HOST:8000` | |
| `BACKEND_API_KEY` | — | Must match backend |
| `WAKE_WORD` | `alexa` | openwakeword model name |
| `WAKE_THRESHOLD` | `0.55` | Detection threshold |
| `DEEPGRAM_API_KEY` | — | Falls back to wyoming-whisper if unset |
| `DEEPGRAM_MODEL` | `nova-2` | |
| `AUDIO_DEVICE` | system default | sounddevice index or ALSA name |
| `LOG_LEVEL` | `INFO` | |

### Manage

```bash
ssh pi@<ip> journalctl -u mauricio-satellite -f
ssh pi@<ip> sudo systemctl restart mauricio-satellite
```

---

## WhatsApp

Uses [Evolution API](https://github.com/EvolutionAPI/evolution-api) (Baileys). Only responds to messages *you* send — incoming messages from others are ignored.

```bash
# Add to .env
EVOLUTION_API_URL=http://evolution:8080
EVOLUTION_API_KEY=your-key
EVOLUTION_INSTANCE=mauricio
EVOLUTION_WEBHOOK_TOKEN=random-long-token

# First run
docker compose exec postgres createdb -U ai evolution
docker compose up -d evolution

# Create instance + get QR code
curl -X POST http://localhost:8080/instance/create \
  -H "apikey: $EVOLUTION_API_KEY" -H "Content-Type: application/json" \
  -d '{"instanceName":"mauricio","qrcode":true,"integration":"WHATSAPP-BAILEYS"}'

curl http://localhost:8080/instance/connect/mauricio -H "apikey: $EVOLUTION_API_KEY"
# Scan QR from WhatsApp → Settings → Linked Devices
```

---

## Self-improvement Loop

Say in LibreChat: *"I want a tool that controls my Spotify"*

1. `propose_new_tool` logs the request and fires triage (async)
2. Triage LLM: **viable** / **clarify_needed** / **not_viable**
3. If viable: Claude Code implements in an isolated git worktree → pytest → `gh pr create`

Requires:
```
REPO_ROOT=/path/to/mauricio
GITHUB_REPO=cuentadesanti/mauricio
```

Also triggerable via GitHub Issues (label `feature-request`) or directly:
```bash
curl -X POST http://localhost:8000/admin/feature-request \
  -H "Authorization: Bearer $BACKEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"spotify","summary":"Control Spotify","use_cases":["play","pause"]}'
```

---

## Prompts

All system prompts live in `prompts/*.md` — edit without touching Python code.

| File | Used by |
|:---|:---|
| `home_assistant.md` | Voice (command mode) |
| `voice_chat.md` | Voice (conversation mode) |
| `whatsapp.md` | WhatsApp |
| `memory_extraction.md` | Background memory extractor |
| `summarization.md` | Conversation summarizer |

Takes effect on `docker compose restart backend`.

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
| `KASA_USERNAME` | — | Tapo smart lamp |
| `KASA_PASSWORD` | — | |
| `LAMP_HOST` | — | Lamp IP |
| `DEFAULT_MODEL` | `anthropic/claude-haiku-4-5` | |
| `STRONG_MODEL` | `anthropic/claude-opus-4-7` | Complex queries |
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | |
| `EVOLUTION_API_URL` | — | WhatsApp |
| `EVOLUTION_API_KEY` | — | |
| `EVOLUTION_WEBHOOK_TOKEN` | — | |
| `REPO_ROOT` | — | Self-improvement loop |
| `GITHUB_REPO` | — | |

---

## Admin API

All endpoints require `Authorization: Bearer $BACKEND_API_KEY`.

| Endpoint | Method | Description |
|:---|:---|:---|
| `/admin/sync-knowledge` | POST | Re-index `knowledge/` |
| `/admin/memory-list` | GET | List memories (`?include_expired=true`) |
| `/admin/memory/{id}/expire` | POST | Expire a memory |
| `/admin/feature-request` | POST | Trigger self-improvement |
| `/health` | GET | Health check (no auth) |

---

## Development

```bash
uv pip install -e ".[dev]"
uv run pytest
uv run ruff check . && uv run ruff format .
uv run mypy apps/

# Rebuild backend after Python changes
docker compose build backend && docker compose up -d backend

# Run evals (needs live DB + API keys)
docker compose exec backend python -m apps.backend.eval.runner
```

---

## Roadmap

- [x] Phase 0 — OpenAI-compatible bridge + LibreChat
- [x] Phase 1 — Persistent chat, model routing, tools
- [x] Phase 2 — Semantic memory, knowledge RAG, summarization, temporality
- [x] Phase 3 — Voice satellite (Raspberry Pi, Deepgram, streaming TTS)
- [x] Phase 4 — WhatsApp (Evolution API)
- [x] Phase 5 — Self-improvement loop, eval framework, prompt externalization
- [ ] Phase 5.5 — Proactive notifications and scheduled reminders
- [ ] Phase 6 — Per-contact knowledge (WhatsApp)
