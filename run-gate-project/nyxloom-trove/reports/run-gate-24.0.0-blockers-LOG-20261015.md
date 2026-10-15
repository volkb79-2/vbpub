# Run-Gate 24.0.0 release blockers — work log (2026-10-15)

Branch `run-gate-rg93-git-metadata-20261013`, worktree
`/workspaces/vbpub/.worktrees/run-gate-rg93-git-metadata-20261013`.
Packet: `run-gate-24-release-blockers`. Reviews acted on (preserved unchanged):
`run-gate-24.0.0-review-claude-opus-20261015.md`,
`run-gate-24.0.0-review-sol-xhigh-20261015.md`.

## Sequence

| # | step | result |
|---|------|--------|
| 1 | Merge `main` (`da4b67f0b`) with `--no-ff` into the selected branch | `1ba70a8ea`. Two backlog conflicts (`ciu/KNOWN_ISSUES_TODO_BACKLOG.md`, `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`), both pure "both sides appended" — resolved by keeping both sides (`git merge-file --union`), blank line between sections restored. |
| 2 | Port the reviewed Run-Gate paths from the candidate `cmru-release-readiness-20261015@2b7f27501` | `ee15a9174`. The candidate contains this branch's head and `main`, so `git checkout 2b7f27501 -- run-gate-project tester-unified` is the exact path-limited port: only adds/modifications under those two trees, no CMRU/CIU/Assay/cgroup-profiler path, no merge of the candidate. The two review reports arrive byte-identical and were not edited afterwards. |
| 3 | RG-98, RG-99, RG-100, RG-101, RG-103, tester image | `3da9b9ce8` (see REPORT for the oracle matrix) |
| 4 | LOG + REPORT | this commit |

## Decisions taken inside the fixed behavior of the packet

* **RG-98 — contract (a), not (b).** The packet requires that the Run-Gate
  `TMPDIR` not redirect Assay's own scratch into `repo_top` and that the
  default in-repo root plus a snapshot-isolated lane reach R0. The candidate's
  contract (b) (preflight refusal, external `state_root`) cannot satisfy that
  (and Sol's review shows its remedy is rejected by the loader for
  `mode = "host"`). Assay has no scratch-root flag and decides scratch from the
  process `TMPDIR`; the only per-child channel Assay offers is
  `[lanes.<n>.infrastructure]` (`required-env:`). So Run-Gate now exports
  `RUN_GATE_TMPDIR` always and `TMPDIR` only when the state root is outside the
  judged worktree. The consumer-visible consequence — a lane that forwarded
  `TMPDIR` by `env_passthrough` while keeping the in-repo default root now
  gets its child `TMPDIR` only through the `required-env:RUN_GATE_TMPDIR`
  mapping — is a documented behavior change (CHANGES, CONSUMERS, SPEC,
  DESIGN-GUIDE). No Assay internals were touched or inspected beyond reading.
* **RG-99 — append, floor 2.38, in the lane's own shell.** The existing count
  can only be read where the lane runs (an exec runner's environment is not the
  controller's), so the append is shell text in the inner script. A
  non-integer count is refused (exit 2) rather than clobbered; an installed
  `git` older than 2.38 is refused by name (exit 2); no `git`, or an
  unparseable banner, is left to Git.
* **RG-100 — fail closed, scoped.** `--list`/help/omitted lane never probe.
  `doctor`, `--check-env`, and `history`/`footprint` without a lane filter
  resolve, and an unavailable judge is an infrastructure refusal (exit 2) —
  no softening to placeholders for these verbs, no manifest written. A named
  lane resolves only when it is not declared locally, so a stopped runner does
  not block questions about local lanes (avoids a superset refusal).
* **RG-101 — remedy validity, not just wording.** The refusal now covers
  legacy `host` as well as `bare-host` (Opus L2), and targets the central
  config when the lane is inherited from it (a project-file migration would be
  a no-op there). `migrate-modes` additionally materializes names referenced
  only by `[assay]` / `[assay.import]`. A test runs the printed command and
  reloads the config.
* **RG-103** — doctor walks sequence members (transitively, cycle- and
  missing-safe) and reports each with its environment facts; also guarded
  lane-without-environment (2b loop, main loop) and `--check-env`.
* **Tester image** — decided by capability (`command -v docker`,
  `docker compose version`), not package name. The literal text
  `docker-cli docker-compose` is intentionally still present in a Dockerfile
  comment because the candidate-side CMRU test greps for it (see REPORT).

## Evidence gathered while working (probes, not test runs)

No `pytest`, Hypothesis, unittest or project test script was executed from the
cockpit (AGENTS.md "gate is never the devcontainer"). What was run, all
read-only or confined to scratch directories under `$CLAUDE_JOB_DIR/tmp`:

* `python3 -m py_compile` on `run_gate.py`, `tests/test_run_gate.py`,
  `tests/test_tester_unified_base_variants.py` — clean.
* `run-gate doctor` through `run_gate.main` on a scratch fixture with a
  `kind = "sequence"` lane (previous behavior: `KeyError: 'environment'`) —
  exit 0, `[OK] sequence 'gate' member 'docs': environment 'bare-host': mode
  host (...)`.
* The generated inner shell under real `bash` and real Git 2.55: baseline repo
  rejected with `GIT_TEST_ASSUME_DIFFERENT_OWNER=1`, accepted with the
  injection; existing count 1 → count 2 with both entries visible; `abc` and
  `08` refused with exit 2; fake `git` banners 2.37.9/1.9.0 refused, 2.38.0 /
  3.0.1 / `2.38.1.windows.2` accepted; `bash -n` of the assay inner.
* The Dockerfile's real apt `RUN` step (extracted, run under `/bin/sh`)
  against shim bases: MDT-like installs no Docker package; docker-without-
  compose installs only `docker-compose`; upstream-like installs both and
  verifies; the legacy unconditional line fails with the dpkg-style overwrite
  error on the MDT-like base (control).

Not run (and why): the registered `selftest`, `assay-r1`, `assay-r2`, any
image build, the live concurrent `docker exec` acceptance, any live Assay run —
the packet reserves authoritative and long gates to the controller.
