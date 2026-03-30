from __future__ import annotations

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ירקות ופירות": ("מלפפון", "עגבניה", "בננה", "תפוח", "בצל", "אבוקדו"),
    "מקרר": ("חלב", "קוטג'", "יוגורט", "גבינה", "ביצים"),
    "מאפיה": ("לחם", "פיתה", "חלה", "לחמניה"),
    "מזווה": ("אורז", "פסטה", "טונה", "קפה", "סוכר"),
    "משקאות": ("קולה", "סודה", "מיץ", "מים"),
}

DEFAULT_CATEGORY = "כללי"



def categorize_item(name: str) -> str:
    lowered = name.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            return category
    return DEFAULT_CATEGORY
