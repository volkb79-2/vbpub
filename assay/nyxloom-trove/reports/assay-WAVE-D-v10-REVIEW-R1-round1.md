# assay Wave D phase 1 — adversarial review R-1, round 1

**Reviewer:** R-1 (fresh Opus, inherits nothing from the three implementer
generations).
**Subject:** `93188912` (`backlog(assay): B024's gate wiring is blocked …`).
**Base:** `a4a865da`. **Branch tip above the subject:** `b90ca598` — verified
docs-only (`assay-WAVE-D-v10-BRIEF-3.md`, `…-LOG.md`); nothing under `src/`,
`tests/`, `gate/` or `docs/` moves in it, so the judgment below is the
judgment of the branch.
**Method:** blind pass on `git diff a4a865da...93188912` + the tree, the ten
backlog contracts, the wave prompt's DA-D1..DA-D16 and Consumer coupling
facts, and the controller log's DA-R1..DA-R7 — then, and only then, the
implementers' LOG/REPORT/BRIEF-1/2.
**Every claim below carries its own transcript.** Probes were built from the
lane grammar in `docs/CONSUMERS.md`, not from the implementers' fixtures, and
run through an editable install of the tip (`scratchpad/r1venv2`) alongside an
editable install of the base `a4a865da` (`scratchpad/basevenv`) so that
"changed" and "already true" are distinguishable.

**Host-load incident and throttle (method note, per the controller).** My
hollow-test sweep launched **seven whole-suite runs concurrently, each with
`pytest -n 4`**, each spawning venv/pip work, on top of the implementer's gate
container. On an 8-core host that drove the 1-minute load to ~82-87 and CPU
PSI to ~88%, degrading a production game server co-resident on the box. The
controller intervened three times: (1) a throttle — no `-n`/xdist, `nice -n 19
ionice -c 3`, targeted test files over whole suites, one `tester-unified`
container at a time, no concurrent build+suite; (2) `SIGSTOP` on six of the
seven runs, released one at a time by a detached serializer
(`…/scratchpad/r1-serialize.log`), which dropped load to ~39 in a minute;
(3) when the paused runs' memory began swapping out at ~20 MB/s and pushed IO
pressure to 60%, **`mut2`..`mut7` were killed outright** and only `mut1` was
allowed to finish. I relaunched, killed and re-ran nothing on my own
initiative.

**What that means for the evidence below, stated exactly:**

* **`mut1` is a completed whole-suite run** (`MUTEXIT=1`, 13m41s serialized).
* **`mut2`..`mut7` were killed with no result** and were **re-run one at a
  time, strictly serially, against only the test files that exercise the
  mutated module**, `nice -n 19 ionice -c 3`, no xdist — after the
  implementer's gate container had cleared. **The evidence for each of those
  six mutants is its targeted rerun**, not the killed whole-suite run.
* **Harness caveat, found by `mut1` and worth recording:** the mutation copies
  are `cp -r` of the worktree's `assay/` directory into the scratchpad, and
  `tests/test_python_qualification.py`, `tests/test_runner_snapshot_selection.py`
  and `tests/test_distribution_build_release.py` compute
  `REPO_ROOT = PROJECT_ROOT.parent` and shell out to `git -C` against it
  (`test_python_qualification.py:42`, `test_runner_snapshot_selection.py:805`).
  In a copy that parent is not a repository, so those three modules contribute
  **11 failures and 13 errors on every copy regardless of the mutation**. None
  of them is in any mutated module's blast radius, and the targeted reruns
  avoid them entirely — but any whole-suite number from a copy must be read
  with that constant subtracted.

My own registered-gate run had already completed (`GATE_EXIT=0`) before the
first intervention, and its worktree was removed. **The mistake was mine**, and
naming it precisely: a mutation sweep never needed the whole suite per mutant —
the test files that exercise the mutated module are sufficient, faster, and
are what I should have run from the start. **Standing rule from here (now in
the wave prompt's RULES): never more than one `pytest` invocation alive at a
time, no `-n`/xdist, always `nice -n 19 ionice -c 3`, targeted files for
mutation probes, whole-suite runs at most once per checkpoint, and check
`docker ps | grep tester-unified` is empty before launching a gate.** This
bounds what a fix-verification round can re-run and is carried into the
self-compaction prompt below.

---

## VERDICT: **NOT ACCEPT** — 2 blockers (B053, B029).

The engineering is, with two exceptions, genuinely good and genuinely
measured: B049 works at all five reserved-artifact reads through the CLI with
non-vacuous controls; B054's per-file split works on both sides of the judged
set; B028's direct-R0 fix is real and red-provable against the base; B029's
"does not reproduce" is reproducible; B024's escape hatch is correctly taken
against three checks I re-ran myself; the registered gate is green on the
subject commit; and a 7-mutant hollow-test sweep found B049's, B054's,
B028's and DA-R4's guards all real and surgical. The blockers are not about
any of that. They are:

1. **B053's central claim is false as shipped** — two refusal sites still
   print nothing, and the branch asserts none remain in three places
   including `CHANGES.md`.
2. **B029's code change ships with zero executable coverage** — the one test
   named after it survives the deletion of both halves of what it names.

---

## BLOCKERS

### BLOCKER 1 — Two refusal sites still print nothing, and the branch states in three places that none remain.

**The claim under test.** DA-R3, verbatim: *"R-1's stated push ('every refusal
reachable through `assay run` prints exactly one line') must hold without
qualification before R-1 is dispatched."* The branch asserts it does, three
times:

* `assay/CHANGES.md:32` — **"'Exactly one line' now holds for EVERY refusal
  reachable through `assay run`, with no exceptions."**
* `assay/tests/test_refusal_announcement.py:384` — *"'Every refusal reachable
  through `assay run` prints exactly one line' then holds without
  qualification."*
* `assay/src/assay/runner.py:3813-3819` — the DA-R3 comment, same claim.

**Measured: it does not hold.** The six sites DA-R3 enumerated are the
**pre-run** guards. The **post-command** dirt/HEAD guards — a different pair
of sites, on both dispatch paths — refuse from a bare `(status, reason_code)`
literal exactly as the six did, and emit nothing.

Probe repo `scratchpad/probe53b`: an R0-only lane and an R0+R1 lane whose
command writes `leftover.txt` (untracked) or makes an empty commit. Tree clean
before the run. Emitter lines counted as `grep -cE '^assay: [A-Z_]+/[A-Z_]+: '`
over stderr with the judge-provenance notice filtered out:

```
##### post-command DIRTY_TREE, direct R0          [emitter lines: 0]
##### post-command DIRTY_TREE, snapshot (R1)      [emitter lines: 0]
##### post-command HEAD_CHANGED, direct R0        [emitter lines: 0]

6 NO_MEASUREMENT [('R0','NO_MEASUREMENT','DIRTY_TREE')]
7 NO_MEASUREMENT [('R0','PASS',None), ('R1','NO_MEASUREMENT','DIRTY_TREE')]
8 NO_MEASUREMENT [('R0','NO_MEASUREMENT','HEAD_CHANGED')]
```

Nothing on stderr but the three-line summary. The same silence reproduced
incidentally three more times while I was building unrelated probes
(`probe49` before `cov/` was gitignored; `probe29` before `__pycache__/` was)
— which is the point: **this is the DIRTY_TREE an operator is most likely to
meet and least able to explain**, because they ran it on a tree they know was
clean and the dirt is their own command's. It is strictly more confusing than
the pre-run case DA-R3 fixed, and the fact needed to act on it (which paths,
which two revisions) is discarded at the guard.

**The sites.**

* `src/assay/runner.py:4618-4642` (`_finish_direct_r0_lane`) —
  `git.dirty_paths(...)` is evaluated for truthiness only, `git.head_rev(...)`
  compared and dropped, and the terminal `assemble_verdict` carries a bare
  `NO_MEASUREMENT`/`post_run_reason` claim. The function takes no
  `diagnostics` parameter at all, so it cannot announce even if it wanted to
  — note that this function is **new in this branch** (`b90ca598`'s parent
  extracted it out of `run_lane` for B028), so the DA-R3 pass could have
  threaded the stream into it at zero extra cost and did not.
* `src/assay/runner.py:2340-2344` (`_execute_snapshot_unit`) → consumed at
  `src/assay/runner.py:2809-2827` (`unit.post_reason` → every declared level's
  claim). Same shape, same discard.

For contrast, the sibling failure in the same function — `unit.profile_error`
— *is* announced, at `runner.py:2886`, using exactly the pattern this fix
needs.

**Prescription (concrete, mirrors machinery already in the file).**

1. `_execute_snapshot_unit`: carry the facts out beside `post_reason` on
   `_PreparedUnit` — `post_dirty: tuple[str, ...]` and
   `post_observed_head: str | None` — exactly as `profile_error` /
   `equivalence_error` are already carried. Then in `_run_prepared_lane` at
   `runner.py:2809`, where `diagnostics` is already in scope, compose the
   `AssayError` and call `announce_refusal`.
2. `_finish_direct_r0_lane`: give it a `diagnostics: "TextIO | None"`
   parameter, keep `dirty`/`observed_head` in locals instead of discarding
   them, and announce before the terminal `assemble_verdict`. This is a
   one-line change at one call site: the function has **exactly one** caller
   (`runner.py:4514`), and `run_lane` already threads `diagnostics` through
   nine other call sites in the same function body.
3. **The message must name the cause, not just the state.** The pre-run
   sentence ("commit or stash, then re-run") is *wrong* here: the operator did
   not leave the dirt, the lane's command did. Say so — "the lane's own
   command left N uncommitted file(s) …: X, Y" / "the lane's own command moved
   HEAD from `<pre>` to `<post>`; a command that commits makes the verdict's
   commit label untrue" — since that is A-175/A-178's whole reason for the
   guard existing.
4. Two CLI-level tests in `tests/test_refusal_announcement.py`, one per
   dispatch path, in the shape of `test_a_dirty_tree_names_the_uncommitted_
   files_on_a_direct_r0_lane` (`len(_refusal_lines(...)) == 1` + the path is
   named). Both are reachable through `assay run` with a two-line shell
   command — no test double needed; my probes are transcript above.
5. Then, and only then, the three claims above become true. If the controller
   instead rules these two sites out of scope for phase 1, **all three claims
   must be narrowed in the same commit** — "every refusal that names a cause
   before the lane's command runs" — because a `CHANGES.md` line that says
   "with no exceptions" is a consumer-facing statement, and this one is
   falsifiable in three commands.

### BLOCKER 2 — `test_the_legacy_standalone_canary_forwards_the_same_two_parameters` is hollow: it survives the removal of the very forwarding it is named for.

`assay/tests/test_r3_canary_sees_infrastructure.py:139-163` is the ONLY test
of the code B029/A-416 actually changed. It asserts three things, all of them
about `inspect.signature(...)` and `__doc__`: that the two parameter names
exist, that they default to `None`, and that `execute_command`'s docstring no
longer contains the string `"accepts no"`. It asserts **nothing about the
values reaching `resolve_command_plan`.**

That matters more than usual here, because I measured that **no CLI run
touches this code at all.** Instrumenting a full R3 CLI run over a real
`derived:` fact (`scratchpad/probe29`, `ciu.global.toml` carrying
`[deploy] cgroup_parent`):

```
r3: PASS (exit 0)
execute_command calls: 0 []
run_isolated_canary: 1   run_python_canary: 0
```

So the shipped path is `run_isolated_canary`, `execute_command` is never
entered, and the sibling test
(`test_an_r3_lane_with_a_resolvable_derived_fact_judges_its_canary`) — which
is a good test — exercises none of B029's diff. The signature check is the
whole guard, and a signature check cannot fail for a dropped forward.

**Mutation evidence — decisive, and already in.** `m1` = the tip with the two
forwards deleted from `runner.execute_command`'s `resolve_command_plan` call
(`runner.py:884-885`). Whole suite, completed:

```
MUTEXIT=1
11 failed, 3956 passed, 18 skipped, 13 errors in 821.95s
```

Every one of those 24 is in `tests/test_python_qualification.py`,
`tests/test_runner_snapshot_selection.py` or
`tests/test_distribution_build_release.py` — the three modules that
`git -C REPO_ROOT` against `PROJECT_ROOT.parent` and therefore fail on **any**
scratchpad copy, mutated or not (see the harness caveat in the method note).
**Not one canary, infrastructure or R3 test moved.**
`test_r3_canary_sees_infrastructure.py` passed with the forwarding gone.

The targeted rerun settles it beyond the copy-harness noise — the five
canary/R3 modules, unmutated baseline versus `m1`, back to back on the same
harness:

```
base-canary exit=0 :: 25 passed in 58.14s   (unmutated tip)
m1-canary   exit=0 :: 25 passed in 41.43s   (runner.execute_command's forwards deleted)
m2-canary   exit=0 :: 25 passed in 42.58s   (canary._run_pipeline's forwards deleted)
```

Same 25 tests, same result, at **both** ends of the threading. The guard does
not guard — and neither half of B029's diff is covered by anything.

**The counter-argument, stated fairly, since the controller may want to
downgrade this.** DA-R6 asked for "the CLI-driven test as a regression guard
(R3 claim PASS/FAIL with a derived fact)" — and *that* test exists, is real,
and is good. So DA-R6's letter is arguably met. What is not met is anything
weaker than this: **every line B029 actually changed ships with zero
executable coverage, on a public API, and the one test named after it cannot
fail.** That is the same defect class as the wave's own B056 finding (a test
whose stated measurement a re-run refutes), committed one wave later. I grade
it a blocker because the fix is ~15 lines and the REPORT already says the red
proof is available; the controller may reasonably downgrade it to a
should-fix, and I will not re-litigate that.

**Prescription.** Replace the signature assertions with a value assertion
that a dropped forward cannot survive. The cheapest honest shape needs no new
harness: call `runner.execute_command` (and `canary.run_python_canary`)
directly on a lane declaring `derived:` with a real `infrastructure_source`,
and assert the resolved fact is present in the returned `CommandResult`'s
`env_effective` / that the command observed it — the REPORT itself says this
is red before `81228b25` ("that IS red before this commit, and was left out").
The implementer disclosed this gap honestly in the REPORT
(`…-REPORT.md:1002-1016`) but the disclosure does not make the guard work: a
regression guard that cannot go red is a comment, and DA-R6's own words are
"land the CLI-driven test as a regression guard".

---

## SHOULD-FIX

### SF-1 — B028: a lane-wide `LANE_TIMEOUT` raised *before* the new catch still writes no verdict, on **both** dispatch paths.

The implementer's own push list asks a reviewer to "construct a budget tight
enough to expire inside `git.repo_top` and see what happens". I did.
`scratchpad/probe28` with `budget = "0.001s"`:

```
### r0slow   exit=4   assay: BUDGET_EXCEEDED/LANE_TIMEOUT: the lane-wide deadline expired
  *** NO VERDICT FILE ***
### r1slow   exit=4   ... same ...
  *** NO VERDICT FILE ***
### base build (a4a865da): identical — no verdict file on either
```

Escape point, captured from the traceback:
`git.py:418 _resolve_repo` → `git.py:268 _run_bounded` →
`git.py:198 _sample_remaining` → `runner.py:219 remaining`
(`LaneDeadline.remaining`, `runner.py:216-224`) — i.e. `git.repo_top` inside
`run_lane`, upstream of both the new direct-R0 `try` (which opens at
`runner.py:4504-4505`) and `_run_higher_rigor_lane`'s outer catch. It reaches
`cli.main`'s handler, which prints and returns the exit code without writing
anything.

DA-D10's *letter* was met (one catch per entry point, in the two places named),
so this is a should-fix and not a blocker. But `CHANGES.md`'s headline —
"A lane that runs out of its `budget` now writes its verdict." — is broader
than what ships, even though the sentence after it is correctly scoped.
Prescription, pick one: (a) one `except AssayError` in `cli._run_reserved`
around the `runner.run_lane` call, scoped to `ReasonCode.LANE_TIMEOUT`,
building the verdict through `runner.refuse_lane` and writing it — the exact
handler that already exists eleven lines up for the attestation timeout
(`cli.py:695-716`), and one handler covers both dispatch paths; or (b) narrow
the `CHANGES.md` headline to "a lane whose **command** runs out of time" and
file the residue as a backlog entry with this transcript.

### SF-2 — a third bare, silent refusal: `except OSError` → `refuse_all(ERROR, GIT_FAILED)`.

`src/assay/runner.py:3944-3950`. Same class as BLOCKER 1 and not in DA-R3's
list of five, so not covered by that ruling — but it is a `GIT_FAILED` the
operator gets with no sentence, from an `OSError` that has a perfectly good
`str()`. One line: compose `AssayError(f"snapshot preparation or cleanup
failed: {exc}", ERROR, GIT_FAILED)` and announce it. (`except RuntimeError`
just below deliberately re-raises when there is no outcome, which is right.)

### SF-3 — `_report_probe_refusal` is a second spelling of the emitter's line.

`src/assay/runner.py:447-451` prints
`f"assay: {status.value}/{reason_code.value}: lane …"` by hand. It is
byte-compatible with `announce_refusal`'s format today, and it is exactly the
"two spellings of one line is how the two drift apart" hazard
`announce_refusal`'s own docstring names as the reason `cli.py`'s two prints
were refactored onto it. Fold it on too (compose the `AssayError`, call the
emitter, keep the `probe stderr:` / `probe stdout:` tails as the indented
context lines — the same shape B033's whole-target site already uses at
`runner.py:3117-3125`).

### SF-4 — nit: a leaked descriptor on B049's new raise path.

`src/assay/mutation.py:1642` — `equivalence_reservation.consume()` can now
raise, and the two `close()` calls at `mutation.py:1648-1651` are skipped when
it does, so `kill_signal_reservation`'s parent fd is never closed. Bounded at
one fd per run (the raise is fatal to the whole R2 claim), so it is a nit, not
a leak. A `try/finally` around the two reads fixes it.

### SF-5 — the `contradictory_branch_lines` carry through `statement_attribution` is unreachable by any real artifact.

`src/assay/statement_attribution.py:229` and `:346`. Both rebuilds are behind
`if file_cov.blocks is None: continue` — only `go-cover` populates `blocks`,
and only `coverage-istanbul-json` populates `contradictory_branch_lines`, so
no artifact can reach either carry today. The carry is correct and cheap
insurance, and the docstring's reasoning is right; it just should not be read
as covered.

**Measured:** mutation `m5` deletes both carries; the statement-attribution and
istanbul-contradictory modules stay green (`41 passed`, identical to the
unmutated baseline). Nothing in the suite can tell the difference, which is
the definition of unreachable — so treat these two lines as insurance, not as
covered code.

---

## WHAT I VERIFIED AND FOUND SOUND

### 1. B049 / DA-D1 — all five reserved-artifact reads, through the CLI, with non-vacuous controls.

Each probe is a 6-15 line `rm -rf <dir> && mkdir <dir> && write` shell tool,
no Vitest. All five refuse `ERROR`/`UNREADABLE_ARTIFACT` and announce exactly
once:

| read | probe | result |
|---|---|---|
| coverage (`_execute_snapshot_unit`, `runner.py:2351`) | `probe49` | `[('R0','PASS'),('R1','ERROR','UNREADABLE_ARTIFACT')]`, 1 emitter line |
| SQL R2 baseline `equivalence_artifact` (`runner.py:2366`) | `probe49sql` | `[('R0','PASS'),('R2','ERROR','UNREADABLE_ARTIFACT')]`, 1 line |
| ingested mutation report (`runner.py:2396`) | `probe49ing` | `[('R0','PASS'),('R2','ERROR','UNREADABLE_ARTIFACT')]`, 1 line |
| **per-mutant equivalence** (`mutation.py:1642`) | `probe49sql`, gate replaces the dir only when the schema is mutated | `[('R0','PASS'),('R2','ERROR','UNREADABLE_ARTIFACT')]` — **not** `crashed`, **not** `EXEC_FAILED` |
| **kill signal** (`mutation.py:1192` via `_read_kill_signal`) | `probe49sql`, kill signal moved to its own `.ks/` so the equivalence read succeeds first | `[('R0','PASS'),('R2','ERROR','UNREADABLE_ARTIFACT')]` naming `'.ks/kill-signal.txt'` |

The two mutation.py sites really propagate: `equivalence_reservation.consume()`
is deliberately **not** inside a `try` (`mutation.py:1642`), and
`_read_kill_signal`'s error is captured and re-raised at `mutation.py:1653`
after the dirt check — both reach `_run_prepared_lane`'s R2 orchestration-fault
catch (`runner.py:3286-3300`), which carries `exc.outcome`/`exc.reason_code`
verbatim. **Controls are not vacuous:** the same lanes with a
non-directory-replacing tool return `PASS` with a real R1 payload
(`probe49`: `judgment.r1` at 100.0%) and a real R2 `PASS` (`probe49sql`
control), and the ingested control gets *past* `consume()` (it refuses later,
on `framework`, which is proof the bytes were read).

**Note on the shipped test's breadth (not a finding).** DA-D1 asked for the
same rule "at ALL THREE sites the entry names".
`tests/test_safeio_replaced_output_directory.py` proves the reservation
contract at unit level, the **coverage** read end to end through `run_lane`,
and the **kill-signal** read through `_read_kill_signal`. The SQL R2 baseline
`equivalence_artifact` and the ingested mutation-report reads have no
end-to-end test of their own; they are the same `consume()` call, and my four
CLI transcripts above cover them, so this is defensible — but it is what a
future refactor of `_execute_snapshot_unit`'s three `try/except AssayError`
blocks would not catch. The regression tool is a 15-line Python fake, no
Vitest, as DA-D1 required (`tests/…:41-46`).

`safeio.py:285-333` is the whole of the fix and it is the right shape:
`os.fstat` on the already-held descriptor, no re-open by name, no new reason
code, no schema change — DA-D1's option (4) exactly, with (1) and (3) named
as rejected in the docstring.

### 2. B054 / DA-D3 + DA-R2 — the per-file split, both halves, on a two-file artifact I built.

`scratchpad/probe54`, a `changed_lines` javascript lane with
`producer = "istanbul"`, two records: `src/inside.ts` clean, `src/outside.ts`
carrying a `branchMap` arc on line 9 that its own `statementMap`/`s` classifies
as nothing.

*Outside the judged set* (only `inside.ts` changed):

```
assay: coverage record for 'src/outside.ts' contradicts itself at line(s) [9]: … Those arcs were dropped. …
js: PASS (exit 0)
PASS  [('R0','PASS',None), ('R1','PASS',None)]
```

*Inside the judged set* (`outside.ts` changed):

```
assay: coverage record for 'src/outside.ts' contradicts itself at line(s) [9]: …
assay: ERROR/UNREADABLE_ARTIFACT: 'src/outside.ts': its 'branchMap' arcs contradict its own 'statementMap'/'s' line classification at line(s) [9] … This lane judges it: 1 changed line(s) …
ERROR [('R0','PASS',None), ('R1','ERROR','UNREADABLE_ARTIFACT')]
```

Both halves name the file **and** the arc line, as DA-D3 requires. Direct
parser probes confirm the rest:

* **A-357 is untouched** — a `branchMap` entry typed `"branch"` still refuses
  the whole artifact: `ERROR UNREADABLE_ARTIFACT … "branch", which is not one
  of the arm-structured branch t…`.
* Both invariants are isolated, not just invariant 3: `tampered -> [2]` (a
  covered arc on a `missing` line) and `unconsidered -> [9]`.
* `_without_lines` returns an **empty** `BranchCoverage`, never `None`
  (`is None? False`), so `derive_branch_capability` still reads `"reported"`
  and cannot be pushed into the mixed-profile refusal — the docstring's claim,
  measured.
* A non-arc-bearing producer records `contradictory_branch_lines = None`, so
  the metadata cannot appear where arcs were never read.

`_contradictory_branch_lines` mirrors `FileCoverage.__post_init__`'s
invariants 3 and 5 exactly and deliberately omits the vacuous `excluded` one
(`model.py:406-442` vs `coverage_istanbul_json.py:345-405`) — so after the
drop, neither surviving invariant can fire, and the "isolation is
per-invariant, not a blanket ignore" claim holds. The two rewritten tests in
`test_coverage_istanbul_branch_arcs.py` are renamed to what they now assert
(`…_is_unreadable` → `…_is_isolated_to_that_file`), which is A-399 done right.

**Not hollow, measured:** mutation `m4` makes the parser record
`contradictory_branch_lines=None` and five tests go red — the three in
`test_coverage_istanbul_contradictory_branch_arcs.py` that cover isolation,
the skipped-and-named half and the judged-and-refused half, plus both
rewritten invariant tests. Named in the sweep below.

### 3. B053 / DA-D2 + DA-R1 + DA-R4 — everything except BLOCKER 1.

* **The six DA-R3 sites, measured one at a time, one line each.**
  `env_required` names the variable; `--shard 9/2` echoes the spec;
  `MISSING_EXTERNAL_TOOL` names `'go'` and the adapter's declared tool list;
  pre-run `DIRTY_TREE` names `src/mod.py` on *both* dispatch paths with two
  correctly *different* explanations (snapshot vs. live tree). All
  `[emitter lines: 1]`.
* **Every `evaluate_r1` refusal class also emits exactly one line** —
  `EMPTY_COVERAGE`, `FORMAT_MISMATCH`, `TARGET_NOT_MEASURED`,
  `BRANCH_UNAVAILABLE` (probe `probe53c`, one lane, four artifact modes).
* **DA-R4 holds in both directions**, which is the part most likely to have
  been faked. `probe49sql` with a baseline that does not write its declared
  `equivalence_artifact`: when the command **fails**, the early R2 claim is
  discarded and `[emitter lines: 0]` — the superseded refusal is silent, and
  the verdict says `COMMAND_FAILED`. When the command **succeeds**, the same
  refusal survives into the document and `[emitter lines: 1]` naming the
  artifact. That is exactly DA-R4's rule, and it is the deferral at
  `runner.py:3214-3215` doing it.
* **DA-D2 (b), the library half.** `runner.run_lane(..., diagnostics=buf)`
  with `stderr` redirected: the full sentence lands on `buf`, `stderr` is
  `(EMPTY)`.
* **No wire change.** Six lanes (clean R1, `EMPTY_COVERAGE`, `require_branch`,
  B049's replaced directory, the SQL R2 lane, B054's istanbul lane) run under
  both the base `a4a865da` build and the tip build, verdicts scrubbed of
  timestamps/versions/commits and compared: **IDENTICAL** in all six, and a
  key-set walk of the B054 pair shows `only base: []  only new: []` over 29
  keys.
* `Outcome`/`ReasonCode` are `StrEnum` (`errors.py:40,69`), so
  `announce_refusal`'s `.value` rendering really is byte-identical to the
  `f"{exc.outcome}/{exc.reason_code}"` `cli.py` used to print — the docstring's
  claim is true, not merely plausible.

### 4. B028 / DA-D10 — the direct-R0 half is real, and the higher-rigor half really was already correct.

`scratchpad/probe28`, `budget = "1s"`, a command that sleeps 3s, run under
both builds:

```
r0slow / base  → assay: BUDGET_EXCEEDED/LANE_TIMEOUT …   NO VERDICT FILE WRITTEN
r0slow / tip   → BUDGET_EXCEEDED [('R0','BUDGET_EXCEEDED','LANE_TIMEOUT')]
r1slow / base  → BUDGET_EXCEEDED [('R0',…,'LANE_TIMEOUT'), ('R1',…,'LANE_TIMEOUT')]   (already correct, silently)
r1slow / tip   → same verdict, now with the emitter line
```

So the implementer's measurement is confirmed independently: the higher-rigor
entry point was **already** writing a correct `LANE_TIMEOUT` verdict before
this branch, and the branch adds only the announcement there. The reserved
`--verdict-json` exists, and `assay verify` accepts it under **both** the
branch build and the shipped 4.1.0 verifier (`exit 0` each); `verify` is not
vacuous — a hand-edited `exit_code: 99` in the same document is refused
(`exit 1`, twice, once by the model check and once by the schema).

`_replace_highest_higher_rigor_claim_with_git_failed`'s new `status`/
`reason_code` parameters default to A-193/A-194's pair, and the caller
narrows on `exc.reason_code is ReasonCode.LANE_TIMEOUT` only — so the
GIT_FAILED rule is narrowed for one cause, not replaced.

**Measured:** mutation `m6` deletes the `LANE_TIMEOUT` branch so every cleanup
failure relabels to `GIT_FAILED` again, and exactly one test goes red —
`test_a_cleanup_that_failed_because_time_ran_out_says_so`, which injects the
cleanup failure through a `scratch_root_factory` double. Its control
(`…_still_says_git_failed`) stays green, which is what proves the change is a
narrowing of A-193/A-194 rather than a replacement of it.

### 5. B029 / DA-R6 — re-measured; the premise does not reproduce, and the shipped path really is untouched.

`scratchpad/probe29`: a real R3 lane, `mechanism = "import-break"`, a real
`ciu.global.toml` carrying `[deploy] cgroup_parent`, an
`infrastructure` table declaring `cgroup_parent = "derived:deploy.cgroup_parent"`,
and a suite that `exit 3`s unless the fact is in its environment.

```
FACT PRESENT:   base → PASS [('R0','PASS'),('R1','PASS'),('R3','PASS')]
                tip  → PASS [('R0','PASS'),('R1','PASS'),('R3','PASS')]
FACT WRONG:     base → FAIL [('R0','FAIL','COMMAND_FAILED'),('R1','PASS'),('R3','INCONCLUSIVE','CANARY_INCONCLUSIVE')]
                tip  → identical
```

The R3 claim is `PASS`, never `ERROR`/`BAD_LANE_CONFIG`, on the **base** as
well — DA-R6's finding, independently reproduced. The non-vacuity control
matters: with a deliberately wrong `ciu.global.toml` value the canary's
control half fails and R3 goes `CANARY_INCONCLUSIVE`, which proves the suite's
`os.environ` check is live and the `PASS` above is real evidence the fact
reached the canary's own side-run. And the instrumentation quoted in BLOCKER 2
shows `execute_command` is entered **zero** times on that run.

Note also: `infrastructure_environment` is threaded but never exercised with a
non-`None` value anywhere in the branch's tests (the implementer says so). Only
`infrastructure_source` is.

### 6. B060 / B056 / B055 / B009 / B024

* **B060 (DA-D14).** `build_zipapp` now stages under a `TemporaryDirectory`
  (`gate/distribution/build_release.py:350-380`); the outcome test
  (`tests/test_distribution_build_release.py:373-405`) builds into
  `<root>/dist` and asserts `[e.name for e in sorted(enclosing.iterdir())] ==
  ["dist"]` on **both** builds. Under the old code `staging = outdir.parent /
  "zipapp-staging"` resolves to `<root>/zipapp-staging`, which is a sibling of
  `dist` inside `enclosing`, so the assertion fails — the test is an outcome
  test that genuinely goes red if the staging tree comes back. I did not mutate this one — the
  arithmetic is exact and needs no run: `outdir.parent` **is** `enclosing`.
* **B056 (DA-D13/DA-R5).** Option 1, as ruled: the module docstring
  (`tests/test_verdict_schema_is_packaged.py:20-42`) now records the *refuted*
  measurement explicitly and states what the file does **not** claim, and the
  assertion message on
  `test_pyproject_declares_the_schema_as_package_data` no longer says
  "without this the schema … vanishes on install" but "it is not the only
  thing that ships the schema today". Both halves of DA-D13, and the
  declaration is kept with the reason recorded. The Go sibling was verified
  in place, not rewritten, as the ruling required.
* **B055 (DA-D12).** `docs/CONSUMERS.md`'s limit 4 now leads with "a Go R1
  claim is statement-granular **TO THE LINE**, not to the statement", names
  all three weighed alternatives and which was taken, and gives the operator
  the one action that changes the measurement (split the line). Matches
  alternative 1 exactly; no wire field.
* **B009 (DA-D16).** The distribution table's four measured claims I re-checked
  myself, in the consumers' own trees: `ciu/tools/assay/assay-3.2.0.pyz` +
  `.sha256`, invoked via `ciu/run-gate.toml:15`'s `assay_command` with
  `[lanes.ciu.pins.assay] version = "3.2.0"`; `cmru/tools/assay/assay-2.3.0.pyz`
  and `cmru/cmru.toml:33`'s `path` pin; `nyxloom/tools/assay/assay-4.0.0.pyz`
  via `nyxloom/run-gate.toml:16`; `dstdns/tools/assay/assay-4.0.0.pyz` with a
  `[lanes.*.pins.assay]` block per lane (`dstdns/run-gate.toml:34,87,98,118`,
  four of them). **All four accurate**, and the entry's own premise ("vendoring
  is retired") is correctly reported as measured FALSE rather than adopted —
  which is what DA-D16 asked for.
* **B024 (DA-D15).** Escape hatch legitimately taken; I re-ran all three
  checks. `docker run --rm tester-unified:local sh -lc 'python -m pyflakes
  --version; ruff --version; python -m ruff --version'` →
  `No module named pyflakes` / `ruff: not found` / `No module named ruff`.
  `gate/distribution/build-wheelhouse/` holds exactly five wheels
  (`packaging`, `setuptools`, `setuptools_scm`, `vcs_versioning`, `wheel`) and
  `build-requirements.txt` pins exactly those five with `--hash=sha256:`.
  `tools/tester-unified-gate.sh` installs `--no-index --find-links` from that
  wheelhouse at `:53-56` and `:82` and the built wheel `--no-index --no-deps`
  at `:126` — no other ingress. Nothing was landed, which is correct.
  DA-R7's replacement ruling lands **after** this tip and is out of scope for
  round 1; I will verify it in the fix round.

### 7. Phase boundary — clean.

```
git diff --name-only a4a865da...93188912 | grep -E '^assay/src/assay/(verdict|verify)\.py$|^assay/src/assay/schemas/|carve-assets'   → (empty)
git log --format=%s a4a865da..93188912 | grep -c '!'                                                                                  → 0
config.py:141  LANE_SCHEMA_VERSION = 2
cli.py:1043    LANE_INVENTORY_SCHEMA_VERSION = 1
verdict.py:245 VERDICT_SCHEMA_VERSION = 9
```

`assay/diff.py`, `assay/git.py`, `assay/mutation.py`, `assay/adapters/python.py`
all present and unrenamed (cmru's four imports). No `carve-assets/W*` touched.
All four README/CONSUMERS anchors into the new DESIGN-GUIDE sections resolve
against real headings (`§B049` at `DESIGN-GUIDE.md:536`, B053 at `:575`, B054
at `:639`, B028 at `:677`).

### 8. Registered gate — GREEN on `93188912`.

Run in my own detached worktree at `/workspaces/vbpub/.worktrees/assay-wave-d-r1`
(removed afterwards), launched detached, verdict read in a separate step from
the log's own markers:

```
Created wheel for assay: assay-4.1.1.dev14+g93188912-py3-none-any.whl
ASSAY_GATE_PHASE=wheel-installed … attestation-hardened … verdict-v6-v7-v8-hard-cut-verified
… verdict-v9-successors-verified
tester-unified: PASS (exit 0)   commit: 931889122cf663469a81e4db6e5e990c43d0263d
ASSAY_GATE_PHASE=self-hosted-lane-passed … topos-qualified … cmru-b006a-qualified
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1        (exactly 1 occurrence)
GATE_EXIT=0
```

No `FAIL`, no `Traceback`, no `error:` anywhere in the log. Independently, the
full suite at the tip is `3980 passed, 18 skipped` (mine, with the gate's own
`--ignore=tests/test_self_hosting.py`; the implementer reports `3985 passed,
20 skipped` without it — the delta is that module).

This is a genuinely independent run, not a re-read of the implementer's: the
gate's throwaway wheel for the same commit is `size=526694
sha256=74fb3b81…` in my log and `size=526694 sha256=087415a9…` in BRIEF-3's.
Same size, different digest, which is expected — the *gate* wheel is built
without `SOURCE_DATE_EPOCH` normalisation, so zip mtimes differ; the
byte-identical claim belongs to the *release* builder and is tested
separately (`test_two_builds_of_one_commit_are_byte_identical`). Worth stating
so nobody reads the two digests as a reproducibility regression.

---

## HOLLOW-TEST SWEEP

**Method.** For each new or changed subject, the guard was removed or the
condition inverted **with the `Edit` tool** on a throwaway copy of the tip
(`scratchpad/m1`..`m7`), never in the branch worktree, and the tests that
exercise that module were run against the copy. The tip itself was never
modified; the copies are disposable.

**Status of the evidence: complete.** `m1` is a completed whole-suite run;
`m2`..`m7` were killed mid-flight by the controller during the host-load
incident and were **re-run strictly serially, targeted, `nice -n 19 ionice -c
3`**, after generation 4's gate container cleared at 17:04:20. Every mutant is
paired with a run of the **identical file set against the unmutated tip**, so
a GREEN result is attributable rather than accidental — all six baselines are
green (25 / 81 / 116 / 41 / 35 / 38 passed). Raw logs:
`scratchpad/reruns.summary` and `scratchpad/rr-<label>.log`.

| # | subject mutated | file:line | expectation | result |
|---|---|---|---|---|
**Baselines first — the file sets are real and green unmutated:**

```
base-canary  exit=0 ::  25 passed in 58.14s
base-safeio  exit=0 ::  81 passed in  6.65s
base-istan   exit=0 :: 116 passed in  2.04s
base-stmt    exit=0 ::  41 passed in  3.27s
base-timeout exit=0 ::  35 passed in 11.41s
base-refusal exit=0 ::  38 passed in 10.14s
```

| # | subject mutated | file:line | expectation | result |
|---|---|---|---|---|
| m1 | `execute_command` drops both infrastructure forwards to `resolve_command_plan` | `runner.py:884-885` | RED if B029's guard is real | **GREEN — hollow → BLOCKER 2.** Targeted: `exit=0 :: 25 passed in 41.43s`, identical to the `base-canary` baseline's 25. Whole suite agreed (24 copy-harness failures only, no canary test) |
| m2 | `canary._run_pipeline` drops both forwards to `execute_command` | `canary.py:227-228` | RED if the canary half is guarded | **GREEN — hollow, same defect as m1** (`exit=0 :: 25 passed in 42.58s`) |
| m3 | B049's guard removed (`_refuse_if_parent_was_replaced` returns unconditionally) | `safeio.py:320-322` | RED | **RED — guard is real** (`4 failed, 77 passed`) |
| m4 | istanbul parser records `contradictory_branch_lines=None` | `coverage_istanbul_json.py:336` | RED | **RED — guard is real** (`5 failed, 111 passed`) |
| m5 | both `statement_attribution` carries removed | `statement_attribution.py:229,346` | **GREEN expected** — no artifact populates `blocks` *and* `contradictory_branch_lines` (SF-5) | **GREEN, as predicted** (`41 passed`) — unreachable, confirmed |
| m6 | B028's `LANE_TIMEOUT` branch removed (always `GIT_FAILED`) | `runner.py:3931-3941` | RED | **RED — guard is real** (`1 failed, 34 passed`) |
| m7 | DA-R4's deferred announcement removed | `runner.py:3214-3215` | RED | **RED — guard is real** (`1 failed, 37 passed`) |

**The two RED mutants killed exactly the right tests** — the guards are
specific, not incidental:

```
m3 (B049 guard removed) -- 4 failed:
  test_consume_refuses_when_the_held_parent_directory_was_replaced
  test_a_top_level_artifact_whose_project_root_was_replaced_names_the_root
  test_run_lane_r1_names_the_replaced_directory_instead_of_reporting_no_coverage
  test_read_kill_signal_refuses_a_replaced_directory_instead_of_folding_to_crashed

m4 (parser records None) -- 5 failed:
  test_the_parser_isolates_the_defect_and_keeps_every_other_file
  test_a_defective_file_outside_the_judged_set_is_skipped_and_named
  test_a_defective_file_inside_the_judged_set_refuses_and_names_the_arc_line
  test_an_arc_on_a_line_no_statement_covers_is_isolated_to_that_file
  test_a_covered_arc_on_a_never_executed_line_is_isolated_to_that_file
```

`m6` and `m7` each killed exactly one test, and exactly the right one:

```
m6 (LANE_TIMEOUT carry removed) -- 1 failed:
  test_a_cleanup_that_failed_because_time_ran_out_says_so
m7 (DA-R4 deferral removed)     -- 1 failed:
  test_a_surviving_early_r2_refusal_is_announced_once
```

Every failing name is a name that describes the removed behaviour — no
collateral, no vague breakage. **B049, B054, B028 and B053/DA-R4 all have
honest, surgical test suites.** The sweep found exactly one hollow guard, and
it is B029's (`m1` and `m2`, both GREEN), plus one deliberate confirmation
that SF-5's carry is dead code (`m5`, GREEN as predicted).

**Sweep scoreboard: 7 mutants, 4 RED (guards real), 2 GREEN-and-wrong
(BLOCKER 2), 1 GREEN-and-expected (SF-5).**

**Independently of the sweep, the two tests that matter most are not hollow**,
because I reproduced both of their subjects from outside: DA-R4 flips
correctly in both directions through the CLI (0 lines superseded / 1 line
surviving), and B049's guard fires at all five reads with passing controls.
The one confirmed hollow guard is `m1`/`m2`'s.

**A-399 (does each touched test still say what it does?).** Yes, and
deliberately so in the two places it mattered:
`test_an_arc_on_a_line_no_statement_covers_is_unreadable` →
`…_is_isolated_to_that_file` and its sibling, both renamed with the
disposition change; `test_run_refuses_a_missing_required_infrastructure_env_var_
without_crashing` keeps its name but its docstring now states that B053
**changed this test's stderr expectation** and asserts the new truth
positively instead of the old absence (`tests/test_cli_run.py:662-695`). That
is the honest handling of an inverted expectation. The only naming problem in
the branch is `test_the_legacy_standalone_canary_forwards_the_same_two_
parameters`, which does not test forwarding — BLOCKER 2.

---

## DECISION ASKS (product calls — not improvised)

1. **BLOCKER 1's scope.** Are the two post-command dirt/HEAD terminals in
   phase 1's B053 scope? They are not among DA-R3's five named sites, but they
   *are* refusals reachable through `assay run` that print nothing, which is
   the bar DA-R3 set for dispatching me. Either fix them (prescription above,
   ~30 lines + 2 tests) **or** narrow all three claims. Landing neither is the
   blocker; I do not get to choose which.
2. **SF-1's scope.** Same question one layer up: does DA-D10's "the reserved
   `--verdict-json` is WRITTEN" bind for a timeout raised *before* the
   command, or only for the command's own overrun? A CLI-level
   `LANE_TIMEOUT` handler covers both dispatch paths in ~20 lines and is
   modelled on one already in `cli.py`; the alternative is to narrow the
   `CHANGES.md` headline and file the residue.
3. **B054's not-judged set (DA-D3's own reopen clause).** DA-D3 said to
   reconsider the v10 `excluded_files` wire list "only if R-1 shows a consumer
   needs it". **I did not find such a consumer.** The Consumer coupling facts
   are unchanged by measurement: no consumer program parses a verdict
   document, dstdns reads five fields by name and none of them is a file list.
   The diagnostics line is sufficient. **Recommend: hold the DA-D3 ruling as
   written.**
4. **DA-R5 (B056) — no argument from me.** The measured truth is in the
   docstring and in the assertion message; I have nothing to add and do not
   ask for the reversal DA-R5 pre-declined.

---

## SELF-COMPACTION PROMPT

*(For resuming this reviewer by `SendMessage` for the fix-verification rounds.
3-round cap; round 1 is spent.)*

**KEEP:**

- **My verdict:** round 1 = **NOT ACCEPT**, 2 blockers, 5 should-fixes, 4
  decision asks. Report at
  `/tmp/claude-1003/-workspaces-vbpub/6b340a26-b326-4787-bd86-cae79b08f519/scratchpad/assay-WAVE-D-v10-REVIEW-R1-round1.md`.
- **BLOCKER 1** — post-command `DIRTY_TREE`/`HEAD_CHANGED` announce nothing on
  BOTH dispatch paths: `runner.py:4618-4642` (`_finish_direct_r0_lane`, has no
  `diagnostics` parameter) and `runner.py:2340-2344` → `:2809-2827`
  (`_execute_snapshot_unit`'s `post_reason`). Three false claims to fix or
  narrow: `CHANGES.md:32`, `tests/test_refusal_announcement.py:384`,
  `runner.py:3813-3819`. Re-verify with `scratchpad/probe53b` (an R0-only and
  an R0+R1 lane whose command writes `leftover.txt` / makes an empty commit);
  the check is `grep -cE '^assay: [A-Z_]+/[A-Z_]+: '` over stderr == 1, and
  the message must blame the lane's own command, not the operator.
- **BLOCKER 2** — `tests/test_r3_canary_sees_infrastructure.py:139` is a
  signature-only check; `execute_command` is entered **0** times on a real R3
  CLI run (measured). Re-verify by deleting the two forwards in
  `runner.execute_command`'s `resolve_command_plan` call and in
  `canary._run_pipeline`'s `execute_command` call and confirming the suite goes
  **red**.
- **SF-1** B028 residue (`budget = "0.001s"` → no verdict file, both paths;
  escape at `git.repo_top` → `runner.py:219`). **SF-2** `runner.py:3944-3950`
  silent `GIT_FAILED`. **SF-3** `_report_probe_refusal` second spelling
  (`runner.py:447`). **SF-4** fd nit at `mutation.py:1642/1648`. **SF-5**
  `statement_attribution.py:229,346` carry unreachable by any real artifact.
- **Already verified — do NOT re-verify unless the fix touches them:** B049 at
  all five reads with non-vacuous controls; B054 both halves + A-357 intact +
  empty-not-None `BranchCoverage`; B053's six DA-R3 sites and DA-R4 both
  directions and the library `diagnostics` half; no wire change (6 lanes,
  base-vs-tip verdicts byte-identical after scrubbing, key sets equal); B028's
  direct-R0 fix red-first vs. base and higher-rigor already-correct;
  B029 non-reproduction with a non-vacuous wrong-fact control;
  B060/B056/B055/B009/B024 all confirmed, B009's four consumer pins re-checked
  in their own trees; phase boundary clean; registered gate GREEN on
  `93188912` (`GATE_EXIT=0`, one `ASSAY_REGISTERED_GATE_COMPLETE=1`, wheel
  `assay-4.1.1.dev14+g93188912`); full suite `3980 passed, 18 skipped`.
- **Reusable harness (rebuild if the scratchpad is gone):**
  `scratchpad/r1venv2` = editable install of the tip worktree
  `scratchpad/r1wt/assay`; `scratchpad/basevenv` = editable install of
  `scratchpad/basewt/assay` (= `a4a865da`) — both `python -m venv
  --system-site-packages` + `pip install --no-index --no-build-isolation -e`.
  Probe repos: `probe49` (coverage), `probe49sql` (SQL R2, per-mutant +
  kill-signal + DA-R4), `probe49ing` (ingested report), `probe54` (two-file
  istanbul), `probe53` / `probe53b` / `probe53c` (refusal classes),
  `probe28` (timeouts), `probe29` (R3 + `derived:` fact).
- **Hollow-test sweep is COMPLETE — do not re-run it.** 7 mutants, each paired
  with a green unmutated baseline of the same file set. RED (guard real):
  `m3` B049 4-failed, `m4` B054 5-failed, `m6` B028 1-failed, `m7` DA-R4
  1-failed. GREEN-and-wrong: `m1` and `m2`, B029's forwarding — **BLOCKER 2**.
  GREEN-and-expected: `m5`, SF-5's dead carry. Raw logs kept at
  `scratchpad/reruns.summary` + `scratchpad/rr-*.log`. Re-run only the
  mutant whose subject a fix actually touches.
- **Throttle rules now in force:** no `pytest -n`/xdist — serial only, prefixed
  `nice -n 19 ionice -c 3`; prefer targeted test files; whole suite at most
  once; before launching the registered gate check `docker ps | grep
  tester-unified` and wait for no gate container, then
  `docker update --cpus=3 …`; never a build/pip step concurrently with a suite.
- **Gate mechanics:** the gate script only accepts worktrees under
  `/workspaces/vbpub` or `/workspaces/vbpub/.worktrees/*`. Use
  `git -C /workspaces/vbpub worktree add --detach
  /workspaces/vbpub/.worktrees/assay-wave-d-r1 <sha>`, launch
  `setsid nohup bash -c '{ bash assay/tools/tester-unified-gate.sh <wt>; echo
  GATE_EXIT=$?; } > <log> 2>&1' < /dev/null > /dev/null 2>&1 &` from
  `/workspaces/vbpub`, arm a `Monitor`/background `until` on `GATE_EXIT=`,
  read the verdict in a separate step, remove the worktree afterwards. Never
  touch `/workspaces/vbpub/.worktrees/assay-wave-d-v10` (generation 4 lives
  there).

**DROP:** the full diff text; every source docstring I read to build the
picture; the B024/B009 verification detail (settled); the base-vs-tip
byte-identity harness output (settled); the whole DA-D1..DA-D16 / DA-R1..DA-R7
text (only the ids matter now).

**ON RESUME:** read `git diff 93188912..<new tip>` FIRST, blind; then check
each blocker's prescription against what actually landed, re-run only the
probes the fix touches, and re-run the registered gate on the new tip under
the throttle. State ACCEPT / ACCEPT-conditional / NOT ACCEPT unambiguously —
the controller merges on my word.
