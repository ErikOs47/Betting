"""Central configuration. Secrets come from environment variables / .env only (never the frontend)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env"), env_prefix="NBI_", extra="ignore")

    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'nbi.db'}"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = REPO_ROOT / "data"

    # --- data-source credentials (server-side only) ---
    odds_api_key: str = ""
    sportsdataio_key: str = ""

    # --- source behaviour ---
    http_user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    # Sports Reference blocks clients above 20 requests/minute (https://www.sports-reference.com/bot-traffic.html)
    bref_min_interval_s: float = 3.3
    odds_regions: str = "eu,us"
    odds_bookmakers: str = ""  # optional comma list; overrides regions
    odds_monthly_credit_budget: int = 500  # free tier; refuse calls beyond the budget

    # --- staleness thresholds (seconds) ---
    stale_odds_s: int = 15 * 60
    stale_injuries_s: int = 3 * 3600
    stale_games_s: int = 36 * 3600

    cors_origins: str = "http://localhost:3000"
    api_token: str = ""  # optional bearer token protecting mutating endpoints

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
