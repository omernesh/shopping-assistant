# Storage approach

## Decision
Use SQLite as the system of record for the Telegram-first pilot.

## Why SQLite first
- single-file persistence
- trivial local setup
- enough for one shared list per chat/topic pilot
- easy to inspect/debug
- straightforward migration path later to Postgres if needed

## Logical model
- `chats`: one row per platform chat
- `shopping_lists`: default one active list per chat
- `list_items`: shopping items with status lifecycle
- `events`: append-only audit/history log
- `price_queries`: lookup history for observability
- `price_cache`: short-lived cache for CHP responses

## Telegram-first mapping
For the pilot, we should treat each Telegram conversation context as an addressable shared list scope.
Preferred mapping:
- group chat + topic => unique shared list scope
- fallback if topic is absent: group chat only

Implementation note:
Store platform/external ids in `chats`, and if topic-aware behavior is needed, extend the external key to include the topic id or add a dedicated column.

## Near-term constraints
- no premature multi-tenant complexity
- no ORM yet
- schema is explicit SQL so behavior stays obvious
- cache CHP results to reduce repeated scraping and brittleness

## Migration posture
If the product graduates beyond pilot:
- keep repository/service boundaries stable
- move from direct SQLite calls to repository interfaces
- add Postgres adapter without rewriting domain logic
