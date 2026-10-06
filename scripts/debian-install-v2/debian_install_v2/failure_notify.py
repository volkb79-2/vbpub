#!/usr/bin/env python3
"""Failure notifier for the bootstrap units (``OnFailure=`` target).

STDLIB ONLY plus ``debian_install_v2.notify`` (itself stdlib-only). It must run
when the failure being reported is a broken import path, so it never imports
``cli_extended`` or any other installer module (live v1001 2026-10-06: stage2
died on ``ModuleNotFoundError: cli_extended`` and nothing told the operator).

Run as ``failure_notify.py UNIT`` by ``vbpub-bootstrap-failed@UNIT.service``. It

* records ``status=failed``, the unit name and the redacted last journal lines
  in ``state.json`` (when a state file exists),
* sends a failure message through the configured backend (Mattermost webhook
  or Telegram) using the credential files the installer wrote,
* never prints or logs the webhook URL or token, and never raises (a status
  channel must not turn one failure into two).
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

if __name__ == "__main__":  # run by path: the package root is one level up
    _here = Path(__file__).resolve().parent
    sys.path[:] = [entry for entry in sys.path if entry and Path(entry).resolve() != _here]
    sys.path.insert(0, str(_here.parent))

from debian_install_v2.notify import (  # noqa: E402
    format_mattermost_message,
    post_webhook,
    redact_text,
)

FIXED_CREDENTIAL_DIR ="/etc/vbpub/credentials"
JOURNAL_LINES = 15
JOURNAL_TIMEOUT_S = 10
STATE_JOURNAL_LIMIT = 4000
UNIT_RE_OK = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:-_.\\@"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def credential_dirs(environ: dict[str, str], state_dir: str) -> list[Path]:
    """Same sources the installer's resume and vbpub-notify use, in that order."""
    dirs: list[Path] = []
    systemd_dir = environ.get("CREDENTIALS_DIRECTORY", "")
    if systemd_dir.startswith("/"):
        dirs.append(Path(systemd_dir))
    if state_dir:
        dirs.append(Path(state_dir) / "credentials")
    dirs.append(Path(FIXED_CREDENTIAL_DIR))
    return dirs


def load_credentials(environ: dict[str, str], state_dir: str) -> dict[str, str]:
    found = {"webhook": "", "token": "", "chat_id": ""}
    names = {"webhook": "mattermost_webhook_url", "token": "telegram_bot_token", "chat_id": "telegram_chat_id"}
    for directory in credential_dirs(environ, state_dir):
        for key, name in names.items():
            if not found[key]:
                found[key] = _read(directory / name)
    return found


def safe_unit_name(unit: str) -> str:
    cleaned = "".join(ch if ch in UNIT_RE_OK else "?" for ch in unit)[:120]
    return cleaned or "unknown-unit"


def read_journal(unit: str, secrets: tuple[str, ...]) -> list[str]:
    """Last journal lines of ``unit``, redacted; never raises."""
    journalctl = shutil.which("journalctl")
    if not journalctl:
        return ["(journalctl not available)"]
    try:
        result = subprocess.run(
            [journalctl, "-u", unit, "-n", str(JOURNAL_LINES), "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=JOURNAL_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"(journal unreadable: {type(exc).__name__})"]
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        lines = ["(no journal lines)"]
    return [redact_text(line, secrets) for line in lines[-JOURNAL_LINES:]]


def record_failure(state_path: Path, unit: str, journal: list[str]) -> bool:
    """Mark state.json failed. Atomic, 0600. False when there is no state to update."""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return False
    except (OSError, ValueError):
        return False
    tail = "\n".join(journal)
    if len(tail) > STATE_JOURNAL_LIMIT:
        tail = tail[-STATE_JOURNAL_LIMIT:]
    state["status"] = "failed"
    state["failed_unit"] = unit
    state["failed_at"] = datetime.now(timezone.utc).isoformat()
    state["failed_journal_tail"] = tail
    state["last_error"] = f"{unit} failed; see journalctl -u {unit}"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{state_path.name}.", dir=state_path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, state_path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return False
    return True


def stage_label(unit: str) -> str:
    name = unit.removeprefix("vbpub-bootstrap-").removesuffix(".service")
    return name or "bootstrap"


def failure_event(unit: str) -> str:
    return f"{unit} FAILED - see journalctl -u {unit}"


def send_telegram(token: str, chat_id: str, text: str, thread_id: str,
                  opener: Callable[..., object] = urllib.request.urlopen) -> bool:
    payload = {"chat_id": chat_id, "text": text[:4000]}
    if thread_id:
        payload["message_thread_id"] = thread_id
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{urllib.parse.quote(token)}/sendMessage",
        data=urllib.parse.urlencode(payload).encode("utf-8"),
    )
    try:
        response = opener(request, timeout=15)
        close = getattr(response, "close", None)
        if close:
            close()
        return True
    except Exception as exc:  # status channel; the type name only, never the URL
        print(f"failure_notify: telegram post failed ({type(exc).__name__})", file=sys.stderr)
        return False


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    environ = dict(os.environ) if environ is None else environ
    unit = safe_unit_name(argv[0] if argv else "vbpub-bootstrap-stage2.service")
    state_dir = environ.get("VBPUB_STATE_DIR", "")
    creds = load_credentials(environ, state_dir)
    secrets = tuple(value for value in (creds["webhook"], creds["token"]) if value)
    journal = read_journal(unit, secrets)

    state: dict = {}
    state_path = Path(state_dir) / "state.json" if state_dir.startswith("/") else None
    if state_path is not None:
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            state = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            state = {}
        recorded = record_failure(state_path, unit, journal)
        print(f"failure_notify: state.json {'updated' if recorded else 'not updated'} for {unit}")
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    excerpt = "\n".join(journal)

    if creds["webhook"]:
        text = format_mattermost_message(
            host_label=str(config.get("notify_host_label") or ""),
            server_name=platform.node() or "unknown-host",
            run_id=str(state.get("run_id") or ""),
            stage=stage_label(unit),
            event=failure_event(unit),
            status="fail",
            excerpt=excerpt,
            secrets=secrets,
        )
        sent = post_webhook(creds["webhook"], text)
        print(f"failure_notify: mattermost notification {'sent' if sent else 'FAILED'}")
    elif creds["token"] and creds["chat_id"]:
        text = redact_text(
            f"FAILED: {platform.node() or 'unknown-host'} | {failure_event(unit)}\n{excerpt}", secrets
        )
        sent = send_telegram(creds["token"], creds["chat_id"], text,
                             str(state.get("telegram_thread_id") or ""))
        print(f"failure_notify: telegram notification {'sent' if sent else 'FAILED'}")
    else:
        print("failure_notify: no notification credentials found; state.json only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
