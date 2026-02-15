from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service
    service_port: int = 17010
    service_host: str = "0.0.0.0"  # nosec B104

    # CORS
    cors_origins: str = "http://localhost:17000"

    # Database
    database_url: str = "postgresql+asyncpg://audio_ident:audio_ident@localhost:5432/audio_ident"

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30

    # App metadata
    app_name: str = "audio-ident-service"
    app_version: str = "0.1.0"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "audio_embeddings"

    # Audio storage
    audio_storage_root: str = "./data"

    # Olaf
    olaf_lmdb_path: str = "./data/olaf_db"
    olaf_bin_path: str = "olaf_c"

    # Embedding
    embedding_model: str = "clap-htsat-large"
    embedding_dim: int = 512

    # Search
    vibe_match_threshold: float = 0.60
    qdrant_search_limit: int = 50

    # Admin
    admin_api_key: str = ""  # Empty = ingest endpoint locked (fail-closed)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
