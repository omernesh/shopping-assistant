"""Download and parse Israeli supermarket XML price feeds into local price DB."""
from __future__ import annotations

import gzip
import logging
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PRICE_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code TEXT NOT NULL,
    item_name TEXT NOT NULL,
    manufacturer TEXT,
    price REAL NOT NULL,
    unit_price REAL,
    quantity TEXT,
    unit_of_measure TEXT,
    is_weighted INTEGER DEFAULT 0,
    chain TEXT NOT NULL,
    store_id TEXT,
    update_date TEXT,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(item_code, chain, store_id)
);

CREATE INDEX IF NOT EXISTS idx_products_item_name ON products(item_name);
CREATE INDEX IF NOT EXISTS idx_products_item_code ON products(item_code);
CREATE INDEX IF NOT EXISTS idx_products_chain ON products(chain);

CREATE TABLE IF NOT EXISTS feed_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    store_id TEXT,
    file_url TEXT,
    item_count INTEGER,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'ok'
);
"""

CHAIN_FEEDS = {
    "shufersal": {
        "name": "\u05e9\u05d5\u05e4\u05e8\u05e1\u05dc",
        "index_url": "https://prices.shufersal.co.il/FileObject/UpdateCategory?catID=2&storeId=0",
        "base_url": "https://prices.shufersal.co.il",
    },
}

FIELD_MAPS = {
    "shufersal": {
        "item_code": "ItemCode", "item_name": "ItemName", "manufacturer": "ManufacturerName",
        "price": "ItemPrice", "unit_price": "UnitOfMeasurePrice", "quantity": "Quantity",
        "unit_of_measure": "UnitOfMeasure", "update_date": "PriceUpdateDate", "is_weighted": "bIsWeighted",
    },
    "rami-levy": {
        "item_code": "ItemCode", "item_name": "ItemNm", "manufacturer": "ManufacturerName",
        "price": "ItemPrice", "unit_price": "UnitOfMeasurePrice", "quantity": "Quantity",
        "unit_of_measure": "UnitOfMeasure", "update_date": "PriceUpdateDate", "is_weighted": "bIsWeighted",
    },
}


def _extract_text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def _safe_float(s: str) -> float:
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


class PriceDB:
    """Local SQLite price database populated from chain XML feeds."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(PRICE_DB_SCHEMA)

    def search_product(self, query: str, limit: int = 20) -> list[dict]:
        """Search products by name. Prefers exact matches, falls back to LIKE."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Try prefix match first (item name starts with query)
            rows = conn.execute(
                """
                SELECT item_name, manufacturer, price, unit_price, chain, store_id, update_date
                FROM products
                WHERE item_name LIKE ?
                ORDER BY price ASC
                LIMIT ?
                """,
                (f"{query}%", limit),
            ).fetchall()

            if not rows:
                # Fallback to contains match
                rows = conn.execute(
                    """
                    SELECT item_name, manufacturer, price, unit_price, chain, store_id, update_date
                    FROM products
                    WHERE item_name LIKE ?
                    ORDER BY price ASC
                    LIMIT ?
                    """,
                    (f"%{query}%", limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_db_size_bytes(self) -> int:
        if self.db_path.exists():
            return self.db_path.stat().st_size
        return 0

    def rotate_if_needed(self, max_bytes: int = 250 * 1024 * 1024) -> bool:
        """Delete old data if DB exceeds max_bytes. Keep only latest fetch."""
        size = self.get_db_size_bytes()
        if size <= max_bytes:
            return False
        logger.warning("Price DB size %d bytes exceeds limit %d, rotating...", size, max_bytes)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                DELETE FROM products WHERE fetched_at < (
                    SELECT MAX(fetched_at) FROM feed_metadata
                )
            """)
            conn.execute("VACUUM")
            conn.commit()
        logger.info("Price DB rotated, new size: %d bytes", self.get_db_size_bytes())
        return True

    def ingest_xml(self, xml_bytes: bytes, chain: str, store_id: str = "") -> int:
        """Parse XML feed bytes and insert products into the DB."""
        field_map = FIELD_MAPS.get(chain, FIELD_MAPS["shufersal"])

        text = None
        for encoding in ("utf-8-sig", "utf-8", "windows-1255", "iso-8859-8"):
            try:
                text = xml_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            logger.error("Could not decode XML for chain %s store %s", chain, store_id)
            return 0

        # Strip BOM if present
        text = text.lstrip("\ufeff")
        # Strip whitespace before XML declaration
        text = text.strip()
        # Remove XML declaration if present (ET.fromstring handles it, but strip to be safe)
        if text.startswith("<?xml"):
            idx = text.find("?>")
            if idx != -1:
                text = text[idx + 2:].strip()

        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            logger.error("XML parse error for chain %s: %s", chain, e)
            return 0

        items = root.findall(".//Item") or root.findall(".//Product")
        if not items:
            return 0

        rows = []
        for item_el in items:
            name = _extract_text(item_el, field_map["item_name"])
            price_str = _extract_text(item_el, field_map["price"])
            if not name or not price_str:
                continue
            rows.append((
                _extract_text(item_el, field_map["item_code"]),
                name,
                _extract_text(item_el, field_map["manufacturer"]),
                _safe_float(price_str),
                _safe_float(_extract_text(item_el, field_map["unit_price"])),
                _extract_text(item_el, field_map["quantity"]),
                _extract_text(item_el, field_map["unit_of_measure"]),
                1 if _extract_text(item_el, field_map["is_weighted"]) == "1" else 0,
                chain,
                store_id,
                _extract_text(item_el, field_map["update_date"]),
            ))

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO products (item_code, item_name, manufacturer, price, unit_price,
                   quantity, unit_of_measure, is_weighted, chain, store_id, update_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            conn.execute(
                "INSERT INTO feed_metadata (chain, store_id, item_count) VALUES (?, ?, ?)",
                (chain, store_id, len(rows)),
            )
            conn.commit()

        return len(rows)


def format_feed_results(results: list[dict], query: str, limit: int = 5) -> str:
    """Format price DB search results for chat display."""
    if not results:
        return ""

    seen_chains: dict[str, dict] = {}
    for r in results:
        chain = r["chain"]
        if chain not in seen_chains or r["price"] < seen_chains[chain]["price"]:
            seen_chains[chain] = r

    sorted_results = sorted(seen_chains.values(), key=lambda x: x["price"])[:limit]
    if not sorted_results:
        return ""

    chain_names = {
        "shufersal": "\u05e9\u05d5\u05e4\u05e8\u05e1\u05dc",
        "rami-levy": "\u05e8\u05de\u05d9 \u05dc\u05d5\u05d9",
        "yochananof": "\u05d9\u05d5\u05d7\u05e0\u05e0\u05d5\u05e3",
        "victory": "\u05d5\u05d9\u05e7\u05d8\u05d5\u05e8\u05d9",
        "osher-ad": "\u05d0\u05d5\u05e9\u05e8 \u05e2\u05d3",
        "mega": "\u05de\u05d2\u05d0",
        "tiv-taam": "\u05d8\u05d9\u05d1 \u05d8\u05e2\u05dd",
    }

    product_name = sorted_results[0].get("item_name", query)
    lines = [f"\u05de\u05d7\u05d9\u05e8 {product_name}:"]
    for i, r in enumerate(sorted_results, 1):
        chain_display = chain_names.get(r["chain"], r["chain"])
        lines.append(f"{i}. {chain_display} \u2014 \u20aa{r['price']:.2f}")

    if len(sorted_results) >= 2:
        spread = sorted_results[-1]["price"] - sorted_results[0]["price"]
        if spread > 0:
            pct = (spread / sorted_results[0]["price"]) * 100
            lines.append(f"\u05e4\u05e2\u05e8: {pct:.0f}%")

    lines.append("\u05de\u05e7\u05d5\u05e8: \u05e4\u05d9\u05d3 \u05e8\u05e9\u05de\u05d9 (\u05d7\u05d5\u05e7 \u05e9\u05e7\u05d9\u05e4\u05d5\u05ea \u05de\u05d7\u05d9\u05e8\u05d9\u05dd)")
    return "\n".join(lines)
