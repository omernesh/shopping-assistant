# Shopping Assistant

> Hebrew-first shared shopping list bot for Telegram with AI-powered natural language understanding and Israeli supermarket price intelligence.

<div align="center">

![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![LLM](https://img.shields.io/badge/LLM-MiniMax%20M2.7-purple)
![Platform](https://img.shields.io/badge/platform-Telegram-blue)

</div>

## What It Does

A Telegram bot that manages shared shopping lists through natural Hebrew conversation. Each Telegram group or forum topic gets its own list. An AI agent (MiniMax M2.7) understands shopping intent from casual messages and routes them to the right action — or stays silent when the message isn't about shopping.

### Features

**Shopping List**
- Add items via natural language — `חלב`, `2 קילו עגבניות`, `חצי ליטר שמנת`
- Show list grouped by category (produce, refrigerator, bakery, pantry, beverages)
- Mark items as purchased — `קניתי חלב`
- Delete items, clear the list
- Duplicate detection with inline keyboard (merge, update quantity, add separately, cancel)
- Per-topic scoping — each forum topic in a group gets its own list
- Multi-user tracking — see who added what

**Price Intelligence**
- Single-item price lookup — cheapest stores in your city via [CHP.co.il](https://chp.co.il)
- Product disambiguation with inline keyboard when multiple matches found
- Estimate total cost of your shopping list
- Compare total cost across supermarket chains with savings percentage
- Local price DB from official Israeli supermarket XML feeds (government-mandated open data)
- Supports 16 Israeli cities, barcode search

**AI Agent**
- MiniMax M2.7 via Anthropic-compatible API with tool calling (10 tools)
- Multi-round tool loop (up to 5 rounds per message)
- Smart silence — ignores non-shopping group chatter, URLs, numbers, long messages
- Graceful fallback to regex parser if LLM is unavailable

## Architecture

```
src/
├── agent/              # LLM client + tool-calling agent loop
│   ├── llm_client.py   # Anthropic-compatible HTTP transport + system prompt + tool defs
│   └── shopping_agent.py  # Agent loop with duplicate/price disambiguation
├── app/
│   └── router.py       # Central action router — bridges intents to storage
├── channels/
│   ├── telegram_bot.py      # Thin adapter: raw Telegram payload → MessageContext
│   ├── telegram_polling.py  # Long-polling bot with inline keyboards
│   └── media_handler.py     # Media message handling
├── config/
│   └── settings.py     # Env-based config loader (no python-dotenv dependency)
├── domain/
│   ├── categorizer.py  # Keyword-based item → category mapping
│   ├── parser.py       # Regex Hebrew parser (LLM fallback path)
│   ├── shopping_list.py  # Item draft builder
│   └── shopping_mode.py  # Shopping mode state
├── integrations/
│   ├── chp_client.py      # CHP.co.il web scraper for price comparison
│   ├── feed_downloader.py # Israeli supermarket XML feed ingester
│   └── price_service.py   # Unified price service (CHP + local DB)
└── storage/
    └── sqlite_store.py  # SQLite persistence — 7 tables, full CRUD + audit log
```

## Setup

### Prerequisites

- Python 3.11+
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- A MiniMax API key (or any Anthropic-compatible LLM provider)

### Install

```bash
git clone https://github.com/omernesh/shopping-assistant.git
cd shopping-assistant
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e .
```

### Configure

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Required | Description |
|----------|----------|-------------|
| `SHOPPING_BOT_TOKEN` | Yes | Telegram bot token |
| `LLM_API_KEY` | Yes | MiniMax (or compatible) API key |
| `SHOPPING_ASSISTANT_DB_PATH` | No | SQLite path (default: `./data/shopping_assistant.sqlite3`) |
| `SHOPPING_ASSISTANT_DEFAULT_CITY` | No | Default city in Hebrew (default: `יבנה`) |
| `SHOPPING_ASSISTANT_DEFAULT_CITY_ID` | No | CHP city ID (default: `2660`) |
| `SHOPPING_ASSISTANT_DEFAULT_STREET_ID` | No | CHP street ID (default: `9000`) |
| `SHOPPING_ASSISTANT_CACHE_TTL_SECONDS` | No | Price cache TTL (default: `21600` / 6 hours) |
| `LLM_API_KEY` | Yes | OpenRouter or DeepSeek API key |
| `LLM_MODEL` | No | Model name (default: `deepseek-chat`) |
| `LLM_BASE_URL` | No | API base URL (default: `https://api.deepseek.com`) |
| `SHOPPING_ASSISTANT_AGENT_ENABLED` | No | Enable LLM agent (default: `true`) |

### Run

```bash
python run_bot.py
```

### Telegram Commands

| Command | Action |
|---------|--------|
| `/start` | Welcome message |
| `/list` | Show current shopping list |
| `/clear` | Clear all items |
| `/city <name>` | Set your city for price lookups |
| `/help` | Show help |

Or just chat naturally in Hebrew:

```
תוסיף חלב ולחם        → adds milk and bread
קניתי חלב              → marks milk as purchased
כמה עולה חלב           → price lookup for milk
מחק לחם                → deletes bread
מה נועה רצתה?          → shows items added by Noa
```

## Price Data Pipeline

The bot has two price data sources:

**1. CHP.co.il (live scraping)**
- Queries the CHP price comparison site in real-time
- Returns top 5 cheapest stores in your city
- Cached locally with configurable TTL

**2. Official supermarket XML feeds (local DB)**
- Government-mandated price transparency data
- Shufersal (Azure blob), Carrefour (HTML listing), TivTaam/Rami Levy/Yochananof (Cerberus FTP)
- Ingested into a local SQLite price DB (`prices.sqlite3`)
- Auto-rotates at 250MB, 7-day expiry

### Update prices

```bash
# Download Shufersal + Carrefour feeds
python scripts/update_prices.py

# Scrape Cerberus portals (requires Playwright)
python scripts/scrape_cerberus_prices.py
```

## AI Agent Framework Integration

This bot runs standalone, but can also be integrated as a skill or plugin in any AI agent framework.

### Standalone (direct Python)

```bash
python run_bot.py
```

No framework needed. The bot connects to Telegram directly via long-polling.

### Claude Code (as a skill)

Use the `hermes-skill/SKILL.md` as a template. Create a skill directory in your Claude Code project:

```
.claude/skills/shopping-assistant/
├── SKILL.md          # Copy and adapt from hermes-skill/SKILL.md
└── references/
    ├── identity.md
    └── tool-contract.md
```

The skill tells Claude Code how to invoke the shopping assistant's tool contract (add_item, show_list, mark_purchased, etc.) and when to stay silent.

### OpenClaw (as a plugin)

Register the bot as an OpenClaw skill by pointing to the tool contract:

1. Copy `hermes-skill/SKILL.md` into your OpenClaw skills directory
2. Map the 7 shopping actions (show_list, add_item, mark_purchased, delete_item, set_city, price_lookup, ignore) to OpenClaw tool handlers
3. The bot's `src/app/router.py` exposes `handle_semantic_action()` which OpenClaw can call directly

### Hermes Agent (as a skill)

The `hermes-skill/` directory contains a ready-to-use Hermes Agent skill:

- `SKILL.md` -- Identity, behavioral rules, tool policy
- `references/identity.md` -- Identity reference
- `references/tool-contract.md` -- Allowed actions and execution contract

Copy the `hermes-skill/` directory into your Hermes skills path and configure the skill in your Hermes config.

## Tests

```bash
# Unit tests
pytest tests/

# Integration test (requires LLM API key + Telegram token)
python scripts/integration_test.py

# Health check
python scripts/check_bot.py
```

## Storage

SQLite with 7 tables: `chats`, `shopping_lists`, `list_items`, `events` (audit log), `price_queries`, `price_cache`, `users`. Auto-rotates purchased/deleted items after 30 days, events after 60 days.

See `docs/storage_approach.md` for design decisions.

## License

MIT
