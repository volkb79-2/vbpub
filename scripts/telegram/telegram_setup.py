#!/usr/bin/env python3
"""Telegram setup helper for vbpub installs.

Goal: make it easy to set up a forum-enabled supergroup (Topics) as the target
for install notifications, so each install run is grouped into its own forum
thread/topic.

This script is intentionally "ops friendly": it can
- validate the bot token (`getMe`)
- help discover the target `TELEGRAM_CHAT_ID` by watching updates after you send
  a setup command in the target chat
- verify the chat supports forum topics by creating a test topic
- create the target forum-enabled supergroup via MTProto (user account)
- write/update keys in `scripts/netcup/.env`

Notes/limits (Telegram-side):
- Enabling Topics (forum) is a chat setting done in the Telegram UI.
- Adding/promoting the bot is also a chat admin action done in the UI.
- Bots cannot fetch arbitrary message history via Bot API; we rely on updates.

API references:
- Bot API (bots): https://core.telegram.org/bots/api
- MTProto API (user accounts): https://core.telegram.org/methods
    - Forums: https://core.telegram.org/api/forum
    - Create a forum supergroup: channels.createChannel
    - Toggle forum mode: channels.toggleForum

Usage (typical - default --env-file is scripts/netcup/.env, netcup's install
tooling being this script's primary consumer):
  python3 scripts/telegram/telegram_setup.py --setup-forum

You can override the env file (e.g. for a different consumer):
  python3 scripts/telegram/telegram_setup.py --setup-forum --env-file path/to/.env

During chat-id discovery you will be prompted to send `/vbpub_setup` in the
target forum supergroup.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import importlib.util
import asyncio
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


API_BASE = "https://api.telegram.org"


def _has_telethon() -> bool:
    return importlib.util.find_spec("telethon") is not None


def _redact_token(token: str) -> str:
    if not token:
        return ""
    if ":" not in token:
        return "***"
    left, right = token.split(":", 1)
    return f"{left}:***"


def _load_env_file(path: Path) -> Dict[str, str]:
    """Minimal .env parser (keeps behavior predictable; no shell expansion)."""
    if not path.exists():
        return {}

    out: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        # Support quoted values.
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        out[key] = value
    return out


def _write_env_file(path: Path, updates: Dict[str, str]) -> None:
    """Update/add keys while preserving unknown lines as much as possible."""
    existing_lines = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    keys_to_update = set(updates.keys())
    seen: set[str] = set()

    new_lines = []
    for raw_line in existing_lines:
        line = raw_line
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue

        key, _ = stripped.split("=", 1)
        key = key.strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new_lines.append(line)

    # Append missing keys at end.
    missing = [k for k in updates.keys() if k not in seen]
    if missing:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        for k in missing:
            new_lines.append(f"{k}={updates[k]}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _api_url(token: str, method: str) -> str:
    return f"{API_BASE}/bot{token}/{method}"


def _tg_get(token: str, method: str, params: Optional[Dict[str, Any]] = None, timeout: int = 35) -> Dict[str, Any]:
    url = _api_url(token, method)
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _tg_post(token: str, method: str, data: Optional[Dict[str, Any]] = None, timeout: int = 35) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data or {}).encode("utf-8")
    request = urllib.request.Request(_api_url(token, method), data=body, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_me(token: str) -> Dict[str, Any]:
    data = _tg_get(token, "getMe", timeout=15)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getMe failed: {data}")
    return data.get("result") or {}


def _resolve_bot_username_from_token(token: str) -> str:
    me = get_me(token)
    username = (me.get("username") or "").strip()
    if not username:
        raise RuntimeError("Bot username missing from getMe()")
    return username


def get_chat(token: str, chat_id: int) -> Dict[str, Any]:
    data = _tg_get(token, "getChat", params={"chat_id": chat_id}, timeout=15)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getChat failed: {data}")
    return data.get("result") or {}


def get_chat_member(token: str, chat_id: int, user_id: int) -> Dict[str, Any]:
    data = _tg_get(token, "getChatMember", params={"chat_id": chat_id, "user_id": user_id}, timeout=15)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getChatMember failed: {data}")
    return data.get("result") or {}


def _print_chat_diagnostics(token: str, *, chat_id: int, bot_id: int) -> None:
    try:
        chat = get_chat(token, chat_id)
    except Exception as e:
        print(f"Chat diagnostics: failed to call getChat(chat_id={chat_id}): {e}")
        return

    title = chat.get("title") or chat.get("username") or "(no title)"
    chat_type = chat.get("type") or "(unknown)"
    is_forum = chat.get("is_forum")

    print("Chat diagnostics:")
    print(f"- chat_id: {chat_id}")
    print(f"- title: {title}")
    print(f"- type: {chat_type}")
    if is_forum is not None:
        print(f"- is_forum: {bool(is_forum)}")
    else:
        print("- is_forum: (not provided by API)")

    if chat_type == "private":
        print("- note: this is a private chat; forum topics only work in supergroups with Topics enabled")

    try:
        member = get_chat_member(token, chat_id, bot_id)
    except Exception as e:
        print(f"- bot membership: failed getChatMember: {e}")
        return

    status = member.get("status")
    can_manage_topics = member.get("can_manage_topics")
    can_post_messages = member.get("can_post_messages")
    can_send_messages = member.get("can_send_messages")

    print("Bot membership:")
    print(f"- status: {status}")
    if can_manage_topics is not None:
        print(f"- can_manage_topics: {bool(can_manage_topics)}")
    if can_post_messages is not None:
        print(f"- can_post_messages: {bool(can_post_messages)}")
    if can_send_messages is not None:
        print(f"- can_send_messages: {bool(can_send_messages)}")
    print()


def delete_webhook(token: str) -> None:
    data = _tg_post(token, "deleteWebhook", data={"drop_pending_updates": True}, timeout=15)
    if not data.get("ok"):
        raise RuntimeError(f"deleteWebhook failed: {data}")


def fetch_updates(
    token: str,
    *,
    offset: Optional[int],
    timeout: int,
    allowed_updates: Iterable[str],
) -> list[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "timeout": timeout,
        "allowed_updates": list(allowed_updates),
    }
    if offset is not None:
        params["offset"] = offset

    data = _tg_get(token, "getUpdates", params=params, timeout=timeout + 10)
    if not data.get("ok"):
        raise RuntimeError(str(data))
    return data.get("result") or []


def bot_watch(env_file: Path, *, token: Optional[str], chat_id: Optional[int], watch_all: bool) -> int:
    """Poll Bot API getUpdates and print messages for debugging.

    This is best-effort and intended for live debugging during installs.
    For a bot to receive all messages in a supergroup, it should be an admin
    (privacy mode is bypassed for admins).

    Important limitation: Telegram does not reliably deliver messages sent by one bot
    to another bot via Bot API updates, even if both are admins. In practice this means
    a "reader bot" may NOT see messages sent by the "sender bot".

    If you need reliable live reading of install output, use --mtproto-watch.
    """

    env = _load_env_file(env_file)
    use_token = (
        token
        or env.get("TELEGRAM_READER_BOT_TOKEN")
        or os.environ.get("TELEGRAM_READER_BOT_TOKEN")
        or env.get("TELEGRAM_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
    )
    if not use_token:
        print("Missing TELEGRAM_READER_BOT_TOKEN (or TELEGRAM_BOT_TOKEN)", file=sys.stderr)
        return 2

    use_chat_id = chat_id
    if use_chat_id is None:
        use_chat_id = _parse_int_env(env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID"))
    if use_chat_id is None and not watch_all:
        print("Missing TELEGRAM_CHAT_ID (or pass --chat-id), or use --bot-watch-all", file=sys.stderr)
        return 2

    # Offset persistence: separate from existing debug reader to avoid clobbering.
    offset_file = env_file.parent / ".telegram.reader.offset"
    offset: Optional[int] = None
    if offset_file.exists():
        try:
            offset = int(offset_file.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            offset = None

    # Print some context.
    try:
        me = get_me(use_token)
        bot_user = (me.get("username") or "").strip() or "(unknown)"
    except Exception:
        bot_user = "(unknown)"
    print("Bot watch active")
    print(f"- bot: @{bot_user}")
    if watch_all:
        print("- chat_id: (watching all chats)")
    else:
        print(f"- chat_id: {use_chat_id}")
    print(f"- offset file: {offset_file}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            updates = fetch_updates(
                use_token,
                offset=offset,
                timeout=30,
                allowed_updates=["message", "edited_message", "channel_post"],
            )

            for update in updates:
                update_id = _extract_update_id(update)
                if update_id is not None:
                    offset = update_id + 1

                msg = _extract_message(update)
                if not msg:
                    continue

                chat = msg.get("chat") or {}
                chat_id_raw = chat.get("id")
                if chat_id_raw is None:
                    continue
                try:
                    msg_chat_id = int(chat_id_raw)
                except Exception:
                    continue

                if not watch_all and msg_chat_id != use_chat_id:
                    continue

                text = (msg.get("text") or msg.get("caption") or "").strip()
                if not text:
                    continue

                from_user = msg.get("from") or {}
                from_name = from_user.get("username") or from_user.get("first_name") or "(unknown)"
                msg_id = msg.get("message_id")
                thread_id = msg.get("message_thread_id")
                thread_hint = f" thread={thread_id}" if thread_id is not None else ""
                chat_hint = f" chat_id={msg_chat_id}" if watch_all else ""
                print(f"msg_id={msg_id}{thread_hint}{chat_hint} from={from_name}: {text}")

            if offset is not None:
                try:
                    offset_file.write_text(str(offset), encoding="utf-8")
                except Exception:
                    pass
    except KeyboardInterrupt:
        return 0


def _extract_message(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return update.get("message") or update.get("edited_message") or update.get("channel_post")


def _extract_text(update: Dict[str, Any]) -> str:
    msg = _extract_message(update) or {}
    return (msg.get("text") or msg.get("caption") or "").strip()


def _extract_chat(update: Dict[str, Any]) -> Dict[str, Any]:
    msg = _extract_message(update) or {}
    return msg.get("chat") or {}


def _extract_update_id(update: Dict[str, Any]) -> Optional[int]:
    value = update.get("update_id")
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


@dataclass
class DiscoveredChat:
    chat_id: int
    title: str


def discover_chat_id(
    token: str,
    *,
    command: str,
    bot_username: str,
    offset_file: Path,
    max_wait_seconds: int,
) -> Optional[DiscoveredChat]:
    offset: Optional[int] = None
    if offset_file.exists():
        try:
            offset = int(offset_file.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            offset = None

    deadline = time.time() + max_wait_seconds
    want_patterns = {
        command,
        f"{command}@{bot_username}" if bot_username else command,
    }

    while time.time() < deadline:
        try:
            updates = fetch_updates(
                token,
                offset=offset,
                timeout=min(30, max(1, int(deadline - time.time()))),
                allowed_updates=["message", "edited_message", "channel_post"],
            )
        except urllib.error.HTTPError as e:
            # Common failure if webhook is configured.
            raise
        except Exception:
            updates = []

        for update in updates:
            update_id = _extract_update_id(update)
            if update_id is not None:
                offset = update_id + 1

            text = _extract_text(update)
            if not text:
                continue

            # Only accept if command appears at the beginning (usual bot command pattern).
            # Still allow extra args.
            first = text.split(maxsplit=1)[0]
            if first not in want_patterns:
                continue

            chat = _extract_chat(update)
            chat_id_raw = chat.get("id")
            if chat_id_raw is None:
                continue
            try:
                chat_id = int(chat_id_raw)
            except Exception:
                continue

            title = chat.get("title") or chat.get("username") or str(chat_id)

            # Persist offset so we don't repeatedly match the same message.
            try:
                offset_file.write_text(str(offset or ""), encoding="utf-8")
            except Exception:
                pass

            return DiscoveredChat(chat_id=chat_id, title=title)

        if offset is not None:
            try:
                offset_file.write_text(str(offset), encoding="utf-8")
            except Exception:
                pass

        time.sleep(0.5)

    return None


def _import_telegram_client() -> Any:
    """Import TelegramClient from the sibling telegram_client.py (now in the
    same scripts/telegram/ directory - no cross-directory sys.path needed)."""
    from telegram_client import TelegramClient  # type: ignore

    return TelegramClient


def _require_mtproto_env(env: Dict[str, str]) -> tuple[int, str, str]:
    api_id = _parse_int_env(env.get("TELEGRAM_API_ID") or os.environ.get("TELEGRAM_API_ID"))
    api_hash = env.get("TELEGRAM_API_HASH") or os.environ.get("TELEGRAM_API_HASH")
    session_path = env.get("TELEGRAM_USER_SESSION_FILE") or os.environ.get("TELEGRAM_USER_SESSION_FILE")
    if api_id is None or not api_hash:
        raise RuntimeError(
            "Missing TELEGRAM_API_ID / TELEGRAM_API_HASH (MTProto user API creds). "
            "Get them from https://my.telegram.org/apps"
        )
    if not session_path:
        # Default next to env file handled by caller.
        session_path = ""
    return api_id, api_hash, session_path


def _isolated_session_path(session_path: str) -> str:
    """Create a per-process copy of a Telethon SQLite session file.

    Telethon stores sessions in SQLite. If two processes open the same session file
    concurrently, SQLite may raise 'database is locked'. To make monitoring reliable,
    we run watch/tail against a copied session DB.

    If the session file doesn't exist yet, we return the original path (first login).
    """

    src = Path(session_path)
    if not src.exists():
        return session_path

    tmp_dir = Path(tempfile.mkdtemp(prefix="vbpub-telethon-session-"))
    dst = tmp_dir / src.name

    try:
        shutil.copy2(src, dst)
    except Exception:
        return session_path

    # Copy common SQLite sidecar files if present.
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(str(src) + suffix)
        if sidecar.exists():
            try:
                shutil.copy2(sidecar, Path(str(dst) + suffix))
            except Exception:
                pass

    return str(dst)


def mtproto_watch(env_file: Path, *, chat_id: Optional[int], session_file: Optional[str]) -> int:
    if not _has_telethon():
        print(
            "Missing dependency 'telethon'. Install it with: pip install telethon",
            file=sys.stderr,
        )
        return 2

    from telethon import TelegramClient as MtTelegramClient  # type: ignore
    from telethon import events  # type: ignore
    from telethon.errors import SessionPasswordNeededError  # type: ignore

    env = _load_env_file(env_file)

    try:
        api_id, api_hash, env_session_path = _require_mtproto_env(env)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2

    phone = env.get("TELEGRAM_PHONE") or os.environ.get("TELEGRAM_PHONE")

    chat_id_val = chat_id
    if chat_id_val is None:
        chat_id_val = _parse_int_env(env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID"))

    if chat_id_val is None:
        print("Missing TELEGRAM_CHAT_ID (or pass --chat-id)", file=sys.stderr)
        return 2

    session_path = session_file or env_session_path or str(env_file.parent / ".telegram.user.session")
    session_path = _isolated_session_path(session_path)

    async def _run() -> int:
        client = MtTelegramClient(session_path, api_id, api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                if not phone:
                    phone_prompt = _prompt_required("TELEGRAM_PHONE (international format, e.g. +4917...)")
                else:
                    phone_prompt = phone

                await client.send_code_request(phone_prompt)
                code = _prompt_required("Enter the login code you received")
                try:
                    await client.sign_in(phone=phone_prompt, code=code)
                except SessionPasswordNeededError:
                    pw = _prompt_required("2FA is enabled. Enter your Telegram password")
                    await client.sign_in(password=pw)

            target = await client.get_entity(chat_id_val)
            print("MTProto watch active")
            print(f"- session file: {session_path}")
            print(f"- chat: {getattr(target, 'title', None) or getattr(target, 'username', None) or chat_id_val}")
            print(f"- chat_id: {chat_id_val}")
            print("Press Ctrl+C to stop.\n")

            @client.on(events.NewMessage(chats=target))
            async def _on_new_message(event: Any) -> None:
                msg = event.message
                text = (getattr(msg, "message", None) or getattr(msg, "raw_text", None) or "").strip()
                try:
                    sender = await event.get_sender()
                    sender_name = getattr(sender, "username", None) or getattr(sender, "first_name", None) or "(unknown)"
                except Exception:
                    sender_name = "(unknown)"

                # Forums: try to surface a thread/topic hint if available.
                thread_hint = ""
                reply_to = getattr(msg, "reply_to", None)
                top_id = getattr(reply_to, "reply_to_top_id", None) if reply_to is not None else None
                if top_id is not None:
                    thread_hint = f" thread_top_id={top_id}"

                print(f"[{msg.date}] from={sender_name} msg_id={msg.id}{thread_hint} text={text}")

            # Telethon's return type differs by version/stubs: sometimes a coroutine, sometimes None.
            maybe_coro = client.run_until_disconnected()
            if asyncio.iscoroutine(maybe_coro):
                await maybe_coro
            return 0
        finally:
            client.disconnect()

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 0


def mtproto_tail(
    env_file: Path,
    *,
    chat_id: Optional[int],
    session_file: Optional[str],
    limit: int,
) -> int:
    """Print the most recent messages from a chat via MTProto (user session).

    This complements --mtproto-watch by letting you review history that was sent
    before you started watching (useful for install runs).
    """

    if limit <= 0:
        print("--mtproto-tail N requires N > 0", file=sys.stderr)
        return 2

    if not _has_telethon():
        print(
            "Missing dependency 'telethon'. Install it with: pip install telethon",
            file=sys.stderr,
        )
        return 2

    from telethon import TelegramClient as MtTelegramClient  # type: ignore
    from telethon.errors import SessionPasswordNeededError  # type: ignore

    env = _load_env_file(env_file)

    try:
        api_id, api_hash, env_session_path = _require_mtproto_env(env)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2

    phone = env.get("TELEGRAM_PHONE") or os.environ.get("TELEGRAM_PHONE")

    chat_id_val = chat_id
    if chat_id_val is None:
        chat_id_val = _parse_int_env(env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID"))

    if chat_id_val is None:
        print("Missing TELEGRAM_CHAT_ID (or pass --chat-id)", file=sys.stderr)
        return 2

    session_path = session_file or env_session_path or str(env_file.parent / ".telegram.user.session")
    session_path = _isolated_session_path(session_path)

    async def _run() -> int:
        client = MtTelegramClient(session_path, api_id, api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                if not phone:
                    phone_prompt = _prompt_required("TELEGRAM_PHONE (international format, e.g. +4917...)")
                else:
                    phone_prompt = phone

                await client.send_code_request(phone_prompt)
                code = _prompt_required("Enter the login code you received")
                try:
                    await client.sign_in(phone=phone_prompt, code=code)
                except SessionPasswordNeededError:
                    pw = _prompt_required("2FA is enabled. Enter your Telegram password")
                    await client.sign_in(password=pw)

            target = await client.get_entity(chat_id_val)
            chat_name = getattr(target, "title", None) or getattr(target, "username", None) or str(chat_id_val)

            print("MTProto tail")
            print(f"- session file: {session_path}")
            print(f"- chat: {chat_name}")
            print(f"- chat_id: {chat_id_val}")
            print(f"- limit: {limit}")
            print()

            # iter_messages yields newest -> oldest.
            rows = []
            async for msg in client.iter_messages(target, limit=limit):
                try:
                    sender = await client.get_entity(msg.sender_id) if getattr(msg, "sender_id", None) else None
                    sender_name = (
                        getattr(sender, "username", None)
                        or getattr(sender, "first_name", None)
                        or getattr(sender, "title", None)
                        or "(unknown)"
                    )
                except Exception:
                    sender_name = "(unknown)"

                text = (getattr(msg, "message", None) or getattr(msg, "raw_text", None) or "").strip()

                thread_hint = ""
                reply_to = getattr(msg, "reply_to", None)
                top_id = getattr(reply_to, "reply_to_top_id", None) if reply_to is not None else None
                if top_id is not None:
                    thread_hint = f" thread_top_id={top_id}"

                rows.append(f"[{msg.date}] from={sender_name} msg_id={msg.id}{thread_hint} text={text}")

            # Print oldest -> newest for readability.
            for line in reversed(rows):
                print(line)

            return 0
        finally:
            client.disconnect()

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 0


def bot_send_test(env_file: Path, *, text: str, thread_id: Optional[int]) -> int:
    env = _load_env_file(env_file)
    token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", file=sys.stderr)
        return 2

    TelegramClient = _import_telegram_client()
    client = TelegramClient(bot_token=token, chat_id=str(chat_id))
    ok = client.send_message(text, prefix_source=True, message_thread_id=thread_id)
    return 0 if ok else 4


def _prompt_yes_no(question: str, default_yes: bool = True) -> bool:
    default = "Y/n" if default_yes else "y/N"
    while True:
        ans = input(f"{question} ({default}): ").strip().lower()
        if not ans:
            return default_yes
        if ans in {"y", "yes"}:
            return True
        if ans in {"n", "no"}:
            return False


def _prompt_required(question: str) -> str:
    while True:
        ans = input(f"{question}: ").strip()
        if ans:
            return ans


def _parse_int_env(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def setup_forum(env_file: Path, *, write_env: bool, force_discover_chat_id: bool) -> int:
    env = _load_env_file(env_file)
    token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Missing TELEGRAM_BOT_TOKEN (set it in scripts/netcup/.env)", file=sys.stderr)
        return 2

    try:
        me = get_me(token)
    except Exception as e:
        print(f"Failed to validate bot token ({_redact_token(token)}): {e}", file=sys.stderr)
        return 2

    bot_username = (me.get("username") or "").strip()
    bot_name = (me.get("first_name") or "").strip()
    bot_id = 0
    bot_id_raw = me.get("id")
    if bot_id_raw is not None:
        try:
            bot_id = int(bot_id_raw)
        except Exception:
            bot_id = 0

    print("Bot looks valid:")
    print(f"- username: @{bot_username}")
    print(f"- name: {bot_name}")
    print()

    chat_id_str = env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    chat_id: Optional[int] = None
    if chat_id_str:
        try:
            chat_id = int(chat_id_str)
        except Exception:
            chat_id = None

    if chat_id:
        print(f"Current TELEGRAM_CHAT_ID is set: {chat_id}")
        if force_discover_chat_id:
            discovered = True
        elif not _prompt_yes_no("Re-discover chat id via getUpdates?", default_yes=False):
            discovered = None
        else:
            discovered = True
    else:
        discovered = True

    discovered_chat: Optional[DiscoveredChat] = None
    if discovered:
        print("Next, create a Telegram supergroup and enable Topics (forum).")
        print("Then add the bot and promote it to admin (must be allowed to create topics).")
        print()
        print("When ready, send this exact command in the target chat:")
        print("  /vbpub_setup")
        print()

        offset_file = env_file.parent / ".telegram.setup.offset"

        try:
            discovered_chat = discover_chat_id(
                token,
                command="/vbpub_setup",
                bot_username=bot_username,
                offset_file=offset_file,
                max_wait_seconds=180,
            )
        except urllib.error.HTTPError as e:
            # Telegram returns 409 when a webhook is set.
            print(f"Failed to poll getUpdates: {e}", file=sys.stderr)
            print("If this bot is configured with a webhook, getUpdates will conflict.")
            print("Options:")
            print("- Run this with --delete-webhook (will drop pending updates)")
            print("- Or implement setup via webhook receiver instead")
            return 3

        if not discovered_chat:
            print("Timed out waiting for /vbpub_setup. Try again.", file=sys.stderr)
            print("Hints:")
            print("- Ensure you sent the command in the target supergroup")
            print("- Ensure the bot is present in the group")
            print("- If privacy mode is on, commands still work; send exactly /vbpub_setup")
            return 3

        chat_id = discovered_chat.chat_id
        print("Discovered chat:")
        print(f"- title: {discovered_chat.title}")
        print(f"- chat_id: {chat_id}")
        print()

    if not chat_id:
        print("No TELEGRAM_CHAT_ID available; cannot continue.", file=sys.stderr)
        return 2

    _print_chat_diagnostics(token, chat_id=chat_id, bot_id=bot_id)

    # Verify forum topic creation works.
    TelegramClient = _import_telegram_client()
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    os.environ["TELEGRAM_CHAT_ID"] = str(chat_id)

    client = TelegramClient(bot_token=token, chat_id=str(chat_id))

    topic_title = f"vbpub install test {time.strftime('%Y-%m-%d %H:%M:%S')}"
    print(f"Creating a test forum topic: {topic_title!r}")
    thread_id = client.create_forum_topic(topic_title)
    if thread_id is None:
        print("Forum topic creation failed.", file=sys.stderr)
        print("Most common causes:")
        print("- TELEGRAM_CHAT_ID is not a forum-enabled supergroup (Topics not enabled)")
        print("- Bot is not admin, or lacks rights to manage topics")
        print("- Chat id points to the wrong chat")
        return 4

    ok = client.send_message(
        f"✅ Forum topic creation works. message_thread_id={thread_id}",
        prefix_source=True,
        message_thread_id=thread_id,
    )
    if not ok:
        print("Created topic but failed to send a test message into it.", file=sys.stderr)
        return 4

    print(f"✅ Verified: can create topics and post into thread {thread_id}.")

    # Update env file.
    updates = {
        "TELEGRAM_BOT_TOKEN": env.get("TELEGRAM_BOT_TOKEN", token),
        "TELEGRAM_CHAT_ID": str(chat_id),
        "TELEGRAM_USE_FORUM_TOPIC": "auto",
        "TELEGRAM_TOPIC_PREFIX": env.get("TELEGRAM_TOPIC_PREFIX") or "vbpub install",
    }

    if write_env:
        _write_env_file(env_file, updates)
        print(f"Updated {env_file} with TELEGRAM_CHAT_ID and forum-topic settings.")
    else:
        print("Dry-run (no env file changes). Would write:")
        for k, v in updates.items():
            if k == "TELEGRAM_BOT_TOKEN":
                v = _redact_token(v)
            print(f"- {k}={v}")

    print()
    print("Next:")
    print("- Ensure your install environment exports TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")
    print("- On install, bootstrap will auto-create one topic per run")

    return 0


def setup_forum_mtproto(
    env_file: Path,
    *,
    write_env: bool,
    forum_title: Optional[str],
    forum_about: Optional[str],
    bot_username: Optional[str],
    session_file: Optional[str],
) -> int:
    """Create a forum-enabled supergroup using a user account (MTProto).

    This is a one-time setup path to avoid manual UI steps.
    """

    if not _has_telethon():
        print(
            "Missing dependency 'telethon'. Install it with: pip install telethon\n"
            "(or add it to requirements.txt and rebuild your environment)",
            file=sys.stderr,
        )
        return 2

    # Local import so the script remains usable without Telethon installed.
    from telethon import TelegramClient as MtTelegramClient  # type: ignore
    from telethon import utils as tg_utils  # type: ignore
    from telethon.errors import SessionPasswordNeededError  # type: ignore
    from telethon.tl.functions.channels import CreateChannelRequest, EditAdminRequest, InviteToChannelRequest  # type: ignore
    from telethon.tl.types import ChatAdminRights  # type: ignore

    env = _load_env_file(env_file)

    try:
        api_id, api_hash, env_session_path = _require_mtproto_env(env)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2

    bot_token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("Missing TELEGRAM_BOT_TOKEN (needed to validate bot + write env)", file=sys.stderr)
        return 2

    reader_bot_token = env.get("TELEGRAM_READER_BOT_TOKEN") or os.environ.get("TELEGRAM_READER_BOT_TOKEN")

    # Determine bot username (if not explicitly provided).
    try:
        bot_me = get_me(bot_token)
    except Exception as e:
        print(f"Failed to validate bot token ({_redact_token(bot_token)}): {e}", file=sys.stderr)
        return 2

    bot_username_resolved = (bot_me.get("username") or "").strip()
    if bot_username:
        bot_username_resolved = bot_username.lstrip("@").strip()
    if not bot_username_resolved:
        print("Could not determine bot username; pass --bot-username", file=sys.stderr)
        return 2

    reader_bot_username_resolved: Optional[str] = None
    if reader_bot_token:
        try:
            reader_bot_username_resolved = _resolve_bot_username_from_token(reader_bot_token)
        except Exception as e:
            print(f"Failed to validate TELEGRAM_READER_BOT_TOKEN ({_redact_token(reader_bot_token)}): {e}", file=sys.stderr)
            return 2

    title = forum_title or env.get("TELEGRAM_FORUM_TITLE") or "vbpub installs"
    about = forum_about or env.get("TELEGRAM_FORUM_ABOUT") or "Install and monitoring notifications"

    session_path = session_file or env_session_path or str(env_file.parent / ".telegram.user.session")

    print("MTProto setup (user account)")
    print(f"- session file: {session_path}")
    print(f"- forum title: {title!r}")
    print(f"- bot to invite/admin: @{bot_username_resolved}")
    if reader_bot_username_resolved:
        print(f"- reader bot to invite/admin: @{reader_bot_username_resolved}")
    print()

    async def _run() -> tuple[int, Optional[int]]:
        client = MtTelegramClient(session_path, api_id, api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                phone = env.get("TELEGRAM_PHONE") or os.environ.get("TELEGRAM_PHONE")
                if not phone:
                    phone = _prompt_required("TELEGRAM_PHONE (international format, e.g. +4917...)")

                await client.send_code_request(phone)
                code = _prompt_required("Enter the login code you received")
                try:
                    await client.sign_in(phone=phone, code=code)
                except SessionPasswordNeededError:
                    pw = _prompt_required("2FA is enabled. Enter your Telegram password")
                    await client.sign_in(password=pw)

            updates = await client(CreateChannelRequest(title=title, about=about, megagroup=True, forum=True))
            created_chat = None
            for ch in getattr(updates, "chats", []) or []:
                created_chat = ch
                break
            if created_chat is None:
                return 4, None

            chat_id = int(getattr(created_chat, "id"))
            bot_api_chat_id = int(f"-100{chat_id}")

            bot_entity = await client.get_entity(bot_username_resolved)
            channel_entity = await client.get_entity(created_chat)
            input_bot = tg_utils.get_input_user(bot_entity)
            input_channel = tg_utils.get_input_channel(channel_entity)
            if input_bot is None or input_channel is None:
                return 4, None
            await client(InviteToChannelRequest(channel=input_channel, users=[input_bot]))

            rights = ChatAdminRights(
                change_info=False,
                post_messages=False,
                edit_messages=False,
                delete_messages=False,
                ban_users=False,
                invite_users=True,
                pin_messages=True,
                add_admins=False,
                anonymous=False,
                manage_call=False,
                other=True,
                manage_topics=True,
            )
            await client(
                EditAdminRequest(
                    channel=input_channel,
                    user_id=input_bot,
                    admin_rights=rights,
                    rank="bot",
                )
            )

            # Optional: invite and promote a second "reader" bot.
            if reader_bot_username_resolved:
                reader_entity = await client.get_entity(reader_bot_username_resolved)
                input_reader = tg_utils.get_input_user(reader_entity)
                if input_reader is None:
                    return 4, None
                await client(InviteToChannelRequest(channel=input_channel, users=[input_reader]))

                # Make it admin so it can receive all group messages (bypasses privacy mode).
                reader_rights = ChatAdminRights(
                    change_info=False,
                    post_messages=False,
                    edit_messages=False,
                    delete_messages=False,
                    ban_users=False,
                    invite_users=False,
                    pin_messages=True,
                    add_admins=False,
                    anonymous=False,
                    manage_call=False,
                    other=False,
                    manage_topics=False,
                )
                await client(
                    EditAdminRequest(
                        channel=input_channel,
                        user_id=input_reader,
                        admin_rights=reader_rights,
                        rank="reader",
                    )
                )

            return 0, bot_api_chat_id
        finally:
            client.disconnect()

    try:
        rc, bot_api_chat_id = asyncio.run(_run())
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130
    except Exception as e:
        print(f"MTProto setup failed: {e}", file=sys.stderr)
        return 4

    if rc != 0 or bot_api_chat_id is None:
        print("Failed to create the forum supergroup via MTProto.", file=sys.stderr)
        return 4

    print("✅ Created forum supergroup via MTProto.")
    print(f"- Bot API chat id (use this in .env): {bot_api_chat_id}")
    print()

    # Sanity-check with Bot API: can we create forum topics now?
    TelegramClient = _import_telegram_client()
    os.environ["TELEGRAM_BOT_TOKEN"] = bot_token
    os.environ["TELEGRAM_CHAT_ID"] = str(bot_api_chat_id)
    bot_client = TelegramClient(bot_token=bot_token, chat_id=str(bot_api_chat_id))

    topic_title = f"vbpub install test {time.strftime('%Y-%m-%d %H:%M:%S')}"
    thread_id = bot_client.create_forum_topic(topic_title)
    if thread_id is None:
        print(
            "Created the forum supergroup, but the bot could not create a forum topic via Bot API.\n"
            "Likely causes: bot admin rights did not apply, or Topics/forum mode is not enabled.",
            file=sys.stderr,
        )
        print("You can still use the chat id; try enabling Topics in the UI or retry admin rights.")
        return 5

    bot_client.send_message(f"✅ Bot forum setup OK. message_thread_id={thread_id}", prefix_source=True, message_thread_id=thread_id)

    updates_env = {
        "TELEGRAM_BOT_TOKEN": env.get("TELEGRAM_BOT_TOKEN", bot_token),
        "TELEGRAM_CHAT_ID": str(bot_api_chat_id),
        "TELEGRAM_USE_FORUM_TOPIC": "auto",
        "TELEGRAM_TOPIC_PREFIX": env.get("TELEGRAM_TOPIC_PREFIX") or "vbpub install",
    }
    if write_env:
        _write_env_file(env_file, updates_env)
        print(f"Updated {env_file} with TELEGRAM_CHAT_ID and forum-topic settings.")
    else:
        print("Dry-run (no env file changes). Would write:")
        for k, v in updates_env.items():
            if k == "TELEGRAM_BOT_TOKEN":
                v = _redact_token(v)
            print(f"- {k}={v}")

    print()
    print("Next:")
    print("- You can now run installs with bot-only credentials (no MTProto needed for sending)")
    print("- If you need to read/monitor forum messages reliably, use MTProto (user session) or store logs externally")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="vbpub Telegram setup helper")
    parser.add_argument(
        "--env-file",
        default=str(Path(__file__).resolve().parent.parent / "netcup" / ".env"),
        help="Path to .env (default: scripts/netcup/.env - netcup is this tool's primary consumer; override for others)",
    )
    parser.add_argument("--setup-forum", action="store_true", help="Interactive wizard: discover chat id + verify forum topics")
    parser.add_argument(
        "--setup-forum-mtproto",
        action="store_true",
        help="Wizard: sign in with a user account (MTProto) to create a forum supergroup, add bot, grant admin rights",
    )
    parser.add_argument(
        "--mtproto-watch",
        action="store_true",
        help="Run a user-session (MTProto) reader that prints new messages from TELEGRAM_CHAT_ID",
    )
    parser.add_argument(
        "--mtproto-tail",
        type=int,
        metavar="N",
        help="Print the most recent N messages from TELEGRAM_CHAT_ID via MTProto (history review)",
    )
    parser.add_argument(
        "--bot-send-test",
        metavar="TEXT",
        help="Send a test message using the bot to TELEGRAM_CHAT_ID",
    )
    parser.add_argument(
        "--bot-watch",
        action="store_true",
        help="Poll getUpdates using TELEGRAM_READER_BOT_TOKEN (or TELEGRAM_BOT_TOKEN) and print messages in TELEGRAM_CHAT_ID",
    )
    parser.add_argument(
        "--bot-watch-all",
        action="store_true",
        help="Like --bot-watch, but prints messages from all chats (helps debug chat_id mismatches and delivery)",
    )
    parser.add_argument("--write-env", action="store_true", help="Write discovered settings back to --env-file")
    parser.add_argument(
        "--discover-chat-id",
        action="store_true",
        help="Force chat-id discovery via getUpdates even if TELEGRAM_CHAT_ID is already set",
    )
    parser.add_argument("--delete-webhook", action="store_true", help="Call deleteWebhook(drop_pending_updates=true) before polling updates")

    parser.add_argument("--forum-title", help="Title for the created forum supergroup (MTProto wizard)")
    parser.add_argument("--forum-about", help="About/description for the created forum supergroup (MTProto wizard)")
    parser.add_argument("--bot-username", help="Bot username to invite/admin (default: resolved from TELEGRAM_BOT_TOKEN)")
    parser.add_argument("--user-session-file", help="Telethon session file path (default: scripts/netcup/.telegram.user.session)")
    parser.add_argument("--chat-id", type=int, help="Chat id for mtproto watch (default: TELEGRAM_CHAT_ID)")
    parser.add_argument("--thread-id", type=int, help="Forum topic thread id (message_thread_id) for --bot-send-test")
    args = parser.parse_args()

    env_file = Path(args.env_file)

    if args.mtproto_tail is not None:
        return mtproto_tail(
            env_file,
            chat_id=args.chat_id,
            session_file=args.user_session_file,
            limit=args.mtproto_tail,
        )

    if args.mtproto_watch:
        return mtproto_watch(env_file, chat_id=args.chat_id, session_file=args.user_session_file)

    if args.bot_watch_all:
        return bot_watch(env_file, token=None, chat_id=args.chat_id, watch_all=True)

    if args.bot_watch:
        return bot_watch(env_file, token=None, chat_id=args.chat_id, watch_all=False)

    if args.bot_send_test is not None:
        return bot_send_test(env_file, text=args.bot_send_test, thread_id=args.thread_id)

    if args.setup_forum_mtproto:
        return setup_forum_mtproto(
            env_file,
            write_env=args.write_env,
            forum_title=args.forum_title,
            forum_about=args.forum_about,
            bot_username=args.bot_username,
            session_file=args.user_session_file,
        )

    if args.setup_forum:
        env = _load_env_file(env_file)
        token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        if args.delete_webhook:
            if not token:
                print("Missing TELEGRAM_BOT_TOKEN", file=sys.stderr)
                return 2
            try:
                delete_webhook(token)
                print("Webhook deleted (drop_pending_updates=true).")
            except Exception as e:
                print(f"Failed to delete webhook: {e}", file=sys.stderr)
                return 2

        return setup_forum(env_file, write_env=args.write_env, force_discover_chat_id=args.discover_chat_id)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
