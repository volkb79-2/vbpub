"""Notifications: typed events -> ntfy/webhook. PACKAGE P06.

INJECTION BOUNDARY (SPEC §13, non-negotiable): notification text is built
ONLY from typed fields — event type, ids, enum values, counts, costs, and
FIXED template strings. Never interpolate handoff prose, receipt
blocked_reason, log text, or any other model-authored string into a
notification. (blocked_reason appears ONLY as its first 80 chars AFTER
cfg.redact and only in the dashboard, never in a push.)

INTERFACE CONTRACT (frozen):

- notification_for(ev: Event) -> dict | None:
  None when ev.type.value not in push_classes handling below; else
  {'title': str, 'body': str, 'click': str, 'priority': int, 'tags': [str]}.
  Titles are fixed per type, e.g.:
    DECISION_OPENED   title 'Decision needed: <decision_id>' priority 5
    TASK_BLOCKED      title '<project>/<task_id> BLOCKED' priority 4
    SPEC_ATTENTION    title 'Spec attention: <payload.reason>' priority 4
    BUDGET_*          priority 4/5, body includes remaining/spent numbers
    NEEDS_OPERATOR    priority 5
    WAVE_CLOSED       title 'Wave merged: <n> task(s)' priority 3
    others in push_classes: generic '<type> <project>/<task_id>' priority 3
  click = 'http://127.0.0.1:<port>/www/task/<project>/<task_id>.html' (or
  index for project-scoped events).
- send(nc: NotifyConfig, note: dict) -> tuple[bool, str]:
  dispatches over resolve_backends(nc) -- see the NotifyBackend seam below.
  ntfy: urllib POST to f'{ntfy_url}/{ntfy_topic}', body=note['body'],
  headers Title/Priority/Tags/Click; webhook: POST JSON of note to
  webhook_url; mattermost: POST mattermost_payload(nc, note) to
  webhook_url. 5s timeout per backend. Never raises; (ok, detail). With no
  explicit nc.backend and both configured -> ntfy wins, webhook is fallback
  on failure (unchanged).

NL-17 2026-09-08 (backend seam; ntfy retired as the ACTIVE channel, kept
selectable) adds:

- NotifyBackend: one delivery channel. Each backend owns translating the
  typed note into ITS OWN payload shape -- the old single webhook path
  json.dumps()'d the internal note dict verbatim, which no real receiver
  understands (Mattermost reads only {'text': ...} and would 200 on a
  payload it cannot render, so the failure was silent). Backends:
  NtfyBackend (unchanged behaviour), WebhookBackend (the legacy raw
  passthrough, kept for receivers already built for it), MattermostBackend
  (new). Telegram/Discord are additional subclasses later; send()'s
  dispatch does not change for them.
- resolve_backends(nc) -> list[NotifyBackend]: nc.backend names the SOLE
  channel; absent, the legacy ntfy-then-webhook precedence stands, so
  existing configs behave exactly as before.
- mattermost_payload(nc, note) -> dict: the §13-clean translation into
  Mattermost's incoming-webhook contract (title/click/priority/tags folded
  into the Markdown `text`, since Mattermost ignores every other key).
- notify_event(cfg, states, ev) -> None:
  if ev.type is NOTIFICATION_* -> return (no recursion). If
  ev.type.value in cfg.notify.push_classes and notification_for gives a
  note: append NOTIFICATION_REQUESTED, call send unless BOTH ntfy and
  webhook are unconfigured (then mark detail 'unconfigured', delivered
  False), append NOTIFICATION_DELIVERED or NOTIFICATION_FAILED with
  {'detail': ...}. Delivery failure never raises (SPEC §13).
- digest(cfg, project, since_seq) -> str:
  plain-text summary of events with type in digest_classes since since_seq:
  counts per type, tasks merged (ids), total cost recorded in the window,
  decisions open count. Deterministic ordering. (Scheduling a daily digest
  is an operator cron concern, exposed via CLI 'nyxloom digest'.)

CR-16 2026-08-03 (liveness, channel health, silent-failure detection;
RISK-007) adds:

- probe_transport(nc, timeout=3.0) -> NotifyTransportProbe: an ACTIVE
  reachability check for the configured push channel, distinct from
  send() -- it never publishes a real notification (no title/body/topic
  write), just confirms the channel answers, so it is safe to run on a
  short poll cycle without becoming the notification-storm watchdog.py
  exists to catch. 'healthy' | 'unreachable' | 'unconfigured'; NEVER
  raises. This is the SECOND, independent alarm path RISK-007 requires:
  doctor.liveness_findings calls it from a plain function call in a
  freshly-started `nyxloom doctor` process, and reports the result via
  DoctorFinding + process exit code -- a channel that has nothing to do
  with ntfy/webhook, so 'the transport that carries the alarm is the
  same transport that just failed' cannot happen here.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from typing import Literal
from urllib.parse import urlencode

from . import snapshot, storage
from .config import NotifyConfig, ProjectConfig
from .log import get_logger
from .types import (
    Actor, ActorKind, Event, EventType, TaskStateFile, TaskState, utc_now,
)

log = get_logger("notify")

#: probe_transport's default network timeout. Short on purpose: it is meant
#: to run from a container healthcheck on a tight interval (see
#: nyxloomd/docker-compose.yml), and a slow/hung transport must fail the
#: probe promptly rather than eat the healthcheck's own wall-clock budget.
_DEFAULT_PROBE_TIMEOUT_S = 3.0

NotifyTransportStatus = Literal["healthy", "unreachable", "unconfigured"]


@dataclass(frozen=True)
class NotifyTransportProbe:
    """Result of one active reachability probe of the notification channel.

    ``status``:
      * ``"healthy"``      -- the configured endpoint answered at all (even a
                              non-2xx HTTP response proves the TRANSPORT
                              carried the request there and a response back;
                              that is all this probe claims).
      * ``"unreachable"``  -- a connection-level fault: refused, timed out,
                              DNS failure, or similar. THE case RISK-007
                              names ("the notification channel was
                              crash-looping").
      * ``"unconfigured"`` -- neither ntfy nor webhook is set up. Not a
                              fault -- a project may legitimately run with no
                              push channel at all.
    """

    status: NotifyTransportStatus
    channel: str    # "ntfy" | "webhook" | "" (unconfigured)
    detail: str

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"


def notification_for(ev: Event) -> dict | None:
    """Transform an event into a notification dict or None.

    Returns None if the event type is not handled. Otherwise returns
    a dict with keys: title, body, click, priority, tags (list).

    Only uses typed fields (event type, ids, counts) and fixed template strings.
    Never interpolates user-authored payload strings into output.
    """
    t = ev.type

    # DECISION_OPENED: project-scoped
    if t is EventType.DECISION_OPENED:
        decision_id = ev.decision_id or "unknown"
        return {
            "title": f"Decision needed: {decision_id}",
            "body": f"Decision {decision_id} opened and awaiting resolution.",
            "click": "http://127.0.0.1:8942/www/index.html",
            "priority": 5,
            "tags": ["decision"],
        }

    # TASK_BLOCKED: task-scoped
    if t is EventType.TASK_BLOCKED:
        project = ev.project or "unknown"
        task_id = ev.task_id or "unknown"
        return {
            "title": f"{project}/{task_id} BLOCKED",
            "body": f"Task {project}/{task_id} is blocked.",
            "click": f"http://127.0.0.1:8942/www/task/{project}/{task_id}.html",
            "priority": 4,
            "tags": ["task", "blocked"],
        }

    # SPEC_ATTENTION: project-scoped; payload.reason is an enum/status, safe to include
    if t is EventType.SPEC_ATTENTION:
        reason = ev.payload.get("reason", "unknown")
        # Only safe enum-like values are in reason (e.g., "ratchet", "stale", etc.)
        # Never user prose.
        return {
            "title": f"Spec attention: {reason}",
            "body": f"Specification requires attention: {reason}",
            "click": "http://127.0.0.1:8942/www/index.html",
            "priority": 4,
            "tags": ["spec"],
        }

    # BUDGET_WARNING: project-scoped; body includes numeric fields
    if t is EventType.BUDGET_WARNING:
        remaining = ev.payload.get("remaining")
        spent = ev.payload.get("spent")
        body = f"Budget warning issued."
        if remaining is not None:
            body = f"Remaining budget: {remaining}"
        if spent is not None:
            if remaining is not None:
                body += f"; spent: {spent}"
            else:
                body = f"Budget spent: {spent}"
        return {
            "title": "Budget warning",
            "body": body,
            "click": "http://127.0.0.1:8942/www/index.html",
            "priority": 4,
            "tags": ["budget"],
        }

    # BUDGET_EXHAUSTED: project-scoped; higher priority
    if t is EventType.BUDGET_EXHAUSTED:
        return {
            "title": "Budget exhausted",
            "body": "Project budget has been exhausted.",
            "click": "http://127.0.0.1:8942/www/index.html",
            "priority": 5,
            "tags": ["budget"],
        }

    # NEEDS_OPERATOR: project-scoped; high priority
    if t is EventType.NEEDS_OPERATOR:
        return {
            "title": "Operator attention needed",
            "body": "An operator action is required.",
            "click": "http://127.0.0.1:8942/www/index.html",
            "priority": 5,
            "tags": ["operator"],
        }

    # WAVE_CLOSED: project-scoped; count tasks from payload
    if t is EventType.WAVE_CLOSED:
        task_ids = ev.payload.get("task_ids", [])
        count = len(task_ids)
        task_list = ", ".join(str(tid) for tid in sorted(task_ids))
        return {
            "title": f"Wave merged: {count} task(s)",
            "body": f"Wave closed with {count} task(s): {task_list}",
            "click": "http://127.0.0.1:8942/www/index.html",
            "priority": 3,
            "tags": ["wave"],
        }

    # PROVIDER_STATE_CHANGED: project-scoped; generic handler
    if t is EventType.PROVIDER_STATE_CHANGED:
        return {
            "title": f"PROVIDER_STATE_CHANGED",
            "body": f"Provider state has changed.",
            "click": "http://127.0.0.1:8942/www/index.html",
            "priority": 3,
            "tags": ["provider"],
        }

    # FINDING_RECORDED (FN-2): only PUSHABLE kinds notify, and the body comes
    # from the kind's FIXED code-owned push_template over its typed
    # required_fields ONLY -- never the finding's free-text title/body
    # (SPEC-13). Unknown/generic kinds -> None (dashboard-only).
    if t is EventType.FINDING_RECORDED:
        from .findings import FINDING_KINDS
        kind = ev.payload.get("kind", "generic")
        spec = FINDING_KINDS.get(kind)
        if spec is None or not spec.pushable or spec.push_template is None:
            return None
        fields = ev.payload.get("fields") or {}
        try:
            body = spec.push_template.format(
                **{k: fields[k] for k in spec.required_fields})
        except (KeyError, IndexError, ValueError):
            return None
        return {
            "title": f"Finding: {kind}",
            "body": body,
            "click": "http://127.0.0.1:8942/www/findings.html",
            "priority": 3,
            "tags": [spec.tag],
        }

    # Unhandled event type
    return None


class NotifyBackend:
    """One delivery channel (NL-17).

    A backend owns translating the typed ``note`` dict
    (``{title, body, click, priority, tags}``, produced by
    ``notification_for``) into ITS OWN wire payload -- there is deliberately
    no shared raw-JSON passthrough, because no two receivers agree on a
    schema (ntfy reads HTTP headers; Mattermost reads a ``{"text": ...}``
    JSON body and ignores every other key).

    The SPEC §13 injection boundary binds every backend equally: a payload
    is assembled ONLY from the typed note fields plus FIXED template
    strings owned by this module. A backend must never widen that by
    forwarding model-authored prose.

    Adding a backend (Telegram, Discord, ...) is: subclass, set ``name``,
    implement the three methods, register in ``_BACKENDS`` and in
    ``config.NOTIFY_BACKENDS``. ``send()``/``probe_transport()`` dispatch
    generically and need no edit.
    """

    #: Selector value in ``[notify] backend`` / ``NotifyConfig.backend``.
    name: str = ""

    def is_configured(self, nc: NotifyConfig) -> bool:
        """True when this project's config carries what the backend needs."""
        raise NotImplementedError

    def deliver(self, nc: NotifyConfig, note: dict) -> tuple[bool, str] | None:
        """Deliver one note. Never raises.

        Returns ``(ok, detail)`` for a DEFINITIVE outcome (the server
        answered, for better or worse), or ``None`` for a transport-level
        fault the caller may retry on the NEXT backend in the chain. Only
        ntfy uses the ``None`` fallthrough -- that is the pre-existing
        "ntfy raised -> try webhook" behaviour, preserved verbatim.
        """
        raise NotImplementedError

    def probe(self, nc: NotifyConfig, *, timeout: float) -> NotifyTransportProbe:
        """Active reachability probe for this channel (CR-16). Never raises,
        never publishes a real notification."""
        raise NotImplementedError


def _probe_url(url: str, channel: str, timeout: float) -> NotifyTransportProbe:
    """Shared CR-16 probe body: a bare GET; ANY HTTP response proves the
    transport carried a request there and an answer back (see
    ``probe_transport``'s docstring for why a 404/405 is still healthy)."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            pass
    except urllib.error.HTTPError as exc:
        return NotifyTransportProbe(
            "healthy", channel, f"{channel} reachable (HTTP {exc.code})")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        return NotifyTransportProbe(
            "unreachable", channel, snapshot.bounded_detail(f"{type(exc).__name__}: {exc}"))
    return NotifyTransportProbe("healthy", channel, f"{channel} reachable")


class NtfyBackend(NotifyBackend):
    """ntfy: title/priority/click/tags travel as HTTP HEADERS, body as the
    raw request body. Behaviour is UNCHANGED from the pre-seam
    implementation (ntfy is stopped as the active channel, not deleted --
    it stays selectable)."""

    name = "ntfy"

    def is_configured(self, nc: NotifyConfig) -> bool:
        return bool(nc.ntfy_url and nc.ntfy_topic)

    def deliver(self, nc: NotifyConfig, note: dict) -> tuple[bool, str] | None:
        try:
            url = f"{nc.ntfy_url}/{nc.ntfy_topic}"
            body = note.get("body", "").encode("utf-8")

            headers = {
                "Title": note.get("title", ""),
                "Priority": str(note.get("priority", 3)),
            }
            token = os.environ.get(nc.token_env or "", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            headers |= {
                "Click": note.get("click", ""),
            }
            tags = note.get("tags", [])
            if tags:
                headers["Tags"] = ",".join(tags)

            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    # NEVER log the token/headers/URL -- only the fixed
                    # channel name and topic (an identifier, not a secret;
                    # SPEC §13's injection boundary + the no-secret rule).
                    log.info("notification sent", channel="ntfy", topic=nc.ntfy_topic)
                    return (True, "ok")
                else:
                    # Non-200 response from ntfy
                    log.warning("notification channel failed", channel="ntfy",
                                topic=nc.ntfy_topic, status=response.status)
                    return (False, f"ntfy returned {response.status}")
        except Exception as e:
            # ntfy failed (connection error, HTTP error, or anything else);
            # try webhook fallback. (logging-P05b: the previous two except
            # clauses -- (HTTPError, URLError, OSError) then a catch-all
            # Exception -- had textually IDENTICAL bodies; merged into one,
            # which is behavior-preserving since Exception is already a
            # strict superset of the narrower tuple.)
            log.warning("notification channel failed", channel="ntfy",
                        topic=nc.ntfy_topic, error=type(e).__name__)
            # None (not a (False, ...) tuple) is what makes the caller try
            # the NEXT backend -- the pre-seam "ntfy raised -> fall through
            # to webhook" behaviour, unchanged. A non-200 above still
            # returns definitively and does NOT fall through, also as before.
            return None

    def probe(self, nc: NotifyConfig, *, timeout: float) -> NotifyTransportProbe:
        return _probe_url(nc.ntfy_url or "", "ntfy", timeout)


class WebhookBackend(NotifyBackend):
    """Generic webhook: POSTs the note dict as JSON verbatim.

    This is the LEGACY passthrough (NL-17's original finding): it assumes
    the receiver understands nyxloom's own internal note schema. Kept
    unchanged for any deployment already pointing it at a receiver built
    for that shape -- new receivers get a real adapter (see
    ``MattermostBackend``) instead.
    """

    name = "webhook"

    def is_configured(self, nc: NotifyConfig) -> bool:
        return bool(nc.webhook_url)

    def deliver(self, nc: NotifyConfig, note: dict) -> tuple[bool, str] | None:
        try:
            headers = {
                "Content-Type": "application/json",
            }
            body = json.dumps(note).encode("utf-8")
            req = urllib.request.Request(nc.webhook_url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    # NEVER log nc.webhook_url -- for many providers the
                    # URL itself IS the secret (e.g. Slack incoming
                    # webhooks). Only the fixed channel name.
                    log.info("notification sent", channel="webhook")
                    return (True, "webhook ok")
                else:
                    log.warning("notification channel failed", channel="webhook",
                                status=response.status)
                    return (False, f"webhook returned {response.status}")
        except Exception as e:
            # (logging-P05b: same merge as the ntfy branch above -- the
            # previous two except clauses' bodies differed only in the
            # returned detail string's prefix ("webhook failed" vs "webhook
            # error"); using "webhook failed" for both preserves the
            # ORIGINAL (HTTPError, URLError, OSError)-branch wording for
            # every caller, since that tuple covers the overwhelmingly
            # common real-world case -- connection errors -- and no
            # existing test asserted the "webhook error" wording.)
            log.warning("notification channel failed", channel="webhook",
                        error=type(e).__name__)
            return (False, f"webhook failed: {type(e).__name__}")

    def probe(self, nc: NotifyConfig, *, timeout: float) -> NotifyTransportProbe:
        return _probe_url(nc.webhook_url or "", "webhook", timeout)


#: Mattermost has NO priority field on incoming webhooks (message priority
#: is an API-only post property), so the ntfy priority signal would be lost
#: outright. These FIXED prefixes preserve it inside the rendered text --
#: the only place the receiver will actually show it. Priorities <= 3 get no
#: prefix on purpose: a badge on every routine note trains the reader to
#: ignore it.
_MATTERMOST_PRIORITY_PREFIX = {
    5: ":rotating_light: ",
    4: ":warning: ",
}


def mattermost_payload(nc: NotifyConfig, note: dict) -> dict:
    """Translate a typed note into Mattermost's incoming-webhook payload.

    Mattermost's documented contract for an incoming webhook is a JSON
    object whose ONLY rendered field is ``text`` (Markdown); ``username``,
    ``icon_url`` and ``channel`` are optional identity/routing overrides
    (the last two only honoured when the server allows overrides), and
    every other key -- including nyxloom's own ``title``/``click``/
    ``priority``/``tags`` -- is ignored. So all four surviving signals have
    to be folded INTO ``text``:

        :rotating_light: **Decision needed: D-7**
        Decision D-7 opened for demo/T-3.
        [open](http://127.0.0.1:8942/www/task/demo/T-3.html)
        _tags: decision_

    SPEC §13: every part comes from the typed note fields or from the fixed
    template strings above -- nothing model-authored is interpolated. The
    text is NOT Markdown-escaped, deliberately: `notification_for` builds
    titles/bodies from ids, enum values and counts only, so there is no
    untrusted text here to escape, and escaping would corrupt the ids.
    """
    lines: list[str] = []
    title = str(note.get("title", "") or "")
    if title:
        prefix = _MATTERMOST_PRIORITY_PREFIX.get(int(note.get("priority", 3) or 3), "")
        lines.append(f"{prefix}**{title}**")
    body = str(note.get("body", "") or "")
    if body:
        lines.append(body)
    click = str(note.get("click", "") or "")
    if click:
        lines.append(f"[open]({click})")
    tags = [str(t) for t in (note.get("tags") or [])]
    if tags:
        lines.append(f"_tags: {', '.join(tags)}_")

    payload: dict = {"text": "\n".join(lines)}
    if nc.mattermost_username:
        payload["username"] = nc.mattermost_username
    if nc.mattermost_icon_url:
        payload["icon_url"] = nc.mattermost_icon_url
    if nc.mattermost_channel:
        payload["channel"] = nc.mattermost_channel
    return payload


class MattermostBackend(NotifyBackend):
    """Mattermost incoming webhook (NL-17).

    Shares ``webhook_url`` with the generic backend -- a Mattermost
    incoming webhook IS a webhook URL, and for Mattermost (as for Slack)
    that URL is itself the credential, which is why it is never logged and
    is settable from the environment (``webhook_url_env``) instead of a
    committed nyxloom.toml.
    """

    name = "mattermost"

    def is_configured(self, nc: NotifyConfig) -> bool:
        return bool(nc.webhook_url)

    def deliver(self, nc: NotifyConfig, note: dict) -> tuple[bool, str] | None:
        try:
            body = json.dumps(mattermost_payload(nc, note)).encode("utf-8")
            req = urllib.request.Request(
                nc.webhook_url, data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    # NEVER log nc.webhook_url -- the URL IS the secret.
                    log.info("notification sent", channel="mattermost")
                    return (True, "mattermost ok")
                log.warning("notification channel failed", channel="mattermost",
                            status=response.status)
                return (False, f"mattermost returned {response.status}")
        except Exception as e:
            log.warning("notification channel failed", channel="mattermost",
                        error=type(e).__name__)
            return (False, f"mattermost failed: {type(e).__name__}")

    def probe(self, nc: NotifyConfig, *, timeout: float) -> NotifyTransportProbe:
        return _probe_url(nc.webhook_url or "", "mattermost", timeout)


#: The backend registry. `config.NOTIFY_BACKENDS` is the schema-side copy of
#: these names (config.py cannot import notify.py -- notify imports config);
#: `test_notify` asserts the two never drift.
_BACKENDS: dict[str, NotifyBackend] = {
    b.name: b for b in (NtfyBackend(), WebhookBackend(), MattermostBackend())
}

#: Legacy precedence, preserved for configs with no explicit selector:
#: ntfy first, webhook as the fallback.
_LEGACY_CHAIN = ("ntfy", "webhook")


def resolve_backends(nc: NotifyConfig) -> list[NotifyBackend]:
    """The ordered backend chain `send()`/`probe_transport()` will use.

    * ``nc.backend`` set  -> EXACTLY that backend, no implicit fallback.
      Switching the active channel is then a declared config change, and a
      notification can never silently leave by a channel the operator did
      not name (NL-17 §3).
    * ``nc.backend`` unset -> the historical implicit precedence (ntfy if
      url+topic are set, then webhook if its url is set), so every existing
      nyxloom.toml keeps behaving exactly as before.

    Backends that are not configured are dropped; an empty list means "no
    push channel", which is a legitimate state, not a fault.
    """
    if nc.backend:
        backend = _BACKENDS.get(nc.backend)
        if backend is None:
            log.warning("notification backend unknown", backend=nc.backend)
            return []
        return [backend] if backend.is_configured(nc) else []
    return [_BACKENDS[name] for name in _LEGACY_CHAIN
            if _BACKENDS[name].is_configured(nc)]


def send(nc: NotifyConfig, note: dict) -> tuple[bool, str]:
    """Send a notification through the configured backend chain (NL-17).

    Dispatches over `resolve_backends(nc)`: each backend translates the
    typed note into its own payload shape. A backend that returns a
    definitive outcome ends the call; a backend that reports a
    transport-level fault (``None``) falls through to the next one -- which
    is how the historical "ntfy failed -> webhook fallback" still works,
    unchanged, for configs with no explicit selector.

    Never raises; returns (ok: bool, detail: str). Timeout is 5 seconds per
    backend. No channel configured -> (False, "unconfigured").
    """
    last_fault: str | None = None
    for backend in resolve_backends(nc):
        result = backend.deliver(nc, note)
        if result is not None:
            return result
        last_fault = f"{backend.name} failed"
    if last_fault is not None:
        # Every backend in the chain faulted at the transport level.
        return (False, last_fault)
    # No notification channel configured
    log.debug("notification unconfigured")
    return (False, "unconfigured")


def probe_transport(nc: NotifyConfig, *, timeout: float = _DEFAULT_PROBE_TIMEOUT_S,
                     ) -> NotifyTransportProbe:
    """Active reachability probe for the configured push channel (CR-16).

    Deliberately NOT `send()`: this never writes a title/body/topic, so it
    is safe to run on a tight interval without becoming the very
    notification storm watchdog.py exists to catch. It probes the FIRST
    backend of `resolve_backends(nc)` -- i.e. the channel a real
    notify_event() call would actually use, which is the point (NL-17: with
    an explicit `backend` selector that is the selected one; without it, the
    legacy ntfy-wins-over-webhook precedence, as before).

    A GET to the bare configured URL is enough: any HTTP response (even a
    404/405 -- ntfy's root path, a Mattermost webhook endpoint and a plain
    webhook receiver all routinely reject a bare GET) proves the socket
    connected and the server answered, which is everything this probe
    claims. A connection-level fault (refused, DNS, timeout) OR a malformed
    configured URL (`ValueError` -- urllib's own reaction to e.g. a
    scheme-less string; an operator typo is exactly as unreachable as a
    dead server, and this probe existing to be a caller's ONE place to ask
    "can the channel be used" is defeated if a typo instead raises past it)
    both mean the transport itself is down.
    """
    chain = resolve_backends(nc)
    if not chain:
        return NotifyTransportProbe("unconfigured", "", "no notification backend configured")
    return chain[0].probe(nc, timeout=timeout)


def notify_event(cfg: ProjectConfig, states: dict[str, TaskStateFile], ev: Event) -> None:
    """Append notification events if ev triggers a notification.

    1. If ev.type is NOTIFICATION_*, return (recursion guard).
    2. If ev.type.value in cfg.notify.push_classes:
       - Call notification_for(ev)
       - If result is None, return (not a handled type within push_classes)
       - Append NOTIFICATION_REQUESTED
       - Unless BOTH ntfy_url and webhook_url are unconfigured:
         - Call send(cfg.notify, note)
       - Append NOTIFICATION_DELIVERED or NOTIFICATION_FAILED based on result
    3. Never raise; all failures are recorded as events.
    """
    # Recursion guard
    if ev.type in (EventType.NOTIFICATION_REQUESTED, EventType.NOTIFICATION_DELIVERED, EventType.NOTIFICATION_FAILED):
        return

    # Check if this event type should trigger notifications
    if ev.type.value not in cfg.notify.push_classes:
        return

    # Generate notification content
    note = notification_for(ev)
    if note is None:
        return

    # Append NOTIFICATION_REQUESTED
    req_ev = storage.append_event(
        ev.project,
        actor=Actor(ActorKind.NOTIFIER, "notify"),
        type=EventType.NOTIFICATION_REQUESTED,
        payload={},
        task_id=ev.task_id,
        decision_id=ev.decision_id,
        wave_id=ev.wave_id,
    )

    # Is there any usable channel at all? (NL-17: asked of the resolved
    # backend chain rather than of the two url fields directly, so an
    # explicit `backend` selector is honoured here too. The recorded event
    # is identical either way -- NOTIFICATION_FAILED, detail 'unconfigured'.)
    both_unconfigured = not resolve_backends(cfg.notify)

    if both_unconfigured:
        # Both unconfigured: don't call send, just mark as failed -- a soft,
        # expected skip (no channel configured), not an operational failure.
        log.warning("notification skipped", reason="unconfigured",
                    event_type=ev.type.value, project=ev.project, task=ev.task_id)
        storage.append_event(
            ev.project,
            actor=Actor(ActorKind.NOTIFIER, "notify"),
            type=EventType.NOTIFICATION_FAILED,
            payload={"detail": "unconfigured"},
            task_id=ev.task_id,
            decision_id=ev.decision_id,
            wave_id=ev.wave_id,
        )
    else:
        # Try to send
        ok, detail = send(cfg.notify, note)
        if ok:
            log.info("notification delivered", event_type=ev.type.value,
                     project=ev.project, task=ev.task_id)
            storage.append_event(
                ev.project,
                actor=Actor(ActorKind.NOTIFIER, "notify"),
                type=EventType.NOTIFICATION_DELIVERED,
                payload={"detail": detail},
                task_id=ev.task_id,
                decision_id=ev.decision_id,
                wave_id=ev.wave_id,
            )
        else:
            log.warning("notification delivery failed", event_type=ev.type.value,
                        project=ev.project, task=ev.task_id, detail=detail)
            storage.append_event(
                ev.project,
                actor=Actor(ActorKind.NOTIFIER, "notify"),
                type=EventType.NOTIFICATION_FAILED,
                payload={"detail": detail},
                task_id=ev.task_id,
                decision_id=ev.decision_id,
                wave_id=ev.wave_id,
            )


def digest(cfg: ProjectConfig, project: str, since_seq: int) -> str:
    """Generate a plain-text digest of events.

    Summarizes events with type in digest_classes (MERGE_RECORDED, TASK_TRANSITIONED)
    since since_seq (exclusive). Reports:
    - Counts per type
    - Task IDs merged (sorted, unique)
    - Total cost recorded
    - Count of decisions still open (DECISION_OPENED without corresponding DECISION_RESOLVED)

    Output is deterministic (sorted order).
    """
    merge_count = 0
    transition_count = 0
    merged_tasks = set()
    total_cost = 0.0

    # Collect digest_classes events
    for ev in storage.iter_events(project, since=since_seq):
        if ev.type is EventType.MERGE_RECORDED:
            merge_count += 1
            if ev.task_id:
                merged_tasks.add(ev.task_id)
        elif ev.type is EventType.TASK_TRANSITIONED:
            transition_count += 1

    # Count open decisions (DECISION_OPENED without DECISION_RESOLVED in the same project)
    # For simplicity, collect all decision_ids from DECISION_OPENED and DECISION_RESOLVED
    open_decisions = set()
    for ev in storage.iter_events(project, since=0):
        if ev.type is EventType.DECISION_OPENED and ev.decision_id:
            open_decisions.add(ev.decision_id)
        elif ev.type is EventType.DECISION_RESOLVED and ev.decision_id:
            open_decisions.discard(ev.decision_id)

    # Build digest lines in deterministic order
    lines = []

    if merge_count > 0 or transition_count > 0:
        lines.append(f"MERGE_RECORDED: {merge_count}")

    if merged_tasks:
        sorted_tasks = sorted(merged_tasks)
        lines.append(f"Merged tasks: {', '.join(sorted_tasks)}")

    if transition_count > 0:
        lines.append(f"TASK_TRANSITIONED: {transition_count}")

    if open_decisions:
        lines.append(f"decisions open: {len(open_decisions)}")

    log.debug("digest generated", project=project, merges=merge_count,
              transitions=transition_count, decisions_open=len(open_decisions))

    if not lines:
        return ""

    return "\n".join(lines)
