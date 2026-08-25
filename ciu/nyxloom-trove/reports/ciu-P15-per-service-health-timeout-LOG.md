# LOG — ciu-P15-per-service-health-timeout

- Package: `ciu-P15-per-service-health-timeout` (CIU-QOL-8)
- Worktree: `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu`
- Branch: `feat/ciu-qol-v8prep-wave`
- Handoff `input_revision`: `370ea8141f7f69399a751f2d5731a8ccf5419921`
- Status: **COMPLETE — O1-O4 all done.** (Superseded the PARTIAL/BLOCKED
  status below: the controller reviewed the BLOCKED evidence, widened
  `scope.touch` to include the 6 dependent test files (handoff commit
  `a255a639`, "Amendment" section), and directed the mechanical path —
  update those 6 files' fakes/assertions to the new interface rather than
  add a compatibility shim. Done; see "Amendment — O3 redone and landed,
  O4 completed" below for the full second-pass account. The narrative
  below this point (up through the first "Gate output" section) is the
  ORIGINAL first-attempt record, kept verbatim for the audit trail — do
  not read it as the current state of the code.**

## Summary of the block (read this first)

The handoff's `escalate_if` names a specific trap: "an existing test in
`test_ciu_deploy_health.py` or `test_ciu_deploy_actions.py` asserts
`wait_for_gate`'s or `run_container_health_gate`'s EXACT current signature
in a way your additive change would break — BLOCKED naming the failing
assertion, do not weaken it." `test_ciu_deploy_health.py` did not exist yet
(I created it fresh for O2's tests, see below). I found the trap fires, but
across a WIDER set of files than the two named — `resolve_selection_health_containers`'s
mandated return-type change (`list[str]` -> `dict[str, float]`, O3's own
observable text) and `run_container_health_gate`'s mandated signature
narrowing (dropping `timeout_s` for a `container_timeouts` dict, O3's own
observable text) are both **breaking, non-additive** changes to two
functions that FIVE existing test files outside this package's
`scope.touch` call directly or monkeypatch with the current signature
pinned:

- `tests/tests/test_ciu_deploy_actions.py`
- `tests/tests/test_ciu_deploy_branch103.py`
- `tests/tests/test_ciu_deploy_deeper6.py`
- `tests/tests/test_ciu_deploy_deeper9.py`
- `tests/tests/test_ciu_deploy_direct72.py`
- `tests/tests/test_ciu_deploy_health_boundaries.py`

(six files, not the two the `escalate_if` bullet named — I grepped the
whole `tests/tests/` tree for both function names before concluding this,
per `review_focus` item 3's own instruction to "grep for all call sites
before declaring done.")

I implemented O3 exactly as specified (see "What I actually built and then
reverted" below) to get concrete, non-hypothetical failure evidence rather
than escalate on a hunch, ran the full suite, and got **19 failing tests**,
all `TypeError`s or hard value-shape mismatches, none fixable without
editing a file outside `scope.touch`. Full list and exact error text below.
Per the BLOCKED rule ("do not weaken [an out-of-scope test's] assertion"
and "a file outside `scope.touch` is needed... STOP"), I reverted every
`src/ciu/deploy.py` change (verified via `git diff` showing zero remaining
delta on that file) and kept only the two independently-additive,
independently-tested pieces: O1 (`phases.py`) and O2 (`health.py`).

## What I actually built and then reverted (O3 — full detail, for whoever
resolves this)

In `src/ciu/deploy.py`:

- `resolve_selection_health_containers(repo_root, profile, selection, *,
  default_timeout_s: float) -> dict[str, float]` — gained a **required**
  keyword-only `default_timeout_s` param (the handoff's own interface line
  shows no default value), and its dedup logic (`seen: set[str]` guarding
  first-insertion-wins) changed from `resolved.append(cname)` to
  `resolved[cname] = entry_timeout_s`, changing the return type from
  `list[str]` to `dict[str, float]`, per O3's observable text verbatim.
- `run_container_health_gate(container_timeouts: dict[str, float], *,
  interval_s: float = 5.0) -> tuple[bool, dict]` — signature narrowed from
  `(container_names: list[str], *, timeout_s: float, interval_s=5.0)`,
  dropping `timeout_s` entirely and iterating the dict's keys inside
  `check_fn`, calling `health_pkg.wait_for_gate_per_target` instead of
  `health_pkg.wait_for_gate`. Exactly the O3 interface line.
- `run_health_gate` (the small service-suffix adapter at ~line 1184, an
  EXISTING caller of `run_container_health_gate` not named in O3's "both
  call sites" list — this is the "not just the two named in O3" caller
  `review_focus` item 3 was pointing at) updated internally to build
  `{cname: timeout_s for cname in names.values()}` and call
  `run_container_health_gate(container_timeouts, interval_s=interval_s)`,
  while its OWN external signature (`config, service_names, *, timeout_s,
  interval_s=5.0`) stayed byte-for-byte unchanged — confirmed this still
  satisfies `test_spec_contracts.py`'s and
  `test_ciu_deploy_deeper10.py`'s existing direct calls to `run_health_gate`
  (both pin only `run_health_gate`'s own signature, never
  `run_container_health_gate`'s).
- Both named call sites (`action_deploy` ~1487, `action_healthcheck`
  ~1616) updated per the handoff's Construction step 4: `default_timeout_s
  = _seconds(...)` computed once, passed into
  `resolve_selection_health_containers(..., default_timeout_s=...)`, and
  `run_container_health_gate(container_timeouts)` called with no
  `timeout_s=` kwarg.

**One packet-internal discrepancy I resolved and want to flag explicitly**
(moot now that O3 is reverted, but load-bearing for whoever re-attempts
this): the O3 oracle's observable text says the per-entry timeout should be
resolved via `_seconds(override, default=default_timeout_s)`, but the
"Construction and state flow" section's literal code line says
`_seconds(raw_override)` (no `default=` kwarg, meaning an unparseable
override string would silently fall back to `_seconds`'s own hardcoded
`30.0` default instead of the caller's actual configured default). I
followed the **oracle text** (`default=default_timeout_s`), since it's more
semantically correct (an unparseable override should fall back to the
profile's real default, not a hardcoded 30s) and is the line actually used
for grading. Flagging the mismatch since I can't be sure which the author
intended as authoritative.

### Confirmed via `python -c "import ciu.deploy"` and `import ciu.deploy_pkg.health`

No circular import: `deploy_pkg/health.py` never imports `_seconds` (it
doesn't need it — `wait_for_gate_per_target`'s `target_timeouts` is already
`Mapping[str, float]`, resolved seconds, never a raw duration string, per
the handoff's own design). The `escalate_if` circular-import trap did NOT
fire; `deploy.py -> deploy_pkg.health` (one direction, pre-existing) stayed
the only edge.

### Exact failure evidence (ran `.venv/bin/python -m pytest tests -q` with
the O3 wiring above applied)

```
19 failed, 2416 passed, 20 warnings in 22.61s
```

Full list of the 19, plus the exact `TypeError`/assertion each one hits:

**Category 1 — `resolve_selection_health_containers` called directly with
its OLD 3-positional-arg convention; the new required keyword-only
`default_timeout_s` is missing** (`TypeError: resolve_selection_health_containers()
missing 1 required keyword-only argument: 'default_timeout_s'`) — 11 tests:

- `test_ciu_deploy_actions.py::test_health_targets_come_from_all_compose_services_not_phase_display_name`
  (line 162; also asserts `targets == ["p-t-postgres", "p-t-minio"]` — a
  bare list-equality on the return value, which would ALSO fail even with a
  default value supplied, since the return type is now a dict)
- `test_ciu_deploy_actions.py::test_health_targets_honor_entry_and_host_compose_profiles`
  (line 209, same list-equality shape)
- `test_ciu_deploy_actions.py::test_health_target_resolution_fails_for_ambiguous_compose_identity`
  (line 237)
- `test_ciu_deploy_branch103.py::test_health_resolution_deduplicates_container_names`
  (line 136, `== ["ciu-test-api"]`)
- `test_ciu_deploy_deeper6.py::test_health_target_resolution_requires_rendered_compose_model`
  (line 56)
- `test_ciu_deploy_deeper6.py::test_health_target_resolution_rejects_invalid_profile_declaration`
  (line 68)
- `test_ciu_deploy_deeper6.py::test_health_target_resolution_rejects_stack_with_no_active_services`
  (line 80)
- `test_ciu_deploy_deeper6.py::test_health_target_resolution_rejects_unreadable_yaml_model`
  (line 89)
- `test_ciu_deploy_health_boundaries.py::test_malformed_compose_health_model_is_authoring_error`
  (line 126, all 3 parametrizations)

**Category 2 — `run_container_health_gate` called directly with its OLD
`timeout_s=` keyword** (`TypeError: run_container_health_gate() got an
unexpected keyword argument 'timeout_s'`) — 4 tests:

- `test_ciu_deploy_deeper9.py::test_health_gate_fails_closed_for_failed_or_malformed_docker_inspect`
  (line 31)
- `test_ciu_deploy_health_boundaries.py::test_health_gate_inspects_exact_resolved_container_names`
  (line 53)
- `test_ciu_deploy_health_boundaries.py::test_health_gate_pending_then_healthy_uses_deterministic_polling`
  (line 83)
- `test_ciu_deploy_health_boundaries.py::test_health_gate_timeout_preserves_pending_summary`
  (line 96)

**Category 3 — a `monkeypatch.setattr` fake replacing
`resolve_selection_health_containers` (or `run_container_health_gate`) with
a lambda/def that only accepts the OLD positional convention; the new call
site now passes `default_timeout_s=` as a keyword, which the fake doesn't
accept** (`TypeError: <fake>() got an unexpected keyword argument
'default_timeout_s'`) — 4 tests:

- `test_ciu_deploy_direct72.py::test_healthcheck_failed_gate_reports_summary_and_returns_one`
  (line 30, `lambda *_args: ["project-prod-api"]`)
- `test_ciu_deploy_branch103.py::test_health_failure_reclassifies_started_stack_as_failed`
  (line 180, `lambda *_args: ["ciu-test-api"]`)
- `test_ciu_deploy_actions.py::test_deploy_health_failure_stops_later_phase_after_reporting_summary`
  (line 509, `def fake_targets(_root, _profile, entries):`)
- `test_ciu_deploy_actions.py::test_deploy_ignore_errors_continues_after_health_failure_but_returns_1`
  (line 551, same shape)

11 + 4 + 4 = 19, matching the full failure count exactly — no unaccounted
failures.

Representative pasted tracebacks (one per category, real output, not
paraphrased):

```
tests/tests/test_ciu_deploy_deeper6.py:56: in test_health_target_resolution_requires_rendered_compose_model
    deploy.resolve_selection_health_containers(tmp_path, profile, _selection(profile))
E   TypeError: resolve_selection_health_containers() missing 1 required keyword-only argument: 'default_timeout_s'

tests/tests/test_ciu_deploy_deeper9.py:31: in test_health_gate_fails_closed_for_failed_or_malformed_docker_inspect
    passed, summary = deploy.run_container_health_gate(
        ["project-prod-gone", "project-prod-proxy-error"], timeout_s=0, interval_s=0
    )
E   TypeError: run_container_health_gate() got an unexpected keyword argument 'timeout_s'

tests/tests/test_ciu_deploy_direct72.py:38: in test_healthcheck_failed_gate_reports_summary_and_returns_one
    assert deploy.action_healthcheck(tmp_path, Profile(), [{"path": "apps/api"}]) == 1
src/ciu/deploy.py:1638: in action_healthcheck
    container_timeouts = resolve_selection_health_containers(
        repo_root, profile, selection, default_timeout_s=default_timeout_s,
    )
E   TypeError: test_healthcheck_failed_gate_reports_summary_and_returns_one.<locals>.<lambda>() got an unexpected keyword argument 'default_timeout_s'
```

None of these are fixable by touching only `scope.touch` files — the fix
in every case is to edit an assertion or a fake's signature in one of the
six files listed above, all outside `scope.touch`, or to redesign O3's
interface to be additive (e.g. giving `run_container_health_gate` a
`timeout_s: float | None = None` legacy path, or having
`resolve_selection_health_containers` accept the new keyword as optional
AND keep returning something list-like) — neither of which the handoff's
"Degrees of freedom" section authorizes me to invent; it explicitly pins
both signatures as replacements and explicitly forbids "a `max()` of all
timeouts" as the ONE named workaround, without carving out room for a
dual-mode/back-compat signature either.

**Recommended resolution paths** (not something I'm authorized to choose
between — leaving this for whoever picks the package back up):

1. Widen `scope.touch` to include the six files above, update their pinned
   assertions/fakes to the new dict-based interfaces (mechanical: swap
   `== [...]` for `== {...: <expected_default_timeout>, ...}`, add
   `default_timeout_s=...` to monkeypatched fakes' signatures, drop
   `timeout_s=` from direct `run_container_health_gate(...)` calls). This
   is what I'd expect to be the actual next step — the breakage is
   mechanical and shallow, not a sign the O3 design is wrong.
2. Alternatively, redesign O3 to be strictly additive (e.g. a NEW function
   name for the dict-based resolver/gate, leaving the old
   `list`-returning/`timeout_s`-accepting ones in place for existing
   callers, with the two named production call sites switched to the new
   ones). This avoids touching any test file but contradicts the handoff's
   explicit interface pins and its "Degrees of freedom" section, so I did
   not do this without new authorization.

## O1 — `service_health_timeout` (DONE)

Added to `src/ciu/deploy_pkg/phases.py`, immediately after
`service_health_enabled` (mirrors its exact validation pattern, per the
handoff's Context item 4):

```python
def service_health_timeout(service: dict) -> str | None:
    raw = service.get("health_timeout")
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    raise ValueError(
        f"[S7.2] service 'health_timeout' must be a string duration (e.g. '300s'); "
        f"got {type(raw).__name__} {raw!r}."
    )
```

Does NOT call `_seconds` itself (duration parsing stays the caller's job,
per O1's observable text) — confirmed by grep, `_seconds` never appears in
`phases.py`.

New test file `tests/tests/test_ciu_deploy_phases.py` (this package's
`scope.touch` names it; it did not exist before — created fresh, mirroring
`test_ciu_deploy_pkg.py`'s `TestServiceHealthEnabled` class shape):

- absent -> `None`
- a real duration string (`"300s"`, `"5s"`) -> returned verbatim, unparsed
- explicit `None` value (as opposed to the key being absent) -> also
  `None` (same `service.get("health_timeout")` default-less lookup path)
- every non-str type (`int`, `float`, `bool` x2, `list`, `dict`) -> raises
  `[S7.2]` `ValueError` naming the type
- the error message names the actual bad type and value (`got int 300`)

10 tests, all passing. 100% line+branch coverage of the new function
confirmed in isolation:
`.venv/bin/python -m pytest tests/tests/test_ciu_deploy_phases.py --cov=ciu.deploy_pkg.phases --cov-report=term-missing --cov-branch -q`
— the new function's lines (140-148) do not appear in that run's "Missing"
column.

## O2 — `wait_for_gate_per_target` (DONE)

Added to `src/ciu/deploy_pkg/health.py`, between `wait_for_gate` and the
`wait_healthy`/`_READY_STATUSES` section (referencing `_READY_STATUSES`,
which is defined later in the same module — safe, since Python resolves
module-global names at call time, not def time; confirmed by
`python -c "import ciu.deploy_pkg.health"` succeeding and by the new tests
actually passing):

```python
def wait_for_gate_per_target(
    check_fn: Callable[[], dict[str, str]],
    target_timeouts: Mapping[str, float],
    *,
    interval_s: float = 5.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, dict]:
    deadlines = {name: clock() + timeout_s for name, timeout_s in target_timeouts.items()}
    resolved: dict[str, str] = {}
    while len(resolved) < len(target_timeouts):
        statuses = check_fn()
        for name in target_timeouts:
            if name in resolved:
                continue
            if statuses[name] in _READY_STATUSES or clock() >= deadlines[name]:
                resolved[name] = statuses[name]
        if len(resolved) < len(target_timeouts):
            sleep_fn(interval_s)
    return evaluate_gate(resolved)
```

`wait_for_gate` itself: confirmed byte-for-byte unchanged (`git diff` shows
zero delta inside `wait_for_gate`'s own body; the only diff in the file is
the new function inserted after it, plus the `Mapping` import).

New test file `tests/tests/test_ciu_deploy_health.py` (did not exist
before — this package's `scope.touch` names it), all fixtures use an
injected fake clock/sleep_fn (a mutable "sim time" list that only advances
inside `sleep_fn`, never real `time.sleep`/`time.monotonic`), per
AUTHORING.md 3b.A:

- `test_no_overrides_matches_wait_for_gate_for_a_uniform_selection` — every
  target sharing one timeout resolves identically to the old primitive.
- `test_empty_target_timeouts_passes_without_polling` — zero targets:
  `check_fn` never called, gate passes immediately, no sleep.
- `test_single_target_resolves_early_when_healthy_before_its_deadline` —
  healthy on the very first poll; resolved without ever sleeping.
- `test_single_target_never_healthy_locks_in_at_its_own_deadline` — never
  healthy, resolved (as pending) exactly at its own deadline (5 ticks of
  1.0s each for a 5.0s timeout).
- **`test_false_pass_attack_fast_broken_does_not_wait_behind_slow_legitimate`
  — THE required combined-axis fixture from `review_focus` item 1.** One
  target `fast-broken` (`5.0s`, never healthy) and one `slow-legit`
  (`240.0s`, becomes healthy at simulated t=200s) in the SAME call.
  Asserts: overall `passed is False` (the broken one never became
  healthy); `5.0 in check_calls` (a poll happened exactly at the fast
  target's own deadline); `240.0 not in check_calls` (the loop never ran
  anywhere near the slow ceiling); `max(check_calls) == 200.0` (the loop
  correctly ran on to the slow target's real, independent resolution
  instant); `len(slept) == 40` (exactly `200.0s / 5.0s-interval`, proving
  the wall-clock claim is driven by clock progression, not an inferred
  wall-clock duration — the test itself runs in well under a second of
  real time while "simulating" 200 seconds).
- `test_check_fn_polls_every_target_every_tick_even_after_one_resolves` —
  confirms `check_fn` is asked to classify BOTH targets on every tick, per
  the handoff's explicit "do not try to skip already-resolved ones in the
  `check_fn` call itself" instruction — the already-resolved target's
  returned status is simply ignored, not requested-around.
- `TestWaitForGatePerTargetAgainstWaitForGate::test_uniform_timeouts_produce_the_same_result_as_wait_for_gate`
  — runs the OLD `wait_for_gate` and the NEW `wait_for_gate_per_target`
  side by side against equivalent check_fn/clock fixtures with a uniform
  timeout, asserts identical `(passed, summary)` — this is the O2-level
  analogue of O3's "regression bar" (I could not exercise the actual O3
  regression bar end-to-end since O3 itself is blocked, but this proves
  the underlying per-target primitive degrades to the exact old behavior
  when every target shares one timeout).

7 tests, all passing. 100% line+branch coverage of the new function
confirmed in isolation (its line range 137-178 does not appear in that
run's "Missing" column for `--cov=ciu.deploy_pkg.health`).

## O3 — BLOCKED (see "Summary of the block" and "What I actually built and
then reverted" above)

`src/ciu/deploy.py` is, as of this LOG, **byte-for-byte identical to before
this package started** (`git diff src/ciu/deploy.py` produces zero output)
— I reverted the O3 wiring with `git checkout -- src/ciu/deploy.py` after
gathering the failure evidence above, rather than leave the tree in a
red-test state.

## O4 — deferred (depends on O3)

Not touched: `docs/SPEC.md`, `docs/CONFIG.md`, `CHANGES.md`, `docs/BACKLOG-2026-08-24.md`.
`CIU-QOL-8`'s row in the backlog remains `**Status:** OPEN` — accurate,
since the actual per-target-deadline wiring is not shipped; the `phases.py`/
`health.py` primitives added by O1/O2 have no caller yet, so a `health_timeout`
key set by an operator today would have **zero effect** (silently ignored,
since `resolve_selection_health_containers` never calls
`service_health_timeout`). Documenting the config key now, before O3 ships,
would be exactly the negative constraint O4 itself warns against ("a reader
would reasonably assume it just changes A number") — worse, it would
document a key that does nothing at all yet. Left for whoever ships O3.

## Gate output (real, pasted verbatim — final state, O1+O2 only, O3
reverted)

```
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/__init__.py                                  3      0      0      0   100%
src/ciu/__main__.py                                  3      0      2      0   100%
src/ciu/_version.py                                 11      0      0      0   100%
src/ciu/activate.py                                119      0     46      0   100%
src/ciu/cli.py                                     670      0    242      0   100%
src/ciu/cli_utils.py                                11      0      0      0   100%
src/ciu/composefile.py                             388      0    180      0   100%
src/ciu/config_constants.py                         29      0      4      0   100%
src/ciu/config_model.py                            276      0    128      0   100%
src/ciu/deploy.py                                 1324      0    562      0   100%
src/ciu/deploy_pkg/__init__.py                       8      0      0      0   100%
src/ciu/deploy_pkg/health.py                       205      0    108      0   100%
src/ciu/deploy_pkg/http_util.py                     24      0      2      0   100%
src/ciu/deploy_pkg/layouts.py                       63      0     24      0   100%
src/ciu/deploy_pkg/phases.py                        76      0     44      0   100%
src/ciu/deploy_pkg/profiles.py                     131      0     64      0   100%
src/ciu/deploy_pkg/registry.py                      38      0     20      0   100%
src/ciu/dev.py                                     196      0     74      0   100%
src/ciu/diagnose.py                                 79      0     34      0   100%
src/ciu/engine.py                                  887      0    292      0   100%
src/ciu/governance.py                              382      0    158      0   100%
src/ciu/hooks/__init__.py                            0      0      0      0   100%
src/ciu/hooks/examples/__init__.py                   0      0      0      0   100%
src/ciu/hooks/examples/post_compose_example.py       5      0      0      0   100%
src/ciu/hooks/examples/pre_compose_example.py        4      0      0      0   100%
src/ciu/hooks_runner.py                            123      0     52      0   100%
src/ciu/hosts.py                                    61      0     28      0   100%
src/ciu/ksm.py                                     180      0     64      0   100%
src/ciu/output.py                                   89      0     34      0   100%
src/ciu/paths.py                                    30      0     12      0   100%
src/ciu/procutil.py                                 17      0      2      0   100%
src/ciu/provisioning.py                            256      0    120      0   100%
src/ciu/scaffold.py                                104      0     36      0   100%
src/ciu/secrets/__init__.py                          3      0      0      0   100%
src/ciu/secrets/directives.py                      140      0     78      0   100%
src/ciu/secrets/materialize.py                     229      0     64      0   100%
src/ciu/secrets/providers.py                       111      0     38      0   100%
src/ciu/transport_ssh.py                           219      0     70      0   100%
src/ciu/warn_policy.py                              32      0     14      0   100%
src/ciu/workspace_env.py                           454      0    190      0   100%
src/ciu/worktree.py                               1115      0    432      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             8095      0   3218      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2452 passed in 17.89s =============================
```

2452 passed (2435 pre-existing + 17 new: 10 in `test_ciu_deploy_phases.py`
+ 7 in `test_ciu_deploy_health.py`), 0 failed, 100.00% line+branch coverage
across the whole `ciu` package, `src/ciu/deploy.py` unchanged at 1324
statements (confirms the revert).

## Oracle table

| Oracle | Status | Satisfied by |
|---|---|---|
| O1-declaration | DONE | `src/ciu/deploy_pkg/phases.py`'s new `service_health_timeout`; 10 tests in new `tests/tests/test_ciu_deploy_phases.py`; 100% coverage of the new lines. |
| O2-per-target-poll | DONE | `src/ciu/deploy_pkg/health.py`'s new `wait_for_gate_per_target`; `wait_for_gate` itself untouched; 7 tests in new `tests/tests/test_ciu_deploy_health.py` including the required combined-axis fixture; 100% coverage of the new lines. |
| O3-wiring | **BLOCKED** | Implemented exactly per the packet, ran the full suite, got 19 failing tests across 6 files outside `scope.touch` (full list and exact errors above). Reverted `src/ciu/deploy.py` to its pre-package state. |
| O4-docs | **Deferred** (depends on O3) | Not touched — documenting `health_timeout` before O3 ships would describe a config key with zero actual effect. |

## Files changed

- `src/ciu/deploy_pkg/phases.py` — new `service_health_timeout` function.
- `src/ciu/deploy_pkg/health.py` — new `wait_for_gate_per_target` function
  (`wait_for_gate` unchanged); `Mapping` added to the `typing` import.
- `tests/tests/test_ciu_deploy_phases.py` — new file, 10 tests (O1).
- `tests/tests/test_ciu_deploy_health.py` — new file, 7 tests (O2).
- `nyxloom-trove/reports/ciu-P15-per-service-health-timeout-LOG.md` (this
  file).

Not touched (all `scope.touch` entries, deliberately left alone): `src/ciu/deploy.py`
(reverted after evidence-gathering), `docs/SPEC.md`, `docs/CONFIG.md`,
`CHANGES.md`, `docs/BACKLOG-2026-08-24.md`.

No `scope.forbid` file was touched (confirmed by `git status` — only the
five files above are modified/new).

## Commit hash(es)

Read back with `git log -1 --format=%H` immediately after each commit (not
predicted):

- Code/tests commit (O1 + O2): `7a1ca22bd71c4c949a9953d2ee78ada1dffb867e`
  — `src/ciu/deploy_pkg/health.py`, `src/ciu/deploy_pkg/phases.py`,
  `tests/tests/test_ciu_deploy_health.py` (new),
  `tests/tests/test_ciu_deploy_phases.py` (new).
- LOG commit (first version): `422de38b099bc48750d8d11a3ddd5df9775d1ce9`.
- See "Amendment" section below for the O3/O4 commits that supersede the
  BLOCKED status recorded above.

---

## Amendment — O3 redone and landed, O4 completed

Controller reviewed the BLOCKED evidence above and widened `scope.touch`
(handoff commit `a255a639`, "Amendment" section) to include the 6
dependent test files, with an explicit ruling: `resolve_selection_health_containers`/
`run_container_health_gate` are internal `deploy.py` helpers, not part of
ciu's public CLI/config contract, so updating their 6 dependent test
files' fakes/assertions to the new `dict`-based interface is an ordinary
internal refactor — take the mechanical path, not a compatibility shim.

### O3 — redone (DONE)

Re-applied the exact same `src/ciu/deploy.py` wiring described in "What I
actually built and then reverted" above, verbatim (confirmed via `git
diff` against my in-memory record of the first attempt — same functions,
same signatures, same `_seconds(raw_override, default=default_timeout_s)`
resolution I'd already flagged as the correct reading of the two
discrepant packet sections). Did **not** re-implement or revert O1/O2 —
built directly on the already-landed `7a1ca22b`.

### Six dependent test files — fixed to genuinely exercise the new
interface (not just silenced)

For each, I converted assertions/fakes to the new shape rather than
papering over the type error:

- **`tests/tests/test_ciu_deploy_deeper6.py`** (4 tests) — all four calls
  only assert an exception is raised (never inspect the return value), so
  the only change needed was adding `default_timeout_s=30.0` to each
  direct call.
- **`tests/tests/test_ciu_deploy_health_boundaries.py`** (4 failing
  tests, 11 total in file):
  - `test_health_gate_inspects_exact_resolved_container_names` and
    `test_health_gate_timeout_preserves_pending_summary`: `["name"],
    timeout_s=N` → `{"name": N}` (dict), dropping `timeout_s=`.
  - `test_malformed_compose_health_model_is_authoring_error` (3
    parametrizations): added `default_timeout_s=30.0`.
  - `test_health_gate_pending_then_healthy_uses_deterministic_polling`:
    this one needed a REAL rewrite, not a mechanical signature patch — it
    monkeypatched `deploy.health_pkg.wait_for_gate` (the OLD primitive)
    with a real-`wait_for_gate`-backed fake using an injected clock/sleep,
    to prove `run_container_health_gate` correctly delegates polling.
    Since `run_container_health_gate` now calls `wait_for_gate_per_target`
    instead, patching `wait_for_gate` no longer intercepts anything — the
    test would have silently stopped testing what it claimed to test
    (using real `time.sleep`/`time.monotonic` instead, and only passing by
    coincidence). Rewrote it to monkeypatch `deploy.health_pkg.
    wait_for_gate_per_target` instead, using the REAL
    `health.wait_for_gate_per_target` wrapped around the same injected
    fake clock/`slept.append` sleep_fn, called with a `{"project-prod-cache":
    5}` timeout dict. Traced the fake clock/status sequence by hand before
    running it (starting → healthy on the 2nd check_fn call, one sleep)
    and confirmed the test's `assert slept == [1]` still holds for the
    right reason — the poll loop's actual, injected clock progression —
    not an accident of unpatched real timing.
- **`tests/tests/test_ciu_deploy_deeper9.py`** (1 test) — `["a","b"],
  timeout_s=0` → `{"a": 0, "b": 0}`.
- **`tests/tests/test_ciu_deploy_direct72.py`** (1 failing test) — the
  `resolve_selection_health_containers` fake `lambda *_args: [...]` →
  `lambda *_args, **_kwargs: {"project-prod-api": 30.0}` (absorbs the new
  `default_timeout_s=` keyword, returns a dict). Left
  `test_healthcheck_empty_selection_is_successful_noop`'s fake untouched —
  it's never invoked (`action_healthcheck` returns before calling
  `resolve_selection_health_containers` for an empty selection), and it
  wasn't in the original 19 failures.
- **`tests/tests/test_ciu_deploy_branch103.py`** (2 failing tests, 11
  total in file):
  - `test_health_resolution_deduplicates_container_names`: `==
    ["ciu-test-api"]` → `== {"ciu-test-api": 30.0}` (both source entries
    dedupe to the same container name; the FIRST entry's resolved timeout
    — here, `default_timeout_s` for both, since neither declares an
    override — wins, matching the pre-existing dedup semantics of "first
    insertion wins").
  - `test_health_failure_reclassifies_started_stack_as_failed`: fake
    `lambda *_args: [...]` → `lambda *_args, **_kwargs: {"ciu-test-api":
    30.0}`.
- **`tests/tests/test_ciu_deploy_actions.py`** (5 failing tests, 72 total
  in file):
  - `test_health_targets_come_from_all_compose_services_not_phase_display_name`
    and `test_health_targets_honor_entry_and_host_compose_profiles`: list
    equality → dict equality (`{"p-t-postgres": 30.0, "p-t-minio": 30.0}`,
    `{"p-t-always": 30.0, "p-t-debug": 30.0, "p-t-metrics": 30.0}`); the
    surviving `assert all("Database Core" not in target for target in
    targets)` line needed NO change — iterating a dict already yields its
    keys, so this line means exactly the same thing before and after the
    return-type change.
  - `test_health_target_resolution_fails_for_ambiguous_compose_identity`:
    added `default_timeout_s=30.0` (exception-only assertion, no shape
    change needed otherwise).
  - `test_deploy_health_failure_stops_later_phase_after_reporting_summary`
    and `test_deploy_ignore_errors_continues_after_health_failure_but_returns_1`:
    both `fake_targets(_root, _profile, entries)` → `fake_targets(_root,
    _profile, entries, *, default_timeout_s)`, returning `{name:
    default_timeout_s for name in names}` instead of `list(names)`
    (keeping the recorded `events` tuple of names unchanged — the test's
    externally-observed sequence assertions did not need to change at
    all); both `fake_gate(names, **_kwargs)` → `fake_gate(container_timeouts,
    **_kwargs)`, deriving `names = tuple(container_timeouts)` (dict keys,
    same content as before) since `names[0]` on a dict would have raised
    `KeyError: 0`.

### Regression bar re-confirmed (controller's item 3)

`grep -rn "health_timeout" tests/tests/test_ciu_deploy_actions.py
tests/tests/test_ciu_deploy_branch103.py tests/tests/test_ciu_deploy_deeper6.py
tests/tests/test_ciu_deploy_deeper9.py tests/tests/test_ciu_deploy_direct72.py
tests/tests/test_ciu_deploy_health_boundaries.py` — **zero matches**. None
of these six files' fixtures declare a `health_timeout` override anywhere,
so every one of their scenarios exercises exactly the "no entry declares
`health_timeout`" regression-bar row from O3's own decision table: every
container in each of these tests' selections shares the same
`default_timeout_s`, and each test's pre-existing pass/fail/summary
expectations (only the container-list-vs-dict *shape* changed in the
assertions above, never the expected health outcome, exit code, or log
text) hold unchanged. This is the concrete, per-file confirmation the
controller asked for, not an inference from the isolated O2-level
`TestWaitForGatePerTargetAgainstWaitForGate` unit test alone (which
remains true too, and covers the primitive-level case).

### O4 — docs (DONE)

- **`docs/SPEC.md`** S7.7: added a paragraph immediately after the
  existing timeout/healthcheck sentence describing the optional
  `health_timeout` key, its fallback to `[deploy.health].timeout`, and —
  per O4's own negative constraint — the actual per-target-deadline
  polling semantics (a broken override-tagged service can fail before the
  global timeout elapses, independent of any other target in the same
  gate call), not merely "a number changes." Split the bullet into two
  paragraphs (blank line + 2-space indent continuation) matching the
  existing multi-paragraph-bullet convention already used by S8.3 in the
  same file.
- **`docs/CONFIG.md`**: added a paragraph after the existing `health =
  false` paragraph in the phase-service-entry section, with the exact
  worked example from the backlog item (Authentik `"240s"` / worker
  `"5s"`), and updated the `[deploy.health]` subsections-table row to
  point at it.
- **`CHANGES.md`**: added a new `### Added` subsection to the existing
  `[Unreleased]` block (ordered before the pre-existing `### Fixed`
  subsection from ciu-P14, matching this file's own Added-before-Fixed
  convention visible in the `[7.0.0]` release section above it).
- **`docs/BACKLOG-2026-08-24.md`**: `CIU-QOL-8`'s `**Status:**` line `OPEN`
  → `✅ FIXED (ciu-P15)`, plus a new `**Evidence:**` line naming the
  shipped functions, the SPEC/CONFIG doc locations, and this LOG.

### Full gate — final state (real, pasted verbatim)

```
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/deploy.py                                 1327      0    562      0   100%
src/ciu/deploy_pkg/__init__.py                       8      0      0      0   100%
src/ciu/deploy_pkg/health.py                       205      0    108      0   100%
src/ciu/deploy_pkg/phases.py                        76      0     44      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             8098      0   3218      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
====================== 2452 passed, 6 warnings in 16.12s =======================
```

(Full 40-module coverage table omitted here for length — every module
reports 100%, identical in shape to the first-attempt table above except
`src/ciu/deploy.py` grew from 1324 to 1327 statements, the O3 wiring's net
new lines. Same 2452 total tests as the O1+O2-only run: the 6 dependent
files' tests were fixed in place, not added to.)

### Oracle table — final

| Oracle | Status | Satisfied by |
|---|---|---|
| O1-declaration | DONE | Unchanged from the first attempt — `service_health_timeout` in `phases.py`, 10 tests, 100% coverage. |
| O2-per-target-poll | DONE | Unchanged from the first attempt — `wait_for_gate_per_target` in `health.py`, 7 tests including the required combined-axis fixture, 100% coverage. |
| O3-wiring | **DONE** | `resolve_selection_health_containers`/`run_container_health_gate`/both `deploy.py` call sites wired exactly per the packet; 6 dependent test files updated to the new interface (see above); "no override" regression bar re-confirmed by grep across all 6 files. |
| O4-docs | **DONE** | `docs/SPEC.md` S7.7, `docs/CONFIG.md` phase-service-entry section + worked example, `CHANGES.md` Unreleased/Added, `docs/BACKLOG-2026-08-24.md` CIU-QOL-8 → FIXED with evidence. |

### Files changed (this amendment)

Wiring + test-fix commit:
- `src/ciu/deploy.py`
- `tests/tests/test_ciu_deploy_actions.py`
- `tests/tests/test_ciu_deploy_branch103.py`
- `tests/tests/test_ciu_deploy_deeper6.py`
- `tests/tests/test_ciu_deploy_deeper9.py`
- `tests/tests/test_ciu_deploy_direct72.py`
- `tests/tests/test_ciu_deploy_health_boundaries.py`

Docs commit:
- `docs/SPEC.md`
- `docs/CONFIG.md`
- `CHANGES.md`
- `docs/BACKLOG-2026-08-24.md`

No `scope.forbid` file was touched at any point in this package (both
attempts).

### Commit hashes (this amendment, read back via `git log -1 --format=%H`,
not predicted)

- Wiring + test-fix commit: `75a643c169d19ec774755164a685925e27ed6a1d`
- Docs commit: `797e403fbaaced530138de56cd0a6f42c3d70c85`
- LOG commit (this update): committed next, see repository history.
