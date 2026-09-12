# run-gate-WAVE-RG55-P4 — REPORT (partial: everything except `assay-r2`)

Package P4 (run-gate follow-ups: RG-57, RG-58, RG-59, RG-60, RG-61) of the
RG-55 wave, third track (RW-27). Written at the end of session 3, tip
`7539a44e` on branch `rg55-followups-run-gate`. **This REPORT is not yet
complete**: `assay-r2` (mutation) has not run — occupied by P2's own
resume for the whole of this session (see the LOG's "`assay-r2` —
occupied, not attempted" section and `run-gate-WAVE-RG55-P4-BRIEF-3.md`).
Everything else the handoff's own Records section asks for is here.
Claim only what was run — every line below is either a command this
session actually executed (verbatim output, or the exact assertion from
it) or explicitly marked as inherited from an earlier session's own LOG
entry (cited by commit hash, not re-derived).

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

## Status

Everything this package's handoff asked for is done EXCEPT `assay-r2`.
Tip `7539a44e`, working tree clean. Continuation: `run-gate-WAVE-RG55-
P4-BRIEF-3.md`.
