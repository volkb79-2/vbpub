---
kind: backlog-entry
schema_version: 1
id: NL-14
title: "gate_verify_interval_days/days_since_gate_verify are dead, schema-validated config surface since GA4's retirement"
status: open
type: "bugfix"
severity: "low"
component: "config"
provenance: "nyxloom-P102 retroactive adversarial review, 2026-09-08, F2"
filed_date: "2026-09-08"
---

## Observed mechanism and reproduction

`nyxloom-P98` (2026-09-03) retired GA1's `nyxloom gate verify` CLI and
GA4's daemon verify-cadence, but deliberately kept two fields declared
(per that package's own carve, to avoid breaking `tests/legacy_planner.py`'s
frozen byte-identity snapshot check):

- `src/nyxloom/config.py:169` -- `gate_verify_interval_days: int = 0`, a
  live field on `Policy`, still schema-validated
  (`src/nyxloom/schemas/nyxloom-config.schema.json:253-256`,
  `"type": "integer", "minimum": 0`). A project can set this in its
  `nyxloom.toml` today and it validates cleanly while doing nothing.
- `src/nyxloom/reconcile.py:879` -- `days_since_gate_verify: float | None
  = None`, a live field on `ReconcileInput`.

The only code that reads `policy.gate_verify_interval_days` and branches
on `inp.days_since_gate_verify` today is `tests/legacy_planner.py:2123-2149`,
which is test-only -- `grep -rn "legacy_planner" src/` returns no import
from the package itself (only a packaging listing in an `egg-info/SOURCES.txt`
artifact).

This is a real (if latent) code-hygiene issue: a config field that
silently no-ops. Found during the retroactive adversarial review of
nyxloom-P102 (F2), which flagged that a docs banner calling GA4 "likewise
gone" was misleading given this live, settable, schema-blessed knob
survives.

## Why nyxloom owns it

Both fields and the schema declaration are nyxloom's own; the frozen
snapshot test that currently requires them (`tests/legacy_planner.py`) is
also nyxloom's own.

## Proposed contract

Two options, not mutually exclusive, deliberately left open for whoever
carves this to choose with a fresh look at `tests/legacy_planner.py`'s
actual purpose:

1. **Retire the fields entirely** -- remove `gate_verify_interval_days`
   from `config.py` + `nyxloom-config.schema.json`, remove
   `days_since_gate_verify` from `reconcile.py`, and update or retire
   whatever `tests/legacy_planner.py`'s frozen snapshot check was actually
   protecting (it may itself be a candidate for retirement if its whole
   purpose was pinning GA1/GA4-era behavior).
2. **Add an explicit deprecation warning** at config-load time when a
   project sets `gate_verify_interval_days` to a nonzero value, so the
   silent no-op becomes a loud one, without touching the frozen test.

## Oracles

- `grep -rn "gate_verify_interval_days\|days_since_gate_verify"
  src/nyxloom/` returns zero hits (option 1), OR a project setting
  `gate_verify_interval_days` nonzero produces an observable warning at
  load time (option 2) -- pick one, but the current silent-no-op state
  must not survive unchanged.
- Whichever option lands, `tests/legacy_planner.py`'s own frozen-snapshot
  purpose is re-examined and either preserved correctly or retired with
  its own stated reasoning -- not silently broken.

## SPEC ownership

`src/nyxloom/config.py`, `src/nyxloom/reconcile.py`,
`src/nyxloom/schemas/nyxloom-config.schema.json`, `tests/legacy_planner.py`.

## Provenance

Found during the retroactive adversarial review of nyxloom-P102, 2026-09-08
(F2) -- pre-existing since nyxloom-P98's deliberate keep-for-now decision
(2026-09-03), not a new defect.
