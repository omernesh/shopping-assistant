from __future__ import annotations

import logging

from src.agent.llm_client import LLMConfig, LLMTransport
from src.agent.shopping_agent import ShoppingAgent
from src.app.router import ShoppingAssistantRouter
from src.integrations.chp_client import CHPClient
from src.channels.telegram_polling import TelegramPollingBot
from src.config.settings import load_settings
from src.storage.sqlite_store import SQLiteStore
from src.integrations.feed_downloader import PriceDB
from src.integrations.price_service import PriceService


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
    store.rotate_if_needed(max_bytes=250 * 1024 * 1024)
    chp_client = CHPClient(timeout=15)
    price_db_path = settings.db_path.parent / "prices.sqlite3"
    price_db = PriceDB(price_db_path)
    price_db.initialize()
    price_service = PriceService(chp_client=chp_client, price_db=price_db)
    router = ShoppingAssistantRouter(store=store, default_city=settings.default_city, chp_client=chp_client, price_db=price_db, price_service=price_service)
    price_db.rotate_if_needed(max_bytes=250 * 1024 * 1024)
    size_mb = price_db.get_db_size_bytes() / (1024 * 1024)
    logging.info("Price DB loaded (%.1f MB)", size_mb)
    total_mb = (store.get_db_size_bytes() + price_db.get_db_size_bytes()) / (1024 * 1024)
    logging.info("Total storage: %.1f MB (shopping: %.1f MB, prices: %.1f MB)",
        total_mb,
        store.get_db_size_bytes() / (1024 * 1024),
        price_db.get_db_size_bytes() / (1024 * 1024),
    )
    logging.info("CHP price lookup enabled (fallback)")

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
