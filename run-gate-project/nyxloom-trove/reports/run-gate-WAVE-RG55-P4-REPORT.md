# run-gate-WAVE-RG55-P4 — REPORT (historical evidence retained; current
# round-3 repair follow-up is recorded in Session 11)

Package P4 (run-gate follow-ups: RG-57, RG-58, RG-59, RG-60, RG-61) of the
RG-55 wave, third track (RW-27). The opening sections below are retained
historical session records; their earlier partial-status header is
superseded by Sessions 9–10. The current round-3 review found B1/B2, and
Session 11 records the focused repair follow-up. Claim only what was run —
every line below is either a command that session actually executed
(verbatim output, or the exact assertion from it) or explicitly marked as
inherited from an earlier session's own LOG entry (cited by commit hash, not
re-derived).

## Deliverable-by-deliverable evidence

### C1 — RG-60: exec-lane inflight record (session 1, commit `a6716422`)

Oracle (from the backlog entry): an exec lane writes the SAME inflight
record shape a container lane writes, present for the whole exec window,
cleared on every exit path. Tests (`TestExecLaneInflightRecord`, 3 tests):
disk-state-at-the-instant-of-exec proof, a simulated-client-death record-
survival proof, and a profiling-disabled regression test (added after a
first implementation attempt broke `test_run_exec_lane_tolerates_no_run_
record` — full account in the LOG's own Commit 1 section, including the
R-04/R-36h reasoning for reverting `container_state()` back to the
profiling-enabled branch only). Targeted verdict (session 1):
`TestExecLaneInflightRecord or TestExecLaneProfilingWiring or
TestReattachProfilingWiring or TestAwaitContainerProfilingWiring` — 23
passed; `Inflight or inflight or ContainerLane or container_lane` — 62
passed. SPEC R-43a/R-43f updated (the "exec lane writes no inflight
record today" caveat dropped).

### C2 — RG-59: live-run daemon-absent warning names the real cause (session 1, `b5e4a9c6`)

Oracle: `_ctl`'s `JSONDecodeError` branch tells "daemon container absent"
(docker's own `stderr_tail` matches `_stderr_names_daemon_not_running()`)
from "a running daemon returned garbage" (stays "unparsable stdout"), one
shared `daemon_not_running_reason()` function feeding both `cmd_doctor`'s
WARN and the live-run reason so the two surfaces cannot drift apart.
3 new `TestProfilerClient` tests (not-running / no-such-container /
malformed-stdout-stays-malformed). Targeted verdict (session 1):
`TestProfilerClient or TestDoctorProfilerCheck` — 30 passed; `profil or
Profil` — 139 passed. Decision-ask recorded (RW-9): the backlog's own
quoted "already good" `cmd_doctor` text did not byte-match the
pre-existing code — treated as a paraphrase, not a literal pin.

### C3 — RG-58: bare-host `stall_timeout` load-time + doctor WARN (session 1, `e698835f`)

Oracle (RW-27a: warn, never refuse): a bare-host lane declaring
`stall_timeout` gets one load-time WARNING (`stall_timeout is inert on a
bare-host lane`) and a matching `doctor` WARN, config still loads, exit
code unchanged, container/exec lanes untouched. Full test/verdict detail
in the LOG's own Commit 3 section (session 1); not re-derived here.

### C4 — RG-57: bare-host lanes are profiled (session 2, `c37b6e94`)

Oracle (RW-27b, both halves as filed): daemon path via `resolve_self_
container_id` (self container id, scope ALWAYS `container-shared`,
per-invocation token) when a daemon is reachable; daemon-absent path via
`resource.getrusage(RUSAGE_CHILDREN)` (`method: "rusage"`, `source:
"rusage-maxrss"`, NEVER a `BasicSampler` — RW-27b, explicit and
deliberate: no cgroup here is safely attributable to just this lane's own
child). R-36h discipline: both `getrusage` calls guarded individually
(a genuine internal contradiction between BRIEF-1's own prose and
pseudocode, resolved in favor of guarding both — see the LOG's "R-36h
containment" note under session 2's Commit 4). `TestBareHostProfilingWiring`
originally shipped with 7 tests (session 2); THIS session added 9 more
(2 new classes/extensions — see "Coverage gap" below) once the real
`selftest` gate exposed branches session 2's own tests never reached.
SPEC R-43i (new sub-rule), R-36 series, CONSUMERS §6, LANE-AUTHORING all
updated (session 2, commit `5b80c024`).

### C5 — RG-61: eight-item documentation sweep (session 2 `5b80c024` + session 3 `02707e30`)

All eight of the backlog entry's own checklist items — see BRIEF-2's own
"What C5 delivered" section for items 1-4, 6-8 (all session 2). **Item 5
(the live `footprint --write` transcript) is THIS session's own
deliverable** — see "The real footprint transcript" below. **RG-61 is now
100% closed.**

## Coverage gap this session found and closed (not a separate RG id — a
release-gate defect in already-shipped C4/C5 code, closed before release)

The handoff's own package gates had never actually been RUN before this
session (BRIEF-2 cut before the first `selftest` launch). The first real
run exposed two genuine gaps, both traced to their root cause and fixed
before being declared green — full narrative, root-cause tracing, and the
exact uncovered-line lists are in the LOG's Commits 5, 6, and 9. Summary:

1. `test_no_stdlib_violations`'s allowlist never gained `"resource"` when
   C4 added `import resource` (fixed, commit `af654ede`).
2. `coverage_gate.py --base main` (BRIEF-2's own confirmed finding: the
   `selftest` lane hardcodes this, judging the FULL P0+P1+P2+P4 wave diff,
   not just this package's own increment) came back 97.2%/97.4% — every
   uncovered line traced to C4's own code (`resolve_self_container_id`,
   `start_bare_host_profiling`, two spots in `run_bare_host_lane`'s own
   `finally`), none pre-existing. 13 new tests closed it (commit
   `e0e02dce`) — see the LOG's Commit 6 for the full list and the
   local-coverage verification done before committing.
3. `assay-r3`'s own `median-not-mean-series-stats` canary had gone stale
   against a pre-existing (P2's own, RW-24/R-36k) refactor of
   `series_stats` — `tools/canary-run.sh` re-anchored to current source,
   same mutation semantics, same killing test (commit `7539a44e`).

## Gate verdicts (verdict read in a SEPARATE step every time, never a pipe tail)

```
$ nice -n 19 ionice -c 3 ./run-gate.py selftest      # third run, after both fixes above
...
1114 passed, 3 skipped, 2 warnings in 168.88s (0:02:48)
diff-coverage OK: 1008/1008 changed executable lines covered (100.0% ≥ 100.0% floor); branches 376/376 taken
run-gate: lane 'selftest' exit 0
```

```
$ nice -n 19 ionice -c 3 ./run-gate.py --base rg55-run-gate-client assay-r1
...
r1: PASS (exit 0)
  commit: 02707e30ddd6e4298437e7d0541ec3cd8150a16e
  argv: python3 -m pytest tests -q --cov=. --cov-branch --cov-report=json:coverage.json
run-gate: lane 'assay-r1' exit 0
```

```
$ nice -n 19 ionice -c 3 ./run-gate.py assay-r3      # `--base` REFUSED first (no {base} token, exit 2)
                                                       # second run, after the canary fix
run-gate-project assay-r1 canary
  median-not-mean                    ok (assay-r1 would reject it)
  median-not-mean-series-stats       ok (assay-r1 would reject it)

canary: 2 rejected, 0 survived
run-gate: lane 'assay-r3' exit 0
```

**`duration_stats`'s own canary (`median-not-mean`) — the item the dispatch
prompt named explicitly — was VERIFIED, not just run**: the script's own
mechanism runs the REAL `pytest` selector
(`TestHistoryRollingSeries::test_one_slow_outlier_does_not_become_the_
typical_cost`) against a disposable tar copy with the sabotage applied,
and the script itself fails (exit 1, "SURVIVED") if that test does NOT go
red — "ok (assay-r1 would reject it)" is the script's own positive
confirmation that the test DID fail on the sabotaged copy, not a
"the script ran without crashing" default. Confirmed by reading
`tools/canary-run.sh`'s `canary()` shell function before treating "ok" as
meaningful.

```
$ ./run-gate.py assay-r2      # NOT RUN — see below
```

`assay-r2` (mutation): **not attempted this session** — `pgrep -af
'run-gate.py --base main assay-r2|assay.cli run r2'` showed PID `1499375`
(`/proc/1499375/cwd` confirmed: P2's own worktree,
`.worktrees/rg55-run-gate-client`), P2's own resume, occupying
run-gate-project's shared `r2` lane. Per the dispatch prompt's own rule
("If occupied: write 'r2 pending'... and RETURN"), this session returns
without it. **Survivor table: not yet applicable — no run-gate-project
mutation run has completed under THIS package's own tip.**

## The real footprint transcript (RG-61 item 5)

Captured from the `selftest` PASS above (`.run-gate/history.json`'s real
`selftest` entry: `method: rusage`, `source: rusage-maxrss`, peak
267 MiB — no `cgprofile-host-daemon` reachable in this devcontainer):

```console
$ ./run-gate.py footprint --write
run-gate rev 42 — lane resource footprint
store: /workspaces/vbpub/.worktrees/rg55-followups-run-gate/run-gate-project/.run-gate/history.json
manifest written: /workspaces/vbpub/.worktrees/rg55-followups-run-gate/run-gate-project/run-gate.footprint.json
  LANE                 RUNS  PEAK(med/max)            +BASE    HOT p90   CORES    STALL   DURATION
  selftest                1    267 MiB/267 MiB            -          -    0.54        -     172.3s [source: rusage-maxrss]
```

Pasted byte-for-byte (paths as printed from this worktree, flagged as
checkout-relative in the surrounding CONSUMERS.md prose) into
`CONSUMERS.md`'s "The footprint manifest" section, replacing the old
rev-41 fabricated 8-run/746-MiB example. `run-gate.footprint.json`
committed (TRACKED per its own contract) — `02707e30`.

## `./run-gate.py doctor` (captured for this REPORT, not committed)

```
run-gate: doctor: [OK] docker: /usr/bin/docker
run-gate: doctor: [OK] lane argv[0] (RG-34): 3 container command lane(s): every argv[0] is {worktree}-anchored, absolute, or a bare command name
run-gate: doctor: [OK] git: worktree /workspaces/vbpub/.worktrees/rg55-followups-run-gate
run-gate: doctor: [WARN] host-lane git view (RG-21): /workspaces/vbpub/.worktrees/rg55-followups-run-gate is a LINKED worktree; its gitdir is /workspaces/vbpub/.git/worktrees/rg55-followups-run-gate, OUTSIDE the tree. run-gate's own container lanes are fine (they dual-mount the repo root), but a host lane delegating to a harness that bind-mounts only the judged tree by host path will fail with 'not a git repository: /workspaces/vbpub/.git/worktrees/rg55-followups-run-gate'. Mount the common gitdir into that container too, or pass it as GIT_DIR, or run the lane from the main checkout
run-gate: doctor: [OK] mountinfo: namespace alias derivable: /home/vb/volkb79-2/vbpub
run-gate: doctor: [OK] git-config: /tmp writable for GIT_CONFIG_GLOBAL (safe.directory isolation)
run-gate: doctor: [SKIP] lane 'assay-r1' toolchain: environment is the built-in 'bare-host' — its PATH is this machine's, and the lane's own run reports what is missing
run-gate: doctor: [SKIP] lane 'assay-r2' toolchain: environment is the built-in 'bare-host' — its PATH is this machine's, and the lane's own run reports what is missing
run-gate: doctor: [OK] footprint drift: 1 lane(s) within 25% of their live history median peak
run-gate: doctor: [INFO] footprint source: selftest: memory peak is rusage-maxrss (RG-57 bare-host daemon-absent path) — the largest single CHILD process's RSS, not a cgroup read and not a sum across children
run-gate: doctor: [OK] footprint staleness: distilled 0 day(s) ago (<= 30 day threshold)
run-gate: doctor: [OK] profile config: enabled=True daemon='cgprofile-host-daemon' interval=1s damon=True (source: default)
run-gate: doctor: [WARN] profiler daemon: 'cgprofile-host-daemon' not running (start it: cd scripts/cgroup-profiler && ciu up) — every lane falls back to basic (in-lane) sampling
run-gate: doctor: 13 check(s): 8 OK, 2 warning(s), 0 failure(s), 2 skipped (could not determine), 1 info
```

0 failures. The two WARNs are both pre-existing/environmental (the linked-
worktree gitdir note, RG-21 — not this package's concern; and the
profiler daemon simply not running in this devcontainer, expected — it
is exactly what drives `selftest`'s own `rusage` path). The `[INFO]`
confirms C4/RG-57's own disclosure design is live end-to-end.

## Docs disposition (C5, session 2 + session 3's own additions)

| Item | File | Status |
|---|---|---|
| RG-61 #1 New `R-30c` | SPEC.md | done (`5b80c024`) |
| RG-61 #2 `R-30` status-line count | SPEC.md | done (`5b80c024`) |
| RG-61 #3 RG-51 stale narrative | SPEC.md | done (`5b80c024`) |
| RG-61 #4 `[profile]`/`[footprint]` schema | CONSUMERS.md | done (`5b80c024`) |
| RG-61 #5 real footprint transcript | CONSUMERS.md | done (`02707e30`, this session) |
| RG-61 #6 `[Unreleased]` header | CHANGES.md | done (`5b80c024`) |
| RG-61 #7 `usage()`/R-43g-h | run-gate.py/SPEC.md | done (`5b80c024`) |
| RG-61 #8 commit hashes on FIXED entries | KNOWN_ISSUES_TODO_BACKLOG.md | done (`5b80c024`) |
| C4-deferred: `R-43b` stale line | SPEC.md | done (`5b80c024`) |
| C4-deferred: new `R-43i` | SPEC.md | done (`5b80c024`) |
| `tools/canary-run.sh` staleness (found this session, not a checklist item) | tools/canary-run.sh | done (`7539a44e`) |

## E-002 telemetry (this session)

Re-orientation (~10 calls): worktree/tip verification (including the
environment-cwd discrepancy noted in the LOG — the ambient "Primary
working directory" tag pointed at the WRONG worktree, `.worktrees/
rg55-run-gate-client`; every command this session used an explicit `cd`
into the correct one instead), BRIEF-2/HANDOFF/controller-log reads, host-
load re-check. `selftest` cycle (~35 calls): first RED (the `resource`
import gap), fix + targeted rerun + commit, second RED (the wave-diff
coverage gap — reading `resolve_self_container_id`/`start_bare_host_
profiling`/`run_bare_host_lane`'s own uncovered lines directly rather than
guessing, designing and writing 13 tests, local coverage verification,
commit), third run GREEN. Footprint capture (~5 calls): `footprint
--write`, `CONSUMERS.md` edit (with a self-caught inconsistency — an
early draft normalized the store/manifest paths while claiming
"verbatim"; caught and fixed before committing), commit, `doctor` capture.
`assay-r1`/`assay-r3` (~10 calls): r1 launch/verdict, r3's `--base`
refusal (a genuine finding — R-35 applies to it too, not just `selftest`),
the `series_stats` canary staleness (reading the actual current source
before touching the script, confirming the target test's field coverage
before re-anchoring), fix, local canary verification, commit, real r3
rerun GREEN. `assay-r2` check (~3 calls): `pgrep`/`/proc/<pid>/cwd`/
`docker ps` confirmed occupied by P2's own resume. Records (~8 calls):
LOG entries, this REPORT, BRIEF-3. Total this session: comfortably under
the ~90-call hard ceiling; cutting here because `assay-r2` genuinely
cannot proceed (occupied), not because of budget.

**What was missing from BRIEF-2/the handoff that this session had to
re-derive**: neither document anticipated the `selftest`/`assay-r3` gates
actually FAILING on first run (BRIEF-2 explicitly says "none have run yet
this session" and treats the `--base main` wave-wide-diff fact as a
finding to REPORT, not a coverage gap to expect) — both RED verdicts were
genuine, previously-undiscovered defects in already-committed code
(C4's own coverage gap; a stale canary from an EARLIER package, P2). Both
are now fixed and verified rather than left for a future session to
rediscover.

## Status (superseded by session 4 below for round-1 repairs)

Everything this package's handoff asked for is done EXCEPT `assay-r2`.
Tip `7539a44e`, working tree clean. Continuation: `run-gate-WAVE-RG55-
P4-BRIEF-3.md`.

---

# Session 4 — round-1 repair set (RW-43)

Fresh successor from `run-gate-WAVE-RG55-P4-REVIEW-round1.md` (ACCEPT-
conditional B1–B4) and controller ruling RW-43. Starting tip `0bb3bbeb`.

## Round-1 repairs table

| finding | commit | one-command verification |
|---|---|---|
| B1 — rusage path took numbers from EVERY reaped child, not the lane's own | `a1cebacf` | `python3 -m pytest tests/test_run_gate.py -k "BareHostProfilingWiring or test_bare_host_rusage_run_makes_write_stop_refusing" -q` (16 passed) |
| B2 — no oracle pinned the rusage arithmetic | `8c5af489` | `python3 -m pytest tests/test_run_gate.py -k "TestBareHostRusageArithmetic or test_profile_session_recorded_only_on_the_daemon_path" -q` (6 passed) |
| B3 — container path could act on an exec-written (foreign) inflight record | `05193f44` | `python3 -m pytest tests/test_run_gate.py -k "TestInflightRecordDecisions" -q` (49 passed) |
| B4 — doctor's profiler-daemon WARN named only the wrong fallback | `9489bb6d` | `python3 -m pytest tests/test_run_gate.py -k "TestDoctorProfilerCheck" -q` (8 passed) |
| B1's own wave-diff coverage gap (KeyboardInterrupt/wait4 `BaseException` branch, found by selftest itself) | `50684f2c` | `python3 -m pytest tests/test_run_gate.py -k "wait4_interrupted" -q` (1 passed) |

Non-blocking items S1 (rusage caveat on the live footprint/history lines),
S2/RG-59 (narrow the daemon-absent match to docker's own exec failure), S3
(the circular `test_killed_client_leaves_the_record_on_disk`), S4 (the
stale ~8136 comment), S5 (RG-61 item-8 test counts) were **NOT reached
this session** — see "What was not done" below. `--assay-r2` was out of
this dispatch's scope (controller-scheduled separately, RW-42/RW-43).

## Mutant table (B2, planted with `cp` backup/restore against tip `8c5af489`, each restored before the next)

| mutant | location | result |
|---|---|---|
| M2 `* 1024` → `* 1` (KiB→bytes) | `run-gate.py:2050` | KILLED — `test_exact_arithmetic_from_a_known_rusage` |
| M3 cpu drops `ru_stime` | `run-gate.py:2033` | KILLED — `test_exact_arithmetic_from_a_known_rusage` |
| N4 boolop `or`→`and` in the mode/None guard | `run-gate.py:2023` | KILLED — `test_ru_is_none_never_fabricates_a_profile` (raises `AttributeError` on `None.ru_utime`) |
| N13 exec-record compare `==`→`!=` | `run-gate.py:7417` | KILLED — `test_profile_session_recorded_only_on_the_daemon_path` |
| M1 before/after swap (`ru_before`↔`ru_after`) | N/A | STRUCTURALLY ELIMINATED — B1 removed `ru_before`/`ru_after` entirely; there is exactly one `ru`, read once from `os.wait4()`. Nothing to swap. |

Also planted (B3): the foreign-record guard's `!=`→`==` at
`run-gate.py:6756` — KILLED by all three of
`test_dry_run_refuses_a_record_written_by_the_exec_runner`,
`test_live_run_refuses_a_foreign_record_and_runs_fresh_instead`,
`test_fresh_refuses_to_remove_a_foreign_record` (confirmed and reverted).

## The `["true"]`-lane probe (B1's own acceptance bar)

Reviewer's own probe, reproduced in a throwaway project (this environment,
`/tmp/.../scratchpad/b1-probe`), `RUN_GATE_PROFILE=on`, real docker on
PATH, real `resolve_self_container_id` (no mock):

```
"resources": {"memory": {"peak_bytes": 37408768, "source": "rusage-maxrss"}, ...}
```

**This number (~35.7 MiB) is close in magnitude to the OLD bug's own
number (37761024/37261312 bytes) in the reviewer's environment, and does
NOT meet the dispatch prompt's literal "a few hundred KiB at most" bar in
THIS environment.** This was investigated, not waved past:

- Three independent process-creation mechanisms (`subprocess.Popen`,
  `os.posix_spawn`, raw `os.fork()+os.execvp()`) were probed directly,
  bypassing run-gate entirely: an artificial "small parent" (~11 MiB
  resident) forking `/bin/true` measured `ru_maxrss` ≈ 11 MiB for the
  child; the SAME fork with a "big parent" (300 MiB allocated first)
  measured `ru_maxrss` ≈ 311–318 MiB for the SAME trivial child, on ALL
  THREE mechanisms identically.
- This proves the floor is the PARENT's (run-gate.py's own) resident
  memory at the moment of spawn, not an implementation bug in the B1
  rewrite — it is a Linux kernel/fork() characteristic (COW page-table
  inheritance interacting with `wait4()`'s hiwater-rss accounting for a
  short-lived child), reproducible with zero run-gate code involved.
- **The fix is still a real, large improvement, proven differentially**:
  the SAME `["true"]` probe re-run with the lane's argv replaced by
  `python3 -c "d=bytearray(200*1024*1024)"` reported a footprint peak of
  **209 MiB** for a 200 MiB request (the ~9 MiB gap is the interpreter's
  own overhead) — the number tracks the LANE's real usage proportionally
  above run-gate's own floor, which the OLD `RUSAGE_CHILDREN` bug never
  did (it was a flat, unbounded-over-the-process-lifetime floor
  regardless of the lane). The old bug also accumulated monotonically
  across every child reaped in one run-gate invocation (docker inspect,
  `ctl version`, git, …); the new number is independent per lane run,
  bounded to THIS process's own resident size, never inflated by an
  unrelated sibling child.
- **New finding for the controller/round-2 reviewer**: RW-43's ruling text
  ("exact, no baseline arithmetic") did not anticipate this residual
  floor. Whether it warrants a further repair (e.g. disclosing the floor,
  or a design change such as forking earlier/lighter) is a decision this
  session did not make unilaterally — flagged here rather than either
  silently claiming "fixed" or unilaterally redesigning past RW-43's
  explicit mechanism.

## Gate verdicts this session (7 selftest attempts, 1 assay-r1 attempt, 1 assay-r3 attempt — full detail below)

- **`assay-r3` (bare): PASS**, first attempt, exit 0. `canary: 2 rejected,
  0 survived`; `duration_stats`/`series_stats` canaries both verified
  rejected. `run-gate: lane 'assay-r3' exit 0`.
- **`selftest` (bare): RED, 7 attempts, none clean** — but NOT a B1–B4
  regression. Full account:
  - Attempt 1: RED on `TestStallEndToEnd::test_a_moving_lane_is_never_stopped`
    (a 1-second `stall_timeout` timing test) — passes in isolation
    (`python3 -m pytest tests/test_run_gate.py::TestStallEndToEnd::test_a_moving_lane_is_never_stopped -q` → 1 passed).
  - Attempt 2: RED on 4 `TestExecModeMutex` tests, all
    `IsADirectoryError: [Errno 21] Is a directory:
    '/tmp/run-gate-exec-myproj-dev1-<pid>-runner.lock'`.
  - Investigation: `SHARED_LOCK_DIR = "/tmp"` (`run-gate.py:168`) is a
    plain, HOST-WIDE path — not scoped per worktree/project — and
    `find /tmp -maxdepth 1 -name "run-gate-exec-*-runner.lock" -type d`
    found **493 such stale DIRECTORIES**, dated across many days back to
    **Sep 3** (long before this session), none created by this package.
    A directory at that path is NEVER a legitimate lock (the code always
    `os.open()`s a plain file there) — these are pure leftover corruption
    from some other invocation, on some other day, of the SAME shared
    fixture name this test suite (copied into every RG-55 worktree) uses.
    All 493 removed (`rmdir`, all empty, nothing destructive).
  - `ps aux` at the time showed OTHER `run-gate.py`/`pytest` processes
    actively running concurrently on this SAME shared host (P1's `r2`,
    P2's `assay-r2` resume, and at least two independent `python3 -m
    pytest` processes belonging to neither this session nor any command
    it started) — confirmed via pid/cwd, matching the controller log's
    own dispatch table (P1/P2/P6/P7 all active this window).
  - Attempt 3: pytest **fully green** (1121 passed, 3 skipped, ZERO
    failures) — the coverage judge caught a real, separate B1-introduced
    gap instead (see the coverage-gap commit above). This attempt proves
    the CODE is correct; the intermittent failures are a contention
    artifact, not a logic defect.
  - Attempts 4–7 (after the coverage fix): RED again, `TestExecModeMutex`
    each time, a DIFFERENT subset of its tests and a DIFFERENT pid each
    time (2585135, then clean-and-retry pids in later attempts) — the
    changing failure set across attempts is itself evidence of a race,
    not a deterministic break.
  - **Isolated reproduction, proving this is NOT selftest-run-order
    dependent**: `python3 -m pytest tests/test_run_gate.py -k
    "TestExecModeMutex" -q` run BY ITSELF, immediately after clearing
    every stale lock directory, STILL hit the identical
    `IsADirectoryError` at a brand-new pid — i.e. it reproduces even with
    no other test in this suite running before it, which is only
    possible if something OUTSIDE this pytest process (another
    concurrently-running copy of this same test suite, elsewhere in the
    estate, sharing this host's `/tmp`) is racing it.
  - **This is unrelated to run_bare_host_lane, finish_bare_host_profiling,
    resolve_inflight, or cmd_doctor** — `TestExecModeMutex` exercises
    `acquire_exec_lock`/R-41 exec-mode internal mutual exclusion, code
    none of B1–B4 touch.
- **`assay-r1` (`--base rg55-run-gate-client`): RED, 1 attempt** — same
  root cause: its own baseline runs the identical `python3 -m pytest
  tests -q --cov=. ...` command (`.assay/progress-r1.jsonl`:
  `"event":"command_finished","outcome":"FAIL","reason_code":
  "COMMAND_FAILED","returncode":1`), so it inherits the same host-wide
  `/tmp` contention. Not re-attempted a second time given the call budget
  already spent establishing the selftest pattern; the same fix (wait for
  quieter host / retry) applies.

**Recommendation for the controller**: re-run `selftest` and `assay-r1`
when the host is less contended (or after the concurrently-running
sibling packages' own test/mutation runs finish); consider filing a
backlog entry against run-gate's OWN test suite — `SHARED_LOCK_DIR` being
literally `/tmp` combined with several tests' fixed, non-pid-qualified
container-name fixtures (`"myproj-dev1-runner"` et al.) is a genuine
cross-process collision hazard whenever two copies of this suite run
concurrently on one host, which this estate now does routinely
(RW-39/RW-42's own concurrency rulings). This is a pre-existing hazard,
not introduced by P4.

## What was NOT done this session (flagged, not silently dropped)

- **Non-blocking S1/S2(RG-59)/S3/S4/S5**: not reached. The gate-
  verification cycle above (7 selftest attempts diagnosing a genuine,
  non-obvious environmental hazard) consumed the session's budget past
  the point a further commit round could be safely attempted and
  verified. Left for the next session/round.
- **`run-gate.footprint.json`/`CONSUMERS.md` regeneration**: the dispatch
  prompt asked for this "from a fresh clean-tree selftest PASS +
  `footprint --write`". Since `selftest` did not reach a clean PASS this
  session (see above — environmental, not a regression), the tracked
  footprint manifest and CONSUMERS transcript **were NOT regenerated** and
  still reflect PRE-B1 numbers. This is real outstanding work, not an
  oversight: doing it against a RED selftest would either fabricate a
  "clean" transcript or bake in whatever partial/contaminated state a
  failed run left behind, neither of which this package's own honesty
  standard (contract Sec 1.7) allows. **Next session: re-run selftest
  clean first, then regenerate.**
- **Decision ask for the controller**: the residual rusage-floor finding
  above (run-gate's own resident memory sets a floor on a short-lived
  bare-host lane's reported peak) was discovered during verification, not
  anticipated by RW-43. No code change was made for it beyond what RW-43
  already specified — flagged for a ruling, not acted on unilaterally.

## Status

Tip **`50684f2c`** on branch `rg55-followups-run-gate`. Working tree
clean. B1–B4 all repaired, committed, and individually verified via
targeted tests (every targeted run in the table above is GREEN). The
WHOLE-SUITE gates (`selftest`, `assay-r1`) are currently RED for reasons
established above to be environmental and pre-existing, not caused by
this package's commits; `assay-r3` is GREEN. Continuation:
`run-gate-WAVE-RG55-P4-BRIEF-4.md`.

---

# Session 5 — RW-46 (fresh successor: lock-dir isolation, rusage floor, S1-S5, gates)

Fresh successor from `run-gate-WAVE-RG55-P4-BRIEF-4.md` and controller
ruling RW-46. Starting tip `00a79de4`. Final tip `d444b246`.

## RW-46a — test-suite lock-dir isolation + root cause, with evidence

**Root cause of the 493 stale `run-gate-exec-*-runner.lock` DIRECTORIES**
(session 4's own finding): `TestResourceAdmission::test_unusable_lock_
path_is_infra_failure_not_traceback` (RG-20 precedent) and
`TestExecModeMutex::test_unusable_lock_path_is_infra_failure_not_
traceback` each deliberately `(lock path).mkdir()` a DIRECTORY at the
lock path — to prove `acquire_*_lock`'s `OSError`-not-traceback behavior
(`_open_lockfile` only ever `os.open()`s a plain FILE; a pre-existing
directory there makes that `open()` raise `IsADirectoryError`, which the
caller correctly turns into `fail_infra`, exit 3) — **with no cleanup**.
Both tests share a container name (`myproj-dev1-<pid>-runner`) that
includes the RUNNING PID, so every green run of either test, across every
RG-55-era invocation since Sep 3, left exactly one NEW directory behind
forever (a directory is never removed by anything else in this codebase —
confirmed by grep: no `mkdir`/`os.mkdir`/`Path.mkdir` touches a
`run-gate-{exec,shared}-*.lock` path anywhere else in `run-gate.py` or
`tests/test_run_gate.py`). Located by reading `tests/test_run_gate.py`
directly (`grep -n "run-gate-exec-\|SHARED_LOCK_DIR\|TestExecModeMutex"`)
— no speculation, the leak is visible in the test source itself.

**Fix (both parts of the ruling):**
1. **Isolation.** New `LOCK_DIR_ENV_VAR = "RUN_GATE_LOCK_DIR"` +
   `_lock_dir()` helper in `run-gate.py`, re-reading
   `os.environ.get(LOCK_DIR_ENV_VAR, SHARED_LOCK_DIR)` on every call
   (never cached) — the same override shape as the pre-existing
   `RUN_GATE_CGROUPFS_ROOT`/`RUN_GATE_PROC_ROOT` test/namespace levers,
   chosen deliberately because it reaches BOTH in-process `run_gate.
   main()` calls and subprocess `run_tool()`/`_TOOL_INVOKE` invocations
   without a module-attribute monkeypatch (which would be unsafe: this
   test file and `test_coverage_gate.py` each load their own tool via a
   fresh `importlib.util.spec_from_file_location` at THEIR OWN collection
   time, so a patch against one loaded module object would not reach a
   different copy's own global lookups). New `tests/conftest.py`: autouse
   `isolate_shared_lock_dir` fixture points `RUN_GATE_LOCK_DIR` at a
   fresh `tmp_path_factory.mktemp(...)` per test. All 9 hard-coded
   `Path("/tmp") / f"run-gate-{...}"` constructions in
   `TestResourceAdmission`/`TestExecModeMutex` (which pre-open "holder"
   fds to simulate contention) now route through a matching
   `_shared_lock_dir()` test helper — required, or the test's own holder
   and the code's own lock would sit on two different files.
2. **Root cause.** Both offending tests wrapped in `try/finally:
   <path>.rmdir()`. Regression proof, direct and estate-wide:
   `TestExecModeMutex::test_a_real_lane_run_never_touches_host_tmp`
   snapshots real `/tmp` before/after a REAL in-process lane run and
   asserts byte-identical, plus a positive check that the isolated dir
   DID receive the lock (isolation isn't silently a no-op). The
   `isolate_shared_lock_dir` fixture's own teardown additionally asserts,
   after EVERY test in the whole suite, that no directory-shaped entry
   survives under the isolated dir — a permanent, suite-wide guard against
   this exact defect class recurring anywhere, present or future, not
   just at the two known sites.
3. **`doctor` INFO check** (new section 8): counts
   `run-gate-{exec,shared}-*.lock` entries under `_lock_dir()` older than
   1 day, names how many are directories (always corruption), never
   deletes. **Live confirmation this session** (`./run-gate.py doctor`,
   captured verbatim below): `2603 entries older than 1 day under /tmp,
   306 as a DIRECTORY` — real, growing evidence that OTHER RG-55
   packages' own un-fixed test suites are still writing to host `/tmp`
   concurrently (this package's own suite no longer contributes, per the
   regression test above).

One-command verification: `python3 -m pytest tests/test_run_gate.py -k
"TestExecModeMutex or TestResourceAdmission or TestDoctorStaleLockCheck" -q`
→ 34 passed.

## RW-46b — rusage `memory.floor_bytes`/`memory.peak_at_floor`

`os.wait4()`'s `ru_maxrss` (RW-43/B1) is exact for the lane's OWN child —
nothing left in the arithmetic to fix. But fork+exec's COW page-table
inheritance means a short-lived child's own high-water RSS can never fall
below run-gate's OWN resident size at the moment it forked the child
(session 4's own proof, carried forward: an artificial small parent
~11 MiB resident forking `/bin/true` measured `ru_maxrss` ~11 MiB for the
child; the SAME fork with a 300 MiB parent measured ~311-318 MiB for the
SAME trivial child, on `Popen`/`posix_spawn`/raw `fork()+exec()`
identically). New `_self_rss_bytes()` reads `/proc/self/statm` (resident
pages × page size, honors `RUN_GATE_PROC_ROOT`) immediately before
`Popen()` on the rusage path only, threaded into `finish_bare_host_
profiling` as `memory.floor_bytes` (new optional parameter, default
`None` — every pre-existing call site unaffected). `memory.peak_bytes` is
UNCHANGED; new `memory.peak_at_floor`: `true` when `peak_bytes <=
floor_bytes`, `false` when the child's own footprint exceeded it, `null`
only when `floor_bytes` itself is unreadable (never fabricated).
Propagated into the footprint manifest (per-lane, from the most-recently-
profiled entry — a lane's floor-bound-ness can change run to run) and
disclosed everywhere a rusage peak is shown: `print_footprint_line`
(S1: previously showed NEITHER caveat at all), `_fmt_resource_stats`
(`history`, same S1 gap), `_fmt_footprint_row`, and a new doctor INFO
block. SPEC `R-43i`/`R-44a`, `LANE-AUTHORING.md`, `CHANGES.md` all
explain the mechanism.

One-command verification: `python3 -m pytest tests/test_run_gate.py -k
"TestBareHostRusageArithmetic or TestSelfRssBytes or TestBareHostProfilingWiring or TestFootprintDoctorChecks" -q`
→ 45 passed.

## S1-S5 table

| finding | what changed | one-command verification |
|---|---|---|
| S1 — rusage caveat missing from the live `footprint` line and `history` | `print_footprint_line`/`_fmt_resource_stats` now print `[source: rusage-maxrss]` (folded into the RW-46b commit, designed together) | `python3 -m pytest tests/test_run_gate.py -k "TestBareHostProfilingWiring or TestFootprintDoctorChecks" -q` |
| S2/RG-59 — daemon-absent match over-fired on a RUNNING daemon's own crash | `_stderr_names_daemon_not_running` narrowed to docker's reserved exit codes (125/126/127) or a `docker:`/`Error response from daemon:` stderr prefix | `python3 -m pytest tests/test_run_gate.py -k "ProfilerClient" -q` (26 passed) |
| S3 — `test_killed_client_leaves_the_record_on_disk` was circular | rewritten around a REAL client subprocess killed mid-exec (mirrors `TestReattachAcrossADeadClient`); proven to fail with the write reverted | `python3 -m pytest tests/test_run_gate.py::TestExecLaneInflightRecord -q` (4 passed) |
| S4 — stale comment at `run-gate.py:8136` stated the opposite of RG-57 | comment corrected; confirmed no test pins the stale wording | `grep -n "categorically unprofiled" tests/test_run_gate.py` (no hits) |
| S5 — RG-61 item-8 test counts stale | recounted at the current tip (`TestBareHostStallTimeoutWarning`=3, `TestBareHostProfilingWiring`=17, `TestExecLaneInflightRecord`=4, `TestResolveSelfContainerIdDirectBranches`=6); `KNOWN_ISSUES_TODO_BACKLOG.md` RG-57/58/60 + this LOG corrected, RG-61's own prior correction annotated rather than overwritten | manual `awk`-bounded recount, shown in the LOG |

## The regenerated footprint transcript (item 4)

```console
$ ./run-gate.py footprint --write
run-gate rev 42 — lane resource footprint
store: /workspaces/vbpub/.worktrees/rg55-followups-run-gate/run-gate-project/.run-gate/history.json
manifest written: /workspaces/vbpub/.worktrees/rg55-followups-run-gate/run-gate-project/run-gate.footprint.json
  LANE                 RUNS  PEAK(med/max)            +BASE    HOT p90   CORES    STALL   DURATION
  assay-r1                2    265 MiB/266 MiB            -          -    0.59        -     179.5s [source: rusage-maxrss]
  assay-r3                3    181 MiB/198 MiB            -          -    0.69        -      15.9s [source: rusage-maxrss]
  selftest                3    268 MiB/286 MiB            -          -    0.59        -     157.6s [source: rusage-maxrss]
```

Captured on a clean tree immediately after the FIRST green `selftest`
this session (`git status --porcelain` empty, tip `9159c58d` at capture
time). `peak_at_floor`: `false` for `selftest` (its most-recent run is
from this session, post-RW-46b); `null` for `assay-r1`/`assay-r3` (their
most-recently-profiled history entries predate this session's
`floor_bytes` field — contract Sec 1.7: absent means unknown, never
fabricated as `false`). `CONSUMERS.md`'s own transcript replaced verbatim
with this capture.

## `./run-gate.py doctor` (captured live this session)

```
run-gate: doctor: [OK] docker: /usr/bin/docker
run-gate: doctor: [OK] lane argv[0] (RG-34): 3 container command lane(s): every argv[0] is {worktree}-anchored, absolute, or a bare command name
run-gate: doctor: [OK] git: worktree /workspaces/vbpub/.worktrees/rg55-followups-run-gate
run-gate: doctor: [WARN] host-lane git view (RG-21): ... (linked worktree, pre-existing, unrelated to P4)
run-gate: doctor: [OK] mountinfo: namespace alias derivable: /home/vb/volkb79-2/vbpub
run-gate: doctor: [OK] git-config: /tmp writable for GIT_CONFIG_GLOBAL (safe.directory isolation)
run-gate: doctor: [SKIP] lane 'assay-r1' toolchain: environment is the built-in 'bare-host' — its PATH is this machine's, and the lane's own run reports what is missing
run-gate: doctor: [SKIP] lane 'assay-r2' toolchain: environment is the built-in 'bare-host' — its PATH is this machine's, and the lane's own run reports what is missing
run-gate: doctor: [OK] footprint drift: 3 lane(s) within 25% of their live history median peak
run-gate: doctor: [INFO] footprint source: assay-r1, assay-r3, selftest: memory peak is rusage-maxrss (RG-57 bare-host daemon-absent path) — that lane's own child's peak RSS (os.wait4), not a cgroup read and not a sum across children
run-gate: doctor: [OK] footprint staleness: distilled 0 day(s) ago (<= 30 day threshold)
run-gate: doctor: [OK] profile config: enabled=True daemon='cgprofile-host-daemon' interval=1s damon=True (source: default)
run-gate: doctor: [WARN] profiler daemon: 'cgprofile-host-daemon' not running (start it: cd scripts/cgroup-profiler && ciu up) — container/exec lanes fall back to basic (in-lane) sampling, bare-host lanes to coarse rusage accounting (R-43i)
run-gate: doctor: [INFO] stale coordination locks: 2603 entries older than 1 day under /tmp, 306 as a DIRECTORY (never legitimate — corruption, see above) — report only, doctor never deletes
run-gate: doctor: 14 check(s): 8 OK, 2 warning(s), 0 failure(s), 2 skipped (could not determine), 2 info
```

No lane in this project is floor-bound at this snapshot, so the
"footprint peak-at-floor" INFO block (also new this session) does not
appear here — see `TestFootprintDoctorChecks` for it exercised directly.

## Gate verdicts (verdict read in a SEPARATE step every time)

- **`./run-gate.py selftest` (bare): PASS** — 1143 passed, 4 skipped, 0
  failures; `diff-coverage OK: 1066/1066 changed executable lines covered
  (100.0%); branches 394/394 taken`; `lane 'selftest' exit 0`. Second
  attempt (first attempt's own new-code diff-coverage gaps closed by two
  intermediate commits — pytest itself was green on BOTH attempts).
- **`./run-gate.py --base rg55-run-gate-client assay-r1`: PASS** — third
  attempt. Attempts 1-2 failed on two DIFFERENT pre-existing,
  environment-/order-sensitive tests unrelated to this package's diff
  (`TestEstateBudgetTimeoutPairing::test_estate_pairing_sweep_is_alive`,
  an order-dependent flake confirmed non-deterministic via 3 isolated
  reruns of just that class — PASS/PASS/FAIL — and traced to
  `pytest-randomly` 5.0.0 being active with no seed pinned; then
  `TestHistoryEligibilityGuard.test_tree_state_is_sampled_before_the_
  lane_not_after`, an ordinary `4.001 == 4.0` timing flake under load).
  Filed `KNOWN_ISSUES_TODO_BACKLOG.md` RG-62 for both — root-caused, not
  blindly retried past. `r1: PASS (exit 0)`.
- **`./run-gate.py assay-r3` (bare): PASS** — first attempt, exit 0. Both
  canaries (`median-not-mean`, `median-not-mean-series-stats`) verified
  rejected: `canary: 2 rejected, 0 survived`.
- **`assay-r2`: NOT attempted** — controller-scheduled separately
  (RW-42/RW-43/RW-46), never this package's own call to make.

## Status

Tip **`d444b246`** on branch `rg55-followups-run-gate`, worktree
`/workspaces/vbpub/.worktrees/rg55-followups-run-gate`. Working tree
clean. RW-46a and RW-46b both landed and tested; S1-S5 all closed;
`run-gate.footprint.json`/`CONSUMERS.md` regenerated from a clean
selftest PASS; `doctor` output captured; `selftest`/`assay-r1`/`assay-r3`
all GREEN this session (two unrelated pre-existing flakes diagnosed and
filed as RG-62, not fixed — out of this package's own RG-57..61 scope).
`assay-r2` remains controller-scheduled. Package is DONE modulo `assay-r2`
and reviewer round 2.

## Session 6 — Round-2 repairs (RW-51: B5 open, S6-S11 non-blocking)

| finding | commit | one-command verification |
|---|---|---|
| B5 — `meta.expected` missing `source` (contract §3a) | `07ae3a47` | `python3 -m pytest tests/test_run_gate.py -k TestFootprintProfileMetaExpected -q` |
| S7 — exec/container `runner` stamp value unasserted (mutant MH) | `459d07a5` | `python3 -m pytest tests/test_run_gate.py -k "test_record_exists_when_exec_begins_and_cleared_after or test_the_record_names_the_container_commit_and_tree" -q` |
| S8 — footprint `[source:…]` note + history any/all unasserted (mutants MN, MO) | `459d07a5` | `python3 -m pytest tests/test_run_gate.py -k "test_rusage_source_note_appears_only_when_the_summary_says_so or test_lane_stats_source_and_floor_flags_are_any_not_all" -q` |
| S9 — host-/tmp test flakes on a concurrent foreign writer | `0680d6d8` | `python3 -m pytest tests/test_run_gate.py -k test_a_real_lane_run_never_touches_host_tmp -q` |
| S10 — `Popen.returncode` never set after `wait4` (ResourceWarning) | `cf043dda` | `python3 -m pytest tests/test_run_gate.py -k test_returncode_is_set_after_wait4_reaps_the_child -q` |
| S11 — RG-59's 126/127 arm mislabels a running-but-broken daemon as "not running" | `89eb7e38` | `python3 -m pytest tests/test_run_gate.py -k "test_docker_reserved_exit_code_names_the_real_cause_even_with_no_recognizable_prefix or test_exit_code_126_127_name_a_broken_daemon_not_a_stopped_one" -q` |
| S6 — manifest/CONSUMERS transcript one generation stale (mixed pre-/post-wait4 rows) | `864f60f3` | `python3 -c "import json; d=json.load(open('run-gate.footprint.json')); print({k: v['peak_at_floor'] for k, v in d['lanes'].items()})"` (expect `False` for all three, not `None`) |

All six mutants/gaps (MH-exec, MH-container, MN, MO, S10's own
ResourceWarning, S11's 126/127 swap) were verified by planting the exact
revert against the worktree's own `run-gate.py` (single-line `sed -i`),
confirming the new/changed test FAILS, then restoring byte-for-byte
(`diff -q` against a pre-edit backup) before the next mutant — never two
mutants live at once, per package (see LOG Session 6 for each result).

### Regenerated transcript (this session, tip `864f60f3`)

```console
$ ./run-gate.py footprint --write
run-gate rev 42 — lane resource footprint
store: /workspaces/vbpub/.worktrees/rg55-followups-run-gate/run-gate-project/.run-gate/history.json
manifest written: /workspaces/vbpub/.worktrees/rg55-followups-run-gate/run-gate-project/run-gate.footprint.json
  LANE                 RUNS  PEAK(med/max)            +BASE    HOT p90   CORES    STALL   DURATION
  assay-r1                4    266 MiB/289 MiB            -          -    0.61        -     179.5s [source: rusage-maxrss]
  assay-r3                5    198 MiB/201 MiB            -          -    0.74        -      15.9s [source: rusage-maxrss]
  selftest                5    285 MiB/286 MiB            -          -    0.56        -     172.3s [source: rusage-maxrss]
```

Full transcript + the corrected prose (why every row is now
`peak_at_floor: false`, never `null`) live in `CONSUMERS.md`'s "The
footprint manifest" section, "Recaptured RW-51/session 6" paragraph.

### Gate verdicts (final, tip `864f60f3`, verdict read in a SEPARATE step)

- **`./run-gate.py selftest` (bare): PASS** — 1149 passed, 3 skipped, 0
  failures; `diff-coverage OK: 1076/1076 changed executable lines covered
  (100.0%); branches 398/398 taken`; `lane 'selftest' exit 0`.
- **`./run-gate.py --base rg55-run-gate-client assay-r1`: PASS** — first
  attempt, exit 0, `commit: 864f60f3d8925d35bc25cf3619c92e2452565b49`.
- **`./run-gate.py assay-r3` (bare): PASS** — first attempt, exit 0;
  `canary: 2 rejected, 0 survived`.

### Status

Tip **`864f60f3`** on branch `rg55-followups-run-gate`. Working tree
clean, all six commits pushed to the branch. B5 and all six non-blocking
findings (S6-S11) closed. `assay-r2` launch condition changed mid-session
by controller ruling RW-52 (no longer gated on P1/P6's container lanes,
memory PSI alone) — see the r2 status note this session appends below
before the final return.

## Session 7 — corrected synthetic-tree R2 survivor triage (RW71-RW73)

### Evidence identity and disclosure

The merge-tip mutation attempt recorded in RW71 was not valid evidence:
assay's first-parent changed-line resolver selected `b72cba31` and found no
changed executable lines, yielding `INCONCLUSIVE/NO_MUTANTS`. RW72 then
constructed the exact-tree non-merge judgment commit
`cd6780ed49ff06c259578b37c7b4e2dd0b7f23f9`, with parent
`fccba080d0d35b9eb536489e8b6e6fd30ff2c52d` and the same tree as final branch
tip `d4c57c1ac631a027d0bdfb6fb0d1219c9f4bca98`; the tree comparison was empty.
This commit was ephemeral assay identity only and is not product history.

The corrected run was launched from `run-gate-project` with HEAD quiet and
host memory PSI full avg10 `0.23%` at launch:

```console
$ nice -n 19 ionice -c 3 ./run-gate.py --base main assay-r2
run-gate: comparison base main (from --base) → --request-base
run-gate: rev 42 | lane assay-r2 | env built-in 'bare-host'
assay-6.1.1.pyz: OK
r2: FAIL/MUTANTS_SURVIVED (exit 1)
  commit: cd6780ed49ff06c259578b37c7b4e2dd0b7f23f9
run-gate: lane 'assay-r2' exit 1
ASSAY_EXIT=1
```

The verdict was read separately from
`.assay/verdict-r2.json`: R0 `PASS`; R2 `FAIL/MUTANTS_SURVIVED`; 57
candidates, 43 killed, 14 survived, 0 equivalent, 0 budget-exceeded, 0
crashed. The completion marker was
`/tmp/rg55-p4-corrected-r2.Mn6ZDQ/DONE.marker` with `ASSAY_EXIT=1`.

### Survivor dispositions

All 14 survivors were executable-behavior oracle gaps, not equivalent
mutants. The focused regression tests below pin the intended behavior. No
executable source was changed; the pending change is tests plus this
evidence, so the branch source tree remains identical to the judged
synthetic tree and the controller-requested fresh R2 is required after the
commit.

| # | survivor | disposition and focused oracle |
|---:|---|---|
| 1 | `run-gate.py:507 True→False` (`flush=True` in the load-time stall warning) | Real immediate-disclosure contract; pinned by `TestBareHostStallTimeoutWarning.test_load_time_warning_is_flushed_for_immediate_disclosure`. |
| 2 | `:1834 True→False` (`capture_output=True` in self-container inspection) | Real subprocess-capture contract; pinned by `TestResolveSelfContainerIdDirectBranches.test_docker_inspect_captures_stdout_and_stderr`. |
| 3 | `:2007 Or→And` (daemon version document fallback) | Real daemon-status disclosure contract; pinned by `TestBareHostProfilingWiring.test_daemon_path_targets_self_id_scope_and_injects_token`. |
| 4 | `:2025 And→Or` (non-boolean baseline guard) | Real unknown-baseline contract; pinned by `TestBareHostProfilingWiring.test_daemon_path_missing_baseline_discloses_unknown`. |
| 5 | `:2065 Lt→LtE` (zero resident-page validation) | Real zero-page boundary contract; pinned by `TestSelfRssBytes.test_zero_resident_pages_is_a_valid_read`. |
| 6 | `:2290 True→False` (`flush=True` in footprint disclosure) | Real immediate-disclosure contract; pinned by `TestFootprintDisclosureLine.test_footprint_line_flushes_after_writing_the_disclosure`. |
| 7 | `:3665 Eq→NotEq` (rusage source flag) | Real any-rusage-series disclosure contract; pinned by `TestHistory...test_lane_stats_source_flag_is_true_for_an_all_rusage_series`. |
| 8 | `:3876 Or→And` (profiled `peak_at_floor` fallback) | Real most-recent-profiled-entry contract; pinned by `TestFootprintManifestBuild.test_peak_at_floor_comes_from_the_most_recent_profiled_entry`. |
| 9 | `:5580 And→Or` (bare-host stall-timeout doctor filter) | Real environment-specific doctor contract; pinned by `TestBareHostStallTimeoutWarning.test_doctor_does_not_warn_for_a_bare_host_lane_without_stall_timeout`. |
| 10 | `:5853 Lt→LtE` (stale-lock cutoff) | Real strict “older than one day” boundary; pinned by `TestDoctorStaleLockCheck.test_lock_exactly_one_day_old_is_not_stale`. |
| 11 | `:7003 True→False` (`flush=True` in foreign-record refusal) | Real immediate refusal disclosure; pinned by `TestInflight...test_fresh_refuses_to_remove_a_foreign_record`. |
| 12 | `:7854 True→False` (`flush=True` in DEVCONTAINER-WIDE disclosure) | Real immediate disclosure; pinned by `TestBareHostProfilingWiring.test_daemon_path_targets_self_id_scope_and_injects_token`. |
| 13 | `:7901 Or→And` (preserve existing profiler warning on wait4 failure) | Real error-preservation contract; pinned by `TestBareHostProfilingWiring.test_getrusage_raising_never_aborts_the_lane`. |
| 14 | `:7946 True→False` (`flush=True` in cleanup-crash warning) | Real immediate cleanup-failure disclosure; pinned by `TestBareHostProfilingWiring.test_cleanup_crash_never_escapes_the_lane`. |

Focused regression, run after the test-only changes and before the fresh R2:

```console
$ python3 -m py_compile run-gate.py tests/test_run_gate.py
$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py -q -k 'load_time_warning_is_flushed or docker_inspect_captures_stdout_and_stderr or daemon_path_targets_self_id_scope_and_injects_token or daemon_path_missing_baseline_discloses_unknown or zero_resident_pages_is_a_valid_read or lane_stats_source_flag_is_true_for_an_all_rusage_series or peak_at_floor_comes_from_the_most_recent_profiled_entry or footprint_line_flushes_after_writing_the_disclosure or doctor_does_not_warn_for_a_bare_host_lane_without_stall_timeout or lock_exactly_one_day_old_is_not_stale or fresh_refuses_to_remove_a_foreign_record or getrusage_raising_never_aborts_the_lane or cleanup_crash_never_escapes_the_lane'
.............                                                            [100%]
13 passed, 1047 deselected in 4.72s
exit 0
```

The pre-R2 test/report commit will be made on `rg55-followups-run-gate`
after this section is recorded. A fresh exact-tree non-merge assay identity
will then be created from that committed branch tree; no commit will be
made while detached. The fresh R2 verdict, final selftest, assay-r1, and
assay-r3 will be appended in the next session section.

## Session 8 — fresh R2 survivor correction and second exact-tree assay

The first controller-requested fresh R2 was launched against synthetic
non-merge commit `63d0633f174b6f2c8f618e443e563deb58c00f2e`, parent
`12e4e150ef9f5ad72198790c15986319fecd0589`, with tree
`56c44350e8048baf3a6c998963b404bd31f4b485`, exactly the committed triage tree.
Its separately read verdict was `FAIL/MUTANTS_SURVIVED`, exit 1: 57
candidates, 56 killed, 1 survived, 0 equivalent, 0 budget-exceeded, and 0
crashed. The sole survivor was `run-gate.py:2065 LtE->Lt`, changing the
`page_size <= 0` guard in `_self_rss_bytes` to `page_size < 0`.

The earlier disposition for line 2065 was incomplete: the existing
`test_zero_resident_pages_is_a_valid_read` exercised `resident_pages == 0`,
not `page_size == 0`. This is a real oracle gap, not an equivalent mutant.
The focused test `TestSelfRssBytes.test_zero_page_size_is_rejected` now
supplies a non-zero resident-page reading while making `os.sysconf` return
zero, and asserts that `_self_rss_bytes()` degrades to unknown. The targeted
regression passed: `2 passed, 1059 deselected in 7.47s`, exit 0, with
`py_compile` and `git diff --check` also exiting 0.

The corrected survivor table is therefore: all 14 original survivors are
real oracle gaps; line 2065 is pinned by both boundary tests, including the
new zero-page-size test. The test and this report/log correction must be
committed on `rg55-followups-run-gate`; a second fresh R2 is required on a new
exact synthetic non-merge tree. No executable source was changed.

## Session 9 — final exact-tree R2 closure and branch transfer

After the page-size correction, the worktree was switched from the detached
assay identity to branch `rg55-followups-run-gate` at
`cd7596e9cd7e8cf64d4435c4d23b12ef636c475e` before this record was extended.
The branch tree is `f673be7b41127e2b72189a450845a873dab4b8df`, exactly the
tree judged by synthetic non-merge commit
`d750e6ad63240046f35bd3fdaa2459f8c98af243`, whose parent is the branch tip.
No commit was made while detached.

The final exact-tree R2 command was run from `run-gate-project`:

```console
$ nice -n 19 ionice -c 3 ./run-gate.py --base main assay-r2
```

The persistent launcher was `setsid nohup`; wrapper PID `2925691`, gate PID
`2925693`, and assay PID `2925786`. Launch PSI was `full avg10=0.00`, under
the required `<=5` threshold. The scratch/log directory was
`/tmp/rg55-p4-final-r2-relaunch.msXGx0`; the assay ran bare-host with two
jobs and no mutation container. It used `--resume --progress`, and the
current run reported `resumed_total=0` before selecting all 57 candidates.

The wrapper exited normally. Its explicit marker was read separately:
`ASSAY_EXIT=0`. The verdict artifact was then read separately and reported:

```text
commit=d750e6ad63240046f35bd3fdaa2459f8c98af243
assay_version=6.1.1
R0=PASS
R2=PASS
candidate_count=57 killed=57 survived=0 equivalent=0
budget_exceeded=0 crashed=0
jobs=2 mode=changed_lines
base=a921100db0897a37390273973451737584bf4aed
base_resolution=merge-base
```

This closes the one remaining page-size survivor. All 14 original survivor
dispositions remain genuine oracle gaps, and line 2065 is now covered at
both boundaries by `test_zero_resident_pages_is_a_valid_read` and
`test_zero_page_size_is_rejected`; the final R2 killed every candidate.
The earlier wrong-directory launch was not a verdict: its log recorded
`ionice: failed to execute ./run-gate.py: No such file or directory` and
`ASSAY_EXIT=127`, with no verdict artifact. The successful relaunch from the
project directory is the valid evidence above.

## Session 10 — final branch gates and review handoff

The controller ran the final gates on clean branch tip
`97d294c034d6b54c7d99a10d1734b2b23ef79b8c` after the exact-tree R2 records
were transferred. The reported results were:

| gate | result |
|---|---|
| `./run-gate.py selftest` | exit 0; 1159 passed; coverage `208/208` changed executable lines and `66/66` branches |
| `./run-gate.py assay-r1` | exit 0; verdict `PASS` |
| `./run-gate.py assay-r3` | exit 0; 2 canaries rejected, 0 survived |

The separate attempted command `./run-gate.py --base main assay-r3` was a
command-lane refusal with exit 2, not a gate run, and is excluded from the
gate verdicts. No implementation source changed after the final exact-tree
R2. This package is ready for a fresh Sol xhigh adversarial review only;
there was no reviewer dispatch, merge, or release.

## Session 11 — round-3 B1/B2 repair follow-up

The current branch tip before this follow-up was `b4fb7b1b` on
`rg55-followups-run-gate`, with a clean worktree. The round-3 blockers were
rechecked against the current tree. The behavioral B1 repair is present from
`28bc3feb`: `run_exec_lane` puts the synchronous `Popen` attempt inside the
cleanup boundary and clears the pre-spawn record in the outermost `finally`,
while a client killed after a child has started still leaves the record for
reconciliation. The existing regression test exercises the synchronous
failure and asserts both the propagated lane failure and record removal; the
existing real client-death test continues to assert record survival.

The adopter-facing B2 contract is also current: `SPEC.md` R-43/R-43i and
`CONSUMERS.md` describe `os.wait4(pid, 0)` on the lane's own child,
`ru_maxrss * 1024` Linux bytes, and the absence of cgroup/pressure/DAMON/events
data in the fallback. The relevant test docstrings now identify
`getrusage(RUSAGE_CHILDREN)` only as the historical rejected algorithm. The
`CONSUMERS.md` sweep found no `getrusage` or `RUSAGE_CHILDREN` wording; the
remaining SPEC/test matches are explicitly historical explanations.

Focused serial verification for this follow-up:

| check | result |
|---|---|
| `git diff --check` | exit 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile run-gate.py tests/test_run_gate.py` | exit 0 |
| focused B1/B2 pytest selector | 6 passed, 1056 deselected in 4.29s; exit 0 |
| current overview/test `rg` sweep | only explicitly historical rejected-algorithm matches; `CONSUMERS.md` clean |

The prior committed mutation evidence and final exact-tree R2 evidence remain
preserved in Sessions 7–10. They do not certify this post-repair tree. A new
`assay-r2` is required after this code repair; no package-ready or release
claim is made here. This follow-up did not launch a whole-package gate,
mutation campaign, merge, or release.
