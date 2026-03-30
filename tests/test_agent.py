from src.agent.llm_client import LLMResponse, ToolCall
from src.agent.shopping_agent import ShoppingAgent
from src.app.router import MessageContext, ShoppingAssistantRouter
from src.storage.sqlite_store import SQLiteStore


class StubTransport:
    """Returns a pre-configured LLMResponse sequence."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = []

    def send(self, *, system: str, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        return self.responses.pop(0)


class FailingTransport:
    def send(self, *, system: str, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        raise RuntimeError("boom")


def build_router(tmp_path):
    store = SQLiteStore(tmp_path / "shopping.sqlite3")
    store.initialize()
    return ShoppingAssistantRouter(store=store)


def _ctx(text: str) -> MessageContext:
    return MessageContext(
        platform="telegram",
        external_chat_id="123:7",
        user_id="42",
        text=text,
        title="Shopping Group",
    )


def test_agent_tool_call_add_item(tmp_path) -> None:
    router = build_router(tmp_path)
    # Round 1: LLM calls add_item tool
    # Round 2: LLM responds with text confirmation
    transport = StubTransport([
        LLMResponse(
            tool_calls=[ToolCall(id="call_1", name="add_item", input={"item_name": "קולה זירו", "quantity": 2})],
            stop_reason="tool_use",
        ),
        LLMResponse(text="נוסף קולה זירו", stop_reason="end_turn"),
    ])
    agent = ShoppingAgent(router=router, transport=transport)

    response = agent.handle_message(_ctx("תביא 2 קולה זירו"))

    assert "קולה זירו" in response
    listed = router.handle_message(_ctx("?"))
    assert "קולה זירו" in listed
    assert len(transport.calls) == 2


def test_agent_text_only_ignore(tmp_path) -> None:
    router = build_router(tmp_path)
    transport = StubTransport([
        LLMResponse(text="", stop_reason="end_turn"),
    ])
    agent = ShoppingAgent(router=router, transport=transport)

    response = agent.handle_message(_ctx("מה נשמע?"))

    assert response == ""
    listed = router.handle_message(_ctx("?"))
    assert listed == "הרשימה ריקה"


def test_agent_falls_back_to_router_when_transport_fails(tmp_path) -> None:
    router = build_router(tmp_path)
    agent = ShoppingAgent(router=router, transport=FailingTransport())

    response = agent.handle_message(_ctx("2 חלב"))

    assert "נוסף" in response
    listed = router.handle_message(_ctx("?"))
    assert "חלב" in listed


def test_agent_no_transport_uses_parser(tmp_path) -> None:
    router = build_router(tmp_path)
    agent = ShoppingAgent(router=router, transport=None)

    response = agent.handle_message(_ctx("2 חלב"))

    assert "נוסף" in response


def test_clear_list_action(tmp_path) -> None:
    router = build_router(tmp_path)
    ctx = _ctx("חלב")
    # Add an item first via semantic action
    router.handle_semantic_action(ctx, action="add", item_name="חלב")

    # Now clear via tool call
    transport = StubTransport([
        LLMResponse(
            tool_calls=[ToolCall(id="call_1", name="clear_list", input={})],
            stop_reason="tool_use",
        ),
        LLMResponse(text="הרשימה נוקתה", stop_reason="end_turn"),
    ])
    agent2 = ShoppingAgent(router=router, transport=transport)
    response = agent2.handle_message(_ctx("נקה את הרשימה"))

    assert "נוקתה" in response
    listed = router.handle_message(_ctx("?"))
    assert listed == "הרשימה ריקה"
