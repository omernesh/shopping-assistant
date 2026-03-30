from __future__ import annotations

from dataclasses import dataclass

from src.domain.categorizer import categorize_item


@dataclass
class ShoppingItemDraft:
    raw_text: str
    normalized_name: str
    quantity_value: float | None = None
    quantity_unit: str | None = None
    note: str | None = None
    category: str | None = None



def build_item(raw_text: str, normalized_name: str, quantity_value: float | None = None) -> ShoppingItemDraft:
    return ShoppingItemDraft(
        raw_text=raw_text,
        normalized_name=normalized_name,
        quantity_value=quantity_value,
        category=categorize_item(normalized_name),
    )
