# run-gate-WAVE-RG55-P7 — CHECKPOINT BRIEF-8

Checkpoint fired per the handoff's own HARD checkpoint clause (ARM at
~60 tool calls / ~120k context, CUT at the next coherent boundary; the
gate's own wall time does not count against the budget — park with the
watcher and return with pid + log path). **Green-boundary cut**: every
root-cause fix this session was dispatched for is done, tests-verified in
isolation and in their surrounding suites, and committed; the gate has
been RELAUNCHED from the repaired tip and is parked, capped, and watched.
The only open item is reading its verdict in a separate step once it
lands.

## What's DONE this session (all 5 gate findings, root-caused and fixed)

The registered gate's first real run (session 7's own launch, read by this
session from `/tmp/claude-1003/-workspaces-vbpub/
5d55184a-d2df-482e-aa2b-541cae13c0ad/scratchpad/p7-gate2.log`) ended RED:
`5 failed, 4681 passed, 20 skipped`. All five were genuine, previously
undetected gaps — none flaky, none environment-only — because the three
files that caught them (`test_distribution_gate.py`,
`test_untrusted_json_parse_sweep.py`, `test_standalone.py`) build/install a
real wheel or shell out to a real lint venv and were never part of A6's own
2077-test deferred sweep.

1. **`test_the_shipped_source_tree_is_pyflakes_clean`** — 5 real unused
   imports (`cli.py`'s `MUTATION_BUCKETS`, `liveness.py`'s
   `TYPE_CHECKING`-only `ProcessRunner`, `pytest` in two hung-bucket test
   files, `pathlib.PurePosixPath` in one of them), each confirmed zero call
   sites before removal. Fixed, committed `95d02f50`.
2. **`test_every_untrusted_json_parse_site_catches_RecursionError`** —
   `liveness.py`'s `_iter_test_events`/`_read_events_progress` parse the
   materialized pytest plugin's own NDJSON side-file events, written
   inside the CANDIDATE's (a consumer project's) own interpreter —
   untrusted per this project's own bar — and both caught only
   `ValueError`, missing `RecursionError`. Widened both to
   `except (ValueError, RecursionError):`, matching the established
   one-line shape used at every other guarded site. Fixed, committed
   `95d02f50` (same commit as #1).
3–5. **The three `test_standalone.py` real-R2-through-the-wheel tests**
   (`kills_one_mutant_...`, `with_no_declared_operator_site_is_
   inconclusive`, `mutant_that_outlives_the_lane_budget_is_its_own_
   bucket`) — `_expected_r2_artifact`'s hand-built documents were stale
   against two of this package's own additive fields never exercised by
   any prior session's own test runs: A3's `hung` bucket (added
   `"hung": []` to the 3 `r2_claim["mutation"]` literals) and RW-36's
   `judgment.r2.liveness` record (added `liveness_active`/`liveness_
   reason`/`liveness_plugin` params, defaulted to the "off" shape every
   real lane in this module hits). Also: `judgment.r2.budget_per_
   candidate_derived_s` (A1) is measured from the run's own real baseline
   wall-clock time — genuinely unpredictable, same class as the existing
   `volatile` top-level fields — so `_assert_complete` now pops+sanity-
   checks it from a copy before the exact-match assertion instead of
   requiring the fixture to hand-inject it. Fixed, committed `ee24ced6`.

Full root-cause/fix/commit/proof table: REPORT.md's new "Session 8 — gate
repairs" section (committed `33a30e11` alongside the matching LOG
entries). No test was weakened, skipped, or had an assertion removed to
reach green.

**Verification beyond the five tests themselves**: `liveness.py` at
**100% line+branch coverage** (`--cov=assay.liveness --cov-branch`)
against the full liveness-relevant suite (410 tests across 11 files);
`cli.py`'s changed import verified against its own 4 direct suites (106
tests). All local runs used `nice -n19`/`ionice -c3`, serial, with PSI
checked before each (`full avg10` stayed 0.07–2.13 throughout, well under
the 5.0 threshold).

**Tip at cut:** `33a30e11` on branch `assay-liveness`, worktree
`/workspaces/vbpub/.worktrees/assay-liveness`. Commits this session (newest
first):

```
33a30e11 log(run-gate-project): P7 LOG self-hash for 95d02f50/ee24ced6 + REPORT "Gate repairs" section
ee24ced6 fix(assay/tests): B091 P7 gate finding -- test_standalone.py's real-wheel expected-artifact drift (3 R2 tests)
95d02f50 fix(assay): B091 P7 gate finding -- pyflakes unused imports + RecursionError guard on liveness.py's two untrusted JSON parse sites
```

## What's OPEN — reading the SECOND real gate run's verdict

The gate (`./run-gate.py tester-unified`) was relaunched from `33a30e11`
after a PSI check (`full avg10` 0.18). Launching shell pid `2868853`, the
`run-gate.py` process itself pid `2868854`, container `clever_maxwell`
(image `tester-unified:local`), capped `docker update --cpus=3`
immediately after it appeared. Log:
`/tmp/claude-1003/-workspaces-vbpub/5d55184a-d2df-482e-aa2b-541cae13c0ad/
scratchpad/p7-gate4.log` — **session-local; will not exist for a genuinely
fresh successor's own environment.** A tracked background watcher
(`until ! kill -0 2868854; do sleep 60; done`) was armed in this session
so a notification arrives automatically when the process exits; if this
session is the one reading this brief (resumed rather than replaced), that
notification is the next thing to act on. A successor must instead:

1. Check whether the process/container is still alive:
   `docker ps --no-trunc --format '{{.Names}} {{.Status}}' | grep
   tester-unified` (image `tester-unified:local`) and/or `pgrep -af
   'run-gate.py tester-unified'`. If gone with no verdict captured (the
   ORIGINAL log path above is gone too), check `git log --oneline -3`
   first; if `33a30e11` is still the tip, relaunch exactly as this session
   did:
   ```
   cd /workspaces/vbpub/.worktrees/assay-liveness/assay
   cat /proc/pressure/memory   # back off while full avg10 > 5
   nohup ./run-gate.py tester-unified > <scratchpad>/p7-gate5.log 2>&1 & disown
   # then: docker update --cpus=3 <new container name> once it appears
   ```
   Check `docker ps` for gate containers estate-wide first — ≤ 2 at a
   time (other packages' mutation containers count).
2. Read the verdict in a SEPARATE step from any launch (LESSONS L4) —
   `grep`/`tail` the log for `run-gate: lane 'tester-unified' exit N` and
   the pytest summary line(s) immediately above it, never a pipe tail off
   the launch itself.
3. **If GREEN:** add a final LOG entry + REPORT "Gate re-run" paragraph
   with the verdict line and elapsed time; the package is DONE. Report the
   tip hash and the gate verdict line, total elapsed wall time, and that
   all 5 original failures are individually accounted for in REPORT.md's
   table.
4. **If RED again:** triage exactly as this session did — read the actual
   failing test(s) named in the summary (never assume it is the same five;
   confirm), determine whether it is a NEW real gap (fix it, commit,
   re-run) or something this session's own fixes somehow missed, before
   declaring the package done. Do not re-touch the 5 already-fixed sites
   without first confirming the new failure list names them again.

## Judgment calls this session made (flagged per BLOCKED protocol)

1. **Split the 5 fixes into 2 code commits by root-cause family**
   (pyflakes+RecursionError in one, since both are lint/robustness fixes
   to the same 2 `liveness.py`/`cli.py` files plus 2 test-import cleanups;
   the 3 `test_standalone.py` fixes in the other, since they are one
   coherent fix to one file's own expected-artifact helper) rather than 5
   separate commits. Default: the handoff's "LOG entry per commit" reads
   as commits mapping to coherent root-cause units, not literally one
   commit per failing test name — REPORT.md's table still accounts for
   all 5 individually regardless of commit boundary.
2. **`budget_per_candidate_derived_s` handling in `_assert_complete`**:
   chose to pop-and-sanity-check it (mirroring the file's own existing
   top-level `volatile` pattern and `test_runner_run_lane_r2.py`'s
   existing `is not None`/`> 0` precedent for the identical field) rather
   than adding it as a new required parameter to `_expected_r2_artifact`
   that callers would have to somehow predict. A real run's own measured
   wall-clock timing cannot be hand-stated in a fixture without turning
   the assertion into a tautology (measuring against itself) or a flaky
   hard-coded number — the same reasoning the file's own `_assert_complete`
   docstring already states for `assay_version`/`started`/`ended`.
3. **Reproduced all 5 failures locally rather than trusting the captured
   CI log's own diffs verbatim** — pytest's assertion-diff truncation
   persisted even under `-vv` when combined with `-q` (a `-q`/`-vv`
   verbosity-counter interaction), so the captured `p7-gate2.log`'s own
   `Differing items:` lines were NOT sufficient to see the exact missing
   keys; local repro with `-vv` alone (no `-q`) surfaced the full `Full
   diff:` block needed to root-cause `hung`/`liveness`/`budget_per_
   candidate_derived_s` precisely rather than guessing from truncated
   output. Worth flagging for any future session reading a gate log with
   a similar truncated assertion diff.

## HOST LOAD — this session's own observations

`/proc/pressure/memory` `full avg10` ranged 0.07–2.13 across this
session's own local test runs (well under the 5.0 back-off threshold) and
was 0.18 at the second gate's launch. `nice -n19`/`ionice -c3` for every
pytest invocation; serial; no concurrent heavy command started by this
session. Gate containers estate-wide: 1 (P1's long-running
`run-gate-vbpub-r2-680904-...`) immediately before this session's own
launch, 2 (adding `clever_maxwell`) after — at, not over, the `<= 2`
limit. `docker update --cpus=3` applied to `clever_maxwell` within
seconds of it appearing in `docker ps`.

## Self-authored retention prompt (paste into a successor's first turn, if one is dispatched)

```
Resume RG-55 P7 (assay B091) from BRIEF-8 -- reading a gate verdict, not a
new deliverable. Tip is 33a30e11 on branch assay-liveness (already the
worktree's current branch -- no new worktree add). ALL FIVE of the prior
gate's failures are root-caused, fixed, and committed (95d02f50: pyflakes
5 unused imports + RecursionError guard on liveness.py's 2 untrusted JSON
parse sites; ee24ced6: test_standalone.py's 3 real-wheel R2 tests, stale
against A3's `hung` bucket + RW-36's `liveness` record + A1's measured
`budget_per_candidate_derived_s`). REPORT.md's "Session 8 -- gate repairs"
table and matching LOG entries are committed (33a30e11). liveness.py
verified at 100% line+branch coverage; cli.py's changed import verified
against its own 4 suites; no test weakened or skipped.

The gate (./run-gate.py tester-unified) was RELAUNCHED from 33a30e11 (PID
2868854 the run-gate.py process, container clever_maxwell, capped at 3
CPUs) with a tracked background watcher armed. ONE thing left: read that
run's verdict IN A SEPARATE STEP from any launch (LESSONS L4) -- check
`docker ps`/`pgrep -af 'run-gate.py tester-unified'` first; if it's gone
with no verdict captured, relaunch from tip 33a30e11 after a PSI check
(<=2 gate containers estate-wide). If GREEN: add a final LOG/REPORT entry
with the verdict line and elapsed time, the package is DONE -- report tip
hash + verdict + elapsed time. If RED: read the ACTUAL failing test names
from THIS run's own summary before assuming they match the prior five --
triage genuinely, same discipline this session used (reproduce locally
with `-vv` alone, never `-q -vv` together -- that combination truncates
pytest's own assertion diff even at max verbosity).
```
