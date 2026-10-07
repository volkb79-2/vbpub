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

nyxloom-P109 adds a SECOND, narrower instance of the same shape:
`mmctl token generate` PRINTS the minted token on stdout (`<token>:
<description>`, or a one-element JSON array under `--json`). The value is
never an ARGUMENT, so it does not reach the process table; it is read out of
the captured stdout of one `docker exec`, handed straight to S9.4a, and never
logged, never put in an exception message, and never returned to a caller
that prints. `_must` is deliberately NOT used for that one command, because
`_must`'s failure path folds stdout into the raised message.

PERSONAL ACCESS TOKENS (nyxloom-P109 / backlog B9) — what was MEASURED
----------------------------------------------------------------------
Every claim below was verified against a throwaway 11.10.1 instance, not read
off documentation, because three of them are the opposite of the obvious
guess:

* `mmctl --local token generate <user> <description>` needs
  `ServiceSettings.EnableUserAccessTokens = true`. With it false the command
  fails cleanly: `Personal access tokens are disabled on this server.`
* The TARGET account needs NO role grant. Mattermost's app layer checks only
  `EnableUserAccessTokens` (and a bot exemption); the `system_user_access_token`
  role gates a user minting their OWN token through the UI, and a local-mode
  session is unrestricted. Confirmed by minting against a plain `system_user`
  with no extra roles. This matters because `mmctl roles` can ONLY promote to
  or demote from system admin — there is no verb that could have granted it.
* The BOT-account escape (Mattermost exempts bots from
  `EnableUserAccessTokens` entirely) is NOT reachable here:
  `mmctl bot create` answers `This command cannot be run in local mode`.
  Reaching it would mean using the network admin API with an admin password,
  which is the exact widening this hook's local-mode design rejects. So the
  server-setting flip really is required; it was tested, not assumed.
* `mmctl --local token list <user>` prints `<id>: <description>` per token and
  exits 1 with `there are no tokens for the "<user>"` on stderr when there are
  none. The DESCRIPTION is what makes deduplication possible — the same
  display-name matching `_ensure_webhooks` uses.
* `token list` NEVER returns the token VALUE (only `id`, `user_id`,
  `description`, `is_active`, `expires_at`). Mattermost hands the secret out
  exactly once, at creation. That is what forces `_ensure_token`'s orphan
  refusal below: "the token exists" and "we still have the token" are
  different questions, and only the second one keeps the consumer working.
* `token revoke <token-id>` DELETES the row — it does not deactivate it, so a
  revoked token is absent from `token list --active` and `--inactive` alike.
  Rotation is therefore `revoke` + the next `ciu up`, exactly like a webhook.
"""
from __future__ import annotations

import json
import re
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
    """Roles of *username*, or an EMPTY SET when they cannot be read.

    Fail-OPEN, and only safe where the caller's use of an empty answer is
    itself safe. There is exactly one such caller: `_ensure_account`'s
    promotion guard, where "no roles" means "promote", and promoting an
    account that already holds the role is a server-side no-op. Erring toward
    a redundant promotion is harmless; aborting a deploy over a transient
    parse failure would not be.

    It is NOT safe for the demotion guard, where an empty answer reads as
    "not an admin, nothing to strip" — see `_require_user_roles`.
    """
    rc, parsed = _mmctl_json(container, "user", "search", username)
    if rc != 0 or not isinstance(parsed, dict):
        return set()
    return set(str(parsed.get("roles", "")).split())


def _require_user_roles(container: str, username: str) -> set[str]:
    """Roles of *username*, REFUSING rather than guessing when unreadable.

    The fail-open twin above cannot be used by `_demote_unintended_admins`:
    that guard exists to take `system_admin` away from an account Mattermost
    auto-promoted, and it decides by asking whether the role is present. An
    unreadable answer becomes "not an admin, skip", so an mmctl output-format
    drift would leave a freshly created service account holding server
    administration and say nothing — silently reinstating the exact defect the
    guard was added for.

    A privilege guard has to fail CLOSED. This one raises, which aborts the
    deploy with the account named. The blast radius of that is small and
    bounded to the case where it matters: it can only fire for an account the
    SAME run just created, i.e. on a fresh instance, which is precisely when
    an operator is present and wants to be told.
    """
    rc, out, err = _mmctl(container, "user", "search", username, as_json=True)
    if rc != 0:
        raise ProvisionError(
            f"cannot read the roles of {username!r} (rc={rc}): "
            f"{(err or out).strip()[:400]} — refusing to assume this account "
            "is not a system admin"
        )
    try:
        parsed = json.loads(out)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProvisionError(
            f"`mmctl --local --json user search {username}` did not emit JSON; "
            "the output format has changed and this hook can no longer tell "
            "whether the account is a system admin. Refusing rather than "
            "assuming it is not."
        ) from exc
    if not isinstance(parsed, dict) or "roles" not in parsed:
        raise ProvisionError(
            f"the JSON for {username!r} carries no `roles` field; refusing "
            "rather than assuming the account is not a system admin"
        )
    return set(str(parsed["roles"]).split())


_TEAM_MISSING_RE = re.compile(r"^unable to find team\b", re.IGNORECASE)


def _team_exists(container: str, team: str) -> bool:
    """True when a team named EXACTLY *team* exists.

    This must not be `rc == 0`, which is what it was until nyxloom-P110 and
    what made the hook unable to provision a genuinely empty instance at all.
    Measured on 11.10.1, against a Mattermost with no teams:

        $ mmctl --local team search nyxloom
        Unable to find team 'nyxloom'
        $ echo $?
        0

    Exit 0 for "not found". So `_ensure_team` concluded the team already
    existed, skipped `team create`, and the very next step —
    `_ensure_channels` -> `_channel_names` — aborted the whole deploy with
    `unable to find team "nyxloom"` (rc=1 there, because `channel list` does
    signal missing teams through its exit code). The bug was invisible for as
    long as it was, only because every run so far had P106's team already in
    place; "fresh stack create" is the exact case it breaks.

    `mmctl user search` is NOT affected — it exits 1 on a missing user
    (measured in the same session), which is why `_user_exists` is left as a
    returncode read.

    Matching is EXACT, not "the search returned a row": `team search probe`
    prints `probe-team: Probe (<id>)`, i.e. mmctl matches on a prefix. A
    substring hit would let `_ensure_team` skip creating `nyxloom` because
    some unrelated `nyxloom-archive` exists.
    """
    rc, out, err = _mmctl(container, "team", "search", team)
    if rc != 0:
        raise ProvisionError(
            f"cannot search for team {team!r} (rc={rc}): "
            f"{(err or out).strip()[:400]}"
        )
    found = False
    unrecognised = 0
    for raw in out.splitlines():
        line = raw.strip()
        if not line or _is_noise(line) or _TEAM_MISSING_RE.match(line):
            continue
        # `<name>: <display name> (<id>)`
        name, sep, rest = line.partition(": ")
        if not sep or not rest.endswith(")"):
            unrecognised += 1
            continue
        if name.strip() == team:
            found = True
    _refuse_unrecognised(
        unrecognised,
        what=f"the team search for {team!r}",
        fix="Compare `mmctl --local team search <team>` against "
        "_team_exists() — treating an unparseable row as 'no such team' "
        "would make this hook try to re-create a team that exists, and "
        "`team create` failing aborts every later deploy.",
    )
    return found


_COUNT_RE = re.compile(r"^there are (\d+) \w+ on local instance", re.IGNORECASE)
_EMPTY_SENTINELS = ("no users found", "no channels found", "no webhooks found")


def _refuse_unrecognised(unrecognised: int, *, what: str, fix: str) -> None:
    """Refuse when mmctl printed rows this parser did not recognise.

    This is the guard the nyxloom-P107 review (F1) correctly identified as
    missing. The webhook incident was NOT "saw two, picked wrong" — it was
    `_incoming_webhooks` returning an EMPTY list because mmctl's `Incoming:`
    prefix was not stripped. Against an empty parse, an ambiguity check on
    `len(found) > 1` can never fire: the caller concludes "nothing exists",
    creates, and does it again on the next `ciu up`, unbounded and silent.

    The invariant enforced instead is **every non-empty stdout row must be
    recognised**. It fires on the SECOND run — the first on which a duplicate
    could be minted — instead of whenever somebody happens to look, and it is
    applied to every list parser here, not just the webhook one.

    WHY NOT mmctl's own `There are N <things> on local instance` sentence,
    which was the obvious candidate and the first thing tried: **that N counts
    PRINTED LINES, not entities.** Measured on 11.10.1 against an empty
    private channel:

        stdout: No users found
        stderr: There are 1 userss on local instance

    One "user" reported, zero users. Cross-checking a parsed entity count
    against it therefore refuses a perfectly healthy empty channel — which is
    exactly what it did on the live instance before this rewrite. (The
    sentence is also on STDERR while the rows are on stdout, so a stdout-only
    scan finds no count line at all and refuses everything. Both mistakes were
    made and caught here.) Since N is just the printed-line count, it carries
    no information the line-recognition invariant does not already have, and
    it is no longer consulted.

    The refusal names the COUNT only, never a sample row: for
    `_incoming_webhooks` a row carries a webhook id, which is half the
    credential (S4.23).
    """
    if unrecognised:
        raise ProvisionError(
            f"mmctl printed {unrecognised} row(s) this hook could not parse "
            f"while listing {what}; the output format has changed and the "
            f"parse can no longer be trusted. Refusing rather than treating "
            f"an unrecognised list as 'nothing exists' and creating "
            f"duplicates. {fix}"
        )


def _is_noise(line: str) -> bool:
    """True for a line that is mmctl chatter rather than a data row.

    The count sentence and the `Unable to list outgoing webhooks` warning both
    go to STDERR in 11.10.1, so neither normally reaches a stdout parser; they
    are matched anyway because that placement is mmctl's choice, not a
    contract, and a future version moving one to stdout must not read as an
    unrecognised row and start refusing deploys.
    """
    low = line.lower()
    return (
        bool(_COUNT_RE.match(line))
        or low in _EMPTY_SENTINELS
        or low.startswith("unable to list outgoing")
    )


def _channel_names(container: str, team: str) -> list[str]:
    """Every ACTIVE channel name in *team*, public and private.

    Real `mmctl channel list` output (11.10.1, verified against the running
    server rather than assumed) — note the stream split:

        stdout: alerts
                installs
                p107-probe-arch (archived)
                p107-probe-priv (private)
        stderr: There are 6 channels on local instance

    So: private channels carry a ` (private)` SUFFIX and archived ones a
    ` (archived)` suffix. There is no `*` prefix — an earlier revision of this
    function stripped one, defending against a format that does not exist
    while missing the two that do. Both suffixes must come off, and archived
    channels must be DROPPED, because both feed straight into callers that
    use the name verbatim:

    * `_ensure_channels` would not match `foo (private)` against declared
      `foo`, would try to re-create it, and `_must` would abort the deploy —
      permanently, on every subsequent run. `private = true` is a supported
      option this hook already emits `--private` for, so this is reachable
      from the shipped config surface.
    * `_ensure_memberships`' `all_channels = true` (which `nyxloom-operator`
      uses) would call `_channel_members(team, "foo (archived)")`, mmctl would
      fail, and again every later `ciu up` aborts. Archiving a channel in the
      UI is enough to trigger it.

    Neither showed up in live testing only because all four current channels
    are public and unarchived.
    """
    rc, out, err = _mmctl(container, "channel", "list", team)
    if rc != 0:
        raise ProvisionError(
            f"cannot list channels of team {team!r} (rc={rc}): "
            f"{(err or out).strip()[:400]}"
        )
    names: list[str] = []
    unrecognised = 0
    for raw in out.splitlines():
        line = raw.strip()
        if not line or _is_noise(line):
            continue
        if line.endswith(" (archived)"):
            continue
        if line.endswith(" (private)"):
            names.append(line[: -len(" (private)")].strip())
            continue
        # A bare name is the normal case. An UNKNOWN parenthesised suffix is
        # not: mmctl marks channel kinds that way, so a future ` (shared)` or
        # ` (deleted)` would otherwise be silently treated as part of the
        # channel's name — matching nothing, and sending `_ensure_channels`
        # off to re-create a channel that already exists.
        if line.endswith(")") and " (" in line:
            unrecognised += 1
            continue
        names.append(line)
    _refuse_unrecognised(
        unrecognised,
        what=f"channels of team {team!r}",
        fix="Compare `mmctl --local channel list <team>` against "
        "_channel_names() — a new ` (<kind>)` suffix has to be classified as "
        "active or skipped.",
    )
    return names


def _channel_members(container: str, team: str, channel: str) -> set[str]:
    """Usernames in *channel*.

    `mmctl channel users list` prints one member per line as
    ``<id>: <username> (<email>) <role>`` — the username is what the caller
    compares against, so it is extracted rather than the whole line. Getting
    this wrong is silent: a whole-line set never matches a username, the
    membership guard never fires, and the hook re-adds every member on every
    run (observed once during nyxloom-P107, hence this note).

    `--all` is REQUIRED, not cosmetic: `channel users list` pages at 200 by
    default (`--per-page`), so past 200 members an unpaged read silently
    under-reports and re-triggers the very re-add loop this function's parsing
    fix closed. Harmless at four accounts; free to get right now.
    """
    rc, out, err = _mmctl(
        container, "channel", "users", "list", f"{team}:{channel}", "--all"
    )
    if rc != 0:
        raise ProvisionError(
            f"cannot list members of {team}:{channel} (rc={rc}): "
            f"{(err or out).strip()[:400]}"
        )
    members: set[str] = set()
    unrecognised = 0
    for raw in out.splitlines():
        line = raw.strip()
        if not line or _is_noise(line):
            continue
        _id, sep, rest = line.partition(": ")
        username, _, _tail = rest.partition(" (")
        if not sep or not username.strip():
            unrecognised += 1
            continue
        members.add(username.strip())
    _refuse_unrecognised(
        unrecognised,
        what=f"members of {team}:{channel}",
        fix="Compare `mmctl --local channel users list <team>:<chan> --all` "
        "against _channel_members().",
    )
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
    unrecognised = 0
    for raw in out.splitlines():
        line = raw.strip()
        if not line or _is_noise(line):
            continue
        # `Outgoing:` rows are skipped, not counted as drift: this stack
        # disables outgoing webhooks, but re-enabling them must not start
        # failing deploys over rows this function was never meant to read.
        if line.lower().startswith("outgoing:"):
            continue
        body = line.split(":", 1)[1].strip() if line.lower().startswith("incoming:") else ""
        if body.endswith(")") and " (" in body:
            display, _, ident = body.rpartition(" (")
            hooks.append({"display_name": display.strip(), "id": ident[:-1].strip()})
            continue
        unrecognised += 1
    _refuse_unrecognised(
        unrecognised,
        what=f"incoming webhooks of team {team!r}",
        fix="Compare `mmctl --local webhook list <team>` against "
        "_incoming_webhooks(). Do NOT re-run the hook until it parses — each "
        "run over an unparseable list mints another webhook.",
    )
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


def _demote_unintended_admins(container: str, prov: dict, created: list[str]) -> list[str]:
    """Strip `system_admin` from accounts THIS RUN created that never asked for it.

    Mattermost auto-promotes the FIRST-EVER account on an empty server to
    system_admin. Measured on 11.10.1 against a genuinely empty instance:

        create probe-first  -> roles: "system_admin system_user"
        create probe-second -> roles: "system_user"

    On a `ciu up` over a fresh stack that first account is whichever entry
    heads `[[mattermost.provision.accounts]]`. When this was written that was
    `nyxloom-daemon`, which declares `system_admin = false` and exists
    precisely so that the thing that only ever POSTs has no administrative
    rights — and a fresh create was measured handing it the role
    (`admin_role_stripped=['nyxloom-daemon']` on the run that found this).
    The account whose webhook credential is destined for third-party install
    hosts would have been a server administrator.

    The shipped config now heads that list with `nyxloom-admin`
    (`system_admin = true`), so in the normal case the promotion lands where
    it belongs and this step reports nothing. That ordering is a PREFERENCE,
    not the guard: it is one line away from being reordered, and an account
    list with no declared admin at the head would silently re-open the same
    hole. Both are kept on purpose.

    On the live instance nothing this hook creates is ever account #1 —
    `nyxloom-admin` predates it — so this step is a no-op there.

    Scope is deliberately narrow — ONLY accounts created in THIS run, and only
    where the spec says `system_admin` is not wanted. An account that already
    existed is still never touched: `_ensure_account`'s rule that this hook
    must not silently undo an operator's manual promotion is unchanged, and it
    is exactly why this cannot be a blanket "reconcile roles downward" pass.

    ORDERING is load-bearing, not incidental. Mattermost refuses to remove the
    role from the only remaining administrator — measured:

        $ mmctl --local roles member probe-first
        can't update roles for user "probe-first": Cannot demote last System
        Admin.                                                        (rc=1)

    So this runs AFTER the whole create-and-promote pass, once
    `nyxloom-operator` (`system_admin = true`) exists to be the other one. A
    config declaring no system admin at all would fail here with that message,
    which is the correct outcome: it says "you asked for a server with no
    administrator", not "the demotion is broken".
    """
    if not created:
        return []
    wanted_admin = {
        spec["username"]
        for spec in prov.get("accounts", [])
        if isinstance(spec, dict) and spec.get("system_admin")
    }
    demoted: list[str] = []
    for username in created:
        if username in wanted_admin:
            continue
        # `_require_user_roles`, not `_user_roles`: this guard must fail CLOSED.
        # An unreadable answer here would read as "not an admin, nothing to do"
        # and silently leave the role in place.
        if "system_admin" not in _require_user_roles(container, username):
            continue
        _must(
            container,
            "roles",
            "member",
            username,
            what=(
                f"strip the auto-granted system_admin role from {username!r} "
                "(Mattermost promotes the first account on an empty server; "
                "this account declares system_admin = false)"
            ),
        )
        demoted.append(username)
    return demoted


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
                    f"which is not an active channel of team {team!r} — it "
                    "does not exist, or it has been ARCHIVED (archived "
                    "channels are excluded from the active list on purpose; "
                    "unarchive it or drop it from "
                    "[[mattermost.provision.channels]])"
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
            # Cleans up AFTER a duplication; it does not prevent one. The
            # prevention lives in `_incoming_webhooks`' `_parse_or_refuse`
            # cross-check, because the incident that produced six webhooks was
            # an EMPTY parse, which this branch can never see. Both are needed.
            #
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
# personal access tokens (nyxloom-P109 / B9) — see the module docstring
# ---------------------------------------------------------------------------


def _user_tokens(container: str, username: str) -> list[dict]:
    """Active tokens of *username* as ``{'id', 'description'}`` dicts.

    Parses the PLAIN-TEXT `<id>: <description>` rows rather than `--json`,
    for the same reason `_incoming_webhooks` does: mmctl's `--json` container
    shape is not stable across result counts (`token list` prints the whole
    slice as one printer row, so it is bare `null` for zero and an array
    otherwise — while `post list` prints one row PER post and so emits a bare
    OBJECT for exactly one). One text parser with the drift guard already
    established here is safer than tracking that per command.

    An EMPTY token set is NOT an error: mmctl exits 1 and puts
    `there are no tokens for the "<user>"` on stderr. Treating that rc as a
    failure would abort every `ciu up` on a healthy first run; treating an
    UNPARSEABLE list as empty would mint a second token on every run, which
    is precisely the webhook incident F1 taught this hook to refuse.
    """
    rc, out, err = _mmctl(container, "token", "list", username)
    stream = f"{out}\n{err}"
    if rc != 0:
        if "no tokens for" in stream.lower():
            return []
        raise ProvisionError(
            f"cannot list access tokens of {username!r} (rc={rc}): "
            f"{(err or out).strip()[:400]}"
        )
    tokens: list[dict] = []
    unrecognised = 0
    for raw in out.splitlines():
        line = raw.strip()
        if not line or _is_noise(line):
            continue
        ident, sep, description = line.partition(": ")
        if not sep or not ident.strip():
            unrecognised += 1
            continue
        tokens.append({"id": ident.strip(), "description": description.strip()})
    _refuse_unrecognised(
        unrecognised,
        what=f"access tokens of {username!r}",
        fix="Compare `mmctl --local token list <user>` against _user_tokens(). "
        "Do NOT re-run the hook until it parses — each run over an "
        "unparseable list mints another token.",
    )
    return tokens


def _hook_secret_path(ctx, name: str) -> Path:
    """Store path of a HOOK-PERSISTED secret (S9.4a).

    `ctx.secret_file` cannot answer this: it resolves only names DECLARED in
    the secrets table and raises `KeyError` for everything else, which is
    every S9.4a name by definition (S9.4a's uniqueness rule forbids declaring
    one). The path is not an implementation detail being reached around —
    S9.4a states it normatively: `<stack>/.ciu/secrets/<name>`. Filed against
    ciu as the real gap it is (a hook cannot read back its own persisted
    secret through the context it was given).
    """
    return Path(ctx.stack_dir) / ".ciu" / "secrets" / name


def _generate_token(container: str, username: str, description: str,
                    expires_in: str | None) -> str:
    """Mint ONE personal access token and return its secret value.

    Deliberately NOT routed through `_must`: that helper folds stdout into
    the ProvisionError it raises, and this command's stdout IS the
    credential (S4.23). Nothing here is logged, printed or re-raised with
    the value in it — the refusals name the USER and the verb only.
    """
    argv = ["token", "generate", username, description]
    if expires_in:
        argv += ["--expires-in", expires_in]
    rc, out, err = _mmctl(container, *argv, as_json=True)
    if rc != 0:
        detail = (err or "").strip()[:200]
        raise ProvisionError(
            f"could not mint an access token for {username!r} (rc={rc}): {detail}. "
            "If this says personal access tokens are disabled, set "
            "MM_SERVICESETTINGS_ENABLEUSERACCESSTOKENS=true on the app service "
            "and re-run."
        )
    try:
        parsed = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        raise ProvisionError(
            f"token generate for {username!r} printed unparseable JSON; a token "
            "may have been created — check `mmctl --local token list` and revoke "
            "any extra before re-running"
        ) from None
    rows = parsed if isinstance(parsed, list) else [parsed]
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("token"), str) and row["token"]:
            return row["token"]
    raise ProvisionError(
        f"token generate for {username!r} returned no token value; a token may "
        "have been created — check `mmctl --local token list` and revoke any "
        "extra before re-running"
    )


def _ensure_tokens(container: str, ctx, config: dict, prov: dict) -> dict[str, str]:
    """Mint each declared PAT when absent; return secret name -> token value.

    A no-op while `[mattermost].enable_user_access_tokens` is false, and that
    gate is what lets this table ship BEFORE the operator decides to widen
    the server. Without it, merging a `[[mattermost.provision.tokens]]` entry
    would break the very next `ciu up` on the live stack, because minting
    against a server with PATs disabled is a hard failure. It is announced,
    not silent: a declared token that is not being minted prints its name.

    Only names in the RETURNED dict get re-persisted, so an already-provisioned
    token is left completely alone — no re-mint, no rotation, no second write.

    THE ORPHAN CASE, and why it refuses instead of doing something. Mattermost
    hands a token's value out exactly once, so this hook can ask "does a token
    with our description exist?" but can never ask "and is it the one we
    stored?". Three states, three answers:

      token absent, store file absent  -> mint, persist.        (first run)
      token present, store file present-> nothing.              (reconcile)
      token present, store file ABSENT -> REFUSE.

    The third is the one that must not be guessed at. Skipping (the naive
    "it exists, we're done") leaves the consumer with no credential forever
    and says nothing. Minting a second one leaves a live orphan token nobody
    can revoke by value and starts the unbounded duplication F1 exists to
    prevent. So it stops and names the exact `revoke` that resolves it —
    the same "will not choose one for you" stance `_ensure_webhooks` takes.
    """
    declared = prov.get("tokens", [])
    if declared and not _root(config).get("enable_user_access_tokens"):
        print(
            "[PROVISION] access tokens NOT minted "
            f"({sorted(s.get('secret', '?') for s in declared)}): "
            "[mattermost].enable_user_access_tokens is false. Set it (and "
            "MM_SERVICESETTINGS_ENABLEUSERACCESSTOKENS follows) to provision them.",
            flush=True,
        )
        return {}

    tokens: dict[str, str] = {}
    for spec in declared:
        username = spec["user"]
        description = spec["description"]
        secret_name = spec["secret"]
        found = [t for t in _user_tokens(container, username)
                 if t["description"] == description]
        stored = _hook_secret_path(ctx, secret_name).exists()
        if len(found) > 1:
            raise ProvisionError(
                f"{len(found)} access tokens of {username!r} share the "
                f"description {description!r}; revoke the extras "
                "(`mmctl --local token revoke <token-id>`) and re-run — this "
                "hook will not choose one for you"
            )
        if found and stored:
            continue
        if found and not stored:
            raise ProvisionError(
                f"account {username!r} already has an access token described "
                f"{description!r}, but its store file {secret_name!r} is gone. "
                "Mattermost only ever reveals a token's value once, so it "
                "cannot be recovered. Revoke it (`mmctl --local token revoke "
                f"<token-id>`, id from `mmctl --local token list {username}`) "
                "and re-run to mint a fresh one."
            )
        tokens[secret_name] = _generate_token(
            container, username, description, spec.get("expires_in"))
    return tokens


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

    # nyxloom-P109: `[[mattermost.provision.tokens]]`. The description is
    # checked as strictly as the secret name because it is the DEDUPLICATION
    # KEY (`_ensure_tokens`) — two token entries sharing one description on
    # one account would make every reconcile ambiguous and refuse the deploy.
    descriptions_seen: set[tuple[str, str]] = set()
    for spec in prov.get("tokens", []):
        if not isinstance(spec, dict):
            findings.append("[[mattermost.provision.tokens]] entry is not a table")
            continue
        secret = spec.get("secret")
        if not secret:
            findings.append("a token entry has no `secret` name to persist into")
        elif secret in declared:
            findings.append(
                f"token secret {secret!r} is ALSO declared in "
                "[mattermost.secrets]; S9.4a refuses a hook-persisted name "
                "that a directive already owns"
            )
        elif secret in secrets_seen:
            findings.append(f"token secret {secret!r} is declared twice")
        if secret:
            secrets_seen.add(secret)
        user = spec.get("user")
        if user not in usernames:
            findings.append(
                f"token {secret!r} belongs to {user!r}, which is not a "
                "declared account"
            )
        description = spec.get("description")
        if not description:
            findings.append(
                f"token {secret!r} has no `description` — it is the key this "
                "hook deduplicates on, so it is required, not cosmetic"
            )
        elif (user, description) in descriptions_seen:
            findings.append(
                f"account {user!r} has two tokens described {description!r}; "
                "reconcile could not tell them apart"
            )
        else:
            descriptions_seen.add((user, description))
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
    # After the whole create+promote pass, never inside it — Mattermost
    # refuses to demote the last remaining System Admin.
    demoted = _demote_unintended_admins(container, prov, accounts_created)
    memberships_added = _ensure_memberships(container, prov)
    webhook_urls = _ensure_webhooks(container, config, prov)
    token_values = _ensure_tokens(container, ctx, config, prov)

    # NAMES only, never values — the same S4.23 rule the webhook line follows
    # (a webhook URL and a PAT are both bearer credentials).
    print(
        f"[PROVISION] team_created={team_created} "
        f"channels_created={channels_created} "
        f"accounts_created={accounts_created} "
        f"admin_role_stripped={demoted} "
        f"memberships_added={memberships_added} "
        f"webhooks={sorted(webhook_urls)} "
        f"tokens_minted={sorted(token_values)}",
        flush=True,
    )

    # S9.4a: a webhook id does not exist until Mattermost mints it, so no S4
    # directive can express it — this is the channel that case exists for.
    # `apply_to_config` is forbidden alongside it and is deliberately absent:
    # a consumer reads the value back with `ctx.secret_file(<name>)`.
    result: dict = {
        name: {"value": url, "persist": "secret"} for name, url in webhook_urls.items()
    }
    # nyxloom-P109: a PAT is the same case for the same reason — it does not
    # exist until Mattermost mints it, so no S4 directive can express it.
    result.update(
        {name: {"value": value, "persist": "secret"} for name, value in token_values.items()}
    )
    result["provision.accounts_created"] = {
        "value": len(accounts_created),
        "persist": "state",
    }
    result["provision.memberships_added"] = {
        "value": memberships_added,
        "persist": "state",
    }
    # Deliberately recorded even though it is normally 0: a non-zero value is
    # the only durable trace that Mattermost's first-account auto-promotion
    # fired on this stack and was reversed.
    result["provision.admin_role_stripped"] = {
        "value": len(demoted),
        "persist": "state",
    }
    return result
