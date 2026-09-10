"""Shopping mode manager — per-chat in-memory state for "at the store" mode."""
from __future__ import annotations

import time

# Hebrew activation phrases
ACTIVATION_PHRASES = ("אני בסופר", "מתחיל קניות", "מצב קניות")
DEACTIVATION_PHRASES = ("סיימתי קניות", "סיום קניות")

ACTIVATE_MSG = "מצב קניות פעיל — כל מוצר שתשלח יסומן כנקנה. יכבה אוטומטית אחרי 45 דקות"
DEACTIVATE_MSG = "מצב קניות כבוי — חזרנו למצב רגיל"


class ShoppingModeManager:
    TIMEOUT_SECONDS = 45 * 60  # 45 minutes
    MAX_ENTRIES = 1000

    def __init__(self) -> None:
        self._active: dict[str, float] = {}  # chat_id -> last_activity_timestamp

    def is_active(self, chat_id: str) -> bool:
        ts = self._active.get(chat_id)
        if ts is None:
            return False
        if time.time() - ts > self.TIMEOUT_SECONDS:
            del self._active[chat_id]
            return False
        return True

    def activate(self, chat_id: str) -> None:
        self._cleanup_expired()
        # LRU eviction: remove oldest entry when at capacity
        if len(self._active) >= self.MAX_ENTRIES and chat_id not in self._active:
            oldest = min(self._active, key=self._active.get)  # type: ignore[arg-type]
            del self._active[oldest]
        self._active[chat_id] = time.time()

    def _cleanup_expired(self) -> None:
        """Remove expired entries to prevent unbounded dict growth."""
        now = time.time()
        expired = [k for k, ts in self._active.items() if now - ts > self.TIMEOUT_SECONDS]
        for k in expired:
            del self._active[k]

    def deactivate(self, chat_id: str) -> None:
        self._active.pop(chat_id, None)

    def touch(self, chat_id: str) -> None:
        if chat_id in self._active:
            self._active[chat_id] = time.time()

    def toggle(self, chat_id: str) -> bool:
        """Toggle shopping mode. Returns True if now active, False if deactivated."""
        if self.is_active(chat_id):
            self.deactivate(chat_id)
            return False
        self.activate(chat_id)
        return True
