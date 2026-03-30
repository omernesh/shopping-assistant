from __future__ import annotations

import json
import logging
from src.agent.llm_client import SYSTEM_PROMPT, TOOLS, LLMConfig, LLMResponse, LLMTransport, ToolCall
from src.app.router import MessageContext, ShoppingAssistantRouter, DuplicateConflict

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5


class ShoppingAgent:
    def __init__(self, router: ShoppingAssistantRouter, transport: LLMTransport | None = None):
        self.router = router
        self.transport = transport
        self.pending_conflicts: dict[str, DuplicateConflict] = {}  # keyed by external_chat_id

    def _should_skip_llm(self, text: str) -> bool:
        """Quick pre-filter to avoid wasting LLM calls on obviously non-shopping messages."""
        stripped = text.strip()
        if not stripped:
            return True
        if len(stripped) > 500:
            return True  # Too long for a shopping item
        # Pure numbers
        if stripped.replace(".", "").replace(",", "").isdigit():
            return True
        # URLs
        if stripped.startswith("http://") or stripped.startswith("https://"):
            return True
        return False

    def handle_message(self, context: MessageContext) -> str:
        if self.transport is None:
            return self.router.handle_message(context)

        # Skip LLM for obviously non-shopping messages
        if self._should_skip_llm(context.text):
            return ""

        try:
            return self._run_tool_loop(context)
        except Exception as exc:
            logger.exception("LLM agent failed, falling back to parser: %s", exc)
            return self.router.handle_message(context)

    def _run_tool_loop(self, context: MessageContext) -> str:
        # Build context for the LLM
        list_preview = self.router.preview_list(context)
        default_city = self.router.get_default_city(context)

        system = SYSTEM_PROMPT + f"\n\nרשימה נוכחית:\n{list_preview}\n\nעיר ברירת מחדל: {default_city}"

        messages = [{"role": "user", "content": context.text}]

        for _round in range(MAX_TOOL_ROUNDS):
            response = self.transport.send(system=system, messages=messages, tools=TOOLS)

            if not response.tool_calls:
                # No tool calls — return text or empty (ignore)
                return response.text.strip()

            # Execute each tool call and build tool_result messages
            # First, add the assistant message with tool_use blocks
            assistant_content = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                })
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools and add results
            tool_results = []
            for tc in response.tool_calls:
                result = self._execute_tool(context, tc)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result,
                })
            messages.append({"role": "user", "content": tool_results})

        # If we exhaust rounds, log and return a user-facing message
        logger.warning("Tool loop exhausted %d rounds for message: %s", MAX_TOOL_ROUNDS, context.text[:100])
        return response.text.strip() if response.text else "לא הצלחתי לעבד את הבקשה — נסה שוב"

    def _execute_tool(self, context: MessageContext, tool_call: ToolCall) -> str:
        name = tool_call.name
        args = tool_call.input
        logger.info("Executing tool %s with args %s", name, args)

        try:
            if name == "show_list":
                return self.router.handle_semantic_action(context, action="show")
            elif name == "add_item":
                result = self.router.add_item_with_duplicate_check(
                    context,
                    item_name=args.get("item_name", ""),
                    quantity=args.get("quantity"),
                )
                if isinstance(result, DuplicateConflict):
                    # Cap at 100 entries
                    if len(self.pending_conflicts) > 100:
                        self.pending_conflicts.clear()
                    self.pending_conflicts[context.external_chat_id] = result
                    existing = result.existing_item
                    eq = existing.quantity_value
                    eq_display = int(eq) if eq and eq == int(eq) else eq
                    return (
                        f"נמצא פריט דומה ברשימה: {existing.normalized_name}"
                        + (f" ({eq_display})" if eq_display else "")
                        + ". מחכה לבחירת המשתמש."
                    )
                return result
            elif name == "mark_purchased":
                return self.router.handle_semantic_action(
                    context, action="done",
                    item_name=args.get("item_name", ""),
                )
            elif name == "delete_item":
                return self.router.handle_semantic_action(
                    context, action="delete",
                    item_name=args.get("item_name", ""),
                )
            elif name == "clear_list":
                return self.router.handle_semantic_action(context, action="clear")
            elif name == "set_city":
                return self.router.handle_semantic_action(
                    context, action="city",
                    city=args.get("city", ""),
                )
            elif name == "price_lookup":
                return self.router.handle_semantic_action(
                    context, action="price",
                    item_name=args.get("item_name", ""),
                )
            elif name == "list_user_items":
                return self.router.list_items_by_user_name(
                    context,
                    user_name=args.get("user_name", ""),
                )
            elif name == "estimate_list_cost":
                return self.router.estimate_list_cost(context)
            elif name == "compare_list_by_chain":
                return self.router.compare_list_by_chain(context)
            else:
                return f"Unknown tool: {name}"
        except Exception as exc:
            logger.exception("Tool %s failed: %s", name, exc)
            return f"Error: {exc}"
