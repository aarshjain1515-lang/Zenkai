from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    database_url_unpooled: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-5.5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    loki_url: str = "http://localhost:3100"
    prometheus_url: str = "http://localhost:9090"
    gateway_url: str = "http://localhost:8080"
    target_name: str = "University portal · Demo target"
    loki_selector: str = '{service_name="zenkai-demo"}'

    @field_validator("loki_selector", mode="before")
    @classmethod
    def default_selector(cls, value):
        return value or '{service_name="zenkai-demo"}'
    portal_url: str = "http://localhost:8100"
    operator_password: str = ""
    session_secret: str = ""
    lab_token: str = ""
    log_dir: Path = Path("logs")
    gateway_config_dir: Path = Path("runtime/gateway")
    auto_approve_safe: bool = True
    poll_seconds: float = 3
    verification_samples: int = 5
    verification_interval: float = 2
    docker_project: str = "zenkai"

    def missing(self):
        return [key for key in ("database_url", "openrouter_api_key", "operator_password", "session_secret", "lab_token")
                if not getattr(self, key)]


@lru_cache
def settings():
    return Settings()
