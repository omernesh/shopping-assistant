from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

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
        logger.info("Starting Telegram polling loop")

        while True:
            try:
                updates = self.get_updates(offset=offset)
                for update in updates:
                    offset = update.update_id + 1
                    self.handle_update(update.payload)
            except requests.RequestException as exc:
                logger.exception("Telegram polling request failed: %s", exc)
                time.sleep(3)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected polling failure: %s", exc)
                time.sleep(3)

    def get_updates(self, offset: int | None = None) -> list[TelegramUpdate]:
        response = self.session.get(
            f"{self.base_url}/getUpdates",
            params={
                "timeout": self.timeout,
                "offset": offset,
                "allowed_updates": '["message"]',
            },
            timeout=self.timeout + 10,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {payload}")
        return [TelegramUpdate(update_id=item["update_id"], payload=item) for item in payload.get("result", [])]

    def handle_update(self, update: dict[str, Any]) -> None:
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

    def send_message(self, chat_id: int, text: str, message_thread_id: int | None = None) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id

        response = self.session.post(f"{self.base_url}/sendMessage", json=payload, timeout=15)
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {body}")
