"""Store-level regression tests added during the 2026-09 QA pass.

Covers: connection lifecycle (closed after `with`), active-list flow, and
LIKE-wildcard escaping in the price DB search.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.integrations.feed_downloader import PriceDB
from src.storage.sqlite_store import SQLiteStore


def _store(tmp_path):
    store = SQLiteStore(tmp_path / "store.sqlite3")
    store.initialize()
    return store


def test_connect_closes_connection_on_exit(tmp_path):
    """A sqlite3 connection left the FD open before the connect() contextmanager fix."""
    store = _store(tmp_path)
    with store.connect() as conn:
        conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_active_list_flow(tmp_path):
    store = _store(tmp_path)
    chat = store.ensure_chat(platform="telegram", external_chat_id="qa-chat", title="QA")

    list_id = store.get_active_list_id(chat.id)
    assert list_id > 0
    assert store.get_active_list_id(chat.id) == list_id  # stable on repeat

    party_id = store.create_list(chat_id=chat.id, name="party")
    store.set_active_list(chat_id=chat.id, list_id=party_id)
    assert store.get_active_list_id(chat.id) == party_id

    assert {entry["name"] for entry in store.get_all_lists(chat.id)} == {"main", "party"}


def test_search_product_escapes_like_wildcards(tmp_path):
    """'%' in a query must be literal, not a wildcard (mirrors storage's escaping)."""
    db = PriceDB(tmp_path / "prices.sqlite3")
    db.initialize()
    xml = (
        "<Root><Items>"
        "<Item><ItemCode>1</ItemCode><ItemName>אבוקדו</ItemName><ItemPrice>5.9</ItemPrice></Item>"
        "<Item><ItemCode>2</ItemCode><ItemName>חלב 3%</ItemName><ItemPrice>6.5</ItemPrice></Item>"
        "</Items></Root>"
    )
    count = db.ingest_xml(xml.encode("utf-8"), chain="shufersal", store_id="qa-store")
    assert count == 2

    # If '%' acted as a wildcard, 'אב%' would prefix-match 'אבוקדו'.
    assert db.search_product("אב%") == []
    assert db.find_matching_products("אב%") == []

    # A literal '%' search must still find the product that contains one.
    literal = db.search_product("חלב 3%")
    assert literal and literal[0]["item_name"] == "חלב 3%"

    # Plain queries keep working.
    hits = db.search_product("אבו")
    assert hits and hits[0]["item_name"] == "אבוקדו"
