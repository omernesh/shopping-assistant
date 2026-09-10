"""Feed-downloader regression tests added during the 2026-09 QA pass.

Covers: store-id extraction from PriceFull filenames/URLs, and replace-store
(snapshot) ingest semantics — a re-ingest of the same store must not grow the DB.
"""
import sqlite3

from src.integrations.feed_downloader import PriceDB, store_id_from_filename

SHUF_URL = (
    "https://pricesprodpublic.blob.core.windows.net/pricefull/"
    "PriceFull7290027600007-001-002-20260910-030000.gz"
    "?sv=2014-02-14&sr=b&sig=abc123DEF456"
)
CARREFOUR_NAME = "PriceFull7290055700007-001-009-20260910-051010.gz"

XML = (
    "<Root><Items>"
    "<Item><ItemCode>1</ItemCode><ItemName>חלב</ItemName><ItemPrice>5.9</ItemPrice></Item>"
    "<Item><ItemCode>2</ItemCode><ItemName>לחם</ItemName><ItemPrice>7.5</ItemPrice></Item>"
    "</Items></Root>"
)


def test_store_id_from_filename():
    # Azure SAS query string must be ignored; store id is the 3rd digit group.
    assert store_id_from_filename(SHUF_URL) == "002"
    assert store_id_from_filename(CARREFOUR_NAME) == "009"
    assert store_id_from_filename("no-numbers.gz") == ""


def test_ingest_replace_store_is_idempotent(tmp_path):
    db = PriceDB(tmp_path / "prices.sqlite3")
    db.initialize()
    payload = XML.encode("utf-8")

    assert db.ingest_xml(payload, chain="shufersal", store_id="002", replace_store=True) == 2
    # Re-ingest the same store: replace, don't append.
    assert db.ingest_xml(payload, chain="shufersal", store_id="002", replace_store=True) == 2

    with sqlite3.connect(tmp_path / "prices.sqlite3") as conn:
        count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        stores = {row[0] for row in conn.execute("SELECT DISTINCT store_id FROM products")}
    assert count == 2
    assert stores == {"002"}


def test_ingest_replace_store_keeps_other_stores(tmp_path):
    db = PriceDB(tmp_path / "prices.sqlite3")
    db.initialize()
    payload = XML.encode("utf-8")

    db.ingest_xml(payload, chain="shufersal", store_id="001", replace_store=True)
    db.ingest_xml(payload, chain="shufersal", store_id="002", replace_store=True)
    db.ingest_xml(payload, chain="shufersal", store_id="002", replace_store=True)

    with sqlite3.connect(tmp_path / "prices.sqlite3") as conn:
        rows = conn.execute("SELECT store_id, COUNT(*) FROM products GROUP BY store_id").fetchall()
    assert dict(rows) == {"001": 2, "002": 2}
