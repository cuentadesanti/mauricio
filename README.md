<p align="center">
  <h1 align="center">Mauricio — Personal AI Backend</h1>
  <p align="center">
    A self-hosted, memory-aware AI assistant with persistent conversations, semantic knowledge retrieval, voice control, WhatsApp, and a self-improvement loop.
  </p>
</p>

---

## What is this?

Mauricio is a **personal AI backend** that connects to [LibreChat](https://github.com/danny-avila/LibreChat), a voice satellite (Raspberry Pi), and WhatsApp — all sharing the same memory and knowledge base. It remembers facts about your life, retrieves your personal notes semantically, summarizes long chats, controls your smart home, and can propose and implement its own new capabilities via pull requests.

### Features

| Feature | Description |
| :--- | :--- |
| **Persistent Conversations** | Chats stored in PostgreSQL. Pick up where you left off across sessions and channels. |
| **Long-term Memory** | Facts, preferences, and entities extracted after each turn, recalled in future conversations. Memories can be superseded, expired, and corrected with full audit trail. |
| **Knowledge Base (RAG)** | Markdown notes in `knowledge/` chunked, embedded, and searched via pgvector. |
| **Conversation Summarization** | Long chats (>20 messages) compressed into rolling summaries automatically. |
| **Smart Model Routing** | Simple queries → Claude Haiku. Complex queries → Claude Opus. Transparent to the user. |
| **Tool Ecosystem** | Web search, note-taking, time awareness, memory editing, smart lamp control, and self-improvement. |
| **Voice Satellite** | Raspberry Pi client: wake word → Whisper STT → backend → Piper TTS. Supports persistent voice chat mode. |
| **WhatsApp Channel** | Receive and respond to WhatsApp messages via Evolution API. Same memory and tools as web. |
| **Self-improvement Loop** | Say "add a tool that does X" → feasibility triage → Claude Code implements it in a git worktree → PR opened automatically. |
| **Eval Framework** | YAML-driven eval cases run on every PR via GitHub Actions. Memory recall, tool selection, and regression detection. |
| **Full Observability** | Every LLM call traced in Langfuse with nested spans for tool loops and background jobs. |

---

## Architecture

```
LibreChat (:3080) ──┐
Voice Satellite     ├──▶  FastAPI Backend (:8000)  ──▶  LLM Providers
WhatsApp            ┘          │                        (Anthropic, OpenAI)
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
         PostgreSQL        Langfuse          Evolution API
         + pgvector          Cloud            (WhatsApp)
```

**Request flow (persistent mode):**
1. Parallel retrieval — memories + knowledge chunks + chat summary
2. System prompt construction — enriched with facts, documents, history
3. Tool-calling loop — up to 5 LLM → tool → LLM iterations
4. SSE stream back to client
5. Background jobs — memory extraction + summarization

**Self-improvement flow:**
1. User: *"I want a tool that sends SMS"*
2. `propose_new_tool` fires → triage LLM (viable / clarify / not_viable)
3. If viable: Claude Code runs in an isolated git worktree, implements the tool, tests pass → PR opened on GitHub

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- API keys: [Anthropic](https://console.anthropic.com), [OpenAI](https://platform.openai.com) (embeddings), [Tavily](https://tavily.com) (web search), [Langfuse](https://langfuse.com) (tracing)

### 1. Clone & configure

```bash
git clone https://github.com/cuentadesanti/mauricio.git
cd mauricio
cp .env.example .env
cp .env.librechat.example .env.librechat
# Fill in your API keys in both files
```

### 2. Launch

```bash
docker compose up -d
```

Runs: PostgreSQL/pgvector, MongoDB, Meilisearch, Mosquitto, FastAPI backend (:8000), LibreChat (:3080), Whisper STT (:10300), Piper TTS (:10200), openWakeWord (:10400).

### 3. Open LibreChat

Go to **http://localhost:3080**, create an account, and pick:
- **personal-ai** — full mode (memory + retrieval + tools)
- **personal-ai-quick** — stateless, no DB

---

## Voice Satellite (Raspberry Pi)

The satellite runs on a Pi with a microphone. It connects to the wyoming services on your Mac and the backend.

### One-command deploy from Mac

```bash
./scripts/deploy-satellite.sh [user@pi-address] [satellite-id]

# Examples:
./scripts/deploy-satellite.sh                          # pi@raspberrypi.local, id=living-room
./scripts/deploy-satellite.sh pi@192.168.1.50          # custom IP
./scripts/deploy-satellite.sh pi@192.168.1.50 bedroom  # custom satellite ID
```

The script:
1. Checks SSH connectivity
2. Installs system deps on the Pi (`portaudio`, `python3`, `uv`)
3. Syncs `satellite.py`
4. Creates Python venv and installs requirements
5. Writes `.env` pointing back to your Mac's IP
6. Installs and starts a `systemd` service (auto-restart on reboot)

**Requirements:** SSH key-based access to the Pi. Set up with `ssh-copy-id pi@<address>`.

### Manage the service

```bash
# Logs (live)
ssh pi@raspberrypi.local journalctl -u mauricio-satellite -f

# Restart / stop
ssh pi@raspberrypi.local sudo systemctl restart mauricio-satellite
ssh pi@raspberrypi.local sudo systemctl stop mauricio-satellite
```

### How it works

```
Wake word ("Okay Nabu") → VAD recording → Whisper STT → POST /v1/voice/turn → Piper TTS → speaker
```

Two modes:
- **home_assistant** (default) — one-off commands. Access memory, knowledge, tools.
- **voice_chat** — persistent conversation mode. Say *"start a conversation"* to enter, *"end conversation"* to exit (or 90s silence).

---

## WhatsApp

Mauricio can receive and respond to your own WhatsApp messages via [Evolution API](https://github.com/EvolutionAPI/evolution-api) (Baileys, self-hosted).

**Policy (Opción C):** only responds to messages you send — when you write to yourself ("note to self") or to a dedicated Mauricio number. Incoming messages from others are ignored by default.

### Setup

1. Add to `.env`:
   ```
   EVOLUTION_API_URL=http://evolution:8080
   EVOLUTION_API_KEY=your-key
   EVOLUTION_INSTANCE=mauricio
   EVOLUTION_WEBHOOK_TOKEN=a-long-random-token
   ```

2. Create the Evolution database and start the service:
   ```bash
   docker compose exec postgres createdb -U ai evolution
   docker compose up -d evolution
   ```

3. Create an instance and scan the QR code:
   ```bash
   # Create instance
   curl -X POST http://localhost:8080/instance/create \
     -H "apikey: $EVOLUTION_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"instanceName": "mauricio", "qrcode": true, "integration": "WHATSAPP-BAILEYS"}'

   # Get QR (scan from WhatsApp → Settings → Linked Devices)
   curl http://localhost:8080/instance/connect/mauricio \
     -H "apikey: $EVOLUTION_API_KEY"
   ```

4. Verify connection: `{"state": "open"}` means you're live.

---

## Self-improvement Loop

Tell Mauricio through LibreChat:

> *"I want a tool that controls my Spotify"*

It will:
1. Call `propose_new_tool` with title, summary, and use cases
2. Triage LLM decides: **viable** / **clarify_needed** / **not_viable**
3. If viable: Claude Code spins up in a git worktree, implements the tool, runs `pytest`, then opens a PR

**Requirements:** set in `.env`:
```
REPO_ROOT=/path/to/mauricio   # local path to this repo
GITHUB_REPO=cuentadesanti/mauricio
```

You can also trigger it directly:
```bash
curl -X POST http://localhost:8000/admin/feature-request \
  -H "Authorization: Bearer $BACKEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "spotify_play", "summary": "Control Spotify playback", "use_cases": ["play music", "pause", "skip track"]}'
```

GitHub Actions watches for issues labeled `feature-request` and dispatches them automatically.

---

## Knowledge Base

Drop markdown files into `knowledge/` and they become searchable:

```bash
# Auto-indexed at boot, or manually:
curl -X POST http://localhost:8000/admin/sync-knowledge \
  -H "Authorization: Bearer $BACKEND_API_KEY"
```

Files are parsed (YAML frontmatter), chunked (~1500 chars, 200 overlap), embedded (OpenAI `text-embedding-3-small`), and indexed via pgvector HNSW.

The `note_add` tool lets Mauricio create notes on your behalf.

---

## Prompts

All system prompts live in `prompts/*.md` — plain text, editable without touching code:

| File | Used by |
| :--- | :--- |
| `prompts/home_assistant.md` | Voice satellite (home_assistant mode) |
| `prompts/voice_chat.md` | Voice satellite (voice_chat mode) |
| `prompts/whatsapp.md` | WhatsApp channel |
| `prompts/memory_extraction.md` | Background memory extractor |
| `prompts/summarization.md` | Conversation summarizer |

Changes take effect on next container restart (`docker compose restart backend`).

---

## Admin API

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/admin/sync-knowledge` | POST | Re-index `knowledge/` |
| `/admin/memory-list` | GET | List memories (`?include_expired=true`) |
| `/admin/memory/{id}/expire` | POST | Expire a specific memory |
| `/admin/feature-request` | POST | Trigger self-improvement triage |
| `/health` | GET | Health check (no auth) |

All admin endpoints require `Authorization: Bearer $BACKEND_API_KEY`.

---

## Development

```bash
# Install deps
uv pip install -e ".[dev]"

# Tests (64 unit tests)
uv run pytest

# Lint & format
uv run ruff check .
uv run ruff format .

# Type check
uv run mypy apps/

# Rebuild after changes
docker compose build backend && docker compose up -d backend

# Run evals (needs live DB + API keys)
docker compose exec backend python -m apps.backend.eval.runner
```

---

## Environment Variables

### Required

| Variable | Description |
| :--- | :--- |
| `BACKEND_API_KEY` | Shared auth key (LibreChat ↔ backend) |
| `ANTHROPIC_API_KEY` | Claude Haiku / Opus |
| `OPENAI_API_KEY` | Embeddings (text-embedding-3-small) |
| `LANGFUSE_PUBLIC_KEY` | Tracing |
| `LANGFUSE_SECRET_KEY` | Tracing |
| `LANGFUSE_HOST` | Langfuse endpoint |

### Optional

| Variable | Default | Description |
| :--- | :--- | :--- |
| `TAVILY_API_KEY` | — | Web search tool |
| `KASA_USERNAME` | — | Smart lamp (Tapo L510) |
| `KASA_PASSWORD` | — | Smart lamp |
| `LAMP_HOST` | — | Lamp IP address |
| `DEFAULT_MODEL` | `anthropic/claude-haiku-4-5` | Default LLM |
| `STRONG_MODEL` | `anthropic/claude-opus-4-7` | Complex query LLM |
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Embedding model |
| `EVOLUTION_API_URL` | — | WhatsApp (Evolution API URL) |
| `EVOLUTION_API_KEY` | — | WhatsApp API key |
| `EVOLUTION_WEBHOOK_TOKEN` | — | Webhook auth token |
| `REPO_ROOT` | — | Path to repo root (self-improvement) |
| `GITHUB_REPO` | — | GitHub repo slug (self-improvement) |

---

## Roadmap

- [x] Phase 0 — OpenAI-compatible bridge + LibreChat
- [x] Phase 1 — Persistent chat, model router, tools
- [x] Phase 2 — Semantic memory, knowledge RAG, summarization
- [x] Phase 2.1 — Memory temporality and supersession
- [x] Phase 3 — Voice satellite (Raspberry Pi)
- [x] Phase 4 — WhatsApp channel (Evolution API)
- [x] Phase 5 — Self-improvement loop + eval framework
- [ ] Phase 5.5 — Proactive notifications (scheduled tasks, reminders)
- [ ] Phase 6 — Per-contact knowledge (WhatsApp context per person)

---

## License

Private project. Not yet licensed for public use.
