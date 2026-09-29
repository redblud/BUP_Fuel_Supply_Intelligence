from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# repo/apps/api/app/core/config.py locally; /app/app/core/config.py in Docker (paths come from env there).
_parents = Path(__file__).resolve().parents
REPO_ROOT = _parents[4] if len(_parents) > 4 else _parents[-1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", ".env"), extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    # "fake" serves tests/fixtures/<fixture_scenario>.json; "real" talks to the BUP simulator.
    simulator_mode: Literal["fake", "real"] = "fake"
    simulator_base_url: str = "http://simulator:8000"
    simulator_connect_timeout: float = 2.0
    simulator_read_timeout: float = 5.0
    simulator_max_retries: int = 2
    simulator_backoff_base: float = 0.2
    simulator_backoff_max: float = 2.0
    fixture_dir: Path = REPO_ROOT / "tests" / "fixtures"
    fixture_scenario: str = "route-disruption"

    database_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / 'data' / 'fuelops.db'}"

    automation_default_mode: Literal["ADVISORY", "GUARDED_AUTO", "MANUAL_DEMO"] = "ADVISORY"
    snapshot_tick_tolerance: int = 1
    snapshot_stale_after_seconds: float = 10.0

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
