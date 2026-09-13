# run-gate-WAVE-RG55-P7 — session 9 checkpoint BRIEF

**Tip as of this brief:** `802f0855` (branch `assay-liveness`, worktree
`/workspaces/vbpub/.worktrees/assay-liveness`, project `assay/`). Worktree
clean. B1 committed and verified; B2/B3/B4/B5 and the S-item fold-in are
NOT started; the registered gate has NOT been run this session.

**Why this session cut here.** Checkpoint clause is HARD: ARM at ~60 tool
calls, CUT at the next coherent boundary. Session 9 reached ~68 calls right
at B1's own commit — a clean green boundary (tests pass, pyflakes clean,
commit landed) — with B2 (the hardest remaining item, a calibration
redesign) entirely unstarted. Continuing risked hitting the ~90-call hard
ceiling mid-B2 with no coherent cut point. Cutting now, at a real commit
boundary, is what the clause asks for.

## Self-authored retention prompt for the successor

KEEP: this whole BRIEF (it is the continuation contract); the round-1
review (`run-gate-WAVE-RG55-P7-REVIEW-round1.md` on `main`) and RW-49
ruling (`run-gate-WAVE-RG55-CONTROLLER-LOG.md` on `main`) — re-read both in
full, they are the actual spec, this BRIEF only indexes them; the file:line
seams below (verified this session, save yourself the re-discovery).
DROP: session 9's own tool-call-by-tool-call path to finding those seams —
only the destination matters, not the search.

## What session 9 did (B1 — DONE, committed `802f0855`)

`_EXIT_STATUS = None` sentinel in `liveness.py`'s `_PLUGIN_SOURCE`;
`pytest_unconfigure` returns without `os._exit`-ing when `_EXIT_STATUS` was
never assigned by `pytest_sessionfinish` (the session-never-started shape).
Docstring wording fixed in `liveness.py` and `docs/CONSUMERS.md` (both said
`os._exit` happens "after `pytest.main` returns" / "right after the
terminal summary prints", true only on the sessionfinish path).

Tests added: `tests/test_liveness.py` (3 plugin-module-level unit tests,
both branches of the new conditional + the `ASSAY_LIVENESS_EXIT` unset
case) and `tests/test_cli_run.py` (1 real-subprocess end-to-end CLI test,
`test_run_liveness_does_not_turn_a_configure_time_raise_into_a_false_survivor`
— the reviewer's own reproduction, a `tests/conftest.py` `pytest_configure`
hook that bootstraps from the mutated product code, asserting
`mutation["killed"] == [<1>]`, not `survived`).

Verification commands (re-run if you want to confirm before building on
top, not required — the LOG entry already records these passed):
```
cd assay && python3 -m pytest tests/test_liveness.py -k unconfigure -q
cd assay && python3 -m pytest tests/test_cli_run.py -k configure_time_raise -q
cd assay && python3 -m pytest tests/test_liveness.py tests/test_liveness_runner_monitor.py tests/test_liveness_proc_helpers.py -q
cd assay && python3 -m pyflakes src/assay/liveness.py
```

## What remains — B2 (RW-49/D3, calibration (a))

**The ruling, verbatim from RW-49** (already quoted in your dispatch, restated
here for locality): the plugin records `session_start` (from
`pytest_configure`), every phase (`setup`/`call`/`teardown`, `when` on the
record) and `session_finish`; `expect_next_event_within_s = max(3 × the
baseline's worst observed inter-event gap incl. the leading and trailing
gaps, 15 s)`; the pre-first-event bound (for a candidate before its own
first event) is 3 × the baseline's leading gap (min 15 s); only `call`
events are forwarded to the progress stream (A4 contract unchanged); the
plugin-inactive fallback stays.

**Seams found this session (liveness.py, pre-B2-edit line numbers — B1's
edit shifted the file by ~9 lines starting mid-plugin-source, re-grep
before trusting these):**
- `_PLUGIN_SOURCE`'s `pytest_runtest_logreport` (~line 169 post-B1) returns
  early unless `report.when == "call"` — this early return is what must
  drop (record every phase, carry `when` on the record), while the
  DOWNSTREAM `test`-event forwarding to the progress stream (`assay.
  liveness.baseline_test_events`, consumed in `mutation.py` ~line 2129)
  must keep filtering to `call` only (A4 contract unchanged) — so the
  filter moves from the plugin's write side to a read side, or the plugin
  tags `event: "test"` only for `call` and a NEW event kind (e.g.
  `event: "phase"`) for `setup`/`teardown`, whichever keeps
  `_iter_test_events`'s existing `record.get("event") == "test"` filter
  correct without touching every caller. Decide before touching
  `pytest_sessionfinish`/`pytest_configure` (need a NEW `pytest_configure`
  hook in the plugin for `session_start` — does not exist today).
- `baseline_slowest_test_s` (~line 587 pre-B1) and
  `compute_expect_next_event_within_s` (~line 635 pre-B1) are the two
  functions the ruling's calibration (a) replaces/extends — probably a
  NEW function (e.g. `baseline_worst_inter_event_gap`) built on a NEW
  generator that yields every phase/session_start/session_finish event in
  file order (parallel to `_iter_test_events`, which stays `test`-only
  per B088's "one parse loop" discipline — you likely want a second
  one-parse-loop function for the gap computation, not a hand-rolled
  second traversal).
- `mutation.py` params `liveness_slowest_test_s`/
  `liveness_expect_next_event_within_s` (~1862-1863) and the `plan` event
  fields `slowest_test_s`/`expect_next_event_within_s` (~2114-2115) are
  READ BACK verbatim from the caller (`runner._run_prepared_lane`, NOT
  YET LOCATED this session — grep `liveness.compute_expect_next_event_within_s`
  or `liveness.baseline_slowest_test_s` in `runner.py` to find the actual
  call site that computes these before `run_mutation` is invoked). That
  is where the new calibration function must actually get called, and
  where the pre-first-event bound (3 × baseline's leading gap) needs its
  own plumbing if it is a distinct number from `expect_next_event_within_s`
  — re-read the ruling text carefully, it may mean `LivenessRunner` needs
  a SECOND threshold for the window before any event has arrived, not
  just the one it has today.
- The review says: "Update `plan.slowest_test_s` semantics (rename or
  document: it is now the worst gap, say which)." — a naming decision:
  either rename the wire field (breaking-ish, needs a CHANGES note) or
  keep the name and document that it now means something different.
  Given D1 already keeps schema 11 with a disclosure note for `hung`,
  the lower-risk choice is probably: keep `slowest_test_s` name, add a
  CHANGES/CONSUMERS sentence saying what it now measures. Your call, not
  pre-decided by the controller.
- CONSUMERS.md's liveness section (`### Liveness for a native R2
  python/pytest lane`, found at line ~2073 pre-B1-edit, search
  `## Liveness for a native R2` to relocate) documents the OLD `max(3 x
  slowest_test_s, 15s)` formula and needs the new one, plus — per the
  review's item 3 — "document the residual limitation... it currently
  documents the formula but not a single word about what makes it
  wrong."

**Regression tests required (review's own prescription, "why the suite
could not catch B1/B2/B3" section):** a fixture project with a
module/session-scoped fixture that idles PAST the derived bound — but
"it can use a short bound by lowering the constants through the existing
constructor injection rather than sleeping 40s in CI" (i.e. drive
`LivenessRunner`'s thresholds down via its constructor kwargs the way
`test_liveness_runner_monitor.py` already does with `_HUNG_CPU_WINDOW_S`
monkeypatching / `expect_next_event_within_s` override — do NOT sleep
40s in a real pytest fixture in CI). Plus a slow-collection case (no
`test` event at all yet). The task's quality bar: `liveness.py` stays
100% line+branch, every new conditional tested with the other optional
parameter at its default, **≥3 planted mutants recorded with the catching
test** for the B2 calibration specifically (mirror the existing mutant
table entries in `test_liveness_runner_monitor.py`,
`test_idle_threshold_is_inclusive_at_the_exact_boundary` /
`test_cpu_growth_floor_is_inclusive_at_the_exact_boundary`, as the style
model for "planted, confirmed silently uncaught before this test, pinned
at the exact boundary").

## What remains — B3 (tests_completed cwd keying)

**Confirmed root cause and fix, both located this session — this is the
smallest of the four remaining blockers.**

Writer is already correct: `LivenessRunner.__call__` (`liveness.py`,
`_events_path_for_cwd`) is called from `execute_plan`
(`runner.py:1188`, `run_cwd = resolve_run_cwd(cwd, plan)` then
`process_runner(argv, env=..., cwd=run_cwd, ...)` inside
`_execute_plan_inner`) — so the writer's `cwd` IS already the
lane-`cwd`-resolved directory.

Reader is wrong: `mutation.py`'s `_run_one` (inside `_execute_mutation_jobs`,
~line 2491-2495) calls
`liveness.candidate_events_path(liveness_events_dir, snapshot.project_root)`
— `snapshot.project_root` is the PRE-resolution directory (what `_run_one`
passes as `cwd=` into `execute_plan`), never re-joined with
`plan.cwd_declared`.

**Fix:** in `_run_one`, compute
`run_cwd = resolve_run_cwd(snapshot.project_root, plan)` (the exact
pattern `runner.py:2805` and `runner.py:4712`'s own A-367 comment already
use — read that comment, it explains WHY this must be the one join, never
a fifth hand-rederivation) and pass `run_cwd` to `candidate_events_path`
instead of `snapshot.project_root`.

**Import wrinkle:** `mutation.py` cannot `from .runner import
resolve_run_cwd` at module level — `runner.py` imports `mutation` at
module level (confirmed: `from . import (..., mutation, ...)` at
`runner.py:98-108`), so the reverse import is circular. `run_mutation`
already breaks this the same way for `execute_plan`: a LAZY `from .runner
import execute_plan` at `mutation.py:2133`, then `execute_plan` is passed
as a `Callable` parameter down into `_execute_mutation_jobs` and used
inside `_run_one`. Thread `resolve_run_cwd` through the identical path
(lazy-import both names together at line ~2133, add a
`resolve_run_cwd: Callable[[Path, "CommandPlan"], Path]` parameter to
`_execute_mutation_jobs`, pass it at the call site ~line 2320, use it in
`_run_one`) rather than a fresh per-call local import inside `_run_one`
— matches the existing style exactly.

**Regression test:** extend `tests/test_mutation_progress_budget_plan.py`
(sits right next to
`test_candidate_progress_event_gains_tests_completed_from_its_own_events_file`,
~line 284 — copy its shape) with a lane declaring `cwd="sub"`. Needed
pieces, all confirmed working this session by reading sibling tests:
- `make_lane(argv=("pytest", "-q"), cwd="sub")` / `make_plan(lane)` (the
  `cwd` kwarg already exists on `conftest.make_lane`).
- The repo fixture must commit the mutated file UNDER `sub/` (e.g.
  `repo.write("sub/pkg/flags.py", _TEXT)`) so `_TARGETS`'s `path` is
  `"sub/pkg/flags.py"` (repo-top-relative, matches `MutationTarget.path`
  semantics — independent of `cwd_declared`, do not confuse the two).
- The fake `decide()`/`process_runner` receives `cwd` as the ALREADY
  RESOLVED `run_cwd` (this is what the writer side gets right today —
  confirmed by reading `test_the_lane_command_runs_in_the_declared_cwd` in
  `tests/test_runner_lane_cwd.py`, which asserts real subprocess `$PWD`
  lines end in `/app`). Write the fake candidate's events file via
  `liveness.candidate_events_path(events_dir, Path(cwd))` — same call the
  real `LivenessRunner` makes — so this test genuinely proves reader/
  writer agreement, not just "some number came back non-zero".
- No `tracked_directories` check fires on this path (that check lives at
  `runner.py:2805`'s neighbourhood, inside whatever function
  `runner.run_lane`'s R2 snapshot-unit path calls — NOT reached when a
  test calls `mutation.run_mutation` directly, which is what this test
  file already does throughout). Confirmed by reading the surrounding
  code; do not add extra git-tracked-directory ceremony to the fixture
  beyond committing the file.
- Assert `tests_completed` is the REAL count (not `0`) on both candidate
  progress events. The review's own repro used a lane with `cwd = "sub"`
  and printed `{'outcome_bucket': 'killed', 'tests_completed': 0}` as the
  BEFORE-fix defect — pin the AFTER-fix non-zero value as the assertion.

## What remains — B4 (schema 11 kept, disclose)

RW-49/D1 already decided: keep `schema_version` 11, disclose in CHANGES as
BREAKING and in CONSUMERS' migration notes, naming the exact diagnostic
`unknown mutation field(s): ['hung']` an assay < 6.2.0 `assay verify`
prints on a 6.2.0 native-R2 document, with the rule "verify with the
release that produced the document or newer." No code change — purely
CHANGES.md + CONSUMERS.md.

**CONSUMERS.md seam:** `## Migration notes (v10 → v11)` is at line **2461**
(confirmed this session). Its opening paragraph currently reads "This cut
carries exactly ONE change, and it touches exactly one field" — that
sentence becomes FALSE the instant `hung`/`liveness` land under the same
schema number without a new cut, so it needs either a caveat inline or
(cleaner) a new subsection appended after the existing "If you ingest a
mutation report" subsection, titled something like `### B091/6.2.0: a
native-R2 verdict is now refused by an assay < 6.2.0 \`verify\`` — put the
diagnostic string, the schema-11-stays-11 fact, and the "pin to the
release that produced the document or newer" rule there. Do not weaken
the existing "exactly ONE field" sentence's truth about the v10→v11 cut
itself (`discarded`) — that claim is still true for THAT cut; B4 is a
second, later, same-schema-number change the section didn't anticipate
when it was written, so it needs its own paragraph, not an edit to the
old one.

**CHANGES.md seam:** the sentence must land in CHANGES too (per the task
brief: the `.assay-inbox` release-notify text — memory rule: sha256
required — "must carry the same note when the controller releases — put
the sentence in CHANGES so it is copied." That is the ENTIRE action needed
here from session 9/10's side — write it once, in CHANGES, worded so the
controller's later release step can copy it verbatim into the
`.assay-inbox/release.json` notify text).

## What remains — B5 (CHANGES restructure)

Current `[Unreleased]` section (CHANGES.md, confirmed read in full this
session, starts line 5) is ONE `### Fixed (detail)` heading containing six
bullets, the second of which ends with the stale sentence:

> **Not yet shipped in this entry:** the active liveness-monitoring loop
> and the new `hung` outcome bucket that distinguishes an idle stall from
> a genuine CPU-bound runaway (`budget_exceeded`) -- tracked as open work
> on B091.

— DELETE that sentence (the 4th bullet in the same section already ships
both things it claims are unshipped).

Restructure into `### Added` / `### Changed` / `### Fixed`, per the task
brief's own instruction (verbatim, do not re-derive):
- **Added:** `judge.mutation.liveness` lane key, `--rejudge`/
  `--rejudge-outcome` CLI surface, the `hung` bucket, the progress
  fields (`slowest_test_s`/`expect_next_event_within_s`/`tests_completed`/
  the `test` forwarding).
- **Changed** (each with a BREAKING note): omitted
  `judge.mutation.budget_per_candidate` now means bounded (was
  unbounded); `_refuse_unbounded_without_unit_bounds` now ADMITS a lane
  it used to refuse (`budget = "unbounded"` + omitted
  `budget_per_candidate`); `argv_effective` moves for every native-R2
  pytest lane (the `-p assay_liveness_plugin` append); B4's schema-11
  same-shape-incompatibility note (cross-reference, do not duplicate the
  full text — point at the paragraph).
- **Fixed:** the genuine bug fixes (the `budget_per_candidate_derived_s`
  verify regression, the `judge_mutation` precedence gap, B1's sentinel
  fix from THIS session — add an entry for `802f0855` here too, it never
  got a CHANGES line).

The six existing bullets map roughly 1:1 onto this split — this is an
edit/reorganize pass over existing prose, not new research. Budget it as
one focused commit.

## S-items — fold if cheap, defer with a reason otherwise

Already screened this session (do not re-screen from scratch):

- **S2 — DO NOT fold blindly.** The prescription (`argv_invokes_pytest`
  should be positional: `argv[0] == "pytest"` etc.) directly CONTRADICTS
  the existing test `test_argv_invokes_pytest_true_cases`'s own docstring
  in `tests/test_liveness.py` (~line 69): "A bare 'pytest' token matches
  WHEREVER it appears... this is a deliberately permissive rule, not a
  positional one" — asserting `("python", "script.py", "pytest")` is
  `True` today. Making S2's fix means CHANGING that test's asserted
  behaviour (and its docstring), not just adding a new one. Real, but not
  a free 10-line fold — decide deliberately, and if you fold it, update
  both the production rule AND that existing test case (probably move it
  to the false-cases list) in the SAME commit so the suite never goes red
  on a stale assertion.
- **S3** — cheap: an unknown `--rejudge` id refuses with
  `UNREADABLE_ARTIFACT` instead of `BAD_LANE_CONFIG`
  (`runner._refuse_bad_rejudge_config`, `runner.py:5775` is the sibling
  that already uses the right code — find the unknown-id refusal site
  near it and match).
- **S4** — cheap: `config.py:3018`'s refusal message says `liveness =
  "true"`/`"false"` (quoted) are accepted spellings; they are refused.
  Fix wording, drop the `LIVENESS_POLICIES` internal-vocabulary echo.
- **S6** — cheap: `cli.py:250`'s `--rejudge-outcome` help text
  hand-transcribes the bucket list; build it from `mutation.
  MUTATION_BUCKETS` + the alias map instead (the A-228 pattern this
  session's own `verdict.py` already guards against elsewhere).
- **S7** — cheap: `mutation.py`'s `mutation_pct` docstring doesn't mention
  `hung` in its denominator explanation; one-sentence fix.
- **S9** — cheap: `materialize_liveness_plugin`'s `target.write_text` can
  raise a bare `OSError` on an unwritable project root; wrap into a clean
  `AssayError`/`BAD_LANE_CONFIG` (or `OUTPUT_WRITE_FAILED` if that reason
  code exists — check `errors.py`) naming the directory.
- **S10** — cheap, docs-only: CONSUMERS.md says liveness applies to "R2
  candidates only"; true of `os._exit` but the argv injection also reaches
  R3 canary runs (inert there — `ASSAY_LIVENESS_EVENTS`/`_EXIT` unset).
  One sentence.
- **S1** — DEFER. Real design work: refuse/WARN when
  `<project_root>/.assay/` is not git-ignored (mirroring the `--progress`
  diagnostic — find that precedent first) PLUS a candidate-file cleanup
  policy (delete each candidate's `.ndjson`/`.stdout`/`.stderr` triple
  once `tests_completed` is read, or key+purge the whole run directory at
  sweep end). Not a small patch; every vbpub subproject happens to be
  covered by root `.gitignore` today so this is not gate-blocking, but it
  is real debt. Defer to a follow-up backlog row, do not rush it into
  this repair set.
- **S5** — DEFER. Perf change inside the hot monitoring loop
  (`_read_events_progress` re-parses the whole events file every tick;
  `cpu_samples` unbounded under `budget_per_candidate = "none"`). Fixing
  it risks a subtle regression in exactly the loop B1/B2 just hardened;
  do it as its own reviewed change, not folded in under time pressure.
- **S8** — cheap, but in a DIFFERENT file:
  `assay/nyxloom-trove/4-backlog.md`'s B091 entry lists six hashes,
  omitting `95d02f50`/`ee24ced6` (the two that actually made the gate
  green), and claims the gate was green after the docs/backlog pass
  when it was RED (5 failed) until those two landed. Add the two hashes,
  correct the sentence. In scope (`assay/` project dir) — fold it.

## GATE — not run this session

Once ALL of B2/B3/B4/B5 land and every folded S-item is committed, and the
worktree is clean: `docker ps` (≤2 gate containers estate-wide), `cat
/proc/pressure/memory` (launch only while `full avg10` < 5 — it read 2.69
at the end of session 9, likely still fine but RE-CHECK, do not trust this
number), then from `assay/`:
```
nohup nice -n 19 ionice -c 3 python3 ./run-gate.py tester-unified > <scratchpad>/p7-gate6.log 2>&1 & disown
```
tracked watcher (`until ! kill -0 <pid>; do sleep 60; done`),
`docker update --cpus=3 <exact container name>` right after it appears.
DO NOT touch any tracked file while it runs (HEAD movement voids the
measurement — this is the exact thing that wasted an earlier P7 run, see
the LOG's `edb995f4`/gate-history entries). Read `run-gate: lane
'tester-unified' exit N` in a SEPARATE step (~25 min wall time; does not
count against your own call budget — park with the watcher and return
pid+log path if you must cut before it finishes, never mid-gate).
Container removal ONLY by exact name (RW-47 — a prior session's
image-filtered sweep destroyed another package's live container).

## Records

Continue the SAME LOG (`run-gate-WAVE-RG55-P7-LOG.md`) and REPORT
(`run-gate-WAVE-RG55-P7-REPORT.md`) files — do not start new ones. LOG
entry per commit, self-hash rule (the entry names the hash of the commit
it documents). REPORT needs, once everything lands: the "Round-1 repairs"
table (finding → commit → one-command verification), the mutant table for
B2's calibration, and the gate verdict line. Commit trailers:
`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and
`Claude-Session: https://claude.ai/code/session_01YBJzBA7KyG4ayu5ndNf9Hx`
(same session id — this is a continuation, not a new dispatch).
