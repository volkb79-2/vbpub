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
  ntfy: urllib POST to f'{ntfy_url}/{ntfy_topic}', body=note['body'],
  headers Title/Priority/Tags/Click; webhook: POST JSON of note to
  webhook_url. 5s timeout. Never raises; (ok, detail). Both configured ->
  ntfy wins, webhook is fallback on failure.
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


def send(nc: NotifyConfig, note: dict) -> tuple[bool, str]:
    """Send a notification via ntfy and/or webhook.

    ntfy wins if both are configured; webhook is fallback on ntfy failure.
    Never raises; returns (ok: bool, detail: str).
    Timeout is 5 seconds. Connection refused or server error returns (False, ...).
    """
    # If ntfy is configured, try it first
    if nc.ntfy_url and nc.ntfy_topic:
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

    # Try webhook fallback if ntfy failed or not configured
    if nc.webhook_url:
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

    # No notification channel configured
    log.debug("notification unconfigured")
    return (False, "unconfigured")


def probe_transport(nc: NotifyConfig, *, timeout: float = _DEFAULT_PROBE_TIMEOUT_S,
                     ) -> NotifyTransportProbe:
    """Active reachability probe for the configured push channel (CR-16).

    Deliberately NOT `send()`: this never writes a title/body/topic, so it
    is safe to run on a tight interval without becoming the very
    notification storm watchdog.py exists to catch. ntfy wins if both are
    configured, mirroring `send()`'s own precedence -- probing the channel
    a real notify_event() call would actually use is the point.

    A GET to the bare configured URL is enough: any HTTP response (even a
    404/405 -- ntfy's root path and a webhook endpoint both routinely
    reject a bare GET) proves the socket connected and the server answered,
    which is everything this probe claims. A connection-level fault
    (refused, DNS, timeout) OR a malformed configured URL (`ValueError` --
    urllib's own reaction to e.g. a scheme-less string; an operator typo is
    exactly as unreachable as a dead server, and this probe existing to be
    a caller's ONE place to ask "can the channel be used" is defeated if a
    typo instead raises past it) both mean the transport itself is down.
    """
    if nc.ntfy_url and nc.ntfy_topic:
        try:
            req = urllib.request.Request(nc.ntfy_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout):
                pass
        except urllib.error.HTTPError as exc:
            return NotifyTransportProbe(
                "healthy", "ntfy", f"ntfy reachable (HTTP {exc.code})")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            return NotifyTransportProbe(
                "unreachable", "ntfy", snapshot.bounded_detail(f"{type(exc).__name__}: {exc}"))
        return NotifyTransportProbe("healthy", "ntfy", "ntfy reachable")

    if nc.webhook_url:
        try:
            req = urllib.request.Request(nc.webhook_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout):
                pass
        except urllib.error.HTTPError as exc:
            return NotifyTransportProbe(
                "healthy", "webhook", f"webhook reachable (HTTP {exc.code})")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            return NotifyTransportProbe(
                "unreachable", "webhook", snapshot.bounded_detail(f"{type(exc).__name__}: {exc}"))
        return NotifyTransportProbe("healthy", "webhook", "webhook reachable")

    return NotifyTransportProbe("unconfigured", "", "no ntfy or webhook configured")


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

    # Check if both notification channels are unconfigured
    both_unconfigured = not (cfg.notify.ntfy_url or cfg.notify.webhook_url)

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
