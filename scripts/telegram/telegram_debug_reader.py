#!/usr/bin/env python3
"""Read and consume Telegram bot messages for debugging.

Uses getUpdates (long polling) to fetch updates for the bot and optionally
filter by chat ID. Supports offset persistence so messages are consumed once.

Environment:
  TELEGRAM_BOT_TOKEN   Required
  TELEGRAM_CHAT_ID     Optional filter (numeric ID)

Usage examples:
  python3 telegram_debug_reader.py --once
  python3 telegram_debug_reader.py --follow
  python3 telegram_debug_reader.py --follow --offset-file /tmp/tg.offset
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


API_BASE = "https://api.telegram.org"


def _api_url(token: str, method: str) -> str:
    return f"{API_BASE}/bot{token}/{method}"


def _http_get_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 35) -> Dict[str, Any]:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _load_offset(offset_file: Optional[Path]) -> Optional[int]:
    if not offset_file:
        return None
    try:
        raw = offset_file.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _save_offset(offset_file: Optional[Path], offset: int) -> None:
    if not offset_file:
        return
    try:
        offset_file.write_text(str(offset), encoding="utf-8")
    except Exception:
        pass


def _extract_text(update: Dict[str, Any]) -> Optional[str]:
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not msg:
        return None
    return msg.get("text") or msg.get("caption")


def _extract_chat_id(update: Dict[str, Any]) -> Optional[int]:
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not msg:
        return None
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    try:
        return int(chat_id)
    except Exception:
        return None


def _format_update(update: Dict[str, Any]) -> str:
    msg = update.get("message") or update.get("edited_message") or update.get("channel_post") or {}
    chat = msg.get("chat") or {}
    chat_title = chat.get("title") or chat.get("username") or str(chat.get("id", "?"))
    from_user = (msg.get("from") or {}).get("username") or (msg.get("from") or {}).get("first_name") or "?"
    text = _extract_text(update) or ""
    return f"[{chat_title}] {from_user}: {text}"


def _emit(line: str, log_file: Optional[Path]) -> None:
    print(line)
    if not log_file:
        return
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def fetch_updates(
    token: str,
    *,
    offset: Optional[int] = None,
    timeout: int = 30,
    allowed_updates: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    if allowed_updates:
        params["allowed_updates"] = json.dumps(allowed_updates)
    data = _http_get_json(_api_url(token, "getUpdates"), params=params, timeout=timeout + 5)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data.get("result", [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Read and consume Telegram bot messages")
    parser.add_argument("--once", action="store_true", help="Fetch once and exit")
    parser.add_argument("--follow", action="store_true", help="Follow updates continuously")
    parser.add_argument("--timeout", type=int, default=30, help="Long-poll timeout seconds (default: 30)")
    parser.add_argument("--offset-file", default=".telegram.offset", help="File to persist update offset")
    parser.add_argument("--no-offset-file", action="store_true", help="Disable offset persistence")
    parser.add_argument("--log-file", default="telegram_debug_reader.log", help="Log file path (default: telegram_debug_reader.log)")
    parser.add_argument("--no-log", action="store_true", help="Disable log file output")
    parser.add_argument("--chat-id", type=int, help="Filter by chat id (overrides TELEGRAM_CHAT_ID)")
    parser.add_argument("--print-raw", action="store_true", help="Print raw JSON updates")
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Missing TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 2

    env_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    chat_id = args.chat_id
    if chat_id is None and env_chat_id:
        try:
            chat_id = int(env_chat_id)
        except Exception:
            chat_id = None

    offset_file = None if args.no_offset_file else Path(args.offset_file)
    log_file = None if args.no_log else Path(args.log_file)
    offset = _load_offset(offset_file)

    if not args.once and not args.follow:
        args.once = True

    try:
        while True:
            updates = fetch_updates(
                token,
                offset=offset,
                timeout=args.timeout,
                allowed_updates=["message", "edited_message", "channel_post"],
            )

            if updates:
                for update in updates:
                    update_id = update.get("update_id")
                    if update_id is not None:
                        offset = int(update_id) + 1
                    if chat_id is not None:
                        upd_chat = _extract_chat_id(update)
                        if upd_chat is None or upd_chat != chat_id:
                            continue
                    if args.print_raw:
                        _emit(json.dumps(update, indent=2), log_file)
                    else:
                        _emit(_format_update(update), log_file)

                if offset is not None:
                    _save_offset(offset_file, offset)

            if args.once:
                break

            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
