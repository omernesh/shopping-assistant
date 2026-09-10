from src.agent.shopping_agent import ShoppingAgent
from src.app.router import ShoppingAssistantRouter
from src.channels.telegram_polling import TelegramPollingBot
from src.config.settings import load_settings
from src.storage.sqlite_store import SQLiteStore

settings = load_settings()
store = SQLiteStore(settings.db_path)
store.initialize()
router = ShoppingAssistantRouter(store=store, default_city=settings.default_city)
bot = TelegramPollingBot(
    token=settings.telegram_bot_token,
    agent=ShoppingAgent(router=router),
)
print(bot.get_me())
