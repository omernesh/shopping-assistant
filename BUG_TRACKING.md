# Shopping Assistant Pilot Bug Tracking

Project: /home/omer/.claude/projects/shopping assistant
Bot under test: @nesher_shopping_bot
Pilot environment: Telegram group/topic

## How to use this file
Log bugs in realtime during the pilot.
Keep entries short and concrete.

Suggested severity:
- P0 = blocks pilot / bot unusable
- P1 = core workflow broken or misleading
- P2 = annoying but workable
- P3 = polish / UX improvement

Suggested status:
- open
- investigating
- fixed
- verified
- wontfix

## Bug log

| ID | Time | Severity | Status | Area | Repro | Expected | Actual | Notes |
|---|---|---|---|---|---|---|---|---|
| B001 | 2026-03-30 00:58 | P1 | fixed | parser/router | Paste long handoff/meta message in the pilot chat | Bot should ignore non-shopping meta text | Bot added the entire prompt as a shopping item | Fixed by adding ignore heuristics for slash commands, long multiline/meta chatter, URLs, and obvious conversational questions |
| B002 | 2026-03-30 00:58 | P1 | fixed | parser/router | Ask the bot a normal conversational question in the group | Bot should stay quiet or only respond to shopping intents | Bot replied with `נוסף:` and treated the question as an item | Fixed by returning `ignore` intent for obvious non-shopping chatter |
| B003 | 2026-03-30 00:58 | P1 | open | pilot data | Run `?` after earlier bad parses | Clean list or only real shopping items should appear | Polluted historical items remain in SQLite and show up in list | Need to wipe/reset contaminated pilot rows before continuing |

## Pilot checklist
- [ ] Bot responds in the correct topic
- [ ] Free-text add works in Hebrew
- [ ] `?` shows the current list
- [ ] `קניתי <item>` marks purchased
- [ ] `מחק <item>` deletes item
- [ ] Topic scoping works correctly
- [ ] Replies stay compact and readable
- [ ] No duplicate or stray replies
- [ ] Bot ignores non-text messages safely
- [ ] Bot survives multiple consecutive messages

## Known current state
- Price lookup command is not wired to live CHP yet; router currently returns a placeholder for `מחיר <item>`.
- Shared shopping list core is live: add/show/purchased/delete.
- Storage is SQLite-backed.
- Group/topic scope is implemented as `chat_id:thread_id`.
