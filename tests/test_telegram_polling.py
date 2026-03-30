from pathlib import Path

from src.agent.shopping_agent import ShoppingAgent
from src.app.router import MessageContext, ShoppingAssistantRouter
from src.channels.telegram_polling import TelegramPollingBot
from src.storage.sqlite_store import SQLiteStore


class DummyAgent(ShoppingAgent):
    def __init__(self, db_path: Path, response_text: str = "ok"):
        store = SQLiteStore(db_path)
        store.initialize()
        router = ShoppingAssistantRouter(store=store)
        super().__init__(router=router, transport=None)
        self.response_text = response_text
        self.seen_contexts: list[MessageContext] = []

    def handle_message(self, context: MessageContext) -> str:
        self.seen_contexts.append(context)
        return self.response_text


def test_handle_update_routes_topic_message_and_replies(tmp_path: Path) -> None:
    agent = DummyAgent(tmp_path / "bot.sqlite3")
    bot = TelegramPollingBot(token="test-token", agent=agent)
    sent_messages: list[dict] = []
    bot.send_message = lambda **kwargs: sent_messages.append(kwargs)  # type: ignore[method-assign]

    bot.handle_update(
        {
            "message": {
                "chat": {"id": -100123, "title": "shopping assistant"},
                "message_thread_id": 9,
                "from": {"id": 77, "is_bot": False},
                "text": "2 חלב",
            }
        }
    )

    assert len(agent.seen_contexts) == 1
    assert agent.seen_contexts[0].external_chat_id == "-100123:9"
    assert sent_messages == [
        {"chat_id": -100123, "text": "ok", "message_thread_id": 9}
    ]


def test_handle_update_ignores_bot_messages(tmp_path: Path) -> None:
    agent = DummyAgent(tmp_path / "bot.sqlite3")
    bot = TelegramPollingBot(token="test-token", agent=agent)
    sent_messages: list[dict] = []
    bot.send_message = lambda **kwargs: sent_messages.append(kwargs)  # type: ignore[method-assign]

    bot.handle_update(
        {
            "message": {
                "chat": {"id": -100123, "title": "shopping assistant"},
                "from": {"id": 77, "is_bot": True},
                "text": "ignore me",
            }
        }
    )

    assert agent.seen_contexts == []
    assert sent_messages == []


def test_handle_update_does_not_send_empty_agent_response(tmp_path: Path) -> None:
    agent = DummyAgent(tmp_path / "bot.sqlite3", response_text="")
    bot = TelegramPollingBot(token="test-token", agent=agent)
    sent_messages: list[dict] = []
    bot.send_message = lambda **kwargs: sent_messages.append(kwargs)  # type: ignore[method-assign]

    bot.handle_update(
        {
            "message": {
                "chat": {"id": -100123, "title": "shopping assistant"},
                "from": {"id": 77, "is_bot": False},
                "text": "random chatter",
            }
        }
    )

    assert len(agent.seen_contexts) == 1
    assert sent_messages == []
