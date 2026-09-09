"""Mattermost <-> feature-intake chat bridge. PACKAGE P109 (backlog B9).

B9 ("intake-over-ntfy chatbot") retargeted at Mattermost after the P106/P107
cutover. `intake_chat.py` (P29) is the whole conversational backend already:
a resumable read-only redacted session that interviews a human from a rough
feature request to a carve-ready `BRIEF:` block. It has no transport. This
module is that transport, and NOTHING else -- it reads new posts out of one
Mattermost channel, folds them into ONE `intake_chat.advance_intake` turn,
and posts the reply back through `notify.send()`.

WHY NOT `commands.CommandListener` (P12), which decision_chat wraps: that
listener long-polls ntfy's `/{topic}/json` endpoint and is ntfy-specific
down to its loop-guard tag. `decision_chat`'s own docstring already records
the consequence -- "the INBOUND half is NOT migrated and is still ntfy-only
... a shared inbound-message capability is a separate, larger package". This
is that package's Mattermost half; it deliberately does not widen
CommandListener, and it does not import decision_chat.

NO DAEMON, NO LOOP (deliberate). `poll_once` polls exactly once and returns.
There is no thread, no sleep and no signal handling anywhere in this module,
so the scheduled-jobs subsystem (B20/F015, unbuilt) can adopt it as a job
without unpicking a loop first. `nyxloom intake-bridge poll` is the CLI
consumer today.

TWO READ TRANSPORTS, ONE INTERFACE (`MessageReader`). Both are real; the
selector is `[intake_bridge] transport`, mirroring `NotifyConfig.backend`'s
own pattern (`resolve_reader` <-> `notify.resolve_backends`).

  * `mmctl`  -- `docker exec <app container> mmctl --local --json post list`.
    Needs NO Mattermost config change: it is the same local-mode admin socket
    `mattermost/hooks/post_compose_provision.py` already uses, and the P107
    argument for it (no network-exposed admin API, no new mount) applies
    verbatim. Measured bonus: `mmctl post create` is refused in local mode
    ("creating posts is not supported in local mode", 11.10.1), so this
    transport is read-only BY CONSTRUCTION, not by policy.
  * `rest`   -- `GET /api/v4/channels/{id}/posts` with a Personal Access
    Token. Needs `MM_SERVICESETTINGS_ENABLEUSERACCESSTOKENS = "true"` and a
    minted PAT; see `mattermost/README.md` for why the PAT belongs to a
    dedicated, non-admin, single-channel account.

The two transports' wire semantics DIFFER in three ways that would each
silently corrupt a cursor, which is why the cursor filter below is shared and
neither reader is trusted to have windowed correctly (all four measured
against 11.10.1, not read off documentation):

  1. ORDER. `mmctl --json post list` returns posts OLDEST-first; the REST
     endpoint returns `order` NEWEST-first.
  2. `since` BOUNDARY. mmctl's `--since` is INCLUSIVE (`create_at >= t`) and
     truncated to whole seconds; REST's `since` is EXCLUSIVE and matches on
     `update_at`, so an EDITED old post reappears in a REST window and never
     in an mmctl one.
  3. `--since` TIME FORMAT. mmctl's help says "ISO 8601" and its parser
     accepts ONLY `2006-01-02T15:04:05-07:00`. A trailing `Z` is REJECTED
     (`Error: invalid since time '...Z'`) -- the obvious RFC3339 spelling is
     the one that does not work. `--number` is also ignored whenever
     `--since` is present, so a since-window is unbounded in size.

MMCTL JSON SHAPE TRAP (measured; the mirror of P107's `Incoming:` prefix
incident). `mmctl --json` does NOT emit a stable container. `post list`
prints one printer row per post, so ZERO posts is EMPTY STDOUT, ONE post is
a BARE OBJECT and N posts is an ARRAY -- while `token list` prints the whole
slice as one row, so it is `null` for zero and an ARRAY for one. A parser
that assumes "array" reads len() over a post's dict KEYS and reports 21
messages for a channel holding one. `_json_rows` normalises all four shapes
and refuses anything else, rather than treating an unrecognised payload as
"nothing new" -- which is the failure mode that let P107 mint six webhooks.

INJECTION BOUNDARY (SPEC section 13). This module inherits decision_chat's
ONE sanctioned free-text carve-out and its three safeguards, unchanged:
(a) the posted reply is `intake_chat.advance_intake`'s return value, already
passed through `cfg.redact()` and capped at `intake_chat.MAX_REPLY_CHARS` by
that function; (b) the agent producing it is dispatched with a read-only
tool allowlist (no Edit/Write/Bash -- `intake_chat.READONLY_ARGV_SUFFIX`);
(c) this module caps again on the way IN (`MAX_TURN_CHARS`), which
decision_chat has no equivalent of because ntfy messages are small and a
Mattermost post is not. Every OTHER push here is a fixed template over typed
fields, exactly like notify.py.

AUTHORISATION. Unlike ntfy, Mattermost DOES attribute a verifiable author to
every post -- but a Mattermost username is still not a nyxloom operator
identity, so the ingress is gated the same way every other channel mutation
is: `control_auth.channel_operator_for` (NYXLOOM_CHANNEL_OPERATOR_ID). An
intake turn opens D-NNN decisions and files backlog items; that is a
mutation, and it is attributed to the NAMED operator, never to a transport
name. What the verifiable author buys is the loop guard below, which on ntfy
could only ever be a self-declared tag.

PERSISTENCE. One `BridgeState` JSON record per (project, team:channel) under
`paths.project_dir(project)/"intake_bridge"/<team>__<channel>.json`: the poll
cursor plus the id of the intake conversation currently in progress.
DELIBERATELY NOT EVENT-SOURCED, and the line is worth stating: the event log
is the audit ledger of DOMAIN state, replayable into task/decision state. A
poll cursor is neither -- it is one reader's resumption offset into an
external system's clock, meaningless on replay and derived from a source the
ledger does not own. The precedent is exact and local: `IntakeChat.session_id`
and `DecisionChat.session_id` are the same class of transport-internal
resumption handle and live in the same kind of JSON record beside the same
`project_dir`. What IS event-sourced is the consequence: every turn this
bridge drives appends `INTAKE_REPLY_RECORDED` under the named operator --
the same event daemon.py's authenticated HTTP `/api/intake` route appends --
and the decisions/backlog items the turn opens are audited by their own
existing paths.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import control_auth, intake_chat, notify, paths, storage
from .config import IntakeBridgeConfig, NotifyConfig, ProjectConfig
from .log import get_logger
from .types import EventType, new_id

log = get_logger("intake_bridge")

MMCTL = "/mattermost/bin/mmctl"

#: Wall-clock ceiling on one transport read. Generous next to
#: `intake_chat.TURN_TIMEOUT_SECONDS` (120) because a `docker exec` on a
#: loaded host is slow to START, not slow to answer.
READ_TIMEOUT_SECONDS = 30

#: Upper bound on the text ONE poll hands to `advance_intake`, after
#: coalescing (see `_coalesce`). The reply cap lives in intake_chat; this is
#: the inbound twin, and it exists because a Mattermost post has no useful
#: length limit while a first-turn system prompt does.
MAX_TURN_CHARS = 4000

#: The one recognised control phrase, anchored and case-insensitive, with no
#: arguments and no free-text interpolation into anything -- the same strict
#: allowlist discipline `commands._VERB_RE` applies to the ntfy channel.
#: It exists because there is no daemon to notice a wedged interview and no
#: second channel to abandon one from.
_NEW_INTAKE_RE = re.compile(r"^new intake$", re.IGNORECASE)

#: `paths` component for a (team, channel) pair. Mattermost channel and team
#: names are `[a-z0-9-_]`, so this cannot collide or escape the directory;
#: anything else is refused rather than sanitised.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: The dashboard page carrying every open intake conversation. Same loopback
#: base every other `click` target in notify.py hardcodes.
INTAKE_UI_URL = "http://127.0.0.1:8942/www/intake.html"

#: An intake id safe to interpolate into a URL fragment. `new_id` produces
#: `intake-<hex>`, and daemon.py's own `_INTAKE_ID_RE` bounds the shape a
#: client may supply -- but this module also builds a link for the
#: conversation-reset notice, which has no id at all, so the guard is here
#: rather than assumed of every caller.
_INTAKE_ANCHOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class BridgeError(RuntimeError):
    """The bridge cannot poll safely; the caller must not treat it as 'no
    new messages'. Never raised for an EMPTY channel, only for a channel
    that could not be read or parsed."""


# ---------------------------------------------------------------------------
# wire types


@dataclass(frozen=True)
class InboundMessage:
    """One channel post, normalised across both transports.

    `is_program` is the LOOP GUARD and is server-attributed, never derived
    from the text: Mattermost stamps `props.from_webhook = "true"` on every
    post an incoming webhook created, which is every post `notify.send()`
    has ever made. `is_system` covers the join/leave/header posts Mattermost
    writes itself (`type` = `system_*`); an empty `type` is a real message.
    """

    post_id: str
    create_at_ms: int
    author_id: str
    text: str
    is_program: bool
    is_system: bool
    is_deleted: bool

    @property
    def is_human_turn(self) -> bool:
        return not (self.is_program or self.is_system or self.is_deleted) and bool(self.text.strip())


@dataclass
class BridgeState:
    """Poll cursor + the conversation in progress. See the module docstring
    on why this is a JSON record and not an event."""

    channel_key: str
    intake_id: str | None = None
    #: `create_at` of the newest post this bridge has already decided about
    #: (ingested OR skipped). 0 = no poll has happened yet.
    last_create_at_ms: int = 0
    #: Post ids sitting at EXACTLY `last_create_at_ms`. Needed because
    #: `create_at` is not unique and mmctl's `--since` is second-truncated:
    #: without it, two posts in the same millisecond either both replay or
    #: one is lost, depending on which way the comparison is rounded.
    boundary_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_key": self.channel_key,
            "intake_id": self.intake_id,
            "last_create_at_ms": self.last_create_at_ms,
            "boundary_ids": sorted(self.boundary_ids),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BridgeState":
        return cls(
            channel_key=d["channel_key"],
            intake_id=d.get("intake_id"),
            last_create_at_ms=int(d.get("last_create_at_ms", 0)),
            boundary_ids=list(d.get("boundary_ids", [])),
        )


@dataclass(frozen=True)
class PollResult:
    """What one poll did. Returned rather than logged so the CLI verb, a
    future scheduled job and the tests all read the SAME record."""

    transport: str
    status: str          # "ok" | "unconfigured" | "refused" | "bootstrapped"
    detail: str = ""
    fetched: int = 0
    ingested: int = 0
    intake_id: str | None = None
    reply_posted: bool = False
    cursor_ms: int = 0


# ---------------------------------------------------------------------------
# persistence


def _channel_key(bc: IntakeBridgeConfig) -> str:
    team, channel = (bc.team or ""), (bc.channel or "")
    if not _NAME_RE.match(team) or not _NAME_RE.match(channel):
        raise BridgeError(
            "[intake_bridge] team/channel must be Mattermost names "
            "([a-z0-9][a-z0-9._-]*); refusing rather than sanitising a value "
            "that names a state file")
    return f"{team}:{channel}"


def _state_path(project: str, channel_key: str) -> Path:
    return (paths.project_dir(project) / "intake_bridge"
            / f"{channel_key.replace(':', '__')}.json")


def load_state(project: str, channel_key: str) -> BridgeState:
    p = _state_path(project, channel_key)
    if not p.exists():
        return BridgeState(channel_key=channel_key)
    try:
        return BridgeState.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, KeyError, ValueError, TypeError):
        # A corrupt cursor must not silently become "replay the channel":
        # that would re-run finished interviews. Refuse; the operator can
        # delete the file to deliberately re-bootstrap.
        raise BridgeError(
            f"intake-bridge state file for {channel_key!r} is unreadable; "
            "delete it to re-bootstrap the cursor at the channel head")


def save_state(project: str, state: BridgeState) -> None:
    p = _state_path(project, state.channel_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# shared parsing


def _json_rows(stdout: str, *, what: str) -> list[dict]:
    """Normalise `mmctl --json` output into a list of row dicts.

    See the module docstring's SHAPE TRAP paragraph: empty stdout, `null`, a
    bare object and an array are ALL shapes 11.10.1 emits, chosen per command
    and per result count. Anything else is REFUSED rather than degraded to
    an empty list -- P107's lesson is that "an unrecognised list read as
    'nothing exists'" is the shape of failure that repeats a mutation.
    """
    text = stdout.strip()
    # Only the EMPTY case is short-circuited (json.loads("") raises). `null`
    # deliberately goes through the parser and out via the `parsed is None`
    # branch below rather than being matched as a literal here -- matching it
    # twice left that branch unreachable, i.e. dead.
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise BridgeError(f"mmctl --json {what}: stdout is not JSON ({exc.__class__.__name__})")
    if parsed is None:
        return []
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        if all(isinstance(row, dict) for row in parsed):
            return parsed
        raise BridgeError(f"mmctl --json {what}: array holds non-object rows")
    raise BridgeError(f"mmctl --json {what}: unexpected top-level {type(parsed).__name__}")


def _message_from_post(post: dict) -> InboundMessage | None:
    """One Mattermost Post object -> InboundMessage. Both transports return
    the SAME object schema (mmctl prints the model verbatim), so this is
    shared rather than duplicated per reader."""
    post_id = post.get("id")
    create_at = post.get("create_at")
    if not isinstance(post_id, str) or not post_id or not isinstance(create_at, int):
        return None
    props = post.get("props")
    props = props if isinstance(props, dict) else {}
    return InboundMessage(
        post_id=post_id,
        create_at_ms=create_at,
        author_id=str(post.get("user_id") or ""),
        text=str(post.get("message") or ""),
        is_program=str(props.get("from_webhook", "")).lower() == "true",
        is_system=bool(post.get("type")),
        is_deleted=bool(post.get("delete_at")),
    )


# ---------------------------------------------------------------------------
# the read-transport seam


class MessageReader:
    """One inbound channel transport (the twin of `notify.NotifyBackend`).

    A reader's ONLY job is "hand me every post at or after this millisecond,
    newest-order-agnostic". It does NOT apply the cursor, the loop guard or
    the human/program split -- `poll_once` does, once, for every transport,
    because those are exactly the rules that must not be able to differ
    between two implementations of the same bridge.
    """

    name: str = ""

    def is_configured(self, bc: IntakeBridgeConfig) -> bool:
        raise NotImplementedError

    def fetch(self, bc: IntakeBridgeConfig, *, since_ms: int) -> list[InboundMessage]:
        """Posts with `create_at >= since_ms`, plus whatever slack the
        transport's own windowing forces. `since_ms == 0` means "no cursor
        yet": return at most `bc.bootstrap_messages + 1` recent posts, so a
        first poll can find the channel head without reading its history."""
        raise NotImplementedError


def _mmctl_since(ms: int) -> str:
    """mmctl `--since`'s ONLY accepted layout (measured -- see the module
    docstring). Floored to the whole second, which is safe in exactly one
    direction: mmctl's `--since` is inclusive, so flooring can over-fetch by
    up to a second and can never skip a post. The duplicates it lets through
    are removed by the shared cursor filter."""
    return datetime.fromtimestamp(ms // 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class MmctlReader(MessageReader):
    """`docker exec <container> mmctl --local --json post list <team>:<chan>`.

    No Mattermost configuration change of any kind: this is the same
    local-mode admin socket the P107 provisioning hook talks over, reached
    the same way (`docker exec`, not a new mount and not a network admin
    API). It needs no PAT and no channel membership -- a local-mode session
    is unrestricted -- which is both its convenience and the reason it is
    only usable from the docker host.
    """

    name = "mmctl"

    def is_configured(self, bc: IntakeBridgeConfig) -> bool:
        return bool(bc.container and bc.team and bc.channel)

    def fetch(self, bc: IntakeBridgeConfig, *, since_ms: int) -> list[InboundMessage]:
        argv = ["docker", "exec", str(bc.container), MMCTL, "--local", "--json",
                "post", "list", f"{bc.team}:{bc.channel}"]
        if since_ms > 0:
            # `--number` is IGNORED alongside `--since` (measured), so the
            # window is bounded by the cursor's age, not by a count.
            argv += ["--since", _mmctl_since(since_ms)]
        else:
            argv += ["--number", str(max(1, bc.bootstrap_messages + 1))]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=READ_TIMEOUT_SECONDS, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise BridgeError(f"mmctl post list failed: {type(exc).__name__}")
        if proc.returncode != 0:
            # NEVER echo stdout here: on a `post list` failure mmctl still
            # prints the count sentence, and a future failure mode could put
            # post text on the stream. The verb and rc are enough.
            raise BridgeError(
                f"mmctl post list exited {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:200]}")
        out: list[InboundMessage] = []
        for row in _json_rows(proc.stdout, what="post list"):
            msg = _message_from_post(row)
            if msg is not None:
                out.append(msg)
        return out


class RestReader(MessageReader):
    """Mattermost's REST API with a Personal Access Token.

    Two calls: resolve the channel id by name, then read its posts. The
    token is a bearer credential that inherits its OWNING ACCOUNT's full
    permissions -- Team Edition has no per-token scoping -- so the account
    it belongs to IS the scope. See `mattermost/README.md`.
    """

    name = "rest"

    def is_configured(self, bc: IntakeBridgeConfig) -> bool:
        return bool(bc.base_url and bc.token and bc.team and bc.channel)

    def _get(self, bc: IntakeBridgeConfig, path: str) -> Any:
        req = urllib.request.Request(
            f"{str(bc.base_url).rstrip('/')}{path}",
            headers={"Authorization": f"Bearer {bc.token}"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # The status is the whole diagnosis (403 = not a member of the
            # channel, 401 = the PAT is revoked or PATs were turned back
            # off). The BODY is not echoed: it is server prose on a path
            # whose request carried a bearer token.
            raise BridgeError(f"mattermost REST {path.split('?')[0]} -> HTTP {exc.code}")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError,
                json.JSONDecodeError) as exc:
            raise BridgeError(f"mattermost REST read failed: {type(exc).__name__}")

    def fetch(self, bc: IntakeBridgeConfig, *, since_ms: int) -> list[InboundMessage]:
        meta = self._get(bc, f"/api/v4/teams/name/{bc.team}/channels/name/{bc.channel}")
        channel_id = meta.get("id") if isinstance(meta, dict) else None
        if not isinstance(channel_id, str) or not channel_id:
            raise BridgeError("mattermost REST: channel lookup returned no id")
        if since_ms > 0:
            # REST's `since` is EXCLUSIVE and matches `update_at`, not
            # `create_at` (measured). `- 1` keeps a post created at exactly
            # the cursor inside the window; the shared filter then drops it
            # by id. An EDITED older post also reappears here and is dropped
            # the same way -- the bridge answers utterances, not revisions.
            query = f"?since={max(0, since_ms - 1)}"
        else:
            query = f"?per_page={max(1, bc.bootstrap_messages + 1)}"
        page = self._get(bc, f"/api/v4/channels/{channel_id}/posts{query}")
        if not isinstance(page, dict) or not isinstance(page.get("posts"), dict):
            raise BridgeError("mattermost REST: posts payload has no 'posts' map")
        out: list[InboundMessage] = []
        for row in page["posts"].values():
            if isinstance(row, dict):
                msg = _message_from_post(row)
                if msg is not None:
                    out.append(msg)
        return out


#: Reader registry. `config.INTAKE_TRANSPORTS` is the schema-side copy of
#: these names (config.py cannot import this module -- this module imports
#: config); `test_intake_bridge` asserts the two never drift.
_READERS: dict[str, MessageReader] = {r.name: r for r in (MmctlReader(), RestReader())}


def resolve_reader(bc: IntakeBridgeConfig) -> MessageReader | None:
    """The reader `poll_once` will use, or None when the bridge is not
    configured. Unlike `notify.resolve_backends` there is NO implicit chain
    and no fallback: falling back from `rest` to `mmctl` would silently
    re-route a read that the operator scoped to a token onto an unrestricted
    admin socket, which is a privilege ESCALATION, not a degradation."""
    if not bc.transport:
        return None
    reader = _READERS.get(bc.transport)
    if reader is None:
        log.warning("intake-bridge transport unknown", transport=bc.transport)
        return None
    return reader if reader.is_configured(bc) else None


# ---------------------------------------------------------------------------
# outbound reply


def _reply_channel(cfg: ProjectConfig, bc: IntakeBridgeConfig) -> NotifyConfig:
    """The NotifyConfig one intake reply is delivered on.

    Same shape of narrowing as `decision_chat._decision_channel`, in the
    opposite direction: a bridge reply MUST land in the channel the question
    came from, so the destination is pinned rather than inherited.

    `mattermost_channel` is cleared explicitly and that is load-bearing, not
    tidiness: nyxloom's own nyxloom.toml sets `mattermost_channel = "alerts"`,
    and Mattermost honours a `channel` key in an incoming-webhook payload as
    an OVERRIDE. Inheriting it would post every intake reply into `alerts` --
    the wrong channel, and one the intake account is deliberately not a
    member of. The webhook is already bound to its channel at creation, so
    the correct value here is 'send no override at all'.
    """
    return replace(cfg.notify, backend="mattermost", webhook_url=bc.webhook_url,
                   mattermost_channel=None)


def _post_reply(cfg: ProjectConfig, bc: IntakeBridgeConfig, intake_id: str,
                reply_text: str) -> bool:
    """SANCTIONED injection-boundary exception (see the module docstring):
    `reply_text` is the intake agent's own free text, already redacted and
    capped by `intake_chat.advance_intake`, posted because the operator
    explicitly opened this conversation."""
    note = {
        "title": f"Intake {intake_id}",
        "body": reply_text,
        "click": intake_chat_url(intake_id),
        "priority": 3,
        "tags": ["intake"],
    }
    try:
        ok, _detail = notify.send(_reply_channel(cfg, bc), note)
    except Exception as exc:  # census: advisory-degradation (SPEC 13)
        # A delivery failure never raises into a poll: the turn already
        # happened and is already persisted by intake_chat, so raising here
        # would strand a real conversation behind a transport hiccup.
        log.warning("intake-bridge reply failed", intake_id=intake_id,
                    exc_type=type(exc).__name__)
        return False
    return ok


def intake_chat_url(intake_id: str) -> str:
    """The dashboard page an operator can continue this conversation on --
    the same loopback base every other `click` target in notify.py uses.

    A FRAGMENT, not a query string: `intake.html` renders every open
    conversation on one page and reads no query parameters at all, but it
    does emit `id="transcript-<intake_id>"` per card (render.py), so
    `#transcript-<id>` actually scrolls to this conversation while
    `?intake=<id>` would be silently ignored -- a link that only looks like
    it deep-links. The anchor is absent once the interview finalises (the
    page renders OPEN chats only), which is inert rather than broken: the
    browser just lands at the top of the right page.
    """
    if not _INTAKE_ANCHOR_RE.match(intake_id):
        return INTAKE_UI_URL
    return f"{INTAKE_UI_URL}#transcript-{intake_id}"


# ---------------------------------------------------------------------------
# the poll


def _coalesce(messages: list[InboundMessage], limit: int) -> tuple[list[InboundMessage], str]:
    """Fold this poll's new human posts into ONE turn's text.

    A poll costs at most one model call by construction, and the poll
    INTERVAL is what defines an utterance boundary: three lines typed
    between two polls are one thing the operator said, not three interviews.
    Only the oldest `limit` are taken; the rest keep their place in the
    channel and are picked up by the next poll, so nothing is dropped.
    """
    taken = messages[:limit]
    text = "\n".join(m.text.strip() for m in taken if m.text.strip())
    return taken, text[:MAX_TURN_CHARS]


def _advance_cursor(state: BridgeState, decided: list[InboundMessage]) -> None:
    """Move the cursor past every message this poll DECIDED about (ingested
    or skipped). Skipped messages must advance it too -- otherwise the bot's
    own replies are re-fetched on every poll forever, and the window grows
    without bound."""
    if not decided:
        return
    newest = max(m.create_at_ms for m in decided)
    if newest > state.last_create_at_ms:
        state.last_create_at_ms = newest
        state.boundary_ids = [m.post_id for m in decided if m.create_at_ms == newest]
    else:
        state.boundary_ids = sorted(
            set(state.boundary_ids)
            | {m.post_id for m in decided if m.create_at_ms == newest})


def poll_once(cfg: ProjectConfig, project: str, *,
              reader: MessageReader | None = None) -> PollResult:
    """Poll the configured channel once, advance at most one intake turn,
    post the reply, and return. Never loops, never sleeps.

    The full order is deliberate: resolve the transport, resolve the
    OPERATOR (before any state is read -- CR-15's rule, so a refusal cannot
    be told apart by what it touched), read, filter, turn, post, then
    persist the cursor LAST. Persisting last means a crash mid-turn replays
    the message rather than losing it; `intake_chat`'s own `brief_id` guard
    is what makes that replay safe.
    """
    bc = cfg.intake_bridge
    active = reader if reader is not None else resolve_reader(bc)
    if active is None:
        return PollResult(transport=bc.transport or "", status="unconfigured",
                          detail="no intake_bridge transport configured")
    if not bc.webhook_url:
        # Fail closed rather than fall back to cfg.notify's webhook: that
        # one is bound to `alerts`, so the "fallback" would answer an intake
        # question in the operator-notification channel.
        return PollResult(transport=active.name, status="unconfigured",
                          detail="intake_bridge has no reply webhook configured")

    operator = control_auth.channel_operator_for("mattermost:intake-bridge")
    if operator is None:
        return PollResult(transport=active.name, status="refused",
                          detail="no named channel operator")

    channel_key = _channel_key(bc)
    state = load_state(project, channel_key)
    fetched = active.fetch(bc, since_ms=state.last_create_at_ms)
    fetched.sort(key=lambda m: (m.create_at_ms, m.post_id))

    if state.last_create_at_ms == 0:
        # First poll: adopt the channel HEAD, not its history.
        adopted = fetched[-bc.bootstrap_messages:] if bc.bootstrap_messages else []
        # Park the cursor just BEFORE the adopted window, not past the whole
        # fetch. Advancing over `fetched` here would mark the very messages
        # being adopted as already-decided, and the `fresh` filter below would
        # then drop every one of them -- `bootstrap_messages` would report a
        # successful poll that ingested nothing. Everything OLDER than the
        # window is genuinely decided (deliberately never ingested); the
        # adopted messages still flow through the ordinary
        # fresh/human/coalesce path, so they get the same loop guard and the
        # same one-turn-per-poll bound as any later message.
        _advance_cursor(state, fetched[:len(fetched) - len(adopted)])
        if not adopted:
            save_state(project, state)
            return PollResult(transport=active.name, status="bootstrapped",
                              detail=f"cursor set at {state.last_create_at_ms}",
                              fetched=len(fetched), cursor_ms=state.last_create_at_ms)
        fetched = adopted

    boundary = set(state.boundary_ids)
    fresh = [m for m in fetched
             if m.create_at_ms > state.last_create_at_ms
             or (m.create_at_ms == state.last_create_at_ms and m.post_id not in boundary)]
    if not fresh:
        return PollResult(transport=active.name, status="ok", detail="no new messages",
                          fetched=len(fetched), intake_id=state.intake_id,
                          cursor_ms=state.last_create_at_ms)

    human = [m for m in fresh if m.is_human_turn]
    if not human:
        _advance_cursor(state, fresh)
        save_state(project, state)
        return PollResult(transport=active.name, status="ok",
                          detail="nothing but program/system posts",
                          fetched=len(fetched), intake_id=state.intake_id,
                          cursor_ms=state.last_create_at_ms)

    taken, turn_text = _coalesce(human, bc.max_messages_per_poll)
    decided = [m for m in fresh if m.create_at_ms <= taken[-1].create_at_ms]

    if _NEW_INTAKE_RE.match(turn_text.strip()):
        state.intake_id = None
        _advance_cursor(state, decided)
        save_state(project, state)
        _post_reply(cfg, bc, "-", "Starting a fresh intake conversation. "
                                  "Describe the feature you want.")
        return PollResult(transport=active.name, status="ok", detail="conversation reset",
                          fetched=len(fetched), ingested=len(taken),
                          reply_posted=True, cursor_ms=state.last_create_at_ms)

    intake_id = state.intake_id or new_id("intake")
    reply = intake_chat.advance_intake(cfg, project, intake_id, turn_text)

    storage.append_event(project, actor=operator,
                         type=EventType.INTAKE_REPLY_RECORDED,
                         payload={"intake_id": intake_id})

    posted = _post_reply(cfg, bc, intake_id, reply)

    # A finalised interview closes the conversation: `intake_chat` has filed
    # the backlog item and would refuse a second BRIEF: on this chat anyway,
    # so the next message starts a new one instead of talking into a chat
    # that can no longer produce anything.
    chat = intake_chat.load_chat(project, intake_id)
    state.intake_id = None if (chat is not None and chat.brief_id) else intake_id
    _advance_cursor(state, decided)
    save_state(project, state)

    log.debug("intake-bridge poll", project=project, transport=active.name,
              intake_id=intake_id, ingested=len(taken), reply_posted=posted)
    return PollResult(transport=active.name, status="ok", detail="turn advanced",
                      fetched=len(fetched), ingested=len(taken), intake_id=intake_id,
                      reply_posted=posted, cursor_ms=state.last_create_at_ms)
