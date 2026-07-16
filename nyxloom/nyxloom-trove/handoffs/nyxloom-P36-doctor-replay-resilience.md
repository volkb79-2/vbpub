---
schema_version: 1
id: nyxloom-P36-doctor-replay-resilience
project: nyxloom
title: "doctor must not die on its own event log (BLOCKED -> BLOCKED replay crash)"
tier: sonnet5-high
input_revision: "a7499cc"
depends_on: []
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
scope:
  touch:
    - "src/nyxloom/storage.py"
    - "src/nyxloom/doctor.py"
    - "tests/test_storage.py"
    - "tests/test_doctor.py"
  forbid:
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/types.py"
oracles:
  - id: O1
    observable: "`storage.apply_event` treats a fixed-target task event whose target equals the task's current state as an idempotent no-op — the same rule TASK_TRANSITIONED already gets at storage.py:209-225 — for TASK_BLOCKED, TASK_SUPERSEDED and TASK_CANCELLED. A test applies a TASK_BLOCKED event to a task already in BLOCKED and asserts no exception, that the state stays BLOCKED, and that no task_id is reported as affected."
    negative: "today's behaviour, reproduced on a7499cc: `check_task_transition(BLOCKED, BLOCKED)` raises `TransitionError: task transition BLOCKED -> BLOCKED not allowed`, because the from==to no-op at storage.py:209 is reached only when `t is EventType.TASK_TRANSITIONED`."
    gate: tester-unified
  - id: O2
    observable: "`storage.replay` completes over an event log containing a duplicate TASK_BLOCKED for an already-BLOCKED task and returns the projection. A test writes such a log, replays it, and asserts the task's replayed state is BLOCKED and carries the LATEST blocker payload."
    negative: "replay raises TransitionError partway and the caller gets no projection at all, so an append-only log written by an older daemon is permanently unreplayable."
    gate: tester-unified
  - id: O3
    observable: "`doctor.doctor_project` returns findings rather than propagating an exception when a check raises: the replay-divergence check catches failures broadly (not only NotImplementedError) and degrades to a finding naming the failure, and the remaining checks still run. A test injects a raising replay and asserts doctor_project returns a finding of the degraded kind AND that later checks still contributed their findings."
    negative: "the live incident (2026-07-16, dstdns, three tasks BLOCKED): `exec-nyxloom.py doctor` exits with a TransitionError traceback instead of a report — the one health surface an operator reaches for when tasks are blocked is the one that dies on them, and all eleven checks are lost because check 1 raised."
    gate: tester-unified
  - id: O4
    observable: "A genuinely illegal transition still raises: a test asserts `apply_event` raises TransitionError for a real violation with distinct from/to (e.g. COMPLETED -> ACTIVE), so this package only relaxes the from==to case."
    negative: "the fix relaxes transition validation generally (e.g. catching TransitionError inside apply_event), letting real state-machine corruption enter the projection silently — replay is the authoritative chokepoint and must stay strict on semantics."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "making replay tolerant of the duplicate-BLOCKED history requires editing types.py, reconcile.py or daemon.py"
  - "the from==to no-op cannot be extended to the fixed-target events without weakening validation for distinct from/to transitions"
---

# P36 — doctor must not die on its own event log

`doctor` is the surface an operator reaches for **precisely when tasks are
BLOCKED** — and that is exactly when it crashes. Backlog **B11**, observed
2026-07-16 against dstdns with three tasks sitting BLOCKED
`interrupted-dead-end`: `exec-nyxloom.py doctor` exits with
`TransitionError: task transition BLOCKED -> BLOCKED not allowed` instead of a
report.

## Root cause (reproduced on `a7499cc`, not inferred)

`storage.apply_event` has an idempotency no-op for a from==to task event, but
its guard is `if t is EventType.TASK_TRANSITIONED and tsf.state == to`
(storage.py:209). Its own comment explains the scoping: TASK_TRANSITIONED is
"the only branch here whose target is a free parameter (BLOCKED/SUPERSEDED/
CANCELLED targets are fixed by the event type itself), so from==to only arises
for this one."

**That reasoning does not hold.** A fixed target still equals the current state
when the task is *already* in it. A second `TASK_BLOCKED` for an already-BLOCKED
task therefore falls straight through to `check_task_transition(BLOCKED, BLOCKED)`
— and `TASK_TRANSITIONS[BLOCKED]` (types.py:91-93) does not contain BLOCKED, so
it raises. Verified in the gate container:

```
RAISED TransitionError: task transition BLOCKED -> BLOCKED not allowed
```

(applying a TASK_BLOCKED event to a `TaskStateFile` already in BLOCKED.)

The crash reaches `doctor` because check 1 of `doctor_project` calls
`storage.replay`, and its `except` clause catches only `NotImplementedError`
(doctor.py:60-84). One raising check kills all eleven.

Reconcile's `!= BLOCKED` guard (reconcile.py:438) stops *new* duplicates, which
is why this is latent rather than constant — but the event log is **append-only**
and dstdns's already contains them (the TICK_ERROR spam at seq 91-137 predates
that guard). History cannot be rewritten, so replay must tolerate it.

## Two layers, both required

1. **Root cause** — `storage.apply_event`: extend the from==to no-op to the
   fixed-target events. A duplicate BLOCKED is a *re-assertion*, not a
   transition.
2. **Defence in depth** — `doctor.doctor_project`: a health surface must degrade
   to a finding, never propagate. Even with layer 1 fixed, any future replay
   defect must not take out the other ten checks.

Layer 1 alone leaves doctor one bug away from dying again; layer 2 alone leaves
`replay` broken for every other caller. Do both.

## Context to read first (read ONLY these, in order)

- `src/nyxloom/storage.py`
  - **201-236** — the task-event branch. **209-225** is the existing from==to
    no-op and the comment whose reasoning you are correcting; **226** is the
    `check_task_transition` call that raises. Note the no-op returns *before*
    touching `tsf.state`/`since`/`blocker`. Decide deliberately what a
    re-asserted TASK_BLOCKED should do with the newer blocker payload, and say
    so in the report — O2 requires the LATEST blocker to win, so a bare early
    return is not sufficient for the BLOCKED case.
  - **301-306** — `replay`; it has no error handling of its own by design.
- `src/nyxloom/types.py` (**READ only — forbidden to edit**) — **63-97**
  `TASK_TRANSITIONS` (BLOCKED's targets exclude BLOCKED) and **538-549**
  `check_task_transition` / `TransitionError`. The transition graph is
  normative (SPEC §4) and must NOT gain a BLOCKED -> BLOCKED self-edge: this is
  an idempotency question at the apply layer, not a legal state-machine edge.
- `src/nyxloom/doctor.py` **56-90** — `doctor_project` check 1 and its
  `except NotImplementedError` clause; the module's other checks follow the same
  degrade-to-finding shape you extend.
- `tests/test_storage.py` and `tests/test_doctor.py` — mirror existing
  apply_event / doctor fixtures.

## Work

1. `storage.apply_event`: extend the from==to idempotent no-op to TASK_BLOCKED,
   TASK_SUPERSEDED and TASK_CANCELLED. Keep validation strict for every distinct
   from/to pair (O4). For a re-asserted TASK_BLOCKED, refresh the blocker payload
   and `notes` so the latest reason wins, without re-validating the transition.
2. `doctor.doctor_project`: broaden check 1's degradation so any check failure
   becomes a finding naming the failure and the remaining checks still run.
   Keep the existing `check-unavailable` behaviour for NotImplementedError.
3. Tests in `tests/test_storage.py` for O1, O2, O4 and `tests/test_doctor.py`
   for O3.

## Scope / forbid

Touch ONLY `storage.py`, `doctor.py` and their two test files. `types.py` is
forbidden — do not add a BLOCKED -> BLOCKED edge to the normative transition
graph. `reconcile.py` and `daemon.py` are forbidden: reconcile's `!= BLOCKED`
guard stays as it is, and this package changes no daemon behaviour.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file (see `escalate_if`), STOP — write `BLOCKED: <reason>` to the LOG, commit,
and exit. Do NOT improvise a workaround.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P36-doctor-replay-resilience` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Gate

`tester-unified` (the project's real gate — never the cockpit):

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
