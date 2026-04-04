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
    except Exception as e:
        print(f"ERROR: {e}")
    
    print(f"{'='*60}")
    # Small delay to avoid rate limiting MiniMax
    time.sleep(1)


def main():
    agent, router, store = setup()
    
    print("\n" + "#"*60)
    print("# SHOPPING ASSISTANT INTEGRATION TEST")
    print("#"*60)
    
    # ===== SECTION 1: Basic Operations =====
    print("\n\n>>> SECTION 1: Basic Operations")
    
    test(agent, "Empty list query", "מה יש ברשימה?")
    test(agent, "Add single item", "חלב")
    test(agent, "Add item with quantity", "3 ביצים")
    test(agent, "Add item with Hebrew quantity", "קילו עגבניות")
    test(agent, "Show list", "תראה לי את הרשימה")
    test(agent, "Show list - slash style", "?")
    
    # ===== SECTION 2: Multi-item add =====
    print("\n\n>>> SECTION 2: Multi-item add (single message with multiple items)")
    
    test(agent, "Multiple items newline", "לחם\nחמאה\nגבינה צהובה")
    test(agent, "Multiple items comma", "תפוחים, בננות, תפוזים")
    test(agent, "Long shopping list", """12 ביצים
3 חלב
גבינה צהובה 400 גרם
נקניק סלמי
מילקי
קוטג׳""")
    
    test(agent, "Show list after bulk add", "/list")
    
    # ===== SECTION 3: Duplicate detection =====
    print("\n\n>>> SECTION 3: Duplicate detection")
    
    test(agent, "Add duplicate - exact", "חלב")
    test(agent, "Add duplicate - with quantity", "5 ביצים")
    test(agent, "Add duplicate - similar name", "גבינה")
    
    # ===== SECTION 4: Mark purchased / delete =====
    print("\n\n>>> SECTION 4: Mark purchased and delete")
    
    test(agent, "Mark purchased", "קניתי חלב")
    test(agent, "Mark purchased - natural", "קניתי את הביצים")
    test(agent, "Delete item", "מחק מילקי")
    test(agent, "Delete non-existent", "מחק שוקולד")
    test(agent, "Mark purchased non-existent", "קניתי אבטיח")
    
    # ===== SECTION 5: Price lookup =====
    print("\n\n>>> SECTION 5: Price lookup")
    
    test(agent, "Price check - simple", "כמה עולה חלב?")
    test(agent, "Price check - specific", "מחיר ביצים ביבנה")
    test(agent, "Price check - non-food", "כמה עולה מברג?")
    
    # ===== SECTION 6: List cost estimate =====
    print("\n\n>>> SECTION 6: List cost and comparison")
    
    test(agent, "List cost estimate", "כמה עולה הרשימה שלי?")
    test(agent, "Chain comparison", "השווה את הרשימה שלי בין רשתות")
    test(agent, "Single item comparison", "כמה עולה לחם, השווה בין רשתות")
    
    # ===== SECTION 7: Clear list =====
    print("\n\n>>> SECTION 7: Clear and reset")
    
    test(agent, "Clear list", "נקה את הרשימה")
    test(agent, "Show after clear", "מה יש ברשימה?")
    test(agent, "Clear empty list", "נקה הכל")
    
    # ===== SECTION 8: Multi-user simulation =====
    print("\n\n>>> SECTION 8: Multi-user simulation")
    
    test(agent, "User Noa adds item", "2 חלב", user_id="user2", user_name="נועה")
    test(agent, "User Noa adds more", "לחם שחור", user_id="user2", user_name="נועה")
    test(agent, "User Yossi adds item", "6 בירה", user_id="user3", user_name="יוסי")
    test(agent, "User Yossi adds item", "נקניקיות", user_id="user3", user_name="יוסי")
    test(agent, "User1 asks what Noa wanted", "מה נועה הוסיפה?", user_id="user1", user_name="דני")
    test(agent, "User1 asks what Yossi wanted", "מה יוסי רצה?", user_id="user1", user_name="דני")
    test(agent, "Show full list", "הצג את הרשימה")
    
    # ===== SECTION 9: Edge cases =====
    print("\n\n>>> SECTION 9: Edge cases")
    
    test(agent, "Empty message", "")
    test(agent, "Just spaces", "   ")
    test(agent, "Random chat - should ignore", "מה נשמע? איך היום שלך?")
    test(agent, "Question mark only", "?")
    test(agent, "Very long item name", "שוקולד מריר 85% קקאו בלגי אורגני ללא סוכר עם שקדים קלויים 200 גרם")
    test(agent, "Hebrew + English mix", "Coca Cola Zero 1.5L")
    test(agent, "Numbers only", "42")
    test(agent, "URL should ignore", "https://www.shufersal.co.il/online/he/search?q=milk")
    test(agent, "Help request", "מה אתה יכול לעשות?")
    test(agent, "City change", "עיר תל אביב")
    
    # ===== SECTION 10: Separate chat (different list) =====
    print("\n\n>>> SECTION 10: Separate chat scope")
    
    test(agent, "Add to chat2", "סוכר", chat_id="testchat2", user_name="דני", user_id="user4")
    test(agent, "Show chat2 list", "מה יש ברשימה?", chat_id="testchat2", user_name="דני", user_id="user4")
    test(agent, "Show chat1 list (should be different)", "מה יש ברשימה?", chat_id="testchat1")
    
    print("\n\n" + "#"*60)
    print("# TEST COMPLETE")
    print("#"*60)


if __name__ == "__main__":
    main()
