# Changelog — run-gate

All notable changes to `run-gate.py` and its estate adoption are recorded
here. The in-file `__revision__` is the drift marker: estate sweeps compare
it, and any consumer holding a COPY (not a symlink) must re-copy when it
moves. Normative behavior lives in SPEC.md; entry-by-entry rationale lives in
KNOWN_ISSUES_TODO_BACKLOG.md and git history.

## [Unreleased]
<!-- hand-written ahead of release; cmru's generator will produce the real dated entry for this range at release time. Fold into the dated section BY HAND the moment that release is cut -- cmru's generator never clears this block itself, and this file's own 2026-09-09 comment records one past instance of that being written down but not carried out. Verified empty as of 2026-09-11's release; NOT empty as of this note (RG-61, 2026-09-12) -- 100+ lines accumulated since, all of it the RG-55 wave's own P1/P2 base package (rev 41) plus this P4 follow-up package (rev 42, RG-57/58/59/60/61 below). `__revision__` 41 -> 42 in this same revision bump; every entry below names its own RG id. -->

- fix(run-gate): make `gate-full` propagate an explicit comparison base to
  its delegating `assay-r1` sub-lane, so linked worktrees can run the complete
  gate with `--base REF`.
- **P4 final-review hardening (2026-09-15).** A foreign-runner inflight
  record is now a terminal exit-2 refusal (including `--fresh`), so the
  container runner cannot start a replacement and overwrite the protected
  record. Bare-host self targeting now treats `/etc/hostname` as candidate
  discovery only and proves the full Docker object by mount-namespace
  equality before starting a session. Daemon absence now requires specific
  Docker-owned absent/stopped text: exit 125, permission failures, and a
  failed `docker ps` remain explicitly indeterminate. Rusage duration comes
  from a monotonic interval rather than whole-second display stamps, and a
  `wait4()` failure appends to (rather than masks) an earlier degradation
  reason before reporting no profile. Finally, successful daemon responses
  validate usable `session`/object `summary` values, while an optional
  non-object `target` is accepted without leaking the valid session; parser
  failures and non-object refusal `error` values also honor the client's
  never-raises degradation contract.

### Added
- **RG-57 — bare-host lanes are profiled (RW-27b).** Bare-host lanes used
  to be categorically UNPROFILED (no container/cgroup of run-gate's own
  to sample); now every lane kind is profiled once `[profile]` is
  enabled. Daemon path: the target is run-gate's OWN process (self
  container id resolved from `/etc/hostname` + a direct `docker
  inspect` and proved against this process's mount namespace), scope ALWAYS
  `container-shared` (a bare-host invocation
  shares its devcontainer's cgroup with everything else in it),
  disclosed as DEVCONTAINER-WIDE. Daemon-absent path: `os.wait4(pid, 0)`
  on the LANE'S OWN child (`Popen` + `wait4`) — `method: "rusage"`,
  `memory.peak_bytes = ru.ru_maxrss * 1024` is that child's own peak RSS
  (`memory.source: "rusage-maxrss"`, never a sum, never borrowed from
  another child), `scope: null` (rusage measures via `wait4()`, not a
  cgroup read — no contract `scope` value is honest), everything else
  `wait4()` cannot supply left `null`. NEVER a `BasicSampler` fallback on
  this path (RW-27b, deliberate: no cgroup here is safely attributable to
  just one lane's own child). `run-gate.footprint.json`/`doctor` disclose
  the `rusage-maxrss` caveat next to the median it qualifies. SPEC
  `R-43i`. **Round-1 review (RW-43/B1):** the first cut of this path used
  `resource.getrusage(RUSAGE_CHILDREN)` deltas instead, which is a
  monotone high-water mark over EVERY child this process has ever reaped
  — a `["true"]` lane was credited with run-gate's own already-reaped
  docker/git subprocess RSS (~36 MiB) rather than its own; repaired to
  `os.wait4()` on the specific child pid, which cannot mix in another
  child's number by construction. **Consequence:** this project's own
  five lanes are all bare-host, so
  `./run-gate.py selftest` now itself records a profile and `footprint
  --write` stops refusing for this project (see "The footprint manifest"
  in `CONSUMERS.md` for a real transcript). **Round-2 review (RW-46b):**
  `os.wait4()`'s `ru_maxrss` is still an honest fix (nothing left to
  correct in the arithmetic itself), but fork+exec's COW page-table
  inheritance means a short-lived child's own high-water RSS can never
  fall below run-gate's OWN resident size at the moment it forked the
  child — proven directly against `Popen`/`posix_spawn`/raw
  `fork()+exec()`. `memory.floor_bytes` (run-gate's own RSS,
  `/proc/self/statm`, read immediately before spawning) and
  `memory.peak_at_floor` (`true` when `peak_bytes <= floor_bytes` —
  unmeasurable beyond that floor, not necessarily the lane's true peak)
  now ride alongside `peak_bytes`; `footprint`/`history`/`doctor` print
  "(peak <= floor)" for such a run, and the footprint manifest's per-lane
  entry carries `peak_at_floor` from its most-recently-profiled run. SPEC
  `R-43i`/`R-44a` amended, LANE-AUTHORING.md's own footprint-budgeting
  guidance updated. **Round-2 review (RW-51/B5):** contract Sec 3a
  requires `meta.expected` (the object `--meta`'s `start` carries) to
  name `source` — the manifest lane's own, so admission (RG-56) knows a
  rusage-derived expectation from a cgroup-measured one — and the shipped
  `footprint_manifest_lane_expected()` still returned the four-key object
  contract Sec 2.2 originally specified. Now a five-key object; a
  manifest entry written before this fix (no `source` key of its own)
  derives it from that entry's own `method`/`scope` pair instead of
  falling back to `null` outright. SPEC `R-44d` amended. **S10 (round-2
  review, RW-51):** the rusage path's `os.wait4()` reaps the lane's child
  directly, which never set `Popen.returncode` on the `proc` object
  itself (only `Popen.wait()` does that) — harmless in practice, but a
  spurious `ResourceWarning: subprocess <pid> is still running` under
  `python3 -W error::ResourceWarning`. `proc.returncode` is now set from
  `os.waitstatus_to_exitcode()`'s own result right after computing it.
- **RG-60 — exec-lane inflight record.** `run_exec_lane` now writes the
  same inflight record `run_container_lane` writes (after the profiling
  session, if any, is established and before the exec begins; cleared in
  the same `finally` that finishes profiling), so a client that dies
  mid-run leaves a recovery record naming the profiling session, exactly
  as a container lane's client would. Written unconditionally, profiled
  or not. Not wired into a re-attach of its own — RG-60's own scope is the
  record existing to be FOUND, not a new re-attach design. SPEC
  `R-43a`/`R-43f` amended (dropped the "an exec lane writes no inflight
  record" caveat). **Round-1 review (RW-43/B3):** both writers now stamp
  `runner` (`"container"`/`"exec"`) into the record, and the container
  path's `resolve_inflight` refuses to attach, follow, collect, or
  `docker rm -f` a record some OTHER runner wrote, and refuses to start a
  replacement that would overwrite it (SPEC `R-39f`) — the
  gap RG-60 opened: an exec-written record sitting at the SAME path a
  container lane reads, indistinguishable from one of its own, so a
  lane's `environment` flipping from exec to an ephemeral-container one
  between a crashed run and the next could otherwise re-attach to, or
  `--fresh`-remove, a persistent CIU runner this project never created
  (the CIU-104 incident class).
- **RG-55 — per-lane resource profiling, against the cgroup-profiler daemon
  contract (`RG55-INTERFACE-CONTRACT.md`).** Every lane invocation gets a
  resource profile — peak memory (+baseline, p90, DAMON hot-set), CPU
  cores, memory-full stall — from the daemon (`cgprofile-host-daemon`,
  `docker exec ... cgprofile ctl ...`) when reachable, or a coarser
  in-lane cgroup sample (`method: "basic"`) when it is not; never nothing,
  unless profiling is disabled outright (`RUN_GATE_PROFILE=off`,
  `[profile] enabled = false`, or a lane's own `profile = false`). Live
  acceptance, real docker, no daemon present (basic-path fallback): an
  ephemeral lane allocating ≥ 100 MiB measured a peak of **115523584 bytes
  (110.17 MiB)**; an exec-mode (`container-shared`) lane allocating 80 MiB
  on a persistent runner measured `peak_over_baseline_bytes` of
  **88612864 bytes (84.51 MiB)** over a 90.14 MiB baseline, with ≥ 2
  samples taken mid-run — both against this package's own numeric
  acceptance criteria (≥ 100 MiB / ≥ 70 MiB respectively). History schema
  2 (SPEC `R-36j`) carries the new `resources`/`profile_error`/
  `profile_ref` fields and five new series (median peak, +baseline,
  hot-set p90, CPU cores, memory-full stall) alongside the existing
  duration series. The three byte-valued series (peak, +baseline, hot-set
  p90) report the NEAREST-RANK p50 — an actual sample, never an averaged
  `.5` value on an even count (SPEC `R-36k`, RW-24, review round 2 S11);
  the CPU-cores and memory-stall series are not bytes and still average.
  New ambient override `RUN_GATE_PROFILE` (`"on"|"off"`,
  SPEC `R-43g`) for a runner without `docker exec` rights to a daemon it
  will never have. SPEC `R-43`.
- **`footprint` verb (RG-55/C5) — a committed resource budget,
  `run-gate.footprint.json`.** `./run-gate.py footprint [LANE] [--json]
  [--write]` distills history into a manifest TRACKED next to
  `run-gate.toml` (unlike `.run-gate/`, still gitignored); `--write`
  REFUSES (exit 2, naming why) when no lane has a completed, profiled run
  yet. `doctor` warns on drift (`[footprint] tolerance_pct`, default 25%)
  or staleness (`max_age_days`, default 30) against the live history; the
  run path reads it to fill the daemon's `expected` meta field and the
  footprint disclosure line's `| manifest <n> MiB` tail. **BREAKING
  (load-time):** `footprint` joins `doctor`/`validate-pointers`/`history`
  as a RESERVED lane name — a copied-script repo with a lane by that name
  must rename it. SPEC `R-44`.
- **RG-48 — `resources.cpus` → `docker run --cpus`.** Lane
  `[lanes.<n>.resources].cpus` or an environment-level fallback
  `[environments.<e>.resources].cpus` (lane wins), a decimal string
  matching docker's own grammar (`^\d+(\.\d+)?$`, > 0). Exec lanes get the
  pre-existing naming-only WARNING (docker exec can neither place nor cap
  work) rather than a refusal. `doctor` warns when a container lane's
  argv spawns workers by name (`-n auto`/`--workers auto`) and neither the
  lane nor its environment declares `cpus`. SPEC `R-29` amended.
- **`doctor`'s "profiler" check (RG-55/C7).** Daemon container
  present/running (`docker ps`), `ctl version` (contract/cgprofile
  version/DAMON state), the effective `[profile]` settings incl. whether
  `RUN_GATE_PROFILE` is overriding them, and — once the daemon answers —
  host and per-slice pressure via `ctl host`. Every finding is
  INFO/WARN/OK/SKIP, never FAIL: profiling is optional infrastructure.
  The pre-existing R-29 "no derivable memory ceiling" WARNING now names
  WHY when the cause is a PRIVATE cgroup namespace (the devcontainer/CI
  default: `/proc/self/cgroup` reads exactly `0::/`) rather than a
  misconfigured `$RUN_GATE_CGROUPFS_ROOT` — host-side slice truth is then
  reachable only through this same "profiler" check. SPEC `R-30c` (RG-61:
  this check had no rule id of its own until now).

### Fixed (detail)
- **RW-46a — the test suite no longer writes its RG-20/R-41 coordination
  locks into host `/tmp`.** `SHARED_LOCK_DIR` stays `/tmp` in production
  (both mutexes coordinate SEPARATE run-gate invocations on one host, by
  design); a new `RUN_GATE_LOCK_DIR` override (re-read on every call, same
  shape as `RUN_GATE_CGROUPFS_ROOT`/`RUN_GATE_PROC_ROOT`) plus an autouse
  `tests/conftest.py` fixture now points every test at a throwaway
  per-test directory. Root cause of 493 stale
  `run-gate-exec-*-runner.lock` DIRECTORIES found accumulated under
  production `/tmp` (never a legitimate shape — the code only ever
  `os.open()`s a plain FILE at that path): two existing tests deliberately
  `mkdir()`ed a directory there, with no cleanup, to prove the
  OSError-not-traceback path; fixed at both call sites plus a permanent,
  suite-wide teardown assertion against the defect class recurring
  anywhere. `doctor` gains an INFO check counting stale lock entries older
  than 1 day (naming how many are directories) — report only, never
  deletes.
- **RG-59 — live-run daemon-absent warning names the real cause.**
  `ProfilerClient._ctl`'s `json.JSONDecodeError` branch used to report
  "produced unparsable stdout" for two structurally different causes: a
  daemon container absent/stopped (`docker exec` itself fails before
  `cgprofile` ever runs, stdout is empty) and a RUNNING daemon returning
  genuinely malformed stdout. Matches docker's own exec failure via
  `daemon_not_running_reason()` — the ONE place both `doctor`'s "profiler
  daemon" WARN and this live-run reason get their text from, so the two
  surfaces cannot drift apart again. The genuinely-malformed-response case
  keeps "produced unparsable stdout". **Round-1 review (S2):** the first
  cut matched any stderr line CONTAINING "no such container"/"is not
  running" (case-folded) — which also matched a RUNNING, reachable daemon
  whose own `cgprofile` process crashed with an application-level
  exception mentioning those same words
  (`cgprofile.errors.TargetError: ... is not running`), misreporting a
  crashing daemon as "not deployed at all". Narrowed to docker's own exec
  failure signature specifically: a stderr line PREFIXED (not merely
  containing) with `docker:`/`Error response from daemon:` that also names
  a missing/stopped container — never a bare substring match against text a
  daemon's own application code might have produced, and never an exit code
  alone.
  **Round-2 review (S11):** those three exit codes are not one condition.
  125 says only that Docker itself could not complete the request; permission,
  transport, and argument failures collapse into it too, so it is explicitly
  indeterminate unless stderr positively names absence. 126 (command not
  executable) and 127 (not found) mean `docker exec` REACHED a live
  container and `cgprofile` itself could not be started inside it — a
  broken image or PATH, reproduced live on this host. Folding all three
  into "not running ... ciu up" told the operator to start a container
  that was already up. 126/127 now get their own `daemon_broken_reason()`
  wording naming the real condition.
- **RG-58 — bare-host `stall_timeout` gets a load-time WARNING + a
  matching `doctor` WARN, never a refusal (RW-27a).** A `stall_timeout`
  declared on a `bare-host` lane was silently inert (`run_bare_host_lane`
  is a plain `subprocess.run` — no `ProgressWatch`/`LogStreamWatch`/timer
  of any kind watches it). Now: ONE load-time `run-gate: WARNING lane
  <name>: stall_timeout is inert on a bare-host lane` at config load
  (`_validate_lane`) and a matching `doctor` WARN, both from the SAME
  shared `bare_host_stall_timeout_inert_reason()` so the two surfaces
  cannot drift apart. Config still loads; exit code unchanged;
  container/exec lanes untouched. SPEC `R-30c`.
- **RG-61 — SPEC/CONSUMERS/LANE-AUTHORING/backlog/`usage()` documentation
  drift, eight items (S14 remainder).** New `R-30c` (the doctor profiler
  check's own rule id); `R-30`'s status-line count corrected (`INFO` is a
  fifth class, not a fourth); the RG-51 narrative's stale `0/0 -> 100%`
  wording corrected to describe RW-5's actual SKIPPED-verdict design;
  `CONSUMERS.md` gains full `[profile]`/`[footprint]` schema blocks and
  the `RUN_GATE_PROFILE` `on`-override + by-name-refusal behavior, plus a
  REAL `footprint --write` transcript replacing a fabricated one;
  `usage()` gains `RUN_GATE_PROC_ROOT`; a stale `R-43g`/`R-43h`
  cross-reference and an inaccurate "both had shipped in code" claim
  (`resources.cpus` is new this rev, not a backfill) both corrected.
- **RW-28 — every R2 (mutation) lane sets `judge.mutation.
  budget_per_candidate` (mandatory, not advisory).** A hung mutation
  candidate (a mutant flipping a `threading.Thread(daemon=True)` to
  non-daemon, blocking the process at interpreter exit) blocks the WHOLE
  R2 run when this key is unset — `stall_timeout`/`budget` bound the
  LANE, never one candidate. `assay.toml`'s `r2` lane now sets
  `budget_per_candidate = "900s"`.
- **The R3 `median-not-mean-series-stats` canary tracked the pre-RG-55
  `series_stats` block and became a false "broken canary" after byte-valued
  series moved to nearest-rank p50 (R-36k).** Its target now anchors the
  non-byte branch, which the existing CPU/stall outlier assertions exercise;
  the canary again rejects the arithmetic-mean mutant.

- **RG-53 — BREAKING: `tools/coverage_gate.py` now reads `missing_branches`
  and refuses a 0/0 diff.** Two independent semantic changes to the vendored
  diff-coverage gate the `selftest` lane's floor rests on:
  1. A changed line that executed but left an `if`/`for`/etc. arm untaken
     (present in the coverage JSON's `missing_branches`, absent from
     `missing_lines`) now counts as uncovered. `--cov-branch` (already
     passed by this project's own `selftest` argv) was previously
     decorative for the diff judge — only whole-file branch totals were
     ever measured, never intersected with the changed-line set. `Verdict`
     gains `branches_total`/`branches_missed`/`branch_partial_lines`,
     reported beside the line counts in both the OK and FAIL CLI lines.
  2. `total_changed_exec == 0` is now reported as **SKIPPED** — exit 0,
     `Verdict.verdict == "skipped"` (never `"ok"`, never a bare `100.0%`
     line) — naming the resolved base and how HEAD relates to it (`HEAD is
     on the base` or `HEAD is N commits ahead of the base; the diff
     touches no executable source line`), closing the `0/0 -> 100%`
     SILENT-pass trap RG-53/RG-51 found without turning every gate run
     that happens not to touch the judged file (e.g. this project's own
     `selftest` on `main` itself) into a hard failure. `evaluate()` itself
     stays a pure 0/0-is-100% classifier (existing direct callers/tests of
     `evaluate()` see identical `pct`/`passed` numbers; only the new
     `Verdict.skipped`/`.verdict` fields distinguish the case) — the
     SKIPPED reporting is CLI-level, in `main()`. Hard refusal (exit 2,
     naming the three known routes to a false 0/0) is now OPT-IN via the
     new `--refuse-empty-diff` flag; `--allow-empty-diff` no longer exists.
     **Reworked 2026-09-12 by RG-55 wave controller ruling RW-5** — this
     package's first landing of RG-53 made the 0/0 case a hard refusal by
     default, which put this project's own `selftest` lane permanently red
     on `main`; RW-5 corrected it to the SKIPPED design above.
  **BREAKING for every consumer of the vendored judge (topos pattern):
  re-copying `tools/coverage_gate.py` picks up BOTH stricter semantics —
  a lane that was previously green on an uncovered branch now fails, and
  one that was silently `0/0 -> 100% OK` now prints a loud, distinct
  SKIPPED notice (still exit 0) instead — plus `--allow-empty-diff` is
  gone (`--refuse-empty-diff` is its opt-in replacement, inverted
  default). Consumers re-copy on their own; this file does not push the
  change.** `run-gate.toml`'s own `selftest` argv is UNCHANGED — on `main`
  it now prints SKIPPED and exits 0; on a branch whose diff touches
  `run-gate.py` it judges those lines normally, same as every other
  consumer. `tools/coverage_gate.py`'s docstring records all three
  changes; `tests/test_coverage_gate.py` covers them (branch-partial lines
  staying/leaving covered, branch totals scoped to changed lines only,
  malformed branch-arc shape rejection, `Verdict.verdict` tri-state for
  both the 0/0 and nonzero cases, the base/HEAD relation text for both
  shapes, the `_empty_diff_notice` pure formatter for both the default and
  `--refuse-empty-diff` outcomes, and an end-to-end `main()` pair proving
  the default SKIPPED exit-0/stdout behavior and the opt-in refusal).

<!-- cmru: release history -->

## [23.8.0] - 2026-09-16
<!-- cmru: generated -->
<!-- cmru: source-end=dee4226bf570cc9f32b3ea9c3a3f0da404fde90d -->

### Added
- feat(tester-unified): codify cockpit gate launcher (411150b1)
- feat(rg55-p4): RG-57 -- bare-host lanes are profiled (C4) (c37b6e94)
- feat(rg55-p4): RG-58 -- bare-host stall_timeout load-time + doctor WARN (C3) (e698835f)
- feat(rg55-p4): RG-59 -- live-run daemon-absent warning names the real cause (C2) (b5e4a9c6)
- feat(rg55-p4): RG-60 -- exec-lane inflight record (C1) (a6716422)

### Fixed
- fix(tester-unified): preserve host-visible tmp for judges (b3f3d5c3)
- fix(tester-unified): keep temp outside git namespace (f336dbff)
- fix(tester-unified): isolate temp without mount collisions (3d6c050d)
- fix(run-gate): keep empty identity response indeterminate (bc1a64f5)
- fix(run-gate): harden P4 recovery and profiling boundaries (a4786545)
- fix(run-gate): propagate base through gate-full conjunction (84fab73e)
- fix(run-gate): close exec inflight spawn window (28bc3feb)
- fix(rg55-p4): S11 -- split RG-59's daemon-not-running matcher, 125 vs 126/127 (89eb7e38)
- fix(rg55-p4): S10 -- set Popen.returncode after os.wait4 reaps the child (cf043dda)
- fix(rg55-p4): B5 -- meta.expected carries source (contract Sec 3a, RW-51) (07ae3a47)
- fix(rg55-p4): S2-S5 -- RG-59 false-positive fix, un-circular the killed-client exec test, stale comment, test-count recount (c49a5d58)
- fix(rg55-p4): RW-46b -- rusage memory.floor_bytes/peak_at_floor, honest disclosure of the fork/COW floor (2f10be9a)
- fix(rg55-p4): RW-46a -- isolate the test suite's exec/shared lock dir, root-cause the stale-lock-directory leak (c199fbdc)
- fix(rg55-p4): B4 -- doctor's profiler-daemon WARN names both fallbacks (RW-43) (9489bb6d)
- fix(rg55-p4): B3 -- container path refuses a foreign (exec-written) inflight record (RW-43) (05193f44)
- fix(rg55-p4): B1 -- rusage path takes numbers from the LANE's own child (RW-43) (a1cebacf)
- fix(rg55-p4): assay-r3 RED -- canary-run.sh's series_stats target went stale (7539a44e)
- fix(rg55-p4): selftest RED -- test_no_stdlib_violations missing 'resource' (session 3) (af654ede)

### Changed
- controller(rg55): land reviewed P4 and preserve operator backlog (dee4226b)
- Merge run-gate 23.8.0 P4 follow-ups (RG-55) (3baffc74)
- review(run-gate): accept RG-55 P4 (d8434fe5)
- review(run-gate): record timing-dependent stall oracle (4d69f197)
- review(run-gate): record tester gate launcher blocker (989065fc)
- review(run-gate): record P4 final review blockers (732c6b4d)
- review(run-gate): record blocked P4 Sol review (6b8e3252)
- ops(rg55): restore detached mutation completion watcher (7dd712c2)
- controller(rg55): record P6 oracle repair relaunch (8247d917)
- controller(rg55): record P6 retry and async development rule (3f0873a6)
- controller(rg55): record P4 mutation closure (90112d71)
- controller(rg55): record crash-revert witness (c477a4b4)
- controller(rg55): record crash-revert witness (d12ca4b3)
- controller(rg55): record promotion recovery checkpoint (3f4ab420)
- controller(rg55): record promotion recovery checkpoint (8429af19)
- controller(rg55): record P35 residual review blockers (ffd07276)
- controller(rg55): record CMRU review blocker (b95f0841)
- controller(rg55): record residual design blockers (4708ce23)
- controller(rg55): record CMRU BuildKit repair review (d642d23d)
- controller(rg55): send RG56 repair for verification (e2fc6b71)
- controller(rg55): record P1 incomplete mutation resume (5a7a846e)
- controller(rg55): record P35 review and repair dispatch (5955a050)
- controller(rg55): record RG-56 design blockers and repair (3616e467)
- merge main after run-gate 23.7.0 for RG-55 P4 (c8f1654c)
- controller(rg55): dispatch RG-56 design review (3df192a3)
- controller(rg55): persist contention-agnostic progress rule (9ed3cebe)
- controller(rg55): dispatch structural follow-up sidecars (7b60431c)
- controller(rg55): set structural contention direction (a1050e58)
- controller(rg55): resume P6 mutation evidence (2a853cf1)
- controller(rg55): distinguish P6 stopped from complete (f576084b)
- controller(rg55): record merged-assay review acceptance (b8c3580c)
- controller(rg55): record SPEC-V8 D.6 presence (5fc73b1d)
- controller(rg55): triage operator RG-45 addendum (690e89c0)
- controller(rg55): record merged-assay review dispatch (7e7b0363)
- controller(rg55): record p1 mutation resume (b032c268)
- controller(rg55): record assay merge and p1 resume (4840ef18)
- controller(rg55): record assay sidecar gate (eea6df52)
- controller(rg55): record subprocess timeout control probe (2851a88d)
- controller(rg55): record CMRU build-stall diagnosis (0bdf0b96)
- controller(rg55): stage isolated P3 report skeleton (d6ebe1bd)
- controller(rg55): record bounded P3 preflight dispatch (028e533a)
- controller(rg55): retire silent closeout audit (c2911162)
- controller(rg55): queue combined assay gate (4968cf38)
- controller(rg55): retire silent P4 reviewer (3fe2f342)
- controller(rg55): record P5 backlog identifier correction (04ea5f00)
- controller(rg55): detach P6 rejudge supervisor (81e1de6e)
- controller(rg55): record P6 rejudge requirement (0d5754ad)
- controller(rg55): dispatch P4 round-3 repair (86cf988e)
- Merge RG-26 gate-full base propagation proof (6f595186)
- controller(rg55): record focused cmru gate (be2a44f8)
- controller(rg55): record release-log and backlog triage (64fcd96e)
- controller(rg55): record CMRU Git fixture review blocker (4ef903f7)
- controller(rg55): record CMRU evidence repair and review dispatch (736ec931)
- controller(rg55): record CMRU evidence review rejection (ead7c996)
- controller(rg55): record takeover and PSI-aborted gate (acb2f717)
- controller(rg55): record P6 identity-preserving budget retry (bcf0d236)
- Merge main into RG-55 P4 repair (b4fb7b1b)
- controller(rg55): launch explicit P6 budget rejudge (6d49c72c)
- controller(rg55): record repair results and P6 rejudge deferral (30c00e18)
- controller(rg55): record mutation survivor gate failure (13a98cb2)
- controller(rg55): record resumed wave snapshot and P6 relaunch (786cb088)
- controller(rg55): correct P4 review round assignment (1d868bb6)
- controller(rg55): supersede stale P2 finalization dispatch (d8e33486)
- controller(rg55): supersede stale P2 finalization dispatch (d5db1118)
- controller(rg55): launch clean B096 gate retry (440d4278)
- controller(rg55): record P2 retry launch (a6e7e88a)
- controller(rg55): dispatch P2 finalization (1064ff27)
- controller(rg55): record B097 review acceptance (e9adbd67)
- controller(rg55): resume B097 review verification (1afba3b2)
- controller(rg55): disclose gate retry PSI race (db55c088)
- controller(rg55): record B096 gate failure (c3ac430e)
- controller(rg55): dispatch B097 final review (8f9f13c4)
- controller(rg55): defer launches under memory PSI (1315501e)
- controller(rg55): record B096 acceptance and gate (6f3709a2)
- controller(rg55): cap discovered mutation lane (72b1c825)
- controller(rg55): triage operator backlog addendum (81835dbc)
- controller(rg55): dispatch assay B097 (95e1159c)
- controller(rg55): approve P6 fresh R2 (fac9bc3b)
- controller(rg55): narrow P6 successor task (3f727b6e)
- controller(rg55): clear stale orphaned pytest (21288d4a)
- controller(rg55): dispatch fresh P6 Luna successor (53d304c3)
- controller(rg55): record unavailable P4 Sol review (de0d960d)
- controller(rg55): record green P4 mutation judgment (99620a0c)
- controller(rg55): record deterministic P6 timeout mutants (4a3b4426)
- controller(rg55): record P6 exact-tree resume (05b47e94)
- controller(rg55): record P6 resume progress (a4ee6290)
- controller(rg55): record unavailable Sol review runtime (4fd8afda)
- Merge branch 'main' into rg55-followups-run-gate (d4c57c1a)
- Merge branch 'main' into rg55-followups-run-gate (b72cba31)
- Merge branch 'main' into rg55-followups-run-gate (874ac05c)
- merge(run-gate): integrate P2 release tip for RG-55 P4 (f92241bb)
- log(rg55-p4): LOG entry for BRIEF-5 -- session 6 wind-down, third ade7916e85220fbb9 message (retraction + lock-cleanup disclosure) relayed (2ca41a02)
- log(rg55-p4): BRIEF-5 -- continuation brief for a fresh successor (operator wind-down; round-2 repairs done, parked on P2's 23.7.0 release) (23e91ce4)
- log(rg55-p4): correct r2-termination attribution (ade7916e85220fbb9 has no authority, per controller); re-verified clean; second unauthorized contact noted, not acted on (1f8d9ca3)
- log(rg55-p4): assay-r2 launched then killed mid-session (RW-52 superseded by RW-54) -- no survivor table, package stops here pending P2's release (e795c8f5)
- log+records(rg55-p4): session 6 LOG/REPORT -- B5 + S6-S11 repairs, regenerated transcript, three gate verdicts (round-2 review closeout) (f3b983ec)
- log(rg55-p4): session 5 REPORT -- RW-46a/b evidence, S1-S5 table, footprint transcript, doctor output, gate verdicts (4fa46b03)
- log(rg55-p4): all three gates GREEN (selftest, assay-r1, assay-r3); RG-62 filed for two pre-existing flakes found live (d444b246)
- log(rg55-p4): session 4 -- B1-B4 round-1 repairs done+verified, gates blocked by host-wide /tmp contention, BRIEF-4 (checkpoint cut) (00a79de4)
- log(rg55-p4): session 3 -- r1/r3 verdicts, r2 occupied, REPORT + BRIEF-3 (checkpoint cut) (0bb3bbeb)
- log(rg55-p4): session 3 -- Commits 5-8, selftest RED->RED->GREEN, footprint (5bdd7a2f)
- plan(rg55-p4): checkpoint -- BRIEF-2 (E-008 cut after C4/merge/C5, coordinator-issued) (b695db00)
- merge(rg55-p4): rg55-run-gate-client -- pick up P2's ongoing close-out work before C5's gates (0c782601)
- plan(rg55-p4): checkpoint -- BRIEF-1 (E-008 cut after C1-C3) (7ff1a819)

### Documentation
- docs(rg55): clarify path-based Sol handoff invocation (b48a68a5)
- docs(rg55): make Sol review launch selection explicit (e314719b)
- docs(rg55): record semantic pause checkpoint (fe4d9614)
- docs(rg55): reserve RG-63 for future P5 follow-ups (7e154817)
- docs(rg55): add manual Sol final-review packet (aff48e01)
- docs(controller): record P1 final gates (aa8d4649)
- docs(controller): record P1 rejudge and P4 gate (34140cd8)
- docs(controller): record P1 mutation resume (76401e1e)
- docs(controller): record CMRU candidate gates (de2e36d3)
- docs(rg55): record stale-process audit (65036b41)
- docs(rg55): record host saturation at P1 boundary (43f96368)
- docs(rg55): record P1 host-PSI observation (ce7f63c0)
- docs(run-gate): record RG-55 P4 round-3 repair evidence (b7d72b2f)
- docs(rg55): triage operator backlog addendum (fe903c10)
- docs(rg55): record B096 Topos qualification failure (4d80df50)
- docs(rg55): record B096 tester-unified reproduction ruling (b7ba999d)
- docs(rg55): record final narrow B096 dispatch (dadb39ca)
- docs(rg55): record B096 worker replacement (6ba7960a)
- docs(rg55): record B096 P25 repair gate failure (6ac5efc5)
- docs(rg55): record installed cmru repair (8ae1a185)
- docs(rg55): record deferred P4 watcher correction (8df7b01d)
- docs(rg55): record P1 coverage gate stop (37c2ad95)
- docs(rg55): record P1 mutation triage and RG-26 merge (f6fa81e6)
- docs(rg26): record final sidecar review (adfa3a5d)
- docs(rg55): record duplicate gate wrapper outcome (b971b4a1)
- docs(rg55): record duplicate gate wrapper outcome (ae93a7f5)
- docs(rg55): record B096 authoritative gate failure (d5dd7962)
- docs(rg55): record RG-26 review blocker (1a07b541)
- docs(rg55): record RG-26 review and repair brief (63fcd32f)
- docs(rg55): clear stale shared edit ask (8487e515)
- docs(rg55): clarify assay gate evidence (95f18931)
- docs(rg55): record B097 final gates (f841c7fa)
- docs(rg55): record assay recovery compatibility ruling (48019ab9)
- docs(rg55): record gate invocation correction (1f5474b6)
- docs(rg55): supervise mutation budget resumes (dde48b12)
- docs(rg55): queue B097 and P4 follow-up gates (9a61b9c9)
- docs(rg55): record resumed controller state (347e71ae)
- docs(rg55): record resumed controller state (e89e27de)
- docs(rg55): record resumed controller state (5be1e8de)
- docs(rg55): record resumed controller state (3af7556d)
- docs(rg55): record resumed controller state (9cc8becd)
- docs(rg55): record resumed controller state (2922434f)
- docs(rg55): record resumed controller state (d40aa8a2)
- docs(rg55): record resumed controller state (c32a8782)
- docs(rg55): record resumed controller state (457b79b3)
- docs(rg55): record resumed controller state (56634748)
- docs(rg55): record resumed controller state (dc097b81)
- docs(rg55): record resumed controller state (65e45a40)
- docs(rg55): record resumed controller state (abb993a8)
- docs(rg55): record p6 mutation launch (8dfae740)
- docs(rg55): record cmru coverage blocker (b3476ae4)
- docs(rg55): queue p6 mutation rerun (ec56b276)
- docs(rg55): record cmru coverage launch (2de4ef0d)
- docs(rg55): record p1 and cmru gate launches (f7a211b4)
- docs(rg55): record mdt release outcome (cc4b3c70)
- docs(rg55): queue fresh p1 mutation run (be1e8139)
- docs(rg55): record capped p6 full verification (6f4853ba)
- docs(rg55): record p4 selftest and queue r1 (4e1544ba)
- docs(rg55): queue cmru gate behind psi (c45e0314)
- docs(rg55): record capped p6 verification (84777d58)
- docs(rg55): restore controller log wrapping (196f192c)
- docs(rg55): record controller resume state (26e1160b)
- docs(run-gate): record P4 repair review verification (74464fed)
- docs(rg55): record independent assay B096 dispatch (9cdc94a2)
- docs(rg55): record asynchronous P6 resume (79830282)
- docs(rg55): record discarded assay review session (8f6d3759)
- docs(rg55): record assay B092 and B098 dispatch (876b8735)
- docs(rg55): record unavailable P4 reviewer runtime (1258506b)
- docs(rg55): record unavailable P1 reviewer runtime (16bf89b7)
- docs(rg55): record approved P6 mutation launch (22cecc6a)
- docs(run-gate): record P4 final gates (068c1dd0)
- docs(run-gate): record P4 final mutation verdict (97d294c0)
- docs(run-gate-project): record P4 survivor disposition state (e5072260)
- docs(run-gate-project): record P6 remaining mutation placeholders (1344dd55)
- docs(run-gate-project): record P4 survivor-oracle repair (3aea5a6e)
- docs(run-gate-project): record unhonored Sol review dispatches (7bc7119b)
- docs(run-gate-project): record P1 closeout receipt (addf20d5)
- docs(run-gate-project): record completed mutation campaign triage state (7949aa65)
- docs(rg55): record exact-tree mutation judgment (05663d7b)
- docs(rg55): rule merge-tip no-mutants invalid (fccba080)
- docs(rg55): record run-gate 23.7.0 release (8d8a66a8)
- docs(rg55-p4): S6 -- regenerate footprint manifest + CONSUMERS transcript from a fresh clean-tree selftest PASS (round-2 review, one generation stale) (864f60f3)
- docs(rg55-p4): regenerate run-gate.footprint.json + CONSUMERS transcript from a clean selftest PASS (9159c58d)
- docs(rg55-p4): RG-61 item 5 -- real footprint transcript, captured on e0e02dce (02707e30)
- docs(rg55-p4): RG-61 -- eight-item documentation sweep + RW-28 config + rev 42 (C5) (5b80c024)

### Testing
- test(run-gate): observe foreign refusal boundaries (b2eb633e)
- test(run-gate): pin textual Docker diagnostics (1dda3bb0)
- test(run-gate): terminate killed mutants promptly (e4888a7f)
- test(run-gate): synchronize moving progress oracle (eae1accf)
- test(tester-unified): derive worktree temp topology (5295df88)
- test(run-gate): isolate profiler doctor mount topology (0a875494)
- test(run-gate): pin empty identity fallback status (4a27eb42)
- test(run-gate): expose empty identity response collapse (dc936cc1)
- test(run-gate): cover P4 negative identity states (095f763d)
- test(run-gate): pin P4 identity and duration wiring (8f664a5b)
- test(run-gate): expose P4 final-review failures (8d6f38d1)
- test(run-gate): pin shipped gate-full base propagation (e766b75a)
- test(run-gate): close P4 page-size survivor (cd7596e9)
- test(run-gate): pin P4 R2 survivor oracles (12e4e150)
- test(rg55-p4): S9 -- scope host-/tmp assertion to this test's own container name, not the whole host (round-2 review flake) (0680d6d8)
- test(rg55-p4): S7/S8 -- pin exec/container runner stamp values, footprint source note, history any-vs-all caveat flags (round-2 review mutants) (459d07a5)
- test(rg55-p4): close the second selftest diff-coverage gap (doctor's peak-at-floor INFO block) (ec6cdc07)
- test(rg55-p4): close this session's own selftest diff-coverage gap (RW-46a/b new-code branches) (3f38dbaa)
- test(rg55-p4): close B1's own wave-diff coverage gap (KeyboardInterrupt/wait4) (50684f2c)
- test(rg55-p4): B2 -- oracles pinning the rusage arithmetic, mutant table (8c5af489)
- test(rg55-p4): selftest RED -- close the wave-diff coverage gap (session 3) (e0e02dce)

## [23.7.0] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=3ac2fd29bcb8d63151c9d2c015cad2e68550c98e -->

### Added
- feat(mdt-host-setup): round-1 repairs C9 -- devcontainer.json /run/cgprofile mount (RW-31/A2, D-30, M5) (d3aa5e6a)
- feat(mdt-host-setup): export CGROUP_PARENT_DEV_INFRA/_GATES to devcontainers (M3) (1de4c93c)
- feat(mdt-host-setup): render/install dev-infra+dev-gates, placement report, checks (M2) (22049949)
- feat(mdt-host-setup): add dev-infra.slice and dev-gates.slice units (M1) (16f3a476)
- feat(rg55-p2): C7 -- doctor's "profiler" check (daemon presence, ctl version/host) + the R-29 private-namespace WHY (e14615b3)
- feat(rg55-p2): C5 -- footprint verb (R-44): manifest build/write/read, doctor drift+staleness, meta.expected, disclosure line (6b9f2f0b)
- feat(rg55-p2): C6 -- resources.cpus (RG-48): --cpus cap, exec-lane naming-only WARNING, doctor worker-count check (f853fb52)
- feat(rg55-p2): C4 -- history schema 2, series_stats, resource columns (R-36) (d17f9899)
- feat(rg55-p2): C3 wiring -- token, daemon/basic orchestration, ephemeral+exec flows, re-attach/promote profiling rules (d8003d36)
- feat(rg55-p2): C3 (partial) -- profiling config + client + accumulator + sampler (4d684920)
- feat(rg55-p2): C2 -- assay-r1/r2/r3 + gate-full lanes for run-gate-project (42fc2d71)

### Fixed
- fix(run-gate): keep R3 canary aligned with byte median split (050c6417)
- fix(mdt-host-setup): round-2 repairs RC1 -- B7 byte-size parser, mdt-cgprofile.conf rename, S20(a)/(b) vacuous-assertion fixes (2922928c)
- fix(assay): B091 RW-36 -- liveness via argv_appended + judge.mutation.liveness gate (e27b107b)
- fix(mdt-host-setup): round-1 repairs C10 -- wizard earmark-sum fourth tier (S2) (e6af30df)
- fix(mdt-host-setup): round-1 repairs C6 -- check.sh B3 fail mechanism + M5 (S9, S5 leftover) (6d9da520)
- fix(mdt-host-setup): round-1 repairs C5 -- install.sh B3 WARN + M5 tmpfiles (83521c56)
- fix(mdt-host-setup): round-1 repairs C4 -- D2 gates ManagedOOMSwap, S5/S12 stale phrasing (21ca5e3f)
- fix(mdt-host-setup): round-1 repairs C3 -- cap-watcher covers dev-gates.slice (B1/D1) (b1ec4f2e)
- fix(mdt-host-setup): round-1 repairs C2 -- env.example B5 + D1 cap knob (f0601a17)
- fix(rg55-p2): RW-24 -- byte-valued series/manifest report nearest-rank p50, not an averaged .5 (026663c1)
- fix(rg55-p2): round-1 review fix -- B1-B5, RW-21 adoption, RW-23 items (5f91f308)
- fix(rg55-p2): RW-17 -- replace the test-only profiling kill switch with an operator-facing RUN_GATE_PROFILE override (b7771be1)
- fix(rg55-p2): basic-path final sample must not record a total docker-exec failure as data (38089fe6)
- fix(rg55-p2): close tools/coverage_gate.py's own diff-coverage gaps found by assay-r1 (45f2aa5a)
- fix(rw5): coverage_gate.py 0/0 diff reports SKIPPED, not refused or silently OK (8c76ba3e)
- fix(rg53): coverage_gate.py reads missing_branches, refuses a 0/0 diff (607950fd)

### Changed
- Merge run-gate P2 client (RG-55) (b54aa1f2)
- Merge assay P7 liveness repair (RG-55) (9d87c523)
- handoff(rg55): P2 BRIEF-7 (c1f70000) recorded; all tracks checkpointed -- controller session closed (f4b5ba5d)
- log+handoff(rg55): RW-58 -- P2's RW-56 remedy is green by hand but not through assay; successor relaunch rule (3 tries, then accept run 2 with disclosure) (68838d05)
- log+handoff(rg55): RW-57 -- P7 round 2 REJECT on B6 (false hung under xdist), gate green on 6f3aefad, rulings for the repair; wind-down state (P1/P4/P6 briefs, gate exit) in the handoff (e4bf71dd)
- records(rg55): P7 review round 2 -- REJECT on B6 (false-hung under xdist multi-process events file); B1-B5 verified; gate green on 6f3aefad (792fc1ba)
- handoff(rg55): controller handoff 2026-09-12 -- session wound down on operator instruction; open asks, running mutation containers, resume order, retention prompt (3184f57e)
- log(rg55): RW-56 -- P2 baseline failure root-caused (order-dependent lock-directory plant, pytest-randomly); final pass with PYTEST_ADDOPTS=-p no:randomly, tree unchanged; P4 parked at 1f8d9ca3 (af151007)
- log(rg55): RW-55 -- a closed P4 session re-woke via its own watcher and acted as a controller; stopped; P4 session 6's early r2 terminated (46e0ec5c)
- log(rg55): RW-54 -- P2's final pass FAIL/COMMAND_FAILED at the R0 baseline under contention; by-hand diagnosis first; P4's r2 moves behind the 23.7.0 release (12ea29ab)
- log(rg55): RW-53 -- P7 session 10 gate-unverified at the container cap; controller runs tester-unified on 6f3aefad (RW-39); round 2 dispatched (2214b12e)
- log+report(run-gate-project): P7 session 10 -- gate BLOCKED at the estate container cap (6f3aefad)
- log+report(run-gate-project): P7 session 10 -- B2-B5 + S-item records, mutant table, deferred S-items (03f42bb9)
- log(rg55): RW-52 -- bare niced mutation runs are PSI-gated, not slot-counted; P2 final pass + P6 r2 recorded (3125c389)
- log+records(rg55): RW-51 -- P4 round 2 ACCEPT-conditional (B5 meta.expected source); round-2 file committed (6ca6104d)
- log(rg55): dispatch table -- P7 session 9 (B1), session 10 dispatched for B2-B5 + gate (fe36d497)
- log(rg55): dispatch table -- P1 RW-48 fix landed + full r2 running; P6 session 10 dispatched (merge P1, r2 on slot) (93b5c368)
- log(rg55): RW-50 -- controller terminated P2's runaway candidate 105 (no per-candidate budget in the 186461de tree); disclosed (4b0073c0)
- log+records(rg55): RW-49 -- P7 round 1 REJECT (false survivor, idle-bound calibration, tests_completed path, schema disclosure, CHANGES); round file committed (ce782cca)
- log(rg55): RW-48 -- P1's BUDGET_EXCEEDED is the hang-at-exit mutant (assay 6.1.1 bucket semantics); root fix + one full r2; P6 r2 held (364945dd)
- log(rg55): dispatch table -- P4 session 5 green at 4fa46b03, review round 2 dispatched; RG-62 taken by flaky-test row (P5 row -> RG-63) (07064fde)
- log(rg55): RW-47 -- controller incident: image-filtered container sweep destroyed P1's relaunch; exact-name rule recorded (950b0093)
- log(rg55): dispatch table -- P7 gate GREEN on 8f972def (after a HEAD_CHANGED void), review round 1 dispatched (754515fb)
- log(rg55): dispatch table -- P1 r2 hit the lane budget at ~201/208; relaunch at the same tree (RW-41) (3a0a1ac5)
- log(rg55): dispatch table -- P6 live probes done, CP-12 (kill never finalized the session) fixed, r2 pending a slot (c3449e86)
- log(rg55): dispatch table -- P7 gate failures root-caused (8f972def), gate re-run in flight (d8fdf4fe)
- log(rg55): RW-46 -- run-gate test suite must isolate the host-wide exec lock dir; rusage floor_bytes; P4 session 5 dispatched (bd68672d)
- log(run-gate-project): P7 LOG self-hash for 95d02f50/ee24ced6 + REPORT "Gate repairs" section (session 8, 2 of 5 gate findings fixed so far) (33a30e11)
- log(rg55): dispatch table -- P6 session 8 dispatched (live probes, r2 when a slot frees) (5ff1b04c)
- plan+log(rg55): P6 review handoff; RW-45 -- builds under PSI, frozen-golden version string, CP-10 root cause (7d729852)
- log(rg55): dispatch table -- P7 gate RED (5 failures through the wheel), session 8 dispatched for root-cause fixes (d4af7995)
- plan(rg55): P7 review handoff (assay B091 liveness: plugin, injection rule, LivenessRunner, hung bucket, progress stream, rejudge) (35cd0ad8)
- log(rg55): RW-44 -- P6 C8 placement landed, session-6 decisions accepted, CP-10/CP-11, session 7 dispatched for close-out (baee1e28)
- log(run-gate-project): P7 LOG self-hash for afda58fd + deferred regression sweep result (8bf77745)
- contract+log(rg55): RW-43 -- P4 round 1 rulings (rusage via os.wait4, exec inflight stamp), contract §4.3a bare-host lanes, round file committed (2ec076e1)
- log(rg55): dispatch table -- P7 session 6 (A4, A5 done), session 7 dispatched for A6 close-out + gate (cafbc664)
- log(rg55): dispatch table -- P6 session 5 (C7 watch role, r0/r1 green), session 6 dispatched for C8 placement (a2e8741b)
- log(rg55): RW-42 -- two PSI-gated mutation runs estate-wide; P4 review round 1 dispatched on 0bb3bbeb, r2 pending a slot (c8b8bcd3)
- log(rg55): RW-41 -- assay per-tree resume identity rejected P2's 174 records after a LOG commit; re-run at 186461de; B092 filed via P7 (a6421295)
- log(rg55): dispatch table -- P7 session 5 (A3 complete, plugin JSON bug fixed), session 6 dispatched for A4-A6 (0a27778f)
- records(rg55): reviewer round files P2 r1-r2, P8 r1-r3 (written by the reviewers, committed by the controller) (d14d6523)
- log(rg55): dispatch table -- P4 session 2 checkpoint (C5 landed), session 3 dispatched for gates (41a7112b)
- log(rg55): RW-40 -- P2 assay-r2 lane timeout at 4 h, resumed; P4 woken under RW-39; P7 session 5 dispatched (db687aad)
- log(rg55): RW-39 -- non-mutation lanes may run under PSI while mutation runs live; P6 session 5 dispatched (C7 watch) (f7f3052c)
- log(rg55): RW-38 -- P8 merged (a71c46b0); operator host-install sequence is the gating step before any devcontainer rebuild (083ca0a8)
- merge(rg55): P8 -- mdt host-setup dev-gates.slice, cap-watcher gates knob, /run/cgprofile template mount + mdt-cgprofile.conf (review ACCEPT round 3) (a71c46b0)
- log(rg55): dispatch table -- P1 orphan verdict captured, budget key fixed (5ce232d1), r2 resume running under run-gate (1ce86520)
- log(rg55): dispatch table -- P6 session 3 checkpoint (C5), session 4 (Opus) dispatched for C6 (90a3f19a)
- log(rg55): dispatch table -- P8 round-2 repairs at e326cc9b, review round 3 dispatched (c7bf1f3a)
- log(rg55): dispatch table -- P7 session 3 checkpoint (RW-36 shipped, verify.py fix), session 4 dispatched for A3 (a11c1b21)
- log(rg55): dispatch table -- P6 session 2 checkpoint (C3, C4), session 3 dispatched (c95183d0)
- log(rg55): RW-37 -- P8 round 2 (B7 size parser, B8 fail-open wording, mdt-cgprofile.conf, merge-ordering constraint) (799181c2)
- log(rg55): dispatch table -- correct three dispatch timestamps (clock read after the fact) (0d3c9c6b)
- log(rg55): dispatch table -- P8 repair set + M5 at ae38d55a, review round 2 dispatched (ff229505)
- log(rg55): RW-36 -- liveness injection via argv_appended, gated by judge.mutation.liveness; P7 session 3 dispatched (33355aaf)
- log(rg55): dispatch table -- P6 session 1 checkpoint (C1, C2), session 2 dispatched (dd3e324b)
- plan(rg55): P5 handoff (run-gate client v1.1: transport seam, gates slice default, policy author, watch consumer, placement requests, RG-56 admission) (114befed)
- plan(rg55): P6 handoff (cgprofile follow-ups: CP-2 socket, CP-4..CP-7, cgprofile.slice, CP-8 watch, CP-9 placement) + RW-35 (7c8e5573)
- contract(rg55): v1.1 amendment (RW-34) -- two carriers, watch streaming, placement, liveness policy, gates slice (6b719d22)
- log(rg55): RW-33 -- P7 liveness mechanism decided (materialized pytest plugin + LivenessRunner), successor dispatched (85096342)
- log(rg55): RW-32 -- P8 round-1 rulings (D1-D5), M5 template mount folded into round 2 (8e54094b)
- design(rg55): amendment A2 / RW-31 -- exec and socket as interchangeable carriers (D-30) (dfaaae09)
- plan(rg55): P8 review handoff (mdt dev-gates.slice) (913385eb)
- revert(mdt-host-setup): withdraw dev-infra.slice (RW-30) -- the daemon ships its own cgprofile.slice (b9e628f5)
- design(rg55): amendment A1 / RW-30 -- daemon is the singleton watcher in its own cgprofile.slice; dev-infra withdrawn; dev-gates stays in mdt (5edec58c)
- design(rg55): liveness/placement/admission design of record (D-17..D-26) + RW-29 -- P7 assay B091, P8 mdt slices handoffs, SPEC-V8 D.7 (11ac5d67)
- plan(rg55): controller log -- RW-28 (hung mutant unblocked; budget_per_candidate mandatory for r2 lanes; B088 re-judge rule) (a9c6d72a)
- plan(rg55): RW-27 third track -- P4 run-gate follow-ups (RG-57..61) handoffs, P5/P6 scheduled (808560d3)
- plan(rg55): controller log -- RW-26 (orphaned P1 r2 container after the low-memory guard; untracked long lanes) (ec64d1fa)
- build(rg55-p2): RW-25 -- assay-r2 jobs=2 for the final close-out mutation run (186461de)
- plan(rg55): controller log -- RW-25 (stale P2 r2 run stopped; final r2 with jobs=2 on the close-out tip), P1 status (cf574f8f)
- plan(rg55): controller log -- P2 review round 2 ACCEPT (two records-only conditions), RW-24 byte medians, close-out plan (14094086)
- Merge branch 'main' into rg55-run-gate-client (a55e4d3e)
- contract(rg55): RW-21 scope-container absolute counters + RW-11 file list -- goldens regenerated (3b75e1df)
- plan(rg55): controller log -- P2 review round 1 REJECT, RW-21 (scope-container absolute counters), RW-22 (r2 fix-round policy), RW-23 (reviewer asks ruled) (8452914d)
- plan(rg55): controller log -- RW-19/RW-20 (mutation-lane budgets and resumes) + dispatch state (P2 review round 1 started) (70b0bba3)
- plan(rg55): adversarial review handoffs for P1 (daemon) and P2 (run-gate client) (ead72a73)
- plan(rg55): controller log -- RW-17/RW-18 (RUN_GATE_PROFILE override, inspect reuse) + the live-probe-only final-sample bug note (e3663418)
- plan(rg55): controller log -- RW-13..RW-16 (status on finished sessions, real report, no-token recommit, write guard) + CP-5 deferral (29ecb7f2)
- plan(rg55): controller log -- RW-10..RW-12 (canary shape, basic-path file list amendment, await_container tick shape) (ca2e8c1d)
- plan(rg55): controller log -- RW-5..RW-9 (0/0 SKIPPED semantics, judge scope, process) + dispatch state (02a15202)
- chore(rg55-p2): vendor assay-6.1.1.pyz for run-gate-project's assay lanes (f687a4ed)
- plan(rg55): P1/P2 implementer handoffs (dispatched 2026-09-12 in parallel per RW-1) (6f827c29)
- plan(rg55): controller log -- record the P0 freeze commit (e499a168)
- plan(rg55): P0 -- interface contract frozen, golden fixtures, RG-56/RG-57 + CP-1..3 filed, SPEC-V8 D.6, controller log (63b928da)
- plan(run-gate): RG-55 lane resource profiling -- wave plan of record from the 2026-09-12 operator interview (90cfcd42)

### Documentation
- docs(rg55): record release dirty-file escape hatch (3ac2fd29)
- docs(rg55): record assay deployment verification (4da28687)
- docs(rg55): record assay release (5bd55394)
- docs(rg55): record scoped backlog preservation ruling (519a0b40)
- docs(rg55): record P2 final adversarial review (581e7e99)
- docs(rg55): record concurrent origin merge (18d49d87)
- docs(rg55): record cmru pushed-snapshot release rule (72905500)
- docs(rg55): record P7 final review and merge (7d5915d2)
- docs(rg55-p7): record final adversarial review (bbd76a92)
- docs(rg55-p7): record final registered gate (90ad8c90)
- docs(rg55-p2): record RW-58 mutation evidence and triage (83094759)
- docs(rg55): record PSI-gated P2 gate interruption (d772fa03)
- docs(rg55): record controller takeover and backlog triage (13ecbc71)
- docs(rg55-p2): BRIEF-7 -- successor handoff for T6 (RW-56/RW-58 findings, 18 verified survivors, candidate 105) (c1f70000)
- docs(run-gate-project): P7 checkpoint BRIEF-9 (B1 done+committed; B2-B5+S-items+gate deferred to a fresh successor) (2c967cae)
- docs(run-gate-project): P7 checkpoint BRIEF-8 (all 5 gate findings fixed; second gate run launched + parked, watcher armed) (8f972def)
- docs(run-gate-project): P7 LOG self-hash for b3f31506 + REPORT A6 section + checkpoint BRIEF-7 (edb995f4)
- docs(run-gate-project): P7 checkpoint BRIEF-6 (A4+A5 shipped; A6 open) (a51b2d23)
- docs(run-gate-project): P7 LOG/REPORT for A5 (c15f6040) (1eaf5683)
- docs(run-gate-project): P7 LOG/REPORT for A4 (5baf2670) (e6a35ff0)
- docs(run-gate-project): P7 LOG/REPORT for 99463ae5+d1540eda + checkpoint BRIEF-5 (72baf838)
- docs(rg55-p2): record T6's first assay-r2 run -- BUDGET_EXCEEDED at 172/283 (RW-40) (647a2cc6)
- docs(run-gate-project): P7 LOG/REPORT for A3 (44dd12ca) + checkpoint BRIEF-4 (4ace234f)
- docs(rg55): P8 REPORT round-2 final -- Gates section refresh (9 assertions), Tip self-reference fix (e326cc9b)
- docs(rg55): P8 REPORT round-2 repairs table + B8/S17/S18/S19 REPORT-side fixes (96a737d1)
- docs(mdt-host-setup): round-2 repairs RC2 -- B8 docker-fail-open correction, S16 worst-case share, S19 ordering sentence (f3ef7b80)
- docs(run-gate-project): P7 checkpoint BRIEF-3 (RW-36 shipped; A3 design decided) (d2b7c76d)
- docs(run-gate-project): P7 LOG self-hash for RW-36 commit (e27b107b) (c8200271)
- docs(rg55): P8 REPORT rewrite for round-1 repairs -- refreshed tip, repairs table, M5, gate verdict (ae38d55a)
- docs(mdt-host-setup): round-1 repairs C8 -- declare CGROUP_PARENT_DEV_GATES in AGENTS.md (D4/S3) (3cd5a91c)
- docs(mdt-host-setup): round-1 repairs C7 -- README B4 sequence rewrite, D1/D2/D3/S1/S4/S10/S11 docs, D5 Changes (20d52736)
- docs(run-gate-project): P7 LOG/REPORT for A2 (f4fa1788) + checkpoint BRIEF-2 (ef5088f6)
- docs(run-gate-project): P7 checkpoint BRIEF-1 (A1 done, A2-A6 open) (723c431d)
- docs(run-gate-project): P7 LOG entry for f649a249 (afac6fcb)
- docs(run-gate-project): P7 LOG/REPORT records for A1 (de32bb91) (5a25655f)
- docs(rg55): P8 REPORT -- final tip, operator command sequence, gate verdict (7bd2f03c)
- docs(mdt-host-setup): dev-gates docs, operator upgrade sequence (M4) (4ec6dcfa)
- docs(rg55): dstdns adoption brief -- DRAFT, not dispatched (P3 fills versions + DAMON overhead) (cc7489c6)
- docs(rg55-p2): SPEC R-43f/R-43a -- profile_token is not recorded on the exec-lane path (69d46544)
- docs(rg55-p2): B3/M5 correction -- M5 is an equivalent mutant, not a closed oracle gap (ACCEPT condition 2) (82094849)
- docs(rg55-p2): file review round-1 deferred residues as backlog RG-58..RG-61 (cc19e1f0)
- docs(rg55-p2): session 7 close -- RW-25 stale r2 run stopped, partial verdict recorded (f08080e7)
- docs(rg55-p2): fix round 1 -- LOG/REPORT records (per-blocker, per-S-item, live probe) (a7a84e09)
- docs(rg55-p2): LOG -- RW-20 ruling, survivor triage items 4-6, shared-worktree concurrency note (d57c3634)
- docs(rg55-p2): session 7 records -- C6/C5/C7/C8 LOG+REPORT, final gate sweep, live footprint probe (62d9a66a)
- docs(rg55-p2): C8 -- SPEC R-43/R-44/amendments, README/CONSUMERS/LANE-AUTHORING, CHANGES, backlog FIXED, revision 41 (ac885ed4)
- docs(rg55-p2): checkpoint after RW-17 + C4 fully done -- LOG, REPORT, BRIEF-6 (38dc9576)
- docs(rg55-p2): checkpoint after C3 fully done + live-probed -- LOG, REPORT, BRIEF-5 (a5a3023b)
- docs(rg55-p2): checkpoint after C3 (partial) -- LOG, REPORT, BRIEF-4 (40c1aa65)
- docs(rg55-p2): checkpoint after C2 fully done + verified -- LOG, REPORT, BRIEF-3 (62f8a18f)
- docs(rg55-p2): checkpoint after C1-rework + C2 vendoring -- LOG, REPORT, BRIEF-2 (554d1a1a)
- docs(rg55-p2): checkpoint after C1 -- LOG, REPORT, BRIEF-1 for successor (1dc201ab)
- docs(run-gate): RG-55 -- file lane resource-usage profiling as a backlog item (15322d20)

### Testing
- test(mdt-host-setup): round-1 repairs C1 -- test-render.sh B2/B6/S7/S8/S14 (244728c0)
- test(mdt-host-setup): add renderer test, wire into the registered gate (62927f1f)
- test(rg55-p2): assay-r2 survivor 9 -- ResourceAccumulator._cores_max one-sided-None pair (098cf24c)
- test(rg55-p2): assay-r2 survivor 8 -- ProfilerClient.start's --meta content (9bcbcd6c)
- test(rg55-p2): assay-r2 survivor 7 -- ProfilerClient._ctl's missing-"ok"-key default (a0edc4ae)
- test(rg55-p2): cover footprint_manifest_lane_peak_median directly (assay-r1 finding) (8faaf969)

## [23.6.2] - 2026-09-11
<!-- cmru: generated -->
<!-- cmru: source-end=5c879d3fa9a67a59ae83a6c3869781ed0329f3ef -->

### Fixed (detail)
- **RG-52 — a comparison base reached a conjunction lane's inner `bash -c` as
  unquoted shell text.** A `kind = "command"` lane propagates its base with a
  `{base}` token in its own argv; `shlex.join` quotes the argv ELEMENT, but
  that element *is* the script the inner `bash -c` re-parses, so
  `--base 'main;touch /tmp/PWNED'` ran `touch` — on a `bare-host` lane, on the
  real host. Git's own ref grammar permits `;`, backticks, `$`, `|`, `&` and
  quotes, so this was reachable from a genuine branch name too.

  `check_base_charset` now refuses any base outside `GATE_SAFE_BASE_RE`
  before it can reach substitution, `--request-base`, or any shell — the
  exact counterpart of `check_worktree_charset` (`R-5`) for the other value
  consumer pointers embed into shell strings. A leading `-` is refused
  separately, on position grounds, because a sub-invoked
  `./run-gate.py --base -weird` would parse it as an option. `--base`
  refuses (exit 2); a `ciu.worktree-instance.json` `base_ref` degrades to
  the `@{upstream}` fallback instead, since a file must never abort a gate
  run — both share the one regex. No real base shape is affected: branch
  names, remote-tracking refs, tags, slashed/dotted names and raw SHAs all
  pass. Found by adversarial review of RG-51; folded in here on an operator
  size call. (RG-53, from the same review, stays OPEN — it changes
  `coverage_gate.py`'s pass/fail semantics estate-wide and needs its own
  cycle.)

- **RG-51 — a delegating lane's DEFAULT comparison base was a REMOTE-tracking
  ref** (`__revision__` 39 → 40). A lane that delegates its base
  (`judge.base_source = "request"`, or a `{base}` token in a command lane's
  argv) invoked without `--base` fell straight through to `merge-base HEAD
  @{upstream}`. Under a "batch commits locally, push later" policy that ref
  drifts behind local work with nobody touching config (measured at 85 and 88
  commits behind in two different projects' lanes in one week), so the default
  silently reproduced the `judge.base = "origin/main"` staleness hazard
  `base_source = "request"` exists to escape — just relocated from a project's
  `assay.toml` into run-gate's own fallback.

  `resolve_comparison_base` now consults, BETWEEN the winning `--base` flag
  and that unchanged `@{upstream}` fallback, the `base_ref` string `ciu
  worktree create|add|adopt` records in `ciu.worktree-instance.json` at a
  managed worktree's CIU root — the ref the tree actually forked from,
  normally a LOCAL branch. Read as a FILE FORMAT (stdlib `json`, well-known
  filename), never a Python import from `ciu`: separate projects, and this
  launcher must run on a fresh clone with zero installs. Both the worktree
  root and the effective project dir inside it are checked, in that order,
  because ciu writes the record at the CIU root, which sits below the git
  worktree root in a monorepo.

  **A recorded ref is used only when it is gate-safe ref text, the record's
  `branch` still matches the tree, git resolves it to a LOCAL branch there,
  `merge-base(base_ref, HEAD)` still EQUALS the `fork_point_sha` ciu recorded
  at creation (ciu CIU-106), and that branch does not already contain the
  tree's HEAD.** These are the safety property, not formalities — THREE
  successive adversarial review rounds each found a FALSE GREEN in the
  preceding cut of this change, every one of them narrowing the judged diff:
  - `ciu worktree adopt` records the adopted checkout's HEAD, and
    `merge-base` against an ancestor of HEAD collapses to that commit — run
    the gate right after an adopt and it would judge zero changed lines.
  - A merged-but-not-torn-down worktree has a real, live `base_ref = "main"`
    that `main` has since absorbed: same collapse, and it was true of 4 of
    the 7 real ciu worktrees in this estate. `git merge-base --is-ancestor
    HEAD <base>` now refuses it, fail-closed, and also covers the
    own-branch and literal-`HEAD` cases (the frozen-id `adopt` case is
    caught by the must-be-a-local-branch clause instead).
  - That same worktree with ONE more commit passes every check above:
    containment stops firing while `merge-base` has quietly moved to the
    pre-merge branch tip, and in the fast-forward variant nothing derivable
    from `base_ref` distinguishes it from a healthy fork. **ciu now records
    the fork commit** (CIU-106) and run-gate requires `merge-base` to still
    equal it. Equality, not an ordering test: a base gaining UNRELATED
    commits leaves `merge-base` unmoved and so already passes. Fail-closed
    on a missing or malformed fork point, so a worktree created by an older
    ciu — or by `adopt` — keeps its previous `@{upstream}` behaviour, and
    says so, until it is recreated. The two guards do NOT subsume each other,
    verified against the real function: a worktree with no commits of its own
    has `fork == merge-base == HEAD`, passes equality, and is caught only by
    containment.
  - A fifth round added one more, found by asking a different question: every
    clause above is about the commit GRAPH, and a branch that committed work
    and then REVERTED it satisfies all of them while producing an EMPTY diff
    — `0/0 = 100%` again. Nothing escapes the judge there (there is no work),
    so it is not the same class; it is another inlet into RG-53, and
    `git diff --quiet <fork> HEAD` closes it for the cost of falling back to
    a base that has something to judge.

  What run-gate hands the judge is now that verified fork COMMIT rather than
  the branch name, so nothing can move it between run-gate's check and the
  judge's own `merge-base` minutes later.

  A tag and a `refs/remotes/…` ref are refused too — the latter because a
  remote-tracking ref is the very thing RG-51 exists to stop defaulting to.
  The allow-list on the recorded ref exists so that RG-51 does not widen the
  pre-existing inner-shell substitution hole from an operator's own command
  line to a git-excluded JSON file; that hole itself is closed separately, on
  the `--base`/`{base}` path, by RG-52 above — one regex, two consumers.

  Every failure mode — absent, unreadable, permission-denied, a FIFO or
  device that would block or explode on read, not JSON, not an object,
  `RecursionError` from deeply nested JSON (a `RuntimeError`, not a
  `ValueError`), missing/non-string/blank `base_ref`, and every rejection
  above — degrades to the pre-existing `@{upstream}` path. `--base` still
  wins outright and a plain checkout is unaffected. Disclosure runs both
  ways, because half of RG-51 was that staleness must be visible: a winning
  record is printed together with the `@{upstream}` ref it displaced, and a
  record that is found and rejected prints `run-gate: ignoring <record>:
  <why>` (the refusal names it too). One deliberate widening — a tree with a
  usable record but no upstream now resolves instead of refusing.

  Two limits worth knowing before relying on this. **It goes inert when the
  worktree is kept current:** merging the base INTO the branch, or rebasing
  onto it, moves the merge-base as surely as merging the other way, spends
  the record, and drops back to `@{upstream}` — a loss of benefit, not a new
  hazard, since the fallback errs WIDE. And **it is sound at run-gate's own
  boundary only:** only the base STRING changes here, and what the judge does
  with it is the judge's contract — assay's `resolve_base` returns HEAD's
  FIRST PARENT and discards the supplied base entirely when HEAD is a merge
  commit (assay B008), which predates this change and behaves identically
  under `--base`. Filed as RG-54. SPEC.md `R-35a`.

  **Consumer note:** the new default is inert until worktrees are recreated.
  Every worktree record that exists today predates `fork_point_sha`, so all
  of them fail closed and keep the old `@{upstream}` behaviour (saying so on
  stdout). Requires ciu with CIU-106; older ciu is fine, it just never
  arms the new path.

<!-- cleared 2026-09-09: the RG-41 write-up that was here (log-stream
     command-lane liveness, LogStreamWatch, three review-round fixes) is
     already shipped -- see [23.6.0]/[23.6.1] below and
     KNOWN_ISSUES_TODO_BACKLOG.md's RG-41 entry for the full detail. Found
     stale (still describing already-released work) while auditing for
     unreleased ciu/cmru work on 2026-09-09 -- this project's standing
     housekeeping rule (clear this block by hand after every release,
     cmru's generator never does it) had been written down here before but
     not actually carried out. -->

### Changed
- backlog(run-gate): RG-54/RG-53 -- record the first-parent 0/0 seen in a real artifact (743869fe)
- RG-51/CIU-106 round-5: a graph clause is not "is there anything to judge" (d8b6f70a)
- RG-51/CIU-106 round-4: capture the fork point at CHECKOUT, not at worktree add (3f5e8912)
- ciu CIU-106 + run-gate RG-51: record the FORK COMMIT, and require it to still hold (d683f608)
- Merge branch 'main' into rg51-worktree-base-fallback (0e339770)
- run-gate: RG-52 gate-safe comparison base; RG-51 held OPEN on a third false-green (3e146571)
- run-gate: RG-51 review round 2 -- a live branch is necessary, not sufficient (f69bda14)
- run-gate: RG-51 review round 1 -- only a live branch may displace @{upstream} (1bcdc8a8)
- run-gate: RG-51 -- default the comparison base to the worktree's recorded fork point (3a4d33cb)
- backlog: RG-51 -- a delegating lane's default base falls back to stale @{upstream} (a1f3744d)
- run-gate: correct RG-50's vendored-copies claim -- in-monorepo consumers are symlinks (977a4d70)
- run-gate: RG-50 -- ProgressWatch's B065 own-clock branch divides by an index of 0 for the first real mutation candidate, crashing the gate (74b964d9)
- Merge nyxloom-P109 -- B9 feature-intake chat bridge over Mattermost (f26aec22)
- backlog(run-gate): file RG-47 -- run-gate.toml resolves relative to invoking CWD, not --worktree (94fe6d06)

### Documentation
- docs(run-gate): RG-49 corroborated by a second independent hit (dstdns-P93) (c4c75211)
- docs(run-gate-project): RG-49 -- RG-38's --state-dir mkdir fails on a root-owned synthetic parent in Mode-B partial-bind-mount worktrees (04cfaac2)
- docs(nyxloom): record the tip's gate PASS; add run-5 evidence to RG-48 (45cb78fc)
- docs(nyxloom): record P109's gate verdict; file run-gate RG-48 (74cd60b2)

## [23.6.1] - 2026-09-08
<!-- cmru: generated -->
<!-- cmru: source-end=ec0bc47fc05f487c5518ed57ff9393745d83383b -->

### Fixed
- fix(run-gate): RG-41 round-3 review -- thread lifecycle on stall, non-UTF-8 byte resilience (c1735aa4)
- fix(run-gate): RG-41 round-2 review -- join() fired on every stall, not just contention (085eff02)
- fix(run-gate): RG-41 round-1 review -- log-stream re-attach staleness, join disclosure, RG-46 filed (db8e46a6)
- fix(run-gate): RG-41 -- container command-lane liveness from log-stream silence (8055e4e6)

### Testing
- test(run-gate): RG-41 round-4 review -- pin errors=replace at the real call site (238edccf)

## [23.6.0] - 2026-09-08
<!-- cmru: generated -->
<!-- cmru: source-end=36eddc76a590c167655de44faa05ba1e06a810ec -->

### Added
- feat(run-gate): 'host' becomes a container default, 'bare-host' is the literal old behavior (rev 24, R-38*) (e7d5cd7c)

### Fixed
- fix(run-gate): round-2 adversarial review -- ASSAY_FLAG_FLOOR, state-dir keying, disclosure (06ba982f)
- fix(run-gate): RG-44/RG-38/RG-40, plus an RG-43 sweep gap found closing the gate (6075a645)
- fix(debian-install-v2): adversarial review batch 2 -- SystemExit boundary, real-dump parsing, RG-43 docs cleanup (d0bb64ea)
- fix(run-gate): fix 10 of main's own tests that assumed host=bare execution, post-merge (01e58c4c)

### Changed
- Merge remote-tracking branch 'origin/main' into debian-install-update (2161b7d3)
- Merge remote-tracking branch 'origin/main' into debian-install-update (6aa14599)

### Documentation
- docs(assay,run-gate): design R0 structured-report tiebreak, file B078, move RG-45's disposition (10a413c1)
- docs(run-gate): RG-38 -- note assay B066's shipped, blocker cleared (555cc95c)
- docs(run-gate): RG-45 -- note 5th reproduction, now under quiet host load too (ab946258)
- docs(run-gate): RG-45 -- vitest RPC heartbeat can spuriously fail a lane under host-wide multi-tenant CPU contention (897df443)
- docs(run-gate): RG-44 -- GONE_SIGNALS case-sensitivity vs this docker version's lowercase stderr (dd8beda6)
- docs(run-gate): renumber the host/bare-host flip -- real collision with main's RG-39/R-39, filed as RG-43/R-42 (eb759ba5)

## [23.5.0] - 2026-09-03
<!-- cmru: generated -->
<!-- cmru: source-end=7cd835a06bf1bc66c2d0d0e612c56df178fc970b -->

### Fixed
- fix(run-gate): RG-39 review report -- strip stray NUL bytes from prior commit (ad2b0e2d)
- fix(run-gate): RG-39 -- cover acquire_exec_lock's OSError branch (diff-coverage floor) (fefc6c19)
- fix(run-gate): RG-39 -- internal exec-mode mutex, no caller flock required (rev 34, R-39) (2c6b2bbc)

### Changed
- merge(run-gate): RG-39 -- internal exec-mode mutual exclusion (rev 35) (609dfb67)

### Documentation
- docs(run-gate): RG-39 exec-mutex merged (609dfb67, rev 35); RG-42 filed for an unrelated pre-existing ciu8 gate-linkage gap (ff1f9a63)
- docs(run-gate): resumable-gate wave SHIPPED -- 23.4.0 merged, released, deployed (471703ee)
- docs(run-gate): RG-39 exec-mutex review round 1 -- ACCEPT (467b2358)

## [23.4.0] - 2026-09-03
<!-- cmru: generated -->
<!-- cmru: source-end=b7b5f38dd7655cc44049d1bb3667ad705c1cd4f0 -->

### Added
- feat(run-gate): bk-lane.sh BK_QUEUE overrides the pipeline queue per build (E5-R6) (17077426)
- feat(run-gate): bk-lane.sh trigger + collector (REMOTE-LANES seam 4) (8fcf3dd9)
- feat(run-gate): Buildkite pipeline generator (REMOTE-LANES seam 2) (c07ae6af)
- feat(run-gate): RG-36 -- liveness judged from the progress file, with an optional stall_timeout (rev 34, R-40) (10aa59e2)
- feat(run-gate): RG-34 -- doctor names an unprefixed script path in a container command lane (rev 34, R-30b) (1e41069f)

### Fixed
- fix(run-gate): RW-37 follow-up -- restore line 1259's coverage after the fix stole it (d274bc73)
- fix(run-gate): RW-37 -- an absent schema key in an inflight record is corrupt, not a mismatch fall-through (0f866432)
- fix(run-gate): review round 2 nits N-a and N-b -- both one-liners, both taken (713887fc)
- fix(run-gate): RW-25 -- an inflight record of another schema is REFUSED, not disclosed-and-overwritten (3af55353)
- fix(run-gate): RW-28 -- a follower that outlives its owner is promoted, not left with an orphan (G2) (7fd45793)
- fix(run-gate): RW-29 -- the owner's identity is namespace-safe, and unknown means ALIVE (G3) (d16d9380)
- fix(run-gate): RW-27 -- silence is measured from the FILE, not from this client (G1) (43d66ba8)
- fix(run-gate): close collect's two uncontracted exits; round-2 nits (E5-R17/R18/R19) (c2105cf7)
- fix(run-gate): bound artifact downloads by stall, API calls by total time (E5-R16) (ef8396df)
- fix(run-gate): bound every request; point §3 at the LANE-AUTHORING rule (E5-R14/R15) (d58f122b)
- fix(run-gate): review round 1 — traversal, swallowed --dry-run, unbounded poll, exit codes (a777f109)
- fix(run-gate): RW-19 -- S7 + N1..N5 + hollow tests 2-6, and one real flake in the wave's own fixture (d7e280ce)
- fix(run-gate): RW-18 -- the dry run names the real outcome, and every path discloses the lane's bounds (S5+S6, R-39b/d) (ffc64903)
- fix(run-gate): RW-17 -- a COLLECTED run's duration is the container's own FinishedAt - StartedAt (S4, R-39c) (86f7f7b4)
- fix(run-gate): RW-20 -- a live owner whose container is already gone is re-polled before the refusal (R-39e) (cb6104a4)
- fix(run-gate): RW-15 -- drop `--since`; plain `docker logs -f` already replays from the first line (S1, R-39b) (a8dc5ebc)
- fix(run-gate): RW-16 -- "gone" is only "No such object", and the name is not the identity (S2+S3, R-39b) (3f157a3e)
- fix(run-gate): RW-13 -- a misplaced LANE key says "move it", and RG-32's impact is the parsed 13 (B1, R-08a) (074ae074)
- fix(run-gate): RW-14 -- a live owner's container is FOLLOWED, never hijacked (B2, R-39e) (73f2bb14)
- fix(run-gate)!: RG-32 -- `pins.*.budget` refused at load; pin tables validate their keys (rev 34, R-08a) (8db781e6)
- fix(run-gate): RG-35 -- a lane's container is found again after its client dies (rev 34, R-39) (6fe633f5)

### Changed
- merge(run-gate): resumable-gate wave (E-1) -- 23.4.0, rev 34 (2dfc45df)
- merge(run-gate): E-5 Buildkite seams 2+4 -- pipeline generator from --list, bk-lane.sh trigger/collector, 72 network-free tests, REMOTE-LANES manual updated (c79d374b)
- backlog(run-gate,assay): the progress/re-attach/unbounded-budget pattern as estate default -- RG-35..RG-37, B065..B067 (b57b2d12)

### Documentation
- docs(run-gate): renumber the wave's own RG-39/RG-40 -- real collision with main's RG-39 (f239001a)
- docs(run-gate): RW-38 -- RW-37's fix shipped an incomplete diff, caught by re-verifying the gate myself (5857045c)
- docs(run-gate): RG-39 exec-mutex review round 1 ACCEPT -- merge-ready, waiting on the wave to land first (62cd09ad)
- docs(run-gate): RG-39 exec-mutex implementer verified, gate GREEN -- merge sequencing decided, review round dispatched (1f601fdd)
- docs(run-gate): new backlog RG-39 (exec mutex) dispatched -- separate branch, plus a real ID-collision note for the wave's own merge (118f79a0)
- docs(ciu): v8 design set rev 3.2 / SPEC-V8 draft.5 -- round-2 delta audit folded (T2-01..T2-10 + every incomplete/broke audit row), canonical lock keys + ciu lease, [ciu] inherit, ciu8 decision recorded; demo ciu.toml files re-included in git (v7 **/ciu.toml rule hid them); RG-39 annotated as buildable; nyxloom NL-6 filed (ccbc02bf)
- docs(run-gate): round 3 NOT ACCEPT -- RW-37, RW-35 was wrong, fix-and-reverify (5c07b5c5)
- docs(run-gate): resumable-gate wave -- review round 3 (verification-only): NOT ACCEPT, RW-35's ruling not implemented (a5b2834a)
- docs(run-gate): correct RW-32 -- already implemented at 7fd45793, no addendum (5c2a77a0)
- docs(run-gate): fix package 2 landed at 661fde05, gate GREEN -- RW-32..RW-36 ruled (718070ee)
- docs(run-gate): fix round 2 -- gate verdicts and what was deliberately not done (661fde05)
- docs(run-gate): RW-30, RW-31, RW-26 -- a dated measurement with its command, the N5 path, and RG-34 closed (2cb91b4e)
- docs(run-gate): RG-39 -- no internal mutual exclusion around the container exec (1f312ab1)
- docs(run-gate): E-5 merged (c79d374b); run-gate review round 2 NOT ACCEPT -- RW-27..RW-31 ruled, fresh fix implementer for package 2, round 3 verification-only (7fb0adde)
- docs(run-gate): adversarial review round 2 -- NOT ACCEPT, 1 new blocker (4a7a490b)
- docs(run-gate): E-5 review round 2 ACCEPT -- E5-R17..R19 (fold SF1 + nits before the merge); LANE-AUTHORING §5 quotes the generator's exact glob (477f80f9)
- docs(run-gate): E-5 Buildkite seams 2+4 -- adversarial review round 2, ACCEPT (10dced36)
- docs(run-gate): fix successor returned green at 21e6bbea, RW-23..RW-26, reviewer round 2 dispatched; E-5 E5-R16 landed at ef8396df, round 2 dispatched (740f43b4)
- docs(run-gate): E-5 controller log -- E5-R14/R15 landed at d58f122b; E5-R16 bounds downloads by stall (59a2120f)
- docs(run-gate): fix round 1 part 2 -- LOG + REPORT, the live two-client probe, RW-20..RW-22 as ruled (21e6bbea)
- docs(run-gate): E-5 controller log -- round-1 fix package at 6dceaaf0 (65 tests); E5-R13..R15; curl timeouts follow-up then round 2 (4f23919d)
- docs(run-gate): REMOTE-LANES §3/§4/§6 corrected after review round 1 (6dceaaf0)
- docs(run-gate): E-5 review round 1 NOT ACCEPT -- E5-R7..R12 ruled; LANE-AUTHORING §5: remote-capable lanes keep artifacts under .assay/ (3f148522)
- docs(run-gate): E-5 Buildkite seams 2+4 -- adversarial review round 1, NOT ACCEPT (31efccca)
- docs(run-gate): E-5 controller log -- BK_QUEUE follow-up landed at 17077426, reviewer round 1 dispatched (7eb3beea)
- docs(run-gate): E-5 controller log -- Buildkite seams 2 and 4 landed on feature/run-gate-buildkite-seams; E5-R1..R6; BK_QUEUE follow-up then review (1dfc07ae)
- docs(run-gate): REMOTE-LANES §3/§4/§6 point at the real Buildkite tools (81ff037f)
- docs(assay,run-gate): limits reset -- three implementers dispatched in parallel (assay gen 11, run-gate fix successor, E-5 Buildkite seams) (c4cdd5e9)
- docs(run-gate): controller log -- the fix implementer's selftest verdict is quoted, not read; successor re-gates first (b30a900d)
- docs(run-gate): resumable-gate wave -- fix implementer checkpointed at e87007cc (RW-13/14/16 landed, gate green); RW-20..RW-22; no successor dispatched (session limit) (d615cc2e)
- docs(run-gate): fix round 1 -- LOG entry and continuation BRIEF 1 (E-008 cut) (e87007cc)
- docs(run-gate): LANE-AUTHORING.md + REMOTE-LANES-BUILDKITE.md -- sibling guides to CONSUMERS; E-5 (remote/async lanes) recorded in the post-v10 wave plan (780f9a98)
- docs(run-gate): resumable-gate wave -- reviewer round 1 NOT ACCEPT (2 blockers); RW-13..RW-19 ruled, fresh fix implementer dispatched (bdad6768)
- docs(run-gate): adversarial review round 1 -- NOT ACCEPT, 2 blockers (be7d94b3)
- docs(run-gate): resumable-gate wave -- follow-up package verified at 73e6b061, reviewer round 1 dispatched (70f676c4)
- docs(run-gate): RG-40 filed (RW-9) and --fresh's conjunction shape documented (RW-10) (73e6b061)
- docs(run-gate): resumable-gate wave -- implementer returned gate-green; controller log with RW-9..RW-12 (061fd861)
- docs(run-gate): wave records for rev 34 + RG-39 (coverage_gate dirty-tree line numbers) (d4e8e137)
- docs(run-gate): RG-34 addendum — second independent repro, broader scope (ff922ddd)
- docs(run-gate,assay): run-gate resumable-gate wave dispatched (E-1: RG-35/36/32/34 -> 23.4.0); operator rulings D1-D7 accepted, F015 leaves Wave D (DA-R21) (dc6e88c4)
- docs(run-gate,assay): RG-37/RG-38 id swap -- resume-state durability is RG-38, RG-37 is the ciu v8 session's container-derivation row (05e123d3)

### Testing
- test(run-gate): RW-29 -- cover the kernel that will not name the namespace (f4a46459)
- test(run-gate): cover the owner-liveness and follow-path edges (diff-coverage floor) (450b3e22)

## [23.3.0] - 2026-09-02
<!-- cmru: generated -->
<!-- cmru: source-end=b36c6925d1d8ff8bf6fd4b74de8a5bd9f3855dbe -->

### Fixed
- fix(run-gate): RG-33 -- every assay lane runs with --resume and --progress, judge floor refused by name (rev 33, R-38) (0a4862db)
  - Hand-authored detail (folded in post-release, the standing rule): every
    `kind = "assay"` lane is now invoked with `--resume --progress
    .assay/progress-<assay_lane>.jsonl`, unconditionally, on every runner.
    Measured cause: dstdns's `sql-mutation` lane re-tested the first of four
    target files from mutant #1 on three budget-capped retries because the
    argv never carried `--resume`. Both flags are no-ops on a lane without R2
    (assay's own contract), so R0/R1 lanes are unchanged; an R2 lane resumes
    from `.assay/mutation-state/` on retry and streams progress beside its
    verdict under the git-ignored `.assay/`.
  - **Consumer note (breaking for very old pins):** the flags need a judge
    that knows them — assay **>= 2.4.1**. A pin declaring an older `version`
    is refused at argv construction by name (lane, pin, version, floor,
    remedy); a pin without a declared version reaches the judge and fails
    loudly there. Re-pin before adopting rev 33 (in this estate only `cmru`
    was below the floor, at 2.3.0; re-pinned to 4.1.0 in `b36c6925`).

### Documentation
- docs(run-gate): RG-34 — schema lane argv doesn't template {worktree} into its own script path (eeda67ce)
- docs(run-gate): RG-33 — assay mutation lanes never pass --resume, retries restart from scratch (a04c95c2)
- docs(run-gate): RG-32 — pins.assay.budget is silently inert, real value lives in the consumer's assay.toml (8be4c6b9)

## [23.2.2] - 2026-09-01
<!-- cmru: generated -->
<!-- cmru: source-end=fad40555fb0f8125315f3811a8dcd95bea6db9c3 -->

### Fixed
- fix(run-gate): RG-31 -- assay_toolchain_findings routes --worktree through resolve_worktree_scope (rev 32) (0efd062e)

## [23.2.1] - 2026-08-31
<!-- cmru: generated -->
<!-- cmru: source-end=fe09688572dc7d744ba81b6b471eb4908599ffa6 -->

### Fixed
- fix(run-gate): RG-30 -- doctor/--check-env honor --worktree (rev 31) (89ca96ba)

### Changed
- backlog(run-gate): file RG-31 -- assay_toolchain_findings bypasses RG-30's validated worktree resolution (da535655)

### Documentation
- docs(run-gate): RG-30 FIXED + run-gate-P04 LOG/REPORT (60512539)

## [23.2.0] - 2026-08-31
<!-- cmru: generated -->
<!-- cmru: source-end=7c47a70710eac58697641d9ee2444a1ea0db8af3 -->

### Added
- feat(run-gate): RG-27 -- lane invocation history + the `history` query verb (rev 30) (1687b60d)

### Fixed
- fix(run-gate): RG-27 round-2 review -- B1 history read scope, B2 at-most-once flush, S1/S2 (0e6d0ea4)
- fix(run-gate): RG-27 -- record inline in main(), and cover the wiring in-process (afcdb39f)

### Changed
- backlog(run-gate): file RG-30 -- doctor/--check-env ignore --worktree, same pattern RG-27 just closed for history (5df35ce4)

### Documentation
- docs(run-gate): RG-27 -- P03 LOG/REPORT carry the real round-2 gate verdict (56d98572)
- docs(run-gate): RG-27 FIXED + run-gate-P03 LOG/REPORT (dbaccfe1)

### Testing
- test(run-gate): RG-27 -- in-process cover for the B1/S1 main() dispatch branches (dc3b1490)

## [23.1.0] - 2026-08-31
<!-- cmru: generated -->
<!-- cmru: source-end=1f47601c1b69a3503c4d94caca3ca90c373f8e0b -->

### Added
- feat(run-gate): RG-26 -- --base REF reaches a delegating assay lane as --request-base (7b30bc49)
- feat(run-gate): RG-25 -- doctor/--check-env preflight assay-lane toolchain fitness (9a403da3)
- feat(run-gate): RG-21 -- doctor names the linked-worktree host-lane git view (9adf11fc)

### Fixed
- fix(cmru,run-gate): RG-29 -- cmru/run-gate.toml's assay pin still named the vanished 2.2.0 sidecar (0ad5372d)
- fix(run-gate): P02 review round -- batch the fitness probe (B2), tell the truth about what dry-run and doctor start (B1/B3) (2f266885)
- fix(run-gate): RG-23 -- declare the env-forward breaking change and widen the drift sweep (c55f5748)
- fix(run-gate): RG-24 -- exec-mode container names resolve from the judged worktree (bd1a3f85)

### Changed
- backlog(ciu,run-gate): file CIU-75 -- backport v8 F2 identity source (breaking, ciu 7.6); retriage CIU-55 -> RG-27 -- gate invocation history + query verb (a78a0046)
- backlog(run-gate,ciu,assay): RG-25/RG-26 -- backport ciu CIU-72 (b)/(c) to the current gate; CIU-73 needs no code (b2884e76)
- backlog(ciu,run-gate): file CIU-71 -- build-context project-directory gap; RG-24 -- exec-mode container resolution is repo-scoped not worktree-scoped (92ae1917)

### Documentation
- docs(run-gate): usage() names the doctor/--check-env checks this bundle added (08783d09)
- docs(run-gate): P02 bundle LOG + REPORT (RG-21/23/24/25/26) (e8a6a34b)
- docs(assay): file the 2.1.0->2.3.0 review-gap audit and its backlog (B030-B034, RG-23) (142143a4)

## [23.0.0] - 2026-08-24
<!-- cmru: generated -->
<!-- cmru: source-end=f8178d9b0b821405f4f0fb8831d710056352193f -->

### Added
- feat(run-gate): adopt estate release orchestration with a diff-coverage floor (b6ec5d6a)

### Fixed
- fix(run-gate): resolve adversarial-review findings on the release-adoption program (db173082)
- fix(run-gate): run the selftest lane in host mode, not tester-unified (ca023b78)
- fix(run-gate): scope the selftest diff-coverage floor to run-gate.py alone (1c1d2fda)
- fix(run-gate): RG-22 — safe.directory global-config write survives pre-existing entries (9ad6388f)

### Changed
- backlog(run-gate): RG-22 — safe.directory overwrite fails when global config has multiple entries (2174d22e)

## [22] - 2026-08-24 — RG-sweep program complete

Backlog entries RG-1..RG-20 implemented (RG-18 excepted — dstdns-side scope,
see its backlog body), adversarially reviewed by two independent fresh
reviewers (findings fixed in rev 21–22 commits), then verified end-to-end.
Merged to main as `vbpub@91df32ee` (--no-ff, no conflicts).

### Rev map (one backlog item each unless noted)
- rev 4 RG-15 — assay lanes execute in the selected worktree
- rev 5 RG-11 — reserved exit codes: 2 config/refusal, 3 infrastructure
- rev 6 RG-4 — pins.version actually checked (whole-token grammar)
- rev 7 RG-16 — central configs may define shared lanes
- rev 8 RG-3 — dual-mount degeneracy fix
- rev 9 RG-5 — `{worktree}` substitution hardened (quoting/injection)
- rev 10 RG-6 — exec-mode refusal names the project-agnostic remedy
- rev 11 RG-17/RG-19 — required_env preflight + forwarding log + --check-env
- rev 12 RG-1 — conjunction override guard (`--worktree`/`--allow-dirty`)
- rev 13 RG-12 — failing-container evidence preserved (mode 0600)
- rev 14 RG-10 — artifacts key + unconditional verdict/evidence disclosure
- rev 15 RG-2 — `validate-pointers` verb + estate linkage meta-tests
- rev 16 RG-8 — `--dry-run` plan rehearsal on all three runners
- rev 17 RG-20 — resource-aware admission (slice budget from cgroupfs,
  shared-infra locks, lane `resources` key)
- rev 18 RG-9 — `doctor` preflight verb
- rev 19 RG-14 — wheel as second artifact (version derived from __revision__)
- rev 20 RG-13 — docs sweep + estate adoption retro ×9 projects
- rev 21–22 — adversarial-review hardening (SPEC header Rev 5 lists the
  rule-level deltas R-02..R-32)

### Verification ledger (2026-08-24, phase B/C)
- Suite: `pytest tests -q` → **205 passed** on the branch pre-merge and again
  on shared main post-merge.
- `--list`: all 9 consuming projects, rc=0.
- Every declared lane executed end-to-end (16 total): mdt/smoke, plesk/smoke,
  topos/py-compile, topos/topos-suite (2922 passed, diff-coverage 100% floor),
  pwmcp/tests (8 passed), nyxloom/tester-unified (diff-coverage 100% floor),
  srdm/unit, srdm/e2e (full systemd suite), ciu/ciu, cmru/{assay, coverage
  (1675 passed, 100%), canary, mutation, gate}.
- Assay judgment read SEPARATELY from each verdict artifact (LESSONS L4):
  assay claims R0–R3 PASS; ciu claims R0–R1 PASS (its declared rigor);
  cmru claim R0 PASS.
- cmru/gate conjunction rc=0 with every sub-invocation carrying explicit
  `--worktree {worktree}` — RG-1's guard exercised against a real daemon.
- cmru/mutation took its honest self-skip path (no changed src since
  cmru-v5.0.0), proving the KI-18 base-resolution contract.
- srdm/coverage **fails from a linked worktree** (covergate git plumbing hits
  the main checkout's gitdir, which gate.sh's single-subtree mount does not
  carry) and **passes on main** — environment topology, not a run-gate defect;
  run-gate's own exit-status passthrough and clean-tree refusal behaved
  correctly throughout. Filed as RG-21 with candidate directions.
- Merge held one foreign uncommitted edit (ciu/docs CIU-V7 proposal notes)
  via tagged stash round-trip; restored untouched afterwards.

### Re-copy obligation (drift marker = 22)
All nine in-repo consumers invoke `../run-gate-project/run-gate.py` through a
SYMLINK — zero re-copies owed inside vbpub. Any out-of-repo COPY of an older
revision (none known today) must re-copy before its next gate run.
