from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests

import time as _time


class TTLDict:
    """Simple dict with automatic expiry of old entries."""
    def __init__(self, ttl_seconds: int = 600, max_size: int = 200):
        self._data: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl_seconds
        self._max_size = max_size

    def __setitem__(self, key: str, value: Any) -> None:
        self._evict()
        self._data[key] = (_time.time(), value)

    def pop(self, key: str, default: Any = None) -> Any:
        entry = self._data.pop(key, None)
        if entry is None:
            return default
        ts, value = entry
        if _time.time() - ts > self._ttl:
            return default
        return value

    def _evict(self) -> None:
        now = _time.time()
        # Remove expired
        expired = [k for k, (ts, _) in self._data.items() if now - ts > self._ttl]
        for k in expired:
            del self._data[k]
        # Enforce max size (remove oldest)
        while len(self._data) >= self._max_size:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            del self._data[oldest]


from src.agent.shopping_agent import ShoppingAgent
from src.channels.telegram_bot import TelegramBotAdapter

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    {"command": "list", "description": "הצג את רשימת הקניות"},
    {"command": "clear", "description": "נקה את כל הרשימה"},
    {"command": "help", "description": "מה אני יכול לעשות?"},
    {"command": "city", "description": "שנה עיר ברירת מחדל"},
]

HELP_TEXT = (
    "אני עוזר קניות לקבוצה הזו. ככה עובדים איתי:\n\n"
    "שלחו שם של מוצר ואני אוסיף אותו לרשימה.\n"
    "כמות? כתבו מספר לפני: 2 חלב\n\n"
    "פקודות:\n"
    "/list \u2014 הצגת הרשימה\n"
    "/clear \u2014 ניקוי כל הרשימה\n"
    "/city <עיר> \u2014 שינוי עיר ברירת מחדל\n"
    "/help \u2014 העזרה הזו\n\n"
    "אפשר גם בשפה טבעית:\n"
    '\"קניתי חלב\" \u2014 סימון כנקנה\n'
    '\"מחק לחם\" \u2014 מחיקה מהרשימה\n'
    '\"מה יש ברשימה\" \u2014 הצגת הרשימה\n'
    '\"מחיר חלב\" \u2014 בדיקת מחיר'
)

START_TEXT = (
    "שלום! אני עוזר הקניות של הקבוצה.\n"
    "שלחו שם של מוצר ואני אוסיף אותו לרשימה.\n"
    "לעזרה: /help"
)


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    payload: dict[str, Any]


class TelegramPollingBot:
    def __init__(self, token: str, agent: ShoppingAgent, timeout: int = 30):
        self.token = token
        self.agent = agent
        self.timeout = timeout
        self.adapter = TelegramBotAdapter()
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.session = requests.Session()
        self.pending_conflicts: TTLDict = TTLDict(ttl_seconds=600, max_size=200)

    def get_me(self) -> dict[str, Any]:
        response = self.session.get(f"{self.base_url}/getMe", timeout=15)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getMe failed: {payload}")
        return payload["result"]

    def set_commands(self) -> None:
        response = self.session.post(
            f"{self.base_url}/setMyCommands",
            json={"commands": BOT_COMMANDS},
            timeout=15,
        )
        response.raise_for_status()
        logger.info("Bot commands registered")

    def poll_forever(self) -> None:
        offset: int | None = None
        consecutive_failures = 0
        logger.info("Starting Telegram polling loop")

        while True:
            try:
                updates = self.get_updates(offset=offset)
                consecutive_failures = 0  # Reset on success
                for update in updates:
                    offset = update.update_id + 1
                    self.handle_update(update.payload)
            except requests.RequestException as exc:
                consecutive_failures += 1
                backoff = min(3 * (2 ** min(consecutive_failures - 1, 5)), 120)
                logger.exception("Telegram polling failed (attempt %d, backoff %ds): %s", consecutive_failures, backoff, exc)
                time.sleep(backoff)
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                backoff = min(3 * (2 ** min(consecutive_failures - 1, 5)), 120)
                logger.exception("Unexpected polling failure (attempt %d, backoff %ds): %s", consecutive_failures, backoff, exc)
                time.sleep(backoff)

    def get_updates(self, offset: int | None = None) -> list[TelegramUpdate]:
        response = self.session.get(
            f"{self.base_url}/getUpdates",
            params={
                "timeout": self.timeout,
                "offset": offset,
                "allowed_updates": '["message","callback_query"]',
            },
            timeout=self.timeout + 10,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {payload}")
        return [TelegramUpdate(update_id=item["update_id"], payload=item) for item in payload.get("result", [])]

    def handle_update(self, update: dict[str, Any]) -> None:
        # Handle callback queries (inline keyboard buttons)
        callback = update.get("callback_query")
        if callback:
            self._handle_callback(callback)
            return

        message = update.get("message")
        if not message:
            return
        if message.get("from", {}).get("is_bot"):
            return
        if not message.get("text"):
            return

        text = message["text"].strip()
        context = self.adapter.normalize_message(update).to_message_context()

        # Handle slash commands directly (no LLM round-trip)
        slash_response = self._handle_slash_command(text, context)
        if slash_response is not None:
            if slash_response:
                self.send_message(
                    chat_id=message["chat"]["id"],
                    text=slash_response,
                    message_thread_id=message.get("message_thread_id"),
                )
            return

        # Regular message — send to agent
        response_text = self.agent.handle_message(context)
        if not response_text:
            return

        # Check for pending duplicate conflicts
        conflict = self.agent.pending_conflicts.pop(context.external_chat_id, None)
        if conflict:
            self._send_duplicate_keyboard(
                chat_id=message["chat"]["id"],
                text=response_text,
                conflict=conflict,
                message_thread_id=message.get("message_thread_id"),
            )
            return

        # Check for pending price disambiguation
        price_result = self.agent.pending_price_choices.pop(context.external_chat_id, None)
        if price_result and price_result.needs_disambiguation:
            self._send_price_picker(
                chat_id=message["chat"]["id"],
                text=response_text,
                choices=price_result.choices,
                query=price_result.query,
                message_thread_id=message.get("message_thread_id"),
            )
            return

        self.send_message(
            chat_id=message["chat"]["id"],
            text=response_text,
            message_thread_id=message.get("message_thread_id"),
        )

    def _handle_slash_command(self, text: str, context: Any) -> str | None:
        if not text.startswith("/"):
            return None

        # Strip bot username if present (e.g., /list@nesher_shopping_bot)
        command = text.split()[0].split("@")[0].lower()
        args = text[len(text.split()[0]):].strip()

        if command == "/list":
            return self.agent.router.handle_semantic_action(context, action="show")
        elif command == "/clear":
            return self.agent.router.handle_semantic_action(context, action="clear")
        elif command == "/help":
            return HELP_TEXT
        elif command == "/city":
            if not args:
                city = self.agent.router.get_default_city(context)
                return f"העיר הנוכחית: {city}\nלשינוי: /city <שם עיר>"
            return self.agent.router.handle_semantic_action(context, action="city", city=args)
        elif command == "/start":
            return START_TEXT

        return None  # Unknown slash command — let agent handle


    def _send_duplicate_keyboard(
        self, chat_id: int, text: str, conflict: Any, message_thread_id: int | None = None,
    ) -> None:
        from src.app.router import DuplicateConflict

        conflict_id = uuid.uuid4().hex[:8]
        self.pending_conflicts[conflict_id] = conflict

        existing = conflict.existing_item
        eq = existing.quantity_value or 0
        nq = conflict.new_quantity or 0
        merged_q = eq + nq
        merged_display = int(merged_q) if merged_q == int(merged_q) else merged_q
        new_display = int(nq) if nq and nq == int(nq) else nq

        buttons: list[list[dict]] = []
        # Always show merge option
        if nq and eq:
            buttons.append([{"text": f"אחד (סה\"כ {merged_display})", "callback_data": f"dup:merge:{conflict_id}"}])
        elif nq:
            buttons.append([{"text": f"אחד ({new_display})", "callback_data": f"dup:merge:{conflict_id}"}])
        else:
            buttons.append([{"text": "אחד", "callback_data": f"dup:merge:{conflict_id}"}])
        
        if nq:
            buttons.append([{"text": f"עדכן ל-{new_display}", "callback_data": f"dup:update:{conflict_id}"}])
        buttons.append([{"text": "הוסף בנפרד", "callback_data": f"dup:add:{conflict_id}"}])
        buttons.append([{"text": "בטל", "callback_data": f"dup:cancel:{conflict_id}"}])

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": json.dumps({"inline_keyboard": buttons}),
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id

        response = self.session.post(f"{self.base_url}/sendMessage", json=payload, timeout=15)
        response.raise_for_status()

    def _send_price_picker(self, chat_id: int, text: str, choices, query: str, message_thread_id: int | None = None) -> None:
        """Send inline keyboard with product choices for price disambiguation."""
        buttons = []
        for i, choice in enumerate(choices[:6]):
            picker_id = uuid.uuid4().hex[:8]
            self.pending_conflicts[picker_id] = ("price_pick", choice)
            label = f"{choice.item_name} \u2014 \u20aa{choice.price:.2f}"
            if len(label) > 45:
                label = label[:42] + "..."
            buttons.append([{"text": label, "callback_data": f"pick:{picker_id}"}])

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": json.dumps({"inline_keyboard": buttons}),
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id

        try:
            response = self.session.post(f"{self.base_url}/sendMessage", json=payload, timeout=15)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to send price picker: %s", exc)

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback["id"]
        try:
            self._process_callback(callback)
        except Exception as exc:
            logger.exception("Callback handling failed: %s", exc)
            self._answer_callback(callback_id, "שגיאה — נסה שוב")

    def _process_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback["id"]
        data = callback.get("data", "")
        message = callback.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        thread_id = message.get("message_thread_id")

        if data.startswith("pick:"):
            picker_id = data.split(":", 1)[1]
            entry = self.pending_conflicts.pop(picker_id, None)
            if entry is None:
                self._answer_callback(callback_id, "\u05d4\u05d1\u05d7\u05d9\u05e8\u05d4 \u05e4\u05d2\u05d4 \u2014 \u05e0\u05e1\u05d4 \u05e9\u05d5\u05d1")
                return
            action_type, choice = entry
            if action_type == "price_pick":
                text = f"\u05de\u05d7\u05d9\u05e8 {choice.item_name}: \u20aa{choice.price:.2f} (\u05e9\u05d5\u05e4\u05e8\u05e1\u05dc)\n\u05de\u05e7\u05d5\u05e8: \u05e4\u05d9\u05d3 \u05e8\u05e9\u05de\u05d9"
                self._answer_callback(callback_id, f"\u20aa{choice.price:.2f}")
                self._edit_message(chat_id, message_id, text)
            return

        if not data.startswith("dup:"):
            self._answer_callback(callback_id, "\u05e4\u05e2\u05d5\u05dc\u05d4 \u05dc\u05d0 \u05de\u05d5\u05db\u05e8\u05ea")
            return

        parts = data.split(":", 2)
        if len(parts) != 3:
            self._answer_callback(callback_id, "\u05e9\u05d2\u05d9\u05d0\u05d4")
            return

        action = parts[1]
        conflict_id = parts[2]

        conflict = self.pending_conflicts.pop(conflict_id, None)
        if conflict is None:
            self._answer_callback(callback_id, "\u05d4\u05e4\u05e2\u05d5\u05dc\u05d4 \u05e4\u05d2\u05d4 \u2014 \u05e0\u05e1\u05d4 \u05e9\u05d5\u05d1")
            self._edit_message(chat_id, message_id, message.get("text", "") + "\n\n(\u05e4\u05d2 \u05ea\u05d5\u05e7\u05e3)")
            return

        # Build a fake context for the router
        from src.app.router import MessageContext
        context = MessageContext(
            platform="telegram",
            external_chat_id=str(chat_id) + (f":{thread_id}" if thread_id else ""),
            user_id=conflict.user_id,
            text="",
        )

        response_text = ""
        if action == "merge":
            nq = conflict.new_quantity or 1
            response_text = self.agent.router.merge_duplicate(
                item_id=conflict.existing_item.id,
                additional_quantity=nq,
            )
        elif action == "update":
            response_text = self.agent.router.update_duplicate(
                item_id=conflict.existing_item.id,
                new_quantity=conflict.new_quantity or 1,
            )
        elif action == "add":
            response_text = self.agent.router.force_add_item(
                context,
                item_name=conflict.new_item_name,
                quantity=conflict.new_quantity,
                note=conflict.new_note or "",
            )
        elif action == "cancel":
            response_text = "\u05d1\u05d5\u05d8\u05dc \u2014 \u05d4\u05e4\u05e8\u05d9\u05d8 \u05dc\u05d0 \u05e0\u05d5\u05e1\u05e3"

        # Answer the callback (removes loading spinner)
        self._answer_callback(callback_id, response_text[:200])

        # Edit the original message to show the result and remove buttons
        original_text = message.get("text", "")
        self._edit_message(chat_id, message_id, original_text + f"\n\n\u2705 {response_text}")

    def _answer_callback(self, callback_id: str, text: str) -> None:
        try:
            self.session.post(
                f"{self.base_url}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text},
                timeout=15,
            )
        except Exception as exc:
            logger.warning("Failed to answer callback: %s", exc)

    def _edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            self.session.post(
                f"{self.base_url}/editMessageText",
                json={"chat_id": chat_id, "message_id": message_id, "text": text},
                timeout=15,
            )
        except Exception as exc:
            logger.warning("Failed to edit message: %s", exc)

    def send_message(self, chat_id: int, text: str, message_thread_id: int | None = None) -> None:
        # Telegram max message length is 4096 characters
        MAX_LEN = 4096
        chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] if len(text) > MAX_LEN else [text]

        for chunk in chunks:
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
            }
            if message_thread_id is not None:
                payload["message_thread_id"] = message_thread_id
            response = self.session.post(f"{self.base_url}/sendMessage", json=payload, timeout=15)
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise RuntimeError(f"Telegram sendMessage failed: {body}")
