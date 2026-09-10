from src.agent.llm_client import TOOLS, LLMConfig, LLMTransport


def test_all_tools_are_openai_function_shape() -> None:
    """Regression: a trailing block of Anthropic-shape ({name, input_schema}) duplicates
    used to be appended after the OpenAI-shape entries and sent verbatim to the
    /v1/chat/completions `tools` param."""
    assert len(TOOLS) == 16
    for entry in TOOLS:
        assert entry.get("type") == "function", entry
        assert "function" in entry and "name" in entry["function"], entry
        assert "input_schema" not in entry


def test_tool_names_are_unique() -> None:
    names = [t["function"]["name"] for t in TOOLS]
    assert len(names) == len(set(names)), names


def test_parse_response_drops_truncated_tool_call() -> None:
    transport = LLMTransport(LLMConfig(api_key="x"))
    body = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"name": "add_item", "arguments": '{"item_name": "ח'},
                        }
                    ],
                },
            }
        ]
    }
    result = transport._parse_response(body)
    assert result.tool_calls == []
    assert result.stop_reason == "length"


def test_parse_response_keeps_valid_empty_args() -> None:
    transport = LLMTransport(LLMConfig(api_key="x"))
    body = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "clear_list", "arguments": "{}"}}
                    ],
                },
            }
        ]
    }
    result = transport._parse_response(body)
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].input == {}


def test_transport_mounts_retry_adapter() -> None:
    transport = LLMTransport(LLMConfig(api_key="x"))
    adapter = transport.session.get_adapter("https://api.example.com")
    assert adapter.max_retries.total == 2
    assert 503 in adapter.max_retries.status_forcelist
