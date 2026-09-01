from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ZONES = "fr-par-1,fr-par-2,nl-ams-1,nl-ams-2,pl-waw-2,pl-waw-3"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Metal Availability"
    app_env: str = "production"
    database_url: str = "sqlite+aiosqlite:///./data/availability.db"
    scw_secret_key: str | None = Field(default=None, repr=False)
    scw_api_base_url: str = "https://api.scaleway.com"
    scw_zones: str = DEFAULT_ZONES
    request_timeout_seconds: float = 15.0
    request_retries: int = 3
    enable_scheduler: bool = True
    stale_after_hours: int = 2
    log_level: str = "INFO"

    @property
    def zones(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(zone.strip() for zone in self.scw_zones.split(",") if zone.strip())
        )

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite+aiosqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        value = self.database_url.removeprefix(prefix)
        return Path(value).expanduser()


@lru_cache
def get_settings() -> Settings:
    return Settings()
