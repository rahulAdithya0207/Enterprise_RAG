"""Pydantic-validated settings loaded from the .env file at startup."""

import os
from urllib.parse import quote, urlunsplit

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration object for the entire application.

    All values are pulled from environment variables or a .env file.
    Unknown keys are silently ignored (extra="ignore") so legacy variable
    names in existing .env files don't crash the app at import time.
    Fields without a default are required — a missing key raises a clear
    validation error before the server starts.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Jina AI — used for both embeddings and reranking (optional, falls back to local models)
    JINA_API_KEY: str | None = None

    # OpenAI — primary LLM credentials (optional if using Portkey routing)
    OPENAI_API_KEY: str | None = None
    JUDGE_OPENAI_API_KEY: str | None = None  # separate key for the RAGAS judge LLM

    # Portkey LLM gateway — handles routing, fallback, caching, and retries
    PORTKEY_API_KEY: str
    PORTKEY_PRIMARY_SLUG: str = "google-gemini"
    PORTKEY_MODEL: str = "gemini-2.5-flash"
    PORTKEY_FALLBACK_SLUG: str = "anthropic-fallback"
    # Portkey saved configs are referenced by their dashboard-assigned ID (pc-...).
    # Required when the workspace has block_inline_config enabled.
    PORTKEY_PRIMARY_CONFIG_ID: str

    # Qdrant vector database
    QDRANT_URL: str | None = Field(default=None, validation_alias=AliasChoices("QDRANT_URL", "QDRANT_CLUSTER_ENDPOINT"))
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "enterprise_rag"

    # Neon serverless Postgres — used as the LangGraph checkpoint store
    NEON_DB_URL: str | None = None

    # Upstash Redis — used for distributed rate limiting
    UPSTASH_REDIS_REST_URL: str | None = None
    UPSTASH_REDIS_REST_TOKEN: str | None = None

    # API security
    API_KEY: str | None = Field(default=None, alias="RAG_API_KEY")
    RATE_LIMIT_PER_MINUTE: int = 20
    STRICT_STARTUP: bool = False  # set True to abort startup if any dependency is down

    # Observability
    LOGFIRE_TOKEN: str | None = None
    LOGFIRE_BASE_URL: str | None = None  # override for EU region tokens
    LANGSMITH_TRACING: str = "true"
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str = "rag_scale_test"
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"

    @field_validator("QDRANT_API_KEY", mode="before")
    @classmethod
    def _blank_qdrant_key_to_none(cls, v):
        """Convert an empty QDRANT_API_KEY to None so Qdrant doesn't receive a blank auth header."""
        return None if v == "" or v is None else v

    @property
    def judge_api_key(self) -> str:
        """Return the dedicated eval judge key, or fall back to the primary OpenAI key."""
        return self.JUDGE_OPENAI_API_KEY or self.OPENAI_API_KEY

    @property
    def postgres_uri(self) -> str:
        """
        Construct the Postgres connection string with TCP keepalive parameters.

        Neon closes idle connections aggressively, so these keepalive options
        prevent the pool from silently dropping connections between requests.
        """
        base = self.NEON_DB_URL.rstrip("/")
        keepalive = "keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5"
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}{keepalive}"

    @property
    def redis_url(self) -> str:
        """
        Build a TLS Redis URL from the Upstash REST credentials.

        Upstash exposes the same hostname for both its REST API and raw Redis
        protocol. The REST token doubles as the Redis password on the default
        username, giving us a standards-compliant rediss:// URL.
        """
        host = self.UPSTASH_REDIS_REST_URL.replace("https://", "").rstrip("/")
        token = quote(self.UPSTASH_REDIS_REST_TOKEN, safe="")
        netloc = f"default:{token}@{host}"
        return urlunsplit(("rediss", netloc, "/0", "ssl_cert_reqs=required", ""))


# Single global instance shared across all modules.
settings = Settings()


def apply_langchain_env():
    """
    Push LangSmith settings into os.environ so LangChain picks them up automatically.

    Tracing is only activated when both the tracing flag and an API key are present.
    Enabling the flag without a key causes LangChain to emit 401 errors on every step.
    """
    if settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", settings.LANGSMITH_TRACING)
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.LANGSMITH_API_KEY)
    if settings.LANGSMITH_PROJECT:
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGSMITH_PROJECT)
    if settings.LANGSMITH_ENDPOINT:
        os.environ.setdefault("LANGCHAIN_ENDPOINT", settings.LANGSMITH_ENDPOINT)


apply_langchain_env()
