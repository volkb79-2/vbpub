# Changelog

## [Unreleased]

<!-- cmru: release history -->

## [0.1.0] - 2026-11-24
<!-- cmru: generated -->
<!-- cmru: source-end=8a438b7f854f509b8f5b23765c36807d90581380 -->

### Added
- feat(test-policy): estate_test_policy pytest plugin and flakehunt stress-repeat runner (AWP-10 slice 1, code and tests) (ae5a43c1d)

### Fixed
- fix(ciu): pin CGROUP_PARENT_DEV_BACKGROUND per test; governance tests no longer read the runner env (CIU-193, FL-13) (2934a07a9)
- fix(test-policy): clean error for a non-integer FLAKEHUNT_PIDS_LIMIT, strict ledger parser, drop stale hash line (80eabd97c)
- fix(test-policy): register release contract, host-owned flakehunt limits, stable formats, deterministic own tests (9c5159a99)

### Changed
- test-policy: publish the estate_test_policy wheel (explicit --glob) and guard the release contract (6cbe27256)
- test-policy: put the package source on PYTHONPATH for subprocess pytester runs (46d3d07cd)
- test-policy: harden child cleanup and containment checks (96d48ee30)
- test-policy: add containment marker and P11 child guard (fd9c21840)
- chore(ciu): renumber CIU-193 -> CIU-194 (id collision with ciu-191 branch) (2e2ca15b1)

### Documentation
- docs(test-policy): TP-13 design review round 1 fixes (e53330021)
- docs(test-policy): TP-13 containment marker design; backlog TP-12/TP-13, amend TP-10(a)/TP-01 (e3eb75e30)
- docs(test-policy): refresh FL-03 base after upstream merge (0c5fc8c72)
- docs(test-policy): track FL-03 integration status (bdfd43cec)
- docs(test-policy): FL-13 cites fix commit (f7deb335a)
- docs(test-policy): flake ledger FL-12 (ciu CONTAINER_UID gate flake, CIU-192) (4f22c9862)
- docs(test-policy): FL-03 root cause (launcher cleanup wait deadlock), fixed on fix-cancel-flake-20261105 (assay@871f614a4) (250a4d890)
- docs(test-policy): record the slow-cpu validation of FL-04 (pre-fix fails run 1, fixed 20/20 on v1001) (ec17ed85b)
- docs(test-policy): flake ledger with root causes, README, CHANGES [Unreleased], backlog TP-01..TP-07 (e841b1516)

<!-- cmru: pending release notes begin -->
- Release contract: the `push` step publishes `estate_test_policy-*.whl` (explicit `--glob`). The tag prefix
  `test-policy` differs from the distribution name, so cmru's prefix-derived default matched nothing and the first
  0.1.0 attempt failed at publish (cmru KI-201). `tests/test_release_contract.py` guards it.
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
<!-- cmru: pending release notes end -->
