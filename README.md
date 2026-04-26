<p align="center">
  <h1 align="center">🧠 Mauricio — Personal AI Backend</h1>
  <p align="center">
    A self-hosted, memory-aware AI assistant with persistent conversations, semantic knowledge retrieval, and smart home control.
  </p>
</p>

---

## What is this?

Mauricio is a **personal AI backend** that sits behind [LibreChat](https://github.com/danny-avila/LibreChat) and turns it into a long-term-memory assistant that *knows you*. It remembers facts about your life, retrieves information from your personal notes, summarizes long conversations, and can control your smart home — all while routing queries to the cheapest model that can handle them.

### Key Features

| Feature | Description |
| :--- | :--- |
| 🗂 **Persistent Conversations** | Chats are stored in PostgreSQL. Pick up where you left off across sessions. |
| 🧠 **Long-term Memory** | Facts, preferences, and entities are extracted automatically after each turn and recalled in future conversations. |
| 📚 **Knowledge Base (RAG)** | Your markdown notes in `knowledge/` are chunked, embedded, and searched semantically via pgvector. |
| 🔄 **Memory Temporality** | Memories can be superseded, expired, and corrected — the system tracks *when* facts became true and what replaced them. |
| 📝 **Conversation Summarization** | Long chats (>20 messages) are automatically compressed into rolling summaries to save context window space. |
| 🔀 **Smart Model Routing** | Simple queries go to Claude Haiku (cheap/fast), complex ones to Claude Opus (powerful). Transparent to the user. |
| 🛠 **Tool Ecosystem** | Web search (Tavily), note-taking, time awareness, memory editing, and smart lamp control — all via function calling. |
| 📊 **Full Observability** | Every LLM call is traced in [Langfuse](https://langfuse.com) with nested spans for tool loops and background jobs. |

---

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  LibreChat   │────▶│  FastAPI Backend  │────▶│  LLM Providers  │
│  (Chat UI)   │◀────│  (this repo)     │◀────│  (Anthropic,    │
│  :3080       │     │  :8000           │     │   OpenAI)       │
└──────────────┘     └────────┬─────────┘     └─────────────────┘
                              │
                    ┌─────────┼──────────┐
                    ▼         ▼          ▼
              ┌──────────┐ ┌────────┐ ┌────────┐
              │ Postgres │ │Langfuse│ │ Tavily │
              │ pgvector │ │ Cloud  │ │  API   │
              │  :5432   │ └────────┘ └────────┘
              └──────────┘
```

### Request Flow (Persistent Mode)

1. **Parallel context retrieval** — memories, knowledge chunks, and chat summary fetched simultaneously
2. **System prompt construction** — dynamic prompt with known facts, relevant documents, and conversation history
3. **Tool-calling loop** — up to 5 iterations of LLM → tool → LLM
4. **SSE streaming** — response streamed back to LibreChat
5. **Background jobs** (fire-and-forget) — memory extraction + summarization run after response is delivered

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- API keys for: [Anthropic](https://console.anthropic.com), [OpenAI](https://platform.openai.com) (embeddings), [Tavily](https://tavily.com) (web search)
- Optional: [Langfuse](https://langfuse.com) account for tracing

### 1. Clone & Configure

```bash
git clone https://github.com/cuentadesanti/mauricio.git
cd mauricio

# Copy and fill in your API keys
cp .env.example .env
cp .env.librechat.example .env.librechat
# Edit both files with your actual keys
```

### 2. Launch

```bash
docker compose up -d
```

This will:
- Start PostgreSQL (with pgvector), MongoDB, Meilisearch, and Mosquitto
- Run Alembic migrations automatically
- Sync any markdown files in `knowledge/` into the vector database
- Start the FastAPI backend on `:8000`
- Start LibreChat on `:3080`

### 3. Use

Open **http://localhost:3080**, create an account, and select either:
- **personal-ai** — persistent mode with full memory and retrieval
- **personal-ai-quick** — stateless mode, no database interaction

---

## Project Structure

```
apps/backend/
├── main.py                     # FastAPI app with lifespan (boot sync)
├── api/
│   ├── chat.py                 # /v1/chat/completions, /v1/responses, /v1/models
│   ├── admin.py                # /admin/sync-knowledge, /admin/memory-list
│   └── health.py               # /health
├── core/
│   └── config.py               # Pydantic Settings (reads .env)
├── domain/
│   ├── chat.py                 # ChatMode enum
│   ├── knowledge.py            # Document, Chunk, KnowledgeStore protocol
│   ├── memory.py               # Memory, MemoryStore protocol
│   └── model_gateway.py        # CompletionRequest/Response protocol
├── db/
│   ├── models.py               # SQLAlchemy ORM (9 tables)
│   ├── repository.py           # All DB access including vector queries
│   └── session.py              # AsyncSession factory
├── gateways/
│   ├── litellm_gateway.py      # LLM calls via LiteLLM + Langfuse
│   └── embeddings_gateway.py   # OpenAI text-embedding-3-small
├── services/
│   ├── chat_service.py         # Main orchestrator: retrieval + tools + jobs
│   ├── knowledge_service.py    # Chunk, embed, and search markdown files
│   ├── memory_service.py       # Store/retrieve with cosine deduplication
│   ├── memory_extractor.py     # Background LLM extraction with supersession
│   ├── summarizer.py           # Rolling conversation compression
│   └── router.py               # Haiku vs Opus model selection
└── tools/
    ├── registry.py             # Tool registry
    ├── time_now.py             # Current time in any timezone
    ├── web_search.py           # Tavily web search
    ├── note_add.py             # Save markdown notes
    ├── note_list.py / note_read.py  # Browse notes
    ├── memory_edit.py          # Expire / correct / add memories
    └── lamp.py                 # Tapo L510 smart lamp control

infra/
├── alembic.ini
├── migrations/versions/
│   ├── 0001_initial.py         # users, chats, messages, events
│   ├── 0002_memory_and_knowledge.py  # memories, summaries, knowledge
│   └── 0003_memory_temporality.py    # valid_from/until, supersession
├── librechat/librechat.yaml
└── mosquitto/mosquitto.conf
```

---

## Memory System

The memory system has three layers:

### Automatic Extraction
After each persistent turn, a background job sends the user+assistant exchange (along with all currently active memories) to a cheap LLM. The model outputs structured JSON:

```json
{
  "facts": [{"content": "The user moved to Lisbon", "supersedes": ["mem_abc"]}],
  "preferences": [{"content": "The user prefers dark mode"}],
  "expire": ["mem_xyz"]
}
```

### Temporal Tracking
Each memory has `valid_from`, `valid_until`, `superseded_by`, and `confidence` fields. When a fact changes (e.g., the user moves cities), the old memory is expired and linked to its replacement — creating a full audit trail.

### User Control
The `memory_edit` tool lets you explicitly tell the AI:
- *"Forget that I work at Acme Corp"* → expires the memory
- *"Actually, I moved to Berlin last month"* → creates replacement, expires old

---

## Knowledge Base

Drop markdown files into `knowledge/` and they become searchable:

```bash
# Auto-indexed at boot, or manually:
curl -X POST http://localhost:8000/admin/sync-knowledge \
  -H "Authorization: Bearer $BACKEND_API_KEY"
```

Files are:
1. **Parsed** — YAML frontmatter extracted for metadata
2. **Chunked** — ~1500 characters with 200-char overlap, split at newlines
3. **Embedded** — OpenAI `text-embedding-3-small` (1536 dimensions)
4. **Indexed** — pgvector HNSW index for fast cosine similarity search

The `note_add` tool lets the AI create notes on your behalf, which are immediately available for retrieval.

---

## Admin API

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/admin/sync-knowledge` | POST | Re-index all markdown files in `knowledge/` |
| `/admin/memory-list` | GET | List extracted memories (supports `?include_expired=true`) |
| `/admin/memory/{id}/expire` | POST | Manually expire a specific memory |
| `/health` | GET | Health check (no auth required) |

All admin endpoints require `Authorization: Bearer $BACKEND_API_KEY`.

---

## Development

```bash
# Install dependencies
uv pip install -e ".[dev]"

# Run tests (38 unit tests)
uv run pytest

# Lint & format
uv run ruff check .
uv run ruff format .

# Type check
uv run mypy apps/

# Rebuild after changes
docker compose build backend && docker compose up -d backend
```

---

## Environment Variables

| Variable | Required | Description |
| :--- | :--- | :--- |
| `BACKEND_API_KEY` | ✅ | Shared auth key between LibreChat and backend |
| `ANTHROPIC_API_KEY` | ✅ | For Claude Haiku/Opus |
| `OPENAI_API_KEY` | ✅ | For embeddings (text-embedding-3-small) |
| `TAVILY_API_KEY` | ✅ | For web search tool |
| `LANGFUSE_PUBLIC_KEY` | ✅ | Langfuse tracing |
| `LANGFUSE_SECRET_KEY` | ✅ | Langfuse tracing |
| `LANGFUSE_HOST` | ✅ | Langfuse endpoint |
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `DEFAULT_MODEL` | ❌ | Default LLM (default: `anthropic/claude-haiku-4-5`) |
| `STRONG_MODEL` | ❌ | Complex query LLM (default: `anthropic/claude-opus-4-7`) |
| `EMBEDDING_MODEL` | ❌ | Embedding model (default: `openai/text-embedding-3-small`) |
| `EXTRACTOR_MODEL` | ❌ | Memory extraction LLM (default: `anthropic/claude-haiku-4-5`) |

---

## Roadmap

- [x] **Phase 0** — OpenAI-compatible bridge with LibreChat
- [x] **Phase 1** — Persistent chat, model router, tools (time, search, notes)
- [x] **Phase 2** — Semantic memory, knowledge RAG, summarization
- [x] **Phase 2.1** — Memory temporality, supersession, memory_edit tool
- [ ] **Phase 3** — Proactive agent (scheduled tasks, notifications)
- [ ] **Phase 4** — Multi-user support, auth improvements

---

## License

Private project. Not yet licensed for public use.
