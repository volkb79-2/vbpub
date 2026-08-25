---
schema_version: 1
id: ciu-P15-per-service-health-timeout
project: ciu
component: health
title: "Optional per-phase-service health_timeout override, so one slow service's deadline no longer masks a fast service's broken healthcheck"
tier: implement-2
input_revision: "370ea8141f7f69399a751f2d5731a8ccf5419921"
source: {kind: backlog, ref: "docs/BACKLOG-2026-08-24.md#CIU-QOL-8"}
stack: none
depends_on: [P14]
session: fresh
scope:
  touch:
    - "src/ciu/deploy_pkg/phases.py"
    - "src/ciu/deploy_pkg/health.py"
    - "src/ciu/deploy.py"
    - "tests/tests/test_ciu_deploy_health.py"
    - "tests/tests/test_ciu_deploy_phases.py"
    - "tests/tests/test_ciu_deploy_actions.py"
    - "tests/tests/test_ciu_deploy_branch103.py"
    - "tests/tests/test_ciu_deploy_deeper6.py"
    - "tests/tests/test_ciu_deploy_deeper9.py"
    - "tests/tests/test_ciu_deploy_direct72.py"
    - "tests/tests/test_ciu_deploy_health_boundaries.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/reports/ciu-P15-per-service-health-timeout-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/cli.py"
    - "src/ciu/config_model.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-declaration
    observable: "A phase service entry MAY set an optional health_timeout = \"300s\" key (string, duration-parseable by the existing _seconds() helper in deploy.py — reuse it, do not reimplement duration parsing). New phases.service_health_timeout(service: dict) -> str | None mirrors service_shipped/service_health_enabled's exact validation pattern (absent -> None; non-str present -> tagged [S7.2] ValueError naming the bad type); it does NOT itself call _seconds (that stays the caller's job, matching how health_cfg.get('timeout') is resolved today)."
    negative: "a numeric-only field (breaks the '300s'/'5s' duration-string convention every other timeout in this codebase uses); silently coercing a bad type instead of raising [S7.2]"
    gate: "tester-unified"
  - id: O2-per-target-poll
    observable: "New health.wait_for_gate_per_target(check_fn, target_timeouts: Mapping[str, float], *, interval_s=5.0, sleep_fn=time.sleep, clock=time.monotonic) -> tuple[bool, dict] in deploy_pkg/health.py: each target gets its OWN deadline (clock() at call time + its own timeout_s); a target is 'resolved' (removed from further polling) the instant it reaches a _READY_STATUSES-equivalent state OR its own deadline passes (whichever first) — a broken FAST service (health_timeout='5s') is locked into its final (non-ready) status and the loop does not keep polling it once its own deadline passes, while a legitimately slow service (health_timeout='240s') keeps being polled up to its own deadline even after the fast one has already resolved. The gate's final passed/summary is computed by calling the EXISTING evaluate_gate(...) once, over the final per-target statuses dict, after every target is resolved (either ready or timed out) -- do not reimplement evaluate_gate's bucketing logic. wait_for_gate (the original, singular-timeout function) is UNCHANGED, byte-for-byte, and keeps every existing caller (deploy.py's run_health_gate adapter at ~line 1223) working exactly as before -- this package adds a sibling, it does not modify or replace the existing primitive."
    negative: "computing one shared deadline (= max of all target timeouts) and polling until that single deadline -- this reproduces today's exact bug: a broken fast service still waits behind the slow ceiling before failing; modifying wait_for_gate's existing signature/behavior in a way that could change ANY existing caller's timing"
    gate: "tester-unified"
  - id: O3-wiring
    observable: "resolve_selection_health_containers gains a default_timeout_s: float keyword parameter and returns dict[str, float] (container_name -> resolved timeout_s) instead of list[str]: each phase entry's containers get service_health_timeout(entry['service']) resolved via _seconds(override, default=default_timeout_s) if declared, else default_timeout_s unchanged. run_container_health_gate's signature changes from (container_names: list[str], *, timeout_s: float, interval_s=5.0) to (container_timeouts: dict[str, float], *, interval_s=5.0) and internally calls health_pkg.wait_for_gate_per_target (O2) instead of wait_for_gate. Both call sites in deploy.py (action_deploy's per-phase health gate, ~line 1408 area, and action_healthcheck, ~line 1622 area) are updated to pass the resolved dict through instead of a bare name list + separate timeout_s. A selection where NO entry declares health_timeout produces IDENTICAL final pass/fail/summary results to before this package (every target shares one deadline, same as today) -- this is the regression bar, not just 'still compiles'."
    negative: "changing the two call sites' behavior for the common case (no per-service override) in any observable way; a container list that silently drops entries when the dict-keyed refactor introduces a bug (verify count parity old-list vs new-dict-keys in a test)"
    gate: "tester-unified"
  - id: O4-docs
    observable: "docs/SPEC.md S7.7 (health gate) documents the optional health_timeout key, its fallback to [deploy.health].timeout, and the per-target-deadline semantics (a broken override-tagged service can now fail before the global timeout elapses). docs/CONFIG.md gets the proposed worked example from the backlog item (Authentik ~240s / workers ~5s) under the phase service entry section. CHANGES.md Unreleased entry. docs/BACKLOG-2026-08-24.md CIU-QOL-8 row -> FIXED with evidence."
    negative: "documenting only the config key without the actual per-target-deadline behavior change (a reader would reasonably assume it just changes A number, not the polling semantics)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "_seconds cannot be imported into deploy_pkg/health.py without creating a circular import (deploy.py -> deploy_pkg.health already exists one direction; verify deploy_pkg/health.py does not already get imported BY deploy.py's _seconds definition point, or by phases.py, before assuming it's safe) -- BLOCKED naming the exact cycle; the packet's fallback is to pass ALREADY-RESOLVED float seconds into wait_for_gate_per_target (its target_timeouts type is already Mapping[str, float], not raw strings) so this function itself never needs _seconds at all -- prefer that if there's any doubt"
  - "an existing test in test_ciu_deploy_health.py or test_ciu_deploy_actions.py asserts wait_for_gate's or run_container_health_gate's EXACT current signature in a way your additive change would break -- BLOCKED naming the failing assertion, do not weaken it"
mutexes: [merge-lane]
review_focus:
  - "the false-PASS attack: a selection with one fast broken service (health_timeout=5s, never becomes healthy) and one slow legitimate service (health_timeout=240s, becomes healthy at t=200s) -- gate must still FAIL overall (broken service never healthy) but must resolve the broken one's bucket at t=5s, not block reporting until t=240s; write a fixture proving the wall-clock claim via injected clock/sleep_fn, never a real sleep (AUTHORING.md 3b.A)"
  - "no existing caller of wait_for_gate (deploy.py ~1223) or run_container_health_gate had its signature silently changed underneath it"
  - "resolve_selection_health_containers's return-type change (list -> dict) is propagated to EVERY caller, not just the two named in O3 -- grep for all call sites before declaring done"
---

# ciu-P15 — per-service health-timeout override (CIU-QOL-8)

## Amendment (controller, after a first BLOCKED attempt)

O1+O2 landed clean (`7a1ca22b`). O3's mandated interface change
(`resolve_selection_health_containers` returning `dict[str, float]` instead of
`list[str]`; `run_container_health_gate` taking `container_timeouts: dict`
instead of `container_names: list` + `timeout_s`) correctly broke 19
pre-existing tests across 6 files that pinned the old signatures — the
implementer correctly reverted rather than ship a red gate, per BLOCKED
discipline, and reported two paths. **Controller decision: take the
mechanical path.** `resolve_selection_health_containers`/
`run_container_health_gate` are internal `deploy.py` helpers, not part of
ciu's public CLI/config contract (no consumer calls them directly; the
public surface — CLI flags, config keys, JSON envelopes — is unaffected by
this change). Updating their 6 dependent test files' fakes/assertions to
match the new interface is an ordinary internal refactor, not a breaking
change worth avoiding with a compatibility shim (this codebase's own
convention, e.g. ciu-P12's deletion of a dead compat alias rather than
keeping one, is to change the code rather than grow a shim). `scope.touch`
above has been widened to include the 6 named test files. Redo O3 against
the ALREADY-LANDED O1/O2 primitives; do not re-implement or revert them.

## Context to read first
1. `docs/BACKLOG-2026-08-24.md#CIU-QOL-8` (already in your context via
   `source`) — read its literal proposed TOML shape, then read the actual
   motivating problem below (item 2), which is more precise than the
   backlog's one-paragraph framing.
2. `src/ciu/deploy_pkg/health.py` in full — `classify` (~26), `evaluate_gate`
   (~60), `wait_for_gate` (~111-129, the SINGLE-shared-deadline primitive you
   are NOT changing), `_READY_STATUSES` frozenset (defined near `wait_healthy`,
   ~line 138). The actual bug this package fixes: today, ALL containers in one
   `run_container_health_gate` call share ONE `timeout_s`. If Authentik needs
   240s and a worker needs 5s, either the global timeout is 240s (so a BROKEN
   worker's healthcheck failure isn't reported until 240s have elapsed — slow
   failure diagnosis, not "wasted CPU time") or 5s (so Authentik's gate
   spuriously fails while it's still legitimately starting). Neither is
   correct; the fix is per-container deadlines within one poll loop, not a
   configurable scalar.
3. `src/ciu/deploy.py` — `run_container_health_gate` (~1203-1224) and
   `resolve_selection_health_containers` (~1226-1300+, read to its end) in
   full, plus BOTH call sites: inside `action_deploy` (~1408, where
   `health_cfg`/`timeout_s` are computed once per deploy, then presumably
   passed per-phase — find the exact call) and `action_healthcheck` (~1622).
   Also read `_seconds` (~119) — the existing duration-string parser
   (`"300s"` -> `300.0`) every timeout in this codebase already uses; reuse
   it, do not write a second one.
4. `src/ciu/deploy_pkg/phases.py` — `service_shipped` (~94) and
   `service_health_enabled` (~113): copy their EXACT validation pattern
   (default via `.get(key, default)`, `isinstance` check, tagged `[S7.2]`
   `ValueError` on a bad type) for the new `service_health_timeout` accessor.
5. `tests/tests/test_ciu_deploy_health.py` and any existing test file
   covering `run_container_health_gate`/`resolve_selection_health_containers`
   (grep `tests/tests/` for both names) — mirror their fixture style
   (injected `sleep_fn`/`clock`, per AUTHORING.md §3b.A: no real sleeps, no
   wall-clock-dependent assertions).

## Implementation packet (normative)

### Owned interfaces
- `deploy_pkg/phases.py`: `service_health_timeout(service: dict) -> str | None`
  — `raw = service.get("health_timeout"); None if raw is None; raw if
  isinstance(raw, str); else raise ValueError("[S7.2] service 'health_timeout'
  must be a string duration (e.g. '300s'); got {type}...")`.
- `deploy_pkg/health.py`: `wait_for_gate_per_target(check_fn: Callable[[],
  dict[str, str]], target_timeouts: Mapping[str, float], *, interval_s: float
  = 5.0, sleep_fn=time.sleep, clock=time.monotonic) -> tuple[bool, dict]`. See
  Construction below. `wait_for_gate` itself: UNTOUCHED.
- `deploy.py`: `resolve_selection_health_containers(repo_root, profile,
  selection, *, default_timeout_s: float) -> dict[str, float]` (was `->
  list[str]`, no `default_timeout_s` param). `run_container_health_gate
  (container_timeouts: dict[str, float], *, interval_s: float = 5.0) ->
  tuple[bool, dict]` (was `(container_names: list[str], *, timeout_s: float,
  interval_s=5.0)`).

### Construction and state flow
1. In `resolve_selection_health_containers`, for each selected phase entry,
   after resolving its concrete container names (existing logic, unchanged):
   `raw_override = phases_pkg.service_health_timeout(entry["service"])`;
   `entry_timeout_s = _seconds(raw_override) if raw_override is not None else
   default_timeout_s`; assign `entry_timeout_s` to every container name
   resolved from THIS entry in the returned dict.
2. `run_container_health_gate` builds `check_fn` exactly as today but iterates
   `container_timeouts` (a dict) instead of `container_names` (a list) — same
   body, `for cname in container_timeouts: statuses[cname] = ...`. Calls
   `health_pkg.wait_for_gate_per_target(check_fn, container_timeouts,
   interval_s=interval_s)`.
3. `wait_for_gate_per_target`: compute `deadlines = {name: clock() + t for
   name, t in target_timeouts.items()}`. Loop: call `statuses =
   check_fn()` (ALL targets, every tick — do not try to skip already-resolved
   ones in the check_fn call itself, simplicity over a marginal optimization);
   for each name not yet in a `resolved: dict[str, str]` map, if
   `statuses[name]` is in `_READY_STATUSES` (import from the `wait_healthy`
   section of this same file) OR `clock() >= deadlines[name]`, set
   `resolved[name] = statuses[name]`. When `len(resolved) ==
   len(target_timeouts)`, stop; else `sleep_fn(interval_s)` and repeat. Return
   `evaluate_gate(resolved)` (reuse verbatim — do not hand-roll bucketing).
4. Both `deploy.py` call sites: replace their `timeout_s = _seconds(...)` +
   `container_names = resolve_selection_health_containers(...)` +
   `run_container_health_gate(container_names, timeout_s=timeout_s)` triplet
   with `default_timeout_s = _seconds(...)` (same as today) +
   `container_timeouts = resolve_selection_health_containers(repo_root,
   profile, selection_for_this_call, default_timeout_s=default_timeout_s)` +
   `run_container_health_gate(container_timeouts)`.

### Decision table
| state | outcome | side effect |
|---|---|---|
| no entry declares `health_timeout` | every container shares `default_timeout_s`; gate behavior IDENTICAL to pre-package (regression bar) | none |
| one entry declares `health_timeout = "5s"`, becomes healthy at t=2s | resolved early at t=2s | none |
| one entry declares `health_timeout = "5s"`, never healthy | resolved (as pending/unhealthy) at t=5s, does NOT block the overall loop past t=5s waiting on it | none |
| another entry with default 240s, healthy at t=200s | resolved at t=200s independent of the 5s entry's earlier resolution | none |
| `health_timeout` present but not a string | `[S7.2]` ValueError from `service_health_timeout`, raised before any polling starts | none |

### Degrees of freedom
Exact variable names, whether `wait_for_gate_per_target` lives above or below
`wait_for_gate` in the file, and how you structure the loop's inner helper (if
any) are yours. NOT a degree of freedom: `wait_for_gate`'s existing signature
and behavior (zero changes), and `evaluate_gate` reuse for final bucketing
(no parallel bucketing logic).

## Work
1. `service_health_timeout` in `phases.py` (O1).
2. `wait_for_gate_per_target` in `health.py` (O2).
3. Wire `resolve_selection_health_containers` + `run_container_health_gate` +
   both `deploy.py` call sites (O3).
4. Tests: the combined-axis fixture from `review_focus` (fast-broken +
   slow-legitimate in one gate call, injected clock/sleep_fn, asserting the
   fast one's bucket resolves at its own deadline via clock progression, not
   wall time) is REQUIRED, not optional — it is the one test that actually
   proves this package fixes anything.
5. Docs per O4.
6. LOG at `nyxloom-trove/reports/ciu-P15-per-service-health-timeout-LOG.md`.

## Environment setup
Same worktree as P12/P14:
`cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu && .venv/bin/python run-ciu-tests.py`
(venv already exists from P14 if it ran first). Iteration signal only.

## BLOCKED rule
Per `escalate_if` above. Forbidden workaround: computing a single
`max(target_timeouts.values())` deadline and calling that "per-target" — it
is not; it reproduces the exact bug this package exists to fix.
