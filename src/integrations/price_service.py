"""Unified price service combining CHP and local price DB."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ItemPrice:
    item_name: str
    quantity: float
    best_price: float | None = None
    best_store: str | None = None
    chain_prices: dict[str, float] = field(default_factory=dict)  # chain_name -> price
    source: str = ""  # "chp" or "feed"


@dataclass
class ListEstimate:
    items: list[ItemPrice]
    total: float
    items_priced: int
    items_missing: int


@dataclass
class ChainComparison:
    chain_totals: dict[str, float]  # chain_name -> total for the whole list
    items: list[ItemPrice]
    items_priced: int
    items_missing: int
    cheapest_chain: str
    cheapest_total: float
    most_expensive_chain: str
    most_expensive_total: float


class PriceService:
    """Combines CHP and local price DB to provide list-level pricing."""

    def __init__(self, chp_client=None, price_db=None):
        self.chp_client = chp_client
        self.price_db = price_db

    def lookup_item_prices(self, item_name: str, quantity: float = 1, city: str = "\u05d9\u05d1\u05e0\u05d4") -> ItemPrice:
        """Look up prices for a single item across chains."""
        result = ItemPrice(item_name=item_name, quantity=quantity)

        # Try CHP first (has multi-chain data)
        if self.chp_client:
            try:
                chp_result = self.chp_client.search(item_name, city=city)
                if chp_result.stores:
                    for store in chp_result.stores:
                        name = store.store_name or "unknown"
                        chain_name = self._normalize_chain_name(name)
                        price = store.price
                        if price and (chain_name not in result.chain_prices or price < result.chain_prices[chain_name]):
                            result.chain_prices[chain_name] = price

                    if result.chain_prices:
                        cheapest = min(result.chain_prices.items(), key=lambda x: x[1])
                        result.best_price = cheapest[1]
                        result.best_store = cheapest[0]
                        result.source = "chp"
                        return result
            except Exception as exc:
                logger.warning("CHP lookup failed for %s: %s", item_name, exc)

        # Fall back to local price DB
        if self.price_db:
            try:
                rows = self.price_db.search_product(item_name, limit=20)
                if rows:
                    for row in rows:
                        chain = row.get("chain", "unknown")
                        chain_display = self._chain_id_to_name(chain)
                        price = row["price"]
                        if chain_display not in result.chain_prices or price < result.chain_prices[chain_display]:
                            result.chain_prices[chain_display] = price

                    if result.chain_prices:
                        cheapest = min(result.chain_prices.items(), key=lambda x: x[1])
                        result.best_price = cheapest[1]
                        result.best_store = cheapest[0]
                        result.source = "feed"
                        return result
            except Exception as exc:
                logger.warning("Price DB lookup failed for %s: %s", item_name, exc)

        return result

    def estimate_list_cost(self, items: list[tuple[str, float]], city: str = "\u05d9\u05d1\u05e0\u05d4") -> ListEstimate:
        """Estimate the total cost of a shopping list using best available prices."""
        priced_items = []
        total = 0.0
        missing = 0

        for item_name, quantity in items:
            ip = self.lookup_item_prices(item_name, quantity, city)
            priced_items.append(ip)
            if ip.best_price is not None:
                total += ip.best_price * quantity
            else:
                missing += 1

        return ListEstimate(
            items=priced_items,
            total=total,
            items_priced=len(items) - missing,
            items_missing=missing,
        )

    def compare_list_by_chain(self, items: list[tuple[str, float]], city: str = "\u05d9\u05d1\u05e0\u05d4") -> ChainComparison | None:
        """Compare the total list cost across different store chains."""
        all_item_prices = []
        missing = 0
        chain_totals: dict[str, float] = {}
        chain_item_counts: dict[str, int] = {}

        for item_name, quantity in items:
            ip = self.lookup_item_prices(item_name, quantity, city)
            all_item_prices.append(ip)

            if not ip.chain_prices:
                missing += 1
                continue

            for chain, price in ip.chain_prices.items():
                chain_totals[chain] = chain_totals.get(chain, 0) + price * quantity
                chain_item_counts[chain] = chain_item_counts.get(chain, 0) + 1

        if not chain_totals:
            return None

        # Only include chains that have prices for at least 50% of items
        min_items = max(1, (len(items) - missing) // 2)
        qualified_chains = {k: v for k, v in chain_totals.items() if chain_item_counts.get(k, 0) >= min_items}

        if not qualified_chains:
            qualified_chains = chain_totals

        cheapest = min(qualified_chains.items(), key=lambda x: x[1])
        most_expensive = max(qualified_chains.items(), key=lambda x: x[1])

        return ChainComparison(
            chain_totals=dict(sorted(qualified_chains.items(), key=lambda x: x[1])),
            items=all_item_prices,
            items_priced=len(items) - missing,
            items_missing=missing,
            cheapest_chain=cheapest[0],
            cheapest_total=cheapest[1],
            most_expensive_chain=most_expensive[0],
            most_expensive_total=most_expensive[1],
        )

    @staticmethod
    def _normalize_chain_name(store_name: str) -> str:
        """Extract chain name from a CHP store name."""
        name = store_name.split("\u2014")[0].split("-")[0].strip()
        chains = {
            "\u05e8\u05de\u05d9 \u05dc\u05d5\u05d9": "\u05e8\u05de\u05d9 \u05dc\u05d5\u05d9",
            "\u05e9\u05d5\u05e4\u05e8\u05e1\u05dc": "\u05e9\u05d5\u05e4\u05e8\u05e1\u05dc",
            "\u05de\u05d2\u05d4": "\u05de\u05d2\u05d4",
            "\u05d5\u05d9\u05e7\u05d8\u05d5\u05e8\u05d9": "\u05d5\u05d9\u05e7\u05d8\u05d5\u05e8\u05d9",
            "\u05d9\u05d5\u05d7\u05e0\u05e0\u05d5\u05e3": "\u05d9\u05d5\u05d7\u05e0\u05e0\u05d5\u05e3",
            "\u05d0\u05d5\u05e9\u05e8 \u05e2\u05d3": "\u05d0\u05d5\u05e9\u05e8 \u05e2\u05d3",
            "\u05d7\u05e6\u05d9 \u05d7\u05d9\u05e0\u05dd": "\u05d7\u05e6\u05d9 \u05d7\u05d9\u05e0\u05dd",
            "\u05d8\u05d9\u05d1 \u05d8\u05e2\u05dd": "\u05d8\u05d9\u05d1 \u05d8\u05e2\u05dd",
            "\u05d9\u05d9\u05e0\u05d5\u05ea \u05d1\u05d9\u05ea\u05df": "\u05d9\u05d9\u05e0\u05d5\u05ea \u05d1\u05d9\u05ea\u05df",
            "\u05e1\u05d5\u05e4\u05e8 \u05e4\u05d0\u05e8\u05dd": "\u05e1\u05d5\u05e4\u05e8 \u05e4\u05d0\u05e8\u05dd",
        }
        for key, display in chains.items():
            if key in store_name:
                return display
        return name

    @staticmethod
    def _chain_id_to_name(chain_id: str) -> str:
        names = {
            "shufersal": "\u05e9\u05d5\u05e4\u05e8\u05e1\u05dc",
            "rami-levy": "\u05e8\u05de\u05d9 \u05dc\u05d5\u05d9",
            "yochananof": "\u05d9\u05d5\u05d7\u05e0\u05e0\u05d5\u05e3",
            "victory": "\u05d5\u05d9\u05e7\u05d8\u05d5\u05e8\u05d9",
            "osher-ad": "\u05d0\u05d5\u05e9\u05e8 \u05e2\u05d3",
            "mega": "\u05de\u05d2\u05d4",
            "tiv-taam": "\u05d8\u05d9\u05d1 \u05d8\u05e2\u05dd",
        }
        return names.get(chain_id, chain_id)


def format_list_estimate(estimate: ListEstimate) -> str:
    """Format a list cost estimate for chat display."""
    lines = ["\u05d4\u05e2\u05e8\u05db\u05ea \u05e2\u05dc\u05d5\u05ea \u05d4\u05e8\u05e9\u05d9\u05de\u05d4 (\u05de\u05d7\u05d9\u05e8 \u05d4\u05db\u05d9 \u05d6\u05d5\u05dc \u05dc\u05db\u05dc \u05e4\u05e8\u05d9\u05d8):\n"]
    for ip in estimate.items:
        q = int(ip.quantity) if ip.quantity == int(ip.quantity) else ip.quantity
        if ip.best_price is not None:
            item_total = ip.best_price * ip.quantity
            lines.append(f"\u2022 {ip.item_name} x{q} \u2014 \u20aa{item_total:.2f} ({ip.best_store})")
        else:
            lines.append(f"\u2022 {ip.item_name} x{q} \u2014 \u05dc\u05d0 \u05e0\u05de\u05e6\u05d0 \u05de\u05d7\u05d9\u05e8")

    lines.append(f"\n\u05e1\u05d4\"\u05db: \u20aa{estimate.total:.2f}")
    if estimate.items_missing > 0:
        lines.append(f"({estimate.items_missing} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd \u05dc\u05dc\u05d0 \u05de\u05d7\u05d9\u05e8)")
    return "\n".join(lines)


def format_chain_comparison(comparison: ChainComparison) -> str:
    """Format a chain comparison for chat display."""
    lines = ["\u05d4\u05e9\u05d5\u05d5\u05d0\u05ea \u05de\u05d7\u05d9\u05e8\u05d9 \u05d4\u05e8\u05e9\u05d9\u05de\u05d4 \u05d1\u05d9\u05df \u05e8\u05e9\u05ea\u05d5\u05ea:\n"]

    for i, (chain, total) in enumerate(comparison.chain_totals.items(), 1):
        marker = " \u2190 \u05d4\u05db\u05d9 \u05d6\u05d5\u05dc" if chain == comparison.cheapest_chain else ""
        lines.append(f"{i}. {chain} \u2014 \u20aa{total:.2f}{marker}")

    if comparison.cheapest_total and comparison.most_expensive_total and comparison.cheapest_chain != comparison.most_expensive_chain:
        savings = comparison.most_expensive_total - comparison.cheapest_total
        pct = (savings / comparison.most_expensive_total) * 100
        lines.append(f"\n\u05d7\u05d9\u05e1\u05db\u05d5\u05df \u05e4\u05d5\u05d8\u05e0\u05e6\u05d9\u05d0\u05dc\u05d9: \u20aa{savings:.2f} ({pct:.0f}%)")

    if comparison.items_missing > 0:
        lines.append(f"({comparison.items_missing} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd \u05dc\u05dc\u05d0 \u05de\u05d7\u05d9\u05e8)")

    lines.append(f"\n\u05de\u05d1\u05d5\u05e1\u05e1 \u05e2\u05dc {comparison.items_priced} \u05e4\u05e8\u05d9\u05d8\u05d9\u05dd \u05de\u05ea\u05d5\u05de\u05d7\u05e8\u05d9\u05dd")
    return "\n".join(lines)
