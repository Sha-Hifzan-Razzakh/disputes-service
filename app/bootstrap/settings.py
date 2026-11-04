"""Typed settings (stage 7, T07.1/T07.2/T07.9). Every value has a default for dev and an APP_ env var."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    env: Literal["dev", "test", "staging", "prod"] = "dev"

    # --- database and checkpointer
    database_url: SecretStr = SecretStr("postgresql+psycopg://postgres@localhost:5432/disputes")
    db_pool_size: int = Field(5, ge=1, le=50)
    db_max_overflow: int = Field(5, ge=0, le=50)
    checkpoint_pool_max: int = Field(4, ge=1, le=50)
    checkpoint_auto_setup: bool = True  # dev convenience; staging/prod run scripts/checkpoint_setup.py at deploy
    checkpoint_retention_days: int = Field(90, ge=7)  # finished threads older than this are pruned

    # --- auth
    jwt_secret: SecretStr = SecretStr("dev-only-secret-change-me-in-every-env")
    jwt_algorithm: str = "HS256"

    # --- model
    model_provider: Literal["rules", "anthropic"] = "rules"
    model_name: str = "claude-sonnet-4-5"
    anthropic_api_key: SecretStr | None = None
    model_timeout_s: float = Field(20.0, gt=0)
    model_max_retries: int = Field(2, ge=0, le=5)
    model_requests_per_second: float = Field(2.0, gt=0)
    model_max_burst: int = Field(5, ge=1)

    # --- outbound HTTP and tools (MCP servers are wired in M3)
    http_timeout_s: float = Field(10.0, gt=0)
    tool_timeout_s: float = Field(4.0, gt=0)
    tool_max_attempts: int = Field(3, ge=1, le=5)
    tools_enabled: bool = False  # kill switch: false → approved actions end EXECUTION_FAILED without any call
    mcp_payments_url: str = "http://localhost:8101/mcp"
    mcp_network_url: str = "http://localhost:8102/mcp"
    mcp_payments_token: SecretStr | None = None
    mcp_network_token: SecretStr | None = None
    tool_rate_per_s: float = Field(5.0, gt=0)  # per tenant per tool
    tool_burst: int = Field(10, ge=1)

    # --- background jobs (approval expiry + side-effect reconciliation)
    sweeper_enabled: bool = True
    sweeper_interval_s: float = Field(300.0, ge=1)
    reconcile_after_minutes: int = Field(15, ge=1)

    # --- telemetry
    otel_exporter: Literal["none", "console", "otlp"] = "none"
    otel_endpoint: str = "http://localhost:4318"
    service_name: str = "disputes-service"

    # --- retrieval and evidence
    retrieval_top_k: int = Field(5, ge=1, le=20)
    embedding_dim: int = 512
    min_evidence_score: float = Field(0.12, ge=0, le=1)
    max_retrieval_attempts: int = Field(2, ge=1, le=5)
    max_refine_attempts: int = Field(1, ge=0, le=3)

    # --- run limits
    run_timeout_s: float = Field(30.0, gt=0)
    recursion_limit: int = Field(40, ge=10, le=200)
    max_concurrency: int = Field(4, ge=1, le=32)
    max_concurrent_runs: int = Field(8, ge=1, le=256)  # admission control per process (T16.13)
    admission_wait_s: float = Field(2.0, ge=0)

    # --- approvals
    approval_ttl_hours: int = Field(72, ge=1)
    urgent_window_hours: int = Field(48, ge=1)
    approval_deadline_margin_hours: int = Field(24, ge=0)

    # --- v1 deprecation
    v1_assist_sunset: str = "Wed, 31 Mar 2027 00:00:00 GMT"

    @model_validator(mode="after")
    def _secrets_per_env(self) -> Settings:
        if self.env in {"staging", "prod"}:
            if self.jwt_secret.get_secret_value().startswith("dev-only"):
                raise ValueError("APP_JWT_SECRET must be set outside dev/test")
            if self.checkpoint_auto_setup:
                raise ValueError("APP_CHECKPOINT_AUTO_SETUP must be false outside dev/test (run it at deploy)")
            if "sslmode=require" not in self.database_url.get_secret_value() and "sslmode=verify" not in (
                self.database_url.get_secret_value()
            ):
                raise ValueError("APP_DATABASE_URL must use sslmode=require or verify-full outside dev/test")
        if self.max_concurrent_runs > self.db_pool_size + self.db_max_overflow - 2:
            # each running thread holds one pooled connection for its advisory lock and needs more for its own
            # reads/writes: more slots than that starves the pool (measured: every run times out; docs/capacity.md)
            raise ValueError(
                f"APP_MAX_CONCURRENT_RUNS={self.max_concurrent_runs} needs a DB pool of at least "
                f"{self.max_concurrent_runs + 2} (APP_DB_POOL_SIZE + APP_DB_MAX_OVERFLOW)"
            )
        if self.tools_enabled and (self.mcp_payments_token is None or self.mcp_network_token is None):
            raise ValueError("APP_TOOLS_ENABLED=true needs APP_MCP_PAYMENTS_TOKEN and APP_MCP_NETWORK_TOKEN")
        if self.model_provider == "anthropic" and self.anthropic_api_key is None:
            raise ValueError("APP_ANTHROPIC_API_KEY is required when APP_MODEL_PROVIDER=anthropic")
        return self

    @property
    def checkpoint_dsn(self) -> str:
        """psycopg DSN for the LangGraph checkpointer (same database, plain driver URL)."""
        return self.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
