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
UNIT_WORDS_RE = re.compile(
    r"^\s*(חצי|קילו|ליטר|גרם|ק״ג|ק\"ג)\s+(.+?)\s*$"
)

UNIT_MAP = {
    "קילו": ("קילו", 1.0),
    "ק״ג": ("קילו", 1.0),
    'ק"ג': ("קילו", 1.0),
    "ליטר": ("ליטר", 1.0),
    "גרם": ("גרם", 1.0),
    "חצי": ("חצי", 0.5),
}
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
META_MARKERS = (
    "shopping assistant bot",
    "hey bot",
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
    return len(stripped.split()) > 6


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
        quantity = max(0.01, min(quantity, 999))  # Clamp to reasonable range
        return ParsedMessage(intent="add", value=quantity_match.group(2), quantity=quantity)

    unit_match = UNIT_WORDS_RE.match(normalized)
    if unit_match:
        unit_word = unit_match.group(1)
        unit_info = UNIT_MAP.get(unit_word)
        if unit_info:
            unit_name, qty = unit_info
            qty = max(0.01, min(qty, 999))
            item_name = unit_match.group(2)
            return ParsedMessage(intent="add", value=f"{unit_name} {item_name}", quantity=qty)

    if _should_ignore_as_item(text):
        return ParsedMessage(intent="ignore", value="")

    return ParsedMessage(intent="add", value=normalized)
