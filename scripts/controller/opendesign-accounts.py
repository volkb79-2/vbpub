#!/usr/bin/env python3
"""Select and record Open Design account usage without handling API keys."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sys
import time


ACCOUNTS = ("volker", "github2")
FIELDS = {"last_used_epoch", "blocked_until_epoch", "window_end_epoch"}
DEFAULT_UNKNOWN_BLOCK_SECONDS = 6 * 60 * 60
# Codex renders the gateway's 402 as a plain-text message on a final `error` or
# `turn.failed` event (the `Reconnecting...` retries are deliberately not matched).
GATEWAY_402_PREFIX = re.compile(
    r"^unexpected status 402 Payment Required: \[code=insufficient_balance\]"
)
GATEWAY_402 = re.compile(
    r"unexpected status 402 Payment Required: \[code=insufficient_balance\].*?"
    r"\bresets at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)",
    re.DOTALL,
)


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
    state = {
        account: {"last_used_epoch": 0, "blocked_until_epoch": 0}
        for account in ACCOUNTS
    }
    state["threads"] = {}
    return state


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
    if not isinstance(value, dict) or set(value) - {*ACCOUNTS, "threads"}:
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
    threads = value.get("threads", {})
    if not isinstance(threads, dict) or any(
        not isinstance(thread_id, str) or not thread_id
        or account not in ACCOUNTS
        for thread_id, account in threads.items()
    ):
        raise StateError("invalid Open Design thread-account state schema")
    result["threads"] = dict(threads)
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


def gateway_text_reset(event):
    if not isinstance(event, dict) or event.get("type") not in ("error", "turn.failed"):
        return False, None
    message = event.get("message")
    if message is None and isinstance(event.get("error"), dict):
        message = event["error"].get("message")
    if not isinstance(message, str) or GATEWAY_402_PREFIX.match(message) is None:
        return False, None
    match = GATEWAY_402.match(message)
    if match is None:
        return True, None
    reset_epoch = epoch_from_iso(match.group(1))
    return True, (match.group(1), reset_epoch) if reset_epoch is not None else None


def coding_plan_reset(value):
    pending = [value]
    found = False
    latest = None
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if current.get("code") == "insufficient_balance":
                found = True
                details = current.get("details")
                if isinstance(details, dict):
                    reset_epoch = epoch_from_iso(details.get("reset_at"))
                    if reset_epoch is not None:
                        reset = (details["reset_at"], reset_epoch)
                        if latest is None or reset_epoch > latest[1]:
                            latest = reset
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return found, latest


def reset_from_files(paths):
    found = False
    latest = None
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
            text_found, text_reset = gateway_text_reset(event)
            json_found, json_reset = coding_plan_reset(event)
            found = found or text_found or json_found
            for reset in (text_reset, json_reset):
                if reset is not None and (latest is None or reset[1] > latest[1]):
                    latest = reset
    return found, latest


def unknown_block_seconds():
    raw = os.environ.get(
        "CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS", str(DEFAULT_UNKNOWN_BLOCK_SECONDS)
    )
    try:
        seconds = int(raw)
    except ValueError:
        seconds = 0
    if not re.fullmatch(r"[0-9]+", raw) or seconds <= 0 or seconds > 2_147_483_647:
        print(
            "invalid CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS; using the six-hour default",
            file=sys.stderr,
        )
        seconds = DEFAULT_UNKNOWN_BLOCK_SECONDS
    return seconds


def ensure_catalog_safe(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        print("refusing: deepseek catalog could not be read; repair the selected account's local catalog first", file=sys.stderr)
        raise SystemExit(3)
    models = data.get("models", []) if isinstance(data, dict) else None
    if not isinstance(models, list) or any(
        not isinstance(model, dict) or model.get("tool_mode") is not None
        for model in models
    ):
        print("refusing: deepseek catalog has a non-null tool_mode; repair the selected account's local catalog first", file=sys.stderr)
        raise SystemExit(3)


def thread_in_stream(path, thread_id):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(event, dict)
            and event.get("type") == "thread.started"
            and event.get("thread_id") == thread_id
        ):
            return True
    return False


def account_for_thread(state, thread_id, runs_dir):
    account = state["threads"].get(thread_id)
    if account is not None:
        return account
    found = set()
    root = Path(runs_dir)
    for route_path in root.glob("*.route"):
        route_to_account = {
            "deepseek-volker": "volker",
            "deepseek-github2": "github2",
        }
        account = route_to_account.get(route_path.read_text(encoding="utf-8").strip())
        if account is None:
            continue
        run = route_path.name[:-len(".route")]
        thread_path = root / f"{run}.thread"
        try:
            recorded_thread = thread_path.read_text(encoding="utf-8").strip()
        except OSError:
            recorded_thread = ""
        if recorded_thread == thread_id or thread_in_stream(root / f"{run}.jsonl", thread_id):
            found.add(account)
    if len(found) > 1:
        raise StateError("thread is recorded under multiple Open Design accounts")
    return next(iter(found)) if found else None


def main(argv):
    if len(argv) < 2:
        raise StateError(
            "usage: opendesign-accounts.py start <auto|volker|github2> <catalogs...> "
            "| start-resume <account> <thread-id> <volker-catalog> <github2-catalog> "
            "| resume <volker|github2> | find-thread-account <thread-id> <runs-dir> "
            "| record-thread <thread-id> <account> | block-run <account> <run-file>..."
        )
    action = argv[0]
    account = argv[1]
    if action not in ("start", "start-resume", "resume", "find-thread-account", "record-thread", "block-run"):
        raise StateError("unknown Open Design account action")
    if action == "start":
        if account == "auto" and len(argv) not in (4, 5):
            raise StateError("automatic start requires both account catalogs and an optional thread id")
        if account in ACCOUNTS and len(argv) not in (3, 4):
            raise StateError("pinned start requires its account catalog and an optional thread id")
        if account not in ("auto", *ACCOUNTS):
            raise StateError("unknown Open Design account")
    if action == "start-resume" and (account not in ACCOUNTS or len(argv) != 5):
        raise StateError("start-resume requires an account, thread id, and both account catalogs")
    if action in ("resume", "block-run") and account not in ACCOUNTS:
        raise StateError("unknown Open Design account")
    if action == "resume" and len(argv) != 2:
        raise StateError("resume requires one account")
    if action == "find-thread-account" and len(argv) != 3:
        raise StateError("find-thread-account requires a thread id and runs directory")
    if action == "record-thread" and (len(argv) != 3 or argv[1] == ""):
        raise StateError("record-thread requires a thread id and account")
    if action == "record-thread" and argv[2] not in ACCOUNTS:
        raise StateError("unknown Open Design account")
    if action == "block-run" and len(argv) < 3:
        raise StateError("block-run requires at least one run stream")

    path = state_path()
    with locked_state(path) as (stream, state):
        now = time.time()
        if action == "start":
            if account == "auto":
                catalogs = dict(zip(ACCOUNTS, argv[2:4]))
                thread_id = argv[4] if len(argv) == 5 else None
                selected = pick_available(state, now)
            else:
                catalogs = {account: argv[2]}
                thread_id = argv[3] if len(argv) == 4 else None
                selected = account
            ensure_catalog_safe(catalogs[selected])
            record_start(state, selected)
            if thread_id:
                state["threads"][thread_id] = selected
            save_state(stream, state)
            print(selected)
            return 0
        if action == "start-resume":
            thread_id, volker_catalog, github2_catalog = argv[2:5]
            catalogs = {"volker": volker_catalog, "github2": github2_catalog}
            selected = account
            if state[account]["blocked_until_epoch"] > now:
                other = next(name for name in ACCOUNTS if name != account)
                if state[other]["blocked_until_epoch"] > now:
                    earliest = min(state[name]["blocked_until_epoch"] for name in ACCOUNTS)
                    print(
                        "both Open Design accounts are blocked; earliest reset is " + format_epoch(earliest),
                        file=sys.stderr,
                    )
                    return 3
                selected = other
            ensure_catalog_safe(catalogs[selected])
            record_start(state, selected)
            state["threads"][thread_id] = selected
            save_state(stream, state)
            if selected != account:
                print(
                    f"account fallback from deepseek-{account} to deepseek-{selected} "
                    "because the recorded account is blocked",
                    file=sys.stderr,
                )
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
        if action == "find-thread-account":
            thread_id, runs_dir = argv[1:3]
            if not thread_id:
                raise StateError("thread id cannot be empty")
            found_account = account_for_thread(state, thread_id, runs_dir)
            if found_account is None:
                return 1
            print(found_account)
            return 0
        if action == "record-thread":
            thread_id, thread_account = argv[1:3]
            state["threads"][thread_id] = thread_account
            save_state(stream, state)
            return 0
        found_402, reset = reset_from_files(argv[2:])
        if not found_402:
            return 0
        if reset is None:
            block_seconds = unknown_block_seconds()
            observed_until = time.time() + block_seconds
            reset_description = f"reset time unknown; temporary block for {block_seconds} seconds"
        else:
            observed_until = reset[1]
            reset_description = "reset time observed"
        previous_until = state[account]["blocked_until_epoch"]
        blocked_until = max(previous_until, observed_until)
        if blocked_until != previous_until:
            state[account]["blocked_until_epoch"] = blocked_until
            save_state(stream, state)
        if previous_until > observed_until:
            reset_description += "; later existing block retained"
        print(
            f"Open Design account {account} {reset_description}; blocked until "
            f"{format_epoch(blocked_until)} (insufficient_balance)",
            file=sys.stderr,
        )
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, StateError) as exc:
        print(f"Open Design account state error: {exc}", file=sys.stderr)
        raise SystemExit(2)
