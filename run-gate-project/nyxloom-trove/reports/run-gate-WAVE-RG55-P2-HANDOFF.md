# run-gate-WAVE-RG55-P2 — handoff (RG-55 wave, package P2: run-gate client)

**Controller:** vbpub session (Fable 5.1). **Implementer:** fresh Sonnet.
**Worktree:** `/workspaces/vbpub/.worktrees/rg55-run-gate-client` (branch
`rg55-run-gate-client`, from `main` at the P0 freeze commit). **Project
dir:** `run-gate-project/` inside that worktree (`run_gate.py` is a symlink
to `run-gate.py`; edit either, one inode). Work ONLY there. Read this file
from the MAIN checkout path if it is not in your worktree.

Plan of record: `/workspaces/vbpub/run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`
(read §1–§3, §5 "P2", §6–§10). Contract (FROZEN, incl. §7 computation
rules): `/workspaces/vbpub/run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`.
Golden fixtures: `/workspaces/vbpub/run-gate-project/nyxloom-trove/fixtures/rg55/`
(read its `README.md` first). Controller log: `reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`
(rulings RW-n bind you).

Every decision below is DECIDED. Do not re-open D-1..D-16. Decision asks
the plan, the contract and the rulings do not settle go into your REPORT
§"Decision asks" and your return message — never decide a product question
on silence, never stop on one either: do everything that does not depend on
it. **The cgroup-profiler daemon is being built in parallel (P1) — you never
need it: every daemon interaction in your tests goes through the fake
docker shim answering with the golden fixtures.**

---

## 1. Read list (in this order)

1. Plan sections named above; the contract with §7; `fixtures/rg55/README.md`.
2. `SPEC.md`: the Status block (lines 1–40), `R-04`, `R-07`, `R-08`, `R-09`,
   `R-29`, `R-36` (all sub-clauses), `R-39`, `R-40`, `R-41`, `R-42`; note the
   two documented drifts (stall_timeout "assay lanes only" text after RG-41;
   R-07 omitting `mode`/`container_name`; the duplicated paragraph in R-08).
3. `run_gate.py`: lines 1–135 (revision note, constants, exit codes),
   152–437 (validators, `LANE_KEYS`, `_check_keys`, `[history]`,
   `load_config`), 640–780 (slice resolution + `check_slice_memory_admission`
   — the inert-from-a-devcontainer read), 900–1250 (history store,
   `start_run_record`/`finish_run_record`/`flush_run_record`, atomic write,
   gitignore check), 1550–1800 (`adopt_container_duration`, `record_lost_run`,
   `duration_stats`, `lane_history_report`, `_print_lane_history`,
   `cmd_history`), 3500–3720 (`assay_artifact_paths`, `build_assay_inner`,
   `build_command_inner`, `ProgressWatch`), 3908–4110 (`LogStreamWatch`,
   `make_progress_watch`), 4117–4300 (`await_container` — the 30 s tick,
   stall path, evidence, `docker rm -f`), 4331–4560 (`follow_container`,
   `promote_follower`, `resolve_inflight`), 4613–4760 (`run_container_lane`:
   argv construction, inflight record), 4823–4930 (`run_exec_lane`,
   `run_bare_host_lane`), 4960–5110 (`usage()`), 5290–5442 (`main`).
4. `tests/test_run_gate.py`: 90–240 (`fake_docker`, `fake_docker_executing`,
   readers, `run_tool`, `ambient_cgroup`), ~3100–3200 (`_fake_cgroupfs` and
   its RG-20 tests), ~6740–6900 (`TestHistoryEndToEnd`, `TestHistoryQueryVerb`,
   `TestHistoryRollingSeries`, `TestHistoryEligibilityGuard`), the RG-35
   `fake_docker_stateful` tests, the RG-41 `LogStreamWatch` tests.
5. `tools/coverage_gate.py` (whole) + `tests/test_coverage_gate.py`;
   `KNOWN_ISSUES_TODO_BACKLOG.md` RG-27, RG-48, RG-53, RG-54, RG-55, RG-56,
   RG-57; `CHANGES.md` head; `README.md`, `CONSUMERS.md` (adoption steps,
   "What each lane costs"), `LANE-AUTHORING.md` §1–§2.
6. The assay-lane template: `cmru/run-gate.toml`, `cmru/assay.toml`,
   `cmru/tools/assay/` (the pinned pyz + sha256 you vendor), `cmru/tools/
   coverage_canary.py` and `scripts/cgroup-profiler/tools/canary-run.sh`
   (two R3 shapes — pick one and say why), `assay/README.md` (rigor ladder,
   `base_source = "request"`, python judge keys), `assay/docs/CONSUMERS.md`
   (lane config keys, `judge.paths`/exclusions if they exist — find the
   key that limits judged source to `run-gate.py`).
7. `scripts/cgroup-profiler/lib/util.py` lines 70–170 (`read_int`,
   `read_kv`, `read_pressure` — port as string parsers, attributed).

## 2. Deliverables (commit order; `selftest` green after every commit)

**C1 — RG-53.** `tools/coverage_gate.py`: read `missing_branches`; a
changed executable line with an untaken arm counts as uncovered (report
`branches_total`/`branches_missed` beside lines); refuse `total_changed_exec
== 0` (exit 2, naming the resolved base, HEAD, and the three known routes to
0/0 — merge-commit first-parent, stale worktree base, reverted work) unless
`--allow-empty-diff`. `run-gate.toml`'s `selftest` argv gains nothing (the
flag is not passed — a 0/0 selftest must go red). Tests in
`tests/test_coverage_gate.py` for both. CHANGES: a BREAKING note (every
consumer of the vendored judge inherits stricter semantics on re-copy).
Backlog RG-53 → FIXED with evidence.

**C2 — assay lanes for run-gate-project (D-11).** Vendor
`tools/assay/assay-6.1.1.pyz` + `.sha256` (copy from cmru; verify the
sha256). `assay.toml` (schema 2) with: `[lanes.r1]` rigor `["R0","R1"]`,
argv `python3 -m pytest tests -q --cov=. --cov-branch --cov-report=json:coverage.json`,
python judge scoped to `run-gate.py` only (tests/, tools/ never judged),
`base_source = "request"`; `[lanes.r2]` rigor `["R0","R2"]`, same argv,
`base_source = "request"`, mutation operators as in
`scripts/cgroup-profiler/assay.toml`, `jobs = 1`, `max_mutants = 1500`,
budget `4h`. `run-gate.toml`: `[lanes.assay-r1]` and `[lanes.assay-r2]`
(`kind = "assay"`, `environment = "bare-host"`, `assay_command =
["python3", "tools/assay/assay-6.1.1.pyz"]`, `[lanes.<n>.pins.assay]
version/sha256`, `clean_tree = true`, `stall_timeout = "20m"` on r2,
budgets 30m/4h), `[lanes.assay-r3]` = a canary lane (`kind = "command"`,
bare-host) running `tools/canary-run.sh`: copy the project to a scratch
dir, break one function the suite provably covers (flip
`duration_stats`'s median to a mean), run the r1 lane there, assert it
goes RED, restore, exit 0 only if the canary was caught. `[lanes.gate-full]`
conjunction = `selftest`, `assay-r1`, `assay-r3` (r2 is invoked
separately, pre-merge). `cmru.toml`'s release gate stays `selftest`
(comment says why). `doctor` must pass for the new lanes. Backlog: no new
entry; README "Gate and evidence" names the four lanes.

**C3 — profiling client (SPEC `R-43`).**
- Constants: `PROFILE_DAEMON_DEFAULT = "cgprofile-host-daemon"`,
  `PROFILE_SAMPLE_SECONDS = 5` (with the reason), `PROFILE_CTL_TIMEOUTS`
  (contract §1.5), `PROFILE_CONTRACT = 1`, `PROFILE_TOKEN_ENV =
  "RUN_GATE_PROFILE_SESSION"`.
- Config: top-level `[profile]` (`enabled`, `daemon`, `interval` in the
  `budget` grammar, `damon`) with whole-table shadowing (R-09) and
  unknown-key refusal; per lane `profile = false` or
  `[lanes.<n>.profile]` `enabled`/`damon`; `[footprint]` (`tolerance_pct`,
  `max_age_days`). Add to `_validate_config` top-level keys and `LANE_KEYS`.
- `ProfilerClient`: `version()`, `start(...)`, `status()`, `host()`,
  `stop(session)` — each ONE `docker exec <daemon> cgprofile ctl … --json`
  via `subprocess.run(capture_output=True, timeout=…)`; parses one JSON
  object; checks `contract == 1`; every failure (non-zero exit, timeout,
  garbage, wrong contract) returns `None` and records ONE reason string —
  the caller prints ONE warning per lane invocation. Never raises.
- `ResourceAccumulator`: contract §7 over samples (shared by nothing else —
  run-gate is standalone) producing the §3 key set with `method: "basic"`.
- `BasicSampler`: one `docker exec <lane container> sh -c 'for f in …; do
  echo "== $f"; cat /sys/fs/cgroup/$f 2>/dev/null; done'` per tick reading
  the file list in contract §4.3, parsed with the ported parsers; host PSI
  from `/proc/pressure/memory` and `/proc/pressure/cpu` read directly
  (readable in the devcontainer; `RUN_GATE_PROC_ROOT` env override for
  tests, like `RUN_GATE_CGROUPFS_ROOT`).
- Token: `secrets.token_hex(16)` per invocation; `-e
  RUN_GATE_PROFILE_SESSION=<token>` appended where `forward_env` values are
  appended in BOTH `run_container_lane` and `run_exec_lane`; recorded in the
  inflight record as `profile_token`, and `profile_session` after `start`.
- Flow, ephemeral: after `docker run -d` succeeds → `docker inspect
  --format '{{.Id}}'` → daemon `version` (once per invocation) → `start
  --scope container` (or basic sampler if unavailable) → `await_container`
  ticks: every `PROFILE_SAMPLE_SECONDS` the basic sampler samples (daemon
  path: no per-tick work; optionally `status` at the 30 s progress tick for
  the live line) → in the SAME `finally` that removes the container, BEFORE
  `docker rm -f`: `stop` (or the basic sampler's final sample + summary) →
  summary into the run record → footprint line. Covers all three arrival
  paths of `await_container`, the stall path (stop before the kill's rm),
  `follow_container`/`promote_follower` (adopt `profile_session` from the
  inflight record; a collected already-exited container → `resources:
  null`, `profile_error: "collected after exit"`), and Ctrl-C (bounded,
  at-most-once, R-36h's staked-claim pattern).
- Flow, exec: rewrite `run_exec_lane` from blocking `subprocess.run` to
  `Popen` + `proc.wait(timeout=PROFILE_SAMPLE_SECONDS)` loop (stdout/stderr
  still inherited; exit code unchanged); container id = the persistent
  runner's; `--scope container-shared`; basic path samples the runner's
  cgroup with baseline subtraction. The R-41 exec mutex is untouched.
- `run_bare_host_lane`: unchanged except the host-pressure line at start
  and `resources: null`, `profile_error: "bare-host lanes are not profiled
  (RG-57)"`. Conjunction lanes: nothing.
- Disclosure lines exactly as contract §4.6; `--dry-run` prints the
  profile plan (daemon name, scope, damon, token env) and starts nothing.
- `[profile] enabled = false` / lane `profile = false` → no token, no
  daemon call, no sampler, `resources: null`, `profile_error: "disabled"`.

**C4 — history schema 2 (R-36 amendments).** `HISTORY_SCHEMA = 2`;
records gain `resources`/`profile_error`/`profile_ref`; `_apply_record`
strips private keys as before; schema-1 stores load with `resources: null`
on old entries and are rewritten as 2 on the next write (test: a schema-1
fixture store → one run → schema 2, old entries intact); `duration_stats`
generalized to `series_stats(entries, getter)` for
`memory_peak_bytes`, `memory_peak_over_baseline_bytes`,
`hot_set_p90_bytes`, `cpu_cores_avg`, `memory_full_stall_seconds` (median
never mean; absent values excluded from the series, `count` says how
many); `history --json` and the table gain them (columns PEAK, +BASE,
HOT p90, CORES, STALL; `-` when null). The RG-27 traps re-proven for the
new series (one 10× outlier does not move the median; a dirty run never
touches a commit's entry).

**C5 — `footprint` verb (SPEC `R-44`).** `run-gate footprint [LANE]
[--json] [--write] [--worktree PATH]`: reads the store (no lock, like
`history`), distills PASS + history-eligible entries per contract §4.5
into the manifest object; `--write` writes `run-gate.footprint.json` next
to the effective project's `run-gate.toml` (temp + `os.replace`, sorted
keys, indent 2, trailing newline; refuses when the store has no eligible
profiled run at all, naming why); without `--write` prints the table
(`LANE RUNS PEAK(med/max) +BASE HOT p90 CORES STALL DURATION`) or `--json`.
`footprint` joins the reserved lane names (R-08) — flag as a load-time
BREAKING change like `history` was. `doctor`: when a manifest exists,
warn per lane when the live history median peak differs from the
manifest's by more than `tolerance_pct`, and once when `distilled_at` is
older than `max_age_days`; when none exists, one INFO line naming
`footprint --write`. The run path reads the manifest (if present) to fill
`meta.expected` in `start` and the `| manifest {n} MiB` tail of the
footprint line.

**C6 — RG-48.** `resources.cpus` (lane) and `[environments.<e>].resources
= { cpus = … }` (environment; lane wins) → `docker run --cpus <n>` on
ephemeral lanes (decimal string, validated like docker: `^\d+(\.\d+)?$`,
> 0); exec lanes: naming-only WARNING like `memory` today (R-29's rule);
`doctor` warns when a container lane's argv contains `-n auto` (or
`--workers auto`) and neither lane nor environment declares `cpus`.
Backlog RG-48 → FIXED; SPEC R-29 amended.

**C7 — `doctor`.** New check "profiler": daemon container present and
running? `ctl version` contract/version/DAMON state; `[profile]`
effective config; when the daemon is up, the host and slice pressure from
`ctl host` (replaces nothing — it is added beside the existing slice
check); the existing R-29 slice read, when it fails from a private cgroup
namespace, now says WHY (`/proc/self/cgroup` is `0::/` and no slice
directory is visible: cgroupns=private — host-side slice truth is only
reachable through the profiler daemon) instead of a bare WARN.

**C8 — docs/spec/backlog/revision.** SPEC: `R-43` (profiling: a–h
sub-clauses covering token, scopes, daemon path, basic path, degradation,
inflight fields, disclosure lines, config), `R-44` (footprint manifest),
`R-29` (cpus), `R-36` (schema 2 + new stats), a rule id for RG-41's
log-stream liveness (backfill, e.g. `R-40f` — keep ids monotonic and note
it in the Status block), drift fixes (stall_timeout text in R-08/R-40c;
R-07 adds `mode`/`container_name`; the R-08 duplicate paragraph removed;
add `Rev 10` to the Status block listing R-43/R-44 and the amendments).
`README.md` (lane schema: `profile`, `resources.cpus`; verbs: `footprint`;
"What each lane costs" gains the footprint story), `CONSUMERS.md`
(adoption steps: the daemon is host infrastructure started from the vbpub
checkout — `cd scripts/cgroup-profiler && ciu up`; `run-gate.footprint.json`
is TRACKED — commit it; `.run-gate/` stays ignored), `LANE-AUTHORING.md`
(one paragraph: footprint-informed budgets), `CHANGES.md` `[Unreleased]`
(RG-55, RG-53 BREAKING, RG-48, `footprint` reserved-name BREAKING, schema
2), backlog RG-55/RG-53/RG-48 → FIXED with measured evidence (RG-56/57
untouched), `__revision__ = 41` with the note prepended in the existing
style, `usage()` text for `footprint` and the flags.

## 3. Tests (every behaviour; red-first where expressible)

- The fake docker shim gains a `cgprofile ctl` branch: `exec
  cgprofile-host-daemon cgprofile ctl <verb> …` answers the golden
  fixture for that verb (copied into `tests/fixtures/rg55/`, with a
  byte-identity test against `nyxloom-trove/fixtures/rg55/`), with per-test
  overrides for: exit 2 + `error-v1`, exit 3, hang (sleep past the
  timeout), garbage stdout, `contract: 2`. Every one → ONE warning, verdict
  unchanged, `resources: null` or basic fallback as the contract says.
- Frame-driven basic path: the shim serves frame k's file contents on the
  k-th `cat` call; the resulting record's `resources` equals
  `summary-basic-v1.json` (after normalizing `started_at`/`ended_at`/
  `duration_seconds` to the fixture's values via an injected clock).
- Daemon path: `stop-v1` → the record's `resources` equals `summary-v1.json`
  byte-for-byte; `profile_ref` filled; footprint line printed exactly.
- `run_exec_lane` red-first: the current blocking implementation cannot
  sample mid-run — a test that asserts ≥ 2 samples during a 12 s fake exec
  fails before C3, passes after.
- Stall path: `stop` is called BEFORE `docker rm -f` (assert argv order in
  the shim log); Ctrl-C during `stop`: no traceback, exit status is the
  signal's; at-most-once flush.
- Re-attach: an inflight record with `profile_session` → `stop` is called
  with that id and no new `start`; collected-after-exit → `resources: null`
  with the exact `profile_error`.
- Schema-1 → 2 migration; stats traps; `footprint` table/json/write/refuse;
  `doctor` divergence and staleness (fixture manifest + store); reserved
  name `footprint` refused as a lane; `[profile]`/`[footprint]`/lane
  `profile` validation incl. unknown keys; `--dry-run` plan text; RG-48
  argv (`--cpus`) + validation + doctor warning; RG-53 branch and 0/0
  cases; the canary lane proves the gate goes red.
- Coverage: `selftest` diff-coverage 100% line+branch (real after C1) —
  no `pragma: no cover`; `assay-r1` 100% on changed lines; `assay-r2` run
  once at the end (survivors → kill them or justify each in the REPORT);
  `assay-r3` green.

## 4. Gate and evidence

From `<worktree>/run-gate-project`: `nice -n 19 ionice -c 3 ./run-gate.py
selftest --allow-dirty` (bare-host, ~2–3 min); read the verdict in a
SEPARATE step (exit code + the `diff-coverage OK` line), never from a pipe
tail. `./run-gate.py assay-r1`, `assay-r3` once before return; `assay-r2`
once at the very end (hours — start it under `nice`, record the verdict;
if it cannot finish inside its budget, report the partial result and the
survivors seen so far). Live acceptance (yours, host rule §6):
1. Ephemeral: a temp project config with `[lanes.probe]` (`kind =
   "command"`, `environment = "tester-unified"`, argv `python3 -c
   "b=bytearray(100*1024*1024); import time; time.sleep(12)"`) run through
   the REAL docker with no daemon present → basic-path record with
   `memory.peak_bytes ≥ 100 MiB`, `source: "memory.peak"`, host PSI filled;
   the footprint line printed. `docker update --cpus=3` after launch; one
   gate container at a time.
2. Exec: `docker run -d --name rg55-p2-shared --cgroup-parent=dev-background.slice --entrypoint sh cmru-enroll-fixture:local -c 'sleep 600'`
   declared as `[environments.shared] mode = "exec", container_name =
   "rg55-p2-shared"` with a command lane allocating 80 MiB → record shows
   `scope: "container-shared"`, `baseline_bytes` > 0,
   `peak_over_baseline_bytes ≥ 70 MiB`, `source: "sampled-max"`; ≥ 2
   samples. `finally`: `docker rm -f rg55-p2-shared`.
3. `footprint --write` on run-gate-project's own store after ≥ 3 selftest
   runs (selftest is bare-host → no resources → the verb must REFUSE with
   the documented message; then use the probe project's store to prove the
   write path). Record both outputs.

## 5. Records

`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-LOG.md` (per
commit: hash, what, gate verdict), `…-P2-REPORT.md` (per deliverable
evidence; golden reproductions; the r1/r2/r3 verdicts with survivors;
live-probe transcripts; decision asks; deferred items with their RG id —
file new RG entries via the backlog file in the house style),
`…-P2-BRIEF-n.md` at checkpoints. The LOG states your orientation call
count and which read-list items were wrong or missing.

## 6. HOST LOAD (binding)

8 cores shared with a production game server; PSI is the signal. pytest
SERIAL only, always `nice -n 19 ionice -c 3`; targeted tests while
iterating; the whole selftest at most once per checkpoint and once before
the return. Gate containers: at most 2 across the estate (`docker ps
--format '{{.Image}}'` for `tester-unified:local` first), `docker update
--cpus=3` right after launch, remove in a `finally`. The r2 mutation lane
is bare-host and serial; run it last. Never pass `--cgroupns=host`/
`--pid=host` to anything.

## 7. Rules

- Edit files with the Edit tool; never sed/python rewrite scripts.
- `git -C <worktree> add <new>`; `git -C <worktree> commit -F <msgfile>
  --only -- <paths>`; never `cd /workspaces/vbpub` before a git command;
  never a bare `git stash`. Trailers on every commit:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01YBJzBA7KyG4ayu5ndNf9Hx`.
  After adding `tools/assay/` and `tests/fixtures/`: `git -C <worktree>
  ls-files run-gate-project/<dir> | wc -l` (a .gitignore rule can drop
  files silently — `.assay/` and `*.pyz` patterns exist; check and add a
  negation if needed, as cmru did).
- Forbid: editing `scripts/cgroup-profiler/`, `ciu/`, `/workspaces/dstdns`;
  any admission/queueing POLICY (RG-56); any daemon call without the
  contract's timeout; changing a lane's exit-status semantics; `pragma:
  no cover`; pushing; merging; `cmru release`.
- Checkpoint (E-008): ARM at ~120k context or ~60 tool calls, CUT at the
  next coherent boundary (green gate > commit > LOG/REPORT write >
  edit-cluster end; never on a red gate); write `…-P2-BRIEF-n.md` + a
  `/compact` retention prompt, commit, return — the controller dispatches
  a fresh successor.
- Return message: commits (hash + subject), gate verdicts (selftest
  numbers, assay-r1/r2/r3 outcomes), live-probe results, decision asks,
  deferred items. Claim only what you ran — a fresh adversarial reviewer
  verifies.
