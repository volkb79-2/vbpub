# Lane-matrix progress — debian-install-update worktree

Source of truth across resumes. Update before every checkpoint.

Worktree: /workspaces/vbpub/.worktrees/debian-install-update, branch debian-install-update.

## Phase 0 — move debian_install_v2 → scripts/debian-install-v2/
Status: DONE (verified)

What happened:
- debian_install_v2/ was already git-tracked (AM state for inuse-partition-editor.py)
  so `git mv` worked for it; the other 4 items (debian-install-v2.py,
  known-shape.json, run-gate.toml, testing/) were untracked (`??`), so plain
  `mv` was used instead (git mv refuses untracked sources), then `git add -A`
  at the new location — git status now shows clean `A` (add), not add+delete,
  confirming git's own rename detection is happy.
- Fixed stale path references in: docs/CONSUMERS.md (2 refs),
  scripts/debian-install-v2/testing/README.md (4 refs),
  scripts/debian-install-v2/run-gate.toml (5 refs, worktree-relative argv),
  scripts/debian-install-v2/debian_install_v2/tests/test_inuse_partition_editor_r1.py
  (1 docstring ref), scripts/debian-install-v2/testing/run-privileged-tests.sh
  (1 usage-comment ref).
- Found and fixed a REAL bug the move exposed: test_r1_coverage.py:105 and
  test_r1_remaining.py:79 had `cwd="scripts/debian-install"` hardcoded
  (subprocess invocation of `python3 -m debian_install_v2.bootstrap --help`/
  `--action resume`), which broke under the new path. Fixed to be
  invocation-cwd-independent: `cwd=str(Path(__file__).resolve().parents[2])`
  (tests/ -> debian_install_v2/ -> scripts/debian-install-v2/, the dir
  `-m debian_install_v2.x` needs as cwd regardless of where pytest itself
  was invoked from). Verified both from within scripts/debian-install-v2 AND
  from the repo root (`pytest scripts/debian-install-v2 -q`).
- Dockerfile/docker-entrypoint.sh/run-privileged-tests.sh needed NO path
  fixes beyond the one comment above — they all resolve relative to
  `$HERE/..` or use docker build context args already passed at the call
  site (run-gate.toml, now fixed).
- Confirmed no v2 remnants left in scripts/debian-install/ (grep clean,
  ls clean, __pycache__ swept).
- DEBIAN-INSTALL-REVIEW.md / DEBIAN-INSTALLv2-REVIEW.md left at repo root
  untouched per brief (out of scope).

Verification: 172 passed, 2 skipped (real-commit tests correctly skip
without root/losetup), from both invocation cwds. `python3 -m compileall`
clean.

## Phase 1 — review + fix + tests: scripts/damon-analysis
Status: STEPS 1-3 DONE (verified). Step 4 (lane matrix) NOT STARTED — deferred
per operator steering to build lane matrices for all projects together later.

What happened (steps 1-3):
- Read every source file (analyze_container.py, analyze_process.py,
  batch_report.py, damon_cli.py, rcon_probe.py, visualize_memory.py,
  web_report.py, lib/damon_analysis.py) and every existing test file.
- Confirmed and fixed the flagship bug: rcon_probe.py's _pack/_recv_packet used
  unsigned struct formats (<III header pack, <II unpack) for a req_id field
  that is signed on the wire — the auth-failure sentinel is -1, which unsigned
  unpacking decodes as 4294967295, so `if req_id == -1` in rcon_connect() could
  never fire (RCON auth failures silently passed through as success). Fixed to
  <Iii pack / <ii unpack, matching the already-correct
  scripts/gstammtisch-guide/files/usr/local/sbin/soulmask_rcon.py (whose
  docstring says it was itself adapted from this file).
- Fixed 3 more real bugs found in review: batch_report.py's fmt_bytes() fell
  off its unit loop and implicitly returned None for >=1024 TiB values (the
  same function in lib/damon_analysis.py and damon_cli.py both already have
  the PiB fallback — batch_report.py's copy was missing it); analyze_container.py's
  main() had a dead if/else where both branches computed the exact same
  summary regardless of --mode (collapsed to one unconditional block);
  web_report.py did an unguarded os.listdir() on its own venv/lib/ path at
  MODULE IMPORT TIME, raising FileNotFoundError whenever no local venv exists
  (every other script in this toolkit guards this same lookup with
  os.path.isfile/os.path.exists — this one didn't).
- Fixed one dead existing test: test_classifier.py's
  test_nonzero_rate_below_warm_not_cold was a stub with body `pass` (asserted
  nothing). Replaced with two real tests covering the actual branch its name
  described: a nonzero-but-below-warm-threshold rate that's young enough is
  'warm' (transitional else-branch); the same rate once aged past cold_age_sec
  becomes 'cold' (rate==0.0 idle-check doesn't apply since rate is nonzero).
- Extended tests for the gaps step 1 surfaced: new tests/test_rcon_probe.py
  (rcon_probe.py had ZERO test coverage before this — the exact module where
  the flagship bug lived; now covers _pack/_recv_packet round-trip including
  the -1 sentinel, _recvn partial-read/early-close, rcon_connect() auth
  success/failure end-to-end via a FakeSocket + monkeypatched
  socket.create_connection, and the _read_proc_status_kb/_read_memory_high
  proc/cgroup helpers); new tests/test_batch_report.py (fmt_bytes unit
  boundaries + PiB-fallback regression test); new tests/test_web_report.py
  (import-survives-missing-venv-dir regression test, via
  pytest.importorskip('flask') since Flask isn't a declared dependency
  anywhere in this project and isn't installed in this environment — skips
  cleanly here, will actually run wherever Flask is present).
- Reviewed the new tests themselves (step 3): confirmed each would have
  FAILED against the pre-fix code (traced through what the old buggy code
  path would have produced for each assertion) rather than passing
  vacuously — not just re-asserting the implementation.

Verification: 145 passed, 10 skipped (9 pre-existing root/DAMON-sysfs skips +
1 new Flask-absent skip), 0 failed. `python3 -m compileall` clean.

Step 4 (lane matrix: r0-r1/r2/r3 + [lanes.gate] + assay.toml) intentionally
NOT done yet — operator steering 2026-08-27 said to get through Phase 1 AND
Phase 2's review+fix+tests+tests-review before the next checkpoint, and build
all 4 projects' lane matrices together afterward (they share the same new
assay r2-mutation-progress+resume capability, so batching them avoids
building that capability twice).

## Phase 2 — review + fix + tests: scripts/cgroup-profiler
Status: STEPS 1-3 DONE (verified). Step 4 (lane matrix) NOT STARTED — same
deferral as Phase 1, batched together at the lane-matrix stage below.

Note on the brief's "two unrun experiments" pointer: checked nyxloom-trove/
(only nyxloom.toml, the gate contract — no backlog/report dir), README.md,
DESIGN.md, ATTACH-GUIDE.md, and the full git log incl. commit bodies for
"experiment" — nothing found anywhere. The memory note was stale/
unsubstantiated; corrected in memory (cgroup-profiler-project.md + MEMORY.md
index) rather than carried forward or acted on.

Scale note: this project is ~17.4k lines (6.9k source across cgprofile.py +
16 lib/ modules, 10.5k tests across 17 test files) — 3.3x damon-analysis, and
notably more mature going in: DESIGN.md's own §1 states hard invariants
("absent is not zero", counters-never-go-negative, two dependency tiers), and
the project already had its OWN tools/gate.sh + tools/canary-run.sh (7 real
mutation canaries proving the suite rejects known-bad code) and a
nyxloom.toml gate contract, built by a prior session. Given the scale, step 1
was parallelized: 6 read-only review agents each covering the module/test
pairs DESIGN.md itself already partitions by original "agent A/B/C/D/E"
authorship (access+targets+util; metrics+limits; sampler+phases+events;
store+damon+caps; analyze+report_md; report_html), each given the relevant
DESIGN.md contract section and told to report structured findings only, not
edit files — I read cgprofile.py + model.py myself (the integrator + shared
types), then applied every verified fix myself against the real source
(trust-but-verify: several proposed fixes were checked/adjusted before
applying, see below). A local scratch venv
(`/tmp/cgprofile-review-venv`, from this project's own pinned
requirements.txt: pandas 3.0.5/plotly 6.9.0/ruptures 1.1.9/etc.) was built to
actually run the full suite including the pandas/plotly-dependent test files,
none of which have local deps installed in this devcontainer by default.

Real bugs found + fixed (source, not test-only):
- **lib/caps.py `_apply_one`** (found by store/damon/caps review agent,
  HIGH severity): a read failure on a cap's PRIOR value was conflated with
  the file genuinely reading "max" — both mapped to `old=None`. A transient
  read race between `_refuse_unless_safe()` and the actual write (the exact
  scenario the existing `test_cgroup_disappearing_between_validation_and_
  write_rolls_back` test already exercises for the cgroup-vanishes case) could
  make `_restore()` later write back the literal string "max" for a cgroup
  that never actually vanished, silently ERASING a real production limit
  instead of restoring it — the exact failure DESIGN.md §4.8/§7 is most
  emphatic about avoiding. Fixed: `old_text is None` now raises `CapsError`
  immediately (refuse this one change; `__enter__`'s existing rollback
  handles the rest), never silently treated as "was max". Updated the
  existing disappearing-cgroup test's expected exception type (CapsError,
  not OSError — same rollback invariant, now detected earlier/more safely)
  and added a new regression test
  (`test_transient_read_failure_refuses_instead_of_treating_value_as_max`)
  that proves a cgroup which never disappears still gets refused rather than
  corrupted.
- **lib/report_html.py `legend_name`** (found by report_html review agent,
  HIGH severity / security): a cgroup/container `Series.label` (untrusted —
  DESIGN.md §4.11 says so explicitly) was passed RAW into a plotly trace's
  `name` (the legend text) — every other surface this label reaches
  (hovertemplate, endpoint annotation, table row) already `html.escape()`s
  it; this one didn't. The reviewing agent verified end-to-end against the
  real pinned plotly 6.9.0 that plotly.js's own pseudo-HTML text-rendering
  pipeline turns an unescaped `<a href="javascript:...">` trace name into a
  real, clickable link in the self-contained report.html — a genuine XSS
  vector when the report is opened in a browser. Fixed with `html.escape()`;
  updated `test_malicious_label_escaped_across_every_surface` (which had
  documented and locked in the vulnerable behavior with a passing assertion)
  to expect the now-correct double-escaped form.
- **lib/report_html.py `_fmt_value`/`_finite_no_nan`**: guarded against NaN
  (with an explicit comment about pandas leaking one in via a 0-count
  resample bucket or 0/0 rate) but not `±inf` (the same bug class via `x/0`)
  — `int(round(inf))` raises `OverflowError`, taking the whole report down
  over one bad sample. Widened both guards from `math.isnan` to
  `not math.isfinite`.
- **lib/analyze.py `build()` never called `resample_frame()`** (found by
  analyze/report_md review agent, HIGH severity, the most involved fix):
  `resample_frame()` was fully implemented and unit-tested in isolation but
  never wired into the pipeline — `correlate`/`detect_changepoints` (via
  `auto_phases`)/`_phase_stats`/`_target_stats` all ran on the RAW,
  adaptively-sampled frame (sampler swings 0.25s-2s+ per DESIGN §4.4), so a
  hot burst's many closely-spaced rows silently outweighed a much longer
  quiet stretch's few rows in every mean/correlation/changepoint. Verified
  this was invisible to the existing suite because conftest.py's fixtures
  all use a fixed 1.0s cadence (a de-facto no-op for 1s resampling). Fixed:
  added `_ANALYSIS_RESAMPLE_STEP = "1s"` (below `_MIN_GAP_DEFAULT`=5s, so it
  can't itself blur two changepoints the min-gap dedup would keep distinct;
  matches report_html.py's own finest post-raw resolution step) and threaded
  a `resampled = resample_frame(df, _ANALYSIS_RESAMPLE_STEP)` through
  `auto_phases`/`correlate`/`_phase_stats`/`_target_stats` — `build_series`
  deliberately stays on the raw `df` (native-resolution charting is its own,
  separate concern per DESIGN §4.11). Added
  `TestBuildResamplesBeforeStats.test_target_mean_is_time_weighted_not_row_
  weighted` with a genuinely irregular-cadence fixture (10s quiet at 2
  samples vs 2s hot-burst at 20 samples) — empirically verified pre-fix mean
  ≈191 (row-weighted, fails the test's `<175` threshold) vs post-fix ≈160
  (time-weighted, passes) by running both code paths directly, not just
  asserting a single number.
- **lib/analyze.py `_flatten_group`'s multi-device io summation**: for a
  field one device didn't report this tick (kernel version skew, a partial
  parse), the sum silently included only the reporting device(s) — same
  "absent is not zero" violation as the already-tested single-device case,
  just unreached by it. Rewrote as a clean two-pass check (collect every key
  across all devices; a key missing OR None on ANY device excludes that
  field entirely for the tick, matching the single-device contract exactly)
  instead of the incremental conditional-sum that made the N-device case
  asymmetric. Verified the single-device existing test still passes
  unchanged; added
  `test_io_field_missing_from_one_of_two_devices_excludes_the_whole_field`.
- **lib/sampler.py `force_hot()` never wired to marks** (found by
  sampler/phases/events review agent): DESIGN.md §4.4's "any... phase mark
  ⇒ snap straight to hot_interval on the next tick" was fully implemented in
  `Sampler.force_hot()` (correctly unit-tested in isolation) but nothing in
  production code ever called it — marks are written by a different process
  sharing the run directory (`cgprofile mark`, a wrapper boundary, a
  `--log-tail` match) and the collector's `on_sample` never polled for new
  ones. Fixed: `cgprofile.py`'s `cmd_collect`'s `on_sample` now polls
  `phases_mod.load_marks(run.path)` each tick and calls `sampler.force_hot()`
  when the count grows. Added `force_hot`/`instances` tracking to
  `make_fake_sampler`'s test double and two new tests
  (`test_a_new_mark_snaps_the_sampler_to_hot_on_the_next_tick`,
  `test_no_new_marks_never_calls_force_hot`).
- **cgprofile.py `cmd_run`/`_start_run` process/container leak** (found by
  me reading cgprofile.py directly, not a sub-agent): if the wrapped command
  fails to launch (`subprocess.run` raising `FileNotFoundError`/
  `PermissionError` — never caught anywhere) OR if `_wait_for(READY_FILE,
  ...)` times out waiting for the collector to signal ready, the function
  unwinds without ever calling `_stop_run`, leaking the background collector
  process — or, in helper mode, an entire privileged Docker container —
  indefinitely (nothing else would ever write the STOP_FILE or signal it).
  Fixed both: `_start_run` now terminates `child` before re-raising if
  `_wait_for` times out (mirrors `_stop_run`'s own terminate→wait→kill
  pattern); `cmd_run`'s `subprocess.run(args.command, ...)` is now wrapped in
  try/except OSError that stops log tailers + the collector before
  converting to a clean `_err()` exit instead of an unhandled traceback with
  a leaked process. Added
  `test_command_launch_failure_still_stops_the_collector` and
  `test_ready_wait_timeout_terminates_the_child` — both verified failing
  before the fix (leak) and passing after.
- **lib/access.py `in_container()`**: read `/proc/1/cgroup` instead of
  `/proc/self/cgroup` — under `--pid=host` (exactly this tool's own helper
  mode), PID 1 is the HOST's real init, so this could report `False` while
  genuinely running inside the privileged helper. Low severity (only feeds
  `cgprofile doctor`'s diagnostic output, no branch depends on it) but a
  clear one-line fix.
- **lib/targets.py, 4 fixes** (found by access/targets/util review agent):
  `_CONTAINER_ID_RE` accepted 12-64 hex chars but resolution only ever
  matches a full 64-char id, so a valid-looking 12-63 char containerid: spec
  always failed — tightened to `{64}`. `find_container_cgroup`'s scope-
  pruning (`dirnames[:] = [...] or dirnames`) fell back to the UNFILTERED
  list whenever every sibling was a `.scope` dir — exactly the realistic
  layout this repo's own fixtures build — defeating the prune in precisely
  the case it exists for; dropped the `or dirnames` fallback (empty is the
  correct "prune everything" result). `resolve_label` never checked
  `docker ps`'s exit code, so a real docker failure (daemon down, permission
  denied) surfaced as "no matching containers" instead of the real error —
  added a returncode check matching the sibling `_docker_json` helper's
  existing pattern. `_looks_like_slice`'s regex had `\\-` inside a raw
  string, compiling to an escaped literal backslash inside the character
  class (so it wrongly accepted values containing `\`) — fixed to `.-`.
- **lib/store.py `append()`**: the single `os.write(fd, line)`'s return
  value was never checked, so a short write (POSIX-legal for a regular file
  under resource pressure, more plausible on the FUSE/virtiofs-style bind
  mounts this tool's own DESIGN.md describes it running on) would silently
  truncate the appended JSONL line. Diverged from the reviewing agent's
  proposed fix (retry-looping a second `os.write()`): this module's own
  documented contract requires EXACTLY ONE write() syscall per append for
  its interleaving-atomicity guarantee against concurrent O_APPEND writers
  — a second write call would reintroduce that exact hazard. Fixed instead
  by raising loudly on a short write (still one syscall attempted, failure
  surfaced instead of masked) rather than retrying.

Findings reviewed and deliberately NOT fixed (with reasoning, so this isn't
re-litigated later):
- store.py `write_manifest`'s tmp-file name uses only PID, a same-PID
  concurrent-thread collision risk — reviewing agent itself confirmed via
  grep this is currently unreachable (only ever called from cgprofile.py's
  single main-thread driver). Left as-is per "no speculative" guidance.
- targets.py `cgroup_of_pid`'s `root` parameter is accepted but unused —
  a discoverability/convention inconsistency (DESIGN §7 says every reader
  takes a root path), not a behavior bug (no fake-/proc concept exists for
  a running process's own cgroup). Would need a real `proc_root` parameter
  design, not a one-line fix — left as a design note, not acted on.

Test-suite-only improvements (step 2/3 of the discipline — extending/fixing
tests, not source):
- limits.py: added
  `test_effective_memory_high_tightest_wins_with_two_real_competing_values`
  — the reviewing agent PROVED (by actually flipping `_tightest_by`'s
  comparison direction in a /tmp copy) that every existing "tightest wins"
  assertion in test_limits.py only ever has ONE non-None value in the
  relevant chain, so the comparison direction itself was never exercised;
  the fixture's own LEAF chain already has two real competing memory_high
  values (wings.slice 14G vs wings-prod.slice 6G) that no test used. I
  independently re-verified the new test fails under the same flipped
  mutation and passes against the real code before trusting it.
- metrics.py: sharpened `test_sample_cgroup_memev_uses_local_not_
  hierarchical` — the fixture wrote identical content to both
  memory.events and memory.events.local, so the test could only prove a key
  existed, not which file was actually read. Now writes genuinely different
  values to the two files in-test and asserts on the value that would
  differ (999 vs 5) if the reader ever regressed to the hierarchical file.

Verification: 910 passed, 0 failed, 0 skipped, run against the real pinned
report-tier deps in /tmp/cgprofile-review-venv (this project has no local
venv checked in — `./setup.sh` builds one from requirements.txt; the scratch
venv is NOT part of the repo, rebuild with
`python3 -m venv /tmp/cgprofile-review-venv && /tmp/cgprofile-review-venv/bin/pip
install -q -r requirements.txt` if resuming in a fresh shell).
`python3 -m compileall` clean on lib/, cgprofile.py, and tests/.

## Commits (this branch, this session)
All Phase 0-2 work plus the assay worktree sync are now REAL COMMITS, not
just working-tree edits — required because assay's own R1+/R2 snapshot
mechanism judges committed git state, not the working tree (confirmed:
`assay plan` refused `GIT_FAILED` naming the old stale HEAD before this).
In order: `276c1912` chore(assay): sync worktree to origin/main
(assay-v2.4.2) — this worktree's assay/ had been stuck at 6c28153c
(2026-08-25), missing B016/B030-B034 including the exact `--progress` flag
this program's r2 tier depends on; brought current via a scoped
`git checkout origin/main -- assay/` (0 ahead/102 behind, a pure ancestor,
zero conflicts outside assay/ — verified via `git diff --name-only
$(merge-base)..origin/main -- scripts/*` before running it). CAUTION FOR
FUTURE RESUMES: that checkout, run before I'd checked per-file dirty status
first, silently discarded one genuine uncommitted edit to
`assay/docs/CONSUMERS.md` (the "Inside vbpub? read INTERNAL-CONSUMERS.md
instead" redirect paragraph) — recovered verbatim from my own prior Read
tool output in the same turn and confirmed byte-identical via line-offset
diffing, but this was a real near-miss; every other file that showed "M"
after the checkout was NOT a lost edit, just a normal, expected diff
between the stale commit and origin/main (verified against this
conversation's very first git-status snapshot, which showed only
`assay/docs/CONSUMERS.md` as dirty under assay/ before the checkout ran).
Lesson for next time: `git status`/`git diff` the SPECIFIC target path
before any checkout/discard-risk command, even a "just sync a subtree"
one that feels routine. `9ab17639` feat(scripts): debian-install-v2 move +
r2/r3/gate lanes. `3c4c5803` fix(damon-analysis): Phase 1 review+fix+tests.
`95ace544` fix(cgroup-profiler): Phase 2 review+fix+tests. `7f05dfab`
refactor(gstammtisch-guide): monitor split (pre-existing uncommitted work
from earlier in this task, committed as-is) + r2->docker test rename.
`44664150` docs: AGENTS.md/INTERNAL-CONSUMERS.md assay live-install policy
+ README.md path fix. Also added a root `.gitignore` entry for
DEBIAN-INSTALL-REVIEW.md/DEBIAN-INSTALLv2-REVIEW.md/custom_script.output
(pre-existing untracked root cruft, out of scope per the original brief) —
NOT touching their content, just stopping them from permanently tripping
assay's repo-wide dirty-tree check, which has no --allow-dirty escape
hatch unlike run-gate.py's.

## Lane matrix (all 4 projects: gstammtisch-guide, debian-install-v2,
## damon-analysis, cgroup-profiler)

**Assay capability check (per operator correction, superseding the
"upstream progress+resume" item below): DONE, no new assay work needed.**
`assay run --help`/`assay plan --help` confirm `--resume`, `--shard`,
`--progress` are all live (B012/B013/B030-B032, released in assay-v2.4.2,
now present in this worktree's own assay/ after the sync above). A
tangential concern about B034's operator-withdrawal (`python:
uuid-equality-swap`/`enum-comparison-swap`) looking unimplemented turned
out to be a false alarm caused by MY OWN worktree being stale at the time
I checked — `WITHDRAWN_MUTATION_OPERATORS` is real and shipped, confirmed
after the sync. No backlog entry was filed (investigation abandoned once
the premise was found to be stale-worktree noise, not a real gap).

**Zipapp pinning is DROPPED for this task** (operator correction,
supersedes the original cmru-reference-implementation plan below). Every
r2 lane installs assay live from the bind-mounted worktree at run time
(`pip install -q -e {worktree}/assay`, matching `assay/docs/
INTERNAL-CONSUMERS.md`'s mechanism) and invokes the bare `assay` command
(resolves via PATH after the install — this devcontainer's `/home/vscode/
.venv/bin` is already on PATH, matching r0-r1's existing bare-`python3`
style; do NOT hardcode a venv path in any lane argv). A `[lanes.X]` in
argv[0] using a bare executable name (`python3`, not an absolute path)
REQUIRES `env_passthrough = ["PATH"]` in the project's own assay.toml, or
assay refuses to load the lane at all (`BAD_LANE_CONFIG`, confirmed
live) — apply this to every project's assay.toml, not just
debian-install-v2's.

**Real per-project run-gate.toml lane naming confirmed against
`run-gate-project/SPEC.md`** (read in full this session): `kind =
"command"|"assay"` (this task only ever uses `"command"`, per the
zipapp-drop above), `environment = "host"|"tester-unified"` (declared
centrally in the root `run-gate.toml`'s `[environments.tester-unified]`,
inherited by every project unless shadowed). `environment = "host"` for a
lane means run-gate.py execs the argv directly with no container, no
placement, no dual-mount — appropriate for anything that does no real
disk/systemd/privileged mutation (matches debian-install-v2's existing
r0-r1/fake-integration precedent). `environment = "tester-unified"` gets
FULL container orchestration natively from run-gate.py itself (SPEC.md
R-15/R-16/R-19a/R-23: detached run, dual mount, cgroup placement +
LoadState verification, safe.directory, separate `docker wait`/`docker
logs` verdict read) — this is a more mature, generalized version of what
cgroup-profiler's own hand-rolled tools/gate.sh does for itself. **Design
decision (resolves the open question below): do NOT generalize
cgroup-profiler's gate.sh/canary-run.sh into a shared script, and do NOT
migrate cgroup-profiler off them either** — keep them as project-owned
scripts (matching the "srdm forked, cgroup-profiler extends the shared
image but keeps its own scripts" precedent already recorded in its own
nyxloom.toml), wired into run-gate.toml as `environment = "host"` command
lanes that just `exec tools/gate.sh {worktree} coverage` /
`exec tools/canary-run.sh` — run-gate.py's own container orchestration is
reserved for lanes that don't already self-contain their own docker calls
(that's exactly cgroup-profiler's r2 mutation lane, the one new thing it
needs: `environment = "tester-unified"`, since assay's own R2 execution
needs the pandas/plotly report-tier deps tester-unified:local's Dockerfile
already builds in).

**Assay lane config facts learned by trial-and-error against the real
CLI, worth recording so the next project's assay.toml doesn't re-hit
them:** (1) rigor requirements are PER DECLARED LEVEL not cumulative — an
`R2`-only claim (`rigor = ["R0", "R2"]`) needs just `language`,
`source_roots`, `judge.mutation`, `judge.base`; it does NOT need R1's
`coverage`/`fail_under` fields, so r2 lanes don't duplicate what the
project's own r0-r1 pytest-cov invocation already measures. (2) Every
R1+/R2/R3 lane needs `[lanes.X.isolation] snapshot_selection =
"repository-minus-unsafe-symlinks"` plus the exact same 3-entry
`unsafe_symlink_omissions` list cmru's own lane uses (Topos's tracked
hostile symlink fixtures) — `"repository"` mode refuses EVERY monorepo
lane permanently the moment it's tried, since the snapshot walks the
whole resolved commit, not just the project's own source roots. (3)
`judge.mutation.operators`: use exactly `python:compare-swap`,
`python:boolop-swap`, `python:bool-const-flip`, `python:falsy-swap` —
never the two withdrawn B034 names. (4) assay's own R3 "canary" concept
(`judge.canary.mechanism = "uncovered-line"`) is NOT what this task means
by r3 — it's a narrow meta-test of the harness's own fault-reporting
pipeline, not "inject a known mutation, confirm a named test catches it".
This task's r3 tier uses a PROJECT-OWNED canary script (cgroup-profiler's
tools/canary-run.sh pattern), never assay's own R3 vocabulary — assay.toml
per project therefore only ever declares `rigor = ["R0", "R2"]`, no R1, no
R3. (5) assay's own dirty-tree check has NO --allow-dirty escape hatch
(unlike run-gate.py's `clean_tree = false`/`--allow-dirty`) — an untracked
file ANYWHERE in the repo blocks every project's R1+/R2 lane; see the
.gitignore fix above.

### debian-install-v2: DONE and LIVE-VERIFIED
[lanes.r0-r1] (pre-existing), [lanes.r2] (new, assay mutation — installs
live from {worktree}/assay, judges debian_install_v2/ against
origin/main, 190 real mutation candidates found via `assay plan`; a real
partial `assay run --shard 0/20` was blocked by DIRTY_TREE until the
.gitignore fix above landed — not yet re-run after that fix, see next
step), [lanes.r3] (new, 3 real canaries: obsolete-v1-config-accepted,
dry-run-executes-anyway, command-allowlist-disabled — all 3 verified
LIVE via `./run-gate.py r3`, real rejection confirmed), [lanes.gate]
(new aggregator, `--dry-run`-verified). run-gate.py symlink added.
assay.toml added (rigor R0+R2 only, per the "assay lane config facts"
above). fake-integration/r1-privileged-commit stay as extra, non-
aggregate lanes exactly as before. NEXT STEP for this project: re-run
`assay run r2 --shard 0/20 --resume --progress ... --verdict-json ...`
now that the tree is clean, to confirm real execution (not just `plan`)
end to end before considering r2 "verified" rather than merely "wired".

### damon-analysis, cgroup-profiler, gstammtisch-guide: NOT YET BUILT
Same 4-lane scheme as debian-install-v2 above. cgroup-profiler reuses its
own tools/gate.sh (r0-r1, target=coverage) and tools/canary-run.sh (r3)
verbatim as `environment = "host"` command-lane bodies (see design
decision above) — needs only run-gate.toml + run-gate.py symlink + a new
assay.toml for r2 (environment = "tester-unified", since it needs
pandas/plotly). damon-analysis and gstammtisch-guide need everything
built fresh, including new tools/canary-run.sh scripts (debian-install-v2's
tools/canary-run.sh, above, is now the reference template — same
structure as cgroup-profiler's original, just reads DEBIAN_INSTALL_V2_
PYTHON instead of doing TESTER_VENV/venv resolution, since these two
projects run r0-r1/r3 host-scoped with bare `python3`).

## Open questions
RESOLVED this session (see "Real per-project run-gate.toml lane naming
confirmed..." above): cgroup-profiler's gate.sh/canary-run.sh pattern
stays project-owned, not generalized into a shared script, and cgroup-
profiler is not migrated onto run-gate.py's native container
orchestration for its existing lanes (only its NEW r2 lane uses it).

## Gate state (12 lanes = 4 projects x 3 tiers)
debian-install-v2: r0-r1 DONE (pre-existing, still green), r2 WIRED but
not yet live-verified past `assay plan` (see next step above), r3 DONE
and LIVE-VERIFIED. cgroup-profiler's pre-existing [gates.unit]/
[gates.coverage]/[gates.canary] (nyxloom.toml) still pass today and are
being left in place, not replaced — its run-gate.toml wrapper is separate,
new, and not yet built. damon-analysis and gstammtisch-guide: all 3 tiers
NOT YET BUILT for either.
