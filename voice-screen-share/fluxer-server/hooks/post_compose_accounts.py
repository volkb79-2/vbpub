"""Idempotent admin + two-user provisioning (ciu `post_compose` hook).

Fluxer has no admin CLI that creates users; the first administrator is the
first account registered with the instance's setup token (the browser wizard
does exactly that), and every other account signs up through the public
registration API. This hook drives the same API, from INSIDE the api
container (`docker exec -i ... node`, see fluxer_accounts.mjs): no host port,
the registration proof-of-work solved with the image's own altcha-lib, and
every credential travels on stdin only.

Credentials: every password is a ciu `GEN_LOCAL` secret, read through
`ctx.secret_file()` and sent to the account script on stdin. This hook never
mints or writes a password and prints only result CODES per username
(`admin_created`, `user_exists`, ...). USERS.md has the rotation recipe and
the one step that stays manual (nothing is required in the browser: the setup
wizard's other steps are optional branding).

Safe to re-run on every `ciu up`: each account is probed with a sign-in first
and only created when that fails.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("fluxer_accounts.mjs")


class AccountsError(RuntimeError):
    """Provisioning failed in a way that must stop the deploy."""


def _root(config: dict) -> dict:
    root = config.get("fluxer")
    if not isinstance(root, dict):
        raise AccountsError("no [fluxer] table in the merged config")
    return root


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AccountsError(f"cannot read {path} ({type(exc).__name__}); run the pre_compose hook first") from exc
    if not isinstance(data, dict):
        raise AccountsError(f"{path.name} is not a JSON object")
    return data


def _account_names(root: dict) -> list[str]:
    cfg = root.get("accounts") or {}
    admin_name = cfg.get("admin")
    user_names = list(cfg.get("users") or [])
    if not admin_name or len(user_names) != 2:
        raise AccountsError("[fluxer.accounts] requires exactly one admin and two regular users")
    return [str(admin_name), *(str(name) for name in user_names)]


def _read_account_password(ctx, username: str) -> str:
    name = f"account_{username}"
    try:
        path = Path(ctx.secret_file(name))
        password = path.read_text(encoding="utf-8")
    except KeyError as exc:
        raise AccountsError(f"ciu secret {name!r} is not materialized") from exc
    except OSError as exc:
        raise AccountsError(f"cannot read ciu secret {name!r}: {type(exc).__name__}") from exc
    if not password:
        raise AccountsError(f"ciu secret {name!r} is empty")
    return password


def build_payload(root: dict, passwords: dict[str, str], setup_token: str) -> dict:
    cfg = root.get("accounts") or {}
    admin_name, *user_names = _account_names(root)
    missing = [name for name in [admin_name, *user_names] if not passwords.get(name)]
    if missing:
        raise AccountsError(f"ciu secret is missing for: {', '.join(missing)}")
    return {
        "admin": {"username": admin_name, "password": passwords[admin_name]},
        "users": [{"username": name, "password": passwords[name]} for name in user_names],
        "setup_token": setup_token,
        "date_of_birth": cfg.get("date_of_birth", "1990-01-01"),
        "registration_mode_after": cfg.get("registration_mode_after", "closed"),
    }


def run_in_container(container: str, payload: dict, *, timeout: int = 600) -> dict:
    argv = [
        "docker", "exec", "-i", container,
        "node", "--input-type=module", "-e", SCRIPT.read_text(encoding="utf-8"),
    ]
    proc = subprocess.run(
        argv,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        # Never fold child output into errors: user passwords travel on stdin
        # and a buggy debug print must not turn into a CIU log entry.
        raise AccountsError(f"account script exited {proc.returncode}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise AccountsError("account script printed no JSON result") from exc


def run(config: dict, ctx) -> dict:
    root = _root(config)
    if ctx.wait_healthy is None:
        return {}
    container = f"{root['container_prefix']}-api"
    if not ctx.wait_healthy("api", timeout_s=420.0):
        raise AccountsError(f"{container} did not become healthy within 420s; accounts are not provisioned")
    directory = Path(ctx.stack_dir) / ".ciu" / "secrets"
    store = _read_json(directory / "fluxer-secrets.json")
    passwords = {name: _read_account_password(ctx, name) for name in _account_names(root)}
    result = run_in_container(container, build_payload(root, passwords, store["setup_token"]))
    failed = {k: v for k, v in result.items() if "failed" in str(v)}
    print(f"[FLUXER-ACCOUNTS] {json.dumps(result, sort_keys=True)}", flush=True)
    if failed:
        raise AccountsError(f"provisioning step(s) failed: {sorted(failed)}")
    return {}
