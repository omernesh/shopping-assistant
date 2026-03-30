from __future__ import annotations

import json
import logging
from typing import Protocol

from src.agent.llm_client import SYSTEM_PROMPT, TOOLS, LLMConfig, LLMResponse, LLMTransport, ToolCall
from src.app.router import MessageContext, ShoppingAssistantRouter, DuplicateConflict

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5


class ShoppingAgent:
    def __init__(self, router: ShoppingAssistantRouter, transport: LLMTransport | None = None):
        self.router = router
        self.transport = transport
        self.pending_conflicts: dict[str, DuplicateConflict] = {}  # keyed by external_chat_id

    def handle_message(self, context: MessageContext) -> str:
        if self.transport is None:
            return self.router.handle_message(context)

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

        # If we exhaust rounds, return whatever text we have
        return response.text.strip() if response.text else ""

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
                    self.pending_conflicts[context.external_chat_id] = result
                    existing = result.existing_item
                    eq = int(existing.quantity_value) if existing.quantity_value and existing.quantity_value.is_integer() else existing.quantity_value
                    nq = int(result.new_quantity) if result.new_quantity and result.new_quantity == int(result.new_quantity) else result.new_quantity
                    return (
                        f"\u05db\u05d1\u05e8 \u05d9\u05e9 \u05d1\u05e8\u05e9\u05d9\u05de\u05d4 \u05e4\u05e8\u05d9\u05d8 \u05d3\u05d5\u05de\u05d4: {existing.normalized_name}"
                        + (f" (\u05db\u05de\u05d5\u05ea: {eq})" if eq else "")
                        + f". \u05d1\u05d9\u05e7\u05e9\u05ea \u05dc\u05d4\u05d5\u05e1\u05d9\u05e3 {result.new_item_name}"
                        + (f" (\u05db\u05de\u05d5\u05ea: {nq})" if nq else "")
                        + ". \u05d4\u05e4\u05e8\u05d9\u05d8 \u05dc\u05d0 \u05e0\u05d5\u05e1\u05e3 \u05e2\u05d3\u05d9\u05d9\u05df \u2014 \u05de\u05d7\u05db\u05d4 \u05dc\u05d4\u05d7\u05dc\u05d8\u05ea \u05d4\u05de\u05e9\u05ea\u05de\u05e9."
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
            else:
                return f"Unknown tool: {name}"
        except Exception as exc:
            logger.exception("Tool %s failed: %s", name, exc)
            return f"Error: {exc}"
