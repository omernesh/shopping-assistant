from __future__ import annotations

import json
import logging
import sqlite3
from collections import OrderedDict
from src.agent.llm_client import SYSTEM_PROMPT, TOOLS, LLMConfig, LLMResponse, LLMTransport, ToolCall
from typing import Any

import requests

from src.app.router import MessageContext, ShoppingAssistantRouter, DuplicateConflict

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5


class ShoppingAgent:
    def __init__(self, router: ShoppingAssistantRouter, transport: LLMTransport | None = None, super_admin_id: str = ""):
        self.router = router
        self.transport = transport
        self.pending_conflicts: OrderedDict[str, DuplicateConflict] = OrderedDict()
        self.pending_price_choices: OrderedDict[str, Any] = OrderedDict()
        self.super_admin_id = super_admin_id

    def _is_super_admin(self, user_id: str) -> bool:
        """Check if a user is the super admin."""
        if not self.super_admin_id:
            return True  # No admin configured = no restriction
        return str(user_id) == self.super_admin_id

    def _should_skip_llm(self, text: str) -> bool:
        """Quick pre-filter to avoid wasting LLM calls on obviously non-shopping messages."""
        stripped = text.strip()
        if not stripped:
            return True
        if len(stripped) > 1000:
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
        active_list_name = self.router.get_active_list_display(context)

        system = (
            SYSTEM_PROMPT
            + f"\n\nרשימה פעילה: {active_list_name}"
            + f"\nפריטים ממתינים:\n{list_preview}"
            + f"\n\nעיר ברירת מחדל: {default_city}"
        )

        # If the current user is the super admin, let the LLM know
        if self._is_super_admin(context.user_id):
            system += "\n\nהמשתמש הנוכחי הוא מנהל הבוט — מותר לענות על שאלות טכניות."

        messages = [{"role": "user", "content": context.text}]

        for _round in range(MAX_TOOL_ROUNDS):
            response = self.transport.send(system=system, messages=messages, tools=TOOLS)

            if not response.tool_calls:
                # No tool calls -- return text or empty (ignore)
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
        return response.text.strip() if response.text else "\u05dc\u05d0 \u05d4\u05e6\u05dc\u05d7\u05ea\u05d9 \u05dc\u05e2\u05d1\u05d3 \u05d0\u05ea \u05d4\u05d1\u05e7\u05e9\u05d4 \u2014 \u05e0\u05e1\u05d4 \u05e9\u05d5\u05d1"

    def _get_chat_id(self, context: MessageContext) -> int:
        """Resolve the internal chat_id for multi-list operations."""
        chat = self.router.store.ensure_chat(
            platform=context.platform,
            external_chat_id=context.external_chat_id,
            title=context.title,
            default_city=self.router.default_city,
        )
        return chat.id

    def _evict_oldest(self, cache: OrderedDict, max_size: int = 100) -> None:
        """Evict the oldest entry if the cache exceeds max_size."""
        while len(cache) > max_size:
            cache.popitem(last=False)

    def _execute_tool(self, context: MessageContext, tool_call: ToolCall) -> str:
        name = tool_call.name
        args = tool_call.input
        logger.info("Executing tool %s with args %s", name, args)

        try:
            if name == "show_list":
                user_name = args.get("user_name", "")
                list_name = args.get("list_name", "")
                # Filter by user if provided
                if user_name:
                    return self.router.list_items_by_user_name(context, user_name=user_name)
                # Show a specific named list without switching the active list
                if list_name:
                    chat_id = self._get_chat_id(context)
                    sl = self.router.store.ensure_active_list(chat_id=chat_id, name=list_name)
                    return self.router._format_list(self.router.store.list_active_items(sl.id))
                return self.router.handle_semantic_action(context, action="show")

            elif name == "add_item":
                result = self.router.add_item_with_duplicate_check(
                    context,
                    item_name=args.get("item_name", ""),
                    quantity=args.get("quantity"),
                )
                if isinstance(result, DuplicateConflict):
                    self._evict_oldest(self.pending_conflicts)
                    self.pending_conflicts[context.external_chat_id] = result
                    existing = result.existing_item
                    eq = existing.quantity_value
                    eq_display = int(eq) if eq and eq == int(eq) else eq
                    return (
                        f"\u05e0\u05de\u05e6\u05d0 \u05e4\u05e8\u05d9\u05d8 \u05d3\u05d5\u05de\u05d4 \u05d1\u05e8\u05e9\u05d9\u05de\u05d4: {existing.normalized_name}"
                        + (f" ({eq_display})" if eq_display else "")
                        + ". \u05de\u05d7\u05db\u05d4 \u05dc\u05d1\u05d7\u05d9\u05e8\u05ea \u05d4\u05de\u05e9\u05ea\u05de\u05e9."
                    )
                return result

            elif name == "mark_purchased":
                return self.router.handle_mark_purchased(
                    context,
                    item_name=args.get("item_name", ""),
                    store_name=args.get("store_name"),
                    chain_name=args.get("chain_name"),
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
                item_name = args.get("item_name", "")
                if self.router.price_service:
                    result = self.router.price_service.price_lookup_with_disambiguation(
                        item_name,
                        city=self.router.get_default_city(context),
                    )
                    if result.needs_disambiguation:
                        self._evict_oldest(self.pending_price_choices)
                        self.pending_price_choices[context.external_chat_id] = result
                        return result.text
                    return result.text
                return self.router.handle_semantic_action(context, action="price", item_name=item_name)

            elif name == "list_user_items":
                return self.router.list_items_by_user_name(
                    context,
                    user_name=args.get("user_name", ""),
                )

            elif name == "estimate_list_cost":
                return self.router.estimate_list_cost(context)

            elif name == "compare_list_by_chain":
                return self.router.compare_list_by_chain(context)

            # -- Multi-list tools --

            elif name == "show_lists":
                chat_id = self._get_chat_id(context)
                return self.router.show_lists(chat_id)

            elif name == "switch_list":
                chat_id = self._get_chat_id(context)
                return self.router.switch_list(chat_id, args.get("list_name", ""))

            elif name == "create_list":
                chat_id = self._get_chat_id(context)
                return self.router.create_list(chat_id, args.get("list_name", ""))

            elif name == "move_items":
                chat_id = self._get_chat_id(context)
                return self.router.move_items(
                    chat_id,
                    args.get("item_names", []),
                    args.get("target_list_name", ""),
                )

            elif name == "complete_list":
                chat_id = self._get_chat_id(context)
                return self.router.complete_list(
                    chat_id,
                    list_name=args.get("list_name"),
                    user_id=context.user_id,
                    user_name=context.user_name,
                )

            elif name == "show_history":
                chat_id = self._get_chat_id(context)
                return self.router.show_history(
                    chat_id,
                    months_back=args.get("months_back", 1),
                )

            else:
                return f"Unknown tool: {name}"
        except (sqlite3.Error, requests.RequestException, ValueError, KeyError) as exc:
            logger.exception("Tool %s failed: %s", name, exc)
            return f"Error: {exc}"
