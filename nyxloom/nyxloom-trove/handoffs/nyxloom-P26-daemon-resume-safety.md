---
schema_version: 1
id: nyxloom-P26-daemon-resume-safety
project: nyxloom
title: "Daemon resume-safety: detect failing resumes, fresh-start instead of looping"
tier: sonnet5-high
input_revision: "6d1f2be"
depends_on: [nyxloom-P24-config-schema-lint]
session: fresh
source: {kind: backlog, ref: nyxloom-trove/4-backlog.md}
scope:
  touch:
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/config.py"
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/schemas/nyxloom-config.schema.json"
    - "tests/test_reconcile.py"
  forbid:
    - "src/nyxloom/wrapper.py"
    - "src/nyxloom/adapters.py"
    - "src/nyxloom/storage.py"
    - "src/nyxloom/types.py"
oracles:
  - id: O1
    observable: "In `reconcile.plan_project`, an INTERRUPTED attempt whose consecutive failed-resume count (new `ReconcileInput.resume_failures[attempt_id]`) is >= `policy.max_resume_failures` no longer yields a `ResumeAttempt`; instead the poisoned attempt is marked terminal and — when the task's fresh-attempt budget (`max_attempts_per_task`, counting distinct attempt records) still has room — a fresh `DispatchImplementer` is planned (new attempt, NO session_handle carried). A unit test in tests/test_reconcile.py builds a ReconcileInput with resume_failures at the threshold and asserts the actions contain a fresh DispatchImplementer and contain NO ResumeAttempt for that attempt."
    negative: "The planner keeps emitting ResumeAttempt for the same session_handle every pass once quiet/interrupted, looping into the same broken session until max_attempts_per_task distinct records are burned (which never happens because resumes reuse one record) — i.e. the current reconcile.py:446-447 behavior, unbounded resume of a poisoned session."
    gate: tester-unified
  - id: O2
    observable: "A resume that made progress does NOT trip the fallback: when resume_failures[attempt_id] < max_resume_failures, an INTERRUPTED attempt with a session_handle and remaining budget still yields ResumeAttempt (unchanged healthy path). A unit test asserts ResumeAttempt (not DispatchImplementer) for an interrupted attempt below the failure threshold."
    negative: "The implementation fresh-restarts on the FIRST interrupt, discarding a resumable session unnecessarily and losing all in-session progress (over-eager fallback)."
    gate: tester-unified
  - id: O3
    observable: "`policy.max_resume_failures` (default 2) and `policy.resume_progress_grace_seconds` (default 120) exist on config.Policy with those defaults; a nyxloom.toml [policy] that sets them overrides; omitting them yields the defaults. Both keys are permitted by src/nyxloom/schemas/nyxloom-config.schema.json (extends P24's schema). Tests: a config-load assertion for defaults + override, and the schema validates a config carrying both keys."
    negative: "Thresholds are hardcoded constants ignoring nyxloom.toml, OR the new [policy] keys make a valid nyxloom.toml fail P24's config-lint because the schema was not extended."
    gate: tester-unified
  - id: O4
    observable: "When fresh-attempt budget is exhausted after the resume-failure fallback (no distinct records left), the task transitions to BLOCKED with a typed ENVIRONMENT blocker (reusing the existing silent-dead-end path at reconcile.py:456-462), never left silently ACTIVE. A test asserts the Transition(to=BLOCKED) with a blocker when resume_failures>=threshold AND attempts_count>=max_attempts_per_task."
    negative: "A resume-poisoned task with no budget is left ACTIVE forever with no further actions (silent dead-end)."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "a named contract cannot be met without touching a forbidden file (wrapper.py / adapters.py / storage.py / types.py)"
  - "distinguishing a failed resume from a healthy one provably requires a new Attempt record field (types.py) or a wrapper/adapter change"
---

# P26 — Daemon resume-safety: detect failing resumes, fresh-start instead of looping

Replaces the manual operator rule *"DON'T restart the daemon needlessly"* with
**automatic, safe, configurable** detection inside the reconciler. Two failure
modes must be handled without a human babysitting the daemon:

1. **Inactivity** — already largely handled (tier-1 `stall_log_quiet_seconds`,
   tier-2 `_confirm_stall` /proc check, and the `attempt_max_wall_seconds`
   wall-clock cap). This package does **not** rebuild that; it only ensures the
   fresh-start path below integrates with it. No change to the stall/wall-clock
   branches (reconcile.py:402-426) except as O1/O4 require.
2. **A resume that does not work** — the real gap. A resumed session that keeps
   dying (the "hung/poisoned resume") is currently resumed **forever**: resumes
   reuse one attempt record (`.resume-N`), so the `attempts_count <
   max_attempts_per_task` guard (reconcile.py:446) never trips. Detect repeated
   failed resumes, **stop resuming that session**, and **fresh-start** a new
   attempt (new session) under a configurable threshold — or BLOCK cleanly if
   the fresh-attempt budget is gone.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P26-daemon-resume-safety` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these, in order)

- `src/nyxloom/reconcile.py`
  - lines 398-462 — the attempt-lifecycle decision block. The INTERRUPTED
    branch at **438-462** is the one you change: 446-447 emits `ResumeAttempt`;
    448-462 is the existing typed-BLOCKED dead-end you reuse for O4.
  - lines 244-272 — the `ReconcileInput` dataclass. Add `resume_failures:
    dict[str, int]` here, mirroring the existing `log_quiet_seconds` /
    `stall_confirmed` dict fields (attempt_id -> count). Give it a
    `field(default_factory=dict)` so existing tests that omit it still build.
  - lines 600-668 — `_dispatch_block_reason`, for how `attempts_count` and
    budget checks are already expressed (mirror the counting idiom).
- `src/nyxloom/config.py`
  - lines 91-116 — the `Policy` dataclass. Add `max_resume_failures: int = 2`
    and `resume_progress_grace_seconds: int = 120` next to
    `stall_log_quiet_seconds` / `attempt_max_wall_seconds`. Confirm the toml
    loader that builds Policy reads them (follow how `stall_log_quiet_seconds`
    is parsed).
- `src/nyxloom/daemon.py`
  - lines 20-95 — `run_pass` input-building narrative + EXECUTION MAP. You add
    ONE input computation: derive `resume_failures[attempt_id]` from the
    attempt dir on disk (count consecutive `.resume-N` logs that never grew
    past a trivial size within `resume_progress_grace_seconds`, i.e. a resume
    that produced no progress), exactly analogous to how `log_quiet_seconds` is
    computed from log mtime. Do NOT add or change any Action handler in the
    EXECUTION MAP — reuse the existing `DispatchImplementer` (fresh) and
    `ResumeAttempt` (resume) actions; the decision is purely in
    `reconcile.plan_project`.
- `src/nyxloom/schemas/nyxloom-config.schema.json` (created by P24, your
  dependency) — add the two new `[policy]` integer keys so a nyxloom.toml
  carrying them passes P24's config-lint.
- `tests/test_reconcile.py` — mirror an existing INTERRUPTED-attempt test to
  build the fixtures for O1/O2/O4. Find the current test that exercises the
  ResumeAttempt-vs-BLOCKED branch and clone its ReconcileInput builder.

## Work

1. `config.Policy`: add `max_resume_failures: int = 2` and
   `resume_progress_grace_seconds: int = 120`; wire both through the toml
   loader with the documented defaults.
2. `src/nyxloom/schemas/nyxloom-config.schema.json`: permit both new
   `[policy]` integer keys (extend P24's schema; keep it strict otherwise).
3. `reconcile.ReconcileInput`: add `resume_failures: dict[str, int]`
   (default-factory dict).
4. `reconcile.plan_project`, INTERRUPTED branch (438-462): before choosing
   `ResumeAttempt`, consult `resume_failures.get(attempt_id, 0)`. If it is
   `>= policy.max_resume_failures`: do NOT resume. Instead, if the task's
   **distinct-record** attempts budget still has room, mark the poisoned
   attempt terminal (INTERRUPTED, via the existing MarkInterrupted/Transition
   idiom — no new attempt state) and plan a fresh `DispatchImplementer` with NO
   session_handle carried; otherwise fall through to the existing typed-BLOCKED
   dead-end (456-462). If `resume_failures < max_resume_failures`, keep the
   current healthy behavior (ResumeAttempt when session_handle + budget).
5. `daemon.run_pass`: compute `resume_failures` into the ReconcileInput from
   the attempt dir on disk (consecutive no-progress `.resume-N` logs within the
   grace window), mirroring `log_quiet_seconds`. Pure input-building; no Action
   handler changes.
6. Tests in `tests/test_reconcile.py` proving O1, O2, O4; a config test proving
   O3 defaults + override; a schema test proving O3's schema acceptance.

## Scope / forbid

Touch ONLY the five files in `scope.touch`. Do **not** add a new Attempt state
or field (types.py is forbidden — reuse INTERRUPTED + the on-disk
`resume_failures` computation), and do **not** change wrapper.py / adapters.py /
storage.py. If the contract genuinely cannot be met without one of those, that
is a BLOCKED trigger, not a workaround.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file (see `escalate_if`), STOP — write `BLOCKED: <reason>` to the LOG, commit,
and exit. Do NOT improvise a workaround. This is a success mode (the controller
re-routes), not a failure.

## Gate

`tester-unified` (the project's real gate — never the cockpit):

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
