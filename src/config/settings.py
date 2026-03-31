from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "shopping_assistant.sqlite3"
DEFAULT_CACHE_TTL_SECONDS = 60 * 60 * 6
DEFAULT_CITY = "יבנה"
DEFAULT_CITY_ID = 2660
DEFAULT_STREET_ID = 9000
DEFAULT_CHP_BASE_URL = "https://chp.co.il"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
HERMES_ENV_PATH = Path.home() / ".hermes" / ".env"
DEFAULT_LLM_MODEL = "MiniMax-M2.7"
DEFAULT_LLM_BASE_URL = "https://api.minimax.io/anthropic"


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str | None
    default_city: str = DEFAULT_CITY
    default_city_id: int = DEFAULT_CITY_ID
    default_street_id: int = DEFAULT_STREET_ID
    chp_base_url: str = DEFAULT_CHP_BASE_URL
    db_path: Path = DEFAULT_DB_PATH
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    llm_api_key: str | None = None
    llm_model: str = DEFAULT_LLM_MODEL
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    agent_enabled: bool = True
    soniox_api_key: str | None = None
    gemini_api_key: str | None = None


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    _load_dotenv(HERMES_ENV_PATH)
    _load_dotenv(DEFAULT_ENV_PATH)
    db_path = Path(os.getenv("SHOPPING_ASSISTANT_DB_PATH", str(DEFAULT_DB_PATH)))

    return Settings(
        telegram_bot_token=os.getenv("SHOPPING_BOT_TOKEN"),
        default_city=os.getenv("SHOPPING_ASSISTANT_DEFAULT_CITY", DEFAULT_CITY),
        default_city_id=int(os.getenv("SHOPPING_ASSISTANT_DEFAULT_CITY_ID", str(DEFAULT_CITY_ID))),
        default_street_id=int(os.getenv("SHOPPING_ASSISTANT_DEFAULT_STREET_ID", str(DEFAULT_STREET_ID))),
        chp_base_url=os.getenv("SHOPPING_ASSISTANT_CHP_BASE_URL", DEFAULT_CHP_BASE_URL),
        db_path=db_path,
        cache_ttl_seconds=int(os.getenv("SHOPPING_ASSISTANT_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))),
        llm_api_key=os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY"),
        llm_model=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
        llm_base_url=os.getenv("LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
        agent_enabled=_env_bool("SHOPPING_ASSISTANT_AGENT_ENABLED", True),
        soniox_api_key=os.getenv("SONIOX_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
    )
