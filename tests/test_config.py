import pytest

from dataexplorer.config import Settings


def production_settings(**overrides) -> dict[str, object]:
    values: dict[str, object] = {
        "environment": "production",
        "auth_mode": "jwt",
        "oidc_jwks_url": "https://identity.example.com/jwks.json",
        "oidc_issuer": "https://identity.example.com/",
        "oidc_audience": "dataexplorer-api",
        "persistence_mode": "postgres",
        "database_dsn": "postgresql://user:secret@db/dataexplorer",
        "policy_backend": "redis",
        "redis_url": "rediss://redis:6378/0",
        "vector_backend": "qdrant",
        "qdrant_url": "https://qdrant.example.com:6333",
        "artifact_gcs_bucket": "governed-artifacts",
    }
    values.update(overrides)
    return values


def test_production_refuses_in_memory_backends() -> None:
    with pytest.raises(ValueError, match="production requires PostgreSQL"):
        Settings(**production_settings(persistence_mode="memory"))


def test_production_accepts_explicit_persistent_backends() -> None:
    settings = Settings(**production_settings())
    assert settings.persistence_mode == "postgres"
    assert settings.policy_backend == "redis"
    assert settings.vector_backend == "qdrant"
