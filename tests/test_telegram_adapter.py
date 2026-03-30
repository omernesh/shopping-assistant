from src.channels.telegram_bot import TelegramBotAdapter


def test_normalize_message_builds_topic_aware_scope_id() -> None:
    adapter = TelegramBotAdapter()

    context = adapter.normalize_message(
        {
            "message": {
                "chat": {"id": -100123, "title": "קניות"},
                "message_thread_id": 77,
                "from": {"id": 42},
                "text": "2 חלב",
            }
        }
    )

    assert context.external_chat_id == "-100123:77"
    assert context.title == "קניות"
    assert context.text == "2 חלב"


def test_normalize_message_falls_back_to_chat_scope_when_no_topic() -> None:
    adapter = TelegramBotAdapter()

    context = adapter.normalize_message(
        {
            "chat": {"id": -100123, "title": "קניות"},
            "from": {"id": 42},
            "text": "לחם",
        }
    )

    assert context.external_chat_id == "-100123"
    assert context.text == "לחם"
