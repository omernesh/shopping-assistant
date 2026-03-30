# Shopping Assistant

Hebrew-first shared shopping assistant.

## Current phase
Phase 0: foundation cleanup

## Product stance
This is being built as a product:
- channel adapters stay thin
- domain logic stays reusable
- persistence is explicit
- Telegram is the proving ground before WhatsApp

## Initial structure
- `src/channels/` — Telegram-first channel adapters
- `src/domain/` — parsing, categorization, shopping logic
- `src/integrations/` — CHP and future external sources
- `src/storage/` — SQLite persistence
- `src/config/` — app settings
- `tests/` — unit tests
- `docs/` — implementation notes
- `data/` — local runtime state

## Storage
See `docs/storage_approach.md`.

## Next steps
1. initialize SQLite on startup
2. add repository methods for chats/lists/items/events
3. make topic-aware list scoping explicit
4. harden CHP parsing and add cache/retry boundaries
5. wire Telegram adapter into real command handling
