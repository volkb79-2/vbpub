# run-gate-WAVE-RG55-P4 — BRIEF-5 (return: round-2 repairs DONE, parked pending P2's 23.7.0 release)

Return reason: operator-instructed wind-down of this Claude session (no
new work; every agent checkpoints to files for a fresh successor) — NOT
a checkpoint-clause cut and NOT a blocker. Round-2 review's B5 + all six
non-blocking findings (S6–S11) are done, committed, and gate-verified
GREEN. Nothing is broken. The ONLY reason this package is not yet in
round 3 is an estate-level sequencing dependency: P2 (`rg55-run-gate-
client`) must release run-gate 23.7.0 to `main` before this package can
merge `main` in and run a real `assay-r2` on the tree it will hand to
the reviewer.

## State

Tip: **`1f8d9ca3`** on branch `rg55-followups-run-gate`, worktree
`/workspaces/vbpub/.worktrees/rg55-followups-run-gate` (already exists —
do NOT create another). Project dir `run-gate-project/`. Working tree
CLEAN. Commits this session (session 6), in order from round-2 review's
tip `4fa46b03`:

| commit | what |
|---|---|
| `07ae3a47` | B5 — `meta.expected` gains `source` |
| `459d07a5` | S7/S8 — pin exec/container `runner` stamp values, footprint source note, history any-vs-all |
| `0680d6d8` | S9 — scope the host-`/tmp` flake to this test's own container name |
| `cf043dda` | S10 — `Popen.returncode` set after `wait4` |
| `89eb7e38` | S11 — split RG-59's exit-code arm (125 vs 126/127) |
| `864f60f3` | S6 — regenerate footprint manifest + CONSUMERS transcript |
| `f3b983ec` | LOG/REPORT for the above |
| `e795c8f5`, `1f8d9ca3` | assay-r2 launched-then-terminated episode (see "The r2 that ran and was killed" below) — no code/test changes |

## What B5 + S6–S11 changed (file:line seams, current tip)

- **B5** — `footprint_manifest_lane_expected()`, `run-gate.py:2973`.
  Returns a FIFTH key `source`: the manifest lane's own `source` if
  present, else derived via `_METHOD_SCOPE_DERIVED_SOURCE` (`run-
  gate.py:2964`, a `{(method, scope): source}` table) from that lane's
  `method`/`scope`, else `null`. SPEC `R-44d` and the `profile_meta()`
  docstring (`run-gate.py:1742`) updated to match. Tests:
  `TestFootprintProfileMetaExpected`
  (`tests/test_run_gate.py`, search the class) — the two existing
  equality tests updated for the new key (they compare the WHOLE dict,
  so leaving them alone would have broken them), one new test each for
  the literal-source and derived-source cases.
- **S7** — `TestExecLaneInflightRecord::test_record_exists_when_exec_
  begins_and_cleared_after` (`tests/test_run_gate.py:15828`) gains
  `assert seen["record"]["runner"] == "exec"`; `TestInflightRecordStore::
  test_the_record_names_the_container_commit_and_tree`
  (`tests/test_run_gate.py:12012`) gains `assert data["runner"] ==
  "container"` — the container path's own half of the same B3 stamp.
  Writers themselves are `run-gate.py:7327` (`"runner": "container"`) and
  `run-gate.py:7643` (`"runner": "exec"`) — unchanged, only newly
  asserted.
- **S8** — `TestFootprintDisclosureLine::test_rusage_source_note_
  appears_only_when_the_summary_says_so` (`tests/test_run_gate.py:7739`)
  pins `print_footprint_line`'s `[source: rusage-maxrss]` segment
  (`run-gate.py:2281`). `TestHistoryTableResourceColumns::test_lane_
  stats_source_and_floor_flags_are_any_not_all`
  (`tests/test_run_gate.py:7169`) pins `_lane_stats`'
  (`run-gate.py:3648`) any-of-entries rule directly (two-entry mixed-mode
  list, asserts both flags `True`). NOTE: `_fmt_resource_stats`
  (`run-gate.py:3726`) has the SAME source-note pattern for the
  `history` table and was already covered by round-1's own
  `test_fmt_resource_stats_populated_and_all_null` — not touched again
  here, only the disclosure-LINE and the any/all FLAGS were the
  round-2-survived gaps.
- **S9** — `test_a_real_lane_run_never_touches_host_tmp`
  (`tests/test_run_gate.py:4099`). Both before/after `/tmp` globs now
  filtered to `f"run-gate-exec-*{my_name}*"` /
  `f"run-gate-shared-*{my_name}*"` where `my_name = self._container_
  name()` (pid-suffixed, unique to this test process) — a concurrent
  foreign checkout's own entries no longer cause a false failure.
- **S10** — `run-gate.py:7925`, `proc.returncode = code` added right
  after `code = os.waitstatus_to_exitcode(status)` in the rusage-path
  `wait4` success branch, inside `run_bare_host_lane()` (`run-
  gate.py:7769`)'s `profiler_state["mode"] == "rusage"` branch of the
  lane-run try/finally. Test: `TestBareHostProfilingWiring::test_returncode_is_
  set_after_wait4_reaps_the_child` (`tests/test_run_gate.py:14822`),
  spies on the real `Popen` object.
- **S11** — `run-gate.py:1139`-`1162`: `_DAEMON_ABSENT_EXIT_CODES =
  (125,)`, `_DAEMON_BROKEN_EXIT_CODES = (126, 127)` (was one combined
  `_DOCKER_EXEC_FAILURE_EXIT_CODES = (125, 126, 127)`), new
  `daemon_broken_reason()` (`run-gate.py:1162`), wired at
  `run-gate.py:1248` (`if proc.returncode in _DAEMON_BROKEN_EXIT_CODES:`)
  inside `ProfilerClient._ctl`'s `JSONDecodeError` branch, AFTER the
  existing not-running check. Tests:
  `test_docker_reserved_exit_code_names_the_real_cause_even_with_no_
  recognizable_prefix` (moved from exit 126 to 125 — its real condition)
  and new `test_exit_code_126_127_name_a_broken_daemon_not_a_stopped_one`
  (`tests/test_run_gate.py:13275`), both in the `TestProfilerClient`
  class (search `class TestProfilerClient`).
- **S6** — `run-gate.footprint.json` and `CONSUMERS.md` (the "The
  footprint manifest" section, "Recaptured RW-51/session 6" paragraph)
  regenerated from a fresh clean-tree `selftest` PASS + `footprint
  --write`. All three lanes now carry `"peak_at_floor": false` (real,
  non-null) instead of the prior generation's stale `null` mix.

All of the above verified by planting the exact mutant/revert against
the worktree's own `run-gate.py` (single-line edit, no copy — targeted
test only, then `diff -q` against a pre-edit backup to confirm
byte-identical restore before the next one). Full blow-by-blow in the
LOG's "Session 6" section.

## The three green lanes (evidence)

All on code/test tip `864f60f3` (the LOG/REPORT/r2-episode commits on
top, `f3b983ec`..`1f8d9ca3`, touch ONLY `.md` files — `git diff --stat
864f60f3..HEAD` shows zero `.py`/`.toml` changes, so these verdicts
still hold at the current tip verbatim):

- **`./run-gate.py selftest` (bare): PASS** — `1149 passed, 3 skipped …
  200.53s`; `diff-coverage OK: 1076/1076 changed executable lines
  covered (100.0%); branches 398/398 taken`; exit 0. Raw log (this
  session's scratchpad, gone with the session, but the REPORT quotes it
  verbatim): REPORT.md, "Gate verdicts (final, tip `864f60f3`...)".
- **`./run-gate.py --base rg55-run-gate-client assay-r1`: PASS** — first
  attempt, exit 0, `commit: 864f60f3d8925d35bc25cf3619c92e2452565b49`.
- **`./run-gate.py assay-r3` (bare): PASS** — first attempt, exit 0,
  `canary: 2 rejected, 0 survived`.

Evidence paths: `nyxloom-trove/reports/run-gate-WAVE-RG55-P4-REPORT.md`
("Session 6 — Round-2 repairs" section, has the finding→commit→
one-command-verification table AND the regenerated transcript AND the
three verdict lines); `nyxloom-trove/reports/run-gate-WAVE-RG55-P4-LOG.md`
("Session 6" section, full blow-by-blow with mutant-kill evidence per
commit).

## The r2 that ran and was killed (do not treat as round-3 evidence)

A live coordinator exchange mid-session (RW-52 → RW-54, see `run-gate-
project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`,
search "RW-52"/"RW-54"/"RW-55") led to `--base main assay-r2` being
launched at 21:14Z (bare, no container, pid 12419/12723) then killed
minutes later once RW-54 established that P4's r2 must judge the
POST-`main`-merge tree, not the current one. Confirmed clean: no
process/container remnants, worktree git status unaffected. Records
discarded (`.assay/verdict-r2.json` was never written; a partial
`.assay/progress-r2.jsonl` is harmless/resumable and was left in place).
Full account, including a stale-session confusion (agent
`ade7916e85220fbb9` — a long-closed prior P4 session, erroneously
reinvoked, sent messages impersonating controller authority, later
retracted, no lasting effect) is in the LOG's own "Session 6" tail —
read it if round 3 or the controller asks what happened here, but there
is NOTHING for a fresh successor to clean up or undo.

## What a FRESH successor must do, IN ORDER

You have NO memory of this session. Read this brief, then the LOG's
"Session 6" section (last ~250 lines) and the REPORT's "Session 6"
section, before doing anything else. Do NOT re-verify B5/S6–S11 from
scratch — they are done; spend your first tool calls on step 1 below,
not on re-reading every commit's diff.

1. **Wait for the controller's word that run-gate 23.7.0 is on `main`**
   (P2/`rg55-run-gate-client`'s own release). Do not poll aggressively —
   this is an external event you'll be told about, or can check cheaply
   with `git -C /workspaces/vbpub log --oneline -5 main -- run-gate-
   project/CHANGES.md` / `git -C /workspaces/vbpub log --oneline -3
   main` for a release commit once, not in a loop.
2. **`git merge --no-ff main`** into `rg55-followups-run-gate`, from
   this worktree. Expected conflicts: version/revision lines
   (`__revision__` — P4 is rev 42, `main` after P2's release will be
   23.8.0's own numbering territory; resolve so P4's rev-42 content
   survives and the version string reflects the merge, not a silent
   revert of either side) and `CHANGES.md` (both sides added entries
   since the fork point — keep both, reorder headings if `[Unreleased]`
   needs it). Resolve conflicts by READING both sides' actual diffs, not
   by mechanically picking "ours"/"theirs" — tell the controller if
   anything beyond version lines/CHANGES conflicts (that would be
   unexpected and worth a pause).
3. **`selftest` + `assay-r1` + `assay-r3` on the merge tip** (bare,
   `nice -n 19 ionice -c 3`, memory `full avg10` checked < 5 before each,
   one lane at a time, verdict read in a SEPARATE step every time — see
   `host-shared-with-production-load-rule` discipline). All three must
   be GREEN before touching r2. If any lane is RED, diagnose whether it
   is a merge-introduced regression (P4's own fault, fix it) or a
   pre-existing/P2-side issue (root-cause before assuming, don't
   blindly retry — see round-1's own RG-62 precedent in the LOG for what
   "root-caused, not retried past" looks like).
4. **Launch `assay-r2` on the merge tip**, bare, niced, HEAD quiet for
   the WHOLE run (no commits in this worktree from the moment you launch
   until the verdict is written — assay 6.1.1's resume identity is
   per-tree, RW-41):
   ```
   grep full /proc/pressure/memory   # must be < 5
   cd /workspaces/vbpub/.worktrees/rg55-followups-run-gate/run-gate-project
   nohup nice -n 19 ionice -c 3 ./run-gate.py --base main assay-r2 > <scratchpad>/p4-r2.log 2>&1 &
   disown
   ```
   No container is expected (this project's lanes are all bare-host) —
   if one DOES appear, remove it only by its exact printed name, never
   an `ancestor=`/pattern filter (a 2026-09-12 incident destroyed a live
   mutation-lane container this way — see memory
   `docker-remove-by-exact-name-only`). Track completion with a tracked
   `until ! kill -0 <pid>; do sleep 60; done` watcher (nohup, this
   session's own background-task mechanism) and park — do not
   synchronous-wait on a multi-hour run.
5. **Survivor triage into the REPORT** once the verdict lands: read
   `.assay/verdict-r2.json`, classify any survivors (justified vs. real
   gap), write the table into REPORT.md's own "Session N — assay-r2
   survivor triage" section, following the same table shape earlier
   sessions used (see e.g. P1's own survivor-triage precedent in the
   CONTROLLER-LOG dispatch table if this file has no local precedent
   yet).
6. **Return for round 3** with reviewer session `a076c67bd8b7a7c2a`
   (round 2 was ACCEPT-conditional on B5 alone; B5 is now done — round 3
   is the r2 survivor table plus a fresh look at the merge diff). Do not
   dispatch the reviewer yourself unless the controller asks you to —
   report readiness and let the controller route it, matching how
   rounds 1→2 were routed here.
7. **After round 3 ACCEPTs**: merge → `cmru release --project run-gate-
   project --set-version 23.8.0` → pip install (devcontainer or
   estate-standard install step, whatever this project's own CONSUMERS.md
   /README.md names as the install path) → verify `__revision__` reads
   42 post-install (`python3 -c "import run_gate; print(run_gate.
   __revision__)"` or the CLI's own `--version`/`doctor` output, whichever
   surfaces it — confirm against `run-gate.py`'s own `__revision__ = 42`
   literal before trusting an installed copy).

## Retention prompt for whoever resumes this (self-authored, `/compact`-style)

```
KEEP:
- P4 (run-gate follow-ups RG-57..61) is CODE-COMPLETE and gate-GREEN
  (selftest/assay-r1/assay-r3 all PASS) at tip 864f60f3 (docs-only
  commits on top through 1f8d9ca3). B5 + S6-S11 (round-2 review's full
  finding list) are done, committed, individually mutant-verified.
- The ONLY remaining work is sequencing: wait for P2's run-gate 23.7.0
  release to main, merge --no-ff main into rg55-followups-run-gate
  (expect version/revision + CHANGES.md conflicts only), re-run the
  three gates on the merge tip, launch assay-r2 bare/niced/HEAD-quiet,
  triage survivors, return for round 3 with reviewer a076c67bd8b7a7c2a.
- An assay-r2 attempt at 21:14Z this session was launched then correctly
  killed (RW-54: must judge the post-merge tree) -- no artifact from it
  matters, nothing to clean up.
- File:line seams for the round-2 diff are in this BRIEF's own "What B5
  + S6-S11 changed" section -- don't re-derive them by re-reading the
  whole diff.
- Host-load discipline: nice -n 19 ionice -c 3, memory `full avg10` < 5
  before every launch, one lane at a time, container removal by exact
  name only, verdict read in a separate step every time.

DROP:
- The mid-session ade7916e85220fbb9 confusion (a stale reinvoked prior
  session that briefly impersonated controller authority, retracted,
  fully resolved) -- background color only, in the LOG if ever needed,
  not load-bearing for anything forward.
- The exact wording/order of this session's own tool calls, mutant-plant
  mechanics, PSI readings at each checkpoint -- the LOG has the full
  record if an audit ever needs it; a fresh successor does not need to
  re-derive HOW round-2's findings were verified, only THAT they were.
```

## Return

Brief: `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-BRIEF-5.md`.
Tip: `1f8d9ca3`.
