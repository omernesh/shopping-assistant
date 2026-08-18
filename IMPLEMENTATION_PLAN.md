# Shopping Assistant Implementation Plan

> Reference product: Hey Sally (https://www.heysally.co.il/)
> Delivery target: WhatsApp
> First test environment: Telegram group

## Goal

Build a Hebrew-first shopping assistant that behaves like a lightweight Sally-style grocery copilot:
- accepts natural-language shopping inputs
- maintains a shared, structured shopping list
- organizes items by supermarket category
- supports commands for viewing/completing/deleting items
- adds price lookup via CHP first
- is architected once, with Telegram used as the first proving ground before WhatsApp rollout

## What I learned from Hey Sally

### Core product behavior
1. WhatsApp-first conversational list management
2. Companion web app for checklist / visual list editing
3. Shared list synchronization across users/devices
4. Auto-categorization by supermarket department
5. Price scanner for nearby supermarkets
6. Expense tracking / purchase history
7. Deal calculator
8. Custom category order for efficient in-store route

### Important UX choices
1. Extremely low friction: send text, list updates automatically
2. No app-install dependency for basic usage
3. Chat-native commands in Hebrew
4. Hybrid model: chat for capture, web for structured review/checkoff
5. Family/shared-household orientation, not solo notes app

### Commands/interaction model from site
- free text item entry
- תראה / ? → show list
- קניתי → mark purchased
- מחק → delete
- “עוד פקודות” suggests command expansion exists beyond the basics

### Likely premium / later-stage features
- multiple parallel lists
- recurring/permanent lists
- quantities and notes
- split shopping between two people
- history-based smart suggestions
- voice/photo ingestion
- direct handoff to ecommerce

## What exists in the copied folder

### 1. SALLY_RESEARCH_REPORT.md
Useful product brief. Good enough to define v1 scope and future roadmap.

### 2. PRICE_COMPARISON_API_RESEARCH.md
Useful technical reconnaissance.
Main conclusion:
- CHP is usable now
- Pricez is blocked / browser-heavy

### 3. chp_price_scraper.py
A decent proof of concept:
- request session + retry adapter
- Hebrew handling notes
- city map
- parse compare_results HTML
- extract physical + online store results
- CLI-oriented output formatter

Weaknesses for product use:
- regex/HTML parsing is brittle
- hardcoded city map
- no product disambiguation flow
- no service boundary / app integration layer
- no persistence / caching / normalization
- no tests

### 4. test_output.txt
Just proof that at least one barcode/product response worked. Not a test harness.

## Product decision: what we should build first

Not “Sally clone”.
Not “price scraper bot”.

We should build:
A shared shopping-list assistant with price lookup as feature #2, not the foundation.

Why:
- Sally’s sticky value is list capture + shared flow + organization
- price lookup is sexy, but not the daily habit loop
- if we start from price scraping, we build a tool
- if we start from shared shopping workflow, we build a product

## Recommended v1 scope

### V1 must have
1. One shared list per chat/group
2. Natural-language item add in Hebrew
3. Quantity parsing
4. Category assignment
5. View current list
6. Mark purchased
7. Delete item
8. Telegram group pilot
9. Basic price lookup for a requested item via CHP
10. Persistent storage
11. Audit trail / simple history

### V1 should not have yet
1. Full web app
2. Expense tracking UI
3. Deal calculator
4. Multi-list support
5. Smart recommendations
6. Voice note ingestion
7. Photo-to-list
8. Pricez integration
9. Full WhatsApp production infra

## Target architecture

### A. Core principle
Separate product logic from channel adapters.

That means 4 layers:
1. Channel adapters
   - Telegram adapter first
   - WhatsApp adapter later
2. Conversation / command router
3. Shopping domain engine
4. Data + external services

### B. Proposed components

1. channel/telegram_bot.py
- receives messages from Telegram group
- resolves sender/chat/message metadata
- passes normalized events inward

2. channel/whatsapp_bot.py
- later
- same internal interface as Telegram adapter

3. app/router.py
- command detection
- intent routing
- decide if text is item-add vs command vs price-query

4. domain/shopping_list.py
- add_item
- remove_item
- mark_purchased
- list_items
- reorder/group_by_category

5. domain/parser.py
- Hebrew command parsing
- quantity extraction
- notes parsing
- cheap NLP rules before LLM fallback

6. domain/categorizer.py
- map items to supermarket categories
- deterministic dictionary + fallback rules
- optionally LLM-assisted unknown-item categorization later

7. domain/price_lookup.py
- clean service wrapper over CHP
- product search, city resolution, result normalization

8. infra/storage.py or db/
- SQLite first for speed and simplicity
- tables for chats, lists, items, events, lookups

9. infra/cache.py
- cache frequent price lookups
- cache city resolution
- maybe cache normalized products/categories

10. web/
- defer until after Telegram pilot proves workflow

## Data model

### chats
- id
- platform
- external_chat_id
- title
- default_city
- created_at

### shopping_lists
- id
- chat_id
- name (default: main)
- is_active
- created_at
- updated_at

### list_items
- id
- list_id
- raw_text
- normalized_name
- quantity_value
- quantity_unit
- note
- category
- status (active / purchased / deleted)
- added_by_user_id
- purchased_by_user_id
- created_at
- updated_at

### events
- id
- chat_id
- user_id
- event_type
- payload_json
- created_at

### price_queries
- id
- chat_id
- query
- city
- resolved_product
- source
- raw_result_json
- created_at

## Telegram-first pilot behavior

### Group chat assumptions
1. One Telegram group = one shared shopping list
2. Anyone in group can add items
3. Commands operate on the shared list
4. Replies should stay compact and useful

### Suggested command set for pilot
- ? or תראה → show grouped list
- קניתי <item> → mark purchased
- מחק <item> → delete
- נקה קניות / איפוס → clear purchased or reset list
- מחיר <item> → run price lookup
- עיר <city> → set default city for price checks
- עזרה → help

### Message examples
Free text:
- חלב
- 2 קוטג׳
- מלפפון, עגבניה, לחם

Price:
- מחיר חלב
- כמה עולה קולה זירו?

Completion:
- קניתי חלב
- מחק לחם

## Natural language strategy

### Start simple, not clever
Use deterministic parsing first:
1. split comma/newline-separated items
2. detect leading quantities (e.g. 2 חלב, 3 קוטג')
3. detect command prefixes
4. normalize punctuation/Hebrew quote variants

Only add LLM parsing when rules fail.

Why:
- cheaper
- more predictable
- easier to test
- better for bot/group command reliability

### Price lookup strategy

### Immediate recommendation
Use a two-stage price intelligence roadmap.

#### Stage A — CHP-backed lookup for v1
Use CHP first in the Telegram pilot.

Why:
- already researched
- already partially implemented
- enough to validate user value quickly
- fastest path to a useful מחיר command

#### Stage B — official chain-feed engine for v2
Add a proper backend based on Israeli Price Transparency Law feeds.

Why:
- more durable than scraping HTML
- better for basket optimization across chains
- better source provenance
- better long-term control over normalization, caching, and promotions
- aligns with the installed `israeli-grocery-price-intelligence` skill

### Source strategy
We should not hard-switch blindly.
Use source arbitration:
1. CHP for fast initial product lookup and pilot validation
2. Official chain feeds as the long-term canonical backend
3. CHP kept as fallback / comparison source when feed coverage is incomplete

### Needed refactor from current scraper
1. turn script into importable service module
2. replace regex-only parsing with HTML parser where possible
3. normalize output schema
4. add caching
5. handle product ambiguity
6. support city config per group
7. add graceful failure messaging

### Needed additions for feed-based engine
1. add chain-feed downloader layer
2. parse chain XML into normalized schema
3. store product/store/promotion snapshots locally
4. match products across chains by barcode first, name/manufacturer second
5. expose basket comparison and cheapest-store calculations
6. support update timestamps and stale-data detection
7. design source attribution per result

### Price result format for chat
Compact summary, not HTML dump:
- top 3 cheapest stores
- online results if relevant
- spread/range
- source attribution

Example:
מחיר חלב 3% ביבנה:
1. רמי לוי — ₪6.90
2. יוחננוף — ₪7.10
3. שופרסל דיל — ₪7.40
פער: 7%
מקור: CHP

## Implementation phases

### Phase 0 — Foundation cleanup
1. create proper project structure under shopping assistant/
2. move CHP logic into service module
3. add config file
4. add SQLite schema
5. add tests

Deliverable:
A runnable local app skeleton with working price service and storage.

### Phase 1 — Shared shopping list core
1. implement list/item CRUD
2. implement command parser
3. implement category grouping
4. implement compact list rendering
5. implement purchased/deleted states

Deliverable:
You can manage a shared list locally from test inputs.

### Phase 2 — Telegram bot pilot
1. connect bot to Telegram group
2. map group to shared list
3. support free-text add + core commands
4. add simple help/onboarding message
5. log events/errors

Deliverable:
Real group can use it as a shopping-list assistant.

### Phase 3a — CHP price lookup integration
1. add מחיר command
2. set per-group city
3. return formatted top-price summaries
4. add caching + basic rate limiting
5. track query history

Deliverable:
Telegram pilot now includes fast, useful supermarket price checks via CHP.

### Phase 3b — Official feed-based price intelligence
1. add downloader/parser for official supermarket XML feeds
2. normalize chain/store/product/promotion data into local storage
3. add product matching across chains
4. add source attribution and freshness metadata
5. support basket-level optimization, not just single-item lookup
6. arbitrate between CHP and official-feed results

Deliverable:
A durable price backend with better long-term coverage and shopping-basket intelligence.

### Phase 4 — Pilot hardening
1. improve parsing of messy user input
2. fix duplicate handling
3. better matching for קניתי/מחק when multiple similar items exist
4. admin/debug commands
5. metrics and error reporting

Deliverable:
Stable enough for real family/group usage.

### Phase 5 — WhatsApp migration layer
1. swap/add WhatsApp channel adapter
2. preserve same domain and router
3. validate command compatibility in WhatsApp thread/chat behavior
4. productionize infra

Deliverable:
Same product behavior, new channel.

## Concrete build order I recommend

1. Refactor CHP scraper into service package
2. Build SQLite-backed shopping list core
3. Build Telegram adapter and test in group
4. Add categories + quantity parsing polish
5. Add price lookup command
6. Harden group behavior
7. Only then do WhatsApp adapter

## Risks / traps

### 1. Overcopying Sally
Bad move.
We should copy the product logic, not blindly mirror all features.

### 2. Starting with WhatsApp infra
Also bad.
Telegram is the correct proving ground because iteration is faster and less annoying.

### 3. Overusing LLMs for parsing
Expensive and flaky.
Rules first, LLM fallback only.

### 4. Price scraping brittleness
CHP may change markup.
Need parser abstraction, caching, graceful failure.

### 5. Group ambiguity
Commands like קניתי חלב can be ambiguous if there are multiple milk items.
Need matching strategy and maybe disambiguation replies.

### 6. Web app too early
Tempting, but wasteful before chat workflow is proven.

## Success criteria for the Telegram pilot

1. Group members can add items naturally without training
2. The rendered list feels cleaner than a normal chat thread
3. קניתי and מחק work reliably
4. Price lookup works often enough to feel useful
5. The bot is fast and replies in compact Hebrew
6. No one needs a manual to use it

## What I would implement first, literally

### Sprint 1
- project skeleton
- config
- SQLite schema
- CHP service refactor
- unit tests for parser/storage/price formatting

### Sprint 2
- shopping list domain engine
- category mapping
- list rendering
- core command parsing

### Sprint 3
- Telegram bot integration
- group shared-list behavior
- deploy and test in Telegram group

### Sprint 4
- CHP-backed price command
- city command
- caching / resilience

### Sprint 5
- official feed ingestion layer
- normalized price store
- source arbitration (CHP vs official feeds)
- basket optimization groundwork

## My opinionated recommendation

Build this as:
- a serious shopping list assistant with price intelligence
not
- a price bot with some list features

That is the difference between “neat utility” and “daily-use product”.

## Next implementation artifact I recommend

Create these first:
- src/
- tests/
- docs/
- src/domain/
- src/integrations/
- src/channels/
- src/storage/

And first real files:
- src/domain/parser.py
- src/domain/shopping_list.py
- src/domain/categorizer.py
- src/integrations/chp_client.py
- src/storage/sqlite_store.py
- src/channels/telegram_bot.py
- tests/test_parser.py
- tests/test_shopping_list.py
- tests/test_chp_client.py
