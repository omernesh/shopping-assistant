#!/usr/bin/env python3
"""
Shopping Assistant Backend — Hermes skill integration layer.
Usage: PYTHONPATH="<project_dir>" python3 shopping_backend.py <action> [args...] [--chat <id>] [--user <name>] [--uid <id>]

Actions:
  init                  Initialize SQLite databases
  add <item> [qty]     Add item to shopping list
  show                  Show current list (grouped by category)
  done <item_name>     Mark item as purchased
  delete <item_name>   Delete item from list
  price <item_name>    Look up price via CHP
  city [city_name]     Get/set default city
  total                Estimate total cost of shopping list
  compare              Compare list cost across chains
  clear                Clear all items
  lists                Show all lists for the chat + which is active
  history [months]     Purchase history for the last N months (default 1)

Chat keys: `tg:<telegram_chat_id>` or `wa:<whatsapp_jid>` (pick by source platform).
shared_lists.json in DATA_DIR can alias chat keys to one shared list — e.g. a
WhatsApp group resolving to the same list as a Telegram group.

Sender attribution: pass `--user <display name>` (and optionally `--uid <platform
user id>`) so list items record who added/purchased them.
"""

import json
import sys
import os
from pathlib import Path

PROJECT_DIR = Path.home() / ".claude" / "projects" / "shopping assistant"
sys.path.insert(0, str(PROJECT_DIR))

DATA_DIR = Path.home() / ".hermes" / "data" / "shopping-assistant"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CITY = "יבנה"

# Sender attribution — set from --user/--uid. Defaults signal a missing attribution.
_USER_ID = "unknown"
_USER_NAME = "לא ידוע"


def _parse_chat(chat_id):
    """Split a prefixed chat id ('tg:'/'wa:') into (platform, external_id)."""
    if chat_id.startswith("tg:"):
        return "telegram", chat_id[3:]
    if chat_id.startswith("wa:"):
        return "whatsapp", chat_id[3:]
    return "telegram", chat_id


def _load_aliases():
    """Chat-key aliases for cross-platform shared lists.

    File: DATA_DIR/shared_lists.json
    Format: {"aliases": {"wa:<jid>": "tg:<chat-id>", ...}} — the SOURCE key
    resolves to the TARGET key's chat + list (one hop).
    """
    try:
        data = json.loads((DATA_DIR / "shared_lists.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    aliases = data.get("aliases") or {}
    return {str(k).strip(): str(v).strip() for k, v in aliases.items() if v}


def _resolve_chat(chat_id):
    """Parse a chat key and follow one shared-list alias hop if present."""
    platform, cid = _parse_chat(chat_id)
    prefix = "wa" if platform == "whatsapp" else "tg"
    target = _load_aliases().get(f"{prefix}:{cid}")
    if target:
        platform, cid = _parse_chat(target)
    return platform, cid


def _make_context(platform="telegram", chat_id="whatsapp-group", user_id=None, text="", user_name=None):
    from src.app.router import MessageContext
    if chat_id.startswith(("tg:", "wa:")):
        platform, chat_id = _resolve_chat(chat_id)
    return MessageContext(
        platform=platform,
        external_chat_id=chat_id,
        user_id=user_id or _USER_ID,
        text=text,
        user_name=user_name or _USER_NAME,
    )


def _chat_from_args(arg):
    """Accept an explicit chat id like 'tg:-1003702048851', 'wa:<jid>@g.us', or plain id."""
    if not arg:
        return "whatsapp-group"
    if arg.startswith("tg:"):
        return arg[3:]
    return arg


def _router_for(chat_id):
    from src.app.router import ShoppingAssistantRouter
    from src.integrations.chp_client import CHPClient
    from src.integrations.price_service import PriceService
    from src.integrations.feed_downloader import PriceDB
    from src.storage.sqlite_store import SQLiteStore

    db_path = DATA_DIR / "shopping.sqlite3"
    store = SQLiteStore(db_path)
    store.initialize()
    store.rotate_if_needed()

    chp_client = CHPClient(timeout=15)
    price_db_path = DATA_DIR / "prices.sqlite3"
    price_db = PriceDB(price_db_path)
    price_db.initialize()
    price_service = PriceService(chp_client=chp_client, price_db=price_db)

    return ShoppingAssistantRouter(
        store=store,
        default_city=DEFAULT_CITY,
        chp_client=chp_client,
        price_db=price_db,
        price_service=price_service,
    )


def cmd_init():
    router = _router_for("init")
    db_path = DATA_DIR / "shopping.sqlite3"
    price_path = DATA_DIR / "prices.sqlite3"
    return {
        "ok": True,
        "db": str(db_path),
        "db_size": db_path.stat().st_size,
        "price_db": str(price_path),
        "price_db_size": price_path.stat().st_size if price_path.exists() else 0,
    }


def cmd_show(chat_id="whatsapp-group"):
    router = _router_for(chat_id)
    ctx = _make_context(chat_id=chat_id, text="?")
    try:
        result = router.preview_list(ctx)
        return {"ok": True, "list": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_add(item, qty=None, chat_id="whatsapp-group"):
    router = _router_for(chat_id)
    ctx = _make_context(chat_id=chat_id, text=item)
    try:
        result = router.handle_semantic_action(
            ctx,
            action="add",
            item_name=item,
            quantity=float(qty) if qty else None,
        )
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_done(item_name, chat_id="whatsapp-group"):
    router = _router_for(chat_id)
    ctx = _make_context(chat_id=chat_id, text=f"קניתי {item_name}")
    try:
        result = router.handle_semantic_action(ctx, action="done", item_name=item_name)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_delete(item_name, chat_id="whatsapp-group"):
    router = _router_for(chat_id)
    ctx = _make_context(chat_id=chat_id, text=f"מחק {item_name}")
    try:
        result = router.handle_semantic_action(ctx, action="delete", item_name=item_name)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_price(item_name, chat_id="whatsapp-group"):
    router = _router_for(chat_id)
    ctx = _make_context(chat_id=chat_id, text=f"מחיר {item_name}")
    try:
        result = router.handle_semantic_action(ctx, action="price", item_name=item_name)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_city(city_name=None, chat_id="whatsapp-group"):
    router = _router_for(chat_id)
    ctx = _make_context(chat_id=chat_id)
    if city_name:
        try:
            result = router.handle_semantic_action(ctx, action="city", city=city_name)
            return {"ok": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True, "city": DEFAULT_CITY}


def cmd_total(chat_id="whatsapp-group"):
    router = _router_for(chat_id)
    ctx = _make_context(chat_id=chat_id, text="כמה עולה")
    try:
        result = router.estimate_list_cost(ctx)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_compare(chat_id="whatsapp-group"):
    router = _router_for(chat_id)
    ctx = _make_context(chat_id=chat_id, text="השוואה")
    try:
        result = router.compare_list_by_chain(ctx)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_clear(chat_id="whatsapp-group"):
    router = _router_for(chat_id)
    ctx = _make_context(chat_id=chat_id)
    try:
        result = router.handle_semantic_action(ctx, action="clear")
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_history(months_back=None, chat_id="whatsapp-group"):
    """Purchase history for the last N months (default 1)."""
    router = _router_for(chat_id)
    try:
        platform, _ext_id = _resolve_chat(chat_id)
        chat = router.store.ensure_chat(platform=platform, external_chat_id=_ext_id)
        months = int(months_back) if months_back else 1
        return {"ok": True, "result": router.show_history(chat.id, months)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_lists(chat_id="whatsapp-group"):
    """Show all lists for this chat, marking the active one."""
    router = _router_for(chat_id)
    try:
        platform, _ext_id = _resolve_chat(chat_id)
        chat = router.store.ensure_chat(platform=platform, external_chat_id=_ext_id)
        return {"ok": True, "result": router.show_lists(chat.id)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "Usage: shopping_backend.py <action> [args...] [--chat <id>] [--user <name>] [--uid <id>]"}))
        sys.exit(1)

    # Parse optional --chat/--user/--uid flags from anywhere in argv
    chat_id = "whatsapp-group"
    argv = sys.argv[1:]
    for flag in ("--chat", "--user", "--uid"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                val = argv[i + 1]
                if flag == "--chat":
                    chat_id = _chat_from_args(val)
                elif flag == "--user":
                    _USER_NAME = val
                else:
                    _USER_ID = val
            del argv[i:i + 2]

    action = argv[0]
    arg1 = argv[1] if len(argv) > 1 else None
    arg2 = argv[2] if len(argv) > 2 else None

    fn_map = {
        "init": lambda: cmd_init(),
        "show": lambda: cmd_show(chat_id),
        "add": lambda: cmd_add(arg1 or "", arg2, chat_id),
        "done": lambda: cmd_done(arg1 or "", chat_id),
        "delete": lambda: cmd_delete(arg1 or "", chat_id),
        "price": lambda: cmd_price(arg1 or "", chat_id),
        "city": lambda: cmd_city(arg1, chat_id),
        "total": lambda: cmd_total(chat_id),
        "compare": lambda: cmd_compare(chat_id),
        "clear": lambda: cmd_clear(chat_id),
        "lists": lambda: cmd_lists(chat_id),
        "history": lambda: cmd_history(arg1, chat_id),
    }

    if action not in fn_map:
        print(json.dumps({"ok": False, "error": f"unknown action: {action}"}))
        sys.exit(1)

    try:
        result = fn_map[action]()
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
