from src.domain.parser import parse_message


def test_parse_quantity_add_message() -> None:
    parsed = parse_message("2 חלב")
    assert parsed.intent == "add"
    assert parsed.quantity == 2
    assert parsed.value == "חלב"


def test_parse_price_command() -> None:
    parsed = parse_message("מחיר קולה זירו")
    assert parsed.intent == "price"
    assert parsed.value == "קולה זירו"


def test_parse_ignores_slash_commands() -> None:
    parsed = parse_message("/chatid")
    assert parsed.intent == "ignore"


def test_parse_ignores_meta_chatter() -> None:
    parsed = parse_message("sammie, did you see the reply of the shopping assistant bot?")
    assert parsed.intent == "ignore"


def test_parse_ignores_long_multiline_prompt() -> None:
    parsed = parse_message("You are joining a Telegram group/topic as Sammie\n- observe\n- help debug")
    assert parsed.intent == "ignore"
