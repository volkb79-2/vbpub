# estate-test-policy and flakehunt

Two tools for AGENTS.md "Tests never depend on host load or speed" (AWP-10, memo section 6). Standard library plus
pytest only. Release contract: `cmru.toml` (tag `test-policy-vX.Y.Z`, wheel to GitHub Releases), gate lanes in
`run-gate.toml` (`r0-r1`, `r2`, `gate`), judge in `assay.toml`; no wheel exists until a first release is cut
(backlog TP-01). Until then use the source tree: `PYTHONPATH=src` and `-p estate_test_policy.plugin`.

* `estate_test_policy`: a pytest plugin. Per test it fails (or warns) on a fixed `time.sleep`/`asyncio.sleep` of
  0.2 s or more outside a poll loop, on literal timeouts below 120 s (`timeout=`, `wait/join/settimeout(N)`, and
  `timeout N` inside strings such as scenario commands), on assertions over elapsed wall time, and on leaked
  threads, file descriptors and child processes (before/after snapshot, all reported). Allowlist:
  `@pytest.mark.estate_allow("fixed-sleep", reason="...", backlog="XX-NN")`; a missing reason or backlog id is
  itself a failure. Rules: `fixed-sleep`, `short-timeout`, `wall-time-assert`, `resource-leak`.
  Load with `-p estate_test_policy.plugin` or install the wheel (entry point `pytest11`).
  `--estate-policy=off|warn|fail` (default fail); ini `estate_policy_sleep_threshold`, `estate_policy_settle_seconds`.
  Rollout warning: the default is `fail`, so installing the wheel into an image enforces the policy on every test
  run in it, and a static violation is a fixture ERROR at setup (the test body does not run, so it also gives no
  signal whether the test is otherwise red). Roll out per project with `--estate-policy=warn` (or
  `-p no:estate_test_policy`) until the project is clean (TP-10).
* `flakehunt`: repeats a pytest selection under a stress recipe until N failures or M passes.
  `flakehunt --recipe slow-cpu --image IMG -v SRC:/work --memory 2g --cgroup-parent SLICE --pids-limit N --run-id ID -- tests/x.py -k name`.
  Recipes: `none`, `slow-cpu` (lane `--cpus 0.25`), `cpu-contended` (lane shares 10 vs an exact-named `stress-ng`
  sibling at 1000, both pinned to cpuset 0), `slow-disk` (`--device-write-bps/--device-read-iops`, needs
  `--io-device`), `mem-pressure` (lane `--memory`, needs it from the observed peak, plus a `stress-ng --vm` sibling),
  `combo` (slow-cpu plus cpu-contended: `--cpus 0.25`, shares 10, cpuset 0, and the cpu stressor on cpuset 0; plus the disk
  limits and a `--memory` sibling, so it needs `--io-device` and `--memory`). The stimulus values (0.25 cores, 6 MB/s, 300 IOPS, 4 workers at 90%) are the
  recipe definitions and have flags.
  Host-owned limits are NOT defaults: `--cgroup-parent` (or `$FLAKEHUNT_CGROUP_PARENT`) and `--pids-limit` (or
  `$FLAKEHUNT_PIDS_LIMIT`) are required in container mode and a missing one is refused, so a stale slice name or limit
  never starts a container silently. Containers are exact-named `flakehunt-<run-id>-<n>-{lane,cpu,mem}`, `--init`,
  never privileged, removed by exact name in a `finally`.
  Start gate: refuses to start when memory PSI `some avg10` or IO PSI `full avg10` is above `--psi-max` (25). CPU PSI
  is not read: it is a stall measure while the CPUs are saturated on purpose (operator directive 2026-11-04b).
  `--dry-run` prints the plan and starts nothing. `--mode inner` runs inside a lane container (no docker; stdlib CPU
  spinners; cgroup recipes are refused because only the outer container can apply them).
  Exit: 0 stable, 1 failed, 2 infrastructure error, 3 inconclusive. `--report FILE` writes the JSON report
  (stable format with `schema_version`, `docs/FORMATS.md`). A one-run `failed` is evidence of a failing test, not of
  flakiness (pytest exit 1 is `fail` whatever the cause); compare with `--recipe none`.
  Image needs `stress-ng` only for `cpu-contended`, `mem-pressure` and `combo`.

## Limits

* Plugin, static analysis: only the test function's own source is analysed, not helpers it calls (the leak check is
  the backstop). Quiet false negatives, backlog TP-09: aliased clock imports
  (`from time import perf_counter as pc`), `datetime.now()` deltas, sleeps in helpers, randomness.
* Order and shared-state dependence is NOT detected (backlog TP-07): a test that passes only in file order is stable
  when the file runs whole and fails with exit 1 when run alone, which looks like an ordinary failure.
* flakehunt validation: only `slow-cpu` in container mode has run against real docker (v1001). `cpu-contended`
  container mode, `slow-disk`, `mem-pressure` and `combo` are covered by the fake runner and the dry-run plan only
  (TP-11); treat them as unvalidated until then. `mem-pressure` runs the stressor as a separate container, so it
  creates slice-level pressure, not pressure inside the lane's own cgroup. The `combo` and `cpu-contended` pinning
  to cpuset 0 needs cpu 0 to be inside the parent slice's allowed cpuset.
* The PSI gate reads memory and IO pressure only; heavy swapping and OOM kills are the operator's stop signals and
  are not read by the tool.
* The CLI is plain argparse (cli-extended adoption: TP-08).

Formats (JSON report, flake ledger): `docs/FORMATS.md`. Known failures and their causes: `docs/FLAKE-LEDGER.md`.
Tests: `PYTHONPATH=src python -m pytest tests --cov --cov-branch`.
