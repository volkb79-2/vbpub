# Changelog

## [Unreleased]

- New library `libraries/test-policy`: the `estate_test_policy` pytest plugin (fixed sleeps, sub-120 s timeouts,
  wall-time assertions, thread/fd/child leaks; allowlist only through `estate_allow` with reason and backlog id)
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
