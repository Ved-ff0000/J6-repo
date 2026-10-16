import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEASEGUARD_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://leaseguard:leaseguard@localhost:5432/leaseguard"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)
    dependency_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    jwt_secret: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(48)))
    jwt_issuer: str = "leaseguard-api"
    jwt_audience: str = "leaseguard-web"
    access_token_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_seconds: int = Field(default=604800, ge=3600, le=2592000)
    registration_enabled: bool = True
    auth_rate_limit: int = Field(default=30, ge=1, le=1000)
    auth_rate_window_seconds: int = Field(default=60, ge=1, le=3600)

    storage_backend: Literal["s3", "local"] = "s3"
    local_storage_path: Path = Path(".data/objects")
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = Field(
        default="leaseguard-documents", pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"
    )
    s3_region: str = "us-east-1"
    s3_access_key: SecretStr | None = Field(default=None, min_length=1)
    s3_secret_key: SecretStr | None = Field(default=None, min_length=1)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)

    processing_enabled: bool = False
    allow_unscanned_processing: bool = False
    parser_max_pages: int = Field(default=500, ge=1, le=1000)
    parser_max_characters: int = Field(default=2_000_000, ge=1000, le=5_000_000)
    parser_max_chunks: int = Field(default=3000, ge=1, le=10000)
    chunk_size: int = Field(default=2000, ge=100, le=8000)
    chunk_overlap: int = Field(default=200, ge=0)
    parser_timeout_seconds: int = Field(default=45, ge=1, le=60)
    processing_lease_seconds: int = Field(default=120, ge=60, le=600)
    processing_max_attempts: int = Field(default=3, ge=1, le=10)
    processing_poll_seconds: float = Field(default=2, ge=0.1, le=60)

    retrieval_enabled: bool = False
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    embedding_provider: Literal["hashing", "openai"] = "hashing"
    embedding_dimensions: int = Field(default=512, ge=128, le=3072)
    embedding_model: str = Field(default="text-embedding-3-small", min_length=1, max_length=120)
    embedding_url: str = "https://api.openai.com/v1"
    embedding_api_key: SecretStr | None = None
    index_timeout_seconds: int = Field(default=120, ge=5, le=300)
    index_lease_seconds: int = Field(default=180, ge=60, le=600)
    index_max_attempts: int = Field(default=3, ge=1, le=10)

    llm_enabled: bool = False
    llm_provider: Literal["openai"] = "openai"
    llm_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr | None = Field(default=None, min_length=1)
    llm_model: str = Field(default="gpt-4.1-mini", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}$")
    llm_timeout_seconds: int = Field(default=30, ge=1, le=60)
    llm_max_output_tokens: int = Field(default=512, ge=64, le=2048)
    llm_probe_limit: int = Field(default=3, ge=1, le=20)
    llm_probe_window_seconds: int = Field(default=600, ge=60, le=3600)

    otel_enabled: bool = False
    otel_endpoint: str = "http://otel-collector:4318/v1/traces"
    metrics_enabled: bool = False
    live_enabled: bool = False
    email_enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, repr=False)
    smtp_password: SecretStr | None = None
    smtp_sender: str = Field(default="leaseguard@example.invalid", repr=False)
    webhook_enabled: bool = False
    webhook_url: SecretStr | None = None
    webhook_signing_key: SecretStr | None = None
    review_auto_enqueue: bool = False
    conflicts_enabled: bool = False
    rules_enabled: bool = False
    rules_disabled: tuple[str, ...] = ()
    lease_extraction_enabled: bool = False
    workflow_enabled: bool = False
    workflow_timeout_seconds: int = Field(default=150, ge=15, le=240)

    graph_enabled: bool = False
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = Field(default="neo4j", min_length=1, max_length=100)
    neo4j_password: SecretStr | None = None
    neo4j_database: str = Field(default="neo4j", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
    graph_timeout_seconds: int = Field(default=60, ge=5, le=120)
    graph_lease_seconds: int = Field(default=120, ge=60, le=300)
    graph_max_attempts: int = Field(default=3, ge=1, le=10)

    @field_validator("neo4j_uri")
    @classmethod
    def graph_uri(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"bolt", "bolt+s", "neo4j", "neo4j+s"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Neo4j URI must be a Bolt/Neo4j endpoint without credentials or query")
        return value

    @field_validator("s3_endpoint", "qdrant_url", "embedding_url", "llm_url")
    @classmethod
    def storage_http_only(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Service endpoint must be an HTTP(S) URL without credentials or query")
        return value

    @field_validator("rules_disabled")
    @classmethod
    def known_disabled_rules(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        from app.rules.catalog import policy

        policy(disabled=value)
        return value

    @model_validator(mode="after")
    def signing_configuration(self) -> "Settings":
        if len(self.jwt_secret.get_secret_value().encode()) < 32:
            raise ValueError("JWT signing key must be at least 32 bytes")
        if self.environment == "production" and "jwt_secret" not in self.model_fields_set:
            raise ValueError("Production requires an explicitly configured JWT signing key")
        if self.environment == "production" and (
            self.storage_backend != "s3"
            or self.s3_access_key is None
            or self.s3_secret_key is None
            or not self.s3_endpoint.startswith("https://")
        ):
            raise ValueError("Production requires S3 credentials and an HTTPS endpoint")
        if self.lease_extraction_enabled and (
            not self.workflow_enabled or self.llm_max_output_tokens < 1024
        ):
            raise ValueError(
                "Lease extraction requires workflow enablement and at least 1024 output tokens"
            )
        if self.workflow_enabled and (
            not self.llm_enabled
            or not self.retrieval_enabled
            or self.workflow_timeout_seconds <= 2 * self.llm_timeout_seconds + 55
            or self.llm_probe_limit < 2
        ):
            raise ValueError(
                "Workflow requires enabled LLM/retrieval, at least two admission units and timeout above two model timeouts plus 55 seconds"
            )
        if self.llm_enabled and self.llm_api_key is None:
            raise ValueError("Enabled LLM requires an explicit provider key")
        if (
            self.llm_enabled
            and self.environment != "test"
            and not self.llm_url.startswith("https://")
        ):
            raise ValueError("LLM calls require HTTPS outside isolated tests")
        if self.graph_enabled and self.neo4j_password is None:
            raise ValueError("Enabled graph requires an explicit Neo4j password")
        if self.graph_lease_seconds <= self.graph_timeout_seconds + 30:
            raise ValueError("Graph lease must exceed timeout plus publication margin")
        if (
            self.environment == "production"
            and self.graph_enabled
            and not self.neo4j_uri.startswith(("bolt+s://", "neo4j+s://"))
        ):
            raise ValueError("Production graph requires certificate-verified TLS")
        if self.index_lease_seconds <= self.index_timeout_seconds + 30:
            raise ValueError("Index lease must exceed index timeout plus publication margin")
        if self.embedding_provider == "openai" and self.embedding_api_key is None:
            raise ValueError("OpenAI-compatible embeddings require an explicit API key")
        if (
            self.environment == "production"
            and self.retrieval_enabled
            and (
                not self.qdrant_url.startswith("https://")
                or self.qdrant_api_key is None
                or (
                    self.embedding_provider == "openai"
                    and not self.embedding_url.startswith("https://")
                )
            )
        ):
            raise ValueError("Production retrieval requires authenticated HTTPS vector services")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.processing_lease_seconds <= self.parser_timeout_seconds + 30:
            raise ValueError(
                "Worker lease must exceed parser timeout plus storage/persistence margin"
            )
        if self.environment == "production" and self.allow_unscanned_processing:
            raise ValueError("Production cannot bypass malware scan policy")
        return self

    @field_validator("database_url")
    @classmethod
    def postgres_async_only(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("Database must use postgresql+asyncpg")
        return value

    @field_validator("redis_url")
    @classmethod
    def redis_only(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("Redis URL must use redis:// or rediss://")
        return value

    @model_validator(mode="after")
    def operations_configuration(self) -> "Settings":
        if self.environment == "production" and self.registration_enabled:
            raise ValueError(
                "Public registration must be disabled until production identity/abuse controls are deployed"
            )
        if self.webhook_enabled:
            from urllib.parse import urlsplit

            if (
                not self.webhook_url
                or not self.webhook_signing_key
                or len(self.webhook_signing_key.get_secret_value()) < 32
            ):
                raise ValueError("Enabled webhooks require URL and at least 32-byte signing key")
            parsed = urlsplit(self.webhook_url.get_secret_value())
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
                or parsed.fragment
            ):
                raise ValueError(
                    "Webhook destination must be HTTPS on port 443 without credentials or fragments"
                )
        if self.email_enabled and (
            not self.smtp_host
            or self.smtp_sender.endswith(".invalid")
            or "\n" in self.smtp_sender
            or "\r" in self.smtp_sender
        ):
            raise ValueError("Enabled email requires a configured SMTP host and sender")
        return self
