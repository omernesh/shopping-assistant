from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import json

from src.domain.parser import ParsedMessage, parse_message
from src.domain.shopping_list import build_item
from src.storage.sqlite_store import SQLiteStore, StoredItem

import logging
from src.integrations.chp_client import CHPClient, format_price_summary
from src.integrations.feed_downloader import PriceDB, format_feed_results
from src.integrations.price_service import PriceService, format_list_estimate, format_chain_comparison, PriceLookupResult

logger = logging.getLogger(__name__)


def _fmt_qty(value: float | None) -> str | None:
    """Format a quantity value for display (e.g., 2.0 -> '2', 1.5 -> '1.5')."""
    if value is None:
        return None
    return str(int(value)) if value == int(value) else str(value)


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
    def __init__(self, store: SQLiteStore, default_city: str = "\u05d9\u05d1\u05e0\u05d4", chp_client: CHPClient | None = None, price_db: PriceDB | None = None, price_service: PriceService | None = None):
        self.store = store
        self.default_city = default_city
        self.chp_client = chp_client
        self.price_db = price_db
        self.price_service = price_service

    # ------------------------------------------------------------------
    # Active-list helpers (delegate to store)
    # ------------------------------------------------------------------

    def _get_active_list_name(self, chat_id: int) -> str:
        """Return the active list name for a chat (default: 'main')."""
        list_id = self.store.get_active_list_id(chat_id)
        all_lists = self.store.get_all_lists(chat_id)
        for lst in all_lists:
            if lst["id"] == list_id:
                return lst["name"]
        return "main"

    def _ensure_chat_and_list(self, context: MessageContext):
        chat = self.store.ensure_chat(
            platform=context.platform,
            external_chat_id=context.external_chat_id,
            title=context.title,
            default_city=self.default_city,
        )
        active_name = self._get_active_list_name(chat.id)
        shopping_list = self.store.ensure_active_list(chat_id=chat.id, name=active_name)
        if context.user_name:
            self.store.upsert_user(chat_id=chat.id, user_id=context.user_id, display_name=context.user_name)
        return chat, shopping_list

    # ------------------------------------------------------------------
    # Public API -- existing
    # ------------------------------------------------------------------

    def handle_message(self, context: MessageContext) -> str:
        parsed = parse_message(context.text)
        return self._handle_parsed_message(context, parsed)

    def get_default_city(self, context: MessageContext) -> str:
        chat, _ = self._ensure_chat_and_list(context)
        return chat.default_city or self.default_city

    def price_lookup_with_disambiguation(self, context: MessageContext, *, item_name: str) -> "PriceLookupResult":
        """Price lookup that may return disambiguation choices."""
        if not self.price_service:
            return PriceLookupResult(text="\u05e9\u05d9\u05e8\u05d5\u05ea \u05d4\u05de\u05d7\u05d9\u05e8\u05d9\u05dd \u05dc\u05d0 \u05d6\u05de\u05d9\u05df \u05db\u05e8\u05d2\u05e2")
        chat, _ = self._ensure_chat_and_list(context)
        city = chat.default_city or self.default_city
        return self.price_service.price_lookup_with_disambiguation(item_name, city=city)

    def preview_list(self, context: MessageContext) -> str:
        _, shopping_list = self._ensure_chat_and_list(context)
        return self._format_list(self.store.list_active_items(shopping_list.id))

    def get_active_list_display(self, context: MessageContext) -> str:
        """Return the name of the currently active list (for LLM context)."""
        chat, _ = self._ensure_chat_and_list(context)
        return self._get_active_list_name(chat.id)

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
                return "\u05d4\u05e8\u05e9\u05d9\u05de\u05d4 \u05db\u05d1\u05e8 \u05e8\u05d9\u05e7\u05d4"
            self.store.record_event(chat_id=chat.id, user_id=context.user_id, event_type="list_cleared", payload={"count": count})
            return f"\u05d4\u05e8\u05e9\u05d9\u05de\u05d4 \u05e0\u05d5\u05e7\u05ea\u05d4 ({count} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd \u05e0\u05de\u05d7\u05e7\u05d5)"
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
        After adding, fetches estimated price and stores it on the item.
        """
        chat, shopping_list = self._ensure_chat_and_list(context)

        # Check for similar items
        similar = self.store.find_similar_items(list_id=shopping_list.id, query=item_name)
        if similar:
            return DuplicateConflict(
                new_item_name=item_name,
                new_quantity=quantity,
                new_note=note,
                existing_item=similar[0],
                list_id=shopping_list.id,
                chat_id=chat.id,
                user_id=context.user_id,
            )

        # No duplicate -- add normally
        return self._add_item_internal(
            context, chat, shopping_list,
            item_name=item_name, quantity=quantity, note=note,
        )

    def _add_item_internal(
        self,
        context: MessageContext,
        chat,
        shopping_list,
        *,
        item_name: str,
        quantity: float | None = None,
        note: str = "",
    ) -> str:
        """Core add-item logic -- creates the item, fetches price estimate, returns confirmation."""
        item_draft = build_item(
            raw_text=item_name,
            normalized_name=item_name,
            quantity_value=quantity,
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
            added_by_name=context.user_name,
        )
        self.store.record_event(
            chat_id=chat.id, user_id=context.user_id,
            event_type="item_added", payload={"item_id": created_item.id},
        )

        # Fetch estimated price (best-effort)
        estimated_price = None
        if self.price_service:
            try:
                city = chat.default_city or self.default_city
                price_info = self.price_service.lookup_item_prices(
                    item_draft.normalized_name,
                    quantity=item_draft.quantity_value or 1,
                    city=city,
                )
                if price_info.best_price is not None:
                    estimated_price = price_info.best_price
                    self.store.update_item_estimated_price(
                        item_id=created_item.id,
                        estimated_price=estimated_price,
                    )
            except Exception as exc:
                logger.warning("Price estimate failed for %s: %s", item_draft.normalized_name, exc)

        display = self._format_item(created_item)
        if estimated_price is not None:
            display += f" \u2014 ~\u20aa{estimated_price:.2f}"
        return f"\u05e0\u05d5\u05e1\u05e3: {display}"

    def merge_duplicate(self, *, item_id: int, additional_quantity: float) -> str:
        """Merge quantity into existing item."""
        item = self.store.merge_item_quantity(item_id=item_id, additional_quantity=additional_quantity)
        if item is None:
            return "\u05d4\u05e4\u05e8\u05d9\u05d8 \u05db\u05d1\u05e8 \u05dc\u05d0 \u05e7\u05d9\u05d9\u05dd \u05d1\u05e8\u05e9\u05d9\u05de\u05d4"
        q = _fmt_qty(item.quantity_value)
        return f"\u05de\u05d5\u05d6\u05d2: {item.normalized_name} (\u05e1\u05d4\"\u05db {q})"

    def update_duplicate(self, *, item_id: int, new_quantity: float) -> str:
        """Update existing item's quantity."""
        item = self.store.update_item_quantity(item_id=item_id, new_quantity=new_quantity)
        if item is None:
            return "\u05d4\u05e4\u05e8\u05d9\u05d8 \u05db\u05d1\u05e8 \u05dc\u05d0 \u05e7\u05d9\u05d9\u05dd \u05d1\u05e8\u05e9\u05d9\u05de\u05d4"
        q = _fmt_qty(item.quantity_value)
        return f"\u05e2\u05d5\u05d3\u05db\u05df: {item.normalized_name} (\u05db\u05de\u05d5\u05ea: {q})"

    def force_add_item(self, context: MessageContext, *, item_name: str, quantity: float | None = None, note: str = "") -> str:
        """Add item without duplicate check (user chose 'add separately')."""
        return self.handle_semantic_action(
            context, action="add", item_name=item_name, quantity=quantity, note=note,
        )

    def estimate_list_cost(self, context: MessageContext) -> str:
        """Estimate total cost of the current shopping list."""
        if not self.price_service:
            return "\u05e9\u05d9\u05e8\u05d5\u05ea \u05d4\u05de\u05d7\u05d9\u05e8\u05d9\u05dd \u05dc\u05d0 \u05d6\u05de\u05d9\u05df \u05db\u05e8\u05d2\u05e2"

        chat, shopping_list = self._ensure_chat_and_list(context)
        items = self.store.list_active_items(list_id=shopping_list.id)
        if not items:
            return "\u05d4\u05e8\u05e9\u05d9\u05de\u05d4 \u05e8\u05d9\u05e7\u05d4"

        city = chat.default_city or self.default_city
        item_tuples = [(item.normalized_name, item.quantity_value or 1) for item in items]
        try:
            estimate = self.price_service.estimate_list_cost(item_tuples, city=city)
            return format_list_estimate(estimate)
        except Exception as exc:
            logger.warning("List cost estimation failed: %s", exc)
            return "\u05e9\u05d2\u05d9\u05d0\u05d4 \u05d1\u05d4\u05e2\u05e8\u05db\u05ea \u05e2\u05dc\u05d5\u05ea \u05d4\u05e8\u05e9\u05d9\u05de\u05d4"

    def compare_list_by_chain(self, context: MessageContext) -> str:
        """Compare total list cost across chains."""
        if not self.price_service:
            return "\u05e9\u05d9\u05e8\u05d5\u05ea \u05d4\u05de\u05d7\u05d9\u05e8\u05d9\u05dd \u05dc\u05d0 \u05d6\u05de\u05d9\u05df \u05db\u05e8\u05d2\u05e2"

        chat, shopping_list = self._ensure_chat_and_list(context)
        items = self.store.list_active_items(list_id=shopping_list.id)
        if not items:
            return "\u05d4\u05e8\u05e9\u05d9\u05de\u05d4 \u05e8\u05d9\u05e7\u05d4"

        city = chat.default_city or self.default_city
        item_tuples = [(item.normalized_name, item.quantity_value or 1) for item in items]
        try:
            comparison = self.price_service.compare_list_by_chain(item_tuples, city=city)
            if not comparison:
                return "\u05dc\u05d0 \u05d4\u05e6\u05dc\u05d7\u05ea\u05d9 \u05dc\u05de\u05e6\u05d5\u05d0 \u05de\u05d7\u05d9\u05e8\u05d9\u05dd \u05dc\u05d4\u05e9\u05d5\u05d5\u05d0\u05d4"
            return format_chain_comparison(comparison)
        except Exception as exc:
            logger.warning("Chain comparison failed: %s", exc)
            return "\u05e9\u05d2\u05d9\u05d0\u05d4 \u05d1\u05d4\u05e9\u05d5\u05d5\u05d0\u05ea \u05de\u05d7\u05d9\u05e8\u05d9\u05dd"

    def list_items_by_user_name(self, context: MessageContext, *, user_name: str) -> str:
        """List items added by a specific user (searched by display name)."""
        chat, shopping_list = self._ensure_chat_and_list(context)
        items = self.store.list_items_by_user(
            list_id=shopping_list.id,
            user_name=user_name,
            chat_id=chat.id,
        )
        if not items:
            return f"\u05dc\u05d0 \u05e0\u05de\u05e6\u05d0\u05d5 \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd \u05e9\u05dc {user_name} \u05d1\u05e8\u05e9\u05d9\u05de\u05d4"

        lines = [f"\u05d4\u05e4\u05e8\u05d9\u05d8\u05d9\u05dd \u05e9\u05dc {user_name}:"]
        for item in items:
            lines.append(f"- {self._format_item(item)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # NEW: Multi-list operations
    # ------------------------------------------------------------------

    def show_lists(self, chat_id: int) -> str:
        """Return formatted overview of all lists for this chat with item counts."""
        all_lists = self.store.get_all_lists(chat_id)

        if not all_lists:
            return "\u05d0\u05d9\u05df \u05e8\u05e9\u05d9\u05de\u05d5\u05ea \u05e2\u05d3\u05d9\u05d9\u05df"

        lines = ["\U0001f4cb \u05d4\u05e8\u05e9\u05d9\u05de\u05d5\u05ea \u05e9\u05dc\u05da:"]
        for lst in all_lists:
            name = lst["name"]
            count = lst["pending_count"]
            if lst["is_working_list"]:
                lines.append(f"\u2705 {name} ({count} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd) \u2014 \u05e4\u05e2\u05d9\u05dc\u05d4")
            else:
                lines.append(f"\U0001f4dd {name} ({count} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd)")
        return "\n".join(lines)

    def switch_list(self, chat_id: int, list_name: str) -> str:
        """Switch the active list for this chat. Creates the list if it doesn't exist."""
        list_name = list_name.strip()
        if not list_name:
            return "\u05e6\u05e8\u05d9\u05da \u05dc\u05e6\u05d9\u05d9\u05df \u05e9\u05dd \u05e8\u05e9\u05d9\u05de\u05d4"
        if not self.store.list_exists(chat_id=chat_id, name=list_name):
            self.store.create_list(chat_id=chat_id, name=list_name)
        sl = self.store.ensure_active_list(chat_id=chat_id, name=list_name)
        self.store.set_active_list(chat_id=chat_id, list_id=sl.id)
        return f"\u05e2\u05d1\u05e8\u05ea\u05d9 \u05dc\u05e8\u05e9\u05d9\u05de\u05d4: {list_name}"

    def create_list(self, chat_id: int, list_name: str) -> str:
        """Create a new list for this chat."""
        list_name = list_name.strip()
        if not list_name:
            return "\u05e6\u05e8\u05d9\u05da \u05dc\u05e6\u05d9\u05d9\u05df \u05e9\u05dd \u05e8\u05e9\u05d9\u05de\u05d4"
        self.store.create_list(chat_id=chat_id, name=list_name)
        return f"\u05e8\u05e9\u05d9\u05de\u05d4 \u05d7\u05d3\u05e9\u05d4 \u05e0\u05d5\u05e6\u05e8\u05d4: {list_name}"

    def move_items(self, chat_id: int, item_names: list[str], target_list_name: str) -> str:
        """Move items from the active list to a target list."""
        target_list_name = target_list_name.strip()

        if not self.store.list_exists(chat_id=chat_id, name=target_list_name):
            return f"\u05d4\u05e8\u05e9\u05d9\u05de\u05d4 \"{target_list_name}\" \u05dc\u05d0 \u05e7\u05d9\u05d9\u05de\u05ea. \u05e8\u05d5\u05e6\u05d4 \u05e9\u05d0\u05e6\u05d5\u05e8 \u05d0\u05d5\u05ea\u05d4?"

        target_list = self.store.ensure_active_list(chat_id=chat_id, name=target_list_name)
        source_list_id = self.store.get_active_list_id(chat_id)

        moved = self.store.move_items_by_name(
            source_list_id=source_list_id,
            item_names=item_names,
            target_list_id=target_list.id,
        )

        if moved:
            return f"\u05d4\u05d5\u05e2\u05d1\u05e8\u05d5 {moved} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd \u05dc-{target_list_name}"
        return "\u05dc\u05d0 \u05e0\u05de\u05e6\u05d0\u05d5 \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd \u05dc\u05d4\u05e2\u05d1\u05e8\u05d4"

    def complete_list(
        self,
        chat_id: int,
        list_name: str | None = None,
        user_id: str | None = None,
        user_name: str | None = None,
    ) -> str:
        """Complete a list: mark pending as bought, snapshot to purchase_history, return summary."""
        if list_name:
            if not self.store.list_exists(chat_id=chat_id, name=list_name):
                return f"\u05dc\u05d0 \u05e0\u05de\u05e6\u05d0\u05d4 \u05e8\u05e9\u05d9\u05de\u05d4: {list_name}"
            sl = self.store.ensure_active_list(chat_id=chat_id, name=list_name)
            list_id = sl.id
            target_name = list_name
        else:
            list_id = self.store.get_active_list_id(chat_id)
            target_name = self._get_active_list_name(chat_id)

        # Get pending items
        pending_items = self.store.list_active_items(list_id)
        if not pending_items:
            return f"\u05d4\u05e8\u05e9\u05d9\u05de\u05d4 \"{target_name}\" \u05e8\u05d9\u05e7\u05d4"

        # Mark each pending item as bought
        for item in pending_items:
            self.store.update_item_status(
                list_id=list_id,
                query=item.normalized_name,
                status="bought",
                acting_user_id=user_id,
            )

        # Snapshot bought items via store
        history_id = self.store.complete_list(
            list_id=list_id,
            completed_by_user_id=user_id,
            completed_by_name=user_name,
        )

        # Format summary
        estimated_total = sum(item.estimated_price or 0 for item in pending_items)
        lines = [f"\u2705 \u05d4\u05e8\u05e9\u05d9\u05de\u05d4 \"{target_name}\" \u05d4\u05d5\u05e9\u05dc\u05de\u05d4!"]
        lines.append(f"   {len(pending_items)} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd")
        if estimated_total > 0:
            lines.append(f"   \u05d4\u05e2\u05e8\u05db\u05d4: \u20aa{estimated_total:.0f}")
        lines.append(f"\n\U0001f4b0 \u05e1\u05d4\"\u05db: \u20aa{estimated_total:.0f}")
        return "\n".join(ln for ln in lines if ln)

    def show_history(self, chat_id: int, months_back: int = 1) -> str:
        """Return formatted purchase history for the last N months."""
        records = self.store.get_purchase_history(chat_id, months_back)

        if not records:
            period = "\u05d7\u05d5\u05d3\u05e9 \u05d0\u05d7\u05d3" if months_back == 1 else f"{months_back} \u05d7\u05d5\u05d3\u05e9\u05d9\u05dd"
            return f"\u05d0\u05d9\u05df \u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d9\u05ea \u05e7\u05e0\u05d9\u05d5\u05ea \u05d1-{period} \u05d4\u05d0\u05d7\u05e8\u05d5\u05e0\u05d9\u05dd"

        period_label = "\u05d7\u05d5\u05d3\u05e9 \u05d0\u05d7\u05d3" if months_back == 1 else f"{months_back} \u05d7\u05d5\u05d3\u05e9\u05d9\u05dd \u05d0\u05d7\u05e8\u05d5\u05e0\u05d9\u05dd"
        lines = [f"\U0001f4ca \u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d9\u05ea \u05e7\u05e0\u05d9\u05d5\u05ea \u2014 {period_label}:"]

        grand_total = 0.0
        for r in records:
            completed_at = (r.completed_at or "")[:10]
            try:
                dt = datetime.fromisoformat(completed_at)
                date_display = dt.strftime("%d/%m/%Y")
            except Exception:
                date_display = completed_at
            list_name = r.list_name
            count = r.item_count or 0
            est = r.total_estimated or 0
            act = r.total_purchased or 0
            cost = act if act else est
            grand_total += cost

            lines.append("")
            lines.append(f"\U0001f6d2 {list_name} \u2014 {date_display}")
            parts = [f"   {count} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd"]
            if est:
                parts.append(f"\u05d4\u05e2\u05e8\u05db\u05d4: \u20aa{est:.0f}")
            if act:
                parts.append(f"\u05d1\u05e4\u05d5\u05e2\u05dc: \u20aa{act:.0f}")
            lines.append(" | ".join(parts))

        # Monthly total from store
        now = datetime.now()
        monthly = self.store.get_monthly_expenses(chat_id, now.year, now.month)
        total = monthly.get("total_spent", grand_total)

        lines.append("")
        lines.append(f"\U0001f4b0 \u05e1\u05d4\"\u05db \u05d4\u05d7\u05d5\u05d3\u05e9: \u20aa{total:.0f}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Mark purchased -- enhanced with price re-lookup
    # ------------------------------------------------------------------

    def handle_mark_purchased(
        self,
        context: MessageContext,
        *,
        item_name: str,
        **kwargs,
    ) -> str:
        """Mark item as bought with price re-lookup at purchase time."""
        chat, shopping_list = self._ensure_chat_and_list(context)

        item = self.store.update_item_status(
            list_id=shopping_list.id,
            query=item_name,
            status="bought",
            acting_user_id=context.user_id,
        )
        if item is None:
            return f"\u05e7\u05e0\u05d9\u05ea\u05d9 {item_name} \u2014 \u05dc\u05d0 \u05d4\u05d9\u05d4 \u05d1\u05e8\u05e9\u05d9\u05de\u05d4"

        self.store.record_event(
            chat_id=chat.id, user_id=context.user_id,
            event_type="item_purchased", payload={"item_id": item.id},
        )

        # Re-lookup price at purchase time
        purchase_price = None
        store_name = None
        chain_name = None
        if self.price_service:
            try:
                city = chat.default_city or self.default_city
                price_info = self.price_service.lookup_item_prices(
                    item.normalized_name,
                    quantity=item.quantity_value or 1,
                    city=city,
                )
                if price_info.best_price is not None:
                    purchase_price = price_info.best_price
                    store_name = price_info.best_store or None
                    if price_info.chain_prices:
                        cheapest_chain = min(price_info.chain_prices, key=price_info.chain_prices.get)
                        chain_name = cheapest_chain
            except Exception as exc:
                logger.warning("Purchase price lookup failed for %s: %s", item.normalized_name, exc)

        # Update item with purchase details via store
        self.store.update_item_purchase(
            item_id=item.id,
            purchase_price=purchase_price,
            store_name=store_name,
            chain_name=chain_name,
            purchased_by_name=context.user_name,
        )

        result = f"\u05e1\u05d5\u05de\u05df \u05db\u05e0\u05e7\u05e0\u05d4: {item.normalized_name}"
        if purchase_price is not None:
            result += f" (\u20aa{purchase_price:.2f}"
            if store_name:
                result += f" \u05d1-{store_name}"
            result += ")"
        return result

    # ------------------------------------------------------------------
    # Internal -- message handling
    # ------------------------------------------------------------------

    def _handle_parsed_message(self, context: MessageContext, parsed: ParsedMessage, note: str | None = None) -> str:
        chat, shopping_list = self._ensure_chat_and_list(context)

        if parsed.intent == "ignore":
            return ""

        if parsed.intent == "show":
            return self._format_list(self.store.list_active_items(shopping_list.id))

        if parsed.intent == "done":
            return self.handle_mark_purchased(context, item_name=parsed.value)

        if parsed.intent == "delete":
            item = self.store.update_item_status(
                list_id=shopping_list.id,
                query=parsed.value,
                status="deleted",
                acting_user_id=context.user_id,
            )
            if item is None:
                return f"\u05dc\u05d0 \u05de\u05e6\u05d0\u05ea\u05d9 \u05d1\u05e8\u05e9\u05d9\u05de\u05d4: {parsed.value}"
            self.store.record_event(chat_id=chat.id, user_id=context.user_id, event_type="item_deleted", payload={"item_id": item.id})
            return f"\u05e0\u05de\u05d7\u05e7: {item.normalized_name}"

        if parsed.intent == "help":
            return "\u05e4\u05e7\u05d5\u05d3\u05d5\u05ea: ?, \u05ea\u05e8\u05d0\u05d4, \u05e7\u05e0\u05d9\u05ea\u05d9 <\u05e4\u05e8\u05d9\u05d8>, \u05de\u05d7\u05e7 <\u05e4\u05e8\u05d9\u05d8>, \u05de\u05d7\u05d9\u05e8 <\u05e4\u05e8\u05d9\u05d8>"

        if parsed.intent == "price":
            if self.price_service:
                result = self.price_service.price_lookup_with_disambiguation(parsed.value, city=chat.default_city or self.default_city)
                return result.text
            if self.price_db:
                try:
                    results = self.price_db.search_product(parsed.value)
                    if results:
                        return format_feed_results(results, parsed.value)
                except Exception as exc:
                    logger.warning("Price DB query failed, trying CHP: %s", exc)
            if self.chp_client:
                try:
                    city = chat.default_city or self.default_city
                    result = self.chp_client.search(parsed.value, city=city)
                    if result.stores or result.online_stores:
                        return format_price_summary(result, limit=5)
                except Exception as exc:
                    logger.exception("CHP price lookup failed: %s", exc)
            return f"\u05d1\u05d3\u05d9\u05e7\u05ea \u05de\u05d7\u05d9\u05e8\u05d9\u05dd \u05dc\u05d0 \u05d6\u05de\u05d9\u05e0\u05d4 \u05db\u05e8\u05d2\u05e2 \u05e2\u05d1\u05d5\u05e8 {parsed.value}"

        if parsed.intent == "city":
            self.store.update_chat_default_city(chat_id=chat.id, default_city=parsed.value or self.default_city)
            return f"\u05e2\u05d9\u05e8 \u05d1\u05e8\u05d9\u05e8\u05ea \u05d4\u05de\u05d7\u05d3\u05dc \u05e2\u05d5\u05d3\u05db\u05e0\u05d4 \u05dc-{parsed.value or self.default_city}"

        # Default: add item
        return self._add_item_internal(
            context, chat, shopping_list,
            item_name=parsed.value, quantity=parsed.quantity, note=note,
        )

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def _format_list(self, items: list[StoredItem]) -> str:
        if not items:
            return "\u05d4\u05e8\u05e9\u05d9\u05de\u05d4 \u05e8\u05d9\u05e7\u05d4"

        grouped: dict[str, list[StoredItem]] = defaultdict(list)
        for item in items:
            grouped[item.category or "\u05db\u05dc\u05dc\u05d9"].append(item)

        cat_emoji = {
            "\u05d9\u05e8\u05e7\u05d5\u05ea \u05d5\u05e4\u05d9\u05e8\u05d5\u05ea": "\U0001f96c",
            "\u05de\u05d5\u05e6\u05e8\u05d9 \u05d7\u05dc\u05d1": "\U0001f9ca",
            "\u05d1\u05e9\u05e8 \u05d5\u05d3\u05d2\u05d9\u05dd": "\U0001f969",
            "\u05de\u05d0\u05e4\u05d9\u05dd \u05d5\u05dc\u05d7\u05dd": "\U0001f35e",
            "\u05e9\u05ea\u05d9\u05d9\u05d4": "\U0001f964",
            "\u05de\u05de\u05ea\u05e7\u05d9\u05dd \u05d5\u05d7\u05d8\u05d9\u05e4\u05d9\u05dd": "\U0001f36c",
            "\u05e0\u05d9\u05e7\u05d9\u05d5\u05df": "\U0001f9f9",
            "\u05db\u05dc\u05dc\u05d9": "\U0001f4e6",
        }

        lines: list[str] = []
        total_estimated = 0.0
        has_prices = False

        for category in sorted(grouped):
            emoji = cat_emoji.get(category, "\U0001f4e6")
            lines.append(f"{emoji} {category}:")
            for item in grouped[category]:
                display = self._format_item(item)
                price_str = ""
                ep = item.estimated_price
                added_by = item.added_by_name
                if ep is not None:
                    price_str += f" \u2014 ~\u20aa{ep:.2f}"
                    total_estimated += ep
                    has_prices = True
                if added_by:
                    price_str += f" [{added_by}]"
                lines.append(f"\u2022 {display}{price_str}")

        if has_prices:
            lines.append("")
            lines.append(f"\U0001f4b0 \u05e1\u05d4\"\u05db \u05de\u05e9\u05d5\u05e2\u05e8: \u20aa{total_estimated:.2f}")
        return "\n".join(lines)

    def _format_item(self, item: StoredItem) -> str:
        if item.quantity_value is None:
            return item.normalized_name

        quantity = _fmt_qty(item.quantity_value)
        if item.quantity_unit:
            return f"{item.normalized_name} ({quantity} {item.quantity_unit})".strip()
        return f"{item.normalized_name} ({quantity})".strip()
