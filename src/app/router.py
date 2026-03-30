from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.domain.parser import ParsedMessage, parse_message
from src.domain.shopping_list import build_item
from src.storage.sqlite_store import SQLiteStore, StoredItem

import logging
from src.integrations.chp_client import CHPClient, format_price_summary
from src.integrations.feed_downloader import PriceDB, format_feed_results

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MessageContext:
    platform: str
    external_chat_id: str
    user_id: str
    text: str
    title: str | None = None
    user_name: str | None = None

    def with_text(self, text: str) -> "MessageContext":
        return MessageContext(
            platform=self.platform,
            external_chat_id=self.external_chat_id,
            user_id=self.user_id,
            text=text,
            title=self.title,
            user_name=self.user_name,
        )


@dataclass(frozen=True)
class DuplicateConflict:
    """Returned when adding an item that already exists in the list."""
    new_item_name: str
    new_quantity: float | None
    new_note: str | None
    existing_item: StoredItem
    list_id: int
    chat_id: int
    user_id: str


class ShoppingAssistantRouter:
    def __init__(self, store: SQLiteStore, default_city: str = "יבנה", chp_client: CHPClient | None = None, price_db: PriceDB | None = None):
        self.store = store
        self.default_city = default_city
        self.chp_client = chp_client
        self.price_db = price_db

    def handle_message(self, context: MessageContext) -> str:
        parsed = parse_message(context.text)
        return self._handle_parsed_message(context, parsed)

    def get_default_city(self, context: MessageContext) -> str:
        chat, _ = self._ensure_chat_and_list(context)
        return chat.default_city or self.default_city

    def preview_list(self, context: MessageContext) -> str:
        _, shopping_list = self._ensure_chat_and_list(context)
        return self._format_list(self.store.list_active_items(shopping_list.id))

    def handle_semantic_action(
        self,
        context: MessageContext,
        *,
        action: str,
        item_name: str = "",
        quantity: float | None = None,
        city: str = "",
        note: str = "",
    ) -> str:
        if action == "add":
            parsed = ParsedMessage(intent="add", value=item_name.strip(), quantity=quantity)
            return self._handle_parsed_message(context, parsed, note=note)
        if action == "show":
            return self._handle_parsed_message(context, ParsedMessage(intent="show", value=""))
        if action == "done":
            return self._handle_parsed_message(context, ParsedMessage(intent="done", value=item_name.strip()))
        if action == "delete":
            return self._handle_parsed_message(context, ParsedMessage(intent="delete", value=item_name.strip()))
        if action == "city":
            return self._handle_parsed_message(context, ParsedMessage(intent="city", value=city.strip()))
        if action == "price":
            return self._handle_parsed_message(context, ParsedMessage(intent="price", value=item_name.strip()))
        if action == "clear":
            chat, shopping_list = self._ensure_chat_and_list(context)
            count = self.store.clear_active_items(list_id=shopping_list.id, acting_user_id=context.user_id)
            if count == 0:
                return "הרשימה כבר ריקה"
            self.store.record_event(chat_id=chat.id, user_id=context.user_id, event_type="list_cleared", payload={"count": count})
            return f"הרשימה נוקתה ({count} פריטים נמחקו)"
        if action == "ignore":
            return ""
        return self.handle_message(context)


    def add_item_with_duplicate_check(
        self,
        context: MessageContext,
        *,
        item_name: str,
        quantity: float | None = None,
        note: str = "",
    ) -> str | DuplicateConflict:
        """Add an item, but check for duplicates first.
        Returns a string (success message) or DuplicateConflict if a similar item exists.
        """
        chat, shopping_list = self._ensure_chat_and_list(context)

        # Check for similar items
        similar = self.store.find_similar_items(list_id=shopping_list.id, query=item_name)
        if similar:
            # Return conflict for the first match
            return DuplicateConflict(
                new_item_name=item_name,
                new_quantity=quantity,
                new_note=note,
                existing_item=similar[0],
                list_id=shopping_list.id,
                chat_id=chat.id,
                user_id=context.user_id,
            )

        # No duplicate - add normally
        return self.handle_semantic_action(
            context, action="add", item_name=item_name, quantity=quantity, note=note,
        )

    def merge_duplicate(self, *, item_id: int, additional_quantity: float) -> str:
        """Merge quantity into existing item."""
        item = self.store.merge_item_quantity(item_id=item_id, additional_quantity=additional_quantity)
        q = int(item.quantity_value) if item.quantity_value and item.quantity_value.is_integer() else item.quantity_value
        return f"\u05de\u05d5\u05d6\u05d2: {item.normalized_name} (\u05e1\u05d4\"\u05db {q})"

    def update_duplicate(self, *, item_id: int, new_quantity: float) -> str:
        """Update existing item's quantity."""
        item = self.store.update_item_quantity(item_id=item_id, new_quantity=new_quantity)
        q = int(item.quantity_value) if item.quantity_value and item.quantity_value.is_integer() else item.quantity_value
        return f"\u05e2\u05d5\u05d3\u05db\u05df: {item.normalized_name} (\u05db\u05de\u05d5\u05ea: {q})"

    def force_add_item(self, context: MessageContext, *, item_name: str, quantity: float | None = None, note: str = "") -> str:
        """Add item without duplicate check (user chose 'add separately')."""
        return self.handle_semantic_action(
            context, action="add", item_name=item_name, quantity=quantity, note=note,
        )

    def _ensure_chat_and_list(self, context: MessageContext):
        chat = self.store.ensure_chat(
            platform=context.platform,
            external_chat_id=context.external_chat_id,
            title=context.title,
            default_city=self.default_city,
        )
        shopping_list = self.store.ensure_active_list(chat_id=chat.id)
        if context.user_name:
            self.store.upsert_user(chat_id=chat.id, user_id=context.user_id, display_name=context.user_name)
        return chat, shopping_list

    def list_items_by_user_name(self, context: MessageContext, *, user_name: str) -> str:
        """List items added by a specific user (searched by display name)."""
        chat, shopping_list = self._ensure_chat_and_list(context)
        items = self.store.list_items_by_user(
            list_id=shopping_list.id,
            user_name=user_name,
            chat_id=chat.id,
        )
        if not items:
            return f"לא נמצאו פריטים של {user_name} ברשימה"

        lines = [f"הפריטים של {user_name}:"]
        for item in items:
            lines.append(f"- {self._format_item(item)}")
        return "\n".join(lines)

    def _handle_parsed_message(self, context: MessageContext, parsed: ParsedMessage, note: str | None = None) -> str:
        chat, shopping_list = self._ensure_chat_and_list(context)

        if parsed.intent == "ignore":
            return ""

        if parsed.intent == "show":
            return self._format_list(self.store.list_active_items(shopping_list.id))

        if parsed.intent == "done":
            item = self.store.update_item_status(
                list_id=shopping_list.id,
                query=parsed.value,
                status="purchased",
                acting_user_id=context.user_id,
            )
            if item is None:
                return f"לא מצאתי ברשימה: {parsed.value}"
            self.store.record_event(chat_id=chat.id, user_id=context.user_id, event_type="item_purchased", payload={"item_id": item.id})
            return f"סומן כנקנה: {item.normalized_name}"

        if parsed.intent == "delete":
            item = self.store.update_item_status(
                list_id=shopping_list.id,
                query=parsed.value,
                status="deleted",
                acting_user_id=context.user_id,
            )
            if item is None:
                return f"לא מצאתי ברשימה: {parsed.value}"
            self.store.record_event(chat_id=chat.id, user_id=context.user_id, event_type="item_deleted", payload={"item_id": item.id})
            return f"נמחק: {item.normalized_name}"

        if parsed.intent == "help":
            return "פקודות: ?, תראה, קניתי <פריט>, מחק <פריט>, מחיר <פריט>"

        if parsed.intent == "price":
            # Try local price DB first (official feeds)
            if self.price_db:
                try:
                    results = self.price_db.search_product(parsed.value)
                    if results:
                        return format_feed_results(results, parsed.value)
                except Exception as exc:
                    logger.warning("Price DB query failed, trying CHP: %s", exc)

            # Fall back to CHP
            if self.chp_client:
                try:
                    chat, _ = self._ensure_chat_and_list(context)
                    city = chat.default_city or self.default_city
                    result = self.chp_client.search(parsed.value, city=city)
                    if result.stores or result.online_stores:
                        return format_price_summary(result, limit=5)
                except Exception as exc:
                    logger.exception("CHP price lookup failed: %s", exc)

            return f"בדיקת מחירים לא זמינה כרגע עבור {parsed.value}"

        if parsed.intent == "city":
            self.store.update_chat_default_city(chat_id=chat.id, default_city=parsed.value or self.default_city)
            return f"עיר ברירת המחדל עודכנה ל-{parsed.value or self.default_city}"

        item_draft = build_item(
            raw_text=context.text,
            normalized_name=parsed.value,
            quantity_value=parsed.quantity,
        )
        item_draft.note = note or item_draft.note
        created_item = self.store.add_item(
            list_id=shopping_list.id,
            raw_text=item_draft.raw_text,
            normalized_name=item_draft.normalized_name,
            quantity_value=item_draft.quantity_value,
            quantity_unit=item_draft.quantity_unit,
            note=item_draft.note,
            category=item_draft.category,
            added_by_user_id=context.user_id,
        )
        self.store.record_event(chat_id=chat.id, user_id=context.user_id, event_type="item_added", payload={"item_id": created_item.id})
        return f"נוסף: {self._format_item(created_item)}"

    def _format_list(self, items: list[StoredItem]) -> str:
        if not items:
            return "הרשימה ריקה"

        grouped: dict[str, list[StoredItem]] = defaultdict(list)
        for item in items:
            grouped[item.category or "כללי"].append(item)

        lines: list[str] = []
        for category in sorted(grouped):
            lines.append(category)
            for item in grouped[category]:
                lines.append(f"- {self._format_item(item)}")
        return "\n".join(lines)

    def _format_item(self, item: StoredItem) -> str:
        if item.quantity_value is None:
            return item.normalized_name

        quantity = int(item.quantity_value) if item.quantity_value.is_integer() else item.quantity_value
        if item.quantity_unit:
            return f"{quantity} {item.quantity_unit} {item.normalized_name}".strip()
        return f"{quantity} {item.normalized_name}".strip()
