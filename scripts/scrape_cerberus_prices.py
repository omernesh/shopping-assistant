#!/usr/bin/env python3
"""Scrape price feeds from Cerberus portals using Playwright DOM extraction.

The Cerberus FTP web client loads files into a DataTable. The JSON API requires
CSRF tokens, but we can extract file data directly from the rendered DOM.
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import logging
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations.feed_downloader import PriceDB  # noqa: E402  (import after sys.path setup)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PRICE_DB_PATH = PROJECT_ROOT / "data" / "prices.sqlite3"
CERBERUS_URL = "https://url.publishedprices.co.il"

CHAINS = {
    "TivTaam": {"username": "TivTaam", "chain_id": "tivtaam", "display": "טיב טעם"},
    "RamiLevi": {"username": "RamiLevi", "chain_id": "rami-levy", "display": "רמי לוי"},
    "Yochananof": {"username": "yohananof", "chain_id": "yochananof", "display": "יוחננוף"},
}


def scrape_chain(chain_name: str, config: dict, db: PriceDB, max_files: int = 3) -> int:
    logger.info("Scraping %s (%s)...", chain_name, config["display"])
    total_items = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        try:
            # Login
            page.goto(f"{CERBERUS_URL}/login", timeout=15000)
            page.fill("#username", config["username"])
            page.click("#login-button")
            page.wait_for_load_state("networkidle")
            time.sleep(2)

            if "/file" not in page.url:
                logger.error("Login failed for %s", chain_name)
                return 0

            logger.info("Logged in as %s", config["username"])

            # Wait for table to fully render
            page.wait_for_timeout(3000)

            # Extract all file links from the DOM
            # Files are in <table> rows with <a href="...">filename</a>
            file_links = page.evaluate("""() => {
                const links = [];
                document.querySelectorAll('table tbody tr').forEach(row => {
                    const a = row.querySelector('td a');
                    if (a) {
                        links.push({
                            name: a.textContent.trim(),
                            href: a.getAttribute('href')
                        });
                    }
                });
                return links;
            }""")

            logger.info("Found %d total files for %s", len(file_links), chain_name)

            # Filter for PriceFull/pricefull files
            # Cerberus uses lowercase: "pricefull" or "PriceFull"
            price_files = [f for f in file_links
                          if "pricefull" in f["name"].lower() or "price" in f["name"].lower()]

            # Prefer PriceFull over Price (PriceFull is the complete catalog)
            pricefull = [f for f in price_files if "pricefull" in f["name"].lower()]
            if pricefull:
                price_files = pricefull
                logger.info("Found %d PriceFull files", len(price_files))
            else:
                # Use regular price files (some chains use lowercase "price" prefix)
                price_files = [f for f in file_links if f["name"].lower().startswith("price")]
                price_files = [f for f in price_files if "promo" not in f["name"].lower()]
                logger.info("Found %d price files (no PriceFull)", len(price_files))

            if not price_files:
                logger.warning("No price files found for %s", chain_name)
                sample = [f["name"] for f in file_links[:10]]
                logger.info("Sample files: %s", sample)
                return 0

            # Deduplicate by store ID, pick latest per store
            # Filename pattern: price{chainId}-{storeId}-{datetime}.gz
            store_files: dict[str, dict] = {}
            for f in price_files:
                parts = re.findall(r'\d+', f["name"])
                store_id = parts[1] if len(parts) > 1 else "0"
                # Keep last (most recent) per store
                store_files[store_id] = f

            logger.info("Unique stores: %d", len(store_files))

            # Download up to max_files
            downloaded = 0
            for store_id, f in list(store_files.items())[:max_files]:
                href = f["href"]
                if not href.startswith("http"):
                    href = f"{CERBERUS_URL}{href}"

                logger.info("Downloading %s (store %s)...", f["name"], store_id)
                try:
                    # Download via browser fetch (keeps session)
                    raw_data = page.evaluate("""async (url) => {
                        const r = await fetch(url);
                        const buf = await r.arrayBuffer();
                        return Array.from(new Uint8Array(buf));
                    }""", href)

                    raw_bytes = bytes(raw_data)

                    # Decompress
                    with contextlib.suppress(gzip.BadGzipFile):
                        raw_bytes = gzip.decompress(raw_bytes)

                    # Ingest
                    count = db.ingest_xml(raw_bytes, chain=config["chain_id"], store_id=store_id)
                    total_items += count
                    downloaded += 1
                    logger.info("Ingested %d items from store %s", count, store_id)

                except Exception as e:
                    logger.error("Failed to download %s: %s", f["name"], e)

            logger.info("%s: %d items from %d stores", chain_name, total_items, downloaded)

        except Exception as e:
            logger.error("Error scraping %s: %s", chain_name, e)
        finally:
            ctx.close()
            browser.close()

    return total_items


def _rotate_old_prices(db: PriceDB) -> None:
    """Delete price entries older than 7 days."""
    import sqlite3
    conn = sqlite3.connect(str(db.db_path), isolation_level=None)  # autocommit for VACUUM
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        deleted = conn.execute("DELETE FROM products WHERE fetched_at < datetime('now', '-7 days')").rowcount
        if deleted:
            conn.execute('VACUUM')
            logger.info('Rotated %d old price entries', deleted)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Scrape Cerberus price portals")
    parser.add_argument("--chain", choices=list(CHAINS.keys()), help="Single chain only")
    parser.add_argument("--max-files", type=int, default=3, help="Max files per chain")
    parser.add_argument(
        "--db", type=Path, default=None, help="Price DB path (default: ./data/prices.sqlite3)"
    )
    args = parser.parse_args()

    db = PriceDB(args.db or PRICE_DB_PATH)
    db.initialize()

    chains = {args.chain: CHAINS[args.chain]} if args.chain else CHAINS
    total = 0

    for name, config in chains.items():
        count = scrape_chain(name, config, db, max_files=args.max_files)
        total += count

    # Prune entries older than 7 days AFTER fresh ingest -- a failed scrape
    # must never wipe existing prices.
    _rotate_old_prices(db)

    size_mb = db.get_db_size_bytes() / (1024 * 1024)
    logger.info("Done. Total: %d items, DB: %.1f MB", total, size_mb)


if __name__ == "__main__":
    main()
