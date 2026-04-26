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

    # default routing — Fase 0 es passthrough, esto es solo el modelo por defecto
    default_model: str = "anthropic/claude-haiku-4-5"
    strong_model: str = "anthropic/claude-opus-4-7"
    extractor_model: str = "anthropic/claude-haiku-4-5"  # cheap model for memory + summary
    default_user_handle: str = "me"  # single-user v0

    # Phase 2 — Knowledge & Memory
    embedding_model: str = "openai/text-embedding-3-small"
    knowledge_dir: str = "/app/knowledge"
    knowledge_s3_bucket: str | None = None
    memory_dedup_threshold: float = 0.92
    chunk_size_chars: int = 1500  # ~ 350-400 tokens
    chunk_overlap_chars: int = 200



settings = Settings()
