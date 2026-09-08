"""Idempotent Mattermost provisioning — ciu `post_compose` hook (S9.1).

Replaces the manual copy-paste `mmctl` recipe that `../README.md` carried
after nyxloom-P106: team, channels, accounts, channel memberships and
incoming webhooks are now declared as data in `[mattermost.provision]` and
reconciled on **every** `ciu up`.

WHY A HOOK AND NOT A ONE-SHOT INIT CONTAINER
--------------------------------------------
Both were evaluated (nyxloom-P107 workstream 2). The hook wins on three
mechanical grounds, not on taste:

1. **The webhook URL has to get back into ciu's secret store.** A webhook id
   does not exist until Mattermost mints it, so no S4 directive can express
   it — which is exactly the case S9.4a's `persist: "secret"` channel exists
   for. A sidecar container has no way to write into the store: it would have
   to be handed a writable bind mount of `<stack>/.ciu/secrets/`, i.e. hand
   a container write access to the credential store to avoid using the
   sanctioned API for writing to the credential store.
2. **Readiness is already solved here.** `ctx.wait_healthy()` (S9.3/CIU-4) is
   wired by the engine; a sidecar would re-implement a poll loop, which S9.3
   explicitly forbids where a helper suffices.
3. **`mmctl --local` needs the app container's local-mode socket**, which
   lives on the app container's own filesystem at
   `/var/tmp/mattermost_local.socket` and is NOT on a shared volume. A
   sidecar would need that socket exported through a new named volume — a
   *widening* of the admin surface (anything that can mount the volume gets
   unauthenticated system-admin) purely to avoid `docker exec`. This hook
   uses `docker exec` against the app container instead, which needs no new
   mount and no new network reachability.

S6.5's "stacks SHOULD NOT carry init containers" points the same way for the
sibling ownership problem — see `../ciu.defaults.toml.j2`'s hostdir tables.

RESIDUAL EXPOSURE (deliberate, documented — not hidden)
-------------------------------------------------------
`mmctl user create` and `user change-password` take the password as a command
line flag; there is no stdin or `*_FILE` form. So a generated password is
briefly visible in the host's process table as an argument of the short-lived
`docker exec` client. This is the same exposure the P106 README recipe had
(`--password "$MM_ADMIN_PW"`), minus that recipe's shell history and exported
environment variable. It is bounded to account CREATION: a reconcile run over
already-existing accounts passes no password at all.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

template_revision: int = 1

MMCTL = "/mattermost/bin/mmctl"


# ---------------------------------------------------------------------------
# mmctl plumbing
# ---------------------------------------------------------------------------


class ProvisionError(RuntimeError):
    """A provisioning step failed in a way that must stop the deploy."""


def _run(argv: list[str], *, timeout: int = 120) -> tuple[int, str, str]:
    """Run *argv*, returning ``(returncode, stdout, stderr)``.

    Never raises on a non-zero exit: several probes here (does this user
    exist?) use the exit code as their answer. A bare ``returncode`` read is
    only safe because every CALLER below classifies it explicitly.
    """
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _mmctl(container: str, *args: str, as_json: bool = False) -> tuple[int, str, str]:
    argv = ["docker", "exec", container, MMCTL, "--local"]
    if as_json:
        argv.append("--json")
    argv.extend(args)
    return _run(argv)


def _mmctl_json(container: str, *args: str) -> tuple[int, object]:
    """Run an mmctl subcommand with ``--json`` and parse stdout.

    Returns ``(rc, parsed)``; *parsed* is ``None`` when the command failed or
    emitted something that is not JSON (mmctl prints human-readable errors on
    both streams even under ``--json``).
    """
    rc, out, _err = _mmctl(container, *args, as_json=True)
    if rc != 0:
        return rc, None
    try:
        return rc, json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return rc, None


def _must(container: str, *args: str, what: str) -> str:
    """Run an mmctl subcommand that MUST succeed, or abort the deploy.

    The failing command's argv is reported WITHOUT its values so a password
    argument can never reach a log or a traceback (S4.23) — only the verb
    chain (`user create`, `channel users add`, ...) is named.
    """
    rc, out, err = _mmctl(container, *args)
    if rc != 0:
        verb = " ".join(args[:2])
        raise ProvisionError(
            f"{what}: `mmctl --local {verb} ...` failed (rc={rc}). "
            f"stderr: {(err or out).strip()[:400]}"
        )
    return out


# ---------------------------------------------------------------------------
# state probes — every mutating step is guarded by one of these (idempotency)
# ---------------------------------------------------------------------------


def _user_exists(container: str, username: str) -> bool:
    rc, _out, _err = _mmctl(container, "user", "search", username)
    return rc == 0


def _user_roles(container: str, username: str) -> set[str]:
    rc, parsed = _mmctl_json(container, "user", "search", username)
    if rc != 0 or not isinstance(parsed, dict):
        return set()
    return set(str(parsed.get("roles", "")).split())


def _team_exists(container: str, team: str) -> bool:
    rc, _out, _err = _mmctl(container, "team", "search", team)
    return rc == 0


def _channel_names(container: str, team: str) -> list[str]:
    """Every non-archived channel name in *team* (public and private).

    `mmctl channel list` prints one name per line plus a trailing count
    sentence; private channels are prefixed with `*` for a local-mode admin.
    """
    rc, out, err = _mmctl(container, "channel", "list", team)
    if rc != 0:
        raise ProvisionError(
            f"cannot list channels of team {team!r} (rc={rc}): "
            f"{(err or out).strip()[:400]}"
        )
    names: list[str] = []
    for raw in out.splitlines():
        line = raw.strip().lstrip("*").strip()
        if not line or line.lower().startswith("there are "):
            continue
        names.append(line)
    return names


def _channel_members(container: str, team: str, channel: str) -> set[str]:
    """Usernames in *channel*.

    `mmctl channel users list` prints one member per line as
    ``<id>: <username> (<email>) <role>`` — the username is what the caller
    compares against, so it is extracted rather than the whole line. Getting
    this wrong is silent: a whole-line set never matches a username, the
    membership guard never fires, and the hook re-adds every member on every
    run (observed once during nyxloom-P107, hence this note).
    """
    rc, out, err = _mmctl(container, "channel", "users", "list", f"{team}:{channel}")
    if rc != 0:
        raise ProvisionError(
            f"cannot list members of {team}:{channel} (rc={rc}): "
            f"{(err or out).strip()[:400]}"
        )
    members: set[str] = set()
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("there are "):
            continue
        _id, _, rest = line.partition(": ")
        if not rest:
            continue
        username, _, _tail = rest.partition(" (")
        if username:
            members.add(username.strip())
    return members


def _incoming_webhooks(container: str, team: str) -> list[dict]:
    """Incoming webhooks of *team*, as dicts with at least `id`/`display_name`.

    `mmctl webhook list` also tries OUTGOING webhooks, which this stack
    disables — that prints an `Unable to list outgoing webhooks` line and
    still exits 0. Under `--json` the outgoing failure makes the payload
    unparseable, so the incoming set is read from the plain-text form, whose
    exact shape (11.10.1) is:

        Incoming:<TAB><display-name> (<id>)

    The `Incoming:` prefix is load-bearing and must be stripped: leaving it on
    makes every lookup miss, and a MISS here is not a harmless no-op — it
    mints a SECOND webhook for the same display name on every `ciu up`. That
    happened during nyxloom-P107 (three runs, six webhooks) and is why
    `_ensure_webhooks` now also refuses an ambiguous display name outright
    instead of silently picking one.
    """
    rc, out, err = _mmctl(container, "webhook", "list", team)
    if rc != 0:
        raise ProvisionError(
            f"cannot list webhooks of team {team!r} (rc={rc}): "
            f"{(err or out).strip()[:400]}"
        )
    hooks: list[dict] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("there are "):
            continue
        if not line.lower().startswith("incoming:"):
            continue
        body = line.split(":", 1)[1].strip()
        if body.endswith(")") and " (" in body:
            display, _, ident = body.rpartition(" (")
            hooks.append({"display_name": display.strip(), "id": ident[:-1].strip()})
    return hooks


# ---------------------------------------------------------------------------
# config access
# ---------------------------------------------------------------------------


def _root(config: dict) -> dict:
    mm = config.get("mattermost")
    if not isinstance(mm, dict):
        raise ProvisionError("no [mattermost] table in the merged config")
    return mm


def _provision(config: dict) -> dict:
    prov = _root(config).get("provision")
    if not isinstance(prov, dict):
        raise ProvisionError("no [mattermost.provision] table in the merged config")
    return prov


def _container_name(config: dict) -> str:
    prefix = _root(config).get("container_prefix")
    if not prefix:
        raise ProvisionError("mattermost.container_prefix is empty")
    return f"{prefix}-mattermost"


def _base_urls(config: dict) -> tuple[str, str]:
    """Return ``(internal_url, siteurl)`` — the two webhook URL bases.

    `internal` is always the private-bridge address nyxloom's own daemon uses.
    `siteurl` mirrors what the compose template sets as
    `MM_SERVICESETTINGS_SITEURL`: the public host when `expose_public` is on,
    otherwise the same internal address. A consumer OFF this host (the
    debian-install-v2 progress reports) can only reach the `siteurl` form once
    `expose_public = true` — see the README.
    """
    mm = _root(config)
    app = mm.get("app", {}) if isinstance(mm.get("app"), dict) else {}
    internal = f"http://{mm['container_prefix']}-mattermost:{app.get('port', 8065)}"
    if mm.get("expose_public"):
        return internal, f"https://{mm.get('public_host')}"
    return internal, internal


def _read_secret(ctx, name: str) -> str:
    try:
        path: Path = ctx.secret_file(name)
    except KeyError as exc:  # pragma: no cover - contract violation path
        raise ProvisionError(
            f"secret {name!r} is not declared in [mattermost.secrets]"
        ) from exc
    if not path.exists():
        raise ProvisionError(f"secret {name!r} has no store file at {path}")
    value = path.read_bytes().decode("utf-8")
    if not value:
        raise ProvisionError(f"secret {name!r} store file is empty")
    return value


# ---------------------------------------------------------------------------
# reconcile steps
# ---------------------------------------------------------------------------


def _ensure_team(container: str, prov: dict) -> bool:
    team = prov["team"]
    if _team_exists(container, team):
        return False
    _must(
        container,
        "team",
        "create",
        "--name",
        team,
        "--display-name",
        prov.get("team_display_name", team),
        what=f"create team {team!r}",
    )
    return True


def _ensure_channels(container: str, prov: dict) -> list[str]:
    team = prov["team"]
    existing = set(_channel_names(container, team))
    created: list[str] = []
    for spec in prov.get("channels", []):
        name = spec["name"]
        if name in existing:
            continue
        args = [
            "channel",
            "create",
            "--team",
            team,
            "--name",
            name,
            "--display-name",
            spec.get("display_name", name),
        ]
        if spec.get("private"):
            args.append("--private")
        _must(container, *args, what=f"create channel {team}:{name}")
        created.append(name)
    return created


def _ensure_account(container: str, ctx, prov: dict, spec: dict) -> bool:
    """Create *spec*'s account when absent; return True when it was created.

    An EXISTING account is never touched — no password is re-sent, no role is
    revoked. Reconciling a password would mean writing a credential on every
    `ciu up` for no gain; reconciling roles DOWNWARD would let this hook
    silently demote an account an operator promoted by hand.
    """
    username = spec["username"]
    created = False
    if not _user_exists(container, username):
        password = _read_secret(ctx, spec["password_secret"])
        _must(
            container,
            "user",
            "create",
            "--email",
            spec["email"],
            "--username",
            username,
            "--password",
            password,
            what=f"create user {username!r}",
        )
        created = True

    # Team membership is added UNCONDITIONALLY and on purpose: `mmctl team
    # users` has no `list` subcommand (only `add`/`remove` — 11.10.1), so
    # there is no probe to guard on, and re-adding an existing member is a
    # server-side no-op that exits 0 (verified across repeated runs). This is
    # the one step here that is idempotent by the SERVER's contract rather
    # than by a local guard, which is why it is called out.
    team = prov["team"]
    _must(
        container,
        "team",
        "users",
        "add",
        team,
        username,
        what=f"add {username!r} to team {team!r}",
    )

    if spec.get("system_admin") and "system_admin" not in _user_roles(container, username):
        _must(
            container,
            "roles",
            "system-admin",
            username,
            what=f"promote {username!r} to system admin",
        )
    return created


def _ensure_memberships(container: str, prov: dict) -> int:
    """Join every account to the channels its spec asks for.

    `all_channels = true` resolves against the team's channel list AS IT IS
    AT THIS MOMENT, so a channel added to `[[mattermost.provision.channels]]`
    is covered on the same run that creates it. A channel created OUT OF BAND
    (in the Mattermost UI) is joined on the NEXT `ciu up` — Mattermost has no
    "member of all future channels" primitive, and polling for one would need
    a daemon this stack deliberately does not run.
    """
    team = prov["team"]
    all_channels = _channel_names(container, team)
    added = 0
    for spec in prov.get("accounts", []):
        username = spec["username"]
        wanted = list(all_channels) if spec.get("all_channels") else list(spec.get("channels", []))
        for channel in wanted:
            if channel not in all_channels:
                raise ProvisionError(
                    f"account {username!r} asks for channel {channel!r} "
                    f"which does not exist in team {team!r}"
                )
            if username in _channel_members(container, team, channel):
                continue
            _must(
                container,
                "channel",
                "users",
                "add",
                f"{team}:{channel}",
                username,
                what=f"add {username!r} to channel {team}:{channel}",
            )
            added += 1
    return added


def _ensure_webhooks(container: str, config: dict, prov: dict) -> dict[str, str]:
    """Create each declared incoming webhook when absent; return name -> URL.

    Matching is by DISPLAY NAME within the team, which is what an operator
    sees and what `webhook list` prints. Rotating a webhook stays a manual
    `mmctl webhook delete <id>` followed by the next `ciu up`, which mints a
    fresh id and re-persists the URL.
    """
    team = prov["team"]
    internal, siteurl = _base_urls(config)
    existing: dict[str, list[str]] = {}
    for hook in _incoming_webhooks(container, team):
        existing.setdefault(hook["display_name"], []).append(hook["id"])
    urls: dict[str, str] = {}

    for spec in prov.get("webhooks", []):
        display = spec["display_name"]
        found = existing.get(display, [])
        if len(found) > 1:
            # Never guess which of several same-named webhooks is "the" one:
            # persisting the wrong id would point a producer at a credential
            # an operator may be about to delete. Naming the count (not the
            # ids — S4.23) is enough for `mmctl webhook list` to finish the job.
            raise ProvisionError(
                f"{len(found)} incoming webhooks in team {team!r} share the "
                f"display name {display!r}; delete the extras "
                "(`mmctl --local webhook delete <id>`) and re-run — this hook "
                "will not choose one for you"
            )
        hook_id = found[0] if found else None
        if hook_id is None:
            out = _must(
                container,
                "webhook",
                "create-incoming",
                "--channel",
                f"{team}:{spec['channel']}",
                "--user",
                spec["user"],
                "--display-name",
                display,
                "--description",
                spec.get("description", ""),
                what=f"create incoming webhook {display!r}",
            )
            hook_id = _parse_webhook_id(out)
            if not hook_id:
                raise ProvisionError(
                    f"webhook {display!r} was created but mmctl printed no Id; "
                    "delete any partial webhook and re-run"
                )
        base = siteurl if spec.get("url_base") == "siteurl" else internal
        urls[spec["secret"]] = f"{base}/hooks/{hook_id}"
    return urls


def _parse_webhook_id(out: str) -> str | None:
    for raw in out.splitlines():
        line = raw.strip()
        if line.lower().startswith("id:"):
            return line.split(":", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# S9.5 preflight
# ---------------------------------------------------------------------------


def validate_config(config: dict, ctx) -> list:
    """Static shape check for `[mattermost.provision]` (S9.5).

    Runs under `ciu check` AND `ciu up`'s own preflight (S13.4c), so a typo in
    a username or an undeclared password secret is refused before anything
    starts rather than halfway through a live reconcile.
    """
    findings: list = []
    mm = config.get("mattermost")
    if not isinstance(mm, dict):
        return ["no [mattermost] table in the merged config"]
    prov = mm.get("provision")
    if not isinstance(prov, dict):
        return ["no [mattermost.provision] table — the provisioning hook has nothing to reconcile"]
    if not prov.get("team"):
        findings.append("[mattermost.provision].team is required")

    declared = mm.get("secrets", {})
    declared = declared if isinstance(declared, dict) else {}
    channel_names = {c.get("name") for c in prov.get("channels", []) if isinstance(c, dict)}
    if not channel_names:
        findings.append("[[mattermost.provision.channels]] is empty — declare at least one channel")

    usernames: set[str] = set()
    for spec in prov.get("accounts", []):
        if not isinstance(spec, dict):
            findings.append("[[mattermost.provision.accounts]] entry is not a table")
            continue
        name = spec.get("username")
        if not name:
            findings.append("an account entry has no `username`")
            continue
        if name in usernames:
            findings.append(f"account {name!r} is declared twice")
        usernames.add(name)
        if not spec.get("email"):
            findings.append(f"account {name!r} has no `email`")
        secret = spec.get("password_secret")
        if not secret:
            findings.append(f"account {name!r} has no `password_secret`")
        elif secret not in declared:
            findings.append(
                f"account {name!r} names password_secret {secret!r}, "
                "which is not declared in [mattermost.secrets]"
            )
        if spec.get("all_channels") and spec.get("channels"):
            findings.append(
                f"account {name!r} sets both `all_channels` and an explicit "
                "`channels` list — they contradict each other"
            )
        for channel in spec.get("channels", []):
            if channel not in channel_names:
                findings.append(
                    f"account {name!r} asks for channel {channel!r}, which is "
                    "not declared in [[mattermost.provision.channels]]"
                )

    secrets_seen: set[str] = set()
    for spec in prov.get("webhooks", []):
        if not isinstance(spec, dict):
            findings.append("[[mattermost.provision.webhooks]] entry is not a table")
            continue
        secret = spec.get("secret")
        if not secret:
            findings.append("a webhook entry has no `secret` name to persist into")
            continue
        if secret in declared:
            findings.append(
                f"webhook secret {secret!r} is ALSO declared in "
                "[mattermost.secrets]; S9.4a refuses a hook-persisted name "
                "that a directive already owns"
            )
        if secret in secrets_seen:
            findings.append(f"webhook secret {secret!r} is declared twice")
        secrets_seen.add(secret)
        if spec.get("channel") not in channel_names:
            findings.append(
                f"webhook {secret!r} targets channel {spec.get('channel')!r}, "
                "which is not declared in [[mattermost.provision.channels]]"
            )
        if spec.get("user") not in usernames:
            findings.append(
                f"webhook {secret!r} posts as {spec.get('user')!r}, which is "
                "not a declared account"
            )
        base = spec.get("url_base", "internal")
        if base not in ("internal", "siteurl"):
            findings.append(
                f"webhook {secret!r} has url_base={base!r}; "
                "expected 'internal' or 'siteurl'"
            )
    return findings


# ---------------------------------------------------------------------------
# S9.1 entry point
# ---------------------------------------------------------------------------


def run(config: dict, ctx) -> dict:
    prov = _provision(config)
    container = _container_name(config)

    if ctx.wait_healthy is None:
        # Bare/unit construction (S9.3): no engine, nothing running to talk to.
        return {}
    if not ctx.wait_healthy("mattermost", timeout_s=300.0):
        raise ProvisionError(
            f"container {container!r} did not reach healthy within 300s; "
            "provisioning refuses to run against an unready server rather "
            "than leaving a half-provisioned instance behind"
        )

    team_created = _ensure_team(container, prov)
    channels_created = _ensure_channels(container, prov)
    accounts_created = [
        spec["username"]
        for spec in prov.get("accounts", [])
        if _ensure_account(container, ctx, prov, spec)
    ]
    memberships_added = _ensure_memberships(container, prov)
    webhook_urls = _ensure_webhooks(container, config, prov)

    print(
        f"[PROVISION] team_created={team_created} "
        f"channels_created={channels_created} "
        f"accounts_created={accounts_created} "
        f"memberships_added={memberships_added} "
        f"webhooks={sorted(webhook_urls)}",
        flush=True,
    )

    # S9.4a: a webhook id does not exist until Mattermost mints it, so no S4
    # directive can express it — this is the channel that case exists for.
    # `apply_to_config` is forbidden alongside it and is deliberately absent:
    # a consumer reads the value back with `ctx.secret_file(<name>)`.
    result: dict = {
        name: {"value": url, "persist": "secret"} for name, url in webhook_urls.items()
    }
    result["provision.accounts_created"] = {
        "value": len(accounts_created),
        "persist": "state",
    }
    result["provision.memberships_added"] = {
        "value": memberships_added,
        "persist": "state",
    }
    return result
