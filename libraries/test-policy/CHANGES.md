# Changelog

## [Unreleased]

- TP-10(a): the pytest entry point now defaults to `off`; projects opt in through ini `estate_policy =
  off|warn|fail`, and an explicit `--estate-policy` wins over the ini value. Installing the wheel no longer
  enforces policy in an unopted-in project.
- TP-12: the plugin sets `PR_SET_CHILD_SUBREAPER` at session start in every pytest/xdist process and replaces the
  old snapshot-diff child check. New direct children and zombies are settled, pidfd-pinned, re-checked by parent
  and start time, killed through the pidfd and reaped with `waitid(P_PIDFD)`; a leak is still reported after
  bounded cleanup, and the scan/kill/reap loop catches grandchildren reparented when their parent dies.
  `estate_allow("children", ...)` is the explicit exception. `estate_allow("resource-leak")` now covers only
  threads and file descriptors; it no longer exempts children, which have the separate `children` rule.
- TP-13: `@pytest.mark.requires_contained_cgroup` checks finite effective cgroup-v2 memory/pids caps and a private
  PID namespace against injectable `/proc` and `/sys/fs/cgroup` roots. `fail` refuses at setup, `warn` warns and
  skips, and `off` is inert.
- New library `libraries/test-policy`: the `estate_test_policy` pytest plugin (fixed sleeps, sub-120 s timeouts,
  wall-time assertions, thread/fd leaks and the separate leftover-child guard; allowlist only through
  `estate_allow` with reason and backlog id)
  and the `flakehunt` stress-repeat runner (slow-cpu, cpu-contended, slow-disk, mem-pressure, combo; container
  and inner modes; dry run). Ledger of known flakes with root causes in `docs/FLAKE-LEDGER.md`.
- Registered with the estate release machinery: `cmru.toml`, `run-gate.toml` (`r0-r1`, `r2`, `gate`),
  `assay.toml`, and a `test-policy` entry (L0) in `cmru.orchestration.toml` (TP-01; no release cut yet).
- flakehunt: `--cgroup-parent` and `--pids-limit` (or `FLAKEHUNT_CGROUP_PARENT`/`FLAKEHUNT_PIDS_LIMIT`) are required in
  container mode with no default; the start gate reads memory PSI `some` and IO PSI `full` only (CPU PSI is not a stop
  signal); the `combo` lane is pinned to the stressor's cpuset and no longer repeats `--cpu-shares`; the JSON report
  carries `schema_version` 1.
- Stable formats documented in `docs/FORMATS.md`; the ledger grammar (new status `FIXED-ON-BRANCH`) is enforced by
  `tests/test_ledger.py`. Known limits (order dependence, aliased clocks, `datetime`, unvalidated recipes) are in the
  README and backlog TP-07, TP-09, TP-11.
- The library's own tests no longer depend on a 1 s thread or child still being alive or on a 5 s settle budget: the
  leaked resources are held by events and pipes the test releases.
