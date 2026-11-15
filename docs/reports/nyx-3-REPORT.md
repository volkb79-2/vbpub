<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/nyx-3-REPORT.md -->

# nyx-3 report (fix-nyx-3-20261104)

Worktree `<local-path>`, branch `fix-nyx-3-20261104`, from main `3d7e34cc6`. Nothing pushed, merged or released.

## Items

| Item | Result | Commit | Evidence |
|---|---|---|---|
| NL-35 | FIXED (listed misses and false positives) | `5351e28bb` | `session_extract/shellcmd.py`, `ledger.py`; tests `tests/test_session_extract_shellcmd.py` |
| NL-24 | FIXED | `684b785c6` | `src/nyxloom/__init__.py` `_resolve_version`; tests `tests/test_version_shadow.py` |
| NL-38 | PARTIAL: docs part FIXED, resolver feature still OPEN | `7f42cac7b` | see below |
| NL-12 | FIXED (genuine race, not only load) | `4c7ba6d65` | `src/nyxloom/wrapper.py`; tests in `tests/test_wrapper.py` |
| Test leaks | FIXED | `122865e80` | `tests/test_daemon.py` (interrupt test), `tests/test_crash.py` (SIGKILL drill) |
| Backlog | statuses set | `1575d3aff` | NL-12/24/35 `fixed` with `closed_reason`; NL-38 note |

### NL-35
Changes in `shellcmd.py`:
- `sudo` wrapper strips `-u USER`, `-g`, `-h`, `-p`, `-C`, `-D`, ... argument options.
- `timeout` wrapper strips `-s SIGNAL` / `-k DURATION` and accepts a fractional duration.
- Bare `git tag`, `-n`, `--contains`, `--sort=` are listings; creation and deletion (`-a -s -d -f -m -u`, `--delete` ...) and `git tag NAME` are effects.
- `curl` patterns skip quoted strings whole (`_ANY`), so `-H "a -d b"` is no longer read as `--data`; real `-d` / `-X DELETE` next to a quoted header still match. A linearity test covers adversarial input.
- More than 12 leading `VAR=x` assignments (wrapper passes 12 -> 64).
- `$(...)` inside double quotes is analysed (`echo "$(git push)"`); inside single quotes it stays literal.
- Contract (b) of the entry: `unresolved_head_segments()` lists `$VAR`-headed segments (also inside `ssh host '...'`); the ledger prints `[possible effect, head unresolved] <segment>`. A segment already classified as an effect is not listed twice.
- Left open, per decision D4 (re-measure the corpus before widening defaults): heredoc piped to ssh, the herestring swallow, `docker compose up/down` and `docker exec` patterns. Recorded in the entry and the module docstring.

### NL-24
`_resolve_version` ignores a `nyxloom*.egg-info` that sits beside the imported package when a distribution is installed elsewhere; an egg-info-only dev install keeps working. Cheap glob guard so normal start-up scans no metadata. Tests use real metadata directories, plus one test that proves the fixture reproduces the shadowing under plain lookup order.

### NL-38 (docs part)
- `1dd3d1` literals replaced by `nyxloom-<instance_id>-...` plus a `ciu resolve --json` pointer in: `ntfy/README.md` (also a `$NTFY` variable), `pwmcp-instance/README.md`, `ciu.compose.yml.j2` and `ciu.defaults.toml.j2` comments, `nyxloomd/systemd/README.md`, `docs/runtime-process-model.md`, `nyxloom-trove/nyxloom.toml` comment.
- `nyxloomd/ciu.toml` (tracked render artifact): `container_prefix` set to the template's no-deploy value `nyxloom`.
- `nyxloomd/docker-compose.yml` (plain-compose fallback): container and network names derive from `${NYXLOOM_CONTAINER_PREFIX:-nyxloom}`. The variable is new in this file; the fallback is not exercised by any test.
- `nyxloom-prod-*` runtime text: `supervise.sh` hint, `nyxloom-liveness.service` comment, `adapters.py` docstring.
- STALE parts of the entry: `mattermost/README.md` and `mattermost/docker-compose.yml` no longer exist under `nyxloom/` (MM-MOVE), and no `1dd3d1` remains in `/mattermost`.
- Deliberately unchanged: dated `docs/plan-*.md` (historical plans), `src/nyxloom/config.py:172` doc comment (frozen-core module per `__init__.py`), test fixtures using `nyxloom-prod-*` as sample data.
- STILL OPEN: the poll-time resolver and the per-poll fail-loud checks (Design a-d) are a feature (`feat`, minor bump) and are not in this fix-only package. The entry stays `open` with a note.

### NL-12
Not only load: a genuine ordering race. `wrapper.py` published `receipt.json` (step 9) and only then assembled the typed result, so the next daemon pass, triggered by the receipt, could read "no typed result for this attempt" and record `REVIEW_REJECTED`. The typed result is now written first; the receipt is the last artifact published. An `OSError` while writing results is logged and does not suppress the receipt. Assertions of the behavioral test were not touched or widened.

Tests (`tests/test_wrapper.py::TestReceiptIsPublishedAfterTheTypedResult`):
- a real FIFO as the judgement file holds result assembly; no receipt may be visible meanwhile (fails on the old order);
- an unwritable result path does not suppress the receipt (fails on the old order).

### Test leaks
- `test_interrupt_attempt_signals_pgid` (`test_daemon.py`): `try/finally` kill and wait of the `sleep` child.
- SIGKILL drill (`test_crash.py`): the child.pid cleanup tolerates a missing or half-written pid file (it already had a `finally`; the exists-then-read race could raise and skip the reap).
- These are hygiene fixes with no failing-without-fix oracle (a leak only shows on a failure path).

## Verification
- Focused runs in `tester-unified:local` (`--init`, `--memory 2g`, `--cgroup-parent dev-gates.slice`, `-n 2`, `--cli-case-partial`, `MDT_COW_ROOT` unset): `test_session_extract_*` (shellcmd, ledger, successor, compress, harness, r2_survivors, claude_code, gap_closure), `test_version_shadow`, `test_cli`, `test_adapters`, `test_wrapper`, `test_result_assembly`, `test_behavioral`, `test_containment`, `test_daemon`, `test_crash`: all pass (EXIT 0).
- New shellcmd tests fail on the pre-fix source (import error on the new function; the parametrized cases cover the detector changes). New wrapper tests fail on the old wrapper.

## Gate lane
`run-gate tester-unified` from the worktree at `1575d3aff`, three attempts, waiting for PSI some avg10 <= 25 before each:
1. Not run: dirty tree (my scratch basetemp inside the worktree; removed).
2. `run-gate-vbpub-tester-unified-978102-1793751748`: FAIL/COMMAND_FAILED, R0 first failing test `test_hang_detection_full_pipeline_real` (same test nyx-1 reported).
3. `run-gate-vbpub-tester-unified-1037381-1793753507`: FAIL/COMMAND_FAILED, R0 first failing test `test_policy_update_full_flow` (a different test; verdict file `nyxloom/.assay/verdict-tester-unified.json`, gitignored).

Both are timing/thread tests that pass in isolation (the hang test also passes alone with `--cov`, with the lane's `TMPDIR`, and inside a full-suite `-n auto --cpus 3 --cov` repro of the lane argv, where the only failures were a `cfg_daemon` teardown-thread flake and two containment tests that fail only because of my `/t` mount). The failing test changes between runs while the host PSI stays 25-50 (other agents' gates), so this is load flakiness, not a deterministic regression from this package. It is NOT a verified PASS: the controller should re-run the lane when the host is quiet, or compare against main. A suspect worth checking is the `cfg_daemon` fixture, whose daemon thread outlived teardown in my repro; not investigated further here.

## Findings and deviations
- Environment hazard found: `test_behavioral.py` review-leg tests fail with `AdapterError('rendered prompt exceeds argv_max (1609 > 1500)')` when the pytest basetemp path is long (the brief says to bind `--basetemp` under the worktree; a deep path trips it). With a short mount (`/t`) they pass. This is a real fragility of the tests/prompt budget vs path length; not filed in the backlog by this package (candidate NL entry for the controller).
- A basetemp inside the git worktree also breaks `test_result_assembly::test_the_head_commit_degrades_to_empty_rather_than_lying` (its "not a repo" dir is inside the repo). Same cure: basetemp outside the repository, bind-mounted.
- `sed -i` was used once on my own scratch runner script (not repository files) to add a mount and an env var. Repository files were edited with the Edit tool only; one `cat >>` appended the test class to `tests/test_wrapper.py` and one `git checkout --` reverted my accidental comment edit of `config.py`.
- `CHANGES.md` `[Unreleased]` gets one `### Fixed` block; nyx-1/nyx-2 add lines at the same spot, so expect a trivial merge conflict there.
- The scratchpad dir is shared between concurrent agents: another agent overwrote a generic runner script name mid-session; mine now lives under `scratchpad/nyx3/`.
