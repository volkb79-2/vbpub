"""Idempotent Jitsi user registration — ciu `post_compose` hook (S9.1).

Declared as data in `[[jitsi.users]]` (name + the GEN_LOCAL secret holding the
password). On EVERY `ciu up` each user is (re)registered in prosody's internal
auth store with `prosodyctl register`, which creates the account or, when it
exists, sets its password to the stored value. Same inputs, same end state:
that is the idempotency. Rotation is therefore: delete the secret file, `ciu up`
(see ../USERS.md).

CREDENTIAL HANDLING
-------------------
* The password is passed to the container on STDIN of `docker exec -i` and read
  there by a tiny `sh -c` wrapper. It is never a host-side argument (so not in
  the host process table), never printed, never in an exception message.
* RESIDUAL (documented, not hidden): `prosodyctl register <user> <domain>
  <password>` takes the password as an argument, so for the lifetime of that
  one in-container process it is visible in the CONTAINER's process table.
* CIU's secret files under `.ciu/secrets/jitsi/` are the only password store.
  The hook writes no aggregate credential file, removes the obsolete copy
  from an older checkout, and prints only usernames.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

template_revision: int = 1

_USER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}$")

# `read` the password from stdin inside the container; $1 = user, $2 = domain.
_REGISTER_SH = (
    'IFS= read -r pw; '
    'exec prosodyctl --config /run/prosody/config/prosody.cfg.lua register "$1" "$2" "$pw"'
)


class ProvisionError(RuntimeError):
    """A provisioning step failed in a way that must stop the deploy."""


def _root(config: dict) -> dict:
    return config["jitsi"]


def _container_name(config: dict) -> str:
    return f"{_root(config)['container_prefix']}-prosody"


def _read_secret(ctx, name: str) -> str:
    path: Path = ctx.secret_file(name)
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise ProvisionError(f"secret {name!r} is empty")
    return value


def _register(container: str, user: str, domain: str, password: str) -> None:
    """Register or update *user*; the password travels on stdin only."""
    proc = subprocess.run(
        ["docker", "exec", "-i", container, "sh", "-c", _REGISTER_SH, "sh", user, domain],
        input=password + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        # Deliberately NOT folding stdout/stderr in: prosodyctl could echo input.
        raise ProvisionError(
            f"prosodyctl register failed for user {user!r} (exit {proc.returncode})"
        )


def validate_config(config: dict, ctx) -> list:
    """Static check (`ciu check`): usernames valid, each secret declared."""
    problems: list = []
    root = _root(config)
    declared = set(root.get("secrets", {}))
    seen: set[str] = set()
    users = root.get("users", [])
    if len(users) != 3:
        problems.append("jitsi.users: exactly three accounts are required")
    for spec in users:
        name = spec.get("name", "")
        if not _USER_RE.match(name):
            problems.append(f"jitsi.users: invalid username {name!r}")
        if name in seen:
            problems.append(f"jitsi.users: duplicate username {name!r}")
        seen.add(name)
        if spec.get("secret") not in declared:
            problems.append(f"jitsi.users[{name}]: secret {spec.get('secret')!r} is not declared")
    return problems


def run(config: dict, ctx) -> dict:
    root = _root(config)
    try:
        (Path(ctx.stack_dir) / ".ciu" / "credentials.txt").unlink()
    except FileNotFoundError:
        pass
    users = root.get("users", [])
    if not root.get("auth", {}).get("enabled", True):
        print("[JITSI-USERS] auth disabled, nothing to register", flush=True)
        return {}
    if ctx.wait_healthy is None:
        raise ProvisionError("ctx.wait_healthy is not wired")
    if not ctx.wait_healthy("prosody", timeout_s=300.0):
        raise ProvisionError("prosody did not become healthy in time")

    container = _container_name(config)
    domain = root["auth"]["xmpp_domain"]
    registered: list[str] = []
    for spec in users:
        password = _read_secret(ctx, spec["secret"])
        _register(container, spec["name"], domain, password)
        registered.append(spec["name"])

    # Names only, never a value.
    print(f"[JITSI-USERS] registered={registered}", flush=True)
    return {"provision.users_registered": {"value": len(registered), "persist": "state"}}
