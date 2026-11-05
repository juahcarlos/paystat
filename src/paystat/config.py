"""Application configuration module using pydantic-settings."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for PayStat service."""

    # Application settings
    app_name: str = "PayStat Analytics API"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8001

    # ClickHouse Database settings
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_database: str = "analytics"
    clickhouse_user: str = "readonly_user"
    clickhouse_password: str = "readonly_secret"

    # Query execution constraints (PRD Section 6)
    query_timeout_seconds: int = 10
    max_query_rows: int = 1000

    # LLM Providers Configuration (PRD Section 4 & 5)
    groq_api_key: str = ""
    primary_model: str = "openai/gpt-oss-120b"
    fallback_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    )

    # Agent constraints (PRD Section 5: "Initial limit: three model calls per analysis")
    max_agent_iterations: int = 3

    # CORS Settings (PRD Section 6:
    #
    # Frontend Preparation:
    # In future development, a web-based dashboard (React/Vue/Next.js) will query
    # the /analyze endpoint directly from browsers. Configurable origins prevent
    # CSRF/unauthorized origins while allowing local developer and staging frontends.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
    )

    @field_validator("fallback_models", mode="before")
    @classmethod
    def parse_fallback_models(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [model.strip() for model in v.split(",") if model.strip()]
        if isinstance(v, list):
            return [str(m).strip() for m in v]
        return ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        if isinstance(v, list):
            return [str(o).strip() for o in v]
        return ["http://localhost:3000", "http://localhost:5173"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
