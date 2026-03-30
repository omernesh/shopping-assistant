#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHP Price Scraper - Search for product prices in Israeli supermarkets
Default location: יבנה (Yavne)

Usage:
    python chp_price_scraper.py "מלפפונים"           # Search in יבנה
    python chp_price_scraper.py "חלב" "תל אביב"      # Search in תל אביב
    python chp_price_scraper.py -c יבנה "ביצים"     # With city flag
"""
import sys
import io
import argparse
import logging
from dataclasses import dataclass, field
from typing import Optional

# Fix UTF-8 output for Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import requests
from requests.adapters import HTTPAdapter
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Configuration
DEFAULT_CITY = "יבנה"
DEFAULT_CITY_ID = 2660
DEFAULT_STREET_ID = 9000
BASE_URL = "https://chp.co.il"

# User-Agent constant
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Known city IDs (can be expanded)
CITY_IDS = {
    "יבנה": 2660,
    "תל אביב": 1700,
    "ירושלים": 1000,
    "חיפה": 4000,
    "באר שבע": 9000,
    "רמת גן": 5200,
    "פתח תקווה": 5600,
    "ראשון לציון": 6200,
    "נתניה": 7500,
    "אשדוד": 2000,
    "רמת השרון": 7800,
    "הרצליה": 2900,
    "כפר סבא": 4400,
    "נהריה": 7300,
    "אילת": 6600,
    "בת ים": 2650,
}


@dataclass
class Store:
    """Represents a store with price information."""
    chain: str
    store_name: str
    address: str
    price: float
    promotion: Optional[str] = None
    is_online: bool = False
    website: Optional[str] = None


@dataclass
class PriceSearchResult:
    """Results from a price search."""
    product: str
    product_full_name: Optional[str] = None
    barcode: Optional[str] = None
    price_range: Optional[str] = None
    city: str = DEFAULT_CITY
    stores: list = field(default_factory=list)
    online_stores: list = field(default_factory=list)


class CHPPriceScraper:
    """Scraper for CHP.co.il price comparison."""

    def __init__(self, timeout: int = 30, retries: int = 3):
        """Initialize the scraper with session and retry logic."""
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "he,en-US;q=0.7,en;q=0.3",
        })

        # Add retry adapter
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        self.timeout = timeout

    def search(
        self,
        product_name: str,
        city: str = DEFAULT_CITY,
        city_id: int = DEFAULT_CITY_ID,
        street_id: int = DEFAULT_STREET_ID,
        num_results: int = 20
    ) -> PriceSearchResult:
        """Search for a product price in supermarkets near a city."""

        logger.info(f"Searching for '{product_name}' in {city} (city_id={city_id})")

        params = {
            "shopping_address": f"{city}+",
            "shopping_address_street_id": street_id,
            "shopping_address_city_id": city_id,
            "product_name_or_barcode": product_name,
            "product_barcode": 0,
            "from": 0,
            "num_results": num_results
        }

        url = f"{BASE_URL}/main_page/compare_results"

        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise

        # Get raw content to handle Hebrew encoding properly
        raw_content = response.content

        # Extract product name from hidden field if barcode search
        product_name_from_api = None
        if product_name.isdigit():
            idx = raw_content.find(b'displayed_product_name_and_contents')
            if idx > 0:
                start = raw_content.find(b'value="', idx) + 7
                end = raw_content.find(b'"', start)
                if end > start:
                    raw_name = raw_content[start:end]
                    try:
                        product_name_from_api = raw_name.decode('utf-8')
                        if product_name_from_api and len(product_name_from_api) > 3:
                            logger.info(f"Product name from API: {product_name_from_api}")
                    except:
                        pass

        return self._parse_html(response.text, product_name, city, product_name_from_api)

    def _parse_html(self, html: str, product_name: str, city: str, product_name_from_api: Optional[str] = None) -> PriceSearchResult:
        """Parse the HTML response to extract price information."""

        result = PriceSearchResult(product=product_name, city=city)

        # Use product name from API if available (barcode searches)
        if product_name_from_api:
            result.product_full_name = product_name_from_api

        # Try multiple patterns to extract product name from HTML
        name_patterns = [
            r'>השוואת מחירים בסופרמרקטים של ([^<]+)<',
            r'<h2[^>]*>([^<]+)</h2>',
            r'<h3[^>]*>([^<]+(?:\([^)]*\))?[^<]*)',
        ]

        for pattern in name_patterns:
            matches = re.findall(pattern, html)
            for match in matches:
                clean_match = match.strip()
                if clean_match and len(clean_match) > 5 and 'ברקוד' not in clean_match:
                    result.product_full_name = clean_match
                    break
            if result.product_full_name:
                break

        # Extract barcode
        barcode_match = re.search(r'ברקוד:\s*(\d+)', html)
        if barcode_match:
            result.barcode = barcode_match.group(1)

        # Extract price range
        range_match = re.search(r'פער בין היקר ביותר לזול ביותר:\s*(\d+)%', html)
        if range_match:
            result.price_range = f"{range_match.group(1)}%"

        # Extract physical stores
        result.stores = self._extract_stores(html, is_online=False)

        # Extract online stores
        result.online_stores = self._extract_stores(html, is_online=True)

        logger.info(f"Found {len(result.stores)} physical stores and {len(result.online_stores)} online stores")

        return result

    def _extract_stores(self, html: str, is_online: bool) -> list[Store]:
        """Extract store information from HTML."""

        stores = []

        # Define section patterns based on online/offline
        if is_online:
            section_pattern = r'תוצאות מחנויות באינטרנט.*?רשת.*?שם.*?אתר.*?מבצע.*?מחיר(.*?)(?:</table|$)'
        else:
            section_pattern = r'מחירים בקרבת.*?רשת.*?כתובת.*?מבצע.*?מחיר(.*?)(?:תוצאות מחנויות באינטרנט|מחירים בקרבת|$)'

        section_match = re.search(section_pattern, html, re.DOTALL)

        if not section_match:
            return stores

        section_html = section_match.group(1)

        # Extract table rows
        row_pattern = r'<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([\d.]+)</td>'

        for match in re.findall(row_pattern, section_html):
            chain = match[0].strip()
            store_name = match[1].strip()
            address = match[2].strip()
            promotion = match[3].strip() or None
            price_str = match[4].strip()

            try:
                price = float(price_str)
            except ValueError:
                continue

            store = Store(
                chain=chain,
                store_name=store_name,
                address=address,
                price=price,
                promotion=promotion,
                is_online=is_online,
                website=address if is_online else None
            )
            stores.append(store)

        return stores


def format_results(result: PriceSearchResult) -> str:
    """Format the search results as a readable string."""

    lines = []

    # Product header
    product_name = result.product_full_name or result.product
    lines.append(f"🔍 Product: {product_name}")

    if result.barcode:
        lines.append(f"   Barcode: {result.barcode}")

    if result.price_range:
        lines.append(f"   Price Range: {result.price_range}")

    lines.append("")

    # Physical stores
    lines.append(f"🏪 Physical Stores in {result.city}:")
    lines.append("-" * 50)

    if result.stores:
        sorted_stores = sorted(result.stores, key=lambda x: x.price)
        for store in sorted_stores:
            promo = f" 🎉 {store.promotion}" if store.promotion else ""
            lines.append(f"   ₪{store.price:.2f} - {store.chain} ({store.store_name}){promo}")
    else:
        lines.append("   No physical store results found")

    # Online stores
    if result.online_stores:
        lines.append("")
        lines.append("🌐 Online Stores:")
        lines.append("-" * 50)
        sorted_online = sorted(result.online_stores, key=lambda x: x.price)
        for store in sorted_online:
            promo = f" 🎉 {store.promotion}" if store.promotion else ""
            lines.append(f"   ₪{store.price:.2f} - {store.chain}{promo}")
            if store.website:
                lines.append(f"      🌐 {store.website}")

    return "\n".join(lines)


def search_product_cli(args: argparse.Namespace) -> None:
    """CLI entry point for product search."""

    # Get city ID
    city_id = CITY_IDS.get(args.city, DEFAULT_CITY_ID)

    scraper = CHPPriceScraper(timeout=args.timeout)

    print(f"Searching for '{args.product}' in {args.city}...")
    print()

    try:
        result = scraper.search(
            product_name=args.product,
            city=args.city,
            city_id=city_id
        )
        print(format_results(result))
    except requests.exceptions.RequestException as e:
        print(f"❌ Error: Failed to fetch data: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def get_city_id(city_name: str) -> Optional[int]:
    """Get city ID by city name."""
    return CITY_IDS.get(city_name)


def main():
    """Main entry point with argument parsing."""

    parser = argparse.ArgumentParser(
        description="Search for product prices in Israeli supermarkets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "מלפפונים"
  %(prog)s "חלב" "תל אביב"
  %(prog)s -c יבנה "ביצים"
  %(prog)s --city "תל אביב" "חלב"
        """
    )

    parser.add_argument(
        "product",
        nargs="?",
        default="מלפפונים",
        help="Product name to search for (Hebrew)"
    )

    parser.add_argument(
        "city",
        nargs="?",
        default=DEFAULT_CITY,
        help=f"City to search in (default: {DEFAULT_CITY})"
    )

    parser.add_argument(
        "-c", "--city",
        dest="city_flag",
        metavar="CITY",
        help="Set city (overrides positional argument)"
    )

    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)"
    )

    parser.add_argument(
        "--list-cities",
        action="store_true",
        help="List available cities and exit"
    )

    args = parser.parse_args()

    # Handle --city flag
    if args.city_flag:
        args.city = args.city_flag

    # Handle --list-cities
    if args.list_cities:
        print("Available cities:")
        for city, city_id in sorted(CITY_IDS.items(), key=lambda x: x[1]):
            marker = " (default)" if city == DEFAULT_CITY else ""
            print(f"  {city} (ID: {city_id}){marker}")
        return

    # Validate city
    if args.city not in CITY_IDS:
        logger.warning(f"City '{args.city}' not in known cities, using default city ID")
        logger.info(f"Use --list-cities to see available cities")

    search_product_cli(args)


if __name__ == "__main__":
    main()
