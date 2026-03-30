from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        external_chat_id TEXT NOT NULL,
        title TEXT,
        default_city TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(platform, external_chat_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shopping_lists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        name TEXT NOT NULL DEFAULT 'main',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(chat_id) REFERENCES chats(id),
        UNIQUE(chat_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS list_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        list_id INTEGER NOT NULL,
        raw_text TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        quantity_value REAL,
        quantity_unit TEXT,
        note TEXT,
        category TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        added_by_user_id TEXT,
        purchased_by_user_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(list_id) REFERENCES shopping_lists(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        user_id TEXT,
        event_type TEXT NOT NULL,
        payload_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(chat_id) REFERENCES chats(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_queries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        query TEXT NOT NULL,
        city TEXT,
        resolved_product TEXT,
        source TEXT NOT NULL,
        raw_result_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(chat_id) REFERENCES chats(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_cache (
        cache_key TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        city TEXT,
        query TEXT NOT NULL,
        response_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expires_at TEXT NOT NULL
    )
    """,
)


@dataclass(frozen=True)
class ChatRecord:
    id: int
    platform: str
    external_chat_id: str
    title: str | None
    default_city: str | None


@dataclass(frozen=True)
class ShoppingListRecord:
    id: int
    chat_id: int
    name: str
    is_active: bool


@dataclass(frozen=True)
class StoredItem:
    id: int
    list_id: int
    raw_text: str
    normalized_name: str
    quantity_value: float | None
    quantity_unit: str | None
    note: str | None
    category: str | None
    status: str
    added_by_user_id: str | None
    purchased_by_user_id: str | None


class SQLiteStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            conn.commit()

    def fetch_schema_objects(self) -> Iterable[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()

    def ensure_chat(
        self,
        *,
        platform: str,
        external_chat_id: str,
        title: str | None = None,
        default_city: str | None = None,
    ) -> ChatRecord:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chats (platform, external_chat_id, title, default_city)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(platform, external_chat_id) DO UPDATE SET
                    title = COALESCE(excluded.title, chats.title),
                    default_city = COALESCE(chats.default_city, excluded.default_city),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (platform, external_chat_id, title, default_city),
            )
            row = conn.execute(
                "SELECT id, platform, external_chat_id, title, default_city FROM chats WHERE platform = ? AND external_chat_id = ?",
                (platform, external_chat_id),
            ).fetchone()
            conn.commit()
        return self._row_to_chat(row)

    def update_chat_default_city(self, *, chat_id: int, default_city: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE chats SET default_city = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (default_city, chat_id),
            )
            conn.commit()

    def ensure_active_list(self, *, chat_id: int, name: str = "main") -> ShoppingListRecord:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO shopping_lists (chat_id, name, is_active)
                VALUES (?, ?, 1)
                ON CONFLICT(chat_id, name) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, name),
            )
            row = conn.execute(
                "SELECT id, chat_id, name, is_active FROM shopping_lists WHERE chat_id = ? AND name = ?",
                (chat_id, name),
            ).fetchone()
            conn.commit()
        return self._row_to_list(row)

    def add_item(
        self,
        *,
        list_id: int,
        raw_text: str,
        normalized_name: str,
        quantity_value: float | None = None,
        quantity_unit: str | None = None,
        note: str | None = None,
        category: str | None = None,
        added_by_user_id: str | None = None,
    ) -> StoredItem:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO list_items (
                    list_id, raw_text, normalized_name, quantity_value, quantity_unit,
                    note, category, status, added_by_user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (list_id, raw_text, normalized_name, quantity_value, quantity_unit, note, category, added_by_user_id),
            )
            row = conn.execute(
                "SELECT * FROM list_items WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            conn.commit()
        return self._row_to_item(row)

    def list_active_items(self, list_id: int) -> list[StoredItem]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM list_items
                WHERE list_id = ? AND status = 'active'
                ORDER BY category ASC, created_at ASC, id ASC
                """,
                (list_id,),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def update_item_status(
        self,
        *,
        list_id: int,
        query: str,
        status: str,
        acting_user_id: str | None = None,
    ) -> StoredItem | None:
        normalized_query = query.strip().lower()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM list_items
                WHERE list_id = ? AND status = 'active' AND lower(normalized_name) LIKE ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (list_id, f"%{normalized_query}%"),
            ).fetchone()
            if row is None:
                return None

            conn.execute(
                """
                UPDATE list_items
                SET status = ?, purchased_by_user_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, acting_user_id if status == "purchased" else row["purchased_by_user_id"], row["id"]),
            )
            updated = conn.execute("SELECT * FROM list_items WHERE id = ?", (row["id"],)).fetchone()
            conn.commit()
        return self._row_to_item(updated)

    def record_event(self, *, chat_id: int, user_id: str | None, event_type: str, payload: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events (chat_id, user_id, event_type, payload_json) VALUES (?, ?, ?, ?)",
                (chat_id, user_id, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            )
            conn.commit()


    def clear_active_items(self, *, list_id: int, acting_user_id: str | None = None) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE list_items
                SET status = 'deleted', purchased_by_user_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE list_id = ? AND status = 'active'
                """,
                (acting_user_id, list_id),
            )
            conn.commit()
        return cursor.rowcount

    def _row_to_chat(self, row: sqlite3.Row) -> ChatRecord:
        return ChatRecord(
            id=row["id"],
            platform=row["platform"],
            external_chat_id=row["external_chat_id"],
            title=row["title"],
            default_city=row["default_city"],
        )

    def _row_to_list(self, row: sqlite3.Row) -> ShoppingListRecord:
        return ShoppingListRecord(
            id=row["id"],
            chat_id=row["chat_id"],
            name=row["name"],
            is_active=bool(row["is_active"]),
        )

    def _row_to_item(self, row: sqlite3.Row) -> StoredItem:
        return StoredItem(
            id=row["id"],
            list_id=row["list_id"],
            raw_text=row["raw_text"],
            normalized_name=row["normalized_name"],
            quantity_value=row["quantity_value"],
            quantity_unit=row["quantity_unit"],
            note=row["note"],
            category=row["category"],
            status=row["status"],
            added_by_user_id=row["added_by_user_id"],
            purchased_by_user_id=row["purchased_by_user_id"],
        )
