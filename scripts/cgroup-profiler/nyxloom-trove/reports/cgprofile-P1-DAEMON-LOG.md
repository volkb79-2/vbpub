# cgprofile-P1-DAEMON — implementer LOG

Package: cgprofile-P1-DAEMON (RG-55 wave, package P1). Worktree
`.worktrees/rg55-profiler-daemon`, branch `rg55-profiler-daemon`, base
`e499a168` (P0 freeze commit). This LOG covers implementer session 1
(fresh Sonnet, checkpoint clause armed).

## Orientation

Read in full before any edit: `RG55-INTERFACE-CONTRACT.md` (all of §1-§7),
`fixtures/rg55/README.md` (the by-hand derivation), the P0 controller log
(`run-gate-WAVE-RG55-CONTROLLER-LOG.md`, rulings RW-1..RW-4), the five
frame trees under `fixtures/rg55/frames/0..4` (every file, cross-checked
against the README's arithmetic by hand before writing any code),
`lib/util.py`, `lib/metrics.py`, `lib/damon.py` (all in full), `cgprofile.py`
around `cmd_targets`/`build_helper_spec` (RW-3's exact site), the existing
`tests/conftest.py` and `tests/test_cgprofile.py`'s `TestCmdTargets` class,
`pyproject.toml`, `run-gate.toml`, `tools/gate.sh` (in full — needed to
understand what the coverage lane actually mounts, which changed the design
of the fixture-identity test), `tools/canary-run.sh` (header only).

Orientation call count: approximately 20 tool calls (reads + greps) before
the first edit (the RW-3 one-liner).

**Read-list items NOT read this session** (§1 of the handoff) — flagged
honestly per §5's instruction, deferred to whoever picks up C2 onward:
- `DESIGN.md` (§1, §3, §4.4, §4.7, §4.8, §6/§6a) — not read at all.
- `ATTACH-GUIDE.md`, project `README.md` — not read at all.
- `lib/sampler.py`, `lib/targets.py`, `lib/store.py`, `lib/events.py` (only
  its module docstring seen via an incidental grep), `lib/limits.py`,
  `lib/access.py` (only `build_helper_spec`'s signature, not the whole
  file), `lib/caps.py` — not read.
- `tests/test_sampler.py`, `tests/test_damon.py` (the "one sampler test
  file, one damon test file" convention read) — not read; conventions were
  instead inferred from `tests/conftest.py` and `tests/test_cgprofile.py`.
- `assay.toml`, `nyxloom-trove/nyxloom.toml` — not read.
- Item 5 (pwmcp shape: `cmru.toml`, `build-push.py`, `docker-bake.hcl`,
  `bundle.toml`, the `ciu.*.toml.j2` templates, `pwmcp/README.md`, root
  `cmru.orchestration.toml`) — not read at all.
- Item 6 (`ciu/src/ciu/governance.py` lines 290-340/1395-1445, `ciu --help`)
  — not read at all (also FORBIDDEN to edit, per §7; reading it is allowed
  and required before C7, just not done yet).
- Item 7 (`dev-interactive.slice.in`, plan §7 memory rule text) — not read.
- The plan of record itself, `WAVE-PLAN-2026-09-12-rg55-profiling.md` §1-3,
  5"P1", 6-10 — **not read**. Its decided content was instead taken
  secondhand from the handoff (which states "every decision below is
  DECIDED") and the frozen contract/controller-log, which is what C0/C1
  actually needed. This is the most important gap for a successor to close
  before touching C4 (`serve.py`)/C7 (ciu stack), since those two
  deliverables lean on plan sections (§7 HOST LOAD is already reproduced
  verbatim in the handoff §6, but D-1..D-16 product decisions referenced
  only by number are not).

None of the read-list items were wrong (nothing contradicted the handoff);
the gaps above are purely "not yet reached", not "found stale".

## Commits

### 1. `688ea7bb` — `fix(cgprofile): RW-3 -- cmd_targets helper spec must mount DEFAULT_OUT, not HERE`

**What:** `cmd_targets`'s helper-mode branch (`cgprofile.py`, was line 685)
called `access.build_helper_spec(HERE, HERE, ...)`, mounting the repo
directory itself as the helper's *output* dir instead of `DEFAULT_OUT`
(`cgprofile.py`'s `runs/` dir) — the same shape of bug
`build_helper_spec`'s second argument exists to prevent, and the one
`cmd_run`'s own call site (line 366, unaffected) already gets right.
Strengthened `TestCmdTargets::test_helper_mode_delegates_through_a_docker_
run_of_itself` to capture and assert the `repo`/`out` arguments (previously
only `cgroup_parent` was checked), so a regression here is caught by the
suite itself, not just by re-reading the diff.

**Gate:** `nice -n 19 ionice -c 3 python3 -m pytest tests/test_cgprofile.py -q`
(local devcontainer venv, pre-existing report-tier deps installed via pip
for iteration speed — NOT a ship verdict on its own) — 153/153 passed. Full
ship-grade verdict for this commit is folded into the C1 gate run below
(gate.sh runs the whole `tests/` tree every time; there is no
per-commit-only gate lane in this project).

### 2. `ba7a3167` — `feat(cgprofile): C1 -- lib/summary.py, the incremental Summary accumulator (RG55 contract §7)`

**What:** `lib/summary.py` (`SummaryAccumulator`) — see the REPORT for full
evidence. `tests/test_summary.py` (20 tests: 2 golden byte-for-byte
reproductions, a `targets_seen` union check, a fixture-tree-identity check,
and branch coverage for every null-discipline path). Vendored
`run-gate-project/nyxloom-trove/fixtures/rg55/` verbatim into
`tests/fixtures/contract/` (read-only copy; `run-gate-project/` itself
untouched, per the forbid list).

**Gate (ship-grade, run AFTER this commit, in `tester-unified:local`):**
- `nice -n 19 ionice -c 3 ./tools/gate.sh /workspaces/vbpub/.worktrees/rg55-profiler-daemon coverage`
  → **exit 0**. `933 passed` (whole suite). Coverage: **100% line, 100%
  branch** across every module in `[tool.coverage.run] source` (`lib` +
  `cgprofile`), including the new `lib/summary.py` (193 stmts / 54
  branches, 0 missed either way). No `pyproject.toml` change was needed —
  `source = ["lib", "cgprofile"]` is directory-based and already covers a
  new module dropped into `lib/`.
- `nice -n 19 ionice -c 3 ./run-gate.py --worktree /workspaces/vbpub/.worktrees/rg55-profiler-daemon r3`
  → **exit 0**. `canaries: 7 rejected, 0 survived`. Verdict re-read
  separately from `.run-gate/history.json` (not just the streamed log):
  `{"lane": "r3", "outcome": "pass", "exit_code": 0, "commit":
  "ba7a3167a35620cab4ef850c34357929d15ca887", "revision": 40}`.
- `r2` (assay mutation lane) **not yet run** — the handoff says run it ONCE
  before the final return, after everything else is green; this is
  checkpoint 1 of what will be a multi-session package, so it is deferred
  to whichever session makes the last commit before the P1 return, per
  §3's own wording ("ONCE before your return").
- 11 hand mutants run against `lib/summary.py` before the gate (see
  REPORT for the full list and outcomes) — all 11 caught by
  `tests/test_summary.py`.

Host load check before each gate run: `docker ps --format
'{{.Image}}'` showed 0 `tester-unified` containers running (both times);
no concurrent image build; the coverage-lane container removed itself via
`gate.sh`'s own `trap`; the r3 lane's container also self-removed (`docker
ps` confirmed clean afterward). `/proc/loadavg` before starting: `3.44 3.43
3.05` on an 8-core host also running production containers (dstdns stack,
wings) — within the expected shared-host band, not idle.

## Checkpoint

Stopping here (E-008): after 2 green-gate commits, at a clean boundary
(LOG/REPORT/BRIEF write), well short of exhausting C0-C9. See
`cgprofile-P1-DAEMON-BRIEF-1.md` for the successor's starting point.

---

## Session 2 (fresh Sonnet, checkpoint clause armed)

### Orientation

Read before any edit, in order: `BRIEF-1.md` (this file's predecessor
checkpoint), the handoff in full, `RG55-INTERFACE-CONTRACT.md` in full
(re-confirmed, not assumed), `run-gate-project/nyxloom-trove/fixtures/rg55/`
directory listing (confirmed no per-pid/environ data in the golden frames —
`lib/subtree.py`'s tests build their own fake `/proc` trees, as BRIEF-1
anticipated), `lib/util.py` and `lib/metrics.py` in full (the "parse past
the last `)`" convention in `_proc_cpu_usec`, reused verbatim in
`subtree._parse_ppid`), `lib/targets.py` in full (`find_container_cgroup`,
`Membership.refresh`'s appeared/disappeared pattern — informed
`SubtreeResolver.refresh`'s own running-union shape), `lib/summary.py` in
full (`read_cgroup_pids`, reused directly rather than re-implemented;
`SummaryAccumulator`'s `_pids_seen` running-union convention, mirrored in
`SubtreeResolver.targets_seen`/`seen_pids`), `tests/conftest.py` in full
(fixture-building conventions; no ready-made pid/environ fixture existed,
confirming a bespoke `mkproc`/`mkcgroup` pair in `test_subtree.py` was the
right call), `tests/test_summary.py` header + helper functions (frame/golden
conventions, confirmed not needed for C2's own tests), the plan of record
`WAVE-PLAN-2026-09-12-rg55-profiling.md` §4.3 (target resolution, confirms
"scan `cgroup.procs` pids' `/proc/<pid>/environ`, then follow descendants at
the discovery interval" matches the handoff exactly), §4.4 (DAMON
multiplexing, needed for C3 next), §5 "P1" (matches handoff scope 1:1, no
new information), §10 (risks — CIU-107 fallback path for C7, confirmed).
`tools/gate.sh` and the exact gate invocations were taken from session 1's
own LOG rather than re-read from scratch (`tools/gate.sh
<worktree> coverage`, `run-gate.py --worktree <worktree> r3`).

Orientation call count: 9 tool calls (reads + one targeted grep + a `wc -l`
survey) before the first edit (`lib/subtree.py`). Session 1's read-list gap
list (LOG lines 25-52) is **not fully closed** by this session — see below.

**Read-list items still not read** (carried forward honestly): `DESIGN.md`
§1/§3/§4.4/§4.7/§4.8/§6-§6a, `ATTACH-GUIDE.md`, `README.md`, `lib/sampler.py`,
`lib/store.py`, `lib/events.py`, `lib/limits.py`, `lib/access.py` (whole —
only `CGROUP_ROOT`/`docker_bin` grepped), `lib/caps.py`, `lib/damon.py` (not
re-read this session; session 1 read it in full and C3 — next — needs it in
full again, so the successor doing C3 should treat it as unread-this-session
and re-open it), `tests/test_sampler.py`, `tests/test_damon.py`, the pwmcp
shape (item 5), ciu governance lines (item 6), `dev-interactive.slice.in`
(item 7). None of these were needed for C2 specifically (a pure `/proc` +
`cgroup.procs` resolver with no DAMON, socket, image, or ciu surface); all
of them are needed before or during C3/C4/C6/C7 and must be read then. No
read-list item was found wrong or stale this session.

### Commits

#### 3. `b2481a34` — `feat(cgprofile): C2 -- lib/subtree.py, token subtree resolver (RG55 contract §2.2/§7)`

**What:** `lib/subtree.py`'s `SubtreeResolver` — given an absolute cgroup
path and a token, `refresh()` returns the pid set: every pid in
`cgroup.procs` whose `/proc/<pid>/environ` carries an *exact*
`RUN_GATE_PROFILE_SESSION=<token>` entry (NUL-split membership check, not a
substring match — a prefix-token false positive was one of the tests),
unioned with every descendant discovered from those owners. Descendant
discovery: fast path `/proc/<pid>/task/*/children` (support detected once
per `refresh()` call, from whichever owner is still alive to answer, so one
owner having already exited does not wrongly trigger the fallback); ppid-map
fallback (`/proc/*/stat`, parsed past the last `)` exactly like
`metrics._proc_cpu_usec`) when the children interface is absent. A pid named
by a live parent's children list but already gone by the time it is probed
further is still attributed (it was real at discovery time) but contributes
no further descendants. `targets_seen`/`seen_pids` are a running union
across `refresh()` calls, mirroring `SummaryAccumulator._pids_seen` — a pid
that appears for one discovery tick and exits before the next still counts,
per contract §3 "distinct pids ever attributed".

Internal design note not in the handoff (implementer's own call, flagged as
a decision-adjacent judgment rather than a re-opened decision): the handoff
says the discovery interval is a server-owned cadence ("re-resolve every
discovery interval"), so `SubtreeResolver` itself carries no clock/timer —
`refresh()` is a pure re-scan the session server's sampling loop calls at
whatever cadence C4 implements. This matches BRIEF-1's own suggested shape.

**Tests:** `tests/test_subtree.py`, 24 tests across 5 classes —
`TestTokenOwnership` (owner-found, exact-match-not-substring,
unreadable-environ-skipped, vanished-cgroup), `TestTaskChildrenFastPath`
(two-level descendants, diamond shared-descendant counted once, a
child-listed-but-already-gone pid still attributed, one owner vanished
before probe does not block detecting support from a second owner),
`TestPpidMapFallback` (falls back when the children file is absent vs. the
whole `task/` dir is absent, non-numeric/unreadable `/proc` entries ignored,
a leaf owner, and an owner that is *also* a descendant of another owner —
exercises the ppid-map walk's own "already visited" branch, which the
task-children diamond test exercises for its own function but does not
share coverage with), `TestRunningUnion` (targets_seen survives a pid
disappearing; stays zero when no owner is ever seen), `TestInternals` (9
direct-unit tests on `_parse_ppid`/`_direct_children_via_task`/
`_build_ppid_map`/`_environ_has` edge cases awkward to reach end-to-end —
same convention `tests/test_targets.py` uses for `_split_spec`/
`_parse_options`/`_looks_like_slice`, confirmed by grep before writing them
this way rather than asking a decision-ask). 4+4+5+2+9 = 24, matching
`pytest --collect-only`'s own count exactly (verified after the fact, since
an earlier draft of this entry mis-added the classes' sizes to 33 —
corrected here; no test content changed, only this record's arithmetic).

**Gate (ship-grade, run AFTER this commit, in `tester-unified:local`):**
- `nice -n 19 ionice -c 3 ./tools/gate.sh /workspaces/vbpub/.worktrees/rg55-profiler-daemon coverage`
  → **exit 0**. `957 passed` (whole suite, up from 933). Coverage: **100%
  line, 100% branch** across every module in `[tool.coverage.run] source`
  (`lib` + `cgprofile`), including `lib/subtree.py` (128 stmts / 40
  branches, 0 missed either way). No `pyproject.toml` change needed
  (directory-based `source`, confirmed by C1's own note).
- `nice -n 19 ionice -c 3 ./run-gate.py --worktree /workspaces/vbpub/.worktrees/rg55-profiler-daemon r3`
  → run once before the commit against the dirty tree (sanity: `exit 0`,
  `7 rejected, 0 survived`, but `.run-gate/history.json`'s `lanes.r3.latest`
  correctly marked `"history_eligible": false` /
  `"excluded_reason": "the judged tree was dirty"` — read in a SEPARATE step,
  not off the streamed log), then **re-run after the commit** on the clean
  tree for the real record: verdict read separately from
  `.run-gate/history.json`: `{"commit": "b2481a34d358a4d2772370bc318b3dc972161d25",
  "dirty": false, "exit_code": 0, "outcome": "pass", "history_eligible": true,
  "revision": 40}`.
- `r2` (assay mutation lane) still **not run** — per the handoff, ONCE
  before the final P1 return, run by whichever session makes the last
  commit. Not this session (C3-C9 remain).
- No hand-mutants required for `lib/subtree.py` specifically — handoff §3's
  "at least 10 hand-mutants" instruction names `summary.py` only (C1); the
  eventual single `r2` mutation lane covers every changed line including
  this module.

Host load check before each gate run: `docker ps --format '{{.Image}}'`
showed 0 `tester-unified` containers running (checked 3 times: before the
local sanity run, before the coverage gate, before each `r3` run); no
concurrent image build; both gate containers confirmed self-removed
afterward. `/proc/loadavg` before the coverage gate: `6.00 4.79 4.29` on
the shared 8-core host; `/proc/pressure/memory` `some avg10=0.10
full avg10=0.10` — comfortably below saturation. All iteration pytest runs
were serial (`nice -n 19 ionice -c 3 python3 -m pytest`, never `-n`); the
whole `tests/` suite ran twice outside the gate container (once
`test_subtree.py` alone with `coverage run --branch` to check the new
module's own number before paying for a gate container, once the full
`tests/` tree in the local devcontainer venv as a pre-gate sanity check) and
once inside the real gate container — within budget.

### Checkpoint

Stopping here (E-008) at a clean boundary: one green-gate commit landed,
LOG/REPORT/BRIEF-2 written, well short of a tool-call or context limit but
matching BRIEF-1's own framing ("C2 through C9... realistically several more
sessions") and this package's standing preference for "finishing a
deliverable over starting the next one" — C3 (`KdamondPool`) is a
red-first, fake-sysfs-tested stateful module that deserves its own clean
budget rather than a partial start here. See
`cgprofile-P1-DAEMON-BRIEF-2.md` for the successor's starting point.

---

## Session 3 (fresh Sonnet, checkpoint clause armed)

### Orientation

Read before any edit, in order: `BRIEF-2.md`, `lib/damon.py` in full
(re-read fresh per BRIEF-2's own instruction — session 1 read it, session
2 didn't re-open it), `tests/test_damon.py` in full (never read by either
prior session), `scripts/damon-analysis/lib/damon_analysis.py`'s
`SysfsInterface` (grepped method list, then read `create_kdamond`/
`create_context`/`create_target`/`create_scheme` in full — all share the
same "read current count, write only if the new index needs more" grow-only
idiom) and `Classifier` in full (never read by either prior session). Also
re-read the handoff's C3 paragraph fresh (denser than the plan summary, per
BRIEF-2) and the frozen contract §1-§7 in full (`RG55-INTERFACE-CONTRACT.md`,
confirming the exact `damon` block shape and `thresholds` keys). Checked
`lib/summary.py`'s `SummaryAccumulator.add_sample`/`_damon_block` (lines
203-417) to confirm the exact `{"hot"/"warm"/"cold"/"idle": bytes}` shape
`collect()` must be reshaped into — no `summary.py` change needed, C1's own
docstring anticipated this correctly. Did NOT read (not needed for C3):
`DESIGN.md` §1/§3/§4.8/§6-§6a, `ATTACH-GUIDE.md`, `README.md`,
`lib/sampler.py`, `lib/store.py`, `lib/events.py`, `lib/limits.py`,
`lib/access.py`, `lib/caps.py` (only grepped for the shared signal-teardown
pattern damon.py's own docstring names), the pwmcp shape, ciu governance
lines, `dev-interactive.slice.in` — all needed before/during C4/C6/C7, not
C3; carried forward for the next successor exactly as BRIEF-2 carried them
to this session.

Orientation call count: ~10 tool calls (reads + two greps + one `wc -l`
survey) before the first edit (the temporary red-probe test). No read-list
item found wrong or stale.

### Red-first probe (not committed — see REPORT for the full account)

Added a temporary test constructing two bare `DamonSession`s
(`kdamond_idx=0`, `kdamond_idx=1`, no pool) to `tests/test_damon.py`, ran
`PYTHONPATH=. python3 -m pytest tests/test_damon.py -k RED -q`: **FAILED**
as predicted — `assert fake_damon.nr_kdamonds() == 2` after exiting
session 0 while session 1 stayed live got `0`, not `2` (session 0's own
stale `__enter__`-time snapshot shrunk the counter past session 1's live
kdamond). Removed the probe once `KdamondPool` existed and the permanent
pool-based suite replaced it (kept only in this record, not the committed
tree).

### Commits

#### 4. `0cdfe89b` — `feat(cgprofile): C3 -- KdamondPool, DAMON multiplexing (RG55 contract §3)`

**What:** `KdamondPool` (lowest-free-index acquire, never-shrink-while-
another-lives release, baseline-restore-on-full-drain) in `lib/damon.py`;
`DamonSession` gained `pool=`, `recommit_targets()`, `thresholds`, and
`last_class_bytes`; `collect()` now reuses one `Classifier` built from the
session's configured thresholds at `__enter__` instead of a fresh
default-threshold one per call. Full design reasoning, the red-first
probe's exact failure, and the C4-scoping note (RW-9) are in REPORT's "C3"
section — not duplicated here.

**Tests:** `tests/test_damon.py`, 43 → 64 tests. See REPORT for the full
list; the four handoff-required pool assertions are each their own test
(indices 0/1; no `nr_kdamonds` write while the higher index lives; index 0
reused by a third session; full shrink to baseline on drain).

**Gate:**
- Local iteration (no docker): `PYTHONPATH=. python3 -m pytest
  tests/test_damon.py` (64 passed) and `coverage run --branch
  --source=lib.damon` (100%/100%, 206 stmts/58 branches) while writing the
  suite; then local `tests/` in full once (979 passed) and local coverage
  in full once (100%/100% across every `pyproject.toml` `source` module)
  as a pre-gate sanity check.
- `docker ps --format '{{.Image}}'` for `tester-unified`: 0 running,
  checked before every gate invocation below (4 checks).
- Pre-commit (dirty tree, sanity only): `run-gate.py r0-r1` exit 0 (979
  passed, 100%/100%); `run-gate.py r3` exit 0 (7/7 rejected).
- Commit `0cdfe89b` landed (`git -C <worktree> commit -F <msgfile> --only
  -- lib/damon.py tests/test_damon.py`, both trailers per BRIEF-2's
  standing note — current attribution reminder's, not the handoff §7
  literal text).
- Post-commit, clean-tree, real record: `run-gate.py r0-r1` exit 0;
  `run-gate.py r3` exit 0, 7/7 rejected. Verdict read in a SEPARATE step
  from `.run-gate/history.json` (never a pipe tail — LESSONS L4): both
  lanes' `latest` show `"commit":
  "0cdfe89b1a19ab921f44addd7921d4916789281a"`, `"dirty": false,
  "exit_code": 0, "outcome": "pass", "history_eligible": true"`, matching
  `git rev-parse HEAD`.
- `r2` (assay mutation lane): **not run** — per the handoff, once before
  the final P1 return; this is not the return.

Host load: see REPORT's "Host load compliance (session 3)" — PSI checked
(not load alone), 0 `tester-unified` containers at every check, no
concurrent image build, both gate containers confirmed self-removed.

### Checkpoint

Stopping here (E-008) at a clean boundary: one green-gate commit landed,
LOG/REPORT written, BRIEF-3 next. Not at a tool-call or context limit, but
matching this package's own standing preference ("finishing a deliverable
over starting the next one near the limit") — C4 (`lib/serve.py`) is
explicitly called out by both the handoff and BRIEF-2 as large (a
threading session server, a registry, retention, SIGTERM handling, a
single write-helper boundary with its own dedicated test) and deserves a
fresh, full budget rather than a partial start appended to an
already-substantial C3 session. See `cgprofile-P1-DAEMON-BRIEF-3.md` for
the successor's starting point.

## Session 4 (fresh Sonnet, checkpoint clause armed)

### Orientation

Read before any edit, in order: `BRIEF-3.md` in full; this LOG's session 3
entry and REPORT's "C3" section (KdamondPool design reasoning, not
re-derived); the handoff (`cgprofile-P1-DAEMON-HANDOFF.md`) in full,
`RG55-INTERFACE-CONTRACT.md` in full, `fixtures/rg55/README.md` in full;
`DESIGN.md` §1/§2/§3/§6/§6a (never read by any prior session); `lib/
summary.py`, `lib/subtree.py`, `lib/damon.py` in full (the three modules
C4 builds on); `lib/sampler.py` in full; `lib/store.py`, `lib/events.py`,
`lib/access.py`, `lib/metrics.py`, `lib/targets.py`, `lib/util.py`,
`lib/caps.py` in full; `cgprofile.py`'s CLI structure (`build_parser`/
`main`, `cmd_targets` for the RW-3 pattern, `_err`/`_note`/`_venv_python`
helpers); `tests/conftest.py` in full (fixture conventions:
`write_cgroup`/`cgroup_files`, `cgroup_root`/`proc_root` fixtures);
`tests/test_summary.py` (golden-reproduction test conventions, confirmed
`tests/fixtures/contract/` already vendors every golden fixture file, not
just the frames); `tests/test_damon.py`'s `FakeSysfsInterface`/
`fake_damon` fixture (the monkeypatch-`KDAMONDS_DIR`-to-a-tmp-path
convention, reused for this session's own `_write_nr_kdamonds` guard
tests). Did NOT read this session (deferred to C5-C9, per each one's own
paragraph in the handoff): `ATTACH-GUIDE.md`, `README.md`, the pwmcp shape
files, ciu governance lines, `dev-interactive.slice.in`.

Orientation call count: ~35 tool calls (reads + a few greps) before the
first edit (`lib/store.py`'s `write_json` refactor). All read-list items
were present and current; none found wrong or stale.

### Commits

#### 5. `8ba01138` — `feat(cgprofile): C4 -- lib/serve.py session server (RG55 contract §1/§2/§5)`

**What:** `SessionServer` (`lib/serve.py`, new) — the Unix-socket
JSON-lines control plane, the per-session sampling thread, the registry,
restart recovery, retention, SIGTERM/SIGINT handling, and the
`WRITABLE_ROOTS` write guard. `lib/damon.py` gained the matching guard
(`HostWriteError`, `_write_nr_kdamonds`) for its own one direct sysfs
write. `lib/store.py` gained a generic `RunDir.write_json` (`write_manifest`
is now a thin wrapper over it). `lib/summary.py` gained `sample_count`/
`targets_seen` read-only properties. Full design reasoning, the
WRITABLE_ROOTS scoping, and every decision-ask/deferred item are in
REPORT's "C4" section — not duplicated here.

**Tests:** `tests/test_serve.py`, new, 69 tests. See REPORT for the full
breakdown; headline items: a full start→sample→stop lifecycle run
synchronously against the frozen contract fixtures reproducing
`summary-v1.json`/`summary-container-v1.json`, a `host-v1.json` exact
reproduction, a symlink-escape test for both write guards, and one real
Unix socket round trip.

**Gate:**
- Local iteration (no docker), repeated throughout: `PYTHONPATH=. python3
  -m pytest tests/test_serve.py` and `coverage run -m pytest tests/` +
  `coverage report`, converging on 100%/100% across every
  `pyproject.toml` `source` module; full local `tests/` once (1048
  passed) as a pre-gate sanity check.
- `docker ps --format '{{.Image}}'` for `tester-unified`: 0 running,
  checked before every gate invocation below.
- Pre-commit (dirty tree, sanity only): `run-gate.py r0-r1` exit 0 (1048
  passed, 100%/100%).
- Commit `8ba01138` landed (`git -C <worktree> commit -F <msgfile> --only
  -- lib/serve.py lib/damon.py lib/store.py lib/summary.py
  tests/test_serve.py`, both trailers per this session's own top-level
  attribution reminder).
- Post-commit, clean-tree: `run-gate.py r0-r1` **first attempt exit 1**
  (an isolated, pre-existing `tests/test_store.py` flake unrelated to
  this session's changes — see REPORT's "C4" Gate subsection and
  backlog **CP-4**); immediate re-run, no code change: exit 0, 1048
  passed, 100%/100%. `run-gate.py r3` exit 0, 7/7 rejected. Verdict read
  in a SEPARATE step from `.run-gate/history.json` (never a pipe tail —
  LESSONS L4): both lanes' `latest` show `"commit":
  "8ba01138b2150e5f7f27f4b26f9714c1352effe9"`, `"dirty": false,
  "exit_code": 0, "outcome": "pass", "history_eligible": true"`, matching
  `git rev-parse HEAD`.
- `r2` (assay mutation lane): **not run** — per the handoff, once before
  the final P1 return; this is not the return.

#### 6. `91d9d6a1` — `chore(cgprofile): file CP-4 -- pre-existing test_store.py flake found during C4 gate run`

`nyxloom backlog new` + regenerated `INDEX.md`, via the `backlog` skill.
See REPORT's "C4" Gate subsection for the full mechanism.

Host load: see REPORT's "Host load compliance (session 4)" — PSI checked
(not load alone), 0 `tester-unified` containers at every check, no
concurrent image build, every gate container confirmed self-removed.

### Checkpoint

Stopping here (E-008) at a clean boundary: two commits landed (the C4
feature commit and the CP-4 backlog filing), LOG/REPORT written,
BRIEF-4 next. This session ran long (C4 is the package's largest single
deliverable, exactly as BRIEF-2/BRIEF-3 both flagged) — comfortably past
the ~60-tool-call/~120k-context ARM threshold — but C4 itself only reached
a genuinely coherent stopping point once fully green (100%/100% local
coverage, the official gate green on a clean commit, the flaky-test
detour understood and filed rather than papered over). Cutting mid-C4
(e.g., after the write guard but before the golden-reproduction test, or
after the registry but before restart recovery) would have hidden real
integration risk in an untested state; the whole module is small enough
in scope (one file, one clear contract boundary) that "finish it, then
stop" was the safer read of "cut after a green sub-cluster" than an
arbitrary earlier point. C5 (CLI `serve`/`ctl` verbs) through C9
(backlog/docs) remain — see `cgprofile-P1-DAEMON-BRIEF-4.md` for the
successor's starting point.

## Session 5 (fresh Sonnet, checkpoint clause armed)

### Orientation

Read before any edit, per `BRIEF-4.md`'s own successor pointer: the full
multi-part dispatch (C5-C9, live acceptance §4, one r2 mutation lane, final
r0-r1/r3), this LOG's session 4 entry and REPORT's "C4" section,
`RG55-INTERFACE-CONTRACT.md` in full (the frozen wire contract C5 implements
verbatim), `ATTACH-GUIDE.md` and `README.md` (deferred by session 4,
required for C5/C7's CLI-shape and docs work), the pwmcp shape's own
`docker-bake.hcl`/`build-push.py`/`Dockerfile` (C6's template), an existing
ciu standalone-root stack in this estate (C7's template), `cmru.toml`
conventions from a sibling project + `cmru.orchestration.toml`'s existing
registrations (C8). This session's own context was compacted mid-way
through live acceptance (after C5-C9 landed and two live bugs were already
found/fixed/committed); the successor half of this same session picked up
mid-diagnosis of a THIRD live bug (the daemon-crashing `OSError`, see below)
with the full technical detail preserved in the compaction summary rather
than by re-reading source from scratch.

### Commits

#### 7. `b734f6b9` — `feat(cgprofile): C5 -- CLI serve/ctl (RG55 contract §1/§2), RW-14, RW-15`

`cgprofile.py` gained `serve` (runs as PID 1, refuses `--cap` structurally,
refuses to start without `access.have_host_cgroup_view()`) and
`ctl <version|start|status|host|stop|report|gc>` (a 25s-timeout Unix-socket
client, exit 0/2/3 per contract §1.3). RW-14: `handle_report` now shells out
to the report tier's own venv interpreter to render the REAL interactive
HTML report (never importing pandas into the collector-tier daemon itself),
which required `_on_session_sample`'s samples record and `_manifest_for` to
actually satisfy `lib.store.RunDir`/`lib.analyze.build`'s expected shapes
(the previous flat samples shape was silently unreadable by `analyze.py`,
never exercised until now). RW-15: the no-token discovery path now shares
`DISCOVERY_INTERVAL_SECONDS` cadence with the token path and only recommits
DAMON targets when the pid set changes. Golden round-trip tests reproduce
`version-v1`/`start-v1`/`status-v1`/`stop-v1`/`error-v1`/`host-v1`
byte-for-byte through a real Unix socket and a real background sampling
thread paused mid-session by a two-event lockstep. Full design detail is in
the commit message itself (`git show b734f6b9`), not restated here.

**Gate:** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%); `r3` exit 0
(7/7 rejected). `docker ps` checked for `tester-unified` before each run (0
running).

#### 8. `a6acb028` — `feat(cgprofile): C6 -- Dockerfile, build-push.py, docker-bake.hcl (pwmcp shape)`

Multi-stage Dockerfile (base `python:3.14-slim`, matching DESIGN.md §6's
dev-environment fact) — builder stage installs `build-essential` and builds
`venv/` from `requirements.txt` (`ruptures==1.1.9` has no cp314 wheel yet
and needs gcc to compile); final stage copies only the finished venv, never
the compiler, since this image runs `--privileged --pid=host --cgroupns=host`.
`docker-bake.hcl`/`build-push.py` mirror pwmcp's variable/target/group
shape, simplified (one externally-resolved coordinate, the scm-strategy
release version, vs. pwmcp's several Playwright pins). Verified for real:
`python3 build-push.py --build` produced both tags (606 MB); a bare
`docker run --rm cgprofile:local` refused cleanly per
`access.have_host_cgroup_view()`'s exit-2 message; the image's own venv
imports every report-tier dependency cleanly.

**Gate:** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%) — no Python
source changed, run as a regression check.

#### 9. `dcfe19da` — `feat(cgprofile): C7 -- ciu stack for cgprofile-host-daemon (standalone root)`

`ciu.global.defaults.toml.j2`/`ciu.defaults.toml.j2`/`ciu.compose.yml.j2`/
`ciu.toml.j2` (pwmcp's standalone-root shape). `deploy.environment_tag =
"host"` is a DELIBERATE fixed singleton (not `"$INSTANCE_ID"` like every
other estate stack) — exactly one daemon may run per host, so a second
worktree's `ciu up` must collide on the fixed container name
`cgprofile-host-daemon` and refuse a sibling. `cgroup_parent` is AUTHORED
from `$CGROUP_PARENT_DEV_INTERACTIVE` via ciu's own `{{ env.X }}` mechanism
(refuses the render when unset, no hardcoded slice fallback);
`mem_limit`/`memswap_limit` explicit, never governance-injected. Verified
for real: `ciu check --define-root .` passes clean; `ciu up --dir .
--dry-run` ran the full 16-step pipeline clean, and diffing the pre-overlay
compose against the rendered overlay confirmed governance's own injection
(`mem_reservation: 256m` + an image-revision env var) never touched the
authored `cgroup_parent`/`mem_limit`/`memswap_limit` (S15.3 precedence
holds). `privileged`/`pid`/`cgroupns`(later renamed `cgroup`,
see commit 12)/`network_mode` were accepted at every validation stage, so
`tools/daemon-run.sh` was correctly NOT shipped (the refusal condition that
would require it never fired).

**Gate:** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%) — docs/
templates only, run as a regression check.

#### 10. `ae61b75a` — `feat(cgprofile): C8 -- cmru registration (RG-55 wave)`

`scripts/cgroup-profiler/cmru.toml` (scm versioning, prefix `cgprofile-v`,
`artifacts = ["oci-image"]`, `run-tests` = `r0-r1` then `r3` with an
explicit comment on why `r2` is NOT a release-gate step). Root
`cmru.orchestration.toml` gained `[orchestration.project.cgroup-profiler]`
plus `project_order`/`default_projects` entries; the machine-owned
dependency-graph comment regenerated via `cmru dependencies --write`
(PREFLIGHT: PASS), never hand-edited. Verified: `cmru status --project
cgroup-profiler` runs clean (read-only); `cmru release` NOT run (forbidden).

**Gate:** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%) — config-only.

#### 11. `a1c49726` — `docs(cgprofile): C9 -- backlog CP-5/6/7, DESIGN.md/README.md daemon docs`

Filed CP-5 (daemon sessions never populate `events.jsonl` with real
detected events — explicitly deferred by the C5 dispatch ruling), CP-6
(`ctl report`'s real render never charts a session's own DAMON series —
`lib.analyze` has no `damon.jsonl` reader), CP-7 (the daemon manifest's
`limits` table is always `{}`, so every `analyze.py` proposal check is a
permanent no-op for a daemon-collected report) via the `backlog` skill —
`nyxloom backlog new` + regenerated `INDEX.md`. DESIGN.md gained a
third-mode paragraph (§1), the full RG-55 file set in the layout tree (§2),
and §4.13/4.14/4.15 (`subtree.py`/`summary.py`/`serve.py`). README.md
gained a "## Verbs" table.

**Gate:** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%) — docs/backlog
only.

#### 12. `697f4e4b` — `fix(cgprofile): two real bugs found during C5-C9 live acceptance`

Found running the ACTUAL live-acceptance steps, not by re-reading code:
(1) `ciu.compose.yml.j2`'s `cgroupns: "host"` is not a valid Compose
service key — docker compose v2.40.3 refused it outright; the Compose
Specification's real key is `cgroup` (singular), verified against a minimal
`docker compose config` probe. (2) `cgprofile.py`'s `ctl` verb subparsers'
shared `parents=[_ctl_common]` mixin had plain (non-`SUPPRESS`) defaults, so
a CHILD subparser silently clobbered a LEADING `--socket`/`--json` the
PARENT `ctl` parser had already set (`ctl --socket X version` parsed back to
the hardcoded default) — invisible to every C5 golden test because they all
happened to use the leading position exclusively; the contract's own
trailing-position convention (`ctl version --json`) was what first exposed
it, live. Fixed with `default=argparse.SUPPRESS` on the child mixin's
copies (trailing > leading > default), pinned by
`test_ctl_json_and_socket_work_in_every_position`.

**Gate:** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%).

#### 13. `4f393cbc` — `fix(cgprofile): DAMON was unconditionally unavailable inside the built image`

`lib/damon.py` resolves `Classifier`/`KDAMONDS_DIR`/`SysfsInterface` from
the SIBLING `scripts/damon-analysis/lib/damon_analysis.py` via a relative
path, but the Dockerfile's build context is `scripts/cgroup-profiler/`
alone — that sibling was never copied in, so `damon_mod.available()`
returned `False` inside every built container regardless of the host
kernel. Found live: `ctl version --json` reported DAMON unavailable despite
`/sys/kernel/mm/damon/admin/kdamonds` being independently confirmed
writable inside the same container. Fixed with a second, named buildx
context (`docker-bake.hcl`'s `contexts = { damon_analysis = "../damon-analysis" }`)
the Dockerfile pulls one file from, plus `--allow=fs.read=../damon-analysis`
on `build-push.py`'s bake invocations (buildx refuses an out-of-context read
as an entitlement by default). Verified: rebuilt, `SysfsInterface` resolved
to the real class (was `None` before), `ctl version --json` reported
`"damon": "available"`.

**Gate:** `run-gate.py r0-r1` exit 0 (1087 passed, 100%/100%) — Dockerfile/
bake/build-push are outside pytest's coverage source, run as a regression
check on the Python suite regardless.

#### 14. `907cb810` — `fix(cgprofile): a foreign OSError from DAMON commit crashed the whole daemon`

The most serious of the three live-found bugs: `ctl start --damon on`
against a real probe container CRASHED THE ENTIRE `cgprofile-host-daemon`
PROCESS (not just that one request) — `docker logs` showed an uncaught
`OSError: [Errno 22] Invalid argument` from `SysfsInterface.kdamond_commit`,
a genuine kernel EINVAL on this host committing a DAMON context despite the
sysfs tree being present and writable (sysfs-tree presence is not commit
capability). Root cause: `DamonSession.__enter__()`'s bare
`except Exception: ... raise` left the foreign `OSError` untranslated, so
`_create_session_locked`'s `except damon_mod.DamonSessionError` never
caught it and it propagated straight through the socket dispatch loop,
killing `_accept_loop`/`serve_forever` — every OTHER live session died with
it (each sampling thread is `daemon=True`), a severe violation of contract
§2.2's "an unavailable DAMON never fails start" promise. Two layers fixed
it: `lib/damon.py` now wraps anything that is not already a
`DamonSessionError` as one (preserving an already-`DamonSessionError` via a
bare re-raise, no double-wrap); `lib/serve.py`'s `_handle_connection` gained
a defense-in-depth broad `except Exception` around `self._dispatch(req)`
that logs to stderr and closes the connection with no reply, mirroring the
per-session safety net `_session_loop` already had, so a FUTURE
unanticipated handler bug can never take the whole process down again
either. New regression tests in both `test_damon.py` (wrap-vs-rewrap
identity, message content) and `test_serve.py` (`_handle_connection`/
`_accept_loop` survive an injected handler exception; a follow-up
connection on the same accept loop is still served normally afterward).

**Gate:** local `pytest`/`coverage` 1091 passed, 100%/100% line+branch
across every `pyproject.toml` source module; `run-gate.py r0-r1` exit 0
(1091 passed, 100%/100%). `docker ps`/PSI checked before every run.

**Live re-verification after the fix** (image rebuilt via `python3
build-push.py --build`, daemon recreated via `ciu up --dir . -y`): the
SAME `ctl start --damon on` call that previously crashed the daemon now
returns cleanly with `"damon": "unavailable:OSError: [Errno 22] Invalid
argument"` — the daemon survives and the contract's own promise holds. Full
live-acceptance results (both probes, the overhead table, `ctl report`) are
recorded in REPORT's "Live acceptance" section, not duplicated here.

#### 15. `71c6f607` — `chore(cgprofile): RW-19 -- raise r2 mutation lane budget 45m -> 4h`

**RW-19 (controller ruling, quoted):** "Controller ruling RW-19 (r2
budget) — apply when your r2 run reports BUDGET_EXCEEDED, no action needed
if it finishes inside its budget. Your r2 lane has 217 candidates at
roughly 84 s each with jobs = 2, so about 2.5 h of wall clock; the 45 m
assay budget will not hold and a 90 m one would still not. Ruling: raise
`[lanes.r2] budget` in `assay.toml` to '4h' and the `run-gate.toml` r2
lane budget to '4h' (one commit, comment naming RW-19 and the measured
per-candidate cost), then re-run `./run-gate.py r2` ONCE — the lane
already passes `--resume --progress`, so it continues from the candidates
already judged."

Applied verbatim: `assay.toml`'s `[lanes.r2] budget` and `run-gate.toml`'s
`[lanes.r2] budget` both raised 45m → 4h, each with a comment naming
RW-19 and the measured per-candidate cost (217 candidates, ~84s each,
jobs=2, ~2.5h wall clock). The FIRST r2 attempt (background, launched
before this ruling landed) had already been running under the OLD 45m
`budget_s` when a host-wide memory-pressure event (unrelated to r2 itself)
killed this session's own supervising bash wrapper — the underlying
`docker run -d` container kept running and judging candidates completely
unaffected (confirmed: `docker stats` showed it healthy, within its own
1 GiB limit, the whole time), so nothing was lost; a `Monitor` polling
loop was used to track it to completion instead of a killable supervising
process.

**Gate:** no Python source changed; not separately gate-verified (folded
into commit 16's r0-r1 re-run below).

#### 16. `5058f04d` — `test(cgprofile): r2 mutation survivor triage -- kill 28, justify 3`

The raised-budget r2 run completed on its own (10234s / 2.84h, under the
4h cap): 217 candidates, 178 killed, 39 survived, verdict FAIL
(`MUTANTS_SURVIVED`). Full triage of every survivor OUTSIDE
`lib/summary.py` (28 of 39) — the 11 `lib/summary.py` survivors were
deferred to the RW-21 rewrite (commit 17), which touches those exact
lines and gets its own fresh mutation judgment (candidates are
content-keyed) once that lands. 28 killed via new/strengthened tests (no
source bug found in any of them — every one was a real gap in what the
existing suite actually observed, never a production defect); 3 justified
as equivalent/not-practically-distinguishable, documented inline at each
site and in REPORT's "r2 mutation verdict" section. Full breakdown in the
commit message itself (`git show 5058f04d`) and REPORT.

**Gate:** local `pytest`/`coverage` 1104 passed, 100%/100%; `run-gate.py
r0-r1` exit 0 (1104 passed, 100%/100%). `docker ps`/PSI checked before the
run (0 `tester-unified`).

#### 17. `c97bd176` (merge) + `7ec4f9e8` — `contract(cgprofile): RW-21 -- scope "container" reads cumulative counters absolutely`

**RW-21 (controller ruling, quoted in full):** "Controller ruling RW-21
(contract §7 amended) — adopt it right after your r2 verdict and survivor
triage, before returning. RW-21: for scope `container` (an ephemeral
lane, the cgroup is born with the lane) every cumulative counter is read
as an ABSOLUTE value at the last successful read, not as a delta from
s_0: `cpu.seconds` = last `usage_usec`/1e6 (likewise `throttled_seconds`,
`nr_throttled`); `pressure.*_stall_seconds` = last totals/1e6; `faults.*`
and `events.oom_kill`/`memory_high_breach` = last read values;
`limit_drift` unchanged; `cores_avg` = cpu.seconds/duration_seconds;
`memory.peak_bytes` = last `memory.peak`; `baseline_bytes` kept
(informational); `peak_over_baseline_bytes` = null. Scope
`container-shared` keeps the delta rules; host fields keep deltas in both
scopes. Reason: the run-gate reviewer measured a 3.3× CPU understatement
on an ephemeral lane with the delta rule. The amendment and regenerated
goldens are on `main` at `3b75e1df`."

`git merge --no-edit main` (commit `c97bd176`) — no conflicts, exactly as
the ruling anticipated (this branch never touched the contract or the
shared fixtures). `lib/summary.py` gained `_rw21_reference(scope,
samples, *keys)` — scope `container` returns the last successful read
(RW-7's own "skip back over unreadable ticks" rule, already used by
`memory.peak_bytes`/`pids.peak`); scope `container-shared` unaffected,
keeps the delta-from-`s_0` rule. Every affected block routed through it:
`_cpu_block` (seconds/throttled_seconds/nr_throttled — `cores_max`
unaffected, a per-interval rate, never a delta), `_pressure_block` (all
five `*_stall_seconds`), `_faults_block` (all three), `_events_block`
(`oom_kill`/`memory_high_breach` — `limit_drift` unaffected).
`_memory_block`: `peak_over_baseline_bytes` unconditionally `None` in
scope `container` (computed BEFORE checking peak/baseline presence, never
merely coinciding with the old floor-at-zero's occasional 0).
`tests/fixtures/contract/` re-vendored from
`run-gate-project/nyxloom-trove/fixtures/rg55/` (byte-identical, `diff
-rq` confirmed): `README.md`, regenerated `summary-container-v1.json`,
new `summary-basic-container-v1.json`. One pre-existing test
(`test_peak_over_baseline_floors_at_zero`) tested behavior RW-21 replaces
outright — rewritten as two: the container-scope null-override (same
fixture, new assertion) and the container-shared scope's own unaffected
floor-at-zero (a corrected two-sample fixture — the original's comment
had already noted its one-sample fixture couldn't organically reach a
negative floor in that scope either).

**Gate:** local `pytest`/`coverage` 1105 passed, 100%/100% line+branch
(`lib/summary.py`: 199→201 statements, 54→58 branches, all covered).
Pre-commit (dirty-tree sanity): `run-gate.py r0-r1` exit 0. Post-commit
(clean tree): `run-gate.py r0-r1` exit 0 (1105 passed, 100%/100%);
`run-gate.py r3` exit 0 (7/7 canaries rejected). Verdict read in a
SEPARATE step from `.run-gate/history.json` (never a pipe tail — LESSONS
L4): both lanes' `latest` show `"commit":
"7ec4f9e819f33b5106fbcc0ae17c313495f02e65"`, `"dirty": false, "exit_code":
0, "outcome": "pass", "history_eligible": true` — matching `git rev-parse
HEAD`. `docker ps`/PSI checked before every run (0 `tester-unified`).

#### 18. Final r2 re-run (per RW-21 step 4)

`./run-gate.py r2` re-run ONCE more on commit `7ec4f9e819f33b5106fbcc0ae17c313495f02e65`
(the RW-21 commit) — resumes from `.assay/mutation-state/` (217 files
present from the first run). One stale "inflight" bookkeeping record
(pointing at the first run's now-exited, already-processed container)
required a `docker rm -f` before this re-run would start — a harmless
leftover from that container never being `--rm`-cleaned, not a defect in
this session's own work.

**Observed, worth recording:** the resume mechanism's own `"event":
"resume"` log line reported `rejected_total: 151, resumed_total: 0` and
re-discovered `candidate_total: 208` (down from 217 — `lib/summary.py`'s
edit shape changed its own raw mutable-expression count, e.g. three
separate inline `is None or is None` checks consolidated into calls to
one shared `_rw21_reference` helper), with ALL 208 landing in
`pending_total`, including candidates with the SAME `candidate_id` (a
content hash) as ones already judged in the first run (confirmed:
`lib/damon.py`'s first two candidates re-ran with identical ids and
reproduced the same "killed" outcome) — i.e. `--resume` here means
"resume a CRASHED/interrupted run of the same invocation," not "reuse a
prior verdict across a commit change." On reflection this is the
CORRECT conservative behavior, not a gap: a mutation verdict is scoped to
a resolved commit (the same "verify against the resolved commit" caution
this estate applies elsewhere, e.g. `ciu provenance`) — a different
commit's test suite could in principle kill or spare an unrelated file's
mutant differently even when that file's own bytes did not change (this
commit's own `tests/test_summary.py` changes are exactly the kind of
cross-cutting change that could do that), so re-validating everything
against the new commit is the safe default, not a defect. This session's
own earlier LOG draft of this note (before this correction) mischaracterized
it as a possible assay tooling gap; struck here rather than left standing.
No action taken beyond letting the full re-run complete under the
still-generous 4h budget. See REPORT for the final verdict.

#### 19. Interruptions while item 18's container ran: RW-26 (orphan-container recovery) and RW-28 (per-candidate hang)

**RW-26** (controller ruling, summarized — full text in this session's
own transcript): this session's own supervising process for `./run-gate.py
r2` was killed by the Claude Code low-memory guard ("system is running
low on memory") not once but several times while trying to track the
container to completion (a plain `run_in_background` bash, then repeated
tracked `until … docker inspect` watchers, each re-armed and each killed
again). The underlying `docker run -d` container
(`run-gate-vbpub-r2-2315801-1789214565`) was NEVER affected by any of
these kills — a separate process, confirmed healthy via `docker
stats`/`docker ps` every time. Per RW-26: never `docker rm -f` a
still-progressing mutation container; let it finish; once done, capture
evidence, resume under REAL run-gate ownership (so a real `.run-gate/
history.json` entry gets written — the orphaned container's own
completion only writes `.assay/verdict-r2.json`, not a run-gate history
record) launched UNTRACKED (`nohup … & disown`) with a cheap tracked
watcher observing it, re-armed if killed. Insurance file
`cgprofile-P1-DAEMON-BRIEF-6.md` (commit `f9f06456`) written per this
ruling in case this session itself got killed next. The controller
eventually took over watching the container from its own heartbeat and
asked this session to stop re-arming the watcher (each kill was
re-invoking the session for nothing) — complied, no watcher armed since.

**RW-28** (controller ruling, quoted in full): candidate 47 of the
in-progress run (`lib/serve.py:479`, the session-loop's
`threading.Thread(..., daemon=True)` flipped to `daemon=False`) let
pytest finish REPORTING its results but then HUNG at interpreter exit for
37 minutes (futex wait, 0% CPU) waiting on the now-non-daemon thread — the
`assay.toml` r2 lane set no `budget_per_candidate`, so assay waited
indefinitely. The controller SIGKILLed the hung pytest inside the
container at 13:22Z; the run resumed on its own (two jobs running again).
Whatever assay recorded for that candidate (`30262744b91e83a5…`) is not
an honest verdict — the suite never actually caught the mutant, it hung.

Root cause, worked out this session: `lib/serve.py`'s `test_start_then_
stop_reports_finished` (this session's own commit `5058f04d`, part of the
FIRST triage pass — this exact mutant, at line 479, was already IN that
triage's 28-killed list) DOES assert `sess.thread.daemon is True`
directly, and correctly fails FAST (confirmed: 1.40s, `pytest tests/
test_serve.py::TestStartRegistry::test_start_then_stop_reports_finished`)
under this exact mutation — a real, honest, non-hanging kill signal for
THAT one test. The hang happens anyway because pytest's default behavior
runs the REST of the suite after one test fails, and several OTHER tests
also start a real session via `_dispatch(_start_req())` and rely on
`daemon=True` to let the interpreter exit cleanly without ever joining
that thread themselves — under the global mutation, EVERY one of those
becomes a non-daemon thread, and Python's own interpreter shutdown blocks
on ALL of them regardless of whether any single test's own assertion
already "won." The real fix for the HANG risk itself (as opposed to
catching the mutant, which was already happening) is exactly what RW-28
prescribes: a per-candidate ceiling so no single candidate can stall the
whole lane indefinitely, independent of whether the SUITE'S own exit
behavior is well-behaved under every possible mutation.

Applied: `assay.toml`'s `[lanes.r2]` gained `budget_per_candidate =
"600s"` (~6x the ~100s measured baseline per-candidate cost), comment
naming RW-28 and the incident. No new test needed for the mutant itself
(already honestly killed by `5058f04d`'s existing assertion) — verified
by re-running that one test directly (1.40s, passes). Per RW-26's own
"delete the stale state file so resume re-judges honestly" instruction
(assay B088: `--resume` keys candidate identity on the mutant's source
bytes, not the test suite's own pass/fail history — a stale verdict from
a killed/hung run would otherwise replay unchanged): `.assay/mutation-
state/30262744b91e83a5*.json` to be removed before the next resume (step
4 of the RW-26 plan, not yet reached as of this entry — the orphaned
container was still running when this was written).

**Gate:** not separately run (working-tree edits only, `assay.toml` is
outside pytest's own coverage scope; the one test re-run directly,
1.40s, passes). Full local suite / gate deferred to RW-26 step 7 (final
gates on the final tip), per the controller's own explicit "do NOT start
any additional pytest or container while the r2 container runs" — memory
was tight host-wide (as low as 451 MiB free / 3.6 GiB available at one
check) the whole time item 18's/19's containers were active.

#### 20. RW-26 steps 4-5 — orphan-container evidence, RW-28 state cleanup, `5ce232d1` config fix, resume launch

Controller confirmed at 15:49Z that the orphaned container
`run-gate-vbpub-r2-2315801-1789214565` (item 19) exited. Captured
evidence BEFORE removing anything, per RW-26 step 4:

```
$ docker inspect -f '{{.State.ExitCode}}' run-gate-vbpub-r2-2315801-1789214565
1
$ docker logs --tail 40 run-gate-vbpub-r2-2315801-1789214565
assay: no judge_provenance recorded -- no installed distribution metadata for 'assay' was found, so this process is running from a source tree; a source tree is not a build artifact and has no digest to record; pass --require-judge-provenance to refuse instead of proceeding
r2: FAIL/MUTANTS_SURVIVED (exit 1)
  commit: 7ec4f9e819f33b5106fbcc0ae17c313495f02e65
  argv: /opt/tester-venv/bin/python3 -m pytest tests -q
```

Read `.assay/verdict-r2.json` in a separate real parse (LESSONS L4, never
a pipe tail): `outcome: FAIL`, `reason_code: MUTANTS_SURVIVED`, `commit:
7ec4f9e819f33b5106fbcc0ae17c313495f02e65` (the RW-21 implementation
commit, `7ec4f9e8`, as expected — the container's tree snapshot was
fixed at launch, before both `802f49ad` and `16f3a29f` landed).
`judgment.r2`: `jobs: 2`, `mode: "changed_lines"`, `max_mutants: 1500`,
the same 4 operators as declared. `judgment.resolved.base:
eb3af770eb16362cff4c39388193c0180c2d370c` (merge-base). Mutation claim:
`candidate_count: 208`, `killed: 195`, `survived: 13`, `equivalent: 0`,
`crashed: 0`, `budget_exceeded: 0` — a complete, honest run (the 37-min
hang was unblocked by the controller's SIGKILL and the run continued to
a real finish, not a partial/aborted one). The 13 survivors, all judged
against the OLD (pre-`802f49ad`, pre-`16f3a29f`) test suite:

```
lib/damon.py:329    python:bool-const-flip  False->True
lib/serve.py:209    python:compare-swap     IsNot->Is
lib/serve.py:389    python:compare-swap     IsNot->Is   <- already fixed by 802f49ad (duration assertion), stale here only because this container predates that commit
lib/serve.py:626    python:falsy-swap       None->[]
lib/serve.py:1136   python:bool-const-flip  True->False
lib/summary.py:136  python:boolop-swap      Or->And
lib/summary.py:173  python:falsy-swap       None->[]
lib/summary.py:177  python:falsy-swap       None->[]
lib/summary.py:197  python:boolop-swap      And->Or
lib/summary.py:214  python:boolop-swap      Or->And
lib/summary.py:264  python:bool-const-flip  False->True
lib/summary.py:345  python:boolop-swap      And->Or
lib/summary.py:392  python:boolop-swap      Or->And
```

Note candidate 47 (`lib/serve.py:479`, the daemon-thread mutant,
`30262744b91e83a5…`) is NOT in this survivor list — it was recorded as
killed even through the hang/SIGKILL sequence, consistent with the
existing `5058f04d` assertion catching it honestly. Per RW-28 it is
still untrusted (the hang means the recorded verdict for that one
candidate is not provably honest) and its state file was deleted anyway
(below).

`docker rm run-gate-vbpub-r2-2315801-1789214565` (already exited, no
`-f` needed). Deleted the RW-28 stale state file:
`.assay/mutation-state/30262744b91e83a5beb36e8ab574e7dca8c836db1fddbbe262a4ce309c0afcd1.json`.
No other candidate's recorded outcome was distrusted — the only
exceptional event this run hit was the single SIGKILL at candidate 47.

Checked `.run-gate/inflight/r2.json` before relaunching: it still named
the dead owner (`owner_pid: 2315801`, the removed container). Did not
hand-clear it — R-39 says read what run-gate prints and let it self
recover.

**Launch attempt 1 (RW-26 step 5, untracked) — failed fast, real bug
found in this session's own `16f3a29f`:** `nohup nice -n 19 ionice -c 3
./run-gate.py r2 > .../p1-r2-resume.log 2>&1 & disown`. run-gate printed
its "inflight record names a container that no longer exists — clearing
and running fresh" diagnostic exactly as R-39 predicts (self-recovered,
no hand-clear needed), started a NEW container
(`run-gate-vbpub-r2-665986-1789228624`), which then failed in ~2s with:

```
assay: ERROR/BAD_LANE_CONFIG: .../assay.toml: lane 'r2': unknown key(s): budget_per_candidate; expected only: scope, rigor, enforcement, argv, env, env_passthrough, budget, allow_argv_append, judge, where, isolation, env_required, environment_command, infrastructure, cwd, result_report
run-gate: lane 'r2' failed with exit 2
```

Root cause: `16f3a29f` placed `budget_per_candidate` directly under
`[lanes.r2]`; assay's schema (`assay/src/assay/config.py`,
`_MUTATION_OPTIONAL_FIELDS`) only accepts it under
`[lanes.r2.judge.mutation]`, alongside `jobs`/`max_mutants`/`operators`
— confirmed by reading `config.py` and by `runner.py`'s own
`lane.judge.mutation.budget_per_candidate` access path. This is the
FIRST real container invocation to ever exercise this key (the orphaned
container's snapshot predated it; the mid-run SIGKILL incident never
re-read the TOML). Fixed by moving the key and its comment into
`[lanes.r2.judge.mutation]` (commit `5ce232d1`), verified against
assay's own `load_lane_file()` directly before relaunching:

```
$ PYTHONPATH=.../assay/src python3 -c "... config.load_lane_file(Path('assay.toml')) ..."
budget: 4h
budget_per_candidate: 600s
jobs: 2
```

Exit-2 failure cost ~2s of container time (config validation runs before
any candidate judging), no wasted mutation work. Confirmed `.run-gate/
inflight/r2.json` was gone (run-gate cleared its own record after the
exit-2 failure) and no leftover `run-gate-vbpub-r2-*` container remained
before relaunching.

**Launch attempt 2 (post-fix) — accepted, running:** same untracked
pattern, new container `run-gate-vbpub-r2-680904-1789228700`, `docker
update --cpus=3` applied immediately after start (host constraint: P2's
bare-host `assay-r2` still running at this time, pid 2415767, ~3.5h
elapsed — one gate container total). Host check right before launch:
`free -h` 834Mi free / 4.4Gi available, PSI memory `some avg10=0.00
full avg10=0.00`, PSI cpu `some avg10=14.44` — inside tolerance. Watching
per RW-26/the "stop re-arming" instruction's spirit: a single cheap
tracked `until ! kill -0 <pid>` watcher, re-armed only if genuinely
killed, not polled.

**Gate:** deferred (mutation lane in progress). RW-26 step 6 (survivor
triage against the fresh verdict) and step 7 (final r0-r1/r3 on the
final tip) follow once this run completes.

#### 21. RW-47 (controller container-sweep collision) and RW-48 (root-cause fix for the per-candidate hang) — survivor triage, second pass

Container `run-gate-vbpub-r2-680904-1789228700` (item 20) itself hit
`BUDGET_EXCEEDED/LANE_TIMEOUT` at 20:00Z after a full ~3h35m run (started
19:34, `commit: 5ce232d1`). Root cause, worked out live: assay 6.1.1
reports the WHOLE lane `BUDGET_EXCEEDED/LANE_TIMEOUT` whenever ANY single
candidate lands in the `budget_exceeded` bucket — this was NOT a
lane-level timeout, it was candidate 47 (`lib/serve.py:479`,
`30262744b91e83a5…`, the RW-28 mutant) alone, exceeding its OWN 600s
`budget_per_candidate` ceiling and being classified `budget_exceeded`
rather than `killed`/`survived`. Per the coordinator: "no resume can fix
it" — the mutant IS honestly killed by the existing `daemon is True`
assertion, but the mutated non-daemon sampler thread still blocks
interpreter shutdown regardless, so pytest never exits inside its own
600s window.

`git switch --detach 5ce232d1` (clean worktree), deleted the stale
`30262744b91e83a5….json` state file again, relaunched untracked
(`run-gate-vbpub-r2-3348439-1789241673`) — the `resume` event confirmed
`resumed_total: 208, rejected_total: 0` (full tree-identity match), but
the container was then killed by the CONTROLLER's own stray-container
sweep at 19:47Z (intended for an unrelated P7 duplicate, matched by
IMAGE `tester-unified:local` instead of by name — **RW-47**, controller
error, not this session's). Relaunched again (untracked,
`run-gate-vbpub-r2-3431654-1789242099`) targeting JUST the one pending
candidate — this time it ran a REAL, isolated 600s window and recorded a
genuine `budget_exceeded` for candidate 47 (`elapsed_seconds: 602.181`,
confirmed via `.assay/r2-progress.jsonl`'s own `candidate` event, not
inferred) — a clean, reproducible, NON-flaky result, conclusively
confirming this is a structural harness-completion gap, not noise.

**RW-48** (controller ruling, quoted in full): fix the ROOT — every test
that starts the daemon's sampler thread(s) must stop them at teardown,
not rely on each test's own manual cleanup lines (which only run on the
happy path, after that test's OWN assertions — useless for exactly the
test whose assertion is the one that fails under this mutation). Applied:

- `tests/test_serve.py` gained an autouse `_stop_leaked_session_threads`
  fixture: tracks every `SessionServer` a test constructs (via a
  monkeypatched `__init__`, so it needs no per-test opt-in) and, in its
  finalizer (runs on a FAILING test exactly as a passing one), calls the
  server's own `_stop_all_sessions(aborted_reason="test-teardown")` —
  already-existing machinery (`_finalize_session_locked` sets
  `stop_event` and joins the thread with a 10s timeout) that this file's
  dozens of tests had been reimplementing by hand, incompletely, for
  years of this package's own short life.
- `tests/conftest.py` gained a session-scoped, autouse
  `_no_leaked_non_daemon_threads_at_session_end` safety net: records the
  thread-ident baseline at session start, asserts at session end that no
  NEW non-daemon thread is still alive, NAMING any that are — a backstop
  in case a thread escapes the per-test fixture (a different test file,
  a future one), not a fix in itself.

**Proof, run by hand** (RW-48 step 3): `lib/serve.py:479`'s `daemon=True`
flipped to `daemon=False` via a real `Edit` (backed by `cp lib/serve.py
lib/serve.py.bak` first), then:

```
$ nice -n 19 ionice -c 3 timeout 300 python3 -m pytest tests -q -x
.......................F
...
E       AssertionError: assert False is True
E        +  where False = <Thread(cgprofile-session-...)>.daemon
1 failed, 23 passed, 1 warning in 12.49s

$ nice -n 19 ionice -c 3 timeout 300 python3 -m pytest tests -q   # full suite, no -x, the exact shape assay's r2 lane runs
1 failed, 1107 passed, 1 warning in 122.09s (0:02:02)
```

No hang, clean interpreter exit, well inside the 600s per-candidate
budget — confirms the fixture closes the gap. File restored
(`diff lib/serve.py lib/serve.py.bak` → identical) before committing;
`git status --porcelain -- lib/serve.py` empty.

**Survivor triage, second pass** (the fresh 12-survivor verdict from
item 20's container, all against the OLD pre-`802f49ad` suite — confirmed
`serve.py:389` is genuinely absent from THIS verdict, i.e. already fixed):
8 killed via new tests (`lib/damon.py:329`; `lib/serve.py:209` ×2 tests;
`lib/serve.py:1136`; `lib/summary.py:136,197,264,345,392`), 2 equivalent
mutants carried over unchanged from the first triage pass
(`lib/serve.py:626,704`), 2 NEW equivalent-mutant justifications
(`lib/summary.py:173,177`, `_parse_iso`'s two failure branches — proven
via its single call site, consumed only by truthiness). Full reasoning
for every one of the 12 plus the `budget_exceeded` candidate is in
REPORT's "Survivor triage, second pass" section — not duplicated here.
One correction recorded there too: the FIRST triage pass's justification
for `lib/serve.py:1136` ("not observable through capsys") was wrong; a
`print` kwargs spy observes it directly, and it is now killed with a
real test rather than left as a false equivalent-mutant claim.

Full suite after this triage: `1114 passed` (was `1108` before this
triage's 9 new tests — some of that session's earlier count already
included the RW-48 fixtures themselves).

**Gate:** full suite green (`1114 passed`, confirmed above, twice: once
under the real tree and once under the hand-applied `daemon=False`
mutation to prove the fix). `r0-r1`/`r3` deferred to RW-48 step 7, after
the next commit and the resulting fresh r2 run (the tree changed — a full
208-candidate re-judgment, not a resume).

### 22. Third r2 verdict and final survivor triage (fresh Codex close-out)

Read `.assay/verdict-r2.json` separately. It durably judges tree
`637b8c0995c5e1ec1ddb802710571fcc305c40dd`: 208 candidates, 203 killed,
5 survived, 0 assay-classified equivalent, 0 `budget_exceeded`, 0 crashed;
R0 PASS, R2/overall FAIL (`MUTANTS_SURVIVED`). The five actual survivors
are `lib/serve.py:626` (`None -> []`), `lib/serve.py:704` (`and -> or`),
`lib/summary.py:173` and `:177` (both `None -> []`), and
`lib/summary.py:214` (the final `or -> and` in the four-reading
`limit_drift` presence guard).

Re-inspection preserved the first four equivalent-mutant identities, with
concrete call-path reasoning in REPORT. It also corrected the old
`serve.py:626` description: the value is the nested sample callback's
return and is consumed once through `bool(events)` by `Sampler.run`, not a
discarded `_on_topology_noop` return. `None` and `[]` remain behaviorally
identical on that path.

The `summary.py:214` survivor is a genuine oracle gap. Added
`test_limit_drift_ignores_pair_with_only_memory_high_missing`, isolating
the final guard operand. Red-first proof with that exact `or -> and`
mutation applied:

```
$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_summary.py::TestAbsentInputsStayNull::test_limit_drift_ignores_pair_with_only_memory_high_missing -q
1 failed in 0.43s (assert 1 == 0)
```

After restoring `lib/summary.py` byte-for-byte, the targeted green run was
`1 passed in 0.23s` with the same argv. Memory PSI full avg10 was 0.13
before the red run and 0.02 before the green run. The production modules
remain unchanged; only the focused oracle and close-out records changed.

This test-tree change requires a fresh 208-candidate r2 judgment on the
triage commit. After that separately read verdict, run final `r0-r1`
(registered bare-host wrapper) and `r3` serially on the final records tip,
only with memory PSI full avg10 below 5. No daemon or host infrastructure
is started by this close-out.

### 23. Fresh R2 on the triage commit and final survivor dispositions

The fresh R2 required by item 22 completed against the exact committed tree
`53abbf2c50d342de64016b5bf7a9198ead5ce717` (the focused test-tree triage
commit). Its progress stream records `run` at `2026-09-13T11:55:57Z`,
`end` at `15:10:08Z`, and `verdict_written` at `15:10:10Z`; the separately
read `.assay/verdict-r2.json` records **208 candidates, 203 killed, 5
survived, 0 equivalent, 0 budget_exceeded, 0 crashed**, outcome
`FAIL/MUTANTS_SURVIVED`, exit 1. The run completed normally; there was no
budget or crash condition.

The five survivors were reconciled as follows:

- `lib/serve.py:626` (`None->[]`) — accepted equivalent. The nested
  `on_sample` result is consumed only as `bool(events)` by `Sampler.run`;
  both values are false and no identity/type/value is persisted.
- `lib/serve.py:704` (`and->or`) — accepted equivalent. Sampler-produced
  CPU usage and monotonic timestamps are assigned together; when usage is
  absent the mutant's `util.rate(None, ...)` still leaves the already-null
  live rate unchanged, and recovery needs the same baseline tick.
- `lib/summary.py:173` and `:177` (`None->[]`) — accepted equivalent.
  `_parse_iso` has one call site and both results are consumed only by
  `start_dt and end_dt`; `None` and `[]` have identical false truthiness and
  neither value is returned or type-inspected.
- `lib/damon.py:329` (`False->True`) — accepted equivalent after correcting
  the old line description: line 329 is `_acquired_from_pool`, not
  `_entered`. When `pool is None`, teardown selects the non-pool branch
  before consulting this flag. When a pool is used, successful `acquire()`
  unconditionally sets the flag true; an acquire failure's release path is
  already a harmless no-op for an unacquired index. The exact temporary
  mutation was applied and restored byte-for-byte; serial
  `nice -n 19 ionice -c 3 timeout ... python3 -m pytest tests/test_damon.py
  -q` passed **68 tests in 0.55s**.

The prior `lib/summary.py:214` survivor is absent from this fresh verdict:
the focused `test_limit_drift_ignores_pair_with_only_memory_high_missing`
now kills it. Thus the mechanical assay result remains FAIL because assay
does not classify human-accepted equivalents, while all five survivors have
an explicit behavioral disposition and none is an untriaged oracle gap.

The final gates below are run on HEAD `53abbf2c` with no HEAD movement. This
section is intentionally recorded before the records-only close-out commit;
the gate receipts will be appended after both lanes are green.

### 24. Final gates and D-15 safety receipt

Both final lanes ran serially with HEAD held at
`53abbf2c50d342de64016b5bf7a9198ead5ce717`; no daemon or host infrastructure
was started:

- `nice -n 19 ionice -c 3 ./run-gate.py r0-r1` started at `15:26:28Z`,
  ran for `102.811s`, and exited 0. The detached coverage gate reported
  **1115 passed, 4 warnings, 100% line coverage and 100% branch coverage**.
- `nice -n 19 ionice -c 3 ./run-gate.py r3` started at `15:28:46Z`, ran for
  `17.553s`, and exited 0: **7/7 canaries rejected, 0 survived**. Its
  container was immediately verified and constrained to `--cpus=3`.

The separately read `.run-gate/history.json` records both latest entries with
the same commit and exit 0. They are marked `dirty: true` and
`history_eligible: false` only because this LOG/REPORT evidence was
intentionally uncommitted while the lanes ran; the tracked executable/test
tree was unchanged and the gate lanes themselves have `clean_tree = false`.
The R2 history entry is clean and eligible for the exact `53abbf2c` commit.

D-15 safety check: `python3 cgprofile.py serve --cap 1` refused with exit 2
(`unrecognized arguments`), proving the daemon CLI cannot reach `--cap`.
Static inspection confirms the daemon compose service is `privileged: true`,
`pid: host`, `cgroup: host`, `network_mode: none`, and uses an authored
`CGROUP_PARENT_DEV_INTERACTIVE` with only the named sessions volume; no Docker
socket or host bind is present. `serve.SessionServer._guard_path` confines
its direct writes/removals to the sessions root or DAMON admin root, and
`damon._write_nr_kdamonds` independently confines its raw sysfs write. The
daemon safety tests were included in the green r0-r1 run. The exact
`cgprofile-host-daemon` status check found `Exited (0)`; it is down, with no
live session or live probe left behind.

### 25. Controller close-out checkpoint after direct survivor recheck (2026-09-15)

The earlier mutation records above are not evidence for the current tree. A
direct diagnostic rejudge of the then-current implementation tip `5fd0ef13`
(using an isolated assay state directory because the registered command
lane's old embedded assay did not propagate the required state override)
completed with **252 candidates, 245 killed, 7 survived, 0 budget_exceeded,
and 0 crashed**. This was diagnostic evidence only, not a registered release
gate. The seven survivors were:

- `lib/damon.py:325` (`Gt -> GtE`)
- `lib/damon.py:589` (`And -> Or`)
- `lib/damon.py:596` (`Gt -> GtE`)
- `lib/serve.py:705` (`None -> []`)
- `lib/serve.py:793` (`And -> Or`)
- `lib/summary.py:174` (`None -> []`)
- `lib/summary.py:178` (`None -> []`)

The controller inspected each survivor's call path. The pool ownership
conjunction at `damon.py:589` was redundant after successful pool acquisition
and was removed. The explicit empty callback return at `serve.py:705` was
removed while preserving the callback's null contract. A solo-teardown
equality oracle, a partial CPU-baseline oracle, and malformed/missing ISO
timestamp oracles were added. Those changes are committed as `8032716c`
(`test(cgprofile): close remaining P1 survivor oracles`). The remaining
mutation lines require the fresh registered R2 on the new tree; the old
direct result must not be presented as a PASS.

Verification on `8032716c` was green before this records checkpoint:

- the focused P1 tests passed: **268 passed, 1 skipped**;
- the full package test run passed: **1200 passed, 5 warnings** (the generic
  `--cov=.` invocation reported aggregate coverage failure because it
  measured unrelated zero-covered `run-gate-project` scripts; the package
  gate below is the authoritative scoped result);
- registered `r0-r1` passed: **1200 passed, 4 warnings, 100% line and 100%
  branch coverage**, exit 0;
- registered `r3` passed: **7 rejected, 0 survived**, exit 0.

The records-only update does not satisfy the pending R2 identity requirement.
Before merge, run R2 against the exact final committed tree, separately read
its verdict, triage every survivor, and obtain the required fresh Sol xhigh
adversarial review. No daemon was started by this checkpoint.

### 26. Controller triage — R2 on `8df62f62` and new oracle

The exact-tree registered R2 verdict for `8df62f628b20c6280574aef8f6e76a53f9d00e35`
was read separately after the owner process and exact container were gone:
250 candidates were accounted for, 248 killed, 2 survived, 0 equivalent, 0
budget-exceeded, and 0 crashed; outcome `FAIL/MUTANTS_SURVIVED`, exit 1,
ended `2026-09-16T15:53:48Z`.

`damon.py:325` (`Gt->GtE`) is accepted as behaviorally equivalent because
`current == expected_end` implies `current > baseline` for every reachable
pool release with a live owned index. `damon.py:385` (`False->True`) was a
real unobserved failure: a failed second acquire could release constructor
index 0 belonging to the first session. The red-first regression
`test_failed_pool_acquire_cannot_release_a_live_constructor_index` failed
under the exact mutant and passed restored; the full `test_damon.py` file
then passed 82 tests. The test-only repair is `8cc740a2`.

That commit changes the judged tree, so the observed R2 is invalidated for
release. A fresh R2 is required on `8cc740a2`, followed by final r0-r1/r3,
fresh Sol xhigh review, and only then merge/release.

### 27. Controller triage — registered R2 on `51198f2e` (2026-09-25)

The registered P1 R2 on the quiet, exact tree
`51198f2e4759acbd69dfd770b843cdf20b1d6ed0` completed at
`2026-09-24T09:19:52.297677Z` in container
`run-gate-vbpub-r2-2104925-1790236649`. The separately read verdict records
R0 PASS and R2 `FAIL/MUTANTS_SURVIVED`, exit 1: 81 candidates, 71 killed,
10 survived, and zero equivalent, budget-exceeded, crashed, or hung. Every
candidate executed; this is not an incomplete or budget-limited run.

All ten survivors were behavioral-oracle gaps, not accepted equivalents:

- `access.py:390` (`True->False`): placement-probe announcement must be
  flushed, including its container identity, before Docker starts. The new
  `test_probe_diagnostic_is_flushed_before_docker_start` asserts this ordering.
- `targets.py:262-263` (`<=-><`, `None->[]`): a nonpositive local PID must
  return `None` before any host-tree lookup. The new
  `test_nonpositive_local_self_pid_returns_none_before_lookup` asserts both.
- `targets.py:267` (two `or->and` candidates): namespace-root derivation must
  refuse if either the host-visible cgroup path or local visible path is
  absent. The parameterized `test_namespace_root_requires_both_mapping_facts`
  covers each missing-fact direction.
- `version.py:52` (`or->and`): empty and non-string project prefixes must be
  rejected; covered by `test_empty_or_non_string_project_prefix_is_refused`.
- `version.py:57` (three subprocess-option flips): tag enumeration requires
  captured text output and must not raise on a nonzero status. The test fake
  now models `capture_output`, `text`, and `check`; the existing failure
  assertions discriminate each altered behavior.
- `version.py:60` (`or->and`): when stderr is empty, preserve the exit-code
  fallback; when stderr is present, preserve that diagnostic. Covered by
  `test_git_tag_probe_failure_preserves_diagnostic_or_exit_fallback`.

The focused suite `tests/test_version.py tests/test_targets.py
tests/test_access.py` passed 235 tests on the repair worktree. Its tests and
this triage record are pending commit. Because the next commit changes the
judged tree, no R2 evidence from `51198f2e` applies to the candidate: commit
the repairs and records first, hold the new tip quiet, then run registered R2
and the final short gates on that exact tip. Do not call this R2 a PASS or
release evidence.

### 28. Reconcile the current main tip before final P1 gates (2026-09-25)

Before starting the next long R2, the controller found `main` had moved from
`b52dc9f8` to `7f465669`. A read-only diff showed the intervening change was
only the 61-line assay backlog entry in `assay/nyxloom-trove/4-backlog.md`;
the P1 candidate did not yet contain that current main tip. Main was merged
cleanly into `rg55-p1-private-ns`, producing `a3b4dc1c`. This also carries
main's current assay source into the source-backed gate environment. The P1
implementation and regression tests were unchanged by the merge, but the
registered P1 R2 must judge the exact post-reconciliation tree. The earlier
short-gate receipts on `3e6e657e` are not the final receipts: rerun r0-r1 and
r3 on the final committed tip, then launch R2 on that same quiet tip.
