#!/usr/bin/env python3
"""Nightly price feed updater. Downloads latest feeds and populates the local price DB.

Usage: python scripts/update_prices.py
Designed to run as a cron job on HPG6.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import sys
from pathlib import Path

import requests

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations.feed_downloader import CHAIN_FEEDS, PriceDB  # noqa: E402  (import after sys.path setup)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PRICE_DB_PATH = PROJECT_ROOT / "data" / "prices.sqlite3"
MAX_DB_SIZE = 250 * 1024 * 1024  # 250MB - rotate if exceeded
MAX_FILES_PER_CHAIN = 3  # Only download a few store files per chain to keep size manageable


def fetch_feed_index(chain_config: dict, session: requests.Session) -> list[str]:
    """Fetch the list of available PriceFull files from a chain's index page."""
    import html as html_mod
    try:
        resp = session.get(chain_config["index_url"], timeout=30)
        resp.raise_for_status()
        # Shufersal uses Azure blob storage URLs; extract href values containing PriceFull
        raw_urls = re.findall(r'href="(https?://[^"]*PriceFull[^"]*\.gz[^"]*)"', resp.text, re.IGNORECASE)
        if not raw_urls:
            raw_urls = re.findall(r'href="([^"]*PriceFull[^"]*\.gz[^"]*)"', resp.text, re.IGNORECASE)
        # Decode HTML entities (e.g. &amp; -> &)
        urls = [html_mod.unescape(u) for u in raw_urls]
        base = chain_config["base_url"].rstrip("/")
        return [u if u.startswith("http") else f"{base}/{u.lstrip('/')}" for u in urls]
    except Exception as e:
        logger.error("Failed to fetch feed index for %s: %s", chain_config["name"], e)
        return []


def download_and_ingest(url: str, chain: str, db: PriceDB, session: requests.Session) -> int:
    """Download a single feed file and ingest it into the price DB."""
    try:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        raw = resp.content

        if ".gz" in url:
            raw = gzip.decompress(raw)

        store_match = re.search(r'(\d{3,})', url.split("/")[-1])
        store_id = store_match.group(1) if store_match else ""

        count = db.ingest_xml(raw, chain=chain, store_id=store_id)
        logger.info("Ingested %d items from %s (store %s)", count, chain, store_id)
        return count
    except Exception as e:
        logger.error("Failed to download/ingest %s: %s", url, e)
        return 0


def fetch_carrefour_feeds(session: requests.Session, db: PriceDB, max_stores: int = 5) -> int:
    """Download Carrefour price feeds by scraping their file listing page."""
    logger.info("Fetching Carrefour feeds...")
    try:
        resp = session.get("https://prices.carrefour.co.il", timeout=30)
        resp.raise_for_status()
        html = resp.text

        # Extract path and files JSON from the page
        path_match = re.search(r"const path = '(\d+)'", html)
        files_match = re.search(r"const files = (\[.*?\]);", html, re.DOTALL)
        if not path_match or not files_match:
            logger.error("Could not parse Carrefour file listing page")
            return 0

        path = path_match.group(1)
        files = json.loads(files_match.group(1))

        # Filter PriceFull files only
        price_files = [f for f in files if f["name"].startswith("PriceFull")]
        logger.info("Found %d PriceFull files for Carrefour", len(price_files))

        # Download up to max_stores files (pick different store IDs)
        seen_stores = set()
        total = 0
        for f in price_files:
            name = f["name"]
            # Extract store ID
            parts = name.replace("PriceFull", "").split("-")
            store_id = parts[1] if len(parts) > 1 else "unknown"
            if store_id in seen_stores:
                continue
            seen_stores.add(store_id)

            if len(seen_stores) > max_stores:
                break

            url = f"https://prices.carrefour.co.il/{path}/{name}"
            count = download_and_ingest(url, "carrefour", db, session)
            total += count

        logger.info("Carrefour total: %d items from %d stores", total, len(seen_stores))
        return total
    except Exception as e:
        logger.error("Carrefour feed fetch failed: %s", e)
        return 0


def _rotate_old_prices(db: PriceDB) -> None:
    """Delete price entries older than 7 days."""
    import sqlite3
    conn = sqlite3.connect(str(db.db_path), isolation_level=None)  # autocommit for VACUUM
    try:
        deleted = conn.execute("DELETE FROM products WHERE fetched_at < datetime('now', '-7 days')").rowcount
        if deleted:
            conn.execute('VACUUM')
            logger.info('Rotated %d old price entries', deleted)
    finally:
        conn.close()


def main():
    db = PriceDB(PRICE_DB_PATH)
    db.initialize()

    db.rotate_if_needed(MAX_DB_SIZE)

    # Rotate old prices (keep only last 7 days)
    _rotate_old_prices(db)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; PriceBot/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    adapter = requests.adapters.HTTPAdapter(max_retries=2)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    total_items = 0
    for chain_key, chain_config in CHAIN_FEEDS.items():
        logger.info("Fetching feeds for %s (%s)...", chain_config["name"], chain_key)
        urls = fetch_feed_index(chain_config, session)
        if not urls:
            logger.warning("No feed files found for %s", chain_key)
            continue

        logger.info(
            "Found %d feed files for %s, downloading up to %d",
            len(urls),
            chain_key,
            MAX_FILES_PER_CHAIN,
        )
        for url in urls[:MAX_FILES_PER_CHAIN]:
            count = download_and_ingest(url, chain_key, db, session)
            total_items += count

    # Carrefour (special handling - file list embedded in HTML)
    carrefour_items = fetch_carrefour_feeds(session, db, max_stores=5)
    total_items += carrefour_items


    size_mb = db.get_db_size_bytes() / (1024 * 1024)
    logger.info("Price update complete. Total items: %d, DB size: %.1f MB", total_items, size_mb)


if __name__ == "__main__":
    main()
