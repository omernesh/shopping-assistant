from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.domain.parser import ParsedMessage, parse_message
from src.domain.shopping_list import build_item
from src.storage.sqlite_store import SQLiteStore, StoredItem


@dataclass(frozen=True)
class MessageContext:
    platform: str
    external_chat_id: str
    user_id: str
    text: str
    title: str | None = None

    def with_text(self, text: str) -> "MessageContext":
        return MessageContext(
            platform=self.platform,
            external_chat_id=self.external_chat_id,
            user_id=self.user_id,
            text=text,
            title=self.title,
        )


class ShoppingAssistantRouter:
    def __init__(self, store: SQLiteStore, default_city: str = "יבנה"):
        self.store = store
        self.default_city = default_city

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

    def _ensure_chat_and_list(self, context: MessageContext):
        chat = self.store.ensure_chat(
            platform=context.platform,
            external_chat_id=context.external_chat_id,
            title=context.title,
            default_city=self.default_city,
        )
        shopping_list = self.store.ensure_active_list(chat_id=chat.id)
        return chat, shopping_list

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
            return f"עדיין לא חיברתי מחיר חי ל-{parsed.value}. זה הבא בתור."

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
