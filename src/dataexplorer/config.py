from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from DATAEXPLORER_* environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DATAEXPLORER_",
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = (
        "development"
    )
    ollama_base_url: HttpUrl = HttpUrl("http://127.0.0.1:11434")
    ollama_chat_model: str = "llama3.1:8b"
    ollama_embedding_model: str = "embeddinggemma"
    model_provider: Literal["ollama", "openai", "anthropic", "router"] = "ollama"
    deployment_region: str = "global"
    openai_api_key: SecretStr | None = None
    openai_base_url: HttpUrl = HttpUrl("https://api.openai.com/v1")
    openai_chat_model: str = "gpt-5.4"
    openai_embedding_model: str = "text-embedding-3-large"
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: HttpUrl = HttpUrl("https://api.anthropic.com/v1")
    anthropic_chat_model: str = "claude-sonnet-4-5"
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    retrieval_limit: int = Field(default=5, ge=1, le=20)
    minimum_relevance: float = Field(default=0.20, ge=-1, le=1)
    reranker: Literal["lexical", "flashrank"] = "lexical"
    max_query_characters: int = Field(default=8_000, ge=100, le=100_000)
    auth_mode: Literal["development", "jwt"] = "development"
    oidc_jwks_url: HttpUrl | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    requests_per_minute: int = Field(default=20, ge=1, le=10_000)
    daily_token_budget: int = Field(default=100_000, ge=1)
    artifact_output_root: Path = Path(".artifacts")
    artifact_tool_node: Path | None = None
    artifact_tool_node_modules: Path | None = None
    artifact_approver_group: str = "content-approvers"
    artifact_gcs_bucket: str | None = None
    artifact_gcs_kms_key: str | None = None
    persistence_mode: Literal["memory", "postgres"] = "memory"
    database_dsn: SecretStr | None = None
    policy_backend: Literal["memory", "redis"] = "memory"
    redis_url: SecretStr | None = None
    vector_backend: Literal["memory", "qdrant"] = "memory"
    qdrant_url: HttpUrl | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = "dataexplorer_chunks_v1"
    observability_admin_groups: frozenset[str] = frozenset(
        {"observability-admins", "platform-admins"}
    )
    llm_input_cost_per_million_usd: dict[str, float] = {"ollama": 0.0}
    llm_output_cost_per_million_usd: dict[str, float] = {"ollama": 0.0}
    telemetry_retention_days: int = Field(default=30, ge=1, le=2555)

    @model_validator(mode="after")
    def production_requires_oidc(self) -> "Settings":
        if self.environment in {"staging", "production"}:
            if self.auth_mode != "jwt":
                raise ValueError("staging and production require JWT authentication")
            if not all((self.oidc_jwks_url, self.oidc_issuer, self.oidc_audience)):
                raise ValueError("JWT authentication requires JWKS URL, issuer, and audience")
        if self.model_provider in {"openai", "router"} and self.openai_api_key is None:
            raise ValueError("the selected model provider requires an OpenAI API key")
        if self.model_provider in {"anthropic", "router"} and self.anthropic_api_key is None:
            raise ValueError("the selected model provider requires an Anthropic API key")
        if self.persistence_mode == "postgres" and self.database_dsn is None:
            raise ValueError("PostgreSQL persistence requires a database DSN")
        if self.environment == "production" and self.persistence_mode != "postgres":
            raise ValueError("production requires PostgreSQL persistence")
        if self.policy_backend == "redis" and self.redis_url is None:
            raise ValueError("the Redis policy backend requires a Redis URL")
        if self.vector_backend == "qdrant" and self.qdrant_url is None:
            raise ValueError("the Qdrant vector backend requires a Qdrant URL")
        if self.environment == "production" and (
            self.policy_backend != "redis" or self.vector_backend != "qdrant"
        ):
            raise ValueError("production requires Redis policy and Qdrant vector backends")
        if self.environment == "production" and not self.artifact_gcs_bucket:
            raise ValueError("production requires a GCS artifact bucket")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
