# Tool Contract Reference

Primary tool contract file: `HERMES_SUBAGENT_TOOLS.md` (project root)

Allowed shopping actions:
- show_list
- add_item
- mark_purchased
- delete_item
- set_city
- price_lookup
- ignore

Execution remains deterministic in code via src/app/router.py.
The semantic planner decides which action to call; it should not freestyle state changes.
