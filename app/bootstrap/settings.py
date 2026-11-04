from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    env: str = "dev"
    database_url: SecretStr = SecretStr("postgresql+psycopg://postgres@localhost:5432/disputes")
    jwt_secret: SecretStr = SecretStr("dev-only-secret-change-me-in-every-env")
    jwt_algorithm: str = "HS256"
    db_pool_size: int = 5

    model_provider: str = "rules"  # rules | anthropic
    model_name: str = "claude-sonnet-4-5"
    anthropic_api_key: SecretStr | None = None
    model_timeout_s: float = 30.0

    retrieval_top_k: int = 5
    embedding_dim: int = 512

    @property
    def checkpoint_dsn(self) -> str:
        """psycopg DSN for the LangGraph checkpointer (same database, plain driver URL)."""
        return self.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
