from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # auth — LibreChat usa esta key para hablar con nosotros (single user v0)
    backend_api_key: str

    # langfuse
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str = "https://cloud.langfuse.com"

    # llm providers (al menos uno)
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # default routing — Fase 0 es passthrough, esto es solo el modelo por defecto
    default_model: str = "anthropic/claude-haiku-4-5"


settings = Settings()
