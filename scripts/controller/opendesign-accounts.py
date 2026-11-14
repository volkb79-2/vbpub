#!/usr/bin/env python3
"""Select and record Open Design account usage without handling API keys."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import sys
import time


ACCOUNTS = ("volker", "github2")
FIELDS = {"last_used_epoch", "blocked_until_epoch", "window_end_epoch"}


class StateError(ValueError):
    pass


def state_path():
    configured = os.environ.get("CONTROLLER_OD_STATE")
    if configured:
        path = Path(configured)
    else:
        runs_dir = os.environ.get("CONTROLLER_RUNS_DIR")
        if not runs_dir:
            raise StateError("CONTROLLER_RUNS_DIR is required for the default account state path")
        path = Path(runs_dir) / "opendesign-accounts.json"
    if not path.is_absolute():
        raise StateError("CONTROLLER_OD_STATE must be an absolute path")
    return path


def fresh_state():
    return {
        account: {"last_used_epoch": 0, "blocked_until_epoch": 0}
        for account in ACCOUNTS
    }


def numeric(value, field, account):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StateError(f"invalid {field} for account {account}")
    try:
        if not math.isfinite(value):
            raise StateError(f"invalid {field} for account {account}")
    except OverflowError:
        raise StateError(f"invalid {field} for account {account}") from None
    return value


def normalize_state(value):
    if not isinstance(value, dict) or set(value) - set(ACCOUNTS):
        raise StateError("invalid Open Design account state schema")
    result = fresh_state()
    for account in ACCOUNTS:
        entry = value.get(account, {})
        if not isinstance(entry, dict) or set(entry) - FIELDS:
            raise StateError("invalid Open Design account state schema")
        result[account]["last_used_epoch"] = numeric(
            entry.get("last_used_epoch", 0), "last_used_epoch", account
        )
        result[account]["blocked_until_epoch"] = numeric(
            entry.get("blocked_until_epoch", 0), "blocked_until_epoch", account
        )
        if "window_end_epoch" in entry and entry["window_end_epoch"] is not None:
            result[account]["window_end_epoch"] = numeric(
                entry["window_end_epoch"], "window_end_epoch", account
            )
    return result


def save_state(stream, state):
    stream.seek(0)
    stream.truncate()
    json.dump(state, stream, sort_keys=True, indent=2)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())


@contextmanager
def locked_state(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.seek(0)
        content = stream.read()
        try:
            state = normalize_state(json.loads(content)) if content.strip() else fresh_state()
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            if isinstance(exc, StateError):
                raise
            raise StateError("invalid Open Design account state file") from None
        if not content.strip():
            save_state(stream, state)
        yield stream, state


def epoch_from_iso(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp()) if parsed.tzinfo is not None else None
    except (OverflowError, OSError, ValueError):
        return None


def format_epoch(value):
    try:
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError) as exc:
        raise StateError("invalid blocked_until_epoch in Open Design account state") from exc


def available_accounts(state, now):
    return [account for account in ACCOUNTS if state[account]["blocked_until_epoch"] <= now]


def pick_available(state, now):
    candidates = available_accounts(state, now)
    if not candidates:
        earliest = min(state[account]["blocked_until_epoch"] for account in ACCOUNTS)
        print(
            "both Open Design accounts are blocked; earliest reset is " + format_epoch(earliest),
            file=sys.stderr,
        )
        raise SystemExit(3)

    prefer_expiring = os.environ.get("CONTROLLER_OD_PREFER_EXPIRING", "0")
    if prefer_expiring not in ("0", "1"):
        raise StateError("CONTROLLER_OD_PREFER_EXPIRING must be 0 or 1")
    if prefer_expiring == "1":
        expiring = [
            account
            for account in candidates
            if state[account].get("window_end_epoch") is not None
            and state[account]["window_end_epoch"] > now
        ]
        if expiring:
            return min(
                expiring,
                key=lambda account: (
                    state[account]["window_end_epoch"],
                    state[account]["last_used_epoch"],
                    ACCOUNTS.index(account),
                ),
            )
    return min(
        candidates,
        key=lambda account: (state[account]["last_used_epoch"], ACCOUNTS.index(account)),
    )


def record_start(state, account):
    now = time.time_ns() / 1_000_000_000
    latest = max(state[name]["last_used_epoch"] for name in ACCOUNTS)
    state[account]["last_used_epoch"] = max(now, latest + 0.000001)


def coding_plan_reset(value):
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if current.get("code") == "insufficient_balance":
                details = current.get("details")
                if isinstance(details, dict):
                    reset_epoch = epoch_from_iso(details.get("reset_at"))
                    if reset_epoch is not None:
                        return details["reset_at"], reset_epoch
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return None


def reset_from_files(paths):
    for path in paths:
        try:
            lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            reset = coding_plan_reset(event)
            if reset:
                return reset
    return None


def main(argv):
    if len(argv) < 2:
        raise StateError("usage: opendesign-accounts.py start <auto|volker|github2> | resume <volker|github2> | block-run <account> <run-file>...")
    action = argv[0]
    account = argv[1]
    if action not in ("start", "resume", "block-run"):
        raise StateError("unknown Open Design account action")
    if action == "start" and account not in ("auto", *ACCOUNTS):
        raise StateError("unknown Open Design account")
    if action in ("resume", "block-run") and account not in ACCOUNTS:
        raise StateError("unknown Open Design account")
    if action == "block-run" and len(argv) < 3:
        raise StateError("block-run requires at least one run stream")

    path = state_path()
    with locked_state(path) as (stream, state):
        now = time.time()
        if action == "start":
            selected = pick_available(state, now) if account == "auto" else account
            record_start(state, selected)
            save_state(stream, state)
            print(selected)
            return 0
        if action == "resume":
            if state[account]["blocked_until_epoch"] > now:
                other = next(name for name in ACCOUNTS if name != account)
                if state[other]["blocked_until_epoch"] > now:
                    earliest = min(state[name]["blocked_until_epoch"] for name in ACCOUNTS)
                    print(
                        "both Open Design accounts are blocked; earliest reset is " + format_epoch(earliest),
                        file=sys.stderr,
                    )
                    return 3
                account = other
            print(account)
            return 0
        reset = reset_from_files(argv[2:])
        if reset is None:
            return 0
        reset_text, reset_epoch = reset
        state[account]["blocked_until_epoch"] = reset_epoch
        save_state(stream, state)
        print(f"Open Design account {account} blocked until {reset_text} (insufficient_balance)", file=sys.stderr)
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, StateError) as exc:
        print(f"Open Design account state error: {exc}", file=sys.stderr)
        raise SystemExit(2)
