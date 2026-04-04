from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

VALID_TABLES = frozenset({
    'chats', 'shopping_lists', 'list_items', 'events',
    'price_queries', 'price_cache', 'users',
    'purchase_history', 'purchase_history_items',
})

def _escape_like(s: str) -> str:
    """Escape LIKE metacharacters for safe use in SQLite LIKE patterns."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        external_chat_id TEXT NOT NULL,
        title TEXT,
        default_city TEXT,
        active_list_id INTEGER REFERENCES shopping_lists(id),
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
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','bought','deleted')),
        added_by_user_id TEXT,
        added_by_name TEXT,
        purchased_by_user_id TEXT,
        purchased_by_name TEXT,
        sku TEXT,
        barcode TEXT,
        store_name TEXT,
        chain_name TEXT,
        estimated_price REAL,
        purchase_price REAL,
        price_currency TEXT DEFAULT 'ILS',
        purchased_at TEXT,
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
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        user_id TEXT NOT NULL,
        display_name TEXT,
        last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(chat_id, user_id),
        FOREIGN KEY(chat_id) REFERENCES chats(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS purchase_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        list_id INTEGER NOT NULL,
        list_name TEXT NOT NULL,
        completed_by_user_id TEXT,
        completed_by_name TEXT,
        total_estimated REAL,
        total_purchased REAL,
        item_count INTEGER,
        receipt_photo_file_id TEXT,
        notes TEXT,
        completed_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(chat_id) REFERENCES chats(id),
        FOREIGN KEY(list_id) REFERENCES shopping_lists(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS purchase_history_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        purchase_history_id INTEGER NOT NULL,
        item_name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        quantity_value REAL,
        quantity_unit TEXT,
        category TEXT,
        sku TEXT,
        barcode TEXT,
        store_name TEXT,
        chain_name TEXT,
        estimated_price REAL,
        purchase_price REAL,
        price_currency TEXT DEFAULT 'ILS',
        added_by_name TEXT,
        purchased_by_name TEXT,
        FOREIGN KEY(purchase_history_id) REFERENCES purchase_history(id)
    )
    """,
    # Indexes (removed duplicate idx_list_items_list_id_status -- same as idx_items_list_status)
    """CREATE INDEX IF NOT EXISTS idx_items_list_status ON list_items(list_id, status)""",
    """CREATE INDEX IF NOT EXISTS idx_items_list_name ON list_items(list_id, status, normalized_name)""",
    """CREATE INDEX IF NOT EXISTS idx_items_user ON list_items(list_id, status, added_by_user_id)""",
    """CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_users_name ON users(chat_id, display_name)""",
    """CREATE INDEX IF NOT EXISTS idx_list_items_status ON list_items(status)""",
    """CREATE INDEX IF NOT EXISTS idx_list_items_added_by ON list_items(added_by_user_id)""",
    """CREATE INDEX IF NOT EXISTS idx_list_items_normalized_name ON list_items(normalized_name)""",
    """CREATE INDEX IF NOT EXISTS idx_list_items_sku ON list_items(sku)""",
    """CREATE INDEX IF NOT EXISTS idx_list_items_barcode ON list_items(barcode)""",
    """CREATE INDEX IF NOT EXISTS idx_list_items_purchased_at ON list_items(purchased_at)""",
    """CREATE INDEX IF NOT EXISTS idx_purchase_history_chat ON purchase_history(chat_id)""",
    """CREATE INDEX IF NOT EXISTS idx_purchase_history_completed ON purchase_history(completed_at)""",
    """CREATE INDEX IF NOT EXISTS idx_purchase_history_items_history ON purchase_history_items(purchase_history_id)""",
    """CREATE INDEX IF NOT EXISTS idx_chats_active_list ON chats(active_list_id)""",
)


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def _get_column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names for a table."""
    if table not in VALID_TABLES:
        raise ValueError(f"Invalid table name: {table}")
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
    existing_cols: set[str],
) -> None:
    """ALTER TABLE ADD COLUMN only if it does not already exist."""
    if table not in VALID_TABLES:
        raise ValueError(f"Invalid table name: {table}")
    if column not in existing_cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        logger.info("Migration: added column %s.%s", table, column)


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Idempotent migrations -- safe to run on every startup."""

    # Only migrate if old tables exist (fresh DB gets new schema directly)
    if not _table_exists(conn, "chats"):
        return

    # --- chats: add active_list_id ---
    chat_cols = _get_column_names(conn, "chats")
    _add_column_if_missing(
        conn, "chats", "active_list_id",
        "INTEGER REFERENCES shopping_lists(id)", chat_cols,
    )

    # --- list_items: add new columns ---
    if _table_exists(conn, "list_items"):
        item_cols = _get_column_names(conn, "list_items")
        for col, defn in (
            ("added_by_name", "TEXT"),
            ("purchased_by_name", "TEXT"),
            ("sku", "TEXT"),
            ("barcode", "TEXT"),
            ("store_name", "TEXT"),
            ("chain_name", "TEXT"),
            ("estimated_price", "REAL"),
            ("purchase_price", "REAL"),
            ("price_currency", "TEXT DEFAULT 'ILS'"),
            ("purchased_at", "TEXT"),
        ):
            _add_column_if_missing(conn, "list_items", col, defn, item_cols)

        # Status migration: 'active' -> 'pending', 'purchased' -> 'bought'
        changed = conn.execute(
            "UPDATE list_items SET status = 'pending' WHERE status = 'active'"
        ).rowcount
        if changed:
            logger.info(
                "Migration: converted %d items from status 'active' to 'pending'",
                changed,
            )
        changed = conn.execute(
            "UPDATE list_items SET status = 'bought' WHERE status = 'purchased'"
        ).rowcount
        if changed:
            logger.info(
                "Migration: converted %d items from status 'purchased' to 'bought'",
                changed,
            )

    conn.commit()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

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


@dataclass
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
    # New fields
    added_by_name: str | None = None
    purchased_by_name: str | None = None
    sku: str | None = None
    barcode: str | None = None
    store_name: str | None = None
    chain_name: str | None = None
    estimated_price: float | None = None
    purchase_price: float | None = None
    price_currency: str | None = None
    purchased_at: str | None = None


@dataclass(frozen=True)
class PurchaseHistoryRecord:
    id: int
    chat_id: int
    list_id: int
    list_name: str
    completed_by_user_id: str | None
    completed_by_name: str | None
    total_estimated: float | None
    total_purchased: float | None
    item_count: int | None
    receipt_photo_file_id: str | None
    notes: str | None
    completed_at: str | None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

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
            # Run migrations BEFORE new schema so ALTER TABLE works on old tables
            _run_migrations(conn)
            for statement in SCHEMA_STATEMENTS:
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    # Tolerate "already exists" from old list_items with different CHECK
                    if "already exists" in str(exc):
                        pass
                    else:
                        raise
            conn.commit()

    def get_db_size_bytes(self) -> int:
        if self.db_path.exists():
            return self.db_path.stat().st_size
        return 0

    def rotate_if_needed(self, max_bytes: int = 50 * 1024 * 1024) -> bool:
        """Rotate old data if DB exceeds max_bytes.
        Deletes bought/deleted items older than 30 days and old events.
        """
        size = self.get_db_size_bytes()
        if size <= max_bytes:
            return False

        logger.warning(
            "Shopping DB size %d bytes exceeds limit %d, rotating...",
            size, max_bytes,
        )

        with self.connect() as conn:
            conn.execute("""
                DELETE FROM list_items
                WHERE status IN ('bought', 'deleted')
                AND updated_at < datetime('now', '-30 days')
            """)
            conn.execute("""
                DELETE FROM events
                WHERE created_at < datetime('now', '-60 days')
            """)
            conn.execute("""
                DELETE FROM price_queries
                WHERE created_at < datetime('now', '-30 days')
            """)
            conn.execute("""
                DELETE FROM price_cache
                WHERE expires_at < datetime('now')
            """)
            conn.execute("VACUUM")
            conn.commit()

        new_size = self.get_db_size_bytes()
        logger.info("Shopping DB rotated: %d -> %d bytes", size, new_size)
        return True

    def fetch_schema_objects(self) -> Iterable[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()

    # ------------------------------------------------------------------
    # Chat methods
    # ------------------------------------------------------------------

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
                    default_city = COALESCE(excluded.default_city, chats.default_city),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (platform, external_chat_id, title, default_city),
            )
            row = conn.execute(
                "SELECT id, platform, external_chat_id, title, default_city "
                "FROM chats WHERE platform = ? AND external_chat_id = ?",
                (platform, external_chat_id),
            ).fetchone()
            conn.commit()
        return self._row_to_chat(row)

    def update_chat_default_city(self, *, chat_id: int, default_city: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE chats SET default_city = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (default_city, chat_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Active list management
    # ------------------------------------------------------------------

    def get_active_list_id(self, chat_id: int) -> int:
        """Return the active list id for the chat.

        If no active list is set, default to the 'main' list (creating if
        needed) and persist the choice.  Entire read-check-write in one connection.
        """
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT active_list_id FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
            if row and row["active_list_id"] is not None:
                return row["active_list_id"]

            # Create/get main list and set as active in same connection
            list_row = conn.execute(
                "SELECT id FROM shopping_lists WHERE chat_id = ? AND name = 'main'",
                (chat_id,),
            ).fetchone()
            if list_row:
                list_id = list_row["id"]
            else:
                cursor = conn.execute(
                    "INSERT INTO shopping_lists (chat_id, name) VALUES (?, 'main')",
                    (chat_id,),
                )
                list_id = cursor.lastrowid
            conn.execute(
                "UPDATE chats SET active_list_id = ?, updated_at = datetime('now') WHERE id = ?",
                (list_id, chat_id),
            )
            conn.commit()
            return list_id
        finally:
            conn.close()

    def set_active_list(self, *, chat_id: int, list_id: int) -> None:
        """Set the working list for a chat."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE chats SET active_list_id = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (list_id, chat_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # List CRUD
    # ------------------------------------------------------------------

    def ensure_active_list(
        self, *, chat_id: int, name: str = "main"
    ) -> ShoppingListRecord:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO shopping_lists (chat_id, name, is_active)
                VALUES (?, ?, 1)
                ON CONFLICT(chat_id, name) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, name),
            )
            row = conn.execute(
                "SELECT id, chat_id, name, is_active "
                "FROM shopping_lists WHERE chat_id = ? AND name = ?",
                (chat_id, name),
            ).fetchone()
            conn.commit()
        return self._row_to_list(row)

    def create_list(self, *, chat_id: int, name: str) -> int:
        """Create a new shopping list.  Returns list_id.
        If it already exists, returns existing id.
        """
        sl = self.ensure_active_list(chat_id=chat_id, name=name)
        return sl.id

    def get_all_lists(self, chat_id: int) -> list[dict]:
        """Return all lists for a chat with pending item counts."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sl.id, sl.name, sl.is_active, sl.created_at,
                       COUNT(CASE WHEN li.status = 'pending'
                             THEN 1 END) AS pending_count
                FROM shopping_lists sl
                LEFT JOIN list_items li ON li.list_id = sl.id
                WHERE sl.chat_id = ?
                GROUP BY sl.id
                ORDER BY sl.created_at ASC
                """,
                (chat_id,),
            ).fetchall()
            active_row = conn.execute(
                "SELECT active_list_id FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
            active_list_id = (
                active_row["active_list_id"] if active_row else None
            )

        return [
            {
                "id": r["id"],
                "name": r["name"],
                "is_active": bool(r["is_active"]),
                "is_working_list": r["id"] == active_list_id,
                "pending_count": r["pending_count"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def list_exists(self, *, chat_id: int, name: str) -> bool:
        """Check if a list with the given name exists for the chat."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM shopping_lists WHERE chat_id = ? AND name = ?",
                (chat_id, name),
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Item CRUD
    # ------------------------------------------------------------------

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
        added_by_name: str | None = None,
        estimated_price: float | None = None,
        sku: str | None = None,
        barcode: str | None = None,
    ) -> StoredItem:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO list_items (
                    list_id, raw_text, normalized_name, quantity_value,
                    quantity_unit, note, category, status,
                    added_by_user_id, added_by_name,
                    estimated_price, sku, barcode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                (
                    list_id, raw_text, normalized_name, quantity_value,
                    quantity_unit, note, category,
                    added_by_user_id, added_by_name,
                    estimated_price, sku, barcode,
                ),
            )
            row = conn.execute(
                "SELECT * FROM list_items WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            conn.commit()
        return self._row_to_item(row)

    def list_active_items(self, list_id: int) -> list[StoredItem]:
        """List pending items.  Kept as list_active_items for backward compat."""
        return self.list_pending_items(list_id)

    def list_pending_items(self, list_id: int) -> list[StoredItem]:
        """List all pending (not bought/deleted) items in a list."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM list_items
                WHERE list_id = ? AND status = 'pending'
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
        purchase_price: float | None = None,
        store_name: str | None = None,
        chain_name: str | None = None,
    ) -> StoredItem | None:
        normalized_query = query.strip().lower()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM list_items
                WHERE list_id = ?
                  AND status = 'pending'
                  AND lower(normalized_name) LIKE ? ESCAPE '\\'
                ORDER BY id ASC
                LIMIT 1
                """,
                (list_id, f"%{_escape_like(normalized_query)}%"),
            ).fetchone()
            if row is None:
                return None

            # Map old status names to new
            actual_status = status
            if actual_status == "purchased":
                actual_status = "bought"
            if actual_status == "active":
                actual_status = "pending"

            extra_sets = ""
            params: list = [actual_status]

            if actual_status == "bought":
                params.append(acting_user_id)
                extra_sets += ", purchased_by_user_id = ?"
                if purchase_price is not None:
                    params.append(purchase_price)
                    extra_sets += ", purchase_price = ?"
                if store_name is not None:
                    params.append(store_name)
                    extra_sets += ", store_name = ?"
                if chain_name is not None:
                    params.append(chain_name)
                    extra_sets += ", chain_name = ?"
                extra_sets += ", purchased_at = datetime('now')"
            else:
                params.append(row["purchased_by_user_id"])
                extra_sets += ", purchased_by_user_id = ?"

            params.append(row["id"])

            conn.execute(
                f"""
                UPDATE list_items
                SET status = ?{extra_sets}, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                params,
            )
            updated = conn.execute(
                "SELECT * FROM list_items WHERE id = ?", (row["id"],)
            ).fetchone()
            conn.commit()
        return self._row_to_item(updated)

    def update_item_purchase(
        self,
        *,
        item_id: int,
        purchase_price: float | None = None,
        store_name: str | None = None,
        chain_name: str | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        purchased_by_user_id: str | None = None,
        purchased_by_name: str | None = None,
    ) -> StoredItem | None:
        """Update item with purchase details and mark as bought."""
        sets = [
            "status = 'bought'",
            "purchased_at = datetime('now')",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        params: list = []
        if purchase_price is not None:
            sets.append("purchase_price = ?")
            params.append(purchase_price)
        if store_name is not None:
            sets.append("store_name = ?")
            params.append(store_name)
        if chain_name is not None:
            sets.append("chain_name = ?")
            params.append(chain_name)
        if sku is not None:
            sets.append("sku = ?")
            params.append(sku)
        if barcode is not None:
            sets.append("barcode = ?")
            params.append(barcode)
        if purchased_by_user_id is not None:
            sets.append("purchased_by_user_id = ?")
            params.append(purchased_by_user_id)
        if purchased_by_name is not None:
            sets.append("purchased_by_name = ?")
            params.append(purchased_by_name)

        params.append(item_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE list_items SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            row = conn.execute(
                "SELECT * FROM list_items WHERE id = ?", (item_id,)
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        return self._row_to_item(row)

    def update_item_estimated_price(
        self,
        *,
        item_id: int,
        estimated_price: float | None = None,
        sku: str | None = None,
        barcode: str | None = None,
    ) -> StoredItem | None:
        """Set estimated price and optional product identifiers."""
        if estimated_price is None and sku is None and barcode is None:
            return None

        sets = ["updated_at = CURRENT_TIMESTAMP"]
        params: list = []
        if estimated_price is not None:
            sets.append("estimated_price = ?")
            params.append(estimated_price)
        if sku is not None:
            sets.append("sku = ?")
            params.append(sku)
        if barcode is not None:
            sets.append("barcode = ?")
            params.append(barcode)

        params.append(item_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE list_items SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            row = conn.execute(
                "SELECT * FROM list_items WHERE id = ?", (item_id,)
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        return self._row_to_item(row)

    def move_items(self, *, item_ids: list[int], target_list_id: int) -> int:
        """Move items to a different list.  Returns count of moved items."""
        if not item_ids:
            return 0
        placeholders = ",".join("?" for _ in item_ids)
        with self.connect() as conn:
            cursor = conn.execute(
                f"UPDATE list_items SET list_id = ?, "
                f"updated_at = CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders})",
                [target_list_id] + item_ids,
            )
            conn.commit()
        return cursor.rowcount

    def move_items_by_name(
        self,
        *,
        source_list_id: int,
        item_names: list[str],
        target_list_id: int,
    ) -> int:
        """Find items by fuzzy name match in source list and move to target.
        Returns moved count.
        """
        if not item_names:
            return 0
        moved = 0
        with self.connect() as conn:
            for name in item_names:
                escaped = _escape_like(name.strip().lower())
                rows = conn.execute(
                    """
                    SELECT id FROM list_items
                    WHERE list_id = ? AND status = 'pending'
                      AND lower(normalized_name) LIKE ? ESCAPE '\\'
                    """,
                    (source_list_id, f"%{escaped}%"),
                ).fetchall()
                ids = [r["id"] for r in rows]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(
                        f"UPDATE list_items SET list_id = ?, "
                        f"updated_at = CURRENT_TIMESTAMP "
                        f"WHERE id IN ({placeholders})",
                        [target_list_id] + ids,
                    )
                    moved += len(ids)
            conn.commit()
        return moved

    def record_event(
        self,
        *,
        chat_id: int,
        user_id: str | None,
        event_type: str,
        payload: dict,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events "
                "(chat_id, user_id, event_type, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    chat_id,
                    user_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            conn.commit()

    def clear_active_items(
        self, *, list_id: int, acting_user_id: str | None = None
    ) -> int:
        """Mark all pending items as deleted."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE list_items
                SET status = 'deleted',
                    purchased_by_user_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE list_id = ? AND status = 'pending'
                """,
                (acting_user_id, list_id),
            )
            conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Purchase history
    # ------------------------------------------------------------------

    def complete_list(
        self,
        *,
        list_id: int,
        completed_by_user_id: str | None = None,
        completed_by_name: str | None = None,
    ) -> int:
        """Snapshot bought items into purchase_history, then mark them deleted.
        Returns the history record id.
        """
        with self.connect() as conn:
            sl = conn.execute(
                "SELECT id, chat_id, name FROM shopping_lists WHERE id = ?",
                (list_id,),
            ).fetchone()
            if sl is None:
                raise ValueError(f"List {list_id} not found")

            bought = conn.execute(
                "SELECT * FROM list_items "
                "WHERE list_id = ? AND status = 'bought'",
                (list_id,),
            ).fetchall()

            total_est = sum(r["estimated_price"] or 0 for r in bought)
            total_pur = sum(r["purchase_price"] or 0 for r in bought)

            cursor = conn.execute(
                """
                INSERT INTO purchase_history
                    (chat_id, list_id, list_name,
                     completed_by_user_id, completed_by_name,
                     total_estimated, total_purchased, item_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sl["chat_id"], list_id, sl["name"],
                    completed_by_user_id, completed_by_name,
                    total_est or None, total_pur or None, len(bought),
                ),
            )
            history_id = cursor.lastrowid

            for r in bought:
                conn.execute(
                    """
                    INSERT INTO purchase_history_items
                        (purchase_history_id, item_name, normalized_name,
                         quantity_value, quantity_unit, category,
                         sku, barcode, store_name, chain_name,
                         estimated_price, purchase_price, price_currency,
                         added_by_name, purchased_by_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        history_id, r["raw_text"], r["normalized_name"],
                        r["quantity_value"], r["quantity_unit"],
                        r["category"], r["sku"], r["barcode"],
                        r["store_name"], r["chain_name"],
                        r["estimated_price"], r["purchase_price"],
                        r["price_currency"],
                        r["added_by_name"], r["purchased_by_name"],
                    ),
                )

            # Mark bought items as deleted after snapshotting
            conn.execute(
                "UPDATE list_items SET status = 'deleted', updated_at = datetime('now') "
                "WHERE list_id = ? AND status = 'bought'",
                (list_id,),
            )

            conn.commit()
        return history_id

    def get_purchase_history(
        self, chat_id: int, months_back: int = 1
    ) -> list[PurchaseHistoryRecord]:
        """Return purchase history entries for the last N months."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM purchase_history
                WHERE chat_id = ?
                  AND completed_at >= datetime('now', ? || ' months')
                ORDER BY completed_at DESC
                """,
                (chat_id, f"-{months_back}"),
            ).fetchall()
        return [self._row_to_purchase_history(r) for r in rows]

    def get_monthly_expenses(
        self, chat_id: int, year: int, month: int
    ) -> dict:
        """Return total spent, item count, per-list breakdown for a month."""
        month_start = f"{year:04d}-{month:02d}-01"
        if month == 12:
            month_end = f"{year + 1:04d}-01-01"
        else:
            month_end = f"{year:04d}-{month + 1:02d}-01"

        with self.connect() as conn:
            totals = conn.execute(
                """
                SELECT COALESCE(SUM(total_purchased), 0) AS total_spent,
                       COALESCE(SUM(item_count), 0)      AS total_items,
                       COUNT(*)                           AS list_count
                FROM purchase_history
                WHERE chat_id = ?
                  AND completed_at >= ? AND completed_at < ?
                """,
                (chat_id, month_start, month_end),
            ).fetchone()

            per_list = conn.execute(
                """
                SELECT list_name,
                       SUM(total_purchased) AS spent,
                       SUM(item_count)      AS items
                FROM purchase_history
                WHERE chat_id = ?
                  AND completed_at >= ? AND completed_at < ?
                GROUP BY list_name
                ORDER BY spent DESC
                """,
                (chat_id, month_start, month_end),
            ).fetchall()

        return {
            "year": year,
            "month": month,
            "total_spent": totals["total_spent"],
            "total_items": totals["total_items"],
            "list_count": totals["list_count"],
            "per_list": [
                {
                    "list_name": r["list_name"],
                    "spent": r["spent"],
                    "items": r["items"],
                }
                for r in per_list
            ],
        }

    # ------------------------------------------------------------------
    # Row converters
    # ------------------------------------------------------------------

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
            added_by_name=row["added_by_name"],
            purchased_by_name=row["purchased_by_name"],
            sku=row["sku"],
            barcode=row["barcode"],
            store_name=row["store_name"],
            chain_name=row["chain_name"],
            estimated_price=row["estimated_price"],
            purchase_price=row["purchase_price"],
            price_currency=row["price_currency"],
            purchased_at=row["purchased_at"],
        )

    def _row_to_purchase_history(
        self, row: sqlite3.Row
    ) -> PurchaseHistoryRecord:
        return PurchaseHistoryRecord(
            id=row["id"],
            chat_id=row["chat_id"],
            list_id=row["list_id"],
            list_name=row["list_name"],
            completed_by_user_id=row["completed_by_user_id"],
            completed_by_name=row["completed_by_name"],
            total_estimated=row["total_estimated"],
            total_purchased=row["total_purchased"],
            item_count=row["item_count"],
            receipt_photo_file_id=row["receipt_photo_file_id"],
            notes=row["notes"],
            completed_at=row["completed_at"],
        )

    # ------------------------------------------------------------------
    # User methods
    # ------------------------------------------------------------------

    def upsert_user(
        self, *, chat_id: int, user_id: str, display_name: str | None
    ) -> None:
        """Record or update a user's display name for this chat."""
        if not display_name:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (chat_id, user_id, display_name)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    display_name = COALESCE(
                        excluded.display_name, users.display_name
                    ),
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (chat_id, user_id, display_name),
            )
            conn.commit()

    def get_user_display_name(
        self, *, chat_id: int, user_id: str
    ) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT display_name FROM users "
                "WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
        return row["display_name"] if row else None

    def list_items_by_user(
        self,
        *,
        list_id: int,
        user_id: str | None = None,
        user_name: str | None = None,
        chat_id: int | None = None,
    ) -> list[StoredItem]:
        """List pending items added by a specific user."""
        actual_user_id = user_id
        if not actual_user_id and user_name and chat_id:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT user_id FROM users "
                    "WHERE chat_id = ? "
                    "AND lower(display_name) LIKE ? ESCAPE '\\'",
                    (chat_id, f"%{_escape_like(user_name.lower())}%"),
                ).fetchone()
            if row:
                actual_user_id = row["user_id"]

        if not actual_user_id:
            return []

        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM list_items
                WHERE list_id = ?
                  AND status = 'pending'
                  AND added_by_user_id = ?
                ORDER BY category ASC, created_at ASC
                """,
                (list_id, actual_user_id),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def find_similar_items(
        self, *, list_id: int, query: str
    ) -> list[StoredItem]:
        """Find pending items whose normalized_name contains the query."""
        normalized_query = query.strip().lower()
        if not normalized_query:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM list_items
                WHERE list_id = ? AND status = 'pending'
                  AND (lower(normalized_name) LIKE ? ESCAPE '\\'
                       OR INSTR(?, lower(normalized_name)) > 0)
                ORDER BY id ASC
                """,
                (
                    list_id,
                    f"%{_escape_like(normalized_query)}%",
                    normalized_query,
                ),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def merge_item_quantity(
        self, *, item_id: int, additional_quantity: float
    ) -> StoredItem | None:
        """Add quantity to an existing item."""
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE list_items
                SET quantity_value = COALESCE(quantity_value, 0) + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (additional_quantity, item_id),
            )
            row = conn.execute(
                "SELECT * FROM list_items WHERE id = ?", (item_id,)
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        return self._row_to_item(row)

    def update_item_quantity(
        self, *, item_id: int, new_quantity: float
    ) -> StoredItem | None:
        """Replace the quantity of an existing item."""
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE list_items
                SET quantity_value = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_quantity, item_id),
            )
            row = conn.execute(
                "SELECT * FROM list_items WHERE id = ?", (item_id,)
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        return self._row_to_item(row)
