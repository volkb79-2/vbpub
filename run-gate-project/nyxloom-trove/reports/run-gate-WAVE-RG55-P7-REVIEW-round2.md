# run-gate-WAVE-RG55-P7 — adversarial review, round 2

**Reviewer:** the same fresh Opus session that produced round 1 (never a
fork of any implementer or the controller). **Branch** `assay-liveness`,
worktree `/workspaces/vbpub/.worktrees/assay-liveness`, project `assay/`.
**Repair range:** `8f972def..6f3aefad` (15 files, +2252/-216). **Full
package range re-walked:** `main...6f3aefad`. Blind phase on the repair
diff first (every hunk of `liveness.py`, `mutation.py`, `runner.py`,
`config.py`, the four test files, CHANGES/CONSUMERS/backlog), reconcile
against LOG/REPORT sessions 9 + 10 second.

## VERDICT

**REJECT — one blocker, B6.**

All five round-1 blockers are genuinely repaired. I re-ran every round-1
probe verbatim against the repair tip and each one now behaves correctly
(the exit-code table, the two e2e lane pairs, the `cwd` lane, the
non-pytest WARN lane, `--rejudge`, the 6.1.1 `verify` refusal). The
repairs are real, not cosmetic; several are better than the prescription
I wrote. `liveness.py` is 100% line AND branch, reproduced independently.

B6 is **not** a regression from these repairs — it is present at
`8f972def` too and I missed it in round 1. It is the same failure class
as round-1 B2 (a healthy candidate SIGKILLed and bucketed `hung`), it is
reachable by a real, in-estate lane at assay's default `liveness = "auto"`
(RW-49/D2), and it silently destroys the R2 measurement on that lane:
**an events file receiving `session_finish` from more than one process —
which is exactly what a `pytest -n auto` (xdist) lane produces — arms the
30 s `_HUNG_SESSION_FINISH_GRACE_S` branch at the FIRST worker's finish,
and that branch consults neither CPU nor any further events.** The
motivating lane is `vbpub/nyxloom/assay.toml` `[lanes.session-extract]`:
native R2, `language = "python"`, argv `… -m pytest … -n auto …`,
`budget_per_candidate = "120s"`.

The minimum merge-safe fix is one conjunct (B6-a below) plus a
regression test; the rest can be a follow-up backlog row. I state that
explicitly so round 3 is cheap.

If the controller judges that a *fail-loud* defect on a lane that has not
yet re-pinned to 6.2.0 is acceptable to merge and repair before release,
that is a legitimate call and B6 becomes a release-blocking backlog row
instead — but it is the controller's call to make with the evidence
below, not mine to wave through, because the default policy that exposes
it (`auto`) was ruled to stay *because* "B1 and B2 are repaired in this
package" (RW-49/D2), and this is an unrepaired instance of the same
class.

---

## Per-blocker verification (the round-2 checklist)

### B1 — `os._exit` default turns a real kill into a false survivor → FIXED (`802f0855`)

Sentinel `_EXIT_STATUS = None` at `assay/src/assay/liveness.py:164`; the
early return at `liveness.py:229-236` when `pytest_sessionfinish` never
ran.

*Unit (mine, verbatim from round 1).* Materialized the plugin from the
tip's own `_PLUGIN_SOURCE` and re-ran my eight-shape exit-code table with
and without `ASSAY_LIVENESS_EXIT=1`. Every shape now agrees:
pass 0/0, fail 1/1, collection-error 2/2, **conftest-`pytest_configure`
raise 3/3**, conftest import error 4/4, no-tests-collected 5/5, usage
error 4/4, **`pytest_sessionstart` raise 3/3**. The two round-1
divergences (3 → 0) are gone.

*End-to-end (mine, the round-1 project `scratchpad/p7exit`, unchanged).*
Lane `cauto` (`liveness = "auto"`) and control lane `coff`
(`liveness = false`) over the same `pkg/mod.py` whose `LtE->Lt` mutant
breaks a `pytest_configure`-time bootstrap:

| lane | liveness | killed | survived | hung | budget_exceeded |
| --- | --- | --- | --- | --- | --- |
| `cauto` | `active: true, reason: auto-pytest-argv` | **1** | 0 | 0 | 0 |
| `coff` | `active: false, reason: declared-false` | **1** | 0 | 0 | 0 |

Round 1 this pair read `survived: 1` vs `killed: 1`. **The kill is the
conftest's own `RuntimeError` (exit 3), not a hang timeout:** `hung` and
`budget_exceeded` are both empty, the candidate's wall time is under a
second, and the control lane with the mechanism entirely off produces the
identical bucket.

*RW-28 cure still intact under the restructured plugin.* Without the
plugin the leaked-non-daemon-thread suite hangs (`EXIT=124` under a
`timeout`); with it, `2 passed`, `EXIT=0`, 0.29 s. With `--cov` the
terminal coverage table, `.coverage` (53 248 bytes) and `cov.xml` all
still get written before the `os._exit`.

### B2 — idle bound derived from `call` durations only → FIXED (`07e121d9`), design accepted

Judged on merit, per the dispatch:

- **`setup`/`teardown` emit `phase`, `call` stays `test`** — correct, and
  it is what keeps A4's "one `test` event per test" contract and
  `count_test_events` honest while still giving the monitor a progress
  signal in every phase. `liveness.py:202-209`.
- **`slowest_test_s` keeps its name and meaning** — right call. It is
  still reported on `plan` (I saw `0.00032` beside `worst_gap_s = 40.52`
  on a real run), which is precisely the evidence an operator needs to
  understand why the old formula was wrong.
- **`worst_gap_s` + `pre_first_event_within_s` added on `plan`** —
  additive, verified on the wire.
- **Missing `session_start` falls back to the worst gap**
  (`liveness.py:818`) — conservative in the correct direction.
- **One `compute_liveness_calibration` replacing two parsers**
  (`liveness.py:822`) — this is the A-367/B088 lesson applied correctly;
  `LivenessCalibration` is consumed by both the `plan` event and the
  runner from a single parse.

*Calibration reproduction (mine, independent of their tests).* I built
the three baselines the dispatch named and drove the **real**
`LivenessRunner._monitor` with a fake clock, a fake `popen` whose child
emits the candidate's events on a schedule, and a flat CPU reader, in
both directions:

| shape | `worst_gap_s` | `slowest_test_s` | new bound | new bound → | old 15.0 s floor → |
| --- | --- | --- | --- | --- | --- |
| 40 s module fixture | 40.0 | 0.00037 | **120.0** | completed at t=41 s | **`LivenessHungExpired` at t=30 s** |
| 20 s slow collection | 20.0 | 0.01 | 60.0 | completed at t=21 s | completed at t=21 s (see note) |
| 30 s trailing teardown | 30.0 | 0.05 | 90.0 (pre-first 15.0) | completed at t=31 s | **`LivenessHungExpired` at t=30 s** |

Note on the 20 s collection row, which is a fidelity correction to the
REPORT's framing rather than a defect: the old 15 s floor could never
kill before **t ≈ 30 s** regardless, because `cpu_growing` defaults to
`True` until `cpu_samples` holds a sample `_HUNG_CPU_WINDOW_S` (30 s)
old. So the 20 s-collection shape was *not* fatal pre-B2; the 40 s and
30 s shapes were. Round 1's measured "killed at ~31 s" is consistent with
exactly this. The calibration change is still correct and still
necessary — it is the ≥ 30 s quiet stretches that were fatal, and those
are common.

*End-to-end (mine, the round-1 project `scratchpad/p7slow`, unchanged).*
Real `assay run` on the lane whose module-scoped fixture sleeps 40 s:

```
plan      baseline_s=42.281  budget_per_candidate_s=126.843  derived=true
          slowest_test_s=0.00032890  worst_gap_s=40.523
          expect_next_event_within_s=121.569  pre_first_event_within_s=121.569
candidate index=0  outcome_bucket=killed  tests_completed=1  elapsed_s=85.983
```

Round 1 this lane produced `hung: 1` at ~31 s, before the asserting test
body ever ran. It now produces `killed: 1` from the real assertion, with
`hung: []`. **This is the both-directions proof on the real code path.**

*Coverage (mine, bare, `nice -n 19 ionice -c 3`).*

```
src/assay/liveness.py   328 stmts   0 miss   90 branch   0 partial   100%
104 passed in 1.35s
```

from `tests/test_liveness.py + test_liveness_proc_helpers.py +
test_liveness_runner_monitor.py` alone — i.e. the claim does not depend
on the whole suite. Statement and branch counts match the REPORT exactly.
(The `_PLUGIN_SOURCE` string is not measured by `coverage` — the plugin's
own logic is covered by subprocess e2e tests, as in round 1.)

### B3 — `tests_completed` keyed on the wrong directory → FIXED (`5c1b9ef8`)

`mutation.py:2545-2549` now reads through `resolve_run_cwd(snapshot
.project_root, plan)`, threaded in beside `execute_plan` at the one lazy
import seam (`mutation.py:2159`, `2360`, `2445-2456`). Routing it
through THE one join rather than re-deriving `project_root / lane.cwd` is
the A-367-correct fix.

*Restore-and-fail (mine).* I did **not** dirty the worktree while the
controller's gate was running: I extracted `git archive 6f3aefad:assay`
into `scratchpad/p7r2b3`, confirmed the tree byte-identical to the
commit, restored the pre-fix line there, and ran the whole file.

```
E  AssertionError: assert {'killed': 0, 'survived': 0} == {'killed': 2, 'survived': 1}
FAILED tests/test_mutation_progress_budget_plan.py::
       test_tests_completed_is_read_from_the_resolved_run_cwd_on_a_lane_declaring_cwd
1 failed, 48 passed
```

Exactly the `{'killed': 0, 'survived': 0}` the commit message claims, and
**only** that test fails — the pre-existing no-`cwd` sibling passes
either way, which is why the suite could not catch this. Reverted; the
copy is again byte-identical to `6f3aefad`.

*End-to-end (mine, `scratchpad/p7cwd`, lane `cwd = "sub"`).*
`candidate` progress event now reads `tests_completed: 2` (round 1: `0`).

### B4 — the same-number additive break → DISCLOSURE ACCEPTED (`4ef3985f`, RW-49/D1)

Judging the disclosure only; the number is settled.

- `assay/docs/CONSUMERS.md:2587-2624` — a dedicated
  "B091/6.2.0: a native-R2 verdict is refused by an assay older than
  6.2.0" section under the v10 → v11 migration notes. It states the
  number stays 11, names both additive changes, prints the exact
  diagnostic, gives the rule ("verify a document with the release that
  produced it, or newer"), and — the part I did not expect and that
  raises this above a minimum-compliance disclosure — **explicitly
  corrects the older paragraph it would otherwise contradict** ("this cut
  carries exactly ONE change … is still exactly true of the v10 → v11 cut
  itself; what follows is a second, later change landing under the same
  schema number, which that paragraph was written before").
- `assay/CHANGES.md:58-72` — a `BREAKING:` bullet in `### Changed`
  carrying the same diagnostic, the RW-33/RW-49-D1 reason for not cutting
  v12, and the pointer to CONSUMERS.

*Measured (mine), all three directions:*

| verifier | document | result |
| --- | --- | --- |
| released **6.1.1** (`/home/vscode/.venv`) | `6f3aefad` native-R2 verdict (`schema_version: 11`, carries `hung`) | `assay verify: schema: unknown mutation field(s): ['hung']` — **byte-exact** to both documents |
| `6f3aefad` | its own native-R2 verdict | rc 0 |
| `6f3aefad` | the same verdict with `hung`/`liveness`/`budget_per_candidate_derived_s` stripped (a pre-`hung` document) | rc 0 |

CONSUMERS' parenthetical "(Measured with the 6.1.1 release.)" is true.

### B5 — `CHANGES.md [Unreleased]` → FIXED (`4ef3985f`)

`### Changed` (four `BREAKING:` bullets: bounded default budget, the
`unbounded` admission change, `argv_effective` moving, B4) → `### Added`
→ `### Fixed`. The self-contradicting "**Not yet shipped in this
entry:** the active liveness-monitoring loop and the new `hung` outcome
bucket" sentence is gone (`grep` finds no occurrence). The B2 repair is
itself disclosed at `CHANGES.md:187-203` with the old formula, the
measured 0.00037 s figure and the new one.

### S2 / S4 / S7 / S8 / S9 / S10 → FOLDED (`ef247935`); pyflakes clean

- **S2** `argv_invokes_pytest` (`liveness.py:300-332`) is now positional:
  `tokens[0] == "pytest"` or `tokens[0].endswith("/pytest")`, plus the
  adjacent `-m pytest` pair anywhere. `["make", "pytest"]` and
  `["tox", "-e", "pytest"]` no longer qualify. The test that asserted the
  old permissive behaviour moved to the false-cases list in the same
  commit — the right call: the measured evidence beat the docstring.
- **S4** `config.py:3022` now names the accepted TOML spellings (bare
  booleans or the string `"auto"`) instead of the internal normalized
  vocabulary.
- **S7** `mutation_pct`'s docstring (`mutation.py:3353-3371`) enumerates
  `hung`.
- **S8** the backlog's B091 gate-history sentence is corrected to what
  actually happened, with `95d02f50`/`ee24ced6` added.
- **S9** `materialize_liveness_plugin` (`liveness.py:287-297`) raises a
  typed `AssayError(Outcome.ERROR, ReasonCode.OUTPUT_WRITE_FAILED)`
  naming the directory and the `liveness = false` way out, instead of a
  bare `OSError` traceback.
- **S10** CONSUMERS `2091-2096` discloses that the `-p` pair rides on an
  `R0/R1/R2/R3` lane's R3 canary probes and is inert there.
- `python3 -m pyflakes src/assay tests` → the only report is the
  deliberate `tests/fixtures/mutation/python/broken.py` syntax fixture.

Targeted suite on the tip (in the clean extracted copy, bare, niced):
`test_cli_run.py + test_liveness.py + test_liveness_proc_helpers.py +
test_liveness_runner_monitor.py + test_mutation_progress_budget_plan.py +
test_verify_hung_bucket.py` → **200 passed**. The REPORT's named B1
command (`-k configure_time_raise`) passes.

### Round-1 probes re-run verbatim

| probe | round 1 | round 2 (`6f3aefad`) |
| --- | --- | --- |
| eight-shape plugin exit-code table | 2 divergences (3 → 0) | all 8 identical |
| `p7exit` `cauto` vs `coff` | `survived: 1` vs `killed: 1` | `killed: 1` both |
| `p7slow` slow-fixture lane | `hung: 1` at ~31 s | `killed: 1`, `hung: []`, bound 121.6 s |
| `p7cwd` `cwd = "sub"` lane | `tests_completed: 0` | `tests_completed: 2` |
| `p7probe` `nonpytest` lane | WARN, `active: false` | identical WARN, `reason: argv-does-not-invoke-pytest` |
| `p7probe` `pyauto` control | liveness active | identical |
| `--resume --rejudge-outcome survived` | `rejudged_total: 4` | `rejudged_total: 4`, `resumed_total: 0` |
| unknown `--rejudge` id | `ERROR/UNREADABLE_ARTIFACT` | unchanged (S3, deferred — see N5) |
| 6.1.1 `verify` on a native-R2 verdict | refuses | refuses, now documented (B4) |

---

## Blockers

### B6 — an events file written by more than one pytest process (xdist) arms the 30 s hung-grace at the FIRST `session_finish`, killing healthy candidates

**Where.**

- `assay/src/assay/liveness.py:1214-1217` — the decision:

  ```python
  hung = (idle_for >= bound and not cpu_growing) or (
      session_finish_at is not None
      and (now - session_finish_at) >= _HUNG_SESSION_FINISH_GRACE_S
  )
  ```

  The second disjunct consults **neither CPU nor any subsequent event**.
- `liveness.py:1158-1159` — `session_finish_at` is armed the first time
  `_read_events_progress` reports `saw_session_finish`.
- `liveness.py:909-914` — `saw_session_finish` is `True` for **any**
  record whose `event` is `session_finish`, with no notion of which
  process wrote it.
- `liveness.py:511` — `_HUNG_SESSION_FINISH_GRACE_S = 30.0`, fixed, never
  calibrated (unchanged by the B2 repair, deliberately).
- `liveness.py:168-180` — the plugin's `_append` writes to the single
  path in `ASSAY_LIVENESS_EVENTS`, inherited by every child of the
  candidate; records carry no process identity.

**Why more than one process writes that file.** Liveness injects
`-p assay_liveness_plugin` into `argv_appended` plus a `PYTHONPATH`
prepend, and `LivenessRunner.__call__` stamps `ASSAY_LIVENESS_EVENTS` /
`ASSAY_LIVENESS_EXIT` into the candidate's environment
(`liveness.py:1071-1072`). pytest-xdist workers are spawned from the
controller's own argv and environment, so **every worker loads the
plugin and appends to the same file**, and every worker runs a full
session with its own `pytest_configure` and `pytest_sessionfinish`.

**Reproduced (mine), stock pytest-xdist 3.8.0, the identical injection
shape assay uses** (`-p assay_liveness_plugin`, `PYTHONPATH` prepended,
one `ASSAY_LIVENESS_EVENTS` path), on a 4-test project with `-n 2`:

```
total records: 30   Counter({'phase': 16, 'test': 8,
                             'session_start': 3, 'session_finish': 3})
   0.000 session_start
   1.792 session_start
   1.803 session_start
   2.938 session_finish exitstatus=0      <- worker A
   2.940 session_finish exitstatus=0      <- worker B
   3.788 session_finish exitstatus=0      <- controller
```

Three `session_start`, three `session_finish`, and **8 `test` records for
4 tests** (each test is reported by its worker and again by the
controller).

**The kill, driven through the real `_monitor`** (fake clock, fake
`popen`, CPU monotonically *growing*, calibrated bound a generous
600 s; the child emits a `test` event every 2 s from t=6 s to t=200 s and
a worker's `session_finish` at t=5 s):

```
LivenessHungExpired at t=35s -- candidate was still emitting a test event
every 2s and burning CPU
```

The candidate is `os.killpg(SIGKILL)`ed and bucketed `hung`. One `hung`
candidate makes the whole lane
`Outcome.BUDGET_EXCEEDED / ReasonCode.CANDIDATE_HUNG`
(`mutation.py:3482-3492`) — the lane can never pass.

**It is reachable by a real estate lane at the default policy.**
`vbpub/nyxloom/assay.toml` `[lanes.session-extract]`:
`rigor = ["R0","R1","R2","R3"]`, `[lanes.session-extract.judge]
language = "python"`, a **native** `[lanes.session-extract.judge.mutation]`
block, and

```toml
argv = ["/opt/tester-venv/bin/python", "-m", "pytest", …18 files…,
        "-n", "auto", "-q", "--cov=src/nyxloom", …]
budget_per_candidate = "120s"
```

`argv_invokes_pytest` matches on the adjacent `-m pytest` pair, so
liveness is **active** for that lane under `auto` (RW-49/D2). The lane's
own comment records "the argv already runs pytest -n auto (up to 8 xdist
workers on this host)". With 8 workers over 18 test files and a 120 s
per-candidate budget, a >30 s gap between the first worker's finish and
the controller's is an ordinary tail imbalance, not an exotic one — and
every candidate that hits it is destroyed.

**Two further consequences of the same root cause, same evidence:**

1. `count_test_events` (`liveness.py:735`) counts every `test` record in
   the file, so `candidate.tests_completed` is **double-counted** under
   xdist (8 for 4 tests above). CONSUMERS documents this field as the
   number of tests the candidate completed; the double count is the same
   class of "reported a measurement it did not make" defect as round-1
   B3.
2. `baseline_event_gaps` (`liveness.py:793-819`) treats the file as one
   chronological stream. Under xdist it is N interleaved streams, so
   `worst_gap_s` is the worst gap of the **merged** timeline, which is
   systematically smaller than any single process's worst gap → a
   tighter `expect_next_event_within_s` than B2's ruling intends, in the
   false-`hung` direction. `leading_gap_s` is likewise measured between
   records that may come from two different processes.

**Prescription.**

- **B6-a (the minimum, and sufficient to stop the kill).** Make the
  grace branch require that nothing else has happened:

  ```python
  hung = (idle_for >= bound and not cpu_growing) or (
      session_finish_at is not None
      and (now - session_finish_at) >= _HUNG_SESSION_FINISH_GRACE_S
      and idle_for >= _HUNG_SESSION_FINISH_GRACE_S
  )
  ```

  This preserves the branch's stated purpose exactly — RW-33's
  thread-join deadlock emits no further events and writes no further
  output, so `idle_for` grows and the kill still fires — while a
  candidate that is still reporting tests can no longer be killed by it.
  One line, plus a regression test: an events file carrying an early
  `session_finish` followed by continuing `test` records must complete,
  not raise.
- **B6-b (the root cause).** Stamp `os.getpid()` (and, when set,
  `PYTEST_XDIST_WORKER`) on every record in `_append`; have
  `_read_events_progress` treat only a `session_finish` whose `pid`
  equals the candidate's own `proc.pid` as *the* session finish, have
  `count_test_events` count only that pid's `test` records, and have
  `baseline_event_gaps` compute gaps per-pid and take the worst across
  processes. Records without `pid` (an older baseline file) keep today's
  behaviour, so nothing pre-existing breaks.
- **B6-c (disclosure, whatever is done about the code).** CONSUMERS'
  liveness section says nothing about xdist. It should state what the
  mechanism does on a lane that fans out, and — if B6-b is deferred — a
  WARN when liveness is active and the argv carries `-n`/`--numprocesses`
  would be honest.

**What I could NOT break, and should be said plainly:** `os._exit` under
xdist does not corrupt the kill signal. With `ASSAY_LIVENESS_EXIT=1` and
`-n 2` I measured exit 0 on an all-passing run and exit 1 with one
failing test, and the summary lines were correct in both. So B6 is a
false-`hung` defect, never a false survivor.

---

## Non-blocking (N)

- **N1 — the four deferred S-items have no backlog rows.** The dispatch
  asked me to verify that S1 (liveness writes into the judged tree with
  no ignore-guard and never cleans up), S3 (`MutationStateError` reason
  mapping), S5 (monitor hot-loop cost and the unbounded `cpu_samples`
  list) and S6 (`--rejudge-outcome` help text from `MUTATION_BUCKETS`)
  are each filed as a row in `assay/nyxloom-trove/4-backlog.md`.
  **None of the four is.** The only backlog change in the repair range is
  the S8 correction to B091's own gate history
  (`4-backlog.md:9396-9410`); no new `B09x` row exists, and grepping for
  the four topics finds nothing. Their reasons ARE written up in the
  REPORT ("Deferred S-items, with reasons", and the S1 entry itself says
  "Should become its own backlog row") — the reasons are good; only the
  filing is missing. Naming it per the dispatch so the controller adds
  them.
- **N2 — the region after `session_finish` is still unmeasured, and the
  30 s grace over it is still a fixed constant.** RW-49/D3's "trailing
  gap" is implemented as *last report → `session_finish`* (covered, and
  `test_calibration_covers_the_trailing_session_teardown_gap` pins it);
  the coordinator's dispatch worded it as "last event → **exit**", which
  is a wider region the calibration cannot see, because the plugin's last
  record is `session_finish` and everything after it (terminal summary,
  other plugins' `pytest_unconfigure`, pytest-cov's report) is bounded
  only by the fixed 30 s. I measured that region on assay's own test
  files: **1.01 s without `--cov`, 0.99 s with `--cov=assay
  --cov-report=term`** — ample headroom, so this is a documented residual
  risk rather than a live defect, and I am *not* raising it as a blocker.
  (My first measurement of it read 12–18 s; that was the B6 contamination
  — a nested pytest run's `session_finish` in my own harness — and the
  investigation of that artifact is what found B6. Recorded here so the
  number is not later mistaken for a real one.) Cheap hardening if the
  controller wants the region measured rather than assumed: have the
  plugin append a `session_exit` record from `pytest_unconfigure` BEFORE
  the `ASSAY_LIVENESS_EXIT` gate (so the baseline records it too) and
  fold that gap into `worst_gap_s`.
- **N3 — `mutation_pct`'s repaired enumeration still omits `crashed`.**
  `mutation.py:3356-3363` now names `budget_exceeded`, `hung`,
  `equivalent` and `discarded` as excluded from the score. `crashed` is
  excluded too (the denominator is `killed + survived`) and is not
  enumerated — the identical defect S7 fixed, one bucket over.
- **N4 — S2's positional rule silently drops liveness for
  `uv run pytest` / `poetry run pytest` / `hatch run pytest`.** The fix
  is right and the direction is fail-safe (no injection, one WARN), but
  those wrappers are common real spellings, and on such a lane an
  explicit `liveness = true` is now REFUSED at load. No consumer can be
  broken by this today (the key ships in this release), so it is
  informational — but CONSUMERS' rule statement should name the wrapper
  case, or `argv_invokes_pytest` should also accept a `pytest` token
  immediately following a `run` subcommand.
- **N5 — S3 reproduces unchanged** (deferred by RW-53, and the deferral
  reason in the REPORT is sound): an unknown `--rejudge` id still refuses
  with `ERROR/UNREADABLE_ARTIFACT` (exit 2) rather than a config-refusal
  code. Verified on the real CLI against the round-1 state store.
- **N6 — the REPORT's mutant table over-credits the 20 s slow-collection
  case.** See the note under B2: pre-B2, the 15 s floor could not kill
  before ~30 s because of the 30 s CPU window, so a 20 s collection gap
  alone was survivable. The table's M-mutants are all genuinely caught
  (I did not re-run all ten; I reproduced the three calibration shapes
  the dispatch named, in both directions) — this is about the narrative,
  not the tests.

---

## Decision asks for the controller

1. **B6's disposition.** Merge-blocking (my reading, because it is
   reachable by `nyxloom`'s `session-extract` lane at the default policy
   and silently destroys that lane's R2 measurement), or merged with
   B6-a only and B6-b/B6-c filed as a release-blocking backlog row? B6-a
   is one conjunct plus one regression test.
2. **RW-49/D3's scope.** The implementer covered "trailing gap" as
   *last report → `session_finish`*; the dispatch's wording was
   *last event → exit*. I judged the implementation to satisfy the
   ruling as written and recorded the wider region as N2 (measured at
   ~1 s). Confirm that reading, or rule that D3 meant the wider region
   and N2 becomes work.
3. **N1** — add the four deferred-S-item backlog rows.

## Claims I could not verify

- The full 10-mutant table of session 10. I reproduced the three
  calibration shapes the dispatch named, in both directions, on the real
  `_monitor`, and independently reproduced the 100 % line + branch
  figure; I did not re-plant M1–M10.
- That `nyxloom`'s `session-extract` lane *would* in practice see a
  >30 s gap between its first worker's finish and the controller's. I
  proved the mechanism and the lane's exposure to it; I did not run that
  lane (it belongs to another package and the host is at its cap).
- The survivor table remains **N/A by design** and I accept that reading,
  unchanged from round 1: `assay/assay.toml` declares one lane,
  `rigor = ["R0"]`, with a header citing A-046/A-133 — "This file stays
  R0-only PERMANENTLY". There is no assay-on-itself R2 lane for a
  survivor table to come from.

## Gate

The controller's `tester-unified` run on `6f3aefad` (log
`scratchpad/p7-gate6.log`) is **GREEN**. I armed an untracked
`until grep -q "lane 'tester-unified' exit"` watcher and read the verdict
in a separate step:

```
ASSAY_GATE_PHASE=pyflakes-clean
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

Cross-checked against the authoritative record rather than the log alone
(round 1's lesson, where a duplicate launch overwrote a green run's
transcript) — `assay/.run-gate/history.json` `lanes["tester-unified"]
.latest`:

```json
{"commit": "6f3aefad4ed10d05901c03089df9b65891b7cb3b", "dirty": false,
 "exit_code": 0, "outcome": "pass", "started_at": "2026-09-12T21:08:15Z",
 "duration_seconds": 1691.635, "history_eligible": true}
```

The two agree: the green run is on the reviewed tip, with a clean tree.
**I did not launch a second `tester-unified` container**, did not remove
any container, and made no commit and no edit to any tracked file in the
worktree (`git status --porcelain` is empty) — the B3 restore-and-fail
was done on a `git archive` extract in the scratchpad precisely so the
running measurement could not be voided.

## Host-load compliance

All my probes were bare pytest / bare `assay` under `nice -n 19
ionice -c 3`, serial, targeted files, in the scratchpad. No container was
created or removed. PSI was checked before each batch
(`/proc/pressure/memory full avg10` stayed ≤ 0.2 throughout; the one
`some avg10 = 5.6` reading was during the gate's own wheel phase and I
paused rather than adding load). Total probe wall time ≈ 6 min of
single-core work.
