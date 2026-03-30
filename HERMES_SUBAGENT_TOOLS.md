# Shopping Assistant Hermes Tool Contract

Project path: /home/omer/.claude/projects/shopping assistant

This file defines the tool surface the Hermes shopping subagent should use.

## Tool 1: show_list
Purpose: Return the active shared list for the current group/topic.
Backed by:
- ShoppingAssistantRouter.handle_semantic_action(... action="show")
- SQLiteStore.list_active_items(...)

Inputs:
- group/topic scope from Telegram context

Output:
- grouped shopping list text

## Tool 2: add_item
Purpose: Add a shopping item to the active shared list.
Backed by:
- ShoppingAssistantRouter.handle_semantic_action(... action="add")
- build_item(...)
- SQLiteStore.add_item(...)

Inputs:
- item_name
- optional quantity
- optional note
- group/topic scope

Output:
- compact confirmation text

## Tool 3: mark_purchased
Purpose: Mark an existing item as purchased.
Backed by:
- ShoppingAssistantRouter.handle_semantic_action(... action="done")
- SQLiteStore.update_item_status(... status="purchased")

Inputs:
- item_name
- group/topic scope

Output:
- compact confirmation or not-found reply

## Tool 4: delete_item
Purpose: Delete an existing item from the active shared list.
Backed by:
- ShoppingAssistantRouter.handle_semantic_action(... action="delete")
- SQLiteStore.update_item_status(... status="deleted")

Inputs:
- item_name
- group/topic scope

Output:
- compact confirmation or not-found reply

## Tool 5: set_city
Purpose: Update the default city for this group/topic.
Backed by:
- ShoppingAssistantRouter.handle_semantic_action(... action="city")
- SQLiteStore.update_chat_default_city(...)

Inputs:
- city
- group/topic scope

Output:
- compact confirmation text

## Tool 6: price_lookup
Purpose: Run price intelligence flow for an item.
Backed by:
- ShoppingAssistantRouter.handle_semantic_action(... action="price")
- future live integration: src/integrations/chp_client.py

Inputs:
- item_name
- group/topic scope
- default city

Output:
- currently placeholder text until live CHP wiring is enabled in the Hermes path

## Tool 7: ignore
Purpose: Do nothing.
Use when the message is not a shopping intent.

## Semantic planner
Current planner implementation:
- src/agent/minimax_client.py
- src/agent/shopping_agent.py

Planner rules:
- prefer ignore over incorrect mutation
- ordinary discussion should not mutate the list
- semantics first, deterministic execution second
