---
schema_version: 1
id: nyxloom-P29-intake-agent-backend
project: nyxloom
title: "Feature-intake conversational agent (backend, decision-chat sibling)"
tier: sonnet5-high
input_revision: "e329de2"
depends_on: [nyxloom-P28-backlog-schema-autotick]
session: fresh
source: {kind: backlog, ref: nyxloom-trove/4-backlog.md}
scope:
  touch:
    - "src/nyxloom/intake_chat.py"
    - "src/nyxloom/cli.py"
    - "src/nyxloom/decisions.py"
    - "tests/test_intake_chat.py"
  forbid:
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/decision_chat.py"
oracles:
  - id: O1
    observable: "`intake_chat.advance_intake(cfg, project, intake_id, user_text)` on the FIRST turn launches a READ-ONLY, redacted claude session seeded with an intake system prompt that (a) names the project context to read (`cfg` [refs] docs + roadmap + backlog + recent handoffs) and (b) states the interview goal. It uses the SAME read-only route/tooling posture as decision_chat (no write/exec tools, redact_patterns applied). A test asserts the constructed session's tool policy is read-only and the system prompt references the context sources. Persistence mirrors decision_chat: an `IntakeChat` dataclass round-trips via `save_chat`/`load_chat` under an intake dir."
    negative: "the intake session is granted write/exec tools or unredacted context — a security regression versus decision_chat's read-only+redacted contract"
    gate: tester-unified
  - id: O2
    observable: "A reply containing a `BRIEF:` finalize block (parsed like decision_chat `_parse_decision_line`) persists a STRUCTURED backlog item via `backlog_items` (P28): a new `B<N>` with status `open`, an optional `priority`, a `decisions` link list, and a pre-carve detail body distilled from the interview (purpose, elicited detail, consequences). A test drives a scripted transcript to a `BRIEF:` and asserts the new structured item exists with those fields."
    negative: "the brief is lost, or persisted as unstructured prose with no carve hints / no structured header — so a later carve re-derives context from scratch"
    gate: tester-unified
  - id: O3
    observable: "When the interview surfaces a genuine product call, the agent files a `D-NNN` in `decisions.md` (via a new `decisions.open_decision(...)` helper that appends a well-formed D entry) and the persisted brief references it in its `decisions: [D-NNN]` field. A test asserts both the D entry and the brief->decision link exist."
    negative: "product calls are silently baked into the brief prose with no D-NNN record, so the eventual handoff has no depends_on decision hold"
    gate: tester-unified
  - id: O4
    observable: "A `nyxloom intake <project> <intake_id> <message>` CLI subcommand advances a turn (start on first call, resume on subsequent), parallel to the existing `discuss` verb for decisions — the programmatic entry P30's UI will call. A test invokes it and asserts a turn is recorded/resumed."
    negative: "there is no programmatic entry point, so the UI (P30) has nothing to call"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "a named contract cannot be met without touching a forbidden file (reconcile.py / daemon.py / decision_chat.py)"
  - "read-only+redacted intake sessions cannot reuse the decision-chat route/adapter posture without importing decision_chat internals (then propose the shared-helper extraction as a D-decision, do not fork the security logic)"
---

# P29 — Feature-intake conversational agent (backend)

Phase **β**. The factory's **front door**: a conversational agent that turns a
ROUGH feature request into a carve-ready structured brief. It is a deliberate
**sibling of `decision_chat.py` (P18)** — same skeleton (resumable read-only
redacted claude session, persisted chat state, confirm-token finalize), a
different goal (interview -> brief) and output (a structured backlog item via
P28, plus spawned `D-NNN` decisions).

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P29-intake-agent-backend` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these, in order)

- `src/nyxloom/decision_chat.py` — THE template. Mirror, do not import-and-fork
  its security logic. Key anchors:
  - `DecisionChat` / `DecisionChatMessage` dataclasses + `to_dict`/`from_dict`
    and `load_chat`/`save_chat`/`_chat_dir`/`_chat_path` (the persistence
    pattern) — build the analogous `IntakeChat`.
  - `advance_chat(cfg, project, decision_id, user_text)` (lines ~381) — the
    launch-first / resume-Nth turn engine; `_first_turn_system_prompt`,
    `_run_subprocess_turn`, `_extract_reply_text`.
  - `_parse_decision_line` / `_finalize_decision` (the `DECISION:` confirm
    token) — build the analogous `BRIEF:` parse + persist-to-backlog.
  - The READ-ONLY + redacted posture (route selection `_pick_route`, the
    read-only tool policy, `cfg.redact_patterns`) — reuse the SAME posture; the
    intake agent must never get write/exec tools or unredacted context.
- `src/nyxloom/backlog_items.py` (from P28, your dependency) — persist the brief
  as a STRUCTURED item (id, status=open, priority, decisions links, detail).
  Add a `create(...)` if P28 only exposed parse/validate/tick.
- `src/nyxloom/decisions.py` — how D-NNN entries are parsed/finalized. Add
  `open_decision(cfg, question, resume_prompt) -> D-id` that appends a
  well-formed new D entry (the inverse of `decide()`), so the intake agent can
  file product calls.
- `src/nyxloom/cli.py` — the `discuss` verb (decision-chat's CLI entry) as the
  pattern for a new `intake` verb.

## Work

1. `src/nyxloom/intake_chat.py`: `IntakeChat` state + persistence (mirror
   decision_chat), `advance_intake(cfg, project, intake_id, user_text)` (turn
   engine reusing the read-only redacted route posture), an intake
   `_first_turn_system_prompt` encoding the 7-step interview (confirm
   understanding -> elicit detail -> surface consequences -> file D-NNN for
   product calls -> estimate blockers/priority over depends_on graph -> ask
   priority -> on satisfaction emit `BRIEF:`), and `_parse_brief` +
   `_finalize_brief` that persists the structured backlog item and links any
   spawned decisions.
2. `src/nyxloom/decisions.py`: add `open_decision(...)` (append a new D entry).
3. `src/nyxloom/cli.py`: add the `intake <project> <intake_id> <message>` verb.
4. `tests/test_intake_chat.py`: prove O1 (read-only+redacted + context refs),
   O2 (BRIEF -> structured item), O3 (product call -> D-NNN + brief link),
   O4 (CLI verb advances/resumes). Script the claude turns (stub the
   subprocess like decision_chat's tests do) — do NOT call a live model.

## Scope / forbid

Touch ONLY the four files in `scope.touch`. Do NOT edit `decision_chat.py`
(forbidden — if a shared read-only-session helper is truly needed, that is a
BLOCKED/D-decision, not an in-place fork). Keep off `reconcile.py`/`daemon.py`
(P26's area). The intake session's tool policy MUST be read-only + redacted.

## BLOCKED rule

If a named contract cannot be met without a forbidden file, STOP — write
`BLOCKED: <reason>` to the LOG, commit, and exit. Product gaps (e.g. the exact
brief field set) are `D-<NNN>` decisions, not workarounds.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
