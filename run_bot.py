from __future__ import annotations

import logging

from src.agent.llm_client import LLMConfig, LLMTransport
from src.agent.shopping_agent import ShoppingAgent
from src.app.router import ShoppingAssistantRouter
from src.channels.telegram_polling import TelegramPollingBot
from src.config.settings import load_settings
from src.storage.sqlite_store import SQLiteStore


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = load_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Missing SHOPPING_BOT_TOKEN in environment or .env")

    store = SQLiteStore(settings.db_path)
    store.initialize()
    router = ShoppingAssistantRouter(store=store, default_city=settings.default_city)

    transport = None
    if settings.agent_enabled and settings.llm_api_key:
        transport = LLMTransport(
            LLMConfig(
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                base_url=settings.llm_base_url,
            )
        )
        logging.info("Semantic agent enabled with model %s at %s", settings.llm_model, settings.llm_base_url)
    else:
        logging.warning("Semantic agent disabled or missing API key; using parser fallback only")

    agent = ShoppingAgent(router=router, transport=transport)
    bot = TelegramPollingBot(token=settings.telegram_bot_token, agent=agent)
    me = bot.get_me()
    logging.info("Connected to Telegram bot @%s (%s)", me.get("username"), me.get("id"))
    bot.set_commands()
    bot.poll_forever()


if __name__ == "__main__":
    main()
