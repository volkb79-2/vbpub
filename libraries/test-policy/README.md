# estate-test-policy and flakehunt

Two tools for AGENTS.md "Tests never depend on host load or speed" (AWP-10, memo section 6). Standard library plus
pytest only. Release contract: `cmru.toml` (tag `test-policy-vX.Y.Z`, wheel to GitHub Releases), gate lanes in
`run-gate.toml` (`r0-r1`, `r2`, `gate`), judge in `assay.toml`; no wheel exists until a first release is cut
(backlog TP-01). Until then use the source tree: `PYTHONPATH=src` and `-p estate_test_policy.plugin`.

* `estate_test_policy`: a pytest plugin. Per test it fails (or warns) on a fixed `time.sleep`/`asyncio.sleep` of
  0.2 s or more outside a poll loop, on literal timeouts below 120 s (`timeout=`, `wait/join/settimeout(N)`, and
  `timeout N` inside strings such as scenario commands), on assertions over elapsed wall time, and on leaked
  threads or file descriptors. Allowlist: `@pytest.mark.estate_allow("fixed-sleep", reason="...", backlog=
  "XX-NN")`; a missing reason or backlog id is itself a failure. Rules: `fixed-sleep`, `short-timeout`,
  `wall-time-assert`, `resource-leak`, `children`.
  Load with `-p estate_test_policy.plugin` or install the wheel (entry point `pytest11`).
  The entry point defaults to `off`, so installing the wheel changes nothing. A project opts in in `pytest.ini`
  (or pyproject `[tool.pytest.ini_options]`):

  ```ini
  [pytest]
  estate_policy = fail
  required_plugins = estate-test-policy
  ```

  `estate_policy` accepts `off|warn|fail`; `--estate-policy=off|warn|fail` overrides it when given. In `off` the
  plugin registers its markers and is otherwise inert. Ini `estate_policy_sleep_threshold` and
  `estate_policy_settle_seconds` keep their defaults (0.2 and 5 seconds). A static violation or a refused
  containment marker is a fixture error at setup under `fail`, so its body does not run; `warn` reports a warning
  and continues, except that a refused containment marker is skipped (TP-10a).
* Leftover-child guard (TP-12): when policy is not `off`, every pytest process, including each xdist worker, first
  sets `PR_SET_CHILD_SUBREAPER` so orphaned descendants reparent to it. At each test teardown the plugin compares
  direct children by `(pid, starttime)`. A new child gets `estate_policy_settle_seconds` to exit; survivors and
  zombies are then pinned with a pidfd, re-checked for the same parent and start time, killed through the pidfd
  when alive, and reaped with bounded `waitid(P_PIDFD, WNOHANG)` polling. The scan/kill/reap sequence repeats so
  grandchildren reparented by killing their parent are found too. An identity that remains unreaped is reported
  once and excluded from later settle and cleanup passes; a reused pid with a different start time is still
  examined. The 10-pass bound is reported when new children remain after those passes. A zombie is reported as a
  zombie leak, a live child is reported after successful SIGKILL and reap, and a child still unreaped at the
  deadline is reported as unreaped: cleanup never hangs or hides the leak. `fail` reports a
  teardown error; `warn` warns and cleans up. A deliberate exception uses
  `@pytest.mark.estate_allow("children", reason="...", backlog="XX-NN")`. Non-Linux runs are a no-op and record
  one warning. Allowed tests leave live children alone but new direct-child zombies are reaped with
  `waitid(WNOHANG)` so they do not accumulate (TP-12). With procfs `hidepid=1`, reading another user's
  `/proc/<pid>/stat` can return `EACCES`; a child can also become unreadable if it changes credentials. The guard
  uses non-reaping `waitid(P_PID, pid, WEXITED|WNOHANG|WNOWAIT)` to distinguish a waitable child from a non-child:
  an unreadable child or an ownership-check error still fails loudly. See [proc(5)'s `hidepid` description](https://manpages.debian.org/trixie/manpages/proc.5.en.html).

  **Adopting the child guard:** a process created lazily by module or session code, or by a self-daemonizing tool
  (`gpg-agent`, SSH `ControlPersist`, git `gc.autoDetach` or automatic maintenance), is a new child of pytest and
  will be killed and reported when its owning test ends. Disable git auto-gc and maintenance in test repositories
  (`git config --local gc.auto 0`; `git config --local maintenance.auto false`), arrange an explicit owner that
  stops it before teardown, or mark the test with `estate_allow("children", ...)` when a persistent helper is
  genuinely part of the test contract.
* `@pytest.mark.requires_contained_cgroup` (TP-13) protects tests that signal, kill or flood processes. At setup
  the plugin requires a finite effective cgroup-v2 `memory.max` and `pids.max`, `/proc` without `hidepid` or
  `subset=pid`, and a private PID namespace: every other process must lie in the test's top-level tree below
  PID 1. It re-scans for a transient foreign pid during `estate_policy_settle_seconds`. Under `fail` a refusal is
  a setup error with all failed facts and the container remedy; under `warn` it emits an `EstatePolicyWarning`
  and skips with reason `estate-test-policy: requires_contained_cgroup refused: ...`; under `off` the marker is
  inert. `estate_allow` does not accept this rule. Formula and false passes/refusals:
  `docs/DESIGN-CONTAINMENT-MARKER.md`.
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

* Plugin, static analysis: only the test function's own source is analysed, not helpers it calls (the thread,
  fd and child checks are the backstop). Quiet false negatives, backlog TP-09: aliased clock imports
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
Tests run through the registered `r0-r1` lane, which uses tester-unified with 100% line and branch coverage.
