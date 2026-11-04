# estate-test-policy and flakehunt

Two tools for AGENTS.md "Tests never depend on host load or speed" (AWP-10, memo section 6). Standard library plus
pytest only.

* `estate_test_policy`: a pytest plugin. Per test it fails (or warns) on a fixed `time.sleep`/`asyncio.sleep` of
  0.2 s or more outside a poll loop, on literal timeouts below 120 s (`timeout=`, `wait/join/settimeout(N)`, and
  `timeout N` inside strings such as scenario commands), on assertions over elapsed wall time, and on leaked
  threads, file descriptors and child processes (before/after snapshot, all reported). Allowlist:
  `@pytest.mark.estate_allow("fixed-sleep", reason="...", backlog="XX-NN")`; a missing reason or backlog id is
  itself a failure. Rules: `fixed-sleep`, `short-timeout`, `wall-time-assert`, `resource-leak`.
  Load with `-p estate_test_policy.plugin` or install the wheel (entry point `pytest11`).
  `--estate-policy=off|warn|fail` (default fail); ini `estate_policy_sleep_threshold`, `estate_policy_settle_seconds`.
  Limits: only the test function's own source is analysed, not helpers it calls (the leak check is the backstop).
* `flakehunt`: repeats a pytest selection under a stress recipe until N failures or M passes.
  `flakehunt --recipe slow-cpu --image IMG -v SRC:/work --memory 2g --run-id ID -- tests/x.py -k name`.
  Recipes: `none`, `slow-cpu` (lane `--cpus 0.25`), `cpu-contended` (lane shares 10 vs an exact-named `stress-ng`
  sibling at 1000, same cpuset), `slow-disk` (`--device-write-bps/--device-read-iops`, needs `--io-device`),
  `mem-pressure` (lane `--memory`, needs it from the observed peak, plus a `stress-ng --vm` sibling), `combo`.
  Containers are exact-named `flakehunt-<run-id>-<n>-{lane,cpu,mem}`, `--init`, `--cgroup-parent dev-gates.slice`,
  never privileged, removed by exact name in a `finally`. A PSI gate (some avg10 <= 25) precedes the first start.
  `--dry-run` prints the plan and starts nothing. `--mode inner` runs inside a lane container (no docker; stdlib CPU
  spinners; cgroup recipes are refused because only the outer container can apply them).
  Exit: 0 stable, 1 failed, 2 infrastructure error, 3 inconclusive. `--report FILE` writes JSON.
  Image needs `stress-ng` only for `cpu-contended`, `mem-pressure` and `combo`.

Known failures and their causes: `docs/FLAKE-LEDGER.md`. Tests: `PYTHONPATH=src python -m pytest tests --cov`.
