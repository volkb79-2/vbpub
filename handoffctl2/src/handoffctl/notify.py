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
  is an operator cron concern, exposed via CLI 'handoffctl digest'.)
"""

from __future__ import annotations

from .config import NotifyConfig, ProjectConfig
from .types import Event, TaskStateFile


def notification_for(ev: Event) -> dict | None:
    raise NotImplementedError


def send(nc: NotifyConfig, note: dict) -> tuple[bool, str]:
    raise NotImplementedError


def notify_event(cfg: ProjectConfig, states: dict[str, TaskStateFile], ev: Event) -> None:
    raise NotImplementedError


def digest(cfg: ProjectConfig, project: str, since_seq: int) -> str:
    raise NotImplementedError
