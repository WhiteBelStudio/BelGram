from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "BelGram API"
    app_version: str = "0.1.0"
    database_url: str = "postgresql+asyncpg://belgram:belgram@localhost:5432/belgram"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me"
    cors_origins: str = "http://localhost:5173"
    verification_ttl_minutes: int = 30
    recovery_ttl_minutes: int = 30
    frontend_url: str = "http://localhost:5173"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
