from src.integrations.chp_client import CHPClient, PriceSearchResult, StorePrice, format_price_summary


def test_resolve_city_id_defaults_when_unknown() -> None:
    client = CHPClient()
    assert client.resolve_city_id("עיר לא קיימת") == 2660


def test_format_price_summary() -> None:
    result = PriceSearchResult(
        product="חלב",
        city="יבנה",
        stores=[StorePrice(chain="רמי לוי", store_name="יבנה", address="רחוב", price=6.9)],
    )
    text = format_price_summary(result)
    assert "רמי לוי" in text
    assert "מקור: CHP" in text
