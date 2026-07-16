---
schema_version: 1
id: nyxloom-P30-intake-ui-tab
project: nyxloom
title: "Dashboard tab: start & run a feature-intake conversation"
tier: sonnet5-high
input_revision: "e329de2"
depends_on: [nyxloom-P29-intake-agent-backend]
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
scope:
  touch:
    - "src/nyxloom/render.py"
    - "src/nyxloom/daemon.py"
    - "tests/test_intake_ui.py"
  forbid:
    - "src/nyxloom/reconcile.py"
oracles:
  - id: O1
    observable: "The dashboard renders a new **Intake** tab (via render.py) that (a) lists open intake conversations and (b) offers a form to start one from a rough feature request. A render test asserts the tab + form appear in the produced HTML for a state containing an intake session."
    negative: "no intake surface exists in the dashboard HTML, or it is only reachable by hand-editing files"
    gate: tester-unified
  - id: O2
    observable: "The daemon's HTTP surface gains a write endpoint (e.g. POST /api/intake) that calls `intake_chat.advance_intake(...)` (P29) and returns the agent's reply — the ONE sanctioned write path, loopback-only like the existing http_port surface, input treated as untrusted (redacted, no shell). A test drives the handler (with advance_intake stubbed) and asserts it advances a turn and echoes the reply."
    negative: "the tab is read-only with no way to submit a reply, OR the endpoint runs shell / trusts input / is exposed beyond loopback"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "adding a POST endpoint requires reworking the read-only HTTP server beyond a single guarded route (then propose the surface change as a D-decision)"
  - "scope requires editing intake_chat.py (P29 owns it) or reconcile.py"
---

# P30 — Dashboard tab: start & run a feature-intake conversation

Phase **γ**. The human surface for P29's intake agent: a dashboard tab where a
user types a rough feature request and runs the interview to a persisted brief.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P30-intake-ui-tab` from `main`);
commit all work on that branch. Do not touch the main checkout.

## Context to read first

- `src/nyxloom/render.py` — how the dashboard HTML is produced today (the tab
  structure, how existing tabs/sections are emitted). Mirror the existing tab
  idiom for the new **Intake** tab. Read the whole module first.
- `src/nyxloom/daemon.py` — the read-only HTTP/SSE surface (the server thread,
  the GET routing). Add ONE guarded POST route for intake replies, loopback-only
  like the existing `http_port`. Do NOT broaden the server's exposure.
- `src/nyxloom/intake_chat.py` (P29, dependency) — the `advance_intake` entry
  your POST handler calls; the `IntakeChat` state your tab renders. READ it;
  do not edit it.
- The P22 dashboard handoff/report (in `nyxloom-trove/archive/` or reports) for
  the established dashboard conventions.

## Work

1. `render.py`: add the **Intake** tab — list open intake conversations
   (from persisted `IntakeChat` state) + a start-a-request form + the running
   transcript.
2. `daemon.py`: add a single loopback-only POST endpoint that calls
   `intake_chat.advance_intake` and returns the reply; treat the body as
   untrusted (redacted, no shell, typed handling).
3. `tests/test_intake_ui.py`: prove O1 (tab+form render) and O2 (POST advances
   a turn; stub advance_intake).

## Scope / forbid

Touch ONLY the three files in `scope.touch`. Do not edit `intake_chat.py`
(P29's) or `reconcile.py`. The POST endpoint stays loopback-only + input-untrusted.

## BLOCKED rule

If the read-only HTTP server cannot take a single guarded POST route without a
larger rework, STOP — write `BLOCKED: <reason>` to the LOG, commit, exit; raise
the surface-change as a `D-<NNN>`.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
