from __future__ import annotations

import re
from dataclasses import dataclass

COMMAND_PREFIXES = {
    "show": ("?", "תראה"),
    "done": ("קניתי",),
    "delete": ("מחק",),
    "price": ("מחיר", "כמה עולה"),
    "city": ("עיר",),
    "help": ("עזרה",),
}

QUANTITY_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s+(.+?)\s*$")
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
META_MARKERS = (
    "sammie",
    "shopping assistant bot",
    "you are joining",
    "pilot",
    "bug tracking",
)


@dataclass
class ParsedMessage:
    intent: str
    value: str
    quantity: float | None = None


def normalize_text(text: str) -> str:
    text = text.replace("׳", "'").replace('״', '"')
    return re.sub(r"\s+", " ", text).strip()


def _should_ignore_as_item(text: str) -> bool:
    stripped = text.strip()
    normalized = stripped.lower()

    if not stripped:
        return True
    if stripped.startswith("/"):
        return True
    if "\n" in text or len(stripped) > 80:
        return True
    if URL_RE.search(stripped):
        return True
    if any(marker in normalized for marker in META_MARKERS):
        return True
    if stripped.endswith("?") and not stripped.startswith("מחיר "):
        return True
    if len(stripped.split()) > 6:
        return True
    return False


def parse_message(text: str) -> ParsedMessage:
    normalized = normalize_text(text)

    for intent, prefixes in COMMAND_PREFIXES.items():
        for prefix in prefixes:
            if normalized == prefix or normalized.startswith(prefix + " "):
                value = normalized[len(prefix):].strip()
                return ParsedMessage(intent=intent, value=value)

    quantity_match = QUANTITY_RE.match(normalized)
    if quantity_match:
        quantity = float(quantity_match.group(1).replace(",", "."))
        return ParsedMessage(intent="add", value=quantity_match.group(2), quantity=quantity)

    if _should_ignore_as_item(text):
        return ParsedMessage(intent="ignore", value="")

    return ParsedMessage(intent="add", value=normalized)
