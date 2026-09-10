from pathlib import Path

from src.config.settings import Settings, _load_dotenv, load_settings


def test_load_dotenv_sets_missing_values(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("SHOPPING_BOT_TOKEN=test-token\nSHOPPING_ASSISTANT_DEFAULT_CITY=חיפה\n", encoding="utf-8")  # noqa: E501
    monkeypatch.delenv("SHOPPING_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SHOPPING_ASSISTANT_DEFAULT_CITY", raising=False)

    _load_dotenv(env_path)

    assert load_settings().telegram_bot_token == "test-token"
    assert load_settings().default_city == "חיפה"


def test_existing_env_wins_over_dotenv(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("SHOPPING_BOT_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.setenv("SHOPPING_BOT_TOKEN", "env-token")

    _load_dotenv(env_path)

    settings = load_settings()
    assert isinstance(settings, Settings)
    assert settings.telegram_bot_token == "env-token"


def test_load_settings_reads_llm_key_from_minimax_env(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "mm-key")
    monkeypatch.delenv("SHOPPING_ASSISTANT_AGENT_ENABLED", raising=False)
    import src.config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "EXTRA_ENV_PATH", Path("/nonexistent/.env"))
    monkeypatch.setattr(settings_mod, "DEFAULT_ENV_PATH", Path("/nonexistent/.env"))

    settings = load_settings()

    assert settings.llm_api_key == "mm-key"
    assert settings.agent_enabled is True


def test_load_settings_reads_llm_key_from_llm_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "mm-key")
    monkeypatch.delenv("SHOPPING_ASSISTANT_AGENT_ENABLED", raising=False)

    settings = load_settings()

    assert settings.llm_api_key == "llm-key"


def test_load_settings_falls_back_to_minimax_key(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "mm-key")
    monkeypatch.delenv("SHOPPING_ASSISTANT_AGENT_ENABLED", raising=False)
    # Prevent _load_dotenv from loading GROQ_API_KEY from ~/.hermes/.env
    import src.config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "EXTRA_ENV_PATH", Path("/nonexistent/.env"))
    monkeypatch.setattr(settings_mod, "DEFAULT_ENV_PATH", Path("/nonexistent/.env"))

    settings = load_settings()

    assert settings.llm_api_key == "mm-key"
