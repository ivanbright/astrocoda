"""Application configuration.

Every runtime dependency (PostgreSQL, Redis, OpenAI, Qdrant, Stripe) is
declared exactly once here and validated eagerly by Pydantic.  The process
refuses to boot on a misconfigured environment instead of failing later with
an opaque connection error.

Settings are loaded from real environment variables first and from a local
``.env`` file as a fallback, so the same image runs in Docker, CI and on a
developer laptop without code changes.
"""

from __future__ import annotations

from functools import lru_cache
import json
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentT = Literal["local", "staging", "production"]
LogLevelT = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Strictly validated environment configuration for every Astrocoda process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # External service connections - REQUIRED
    # ------------------------------------------------------------------
    POSTGRES_URI: str = Field(
        ...,
        description=(
            "Async SQLAlchemy DSN for PostgreSQL. Must use the asyncpg driver, "
            "e.g. postgresql+asyncpg://astrocoda:password@postgres:5432/astrocoda"
        ),
    )
    REDIS_URI: str = Field(
        ...,
        description="Redis DSN backing the ARQ queue, e.g. redis://redis:6379",
    )
    OPENAI_API_KEY: str = Field(
        ...,
        min_length=8,
        description=(
            "Secret API key used for chat completions and embeddings. When "
            "OPENAI_BASE_URL is set, this is the key for that provider instead."
        ),
    )
    OPENAI_BASE_URL: str | None = Field(
        default=None,
        description=(
            "Optional OpenAI-compatible base URL. Leave unset for api.openai.com. "
            "Point it at https://openrouter.ai/api/v1 (or DeepSeek, Together, etc.) "
            "to run the pipeline against another provider."
        ),
    )
    QDRANT_URL: str = Field(
        ...,
        description="Base URL of the Qdrant instance, e.g. http://qdrant:6333",
    )
    QDRANT_API_KEY: str | None = Field(
        default=None,
        description="Optional Qdrant API key. Leave empty for an unsecured local instance.",
    )
    STRIPE_WEBHOOK_SECRET: str = Field(
        ...,
        min_length=8,
        description="Signing secret of the Stripe endpoint (whsec_...).",
    )
    SECRET_KEY: str = Field(
        ...,
        min_length=32,
        description="Signing key for issued JWT access tokens.",
    )

    # ------------------------------------------------------------------
    # Application metadata
    # ------------------------------------------------------------------
    PROJECT_NAME: str = "Astrocoda"
    ENVIRONMENT: EnvironmentT = "local"
    DEBUG: bool = False
    LOG_LEVEL: LogLevelT = "INFO"
    API_V1_PREFIX: str = "/api/v1"
    # Kept as a raw string because pydantic-settings JSON-decodes list fields
    # before validators run, which would reject the friendlier comma separated
    # form. Parsed by the `cors_origins` property below.
    CORS_ORIGINS: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        description='Comma separated origins, or a JSON array, e.g. ["https://app.example.com"].',
    )

    # ------------------------------------------------------------------
    # AI pipeline tuning
    # ------------------------------------------------------------------
    OPENAI_CHAT_MODEL: str = Field(
        default="gpt-4o-mini",
        description=(
            "Model used by Instructor for structured extraction. When pointing "
            "OPENAI_BASE_URL at OpenRouter, use the provider-prefixed id, "
            "e.g. openai/gpt-4o-mini."
        ),
    )
    OPENAI_EMBEDDING_MODEL: str = Field(
        default="text-embedding-3-small",
        description=(
            "Embedding model. Native output dimension is 1536. With OpenRouter "
            "use openai/text-embedding-3-small or qwen/qwen3-embedding-0.6b."
        ),
    )
    EMBEDDING_DIMENSIONS: int = Field(
        default=1536,
        ge=1,
        description="Vector width written to Qdrant. Must match the embedding model.",
    )
    CHUNK_SIZE: int = Field(
        default=1000,
        ge=128,
        le=32_000,
        description="Maximum number of characters per pipeline chunk.",
    )
    MAX_DOCUMENT_CHARACTERS: int = Field(
        default=1_000_000,
        ge=1,
        description="Hard ceiling for the raw_text payload accepted by the API.",
    )
    VECTOR_COLLECTION_NAME: str = Field(
        default="astrocoda_chunks",
        description="Qdrant collection holding every embedded chunk.",
    )
    INSTRUCTOR_MAX_RETRIES: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Instructor self-healing retries when the LLM returns invalid JSON.",
    )
    LLM_MAX_TOKENS: int = Field(
        default=512,
        ge=1,
        description=(
            "Hard cap on generated tokens per extraction call. A summary plus "
            "keywords needs a few hundred tokens; capping it keeps cost (and "
            "OpenRouter credit limits) predictable."
        ),
    )
    EMBEDDING_BATCH_SIZE: int = Field(
        default=16,
        ge=1,
        le=128,
        description="Number of chunks embedded per OpenAI request.",
    )
    ARQ_JOB_TIMEOUT: int = Field(
        default=1800,
        ge=30,
        description="Seconds after which ARQ cancels a stuck job.",
    )
    ARQ_MAX_TRIES: int = Field(
        default=3,
        ge=1,
        description="Maximum ARQ attempts per job before it is moved to the dead set.",
    )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    JWT_ALGORITHM: str = Field(default="HS256", description="Signing algorithm for access tokens.")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=60,
        ge=1,
        description="Lifetime of a JWT access token issued by create_access_token().",
    )
    API_KEY_PREFIX: str = Field(
        default="astro_",
        description="Prefix used when Astrocoda mints a new consumer API key.",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def cors_origins(self) -> list[str]:
        """Parse ``CORS_ORIGINS`` from either CSV or JSON array form."""
        raw = self.CORS_ORIGINS.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(origin).strip() for origin in parsed if str(origin).strip()]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def openai_embedding_dimensions(self) -> int:
        """Dimensions OpenAI actually returns for the configured embedding model."""
        known: dict[str, int] = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        }
        return known.get(self.OPENAI_EMBEDDING_MODEL, self.EMBEDDING_DIMENSIONS)

    def redact(self) -> dict[str, str]:
        """Safe view of the configuration for logging endpoints."""
        return {
            "PROJECT_NAME": self.PROJECT_NAME,
            "ENVIRONMENT": self.ENVIRONMENT,
            "API_V1_PREFIX": self.API_V1_PREFIX,
            "POSTGRES_URI": _mask_dsn(self.POSTGRES_URI),
            "REDIS_URI": _mask_dsn(self.REDIS_URI),
            "QDRANT_URL": self.QDRANT_URL,
            "OPENAI_CHAT_MODEL": self.OPENAI_CHAT_MODEL,
            "OPENAI_EMBEDDING_MODEL": self.OPENAI_EMBEDDING_MODEL,
            "OPENAI_BASE_URL": self.OPENAI_BASE_URL or "https://api.openai.com/v1",
            "VECTOR_COLLECTION_NAME": self.VECTOR_COLLECTION_NAME,
            "CHUNK_SIZE": str(self.CHUNK_SIZE),
            "CORS_ORIGINS": ",".join(self.cors_origins),
        }


def _mask_dsn(dsn: str) -> str:
    """Strip credentials out of a DSN so it can be printed safely."""
    if "@" not in dsn:
        return dsn
    scheme, _, remainder = dsn.partition("://")
    credentials, _, host = remainder.rpartition("@")
    if not credentials:
        return dsn
    user, _, _password = credentials.partition(":")
    return f"{scheme}://{user}:***@{host}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process wide, cached configuration singleton."""
    return Settings()


settings: Settings = get_settings()
