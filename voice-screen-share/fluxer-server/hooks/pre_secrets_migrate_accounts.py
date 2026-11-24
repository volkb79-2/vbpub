"""Migrate legacy Fluxer account passwords before ciu secret materialization.

Older checkouts kept the demo-account passwords in
`.ciu/secrets/accounts.json`, outside ciu's secret store. This hook runs at
`pre_secrets` (S8.3 step 9), before GEN_LOCAL materialization at step 10:

* a declared account whose legacy entry exists and whose ciu store file is
  missing is written byte-for-byte to the path returned by
  `ctx.secret_file()`;
* an existing ciu store file is never overwritten;
* `accounts.json` is removed only after all present declared-account entries
  have been migrated or found already present.

The hook never prints a password or value.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

LEGACY_NAME = "accounts.json"


class MigrationError(RuntimeError):
    """A legacy password could not be migrated safely."""


def _root(config: dict) -> dict:
    root = config.get("fluxer")
    if not isinstance(root, dict):
        raise MigrationError("no [fluxer] table in the merged config")
    return root


def _account_names(root: dict) -> list[str]:
    cfg = root.get("accounts") or {}
    admin = cfg.get("admin")
    users = list(cfg.get("users") or [])
    if not admin or len(users) != 2:
        raise MigrationError("[fluxer.accounts] requires exactly one admin and two regular users")
    return [str(admin), *(str(name) for name in users)]


def _load_legacy(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError(f"cannot read legacy {LEGACY_NAME}: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise MigrationError(f"legacy {LEGACY_NAME} is not a JSON object")
    return data


def _ensure_private_dirs(ctx, target: Path) -> None:
    store_root = Path(ctx.repo_root) / ".ciu" / "secrets"
    store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(store_root.parent, 0o700)
    os.chmod(store_root, 0o700)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)


def _write_if_absent(ctx, target: Path, value: str) -> bool:
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise MigrationError(f"ciu secret path for {target.name!r} is not a regular file")
    if target.is_file():
        return False

    _ensure_private_dirs(ctx, target)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(value.encode("utf-8"))
        try:
            os.link(tmp, target)
        except FileExistsError:
            return False
        os.chmod(target, 0o600)
        return True
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def run(config: dict, ctx) -> dict:
    root = _root(config)
    legacy_path = Path(ctx.stack_dir) / ".ciu" / "secrets" / LEGACY_NAME
    legacy = _load_legacy(legacy_path)
    if legacy is None:
        return {}

    for username in _account_names(root):
        if username not in legacy:
            continue
        value = legacy[username]
        if not isinstance(value, str) or not value:
            raise MigrationError(f"legacy password for {username!r} is not a non-empty string")
        secret_name = f"account_{username}"
        try:
            target = Path(ctx.secret_file(secret_name))
        except KeyError as exc:
            raise MigrationError(f"ciu secret {secret_name!r} is not declared") from exc
        _write_if_absent(ctx, target, value)

    legacy_path.unlink()
    return {}
