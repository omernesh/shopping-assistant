from __future__ import annotations

import logging
import sqlite3
import threading

from src.agent.llm_client import LLMConfig, LLMTransport
from src.agent.shopping_agent import ShoppingAgent
from src.app.router import ShoppingAssistantRouter
from src.channels.media_handler import MediaHandler
from src.channels.telegram_polling import TelegramPollingBot
from src.config.settings import MAX_DB_SIZE_BYTES, load_settings
from src.integrations.chp_client import CHPClient
from src.integrations.feed_downloader import PriceDB
from src.integrations.price_service import PriceService
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
    store.rotate_if_needed(max_bytes=MAX_DB_SIZE_BYTES)
    chp_client = CHPClient(timeout=15)
    price_db_path = settings.db_path.parent / "prices.sqlite3"
    price_db = PriceDB(price_db_path)
    price_db.initialize()
    price_service = PriceService(chp_client=chp_client, price_db=price_db)

    # Update query planner statistics
    for _db_path in [settings.db_path, price_db_path]:
        try:
            with sqlite3.connect(_db_path) as _conn:
                _conn.execute("ANALYZE")
        except sqlite3.Error as exc:
            logging.warning("ANALYZE failed for %s: %s (non-fatal)", _db_path, exc)
    router = ShoppingAssistantRouter(
        store=store,
        default_city=settings.default_city,
        chp_client=chp_client,
        price_db=price_db,
        price_service=price_service,
    )
    price_db.rotate_if_needed(max_bytes=MAX_DB_SIZE_BYTES)
    price_db_size = price_db.get_db_size_bytes()
    store_db_size = store.get_db_size_bytes()
    size_mb = price_db_size / (1024 * 1024)
    logging.info("Price DB loaded (%.1f MB)", size_mb)
    total_mb = (store_db_size + price_db_size) / (1024 * 1024)
    logging.info("Total storage: %.1f MB (shopping: %.1f MB, prices: %.1f MB)",
        total_mb,
        store_db_size / (1024 * 1024),
        price_db_size / (1024 * 1024),
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
        gemini_api_key=settings.gemini_api_key,
    )
    media_caps = []
    if settings.soniox_api_key:
        media_caps.append("voice-to-text (Soniox)")
    else:
        logging.warning("SONIOX_API_KEY not set -- voice transcription disabled")
    if settings.gemini_api_key:
        media_caps.append("image recognition (Gemini Flash)")
    else:
        logging.warning("GEMINI_API_KEY not set -- image recognition disabled")
    if media_caps:
        logging.info("Media handler enabled: %s", ", ".join(media_caps))
    # Warm up Gemini vision model in background to avoid blocking startup
    if settings.gemini_api_key:
        threading.Thread(target=media_handler.warmup_vision, daemon=True).start()

    agent = ShoppingAgent(router=router, transport=transport, super_admin_id=settings.super_admin_id)
    bot = TelegramPollingBot(
        token=settings.telegram_bot_token,
        agent=agent,
        media_handler=media_handler,
        super_admin_id=settings.super_admin_id,
    )
    me = bot.get_me()
    logging.info("Connected to Telegram bot @%s (%s)", me.get("username"), me.get("id"))
    if settings.super_admin_id:
        logging.info("Super admin ID: %s", settings.super_admin_id)
    else:
        logging.warning("SUPER_ADMIN_ID not set -- admin restrictions disabled")
    bot.set_commands()
    bot.poll_forever()


if __name__ == "__main__":
    main()
