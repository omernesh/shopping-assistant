from src.domain.shopping_list import build_item


def test_build_item_assigns_category() -> None:
    item = build_item(raw_text="חלב", normalized_name="חלב")
    assert item.category == "מקרר"
