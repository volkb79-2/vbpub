---
kind: backlog-entry
schema_version: 1
id: NL-17
title: "notify.py's webhook fallback is a raw note-dict passthrough, not a per-backend adapter -- cannot actually target Mattermost/Slack today"
status: open
type: "feature"
severity: "low"
provenance: "operator request, live session 2026-09-08, during nyxloom-P103 ntfy-redeploy follow-up"
filed_date: "2026-09-08"
---

## Observed mechanism and reproduction

`nyxloom/src/nyxloom/notify.py` has exactly two hardcoded delivery paths in
`send(nc: NotifyConfig, note: dict)` (notify.py:256-330ish):

1. **ntfy** (`nc.ntfy_url` + `nc.ntfy_topic`): builds ntfy-specific HTTP
   headers (`Title`, `Priority`, `Click`, `Tags`, optional `Authorization`
   bearer token) and POSTs `note['body']` as the raw request body.
2. **webhook** (`nc.webhook_url`), tried only as a fallback if ntfy is
   unconfigured or fails: `json.dumps(note)` — the raw internal `note` dict
   (`{title, body, click, priority, tags}`, produced by `notification_for()`)
   — POSTed verbatim as the JSON body with `Content-Type: application/json`.

The webhook path is a passthrough, not an adapter: it assumes the receiving
service understands nyxloom's own internal note schema natively. It does
not. Confirmed by inspection against Mattermost's incoming-webhook contract:
Mattermost expects a payload shaped like `{"text": "..."}` (optionally
`username`, `icon_url`, `channel`), and does not read `title`/`click`/
`priority`/`tags` at all — pointing `webhook_url` at a Mattermost incoming-
webhook URL today would deliver a JSON blob Mattermost does not render as a
message. The same gap applies to Slack's incoming-webhook shape. There is no
per-backend translation step; `send()` has exactly one webhook code path
serving every possible receiver identically.

Operator, in a live session (2026-09-08), while nyxloom-P103's own follow-up
was pending (the live `nyxloom-ntfy` container still needed a redeploy to
pick up P103's governance fix — see NL-6/NL-8..11), asked to "plan to remove
ntfy - deactivate paths to it, do not delete it yet. communication to ntfy
should go through an adapter so we can switch the backend/service to use
e.g. against mattermost." Follow-up in the same session, once this entry was
filed: "just disable ntfy. NL17 the adapter just needs to work for
mattermost now, but plan to integrate telegram and discord as well" plus a
directive to stand up a Mattermost instance as its own ciu-managed stack
(mirroring `nyxloom/ntfy/`'s pattern) in parallel — see nyxloom-P106.

**Status update, same session**: `nyxloom-ntfy` (the live, ungoverned
fallback container) was stopped (`docker stop nyxloom-ntfy`, reversible —
not removed) rather than redeployed under P103's governance fix, since it
is being replaced rather than kept running. dstdns's own
`nyxloom-trove/nyxloom.toml` also references this same ntfy instance, but
no `nyxloomd` container was running there at the time, so nothing was
actively depending on it live.

This entry is now **implementation-ready, not just design-tracking**:
scope is Mattermost-only for the real backend build (nyxloom-P106); the
`NotifyBackend` seam should be shaped so Telegram and Discord backends can
be added later without a redesign, but those two are explicitly NOT built
now — no forcing function for them yet, per the estate's standing
don't-build-ahead-of-a-forcing-function discipline.

## Why nyxloom owns it

`notify.py`'s `send()`/`NotifyConfig`/`notification_for()` are nyxloom's own
notification module; the backend-selection and payload-shaping logic live
entirely inside this file, not in any consumer project.

## Proposed contract

Not designed here — this entry tracks the decision, not the implementation.
Sketch, per the operator's own framing:

1. A `NotifyBackend` seam (protocol/small class hierarchy) that `send()`
   dispatches to, where each backend owns translating the typed `note` dict
   into ITS OWN expected payload shape — not a shared raw-JSON passthrough.
   The existing ntfy header-POST logic becomes one such backend, unchanged
   in behavior. **Build one real new backend now: Mattermost**, mapping
   `note` into Mattermost's incoming-webhook shape (`text` built from
   `title`+`body`, optionally `username`/`icon_url`), so `webhook_url`
   actually renders somewhere real instead of failing silently against
   Mattermost specifically (today's `send()` still reports
   `(True, "webhook ok")` on any 200 response — a Mattermost webhook may
   well 200 on a payload it can't render, so this failure mode would
   currently go undetected). Shape the seam so Telegram and Discord
   backends can be added later as additional `NotifyBackend`
   implementations without touching `send()`'s dispatch logic — but do NOT
   build those two now, no forcing function yet.
2. ntfy is now stopped (see status update above). Keep its backend
   implementation in the seam (selectable, not deleted) per the operator's
   "deactivate paths to it, do not delete it yet" — just not the active
   default once Mattermost is verified working.
3. Config shape (`NotifyConfig`) needs an explicit backend selector rather
   than the current implicit "ntfy wins if both configured" precedence,
   so switching backends is a declared config change, not a side effect of
   which URLs happen to be set.
4. Depends on nyxloom-P106 (parallel track) actually standing up a live
   Mattermost instance as a ciu-managed stack, mirroring `nyxloom/ntfy/`'s
   pattern, to configure/verify the new backend against.

## Oracles

Not applicable yet — design-tracking entry, not implementation-ready.
Whoever picks this up re-derives oracles once the backend seam shape is
chosen (candidates: a fake-backend unit test per adapter asserting the
translated payload shape matches the target service's documented contract;
a `send()` behavioral test confirming backend selection is explicit, not
url-presence-implicit).

## SPEC ownership

`nyxloom/src/nyxloom/notify.py`, `NotifyConfig` (config.py), the
`[notify]` section of `nyxloom.toml` schema.

## Provenance

Operator request, live session 2026-09-08, surfaced while resolving
nyxloom-P103's outstanding ntfy-redeploy follow-up (see
nyxloom-trove backlog NL-6/NL-8/NL-9/NL-10/NL-11, all filed the same day
from the same P103 governance work).
