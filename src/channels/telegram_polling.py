from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests


class TTLDict:
    """Simple dict with automatic expiry of old entries."""
    def __init__(self, ttl_seconds: int = 600, max_size: int = 200):
        self._data: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl_seconds
        self._max_size = max_size

    def __setitem__(self, key: str, value: Any) -> None:
        self._evict()
        self._data[key] = (time.time(), value)

    def pop(self, key: str, default: Any = None) -> Any:
        entry = self._data.pop(key, None)
        if entry is None:
            return default
        ts, value = entry
        if time.time() - ts > self._ttl:
            return default
        return value

    def _evict(self) -> None:
        now = time.time()
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
from src.domain.shopping_mode import (
    ShoppingModeManager, ACTIVATION_PHRASES, DEACTIVATION_PHRASES,
    ACTIVATE_MSG, DEACTIVATE_MSG,
)

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    {"command": "list", "description": "\u05d4\u05e6\u05d2 \u05d0\u05ea \u05e8\u05e9\u05d9\u05de\u05ea \u05d4\u05e7\u05e0\u05d9\u05d5\u05ea"},
    {"command": "clear", "description": "\u05e0\u05e7\u05d4 \u05d0\u05ea \u05db\u05dc \u05d4\u05e8\u05e9\u05d9\u05de\u05d4"},
    {"command": "help", "description": "\u05de\u05d4 \u05d0\u05e0\u05d9 \u05d9\u05db\u05d5\u05dc \u05dc\u05e2\u05e9\u05d5\u05ea?"},
    {"command": "city", "description": "\u05e9\u05e0\u05d4 \u05e2\u05d9\u05e8 \u05d1\u05e8\u05d9\u05e8\u05ea \u05de\u05d7\u05d3\u05dc"},
    {"command": "shop", "description": "\u05d4\u05e4\u05e2\u05dc/\u05db\u05d1\u05d4 \u05de\u05e6\u05d1 \u05e7\u05e0\u05d9\u05d5\u05ea"},
    {"command": "lists", "description": "\u05d4\u05e6\u05d2 \u05db\u05dc \u05d4\u05e8\u05e9\u05d9\u05de\u05d5\u05ea"},
    {"command": "history", "description": "\u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d9\u05ea \u05e7\u05e0\u05d9\u05d5\u05ea \u05d7\u05d5\u05d3\u05e9 \u05d0\u05d7\u05e8\u05d5\u05df"},
]

HELP_TEXT = (
    "\u05d0\u05e0\u05d9 \u05e2\u05d5\u05d6\u05e8 \u05e7\u05e0\u05d9\u05d5\u05ea \u05dc\u05e7\u05d1\u05d5\u05e6\u05d4 \u05d4\u05d6\u05d5. \u05db\u05db\u05d4 \u05e2\u05d5\u05d1\u05d3\u05d9\u05dd \u05d0\u05d9\u05ea\u05d9:\n\n"
    "\u05e9\u05dc\u05d7\u05d5 \u05e9\u05dd \u05e9\u05dc \u05de\u05d5\u05e6\u05e8 \u05d5\u05d0\u05e0\u05d9 \u05d0\u05d5\u05e1\u05d9\u05e3 \u05d0\u05d5\u05ea\u05d5 \u05dc\u05e8\u05e9\u05d9\u05de\u05d4.\n"
    "\u05db\u05de\u05d5\u05ea? \u05db\u05ea\u05d1\u05d5 \u05de\u05e1\u05e4\u05e8 \u05dc\u05e4\u05e0\u05d9: 2 \u05d7\u05dc\u05d1\n\n"
    "\u05e4\u05e7\u05d5\u05d3\u05d5\u05ea:\n"
    "/list \u2014 \u05d4\u05e6\u05d2\u05ea \u05d4\u05e8\u05e9\u05d9\u05de\u05d4\n"
    "/clear \u2014 \u05e0\u05d9\u05e7\u05d5\u05d9 \u05db\u05dc \u05d4\u05e8\u05e9\u05d9\u05de\u05d4\n"
    "/city <\u05e2\u05d9\u05e8> \u2014 \u05e9\u05d9\u05e0\u05d5\u05d9 \u05e2\u05d9\u05e8 \u05d1\u05e8\u05d9\u05e8\u05ea \u05de\u05d7\u05d3\u05dc\n"
    "/help \u2014 \u05d4\u05e2\u05d6\u05e8\u05d4 \u05d4\u05d6\u05d5\n\n"
    "/shop \u2014 \u05de\u05e6\u05d1 \u05e7\u05e0\u05d9\u05d5\u05ea (\u05db\u05dc \u05de\u05d5\u05e6\u05e8 \u05d9\u05e1\u05d5\u05de\u05df \u05db\u05e0\u05e7\u05e0\u05d4)\n"
    "/lists \u2014 \u05d4\u05e6\u05d2\u05ea \u05db\u05dc \u05d4\u05e8\u05e9\u05d9\u05de\u05d5\u05ea + \u05de\u05e2\u05d1\u05e8 \u05d1\u05d9\u05e0\u05d9\u05d4\u05df\n"
    "/history \u2014 \u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d9\u05ea \u05e7\u05e0\u05d9\u05d5\u05ea \u05d7\u05d5\u05d3\u05e9 \u05d0\u05d7\u05e8\u05d5\u05df\n\n"
    "\u05d0\u05e4\u05e9\u05e8 \u05d2\u05dd \u05d1\u05e9\u05e4\u05d4 \u05d8\u05d1\u05e2\u05d9\u05ea:\n"
    '"\u05e7\u05e0\u05d9\u05ea\u05d9 \u05d7\u05dc\u05d1" \u2014 \u05e1\u05d9\u05de\u05d5\u05df \u05db\u05e0\u05e7\u05e0\u05d4\n'
    '"\u05de\u05d7\u05e7 \u05dc\u05d7\u05dd" \u2014 \u05de\u05d7\u05d9\u05e7\u05d4 \u05de\u05d4\u05e8\u05e9\u05d9\u05de\u05d4\n'
    '"\u05de\u05d4 \u05d9\u05e9 \u05d1\u05e8\u05e9\u05d9\u05de\u05d4" \u2014 \u05d4\u05e6\u05d2\u05ea \u05d4\u05e8\u05e9\u05d9\u05de\u05d4\n'
    '"\u05de\u05d7\u05d9\u05e8 \u05d7\u05dc\u05d1" \u2014 \u05d1\u05d3\u05d9\u05e7\u05ea \u05de\u05d7\u05d9\u05e8\n\n'
    "\u05d0\u05e4\u05e9\u05e8 \u05d2\u05dd \u05dc\u05e9\u05dc\u05d5\u05d7:\n"
    "\u05d4\u05d5\u05d3\u05e2\u05d4 \u05e7\u05d5\u05dc\u05d9\u05ea \u2014 \u05d0\u05ea\u05de\u05dc\u05dc \u05d5\u05d0\u05d8\u05e4\u05dc \u05d1\u05d1\u05e7\u05e9\u05d4\n"
    "\u05ea\u05de\u05d5\u05e0\u05d4 \u05e9\u05dc \u05de\u05d5\u05e6\u05e8 \u2014 \u05d0\u05d6\u05d4\u05d4 \u05d5\u05d0\u05d5\u05e1\u05d9\u05e3 \u05dc\u05e8\u05e9\u05d9\u05de\u05d4\n\n"
    "\u05ea\u05de\u05d5\u05e0\u05ea \u05e7\u05d1\u05dc\u05d4 \u2014 \u05e1\u05e8\u05d5\u05e7 \u05d5\u05d0\u05d6\u05d4\u05d4 \u05de\u05d4 \u05e7\u05e0\u05d9\u05ea\u05dd"
)

START_TEXT = (
    "\u05e9\u05dc\u05d5\u05dd! \u05d0\u05e0\u05d9 \u05e2\u05d5\u05d6\u05e8 \u05d4\u05e7\u05e0\u05d9\u05d5\u05ea \u05e9\u05dc \u05d4\u05e7\u05d1\u05d5\u05e6\u05d4.\n"
    "\u05e9\u05dc\u05d7\u05d5 \u05e9\u05dd \u05e9\u05dc \u05de\u05d5\u05e6\u05e8 \u05d5\u05d0\u05e0\u05d9 \u05d0\u05d5\u05e1\u05d9\u05e3 \u05d0\u05d5\u05ea\u05d5 \u05dc\u05e8\u05e9\u05d9\u05de\u05d4.\n"
    "\u05d0\u05e4\u05e9\u05e8 \u05d2\u05dd \u05d4\u05d5\u05d3\u05e2\u05d5\u05ea \u05e7\u05d5\u05dc\u05d9\u05d5\u05ea \u05d5\u05ea\u05de\u05d5\u05e0\u05d5\u05ea!\n"
    "\u05dc\u05e2\u05d6\u05e8\u05d4: /help"
)

WELCOME_TEXT = (
    "\u05e9\u05dc\u05d5\u05dd! \u05d0\u05e0\u05d9 \u05e2\u05d5\u05d6\u05e8 \u05d4\u05e7\u05e0\u05d9\u05d5\u05ea \u05e9\u05dc\u05db\u05dd.\n"
    "\u05e9\u05dc\u05d7\u05d5 \u05dc\u05d9 \u05e9\u05dd \u05e9\u05dc \u05de\u05d5\u05e6\u05e8 \u05d5\u05d0\u05d5\u05e1\u05d9\u05e3 \u05d0\u05d5\u05ea\u05d5 \u05dc\u05e8\u05e9\u05d9\u05de\u05d4.\n"
    "/help \u2014 \u05e2\u05d6\u05e8\u05d4 \u05d5\u05e8\u05e9\u05d9\u05de\u05ea \u05e4\u05e7\u05d5\u05d3\u05d5\u05ea\n"
    "/list \u2014 \u05d4\u05e6\u05d2\u05ea \u05d4\u05e8\u05e9\u05d9\u05de\u05d4\n"
    "/lists \u2014 \u05db\u05dc \u05d4\u05e8\u05e9\u05d9\u05de\u05d5\u05ea"
)

# Receipt trigger keywords
RECEIPT_KEYWORDS = {"\u05e7\u05d1\u05dc\u05d4", "\u05d7\u05e9\u05d1\u05d5\u05df", "receipt"}

# Image size limit (5 MB)
IMAGE_MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    payload: dict[str, Any]


def _chat_key(chat_id: int, thread_id: int | None) -> str:
    """Build per-chat key for shopping mode state."""
    return str(chat_id) + (f":{thread_id}" if thread_id else "")


def _fuzzy_match(list_name: str, receipt_name: str) -> bool:
    """Check if a receipt item name fuzzy-matches a list item name.

    Requires ALL significant words (len > 2) from the shorter name to appear
    (as substring) in at least one word of the longer name.
    """
    list_words = set(list_name.split())
    receipt_words = set(receipt_name.split())
    shorter, longer = (list_words, receipt_words) if len(list_words) <= len(receipt_words) else (receipt_words, list_words)
    significant_words = {w for w in shorter if len(w) > 2}
    if not significant_words:
        return list_name == receipt_name
    return all(any(sw in lw or lw in sw for lw in longer) for sw in significant_words)


class TelegramPollingBot:
    def __init__(self, token: str, agent: ShoppingAgent, timeout: int = 30, media_handler: Any = None, shopping_mode: ShoppingModeManager | None = None, super_admin_id: str = ""):
        self.token = token
        self.agent = agent
        self.timeout = timeout
        self.adapter = TelegramBotAdapter()
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.session = requests.Session()
        self.pending_conflicts: TTLDict = TTLDict(ttl_seconds=600, max_size=200)
        self.media_handler = media_handler
        self.shopping_mode = shopping_mode or ShoppingModeManager()
        self.super_admin_id = super_admin_id
        self._bot_id: int | None = None
        self._bot_username: str | None = None

    def _is_super_admin(self, user_id) -> bool:
        """Check if a user is the super admin."""
        if not self.super_admin_id:
            return True  # No admin configured = no restriction
        return str(user_id) == self.super_admin_id

    # -- Helper: build external_chat_id (Issue #8) --

    @staticmethod
    def _build_external_chat_id(chat_id: int, thread_id: int | None) -> str:
        """Build external_chat_id string from chat_id and optional thread_id."""
        return str(chat_id) + (f":{thread_id}" if thread_id else "")

    def get_me(self) -> dict[str, Any]:
        response = self.session.get(f"{self.base_url}/getMe", timeout=15)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getMe failed: {payload}")
        result = payload["result"]
        self._bot_id = result.get("id")
        self._bot_username = result.get("username")
        return result

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

        chat_id = message["chat"]["id"]
        thread_id = message.get("message_thread_id")

        # -- Welcome message: bot added to a new group --
        new_members = message.get("new_chat_members", [])
        if new_members and self._bot_id:
            for member in new_members:
                if member.get("id") == self._bot_id:
                    self._handle_bot_added_to_group(chat_id, thread_id, message)
                    return

        # -- Voice / audio message --
        voice = message.get("voice") or message.get("audio")
        photo_list = message.get("photo")

        if voice and self.media_handler:
            self._handle_voice_message(message, voice)
            return

        if photo_list and self.media_handler:
            self._handle_photo_message(message, photo_list)
            return

        # -- Text message (original flow) --
        if not message.get("text"):
            return

        text = message["text"].strip()

        # Build chat key for shopping mode (per-chat)
        chat_key = _chat_key(chat_id, thread_id)

        # Check for shopping mode activation/deactivation phrases (exact match only)
        if text in ACTIVATION_PHRASES:
            self.shopping_mode.activate(chat_key)
            self.send_message(chat_id=chat_id, text=ACTIVATE_MSG, message_thread_id=thread_id)
            return
        if text in DEACTIVATION_PHRASES:
            self.shopping_mode.deactivate(chat_key)
            self.send_message(chat_id=chat_id, text=DEACTIVATE_MSG, message_thread_id=thread_id)
            return

        context = self.adapter.normalize_message(update).to_message_context()

        # Handle slash commands directly (no LLM round-trip)
        slash_response = self._handle_slash_command(text, context, chat_key=chat_key, chat_id=chat_id, thread_id=thread_id)
        if slash_response is not None:
            if slash_response:
                self.send_message(
                    chat_id=chat_id,
                    text=slash_response,
                    message_thread_id=thread_id,
                )
            return

        # Shopping mode: rewrite text as purchase intent
        if self.shopping_mode.is_active(chat_key):
            self.shopping_mode.touch(chat_key)
            text_rewritten = f"\u05e7\u05e0\u05d9\u05ea\u05d9 {text}"
            context = context.with_text(text_rewritten)

        # Regular message -- send to agent
        self._send_to_agent(context, message)

    def _handle_bot_added_to_group(self, chat_id: int, thread_id: int | None, message: dict) -> None:
        """Handle bot being added to a new group: send welcome and register chat."""
        logger.info("Bot added to group chat_id=%s", chat_id)

        # Register the group immediately
        try:
            external_chat_id = self._build_external_chat_id(chat_id, thread_id)
            title = message.get("chat", {}).get("title")
            store = self.agent.router.store
            store.ensure_chat(
                platform="telegram",
                external_chat_id=external_chat_id,
                title=title,
                default_city=self.agent.router.default_city,
            )
        except Exception as exc:
            logger.warning("Failed to register group on bot join: %s", exc)

        self.send_message(chat_id=chat_id, text=WELCOME_TEXT, message_thread_id=thread_id)

    def _handle_voice_message(self, message: dict, voice: dict) -> None:
        """Handle incoming voice/audio message."""
        chat_id = message["chat"]["id"]
        thread_id = message.get("message_thread_id")
        file_id = voice.get("file_id")

        if not file_id:
            logger.warning("Voice message from user %s has no file_id", message.get("from", {}).get("id"))
            return

        logger.info("Processing voice message from user %s", message.get("from", {}).get("id"))

        transcribed = self.media_handler.process_voice_message(file_id)
        if not transcribed:
            self.send_message(
                chat_id=chat_id,
                text="\u05dc\u05d0 \u05d4\u05e6\u05dc\u05d7\u05ea\u05d9 \u05dc\u05ea\u05de\u05dc\u05dc \u05d0\u05ea \u05d4\u05d4\u05d5\u05d3\u05e2\u05d4 \u05d4\u05e7\u05d5\u05dc\u05d9\u05ea",
                message_thread_id=thread_id,
            )
            return

        # Shopping mode: rewrite voice transcription as purchase
        chat_key = _chat_key(chat_id, thread_id)
        if self.shopping_mode.is_active(chat_key):
            self.shopping_mode.touch(chat_key)
            transcribed = f"\u05e7\u05e0\u05d9\u05ea\u05d9 {transcribed}"

        # Build context with transcribed text
        context = self.adapter.normalize_media_message(message, text_override=transcribed).to_message_context()
        logger.info("Voice transcribed: %s", transcribed[:100])

        self._send_to_agent(context, message)

    def _handle_photo_message(self, message: dict, photo_list: list) -> None:
        """Handle incoming photo message."""
        chat_id = message["chat"]["id"]
        thread_id = message.get("message_thread_id")

        # Get highest resolution photo (last in the array)
        best_photo = photo_list[-1]
        file_id = best_photo.get("file_id")
        if not file_id:
            logger.warning("Photo message from user %s has no file_id", message.get("from", {}).get("id"))
            return

        caption = message.get("caption", "").strip() or None

        logger.info("Processing photo message from user %s (caption: %s)",
                     message.get("from", {}).get("id"), caption[:50] if caption else "none")

        # Check if this is a receipt photo (Issue #10: shopping mode without caption = receipt)
        chat_key = _chat_key(chat_id, thread_id)
        is_receipt = self._is_receipt_photo(caption, chat_key)
        if is_receipt:
            self._handle_receipt_photo(message, file_id, chat_id, thread_id)
            return

        result_text = self.media_handler.process_photo_message(file_id, caption=caption)
        if not result_text:
            self.send_message(
                chat_id=chat_id,
                text="\u05dc\u05d0 \u05d4\u05e6\u05dc\u05d7\u05ea\u05d9 \u05dc\u05d6\u05d4\u05d5\u05ea \u05d0\u05ea \u05d4\u05de\u05d5\u05e6\u05e8 \u05d1\u05ea\u05de\u05d5\u05e0\u05d4",
                message_thread_id=thread_id,
            )
            return

        # Handle barcode detection
        if result_text.startswith("BARCODE:"):
            barcode = result_text[len("BARCODE:"):].strip()
            if not barcode:
                self.send_message(chat_id=chat_id, text="\u05dc\u05d0 \u05d4\u05e6\u05dc\u05d7\u05ea\u05d9 \u05dc\u05e7\u05e8\u05d5\u05d0 \u05d0\u05ea \u05d4\u05d1\u05e8\u05e7\u05d5\u05d3 \u2014 \u05e0\u05e1\u05d4 \u05dc\u05e6\u05dc\u05dd \u05e9\u05d5\u05d1", message_thread_id=thread_id)
                return
            price_db = getattr(self.agent.router, "price_db", None)
            if price_db:
                resolved = price_db.lookup_barcode(barcode)
                if resolved:
                    result_text = resolved
                    logger.info("Barcode %s resolved to: %s", barcode, resolved)
                else:
                    self.send_message(chat_id=chat_id, text=f"\u05dc\u05d0 \u05de\u05e6\u05d0\u05ea\u05d9 \u05d0\u05ea \u05d4\u05de\u05d5\u05e6\u05e8 \u05d1\u05d1\u05e8\u05e7\u05d5\u05d3 {barcode}", message_thread_id=thread_id)
                    return
            else:
                self.send_message(chat_id=chat_id, text=f"\u05d1\u05e8\u05e7\u05d5\u05d3: {barcode} (\u05d7\u05d9\u05e4\u05d5\u05e9 \u05d1\u05e8\u05e7\u05d5\u05d3 \u05dc\u05d0 \u05d6\u05de\u05d9\u05df)", message_thread_id=thread_id)
                return

        # Shopping mode: rewrite as purchase
        if self.shopping_mode.is_active(chat_key):
            self.shopping_mode.touch(chat_key)
            result_text = f"\u05e7\u05e0\u05d9\u05ea\u05d9 {result_text}"

        # Build context with identified product text
        context = self.adapter.normalize_media_message(message, text_override=result_text).to_message_context()
        logger.info("Photo identified: %s", result_text[:100])

        self._send_to_agent(context, message)

    def _is_receipt_photo(self, caption: str | None, chat_key: str) -> bool:
        """Check if a photo should be treated as a receipt."""
        if caption:
            caption_lower = caption.lower().strip()
            for kw in RECEIPT_KEYWORDS:
                if kw in caption_lower:
                    return True
        # Issue #10: In shopping mode, photo without caption is also treated as receipt
        if not caption and self.shopping_mode.is_active(chat_key):
            return True
        return False

    def _handle_receipt_photo(self, message: dict, file_id: str, chat_id: int, thread_id: int | None) -> None:
        """Handle a receipt photo: parse items, match against active list, report."""
        self.send_message(chat_id=chat_id, text="\u05e1\u05d5\u05e8\u05e7 \u05e7\u05d1\u05dc\u05d4...", message_thread_id=thread_id)

        # Download photo
        image_bytes = self.media_handler.download_photo(file_id)
        if not image_bytes:
            self.send_message(chat_id=chat_id, text="\u05dc\u05d0 \u05d4\u05e6\u05dc\u05d7\u05ea\u05d9 \u05dc\u05d4\u05d5\u05e8\u05d9\u05d3 \u05d0\u05ea \u05d4\u05ea\u05de\u05d5\u05e0\u05d4", message_thread_id=thread_id)
            return

        # Issue #5: Image size limit
        if len(image_bytes) > IMAGE_MAX_BYTES:
            self.send_message(
                chat_id=chat_id,
                text="\u05d4\u05ea\u05de\u05d5\u05e0\u05d4 \u05d2\u05d3\u05d5\u05dc\u05d4 \u05de\u05d3\u05d9, \u05e0\u05e1\u05d4 \u05dc\u05e9\u05dc\u05d5\u05d7 \u05ea\u05de\u05d5\u05e0\u05d4 \u05e7\u05d8\u05e0\u05d4 \u05d9\u05d5\u05ea\u05e8",
                message_thread_id=thread_id,
            )
            return

        # Parse receipt via Gemini
        receipt_items = self.media_handler.parse_receipt(image_bytes)
        if not receipt_items:
            self.send_message(chat_id=chat_id, text="\u05dc\u05d0 \u05d4\u05e6\u05dc\u05d7\u05ea\u05d9 \u05dc\u05e7\u05e8\u05d5\u05d0 \u05d0\u05ea \u05d4\u05e7\u05d1\u05dc\u05d4", message_thread_id=thread_id)
            return

        # Get store/chain info from first item
        store_name = ""
        chain_name = ""
        for ri in receipt_items:
            if ri.get("store_name"):
                store_name = ri["store_name"]
            if ri.get("chain_name"):
                chain_name = ri["chain_name"]
            if store_name and chain_name:
                break

        # Issue #7: Extract user_id from message
        user_id = str(message.get("from", {}).get("id", 0))
        user_name = message.get("from", {}).get("first_name", "")

        # Match against active shopping list (Issue #8: use helper)
        from src.app.router import MessageContext
        context = MessageContext(
            platform="telegram",
            external_chat_id=self._build_external_chat_id(chat_id, thread_id),
            user_id=user_id,
            text="",
        )

        # Issue #9: Wrap store operations in try/except
        try:
            store = self.agent.router.store
            chat = store.ensure_chat(
                platform=context.platform,
                external_chat_id=context.external_chat_id,
                title=None,
                default_city=self.agent.router.default_city,
            )
            shopping_list = store.ensure_active_list(chat_id=chat.id)
            active_items = store.list_active_items(shopping_list.id)
        except Exception as exc:
            logger.exception("Failed to load shopping list for receipt: %s", exc)
            self.send_message(chat_id=chat_id, text="\u05e9\u05d2\u05d9\u05d0\u05d4 \u05d1\u05d8\u05e2\u05d9\u05e0\u05ea \u05d4\u05e8\u05e9\u05d9\u05de\u05d4, \u05e0\u05e1\u05d4 \u05e9\u05d5\u05d1", message_thread_id=thread_id)
            return

        matched = []
        unmatched_receipt = []
        total_receipt = 0.0

        for ri in receipt_items:
            # Issue #6: Per-item error handling
            try:
                ri_name = ri["name"].strip().lower()
                ri_price = ri.get("price", 0)
                ri_qty = ri.get("quantity", 1)
                ri_sku = ri.get("sku", "")
                total_receipt += ri_price

                # Issue #3: Use improved fuzzy matching
                found = None
                for item in active_items:
                    item_name_lower = item.normalized_name.lower()
                    if _fuzzy_match(item_name_lower, ri_name):
                        found = item
                        break

                if found:
                    # Issue #4: Store purchase prices via update_item_purchase
                    try:
                        store.update_item_purchase(
                            item_id=found.id,
                            purchase_price=ri_price,
                            store_name=store_name,
                            chain_name=chain_name,
                            sku=ri_sku if ri_sku else None,
                            purchased_by_user_id=user_id,
                            purchased_by_name=user_name,
                        )
                    except Exception as exc:
                        logger.warning("update_item_purchase failed for item %s, falling back: %s", found.id, exc)
                        store.update_item_status(
                            list_id=shopping_list.id,
                            query=found.normalized_name,
                            status="bought",
                            acting_user_id=context.user_id,
                        )
                    try:
                        store.record_event(
                            chat_id=chat.id,
                            user_id=context.user_id,
                            event_type="receipt_match",
                            payload={
                                "item_id": found.id,
                                "receipt_name": ri["name"],
                                "price": ri_price,
                                "quantity": ri_qty,
                                "store_name": store_name,
                                "chain_name": chain_name,
                            },
                        )
                    except Exception as exc:
                        logger.warning("record_event failed for receipt match: %s", exc)
                    matched.append((found.normalized_name, ri["name"], ri_price))
                    # Remove from active_items so we don't match twice
                    active_items = [i for i in active_items if i.id != found.id]
                else:
                    unmatched_receipt.append((ri["name"], ri_price))
            except Exception as exc:
                logger.warning("Failed to process receipt item %s: %s", ri.get("name", "?"), exc)
                continue

        # Build response
        lines = []
        header = "\u05e7\u05d1\u05dc\u05d4 \u05e0\u05e7\u05dc\u05d8\u05d4"
        if store_name:
            header += f" \u2014 {store_name}"
        elif chain_name:
            header += f" \u2014 {chain_name}"
        lines.append(header)
        lines.append(f'{len(receipt_items)} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd, \u05e1\u05d4"\u05db \u20aa{total_receipt:.2f}')
        lines.append("")

        if matched:
            lines.append(f"\u05e1\u05d5\u05de\u05e0\u05d5 \u05db\u05e0\u05e7\u05e0\u05d5 ({len(matched)}):")
            for list_name, receipt_name, price in matched:
                lines.append(f"  \u2705 {list_name} \u2014 \u20aa{price:.2f}")

        try:
            remaining = store.list_active_items(shopping_list.id)
        except Exception as exc:
            logger.warning("Failed to list remaining items: %s", exc)
            remaining = []

        if remaining:
            lines.append("")
            lines.append(f"\u05e0\u05d5\u05ea\u05e8\u05d5 \u05d1\u05e8\u05e9\u05d9\u05de\u05d4 ({len(remaining)}):")
            for item in remaining:
                qty_str = ""
                if item.quantity_value:
                    q = int(item.quantity_value) if item.quantity_value == int(item.quantity_value) else item.quantity_value
                    qty_str = f" x{q}"
                lines.append(f"  \u25aa {item.normalized_name}{qty_str}")

        if unmatched_receipt:
            lines.append("")
            lines.append(f"\u05dc\u05d0 \u05d1\u05e8\u05e9\u05d9\u05de\u05d4 ({len(unmatched_receipt)}):")
            for name, price in unmatched_receipt[:10]:
                lines.append(f"  \u2022 {name} \u2014 \u20aa{price:.2f}")
            if len(unmatched_receipt) > 10:
                lines.append(f"  ... \u05d5\u05e2\u05d5\u05d3 {len(unmatched_receipt) - 10}")

        self.send_message(chat_id=chat_id, text="\n".join(lines), message_thread_id=thread_id)

    def _send_to_agent(self, context: Any, message: dict) -> None:
        """Send context to agent and handle response (shared by text, voice, photo)."""
        chat_id = message["chat"]["id"]
        thread_id = message.get("message_thread_id")

        response_text = self.agent.handle_message(context)
        if not response_text:
            return

        # Check for pending duplicate conflicts
        conflict = self.agent.pending_conflicts.pop(context.external_chat_id, None)
        if conflict:
            self._send_duplicate_keyboard(
                chat_id=chat_id,
                text=response_text,
                conflict=conflict,
                message_thread_id=thread_id,
            )
            return

        # Check for pending price disambiguation
        price_result = self.agent.pending_price_choices.pop(context.external_chat_id, None)
        if price_result and price_result.needs_disambiguation:
            self._send_price_picker(
                chat_id=chat_id,
                text=response_text,
                choices=price_result.choices,
                query=price_result.query,
                message_thread_id=thread_id,
            )
            return

        self.send_message(
            chat_id=chat_id,
            text=response_text,
            message_thread_id=thread_id,
        )

    def _handle_slash_command(self, text: str, context: Any, chat_key: str, chat_id: int = 0, thread_id: int | None = None) -> str | None:
        if not text.startswith("/"):
            return None

        # Strip bot username if present (e.g., /list@my_shopping_bot)
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
                return f"\u05d4\u05e2\u05d9\u05e8 \u05d4\u05e0\u05d5\u05db\u05d7\u05d9\u05ea: {city}\n\u05dc\u05e9\u05d9\u05e0\u05d5\u05d9: /city <\u05e9\u05dd \u05e2\u05d9\u05e8>"
            return self.agent.router.handle_semantic_action(context, action="city", city=args)
        elif command == "/shop":
            if self.shopping_mode.toggle(chat_key):
                return ACTIVATE_MSG
            return DEACTIVATE_MSG
        elif command == "/start":
            return START_TEXT
        elif command == "/lists":
            self._send_lists_keyboard(chat_id, thread_id, context)
            return ""  # Already sent inline keyboard
        elif command == "/history":
            return self._get_purchase_history(context)

        return None  # Unknown slash command -- let agent handle

    # -- /lists with inline keyboard --

    def _send_lists_keyboard(self, chat_id: int, thread_id: int | None, context: Any) -> None:
        """Show all lists for this chat as inline keyboard buttons."""
        store = self.agent.router.store
        chat = store.ensure_chat(
            platform=context.platform,
            external_chat_id=context.external_chat_id,
            title=context.title,
            default_city=self.agent.router.default_city,
        )

        # Get all lists for this chat
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT id, name, is_active FROM shopping_lists WHERE chat_id = ? ORDER BY name",
                (chat.id,),
            ).fetchall()

        if not rows:
            self.send_message(chat_id=chat_id, text="\u05d0\u05d9\u05df \u05e8\u05e9\u05d9\u05de\u05d5\u05ea \u05e2\u05d3\u05d9\u05d9\u05df", message_thread_id=thread_id)
            return

        # Get active list to show current selection
        active_list = store.ensure_active_list(chat_id=chat.id)

        # Issue #2: Use list_id (integer) instead of list_name to avoid callback_data overflow
        buttons = []
        for row in rows:
            list_name = row["name"]
            list_id = row["id"]
            # Count active items
            item_count = len(store.list_active_items(list_id))
            marker = "\u25c9 " if list_id == active_list.id else ""
            label = f"{marker}{list_name} ({item_count})"
            buttons.append([{"text": label, "callback_data": f"switch_list:{list_id}"}])

        text = "\u05d4\u05e8\u05e9\u05d9\u05de\u05d5\u05ea \u05e9\u05dc\u05da \u2014 \u05dc\u05d7\u05e5 \u05dc\u05de\u05e2\u05d1\u05e8:"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": json.dumps({"inline_keyboard": buttons}),
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id

        try:
            response = self.session.post(f"{self.base_url}/sendMessage", json=payload, timeout=15)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to send lists keyboard: %s", exc)
            self.send_message(chat_id=chat_id, text=text, message_thread_id=thread_id)

    # -- /history --

    def _get_purchase_history(self, context: Any) -> str:
        """Get purchase history for last 30 days."""
        store = self.agent.router.store
        chat = store.ensure_chat(
            platform=context.platform,
            external_chat_id=context.external_chat_id,
            title=context.title,
            default_city=self.agent.router.default_city,
        )

        with store.connect() as conn:
            rows = conn.execute(
                """
                SELECT li.normalized_name, li.quantity_value, li.quantity_unit,
                       li.updated_at, li.purchased_by_user_id,
                       sl.name as list_name
                FROM list_items li
                JOIN shopping_lists sl ON li.list_id = sl.id
                WHERE sl.chat_id = ? AND li.status = 'bought'
                  AND li.updated_at >= datetime('now', '-30 days')
                ORDER BY li.updated_at DESC
                LIMIT 50
                """,
                (chat.id,),
            ).fetchall()

        if not rows:
            return "\u05d0\u05d9\u05df \u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d9\u05ea \u05e7\u05e0\u05d9\u05d5\u05ea \u05d1\u05d7\u05d5\u05d3\u05e9 \u05d4\u05d0\u05d7\u05e8\u05d5\u05df"

        lines = ["\u05d4\u05d9\u05e1\u05d8\u05d5\u05e8\u05d9\u05d9\u05ea \u05e7\u05e0\u05d9\u05d5\u05ea (30 \u05d9\u05d5\u05dd \u05d0\u05d7\u05e8\u05d5\u05e0\u05d9\u05dd):"]
        lines.append("")
        current_date = None
        for row in rows:
            # Parse date for grouping
            updated = row["updated_at"] or ""
            date_str = updated[:10] if len(updated) >= 10 else updated
            if date_str != current_date:
                current_date = date_str
                lines.append(f"\u25ab {date_str}")

            name = row["normalized_name"]
            qty = row["quantity_value"]
            qty_str = ""
            if qty:
                q = int(qty) if qty == int(qty) else qty
                unit = row["quantity_unit"] or ""
                qty_str = f" x{q}{unit}"
            list_name = row["list_name"]
            list_tag = f" [{list_name}]" if list_name != "main" else ""
            lines.append(f"  \u2713 {name}{qty_str}{list_tag}")

        lines.append(f'\n\u05e1\u05d4"\u05db {len(rows)} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd')
        return "\n".join(lines)


    def _send_duplicate_keyboard(
        self, chat_id: int, text: str, conflict: Any, message_thread_id: int | None = None,
    ) -> None:

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
            buttons.append([{"text": f'\u05d0\u05d7\u05d3 (\u05e1\u05d4"\u05db {merged_display})', "callback_data": f"dup:merge:{conflict_id}"}])
        elif nq:
            buttons.append([{"text": f"\u05d0\u05d7\u05d3 ({new_display})", "callback_data": f"dup:merge:{conflict_id}"}])
        else:
            buttons.append([{"text": "\u05d0\u05d7\u05d3", "callback_data": f"dup:merge:{conflict_id}"}])

        if nq:
            buttons.append([{"text": f"\u05e2\u05d3\u05db\u05df \u05dc-{new_display}", "callback_data": f"dup:update:{conflict_id}"}])
        buttons.append([{"text": "\u05d4\u05d5\u05e1\u05e3 \u05d1\u05e0\u05e4\u05e8\u05d3", "callback_data": f"dup:add:{conflict_id}"}])
        buttons.append([{"text": "\u05d1\u05d8\u05dc", "callback_data": f"dup:cancel:{conflict_id}"}])

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
            logger.warning("Failed to send duplicate keyboard: %s", exc)
            # Fallback: send plain text without keyboard
            self.send_message(chat_id=payload["chat_id"], text=payload["text"],
                              message_thread_id=payload.get("message_thread_id"))

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
            self.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id)

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback["id"]
        try:
            self._process_callback(callback)
        except Exception as exc:
            logger.exception("Callback handling failed: %s", exc)
            self._answer_callback(callback_id, "\u05e9\u05d2\u05d9\u05d0\u05d4 \u2014 \u05e0\u05e1\u05d4 \u05e9\u05d5\u05d1")

    def _process_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback["id"]
        data = callback.get("data", "")
        message = callback.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        thread_id = message.get("message_thread_id")

        # -- switch_list callback (Issue #2: now uses list_id) --
        if data.startswith("switch_list:"):
            raw_id = data.split(":", 1)[1]
            try:
                list_id = int(raw_id)
            except (ValueError, TypeError):
                self._answer_callback(callback_id, "\u05e9\u05d2\u05d9\u05d0\u05d4")
                return
            user_id = str(callback.get("from", {}).get("id", ""))
            self._handle_switch_list(callback_id, chat_id, message_id, thread_id, list_id, user_id)
            return

        # -- create_and_move callback (Issue #1: now uses short UUID from pending_conflicts) --
        if data.startswith("create_move:"):
            move_id = data.split(":", 1)[1]
            user_id = str(callback.get("from", {}).get("id", ""))
            self._handle_create_and_move(callback_id, callback, move_id, user_id)
            return

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

        # Build a fake context for the router (Issue #8: use helper)
        from src.app.router import MessageContext
        context = MessageContext(
            platform="telegram",
            external_chat_id=self._build_external_chat_id(chat_id, thread_id),
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

    def _handle_switch_list(self, callback_id: str, chat_id: int, message_id: int, thread_id: int | None, list_id: int, user_id: str) -> None:
        """Handle switch_list:{list_id} callback -- Issue #2: uses list_id instead of name."""
        from src.app.router import MessageContext
        context = MessageContext(
            platform="telegram",
            external_chat_id=self._build_external_chat_id(chat_id, thread_id),
            user_id=user_id,
            text="",
        )
        store = self.agent.router.store
        chat = store.ensure_chat(
            platform=context.platform,
            external_chat_id=context.external_chat_id,
            title=None,
            default_city=self.agent.router.default_city,
        )

        # Look up list by ID
        with store.connect() as conn:
            row = conn.execute(
                "SELECT id, name FROM shopping_lists WHERE id = ? AND chat_id = ?",
                (list_id, chat.id),
            ).fetchone()

        if not row:
            self._answer_callback(callback_id, "\u05d4\u05e8\u05e9\u05d9\u05de\u05d4 \u05dc\u05d0 \u05e0\u05de\u05e6\u05d0\u05d4")
            return

        list_name = row["name"]

        # Switch to the requested list
        store.set_active_list(chat_id=chat.id, list_id=list_id)
        shopping_list = store.ensure_active_list(chat_id=chat.id, name=list_name)
        items = store.list_active_items(shopping_list.id)

        item_count = len(items)
        self._answer_callback(callback_id, f"\u05e8\u05e9\u05d9\u05de\u05ea {list_name} ({item_count} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd)")

        # Show the list contents
        if items:
            lines = [f"\u05e8\u05e9\u05d9\u05de\u05ea {list_name}:"]
            for item in items:
                qty_str = ""
                if item.quantity_value:
                    q = int(item.quantity_value) if item.quantity_value == int(item.quantity_value) else item.quantity_value
                    qty_str = f" x{q}"
                lines.append(f"  \u25aa {item.normalized_name}{qty_str}")
            result_text = "\n".join(lines)
        else:
            result_text = f"\u05e8\u05e9\u05d9\u05de\u05ea {list_name} \u05e8\u05d9\u05e7\u05d4"

        self._edit_message(chat_id, message_id, result_text)

    def _handle_create_and_move(self, callback_id: str, callback: dict, move_id: str, user_id: str) -> None:
        """Handle create_move:{short_id} callback -- Issue #1: payload from pending_conflicts."""
        message = callback.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        thread_id = message.get("message_thread_id")

        # Look up stored payload
        move_data = self.pending_conflicts.pop(move_id, None)
        if move_data is None:
            self._answer_callback(callback_id, "\u05d4\u05e4\u05e2\u05d5\u05dc\u05d4 \u05e4\u05d2\u05d4 \u2014 \u05e0\u05e1\u05d4 \u05e9\u05d5\u05d1")
            return

        target_list_name = move_data["target_list"]
        item_names = move_data["items"]

        from src.app.router import MessageContext
        context = MessageContext(
            platform="telegram",
            external_chat_id=self._build_external_chat_id(chat_id, thread_id),
            user_id=user_id,
            text="",
        )
        store = self.agent.router.store
        chat = store.ensure_chat(
            platform=context.platform,
            external_chat_id=context.external_chat_id,
            title=None,
            default_city=self.agent.router.default_city,
        )

        # Create the target list
        target_list = store.ensure_active_list(chat_id=chat.id, name=target_list_name)

        # Get current active list items and move matching ones
        current_list = store.ensure_active_list(chat_id=chat.id)
        moved = 0
        for item_name in item_names:
            current_items = store.list_active_items(current_list.id)
            for item in current_items:
                if item.normalized_name.lower() == item_name.lower():
                    # Add to target list
                    store.add_item(
                        list_id=target_list.id,
                        raw_text=item.raw_text,
                        normalized_name=item.normalized_name,
                        quantity_value=item.quantity_value,
                        quantity_unit=item.quantity_unit,
                        note=item.note,
                        category=item.category,
                        added_by_user_id=item.added_by_user_id,
                    )
                    # Remove from current list
                    store.update_item_status(
                        list_id=current_list.id,
                        query=item.normalized_name,
                        status="deleted",
                        acting_user_id=None,
                    )
                    moved += 1
                    break

        response_text = f"\u05e0\u05d5\u05e6\u05e8\u05d4 \u05e8\u05e9\u05d9\u05de\u05ea {target_list_name} \u05d5\u05d4\u05d5\u05e2\u05d1\u05e8\u05d5 {moved} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd"
        self._answer_callback(callback_id, response_text[:200])

        original_text = message.get("text", "")
        self._edit_message(chat_id, message_id, original_text + f"\n\n\u2705 {response_text}")

    def send_create_and_move_keyboard(
        self, chat_id: int, text: str, target_list: str, item_names: list[str],
        message_thread_id: int | None = None,
    ) -> None:
        """Public method for agent to send a create-and-move keyboard.

        Issue #1: Stores payload in pending_conflicts with a short UUID key
        to stay within Telegram's 64-byte callback_data limit.
        """
        move_id = uuid.uuid4().hex[:8]
        self.pending_conflicts[move_id] = {
            "target_list": target_list,
            "items": item_names,
        }

        buttons = [[{
            "text": f"\u05e6\u05d5\u05e8 {target_list} \u05d5\u05d4\u05e2\u05d1\u05e8",
            "callback_data": f"create_move:{move_id}",
        }]]

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
            logger.warning("Failed to send create_and_move keyboard: %s", exc)
            self.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id)

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
