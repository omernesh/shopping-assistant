from pathlib import Path

from src.app.router import MessageContext, ShoppingAssistantRouter
from src.storage.sqlite_store import SQLiteStore


def build_router(tmp_path: Path) -> ShoppingAssistantRouter:
    store = SQLiteStore(tmp_path / "shopping.sqlite3")
    store.initialize()
    return ShoppingAssistantRouter(store=store)


def test_handle_add_message_persists_item_and_returns_confirmation(tmp_path: Path) -> None:
    router = build_router(tmp_path)

    response = router.handle_message(
        MessageContext(
            platform="telegram",
            external_chat_id="123:7",
            user_id="42",
            text="2 חלב",
            title="Shopping Group",
        )
    )

    assert "נוסף" in response
    assert "2" in response
    assert "חלב" in response

    listed = router.handle_message(
        MessageContext(
            platform="telegram",
            external_chat_id="123:7",
            user_id="42",
            text="?",
            title="Shopping Group",
        )
    )

    assert "מקרר" in listed
    assert "חלב" in listed


def test_handle_done_message_marks_item_purchased(tmp_path: Path) -> None:
    router = build_router(tmp_path)
    context = MessageContext(
        platform="telegram",
        external_chat_id="123:7",
        user_id="42",
        text="חלב",
        title="Shopping Group",
    )

    router.handle_message(context)
    response = router.handle_message(context.with_text("קניתי חלב"))

    assert "סומן כנקנה" in response
    listed = router.handle_message(context.with_text("?"))
    assert "חלב" not in listed


def test_handle_delete_message_marks_item_deleted(tmp_path: Path) -> None:
    router = build_router(tmp_path)
    context = MessageContext(
        platform="telegram",
        external_chat_id="123:7",
        user_id="42",
        text="חלב",
        title="Shopping Group",
    )

    router.handle_message(context)
    response = router.handle_message(context.with_text("מחק חלב"))

    assert "נמחק" in response
    listed = router.handle_message(context.with_text("?"))
    assert "הרשימה ריקה" in listed


def test_chat_scope_is_topic_aware(tmp_path: Path) -> None:
    router = build_router(tmp_path)

    topic_one = MessageContext(
        platform="telegram",
        external_chat_id="123:7",
        user_id="42",
        text="חלב",
    )
    topic_two = MessageContext(
        platform="telegram",
        external_chat_id="123:8",
        user_id="42",
        text="לחם",
    )

    router.handle_message(topic_one)
    router.handle_message(topic_two)

    listed_one = router.handle_message(topic_one.with_text("?"))
    listed_two = router.handle_message(topic_two.with_text("?"))

    assert "חלב" in listed_one
    assert "לחם" not in listed_one
    assert "לחם" in listed_two
    assert "חלב" not in listed_two


def test_handle_ignores_meta_chatter_without_polluting_list(tmp_path: Path) -> None:
    router = build_router(tmp_path)
    context = MessageContext(
        platform="telegram",
        external_chat_id="123:7",
        user_id="42",
        text="sammie, did you see the reply of the shopping assistant bot?",
        title="Shopping Group",
    )

    response = router.handle_message(context)
    assert response == ""

    listed = router.handle_message(context.with_text("?"))
    assert listed == "הרשימה ריקה"


def test_handle_ignores_slash_command_without_polluting_list(tmp_path: Path) -> None:
    router = build_router(tmp_path)
    context = MessageContext(
        platform="telegram",
        external_chat_id="123:7",
        user_id="42",
        text="/chatid",
        title="Shopping Group",
    )

    response = router.handle_message(context)
    assert response == ""

    listed = router.handle_message(context.with_text("?"))
    assert listed == "הרשימה ריקה"

def test_price_lookup_without_client(tmp_path):
    """Price lookup returns graceful message when CHP client is not configured."""
    router = build_router(tmp_path)
    ctx = MessageContext(
        platform="telegram",
        external_chat_id="123:7",
        user_id="42",
        text="מחיר חלב",
        title="Shopping Group",
    )
    result = router.handle_message(ctx)
    assert "לא זמינה" in result
