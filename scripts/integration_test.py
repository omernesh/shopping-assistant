#!/usr/bin/env python3
"""Comprehensive integration test for the shopping assistant bot."""
from __future__ import annotations
import logging
import sys
import os
import time

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.settings import load_settings
from src.storage.sqlite_store import SQLiteStore
from src.app.router import ShoppingAssistantRouter, MessageContext
from src.agent.llm_client import LLMConfig, LLMTransport
from src.agent.shopping_agent import ShoppingAgent
from src.integrations.chp_client import CHPClient
from src.integrations.feed_downloader import PriceDB
from src.integrations.price_service import PriceService

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

# Use a separate test database
TEST_DB = "/tmp/shopping_test.sqlite3"
PRICE_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "prices.sqlite3")

def setup():
    """Initialize full stack with test DB."""
    import pathlib
    pathlib.Path(TEST_DB).unlink(missing_ok=True)
    
    settings = load_settings()
    store = SQLiteStore(TEST_DB)
    store.initialize()
    
    chp = CHPClient(timeout=15)
    pdb = PriceDB(PRICE_DB)
    price_service = PriceService(chp_client=chp, price_db=pdb)
    
    router = ShoppingAssistantRouter(
        store=store,
        default_city="יבנה",
        chp_client=chp,
        price_db=pdb,
        price_service=price_service,
    )
    
    transport = None
    if settings.llm_api_key:
        transport = LLMTransport(LLMConfig(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
        ))
    
    agent = ShoppingAgent(router=router, transport=transport)
    return agent, router, store


def ctx(text: str, user_id: str = "user1", user_name: str = "דני", chat_id: str = "testchat1") -> MessageContext:
    """Create a message context."""
    return MessageContext(
        platform="telegram",
        external_chat_id=chat_id,
        user_id=user_id,
        text=text,
        user_name=user_name,
    )


def test(agent, label: str, message: str, user_id="user1", user_name="דני", chat_id="testchat1"):
    """Run a single test and print result."""
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"USER: {user_name} ({user_id})")
    print(f"INPUT: {message}")
    print(f"-"*60)
    
    context = ctx(message, user_id=user_id, user_name=user_name, chat_id=chat_id)
    try:
        start = time.time()
        response = agent.handle_message(context)
        elapsed = time.time() - start

        # Check for pending conflicts (duplicate detection)
        conflict = agent.pending_conflicts.pop(context.external_chat_id, None)

        print(f"RESPONSE ({elapsed:.1f}s):")
        if response:
            print(response)
        else:
            print("[EMPTY - bot stayed silent]")

        if conflict:
            print(f"\n[DUPLICATE DETECTED]")
            print(f"  Existing: {conflict.existing_item.normalized_name} (qty: {conflict.existing_item.quantity_value})")
            print(f"  New: {conflict.new_item_name} (qty: {conflict.new_quantity})")
        return True
    except Exception as e:
        print(f"ERROR: {e}")
        return False
    
    print(f"{'='*60}")
    # Small delay to avoid rate limiting MiniMax
    time.sleep(1)


def main():
    agent, router, store = setup()
    failures = 0

    def run(label, message, **kwargs):
        nonlocal failures
        if not test(agent, label, message, **kwargs):
            failures += 1

    print("\n" + "#"*60)
    print("# SHOPPING ASSISTANT INTEGRATION TEST")
    print("#"*60)

    # ===== SECTION 1: Basic Operations =====
    print("\n\n>>> SECTION 1: Basic Operations")

    run("Empty list query", "מה יש ברשימה?")
    run("Add single item", "חלב")
    run("Add item with quantity", "3 ביצים")
    run("Add item with Hebrew quantity", "קילו עגבניות")
    run("Show list", "תראה לי את הרשימה")
    run("Show list - slash style", "?")
    
    # ===== SECTION 2: Multi-item add =====
    print("\n\n>>> SECTION 2: Multi-item add (single message with multiple items)")
    
    run("Multiple items newline", "לחם\nחמאה\nגבינה צהובה")
    run("Multiple items comma", "תפוחים, בננות, תפוזים")
    run("Long shopping list", """12 ביצים
3 חלב
גבינה צהובה 400 גרם
נקניק סלמי
מילקי
קוטג׳""")
    
    run("Show list after bulk add", "/list")
    
    # ===== SECTION 3: Duplicate detection =====
    print("\n\n>>> SECTION 3: Duplicate detection")
    
    run("Add duplicate - exact", "חלב")
    run("Add duplicate - with quantity", "5 ביצים")
    run("Add duplicate - similar name", "גבינה")
    
    # ===== SECTION 4: Mark purchased / delete =====
    print("\n\n>>> SECTION 4: Mark purchased and delete")
    
    run("Mark purchased", "קניתי חלב")
    run("Mark purchased - natural", "קניתי את הביצים")
    run("Delete item", "מחק מילקי")
    run("Delete non-existent", "מחק שוקולד")
    run("Mark purchased non-existent", "קניתי אבטיח")
    
    # ===== SECTION 5: Price lookup =====
    print("\n\n>>> SECTION 5: Price lookup")
    
    run("Price check - simple", "כמה עולה חלב?")
    run("Price check - specific", "מחיר ביצים ביבנה")
    run("Price check - non-food", "כמה עולה מברג?")
    
    # ===== SECTION 6: List cost estimate =====
    print("\n\n>>> SECTION 6: List cost and comparison")
    
    run("List cost estimate", "כמה עולה הרשימה שלי?")
    run("Chain comparison", "השווה את הרשימה שלי בין רשתות")
    run("Single item comparison", "כמה עולה לחם, השווה בין רשתות")
    
    # ===== SECTION 7: Clear list =====
    print("\n\n>>> SECTION 7: Clear and reset")
    
    run("Clear list", "נקה את הרשימה")
    run("Show after clear", "מה יש ברשימה?")
    run("Clear empty list", "נקה הכל")
    
    # ===== SECTION 8: Multi-user simulation =====
    print("\n\n>>> SECTION 8: Multi-user simulation")
    
    run("User Noa adds item", "2 חלב", user_id="user2", user_name="נועה")
    run("User Noa adds more", "לחם שחור", user_id="user2", user_name="נועה")
    run("User Yossi adds item", "6 בירה", user_id="user3", user_name="יוסי")
    run("User Yossi adds item", "נקניקיות", user_id="user3", user_name="יוסי")
    run("User1 asks what Noa wanted", "מה נועה הוסיפה?", user_id="user1", user_name="דני")
    run("User1 asks what Yossi wanted", "מה יוסי רצה?", user_id="user1", user_name="דני")
    run("Show full list", "הצג את הרשימה")
    
    # ===== SECTION 9: Edge cases =====
    print("\n\n>>> SECTION 9: Edge cases")
    
    run("Empty message", "")
    run("Just spaces", "   ")
    run("Random chat - should ignore", "מה נשמע? איך היום שלך?")
    run("Question mark only", "?")
    run("Very long item name", "שוקולד מריר 85% קקאו בלגי אורגני ללא סוכר עם שקדים קלויים 200 גרם")
    run("Hebrew + English mix", "Coca Cola Zero 1.5L")
    run("Numbers only", "42")
    run("URL should ignore", "https://www.shufersal.co.il/online/he/search?q=milk")
    run("Help request", "מה אתה יכול לעשות?")
    run("City change", "עיר תל אביב")
    
    # ===== SECTION 10: Separate chat (different list) =====
    print("\n\n>>> SECTION 10: Separate chat scope")
    
    run("Add to chat2", "סוכר", chat_id="testchat2", user_name="דני", user_id="user4")
    run("Show chat2 list", "מה יש ברשימה?", chat_id="testchat2", user_name="דני", user_id="user4")
    run("Show chat1 list (should be different)", "מה יש ברשימה?", chat_id="testchat1")
    
    print("\n\n" + "#"*60)
    print(f"# TEST COMPLETE — {failures} failure(s)")
    print("#"*60)

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
