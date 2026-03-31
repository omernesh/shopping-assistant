from __future__ import annotations

from dataclasses import dataclass

from src.app.router import MessageContext


@dataclass(frozen=True)
class TelegramMessageContext:
    chat_id: int
    thread_id: int | None
    user_id: int
    text: str
    title: str | None = None
    user_name: str | None = None

    @property
    def external_chat_id(self) -> str:
        if self.thread_id is None:
            return str(self.chat_id)
        return f"{self.chat_id}:{self.thread_id}"

    def to_message_context(self) -> MessageContext:
        return MessageContext(
            platform="telegram",
            external_chat_id=self.external_chat_id,
            user_id=str(self.user_id),
            text=self.text,
            title=self.title,
            user_name=self.user_name,
        )


class TelegramBotAdapter:
    """Telegram-first adapter stub for the pilot.

    Phase 0/1 goal is to keep the Telegram boundary thin and move all behavior into the router.
    """

    def normalize_message(self, payload: dict) -> TelegramMessageContext:
        message = payload.get("message", payload)
        chat = message["chat"]
        from_user = message.get("from", {})
        # Build display name from first_name + last_name
        first = from_user.get("first_name", "")
        last = from_user.get("last_name", "")
        user_name = f"{first} {last}".strip() or from_user.get("username") or None
        return TelegramMessageContext(
            chat_id=chat["id"],
            thread_id=message.get("message_thread_id"),
            user_id=message["from"]["id"],
            text=message.get("text", "").strip(),
            title=chat.get("title"),
            user_name=user_name,
        )

    def normalize_media_message(self, message: dict, text_override: str) -> TelegramMessageContext:
        """Normalize a media message (voice/photo), substituting detected text."""
        chat = message["chat"]
        from_user = message.get("from", {})
        first = from_user.get("first_name", "")
        last = from_user.get("last_name", "")
        user_name = f"{first} {last}".strip() or from_user.get("username") or None
        return TelegramMessageContext(
            chat_id=chat["id"],
            thread_id=message.get("message_thread_id"),
            user_id=message["from"]["id"],
            text=text_override,
            title=chat.get("title"),
            user_name=user_name,
        )
