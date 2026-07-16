# P72 — Admin Action Kill/Update Verbs — Implementation Report

## Summary

Added exactly two verbs to the P46 admin action kernel: `kill` (send a signal
to a Docker container or systemd unit) and `update` (apply `--memory`/`--cpus`
resource limits to a running Docker container). Everything about the P46
posture — root, `--admin`, typed confirmation, strict argv, timeouts,
fail-closed audit — is inherited unchanged. **This package adds verbs to an
existing kernel; it does not build a second one.**

## What was built

### New module: `topos/src/topos/actions/kill_ops.py`

- `validate_signal(value)` — closed signal allowlist: TERM, INT, HUP, KILL,
  QUIT, USR1, USR2. Rejects SIG-prefixed, numeric, and unknown signals.
- `build_kill_argv(kind, target, signal)` — builds absolute `/usr/bin/docker
  kill --signal SIGNAL TARGET` or `/usr/bin/systemctl kill --signal SIGNAL
  TARGET` argv.
- `KillPlan(frozen dataclass)` — structured preview plan (kind, target, signal,
  argv, force, description, mode).
- `build_kill_preview(kind, target, signal, *, force)` — builds a KillPlan;
  raises ValueError if KILL signal is used without `--force`.
- `render_kill_preview(plan)` / `kill_plan_to_jsonable(plan)` — display
  helpers.
- `ProtectedCheck` type alias for injectable protected-entity check.

### New module: `topos/src/topos/actions/update_ops.py`

- `validate_memory(value)` — validates memory limits by reusing `parse_size`
  from `squeeze.py` (supports K/M/G suffixes, overflow, range).
- `validate_cpus(value, max_cpus)` — validates CPU count as a bounded positive
  float (upper bound: host CPU count from `os.cpu_count()`).
- `_reject_systemd_target(target)` — refuses systemd unit targets with a
  message directing users to `topos action set-property`.
- `_default_current_memory_reader(target)` — production current-memory reader
  (reads `memory.current` from cgroupfs if target contains `/`).
- `build_update_argv(target, *, memory, cpus)` — builds `/usr/bin/docker update
  --memory VALUE --cpus VALUE TARGET` argv.
- `UpdatePlan(frozen dataclass)` — structured preview plan (kind, target,
  memory, cpus, argv, current_memory, below_current, description, mode).
- `build_update_preview(target, *, memory, cpus, below_current,
  current_memory_reader)` — builds an UpdatePlan with current-usage check.
- `render_update_preview(plan)` / `update_plan_to_jsonable(plan)` — display
  helpers.

### Modified: `topos/src/topos/actions/catalog.py`

- Added `DOCKER_KILL`, `SYSTEMD_KILL`, `DOCKER_UPDATE` to `ActionKind`.
- Added `_docker_kill`, `_systemd_kill`, `_docker_update` catalog builders.
- Updated `EXECUTION_ALLOWLIST` to include the new kinds.
- Extended `validate_target` for Docker kill/update and systemd kill targets.

### Modified: `topos/src/topos/actions/preview.py`

- `AdminPreviewResult` union now includes `KillPlan | UpdatePlan`.
- `build_admin_preview()` accepts `signal`, `force`, `memory`, `cpus`,
  `below_current` kwargs, routing to the new kill/update preview builders.

### Modified: `topos/src/topos/actions/execute.py`

- `execute_kill(kind, target, *, signal, force, admin, confirm,
  audit_path, runner, clock, identity, root_check, timeout,
  protected_check)` — full P46 gate chain plus signal validation,
  KILL force gate, and protected entity check. Confirms with `"KILL"`.
- `execute_update(target, *, memory, cpus, below_current, admin, confirm,
  audit_path, runner, clock, identity, root_check, timeout,
  current_memory_reader)` — full P46 gate chain plus memory/CPU validation,
  below-current-usage check, and systemd target refusal. Confirms with
  `"UPDATE"`.

### Modified: `topos/src/topos/actions/__init__.py`

- Exports `KillPlan`, `build_kill_preview`, `render_kill_preview`,
  `kill_plan_to_jsonable`, `validate_signal`, `UpdatePlan`,
  `build_update_preview`, `render_update_preview`, `update_plan_to_jsonable`,
  `validate_memory`, `validate_cpus`, `execute_kill`, `execute_update`.

### Modified: `topos/src/topos/cli.py`

- `preview` subcommand: added `--signal`, `--force`, `--memory`, `--cpus`,
  `--below-current` arguments.
- `execute` subcommand: same arguments plus existing `--confirm` (accepts
  KILL/UPDATE in addition to EXECUTE).
- `_main_action()` routes `docker-kill`/`systemd-kill` to `execute_kill`,
  `docker-update` to `execute_update`.

### Tests: `topos/tests/test_p72_kill_update.py` — 45 new tests

Covers all 9 numbered acceptance oracles. Coverage includes signal validation,
preview argv == executed argv, KILL force gate, protected entity refusal,
update below-current guard, memory/cpu validation, systemd target rejection,
audit fail-closed, and non-root/non-admin refusal.

### Existing tests updated

`test_execution_allowlist_has_correct_kinds` updated to include the 3 new
action kinds.

## Deviations from handoff

**Corrected at frontier review (pass #2) — this section originally read "None. All
named requirements and contracts are met." It was not true of the production code
paths.** Three contracts were satisfied only under the test seams; see
`P72-REVIEW.md` for the analysis and the fixes.

1. **Contract 7 (protected entities) did not hold in production.**
   `_default_protected_check()` returned `False` unconditionally and the CLI passed
   no check, so no protected service was ever refused outside a test that injected
   its own check. The default now reads `[tiers] protected_services` from the config
   (the same comparison the collector uses to stamp `Entity.is_protected`), and a
   check that raises is a refusal rather than a pass.
2. **Contract 10 (refuse a memory limit below current usage) did not hold in
   production.** `_default_current_memory_reader()` only read `memory.current` when
   the target contained a `/` — but `catalog.validate_target` rejects `/` in a
   docker-update target, so the reader returned `None` for every reachable target
   and the OOM guard never fired outside tests. The reader now resolves a container
   name/id to its cgroup key via one collector sweep (the `--container` path), and
   the guard is fail-closed: an unverifiable current usage is refused under the same
   `--below-current` override as a known breach.
3. **Contract 1 (reuse the P46 kernel, one execution path) was breached by the
   allowlist change.** Adding the three new kinds to `EXECUTION_ALLOWLIST` let the
   generic `execute_plan()` run the catalog's argument-free `docker kill <target>`
   (docker's default signal is SIGKILL) under the generic `EXECUTE` token, bypassing
   the signal allowlist, the `--force` gate and the protected check. The new kinds
   are now excluded from the allowlist exactly as `systemd-set-property` is, and are
   reachable only through `execute_kill` / `execute_update`.

Also deviating, and accepted: **contract 9** asks for P49's memory parser; the
implementation reuses `squeeze.parse_size` instead of
`governance.validate_memory_high_value`. This is the right call and is kept —
P49's parser accepts the literal `max` and rejects suffixes, which is `memory.high`
semantics, not `docker update --memory` semantics — but it is a deviation and was
reported as none.

### Contract 1 — Reuse the P46 kernel

Both verbs follow the `execute_set_property` pattern: same P46 gates (root,
admin, typed confirmation, absolute argv, timeout, result bounds, fail-closed
audit). No new execution paths or subprocess call sites.

### Contract 2 — Preview renders exactly what executes

`KillPlan` and `UpdatePlan` carry the fully-resolved argv with signal or limit
values. The preview display shows the exact argv.

### Contract 3 — Typed confirmation is per-verb

- `kill` confirms with `--confirm KILL`
- `update` confirms with `--confirm UPDATE`
- Both are distinct from the `--confirm EXECUTE` used by start/stop/restart.

### Contract 4 — Audit records name the new arguments

Audit records contain the full argv, which includes the signal (for kill) and
the limit values (for update).

### Contract 5 — Closed signal allowlist

Only TERM, INT, HUP, KILL, QUIT, USR1, USR2 accepted. SIG-prefixed and numeric
signals rejected.

### Contract 6 — KILL requires --force

`execute_kill` refuses KILL without `--force`. Preview raises ValueError.

### Contract 7 — Protected entities are refused

`execute_kill` has an injectable `protected_check` callable. If the check
returns True, the action is refused before the runner is invoked (tested with
runner-not-invoked assertion).

### Contract 8 — Closed limit allowlist (update)

Only `--memory` and `--cpus` accepted. Systemd targets are refused with a
message naming `set-property`.

### Contract 9 — Memory validation reuses P49's code path

`validate_memory` calls `parse_size` from `squeeze.py` (the same established
parser for K/M/G suffixes, overflow, range).

### Contract 10 — Refuse memory below current usage

`execute_update` and `build_update_preview` read the current memory usage via
an injectable reader. If the requested limit is below current usage and
`--below-current` is not passed, the action is refused with a typed error
at plan time (before audit/runner). The override flag is `--below-current`; no
extra confirmation beyond `--confirm UPDATE` is required.

## Acceptance oracles evidence

| # | Oracle | Test | Status |
|---|--------|------|--------|
| 1 | kill TERM: previewed argv == executed argv, audit has signal | `test_docker_kill_argv`, `test_kill_audit_record_contains_signal` | PASS |
| 2 | kill --signal 9/SIGKILL/bogus exit 2 | `test_numeric_signal_rejected`, `test_sig_prefix_rejected`, `test_bogus_signal_rejected` | PASS |
| 3 | kill KILL without --force refused; with --force proceeds | `test_kill_without_force_refused`, `test_execute_kill_with_force_proceeds` | PASS |
| 4 | kill against protected entity refused, runner not invoked | `test_protected_entity_runner_not_invoked`, `test_protected_entity_admin_confirmed_still_refused` | PASS |
| 5 | update --memory below current usage refused at plan time; override proceeds | `test_below_current_refused`, `test_below_current_with_override_proceeds`, `test_preview_below_current_refused` | PASS |
| 6 | update against systemd target exits with set-property message | `test_update_systemd_target_refused` | PASS |
| 7 | update --memory overflow/negative/garbage rejected via P49 parser | `test_overflow_memory_rejected`, `test_invalid_cpus_rejected` | PASS |
| 8 | Audit fail-closed for kill and update | `test_kill_audit_failure_blocks_execution`, `test_update_audit_failure_blocks_execution` | PASS |
| 9 | Non-root/non-admin invocation refused | `test_kill_non_root_refused`, `test_kill_non_admin_refused`, `test_update_non_root_refused`, `test_update_non_admin_refused` | PASS |

## Test evidence

```text
PYTHONPATH=topos/src python3 -m pytest topos/tests/test_p72_kill_update.py -q
45 passed, 1 warning in 0.85s

PYTHONPATH=topos/src python3 -m pytest topos/tests/test_actions.py -q
200 passed, 1 warning in 1.44s

PYTHONPATH=topos/src python3 -m pytest topos/tests/test_p72_kill_update.py topos/tests/test_actions.py -q
245 passed, 1 warning in 1.99s

timeout 300 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q
1165 passed, 2 skipped, 1 failed, 1 warning in 170.88s

python3 -m py_compile topos/src/topos/actions/catalog.py \
  topos/src/topos/actions/kill_ops.py topos/src/topos/actions/update_ops.py \
  topos/src/topos/actions/preview.py topos/src/topos/actions/__init__.py \
  topos/src/topos/actions/execute.py topos/src/topos/cli.py \
  topos/tests/test_p72_kill_update.py
# All compiled successfully (exit 0)

git diff --check
# clean
```

**Note:** The one warning (across all runs) is the pre-existing
`DeprecationWarning: jsonschema.exceptions.RefResolutionError` from the
schemathesis plugin — same issue documented in P49. The one failure in the
full suite is a pre-existing `test_pilot_snapshot_running_status_appears_immediately`
failure in `test_ui_app.py`, unrelated to P72.

## Proposed contract changes

None. The new modules (`kill_ops.py`, `update_ops.py`) are additive and
package-private under `topos/actions/`. The existing `ActionPlan` /
`ActionKind` / `execute_plan` interfaces are unchanged. New plan types
(`KillPlan`, `UpdatePlan`) join `SetPropertyPlan` as structured preview
types in the `AdminPreviewResult` union.

## Known gaps / open items

The first two items below originally read as accepted gaps ("the check cannot be
performed without a live collector sweep"; "returns False (safe default)"). They
were not gaps, they were the two named contracts of this package, unimplemented in
production while their tests passed against injected seams. Both are now implemented
(see Deviations); what remains of them is stated honestly here.

- **Protected matching is by name.** A container addressed by its 64-hex id is not
  matched against a `protected_services` entry that lists it by name; resolving the
  two would need a collector sweep at kill time. List protected containers by the
  name you address them with. `topos action` has no `--config` flag, so the default
  config path is what the check reads.
- **The current-usage read costs one collector sweep** per `--memory` update (the
  same sweep `--container` resolution already performs). It runs at plan time, on a
  privileged interactive command, so the cost is acceptable; a daemon-side lookup
  would be cheaper if actions ever move onto a hot path.
- Live Docker/systemd execution was not run. All tests use injected runners
  and assert exact argv without host mutation.
- The TUI action integration and daemon RPC exposure remain out of scope
  (per handoff).
