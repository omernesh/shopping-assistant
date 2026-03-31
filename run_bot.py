from __future__ import annotations

import logging

from src.agent.llm_client import LLMConfig, LLMTransport
from src.agent.shopping_agent import ShoppingAgent
from src.app.router import ShoppingAssistantRouter
from src.integrations.chp_client import CHPClient
from src.channels.telegram_polling import TelegramPollingBot
from src.channels.media_handler import MediaHandler
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

    # Update query planner statistics
    import sqlite3 as _sqlite3
    for _db_path in [settings.db_path, price_db_path]:
        _conn = _sqlite3.connect(_db_path)
        _conn.execute("ANALYZE")
        _conn.close()
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

    # Media handler for voice and image processing
    media_handler = MediaHandler(
        telegram_token=settings.telegram_bot_token,
        soniox_api_key=settings.soniox_api_key,
        hermes_api_url="http://localhost:8642",
    )
    media_caps = []
    if settings.soniox_api_key:
        media_caps.append("voice-to-text (Soniox)")
    if True:  # Hermes API always available locally
        media_caps.append("image recognition (GPT Vision)")
    if media_caps:
        logging.info("Media handler enabled: %s", ", ".join(media_caps))
    else:
        logging.warning("Media handler: no API keys configured (SONIOX_API_KEY, OPENAI_API_KEY)")

    agent = ShoppingAgent(router=router, transport=transport)
    bot = TelegramPollingBot(token=settings.telegram_bot_token, agent=agent, media_handler=media_handler)
    me = bot.get_me()
    logging.info("Connected to Telegram bot @%s (%s)", me.get("username"), me.get("id"))
    bot.set_commands()
    bot.poll_forever()


if __name__ == "__main__":
    main()
