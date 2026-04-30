from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # auth — LibreChat usa esta key para hablar con nosotros (single user v0)
    backend_api_key: str

    # langfuse
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str = "https://cloud.langfuse.com"

    # db
    database_url: str = "postgresql+asyncpg://ai:ai@postgres:5432/personalai"

    # llm providers (al menos uno)
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    tavily_api_key: str | None = None

    # smart home
    kasa_username: str | None = None
    kasa_password: str | None = None
    lamp_host: str | None = None

    # default routing — Fase 0 es passthrough, esto es solo el modelo por defecto
    default_model: str = "anthropic/claude-haiku-4-5"
    strong_model: str = "anthropic/claude-opus-4-7"
    extractor_model: str = "anthropic/claude-haiku-4-5"  # cheap model for memory + summary
    default_user_handle: str = "me"  # single-user v0

    # Phase 2 — Knowledge & Memory
    # TD-3: embedding_model is coupled to Vector(1536) in the DB schema.
    # Changing model requires a migration to alter the vector dimension.
    # Local fallback: sentence-transformers/all-MiniLM-L6-v2 (384 dims).
    embedding_model: str = "openai/text-embedding-3-small"
    knowledge_dir: str = "/app/knowledge"
    knowledge_s3_bucket: str | None = None
    memory_dedup_threshold: float = 0.92
    chunk_size_chars: int = 1500  # ~ 350-400 tokens
    chunk_overlap_chars: int = 200

    # Phase 4 — WhatsApp (Evolution API)
    evolution_api_url: str | None = None       # e.g. http://evolution:8080
    evolution_api_key: str | None = None
    evolution_instance: str = "mauricio"
    evolution_webhook_token: str | None = None  # optional webhook auth
    # Lock WhatsApp to a single chat JID (typically a one-person group, like
    # "Mauricio"). When set, all other JIDs are dropped at the webhook layer.
    # Crucial when the is_group block is removed below — without this, the
    # bot replies to anything you write in any group.
    whatsapp_only_jid: str | None = None

    # Phase 5 — Self-improvement loop
    repo_root: str | None = None      # absolute path to repo root, e.g. /app or /home/santi/mauricio
    github_repo: str | None = None    # e.g. cuentadesanti/mauricio


settings = Settings()
