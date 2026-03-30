from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """אתה עוזר קניות משותף בעברית לקבוצת טלגרם.

תפקיד:
- לנהל רשימת קניות משותפת לקבוצה
- להגיב רק כשפונים אליך או כשההודעה קשורה בבירור לקניות
- לתת משוב קצר וברור על כל פעולה שביצעת

מתי לענות:
- פנייה ישירה — שאלה, בקשה, פקודה
- הודעה שהיא בבירור פריט/ים לרשימת קניות (למשל: "חלב", "2 לחם", "עגבניות, מלפפונים, גבינה")
- שאלה על מה אתה יכול לעשות

מתי לשתוק (אל תענה בכלל, אל תקרא לשום כלי):
- שיחה רגילה בקבוצה
- דיונים, הערות, בדיחות
- הודעות שלא קשורות לקניות
- כל מקרה לא ברור — עדיף שתיקה על פעולה שגויה

כללי פעולה:
- הצגת רשימה — show_list
- הוספת פריט/ים — add_item (קריאה נפרדת לכל פריט)
- סימון כנקנה — mark_purchased
- מחיקת פריט — delete_item
- ניקוי כל הרשימה — clear_list
- שינוי עיר — set_city
- בדיקת מחיר — price_lookup

משוב:
- על כל פעולה, תן אישור קצר וברור בעברית
- אם הפעולה נכשלה, הסבר בקצרה מה קרה

סגנון:
- ידידותי אבל ענייני
- עברית טבעית, לא רובוטית
- אם מישהו שואל מה אתה יכול לעשות — ענה בחום והסבר בקצרה
- אל תוסיף אימוג'ים מיותרים
"""

TOOLS = [
    {
        "name": "show_list",
        "description": "הצג את רשימת הקניות הנוכחית מקובצת לפי קטגוריות",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "add_item",
        "description": "הוסף פריט לרשימת הקניות",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "שם הפריט בעברית"},
                "quantity": {"type": "number", "description": "כמות (אופציונלי)"}
            },
            "required": ["item_name"]
        }
    },
    {
        "name": "mark_purchased",
        "description": "סמן פריט כנקנה",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "שם הפריט לסימון"}
            },
            "required": ["item_name"]
        }
    },
    {
        "name": "delete_item",
        "description": "מחק פריט מהרשימה",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "שם הפריט למחיקה"}
            },
            "required": ["item_name"]
        }
    },
    {
        "name": "clear_list",
        "description": "נקה את כל הרשימה — מחק את כל הפריטים",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "set_city",
        "description": "עדכן את עיר ברירת המחדל לבדיקת מחירים",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "שם העיר"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "price_lookup",
        "description": "בדוק מחיר של מוצר בסופרמרקטים",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "שם המוצר לבדיקת מחיר"}
            },
            "required": ["item_name"]
        }
    },
]


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = "MiniMax-M2.7"
    base_url: str = "https://api.minimax.io/anthropic"
    timeout: int = 30


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""


class LLMTransport:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        })
        self.messages_url = f"{config.base_url.rstrip('/')}/v1/messages"

    def send(self, *, system: str, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        payload = {
            "model": self.config.model,
            "max_tokens": 1024,
            "system": system,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools

        response = self.session.post(
            self.messages_url,
            json=payload,
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        body = response.json()
        return self._parse_response(body)

    def _parse_response(self, body: dict) -> LLMResponse:
        result = LLMResponse(stop_reason=body.get("stop_reason", ""))
        for block in body.get("content", []):
            block_type = block.get("type")
            if block_type == "text":
                result.text += block.get("text", "")
            elif block_type == "tool_use":
                result.tool_calls.append(ToolCall(
                    id=block["id"],
                    name=block["name"],
                    input=block.get("input", {}),
                ))
            # skip "thinking" blocks
        return result
