from __future__ import annotations

import io
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from src.config.settings import DEFAULT_CHP_BASE_URL, DEFAULT_CITY, DEFAULT_CITY_ID, DEFAULT_STREET_ID

# Fix UTF-8 output for Windows parity with the legacy script.
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

CITY_IDS: dict[str, int] = {
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
class StorePrice:
    chain: str
    store_name: str
    address: str
    price: float
    promotion: str | None = None
    is_online: bool = False
    website: str | None = None


@dataclass
class PriceSearchResult:
    product: str
    product_full_name: str | None = None
    barcode: str | None = None
    price_range: str | None = None
    city: str = DEFAULT_CITY
    stores: list[StorePrice] = field(default_factory=list)
    online_stores: list[StorePrice] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CHPClient:
    def __init__(self, timeout: int = 30, retries: int = 3, base_url: str = DEFAULT_CHP_BASE_URL):
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "he,en-US;q=0.7,en;q=0.3",
            }
        )

        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def resolve_city_id(self, city: str) -> int:
        return CITY_IDS.get(city, DEFAULT_CITY_ID)

    def search(
        self,
        product_name: str,
        city: str = DEFAULT_CITY,
        city_id: int | None = None,
        street_id: int = DEFAULT_STREET_ID,
        num_results: int = 20,
    ) -> PriceSearchResult:
        city_id = city_id or self.resolve_city_id(city)

        logger.info("Searching CHP for %r in %s (city_id=%s)", product_name, city, city_id)

        params = {
            "shopping_address": f"{city}+",
            "shopping_address_street_id": street_id,
            "shopping_address_city_id": city_id,
            "product_name_or_barcode": product_name,
            "product_barcode": 0,
            "from": 0,
            "num_results": num_results,
        }

        response = self.session.get(
            f"{self.base_url}/main_page/compare_results",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()

        product_name_from_api = self._extract_barcode_name(response.content, product_name)
        return self._parse_html(response.text, product_name, city, product_name_from_api)

    def _extract_barcode_name(self, raw_content: bytes, original_query: str) -> str | None:
        if not original_query.isdigit():
            return None

        idx = raw_content.find(b'displayed_product_name_and_contents')
        if idx <= 0:
            return None

        start = raw_content.find(b'value="', idx)
        if start < 0:
            return None
        start += 7
        end = raw_content.find(b'"', start)
        if end <= start:
            return None

        try:
            candidate = raw_content[start:end].decode("utf-8")
        except UnicodeDecodeError:
            return None

        return candidate if len(candidate) > 3 else None

    def _parse_html(
        self,
        html: str,
        product_name: str,
        city: str,
        product_name_from_api: str | None = None,
    ) -> PriceSearchResult:
        result = PriceSearchResult(product=product_name, city=city)
        if product_name_from_api:
            result.product_full_name = product_name_from_api

        for pattern in (
            r'>השוואת מחירים בסופרמרקטים של ([^<]+)<',
            r'<h2[^>]*>([^<]+)</h2>',
            r'<h3[^>]*>([^<]+(?:\([^)]*\))?[^<]*)',
        ):
            matches = re.findall(pattern, html)
            for match in matches:
                candidate = match.strip()
                if candidate and len(candidate) > 5 and 'ברקוד' not in candidate:
                    result.product_full_name = candidate
                    break
            if result.product_full_name:
                break

        barcode_match = re.search(r'ברקוד:\s*(\d+)', html)
        if barcode_match:
            result.barcode = barcode_match.group(1)

        range_match = re.search(r'פער בין היקר ביותר לזול ביותר:\s*(\d+)%', html)
        if range_match:
            result.price_range = f"{range_match.group(1)}%"

        result.stores = self._extract_stores(html, is_online=False)
        result.online_stores = self._extract_stores(html, is_online=True)
        return result

    def _extract_stores(self, html: str, is_online: bool) -> list[StorePrice]:
        if is_online:
            section_pattern = r'תוצאות מחנויות באינטרנט.*?רשת.*?שם.*?אתר.*?מבצע.*?מחיר(.*?)(?:</table|$)'
        else:
            section_pattern = r'מחירים בקרבת.*?רשת.*?כתובת.*?מבצע.*?מחיר(.*?)(?:תוצאות מחנויות באינטרנט|מחירים בקרבת|$)'

        section_match = re.search(section_pattern, html, re.DOTALL)
        if not section_match:
            return []

        section_html = section_match.group(1)
        row_pattern = r'<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([\d.]+)</td>'

        stores: list[StorePrice] = []
        for match in re.findall(row_pattern, section_html):
            try:
                price = float(match[4].strip())
            except ValueError:
                continue

            chain = match[0].strip()
            store_name = match[1].strip()
            address = match[2].strip()
            promotion = match[3].strip() or None
            stores.append(
                StorePrice(
                    chain=chain,
                    store_name=store_name,
                    address=address,
                    price=price,
                    promotion=promotion,
                    is_online=is_online,
                    website=address if is_online else None,
                )
            )

        return stores


def format_price_summary(result: PriceSearchResult, limit: int = 3) -> str:
    name = result.product_full_name or result.product
    physical = sorted(result.stores, key=lambda store: store.price)[:limit]

    lines = [f"מחיר עבור {name} ב{result.city}:"]
    if physical:
        for idx, store in enumerate(physical, start=1):
            lines.append(f"{idx}. {store.chain} — ₪{store.price:.2f}")
    else:
        lines.append("לא נמצאו תוצאות לחנויות פיזיות.")

    if result.price_range:
        lines.append(f"פער: {result.price_range}")

    if result.online_stores:
        cheapest_online = min(result.online_stores, key=lambda store: store.price)
        lines.append(f"אונליין זול ביותר: {cheapest_online.chain} — ₪{cheapest_online.price:.2f}")

    lines.append("מקור: CHP")
    return "\n".join(lines)
