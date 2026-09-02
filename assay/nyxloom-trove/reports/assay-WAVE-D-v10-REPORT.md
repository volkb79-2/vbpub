# assay Wave D (v10) — implementer REPORT

Per item: every acceptance box with file:line evidence, the ruling's A-row,
measured numbers, transcripts, the docs disposition, decision asks, "what a
reviewer should push on", and "what I did NOT do and why".

Branch `feature/assay-wave-d-v10` from `main` at `a4a865da`.
Wave prompt: `nyxloom-trove/WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md`.

---

## Phase 1

### B049 — a replaced output directory (DA-D1 → A-408)

**Ruling applied:** option (4). `os.fstat(parent_fd).st_nlink == 0` on the
ALREADY-HELD descriptor, checked at `consume()` time, refuses
`ERROR`/`UNREADABLE_ARTIFACT` with a message naming the directory, the cause
and the remedy. A-408 records the ruling and names (1), (2) and (3) as
rejected, in B049's own words.

**Implementation, file:line.**

| what | where |
|---|---|
| the guard | `src/assay/safeio.py:285` `OutputReservation._refuse_if_parent_was_replaced` |
| its one call site | `src/assay/safeio.py:276`, inside `consume()`'s `try`, before `_safe_bounded_read`, so the `finally` still closes the descriptor on the refusal path exactly as it does on a raising read |
| `posixpath` import (for the directory name in the message) | `src/assay/safeio.py:24` |

**Why one seam covers all three sites B049 names.** The five shipped reads of
a reservation all call `OutputReservation.consume`, which has exactly one
implementation:

```
$ grep -n 'consume()' src/assay/*.py
src/assay/runner.py:2241:            raw = reservation.consume()                       # coverage read
src/assay/runner.py:2256:            baseline_equivalence = equivalence_reservation.consume()   # SQL R2 equivalence_artifact
src/assay/runner.py:2286:            mutation_report_bytes = mutation_reservation.consume()     # ingested R2 report
src/assay/mutation.py:1642:                        equivalence_bytes = equivalence_reservation.consume()
src/assay/mutation.py:1192:    raw = reservation.consume()                          # kill signal -> `crashed` fold
```

The two `mutation.py` sites both propagate an `AssayError` rather than
absorbing it: `mutation.py:1642` raises directly out of `_MutantRun`
construction, and `mutation.py:1644-1647` catches `_read_kill_signal`'s
`AssayError` into `decode_error`, which `mutation.py:1654-1655` re-raises
after closing both reservations. So the new refusal is not swallowed into
the `crashed` bucket that B049 identifies as the worst of the three folds.

**Tests** — `tests/test_safeio_replaced_output_directory.py`, 8 tests:

| test | site | proves |
|---|---|---|
| `test_consume_refuses_when_the_held_parent_directory_was_replaced` | the seam | `ERROR`/`UNREADABLE_ARTIFACT`; the message names the artifact, the directory (`'reports'`), "deleted and recreated", and the `clean` remedy; asserts as a precondition that the artifact really is complete on disk |
| `test_consume_still_reads_an_artifact_written_into_the_reserved_directory` | the seam | the legitimate state, constructed before the refusal ships |
| `test_consume_still_reports_a_genuinely_absent_artifact_as_absent` | the seam | not a superset refusal — an intact empty directory is still the truthful `None` (P20/A-174) |
| `test_a_top_level_artifact_whose_project_root_was_replaced_names_the_root` | the seam | `dirname == ""` renders as "the project root", not `''` |
| `test_run_lane_r1_names_the_replaced_directory_instead_of_reporting_no_coverage` | **coverage read**, end to end through `runner.run_lane` | R0 `PASS` (the command really ran), R1 `ERROR`/`UNREADABLE_ARTIFACT` — explicitly asserted *not* `EMPTY_COVERAGE`. The fake tool is a real shell command: `rm -rf reports && mkdir reports && cat > reports/cov.json <<'EOF' …` |
| `test_run_lane_r1_passes_when_the_same_tool_writes_into_the_reserved_directory` | **coverage read** | the control: identical lane, identical payload, no `rm -rf` → `PASS`, `pct == 100.0`. So the refusal above is caused by the replacement and by nothing else about the shape |
| `test_read_kill_signal_refuses_a_replaced_directory_instead_of_folding_to_crashed` | **`mutation.py`'s absent read** | refuses instead of returning `None` (which the caller reclassifies `crashed`, A-223e) |
| `test_read_kill_signal_still_returns_none_for_a_genuinely_absent_signal` | **`mutation.py`** | the control for the same |

**Red-first transcript** (`git stash push -- src/assay/safeio.py`, i.e. the
tree with the tests but without the guard):

```
FAILED tests/test_safeio_replaced_output_directory.py::test_consume_refuses_when_the_held_parent_directory_was_replaced
FAILED tests/test_safeio_replaced_output_directory.py::test_a_top_level_artifact_whose_project_root_was_replaced_names_the_root
FAILED tests/test_safeio_replaced_output_directory.py::test_run_lane_r1_names_the_replaced_directory_instead_of_reporting_no_coverage
FAILED tests/test_safeio_replaced_output_directory.py::test_read_kill_signal_refuses_a_replaced_directory_instead_of_folding_to_crashed
4 failed, 4 passed, 1 warning in 1.46s
```

with, on the `run_lane` case, the pre-fix verdict reading
`NO_MEASUREMENT`/`EMPTY_COVERAGE` and, on the mutation case,
`Failed: DID NOT RAISE <class 'assay.errors.AssayError'>`. With the guard
restored: `53 passed` for
`tests/test_safeio_replaced_output_directory.py tests/test_safeio.py`.

**Docs disposition** (AGENTS.md's three-document rule):

| document | change |
|---|---|
| `README.md:264,269` | `clean: false` REQUIRED → RECOMMENDED; the new `ERROR`/`UNREADABLE_ARTIFACT` behaviour stated; links to the new DESIGN-GUIDE section |
| `docs/CONSUMERS.md:622,633` | same downgrade, with the refusal text quoted, the 4.1.0→5.0.0 before/after stated, and the non-coverage artifacts (SQL R2 equivalence, mutation kill signal) named |
| `docs/DESIGN-GUIDE.md` §"A replaced output directory is named, not folded into EMPTY_COVERAGE" | the WHY: why `st_nlink` and not a re-open by name, why `UNREADABLE_ARTIFACT` needs no schema change, the rejected alternatives |

`pytest tests/test_docs_examples_and_vocabulary.py` (38 tests, including the
README→DESIGN-GUIDE anchor resolution check) is green, so the new anchor
resolves.

**What I did NOT do, and why.**

- **No dedicated end-to-end test for the SQL R2 `equivalence_artifact` site
  (`runner.py:2256`).** Standing up a real SQL R2 lane whose own tooling
  recreates its dump directory is a large fixture for a path the single seam
  already covers by construction; the enumeration above (five call sites,
  one `consume` implementation) plus the two directly-tested sites is the
  evidence offered instead. If R-1 judges that insufficient, the cheapest
  addition is a `runner._execute_snapshot_unit`-level test, not a new lane.
- **No `runner.py` or `coverage.py` change.** The fold B049 describes is in
  those files' *reading* of `None`; the fix removes the false `None`, so
  neither caller needed editing. `coverage.py:179-187`'s `EMPTY_COVERAGE`
  path is still exactly right for a genuine absence, and a test asserts it
  still fires there.
- **No schema, `verdict.py`, `verify.py` or drift-guard change** — phase 1
  is required to stay releasable on v9.

**What a reviewer should push on.**

1. That the `st_nlink == 0` witness is not reachable from any *benign*
   state. Directories start at `nlink >= 2`; the only way to reach 0 with a
   live descriptor is an unlink of the directory itself. Try to construct a
   counterexample (a rename? a bind mount? a directory on tmpfs vs ext4) and
   see whether the refusal cries wolf.
2. That the refusal really is reached on the SQL R2 and ingested-report
   sites, not merely argued to be — see "what I did NOT do".
3. That the message is *correct* as prescription, not merely present: it
   tells the consumer to turn the clean option off. Check it against a tool
   that has no such option, where the second clause ("write into the
   existing directory") is the only usable half.
4. That `consume()`'s descriptor bookkeeping is still correct on the new
   raise path (the `finally` closes and nulls `_parent_fd`; `close()` after
   a raising `consume()` must stay a safe no-op — `test_safeio.py`'s
   existing state-machine tests cover this and are green).

---

## Gate — the registered gate, run by the implementer

Command, from `/workspaces/vbpub`, detached, with the implementer's own
marker appended in the same shell:

```
setsid nohup bash -c '{ bash assay/tools/tester-unified-gate.sh \
  /workspaces/vbpub/.worktrees/assay-wave-d-v10; echo GATE_EXIT=$?; } \
  > <log> 2>&1' &
```

Three runs were needed; the first two are recorded in the LOG in full,
because both failures were the implementer's, not the product's.

**Run 3, on `299d18a0` — GREEN.** Verdict read in a separate step from the
log's own markers:

```
$ grep -c 'ASSAY_REGISTERED_GATE_COMPLETE=1' <log>   -> 1
$ grep 'GATE_EXIT=' <log>                            -> GATE_EXIT=0
$ grep -c -E 'FAILED|DIRTY_TREE|Traceback' <log>     -> 0
```

Head (build) and tail (completion):

```
Created wheel for assay: filename=assay-4.1.1.dev5+g299d18a0-py3-none-any.whl
  size=517257 sha256=3b469a2b62be3e370f0b64ce5294fb6671b53c7bf72ddbce19c325e9823aae00
...
tester-unified: PASS (exit 0)
  commit: 299d18a0e6e76fb2372af6b919b845f76558cfb3
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
7 passed in 14.21s
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
GATE_EXIT=0
```

The wheel name carries the judged commit. **Gate-verified commit:
`299d18a0`.** The branch tip is one docs-only commit past it (this report's
own gate entry and the LOG's).

Run 2's suite figure, worth keeping because it is the only full-suite count
this generation measured: `3944 passed, 20 skipped in 567.14s (0:09:27)`.

## Decision asks

*(none — DA-D1 applied as written, no ruling proved inapplicable)*

## Scope: what generation 1 did NOT reach

Nine of phase 1's ten items are untouched: B054, B053(a)(b), B028, B029,
B060, B056, B024, B055, B009. Phase 2 and phase 3 are untouched, and
**nothing under `verdict.py`, `verify.py`, `src/assay/schemas/` or the
drift-guard carve-assets was modified**, so the branch is still releasable on
v9 at every commit, exactly as the wave prompt requires. The designs for the
next two items (B054's per-file disposition and B053's single-emitter
question) are worked out in `assay-WAVE-D-v10-BRIEF-1.md` §3 and §4 with the
seams named, so generation 2 does not re-derive them.

---

# Generation 2

## B053 (a)+(b) — every refusal says WHY (DA-D2 (a)+(b) / DA-R1 → A-409)

**Ruling applied:** ONE emitter, `runner.announce_refusal(exc, *,
diagnostics)`, printing exactly `assay: {outcome}/{reason_code}: {message}`,
called at every site where an `AssayError` becomes a refusal `Verdict` or
`Claim`; `cli.py`'s existing prints refactored onto it; `cli.py` keeps
`diagnostics=err`. Half (c) (the wire `detail`) deliberately NOT done.

### Implementation, file:line

| what | where |
|---|---|
| the one emitter | `src/assay/runner.py:307` `announce_refusal` |
| exported | `src/assay/runner.py:148` (`__all__`, beside `assemble_verdict`) |
| `evaluate_r1` gains `diagnostics`, default `None` | `src/assay/runner.py:1124` (signature), `:1332` (its own `except AssayError` → R1 claim) |
| the stream, threaded (unchanged, pre-existing) | `cli.py:781` `diagnostics=err` → `run_lane` (`runner.py:3867`) → `_run_higher_rigor_lane` (`runner.py:3646`) → `_run_prepared_lane` (`runner.py:2684`) |

The 13 conversion sites the emitter is called at, all of which build a
refusal `Claim` or return a refusal `Verdict`:

```
runner.py:1332   evaluate_r1's own catch                  -> R1 Claim
runner.py:2796   unit.equivalence_error                   -> R2 Claim (A-279)
runner.py:2812   unit.profile_error                       -> R1 Claim
runner.py:2988   diff parse for R2                        -> R2 Claim
runner.py:3044   R2 whole-target resolution (B033)        -> R2 Claim
runner.py:3074   R2 targets-from-diff                     -> R2 Claim
runner.py:3145   ingested R2 (report absent OR unreadable)-> R2 Claim
runner.py:3210   R2 orchestration fault                   -> R2 Claim (+the R3 Claim at :3237, announced once)
runner.py:3301   R3 canary                                -> R3 Claim
runner.py:3685   _run_higher_rigor_lane plan resolution   -> refuse_lane Verdict
runner.py:3795   the snapshot block (git/isolation)       -> refuse_all Verdict, or GIT_FAILED claim replacement
runner.py:4007   run_lane's env_effective plan resolution -> refuse_lane Verdict
runner.py:4254   run_lane's direct-R0 plan resolution     -> refuse_lane Verdict
cli.py:300       main()'s outer handler                   -> exit code only
cli.py:716       attestation LANE_TIMEOUT                 -> refuse_lane Verdict
cli.py:749       adapter resolution                       -> refuse_lane Verdict
```

**Placement rule, and the two places it bites.** The call goes where the
error becomes a claim or a verdict, NOT where it is caught:

- `runner.py:2812` announces `unit.profile_error` inside `if r1_declared:`
  rather than at the catch in the artifact read (`runner.py:2812`'s error is
  produced ~500 lines earlier). On a lane that declares no R1, that read's
  failure never becomes a claim at all, and a line with no document behind it
  would be noise a consumer cannot act on.
- `runner.py:3210` announces the R2 orchestration fault ONCE, even though the
  same error also builds the R3 refusal claim at `runner.py:3237-3247`. One
  fault, one line.

**B033's second spelling, removed.** `runner.py:3044` used to print
`assay: lane {name}: R2 whole-target resolution refused: {exc}` — the same
message in a different format. It is now `announce_refusal` plus an indented
context line (`  in lane 'x', resolving R2 whole-target mutation targets`),
so the refusal itself has exactly one spelling and the extra fact B033 wanted
is still there. No test asserted the old text (`grep -rn 'R2 whole-target'
tests/` → nothing).

**`assay.canary` stays silent by construction.** `evaluate_r1`'s new
parameter defaults to `None` and `canary.py:219`/`:360` do not pass it: a
variant's R1 refusal is a step inside the canary mechanism, not the lane's
own refusal, and announcing it would attribute the variant's cause to the
lane.

### Acceptance boxes, with evidence

| B053 box | evidence |
|---|---|
| every `AssayError` that becomes part of a verdict has somewhere to carry its message | the 13+3 call sites above; `tests/test_refusal_announcement.py` |
| the entry's proposed "narrower fix" (widen `_cmd_run`'s handler) | **cannot work, measured** — `grep -n 'except AssayError' src/assay/runner.py` → 15 handlers that convert and RETURN. Recorded as the rejected alternative in A-409 |
| DA-R3's `diagnostics` route | taken; it is the same stream `_report_probe_refusal` (`runner.py:352`, its own print at `:408`) already writes to |
| oracle 1: a deep refusal prints its message | `tests/test_cli_run.py::test_run_refuses_a_missing_required_infrastructure_env_var_without_crashing` (rewritten) |
| oracle 2: controlled wrong implementation | the pre-fix worktree run below |

### Transcripts

Red-first, pre-fix tree (`36ac802c`) in a detached scratch worktree, the new
test file copied in — no `git stash`:

```
$ git worktree add --detach <scratchpad>/prefix-b053 36ac802c
$ cp tests/test_refusal_announcement.py <scratchpad>/prefix-b053/assay/tests/
$ (cd <scratchpad>/prefix-b053/assay && python -m pytest tests/test_refusal_announcement.py -q)
FAILED ...::test_the_emitter_renders_every_declared_outcome_reason_pair_in_one_format
FAILED ...::test_the_emitter_writes_nothing_when_the_caller_asked_for_no_diagnostics
FAILED ...::test_the_message_is_copied_byte_for_byte_and_not_reformatted
FAILED ...::test_a_broken_coverage_artifact_names_its_cause_on_stderr_exactly_once
FAILED ...::test_an_unresolvable_judge_base_names_the_git_failure_exactly_once
FAILED ...::test_a_library_caller_gets_the_same_line_on_its_own_stream_and_not_stderr
6 failed, 3 passed, 1 warning in 2.52s
```

The 3 passing are controls and MUST pass on both sides:
`test_every_reason_code_in_the_closed_enumeration_is_reachable_by_that_test`
(a property of `errors.py`),
`test_a_structural_refusal_at_the_cli_boundary_uses_the_same_one_line`
(`cli.py` already printed there — which is precisely why a boundary-only
handler is not the fix), and
`test_a_library_caller_that_names_no_stream_still_gets_a_verdict_and_no_output`.

With the fix: `9 passed`. Whole suite, worktree-local:
`3960 passed, 20 skipped in 526.62s (0:08:46)`, zero failures.

The scratch worktree was removed (`git worktree remove --force`) before the
suite ran.

### Docs disposition

| file | change |
|---|---|
| `docs/CONSUMERS.md` | new practice "When a lane refuses, read the one stderr line — the document does not carry the sentence": the exact line shape with a real example, where it goes from the CLI vs. a library caller, that it appears exactly once, that it is diagnostic text and must not be parsed or gated on, and which refusals carry no line |
| `docs/DESIGN-GUIDE.md` | new §6 section "Every refusal says WHY, exactly once, through one emitter (B053/A-409)": why the wire stays closed, why one emitter beats a CLI-boundary `try`, the exactly-once rule, and what deliberately prints nothing |
| `CHANGES.md` | one Fixed bullet and one Documentation bullet |

### Decision asks

1. **Refusals that carry no `AssayError` print no line.** `DIRTY_TREE`
   (`runner.py:3719`, `:4221`), `HEAD_CHANGED` (`:3721`),
   `MISSING_EXTERNAL_TOOL` (`:3960`), the `env_required` refusal (`:4103`)
   and the bad-`--shard` refusal (`:4149`) call `refuse_lane`/`refuse_all`
   with a literal `(status, reason_code)` and no message. DA-R1's format
   requires a message; there is none to copy. Writing one would mint refusal
   text at the point of refusal rather than the point of knowledge, which
   DESIGN-GUIDE §5 forbids. **Question for the controller:** should phase 2
   give those five sites their own `AssayError` (message composed where the
   fact is known — e.g. the dirty-tree check already has the offending paths
   from `git.dirty_paths`), so that "every refusal reachable through
   `assay run` prints exactly one line" becomes true without qualification?
   Landed as-is for now, and named as a known limit in A-409, the backlog
   entry and CONSUMERS.
2. **`_run_prepared_lane`'s `r2_early_claim` may be announced and then
   superseded.** If the lane's own command already failed (`result.outcome is
   not Outcome.PASS`), `runner.py:3134` builds the R2 claim from the command
   result and the earlier `r2_early_claim` is discarded — but its cause was
   already announced. That is one extra true sentence about a real refusal
   that happened, never a false one, and suppressing it would need a
   deferred-print buffer the ruling did not ask for. Flagged so R-1 can rule
   it noise if it disagrees.

### What a reviewer should push on

- **Count, not presence.** Every end-to-end test here asserts
  `len(refusal_lines) == 1`. Try to find a refusal class that prints twice —
  the R2-orchestration-fault → R3 path (`runner.py:3210`/`:3237`) and the
  ingested-report path (`unit.mutation_report_error` folded into
  `ingested_r2_error` at `runner.py:3099`) are the two where a naive
  "announce at the catch" would have double-printed.
- **The enumeration.** `test_the_emitter_renders_every_declared_outcome_reason_pair_in_one_format`
  iterates `errors.REASON_CODES`, and a second test proves that mapping
  covers all of `ReasonCode`. It does NOT prove every code is *reachable
  through `assay run`* — that would need a lane per code. R-1's stated push
  is exactly that; the honest state is "the emitter renders all of them; four
  classes are proven end-to-end (`FORMAT_MISMATCH`, `GIT_FAILED`,
  `BAD_LANE_CONFIG` at the boundary, `BAD_LANE_CONFIG` from an infrastructure
  fact)".
- **stdout is untouched.** `_print_run_summary` was not modified, and the
  `--verdict-json -` tests still parse the WHOLE of stdout as JSON.
- **The canary silence** (`evaluate_r1(diagnostics=None)` from `canary.py`) is
  a judgment call, not a ruling. If R-1 thinks a canary-internal R1 refusal
  should be announced, it is a one-line change.

### What I did NOT do, and why

- **No wire `detail` field** (DA-D2 (c)) — phase 2. Nothing under
  `verdict.py`, `verify.py`, `src/assay/schemas/` or the drift-guard was
  touched.
- **No context/lane name in the standard line.** DA-R1 pins the format to
  `assay: {outcome}/{reason_code}: {message}`. Where a lane name genuinely
  adds something (B033's whole-target site) it goes on a separate indented
  line, so the pinned format stays exactly one thing.
- **No message minted for the five message-less refusals** — decision ask 1.

## B054 — a self-contradictory istanbul `branchMap` (DA-D3 + DA-R2 → A-410)

**Ruling applied:** per file, on A-405's principle. Skipped and NAMED on the
diagnostics stream if the file has no line in the judged set; `ERROR`/
`UNREADABLE_ARTIFACT` naming the file and the arc line if it does. No
`excluded_files` wire list. BRIEF-1 §3 option (a) — store the offending arc
lines on `FileCoverage` — taken; option (b), the `(profile, defects)` parser
signature, rejected.

### Implementation, file:line

| what | where |
|---|---|
| the stored fact | `src/assay/coverage_parsers/model.py:366` `FileCoverage.contradictory_branch_lines`, with its positivity check at `:373` and the docstring paragraph at `:330` saying why it is stored where `line_directive_remapped` is derived |
| detection | `src/assay/coverage_parsers/coverage_istanbul_json.py:345` `_contradictory_branch_lines` — exactly the two invariants an honest producer can trip, computed BEFORE construction so the parser names them rather than reading them out of a `ValueError` |
| the drop | `src/assay/coverage_parsers/coverage_istanbul_json.py:381` `_without_lines` — returns a `BranchCoverage`, never `None`, even when empty |
| the isolation site | `src/assay/coverage_parsers/coverage_istanbul_json.py:310-330` — the existing `try`/`except ValueError` stays, so every OTHER invariant still refuses the artifact whole |
| the rebuilds | `src/assay/statement_attribution.py:225` and `:342`, both carrying the field with a comment |
| the refusal | `src/assay/evaluate.py:195` `_refuse_contradictory_branch_arcs` |
| its two call sites | `src/assay/evaluate.py:561` (changed-lines, inside the per-file loop immediately after A-405's check) and `:1156` (whole-target, immediately after A-405's) |
| the skip notice | `src/assay/runner.py:352` `_announce_contradictory_branch_records`, called from `evaluate_r1` at `:1267`, right after `check_empty_coverage` |

### Two design points a reviewer will ask about

**1. Why the field is STORED when A-405's sibling is DERIVED.** BRIEF-1 §3
named this as the one place the precedent does not transfer, and it is right.
`line_directive_remapped` reads `blocks`, which every rebuild carries, so it
cannot disagree with its own records. A contradicting arc must be DROPPED —
`FileCoverage.__post_init__` refuses construction otherwise — and once
dropped there is nothing left to derive from. The cost is that both rebuild
sites must carry it; both do, with a comment saying a rebuild that forgot it
would launder a defective record into a clean one. Rejected alternative (b),
a `(profile, defects)` parser return: it changes the signature every format's
parser shares to carry a fact only one format can produce.

**2. Why `runner` names EVERY defective record and not only the SKIPPED
ones.** DA-D3 requires that a file with no line in the judged set be named.
"No line in the judged set" is resolved inside `assay.evaluate`, against the
diff, the source-root boundary, the exclusion globs and `is_test_path` —
asking for it in `runner` would mean either re-deriving that join (which
A-385/A-367 forbid: there is ONE key resolution) or threading a stream into a
module DA-R2 explicitly requires to stay pure. Naming every defective record
is a strict superset of what the ruling asks: a judged one is named here AND
refused by name, which is one line more, never one fact less. `evaluate.py`
therefore needed no return-channel change at all, which is why DA-R2's "and
returns the skipped files as data" is not implemented literally — recorded
here rather than silently.

### Acceptance boxes, with evidence

| B054 box | evidence |
|---|---|
| shape 1: isolate the refusal to the offending file | `coverage_istanbul_json.py:345`/`:381`; `tests/test_coverage_istanbul_contradictory_branch_arcs.py::test_the_parser_isolates_the_defect_and_keeps_every_other_file` |
| a file that IS in the diff still refuses | `evaluate.py:195`/`:561`/`:1156`; `…::test_a_defective_file_inside_the_judged_set_refuses_and_names_the_arc_line` |
| oracle: PASS on the strength of the changed-lines diff | `…::test_a_defective_file_outside_the_judged_set_is_skipped_and_named` — `outcome == "PASS"`, `pct == 100.0`, exit 0 |
| oracle: "must at minimum name WHICH file broke it" | the same test asserts the stderr line names `never_imported.ts` AND line `215` |
| controlled wrong implementation: not a silent drop | the field is asserted directly; `…::test_the_committed_real_artifacts_carry_no_contradiction` proves both committed REAL arc-bearing artifacts are unaffected |
| A-357 untouched | `tests/test_coverage_istanbul_branch_arcs.py`'s unrecognised-TYPE tests are unchanged and green |

### Transcripts

Red-first, against `440d5da9` (the tip with B053 but not B054) in a detached
scratch worktree, the new module copied in:

```
$ git worktree add --detach <scratchpad>/prefix-b054 440d5da9
$ cp tests/test_coverage_istanbul_contradictory_branch_arcs.py <scratchpad>/prefix-b054/assay/tests/
$ (cd <scratchpad>/prefix-b054/assay && python -m pytest tests/test_coverage_istanbul_contradictory_branch_arcs.py -q)
FAILED ...::test_the_parser_isolates_the_defect_and_keeps_every_other_file
FAILED ...::test_a_clean_document_records_no_contradiction_at_all
FAILED ...::test_the_committed_real_artifacts_carry_no_contradiction[coverage-istanbul-json.vitest-istanbul.json]
FAILED ...::test_the_committed_real_artifacts_carry_no_contradiction[coverage-istanbul-json.vite-plugin-istanbul.json]
FAILED ...::test_a_defective_file_outside_the_judged_set_is_skipped_and_named
5 failed, 2 passed, 1 warning in 1.83s
```

The 2 that pass on both sides are controls, and both are load-bearing:
`test_a_defective_file_inside_the_judged_set_refuses_and_names_the_arc_line`
(the judged file's DISPOSITION is deliberately unchanged — only the origin of
the refusal moved from the parser to `evaluate`, so a reviewer can see the
fix is not a weakening) and `test_a_lane_that_judges_nothing_defective_is_unaffected`.

With the fix: `7 passed`.

Two pre-existing tests asserted the OLD verdict-wide parse refusal and were
rewritten rather than deleted:
`tests/test_coverage_istanbul_branch_arcs.py::test_an_arc_on_a_line_no_statement_covers_is_*`
and `…::test_a_covered_arc_on_a_never_executed_line_is_*`. Both keep their
invariant names and reasoning and now assert the isolation.

### Docs disposition

| file | change |
|---|---|
| `docs/CONSUMERS.md` | new JavaScript-lanes subsection "A never-executed file's own instrumentation quirk costs you that file, not the verdict (B054)", placed directly after the `coverage.include` synthesis paragraph it follows from: the two dispositions, what a consumer sees for each, that a broad `src/**` include is the right shape again, and what has NOT changed (A-357) |
| `docs/DESIGN-GUIDE.md` | new §6 section "A self-contradictory coverage record is one FILE's defect (B054/A-410)": the A-405 parallel, the one place it does not transfer and what that forced, why `branches` is never `None`, and why there is no wire list |
| `CHANGES.md` | one Fixed bullet |

### What a reviewer should push on

- **The two-file artifact** is exactly what R-1 was told to ask for: one
  defective file outside the judged set, one inside. Both are in
  `tests/test_coverage_istanbul_contradictory_branch_arcs.py`.
- **Whether the drop can launder a real defect.** The anti-tamper invariant
  (a covered arc on a never-executed line) is now DROPPED rather than
  refused at parse time. It is not weakened for a judged file — that file
  refuses — but for an unjudged file the tampered arc simply disappears.
  That is the same trade A-405 already makes, and the unjudged file
  contributes nothing to the verdict either way; push on it if you disagree.
- **Whole-target mode has no lenient case.** A declared target IS the judged
  set, so `evaluate_targets` refuses unconditionally for a defective target.
  Check that reading against B005.
- **`derive_branch_capability`** is computed from whether records carry
  `branches` at all, and `_without_lines` never returns `None`, so a file
  whose every arc was dropped still reports `reported`, not `unavailable`.
  That is deliberate (A-008) and worth an adversarial look.

### What I did NOT do, and why

- **No `excluded_files` wire list** — DA-D3 rejects it; nothing under
  `verdict.py`/`verify.py`/the schema was touched.
- **`evaluate.py` returns no skipped-file data.** See design point 2: the
  fact is carried on the profile, so no return-channel change was needed to
  keep `evaluate` pure. This is a deviation from DA-R2's literal wording and
  is named as such.
- **No change to the other four coverage formats.** Only the istanbul parser
  can produce this defect (its `branchMap` is an independent array); the
  other parsers classify each line from one summed count and cannot violate
  the invariant by construction, as `model.py`'s own docstring already says.

## B060 — the release builder's staging tree (DA-D14 → A-411)

**Ruling applied:** remove the staging tree by building under a
`TemporaryDirectory`; an outcome test that a build leaves no untracked path.

| what | where |
|---|---|
| the fix | `gate/distribution/build_release.py:349` `build_zipapp`, now `with tempfile.TemporaryDirectory(prefix="assay-zipapp-staging-") as tmp:` — `tempfile` was already imported for `build()`'s own clone scratch |
| the outcome test | `tests/test_distribution_build_release.py::test_a_build_writes_nothing_outside_its_own_outdir` |
| the fixture change it needed | the `built` fixture's two real builds now target `mktemp(...)/"dist"` instead of `mktemp(...)`, so the enclosing directory is controlled and "nothing beside the outdir" is expressible |

**Why the test does NOT build into the real repository**, even though the
acceptance criterion is phrased `--outdir <repo>/assay/dist`: a real build
into the worktree during the suite would leave `assay/dist/*.whl` and the
`.pyz` behind for the duration, and the self-hosted gate lane judges that
tree — the test would cause the exact `DIRTY_TREE` failure B060 exists to
remove. The property under test is "the builder writes nothing outside
`--outdir`", which is what makes the repository-path invocation safe, and it
is checkable in a tmpdir with no such hazard. Recorded here because a
reviewer reading the acceptance line literally will ask.

The test asserts the OUTCOME, not the mechanism — it never mentions
`TemporaryDirectory` — so it survives any of B060's three shapes and goes red
if a future edit reintroduces a sibling directory by any means.

**Red-first:** not proved in a scratch worktree for this item. The test's
subject is a real ~40s release build; against the pre-fix builder it fails by
construction (`zipapp-staging` appears beside `dist`), which is the same fact
B060 recorded by direct observation and the reason the entry exists. If R-1
wants the transcript, `git stash`-free reproduction is
`git worktree add --detach <path> 440d5da9` plus this one test — named here
rather than claimed as run.

## B056 — the packaging test's refuted measurement (DA-D13 → A-412)

**Ruling applied:** option 1 — correct the docstring to the measured truth
and assert the OUTCOME; apply the same treatment to the Go helper's test,
which is already in that shape (verify, do not re-write).

| box | evidence |
|---|---|
| ruling as an A-row naming the three options | A-412 |
| the docstring no longer states a refutable measurement | `tests/test_verdict_schema_is_packaged.py`'s module docstring now states A-396's measurement (stanza deleted → the wheel still carries the schema, 47 members) and says the file asserts the outcome |
| the same false claim in the assertion MESSAGE | found and corrected too: `test_pyproject_declares_the_schema_as_package_data` said "without this the schema exists in the source tree and vanishes on install", which is the same refuted claim one layer down. It now says the declaration is kept as the belt to the git finder's braces, for the git-metadata-absent build |
| the Go helper's test | VERIFIED, not rewritten — `tests/test_go_helper_is_packaged.py`'s docstring already states the corrected position and already asserts the outcome. Nothing changed in it |
| option 2 (make the negative reachable) | NOT taken; named as rejected in A-412 with the cost (a second real wheel build) and named as the right upgrade if that cost ever falls |

## B055 — Go line granularity (DA-D12 → A-413)

**Ruling applied:** alternative 1 of three — leave it as a documented limit.
No wire field.

| box | evidence |
|---|---|
| A-row naming all three alternatives | A-413 |
| CONSUMERS' Go paragraph states the limit beside what a Go R1 claim means | `docs/CONSUMERS.md`, "Go lanes" point 4, now opening "a Go R1 claim is statement-granular TO THE LINE, not to the statement", with the exposure (toward false PASS), the triggering shapes, and the one consumer-side remedy (split the line) |
| the test asserting today's behaviour STAYS | `tests/test_statement_attribution_go_witnesses.py:122` `test_lit_go_drops_the_fabricated_signature_but_still_launders_line_four` — untouched, and now the thing that would go red if a future cut reverses A-413 |
| no wire field | nothing under `verdict.py`/`verify.py`/the schema touched |

## B009 — assay.toml's role and the distribution model (DA-D16)

Docs only. New `docs/CONSUMERS.md` section, "What `assay.toml` is, and what
it is not (B009)", placed before "Obtain and verify an immutable release".

**Item 2 is the one that mattered, and the measurement REFUTES the entry.**
B009 states that per-repo vendoring "is RETIRED as the estate pattern".
Measured 2026-09-02, directly, not inferred:

```
$ grep -n assay_command */run-gate.toml
cmru/run-gate.toml:21:assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-2.3.0.pyz"]
ciu/run-gate.toml:15:assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-3.2.0.pyz"]
nyxloom/run-gate.toml:16:assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-4.0.0.pyz"]
$ grep -n 'assay_command\|pins.assay' /workspaces/dstdns/run-gate.toml
33:assay_command = ["python3", "tools/assay/assay-4.0.0.pyz"]
34:[lanes.assay-dlq.pins.assay]
36:sha256 = "tools/assay/assay-4.0.0.pyz.sha256"
$ grep -n 'assay' cmru/cmru.toml        # the S15 pin
33:path    = "tools/assay/assay-2.3.0.pyz"
```

All four consumers vendor a pinned, sha256-verified zipapp. Nothing is baked
into a shared image; no consumer resolves assay from `PATH`; assay itself
builds its own wheel in-repo for its gate and never imports its source under
test. DA-D16 says do NOT prescribe the retired-vendoring future unless it is
already true — it is not, so the docs describe the vendored pin as the
pattern to copy and name the image-bake direction as a backlog item that is
not the shipped state.

Item 1 is stated in substance (adapter + judgment-policy file, not an
estate-wide lane registry, a project that cannot adopt assay declares its
gates in its own script, "assay-judged before release" is per-project release
policy). Item 3 is one closing paragraph, as instructed.

**Correction found while writing this.** CONSUMERS' Go section said a
judge-phase refusal's text "reaches only a caller that invokes the evaluation
layer itself" and named B053 as this wave's-not-to-fix. B053 (a)+(b) shipped
in this generation's commit 4, so that paragraph was false the moment it
landed; it now points at the new "When a lane refuses, read the one stderr
line" section.

### What a reviewer should push on (these four items)

- **B060:** that the archive really is unaffected by the staging path — the
  byte-identical-rebuild assertion (`test_two_builds_of_one_commit_are_byte_identical`)
  is the evidence, and it now runs over builds in two DIFFERENTLY-nested
  directories, which is strictly stronger than before.
- **B056:** whether correcting a message is enough. The entry's own option 2
  is the stronger fix and was declined on cost; A-412 records that, and R-1
  may reasonably rule the other way.
- **B009:** re-run the greps. The whole value of that section is that it is
  measured, so a reviewer should measure it again rather than read it.

## Generation 2 — gate transcript and scope

**GATE-VERIFIED COMMIT: `c80b3452`.** One run, first try, green. Full marker
transcript in `assay-WAVE-D-v10-LOG.md`; the four verdict reads were
`ASSAY_REGISTERED_GATE_COMPLETE=1` × 1, `GATE_EXIT=0`,
`grep -c -E 'FAILED|DIRTY_TREE|Traceback'` = 0, and the wheel
`assay-4.1.1.dev9+gc80b3452-py3-none-any.whl` carrying the judged commit.
Both v9 schema phases passed, which is the mechanical confirmation that
phase 1 is still releasable on v9.

Whole suite, worktree-local, immediately before the commit that was gated:
**3968 passed, 20 skipped in 509.12s (0:08:29)**, zero failures. Generation
1's figure on the same suite was 3944; the 24 added are this generation's 9
(B053) + 7 (B054) + 1 (B060) and 7 that were already on the branch.

## Generation 2 — decision asks, collected

1. **(B053)** Refusals that carry no `AssayError` — `DIRTY_TREE`
   (`runner.py:3719`, `:4221`), `HEAD_CHANGED` (`:3721`),
   `MISSING_EXTERNAL_TOOL` (`:3960`), the `env_required` refusal (`:4103`)
   and the bad-`--shard` refusal (`:4149`) — print no stderr line, because
   they call `refuse_lane`/`refuse_all` with a literal `(status,
   reason_code)` and no message. DA-R1's format requires a message.
   **Question:** should phase 2 give those five sites their own
   `AssayError`, with the message composed where the fact is known (the
   dirty-tree check already holds the offending paths from
   `git.dirty_paths`), so that "every refusal reachable through `assay run`
   prints exactly one line" holds without qualification?
2. **(B053)** An `r2_early_claim` can be announced and then superseded by a
   claim built from a failed command (`runner.py:3134`). That is one extra
   TRUE sentence about a refusal that really happened, never a false one;
   suppressing it needs a deferred-print buffer the ruling did not ask for.
   **Question:** noise, or correct as landed?
3. **(B056)** Option 2 — make the packaging test's negative reachable again
   by building with the git file finder disabled — was declined on cost (a
   second real wheel build in a suite that already pays for two). **Question
   for R-1:** is the corrected message enough, or is the two-sided check
   worth the build?
4. **(B029, raised BEFORE implementing it)** DA-D11 says to thread
   `infrastructure_source`/`infrastructure_environment` "into `canary.py`'s
   two call sites". Measured, `runner.execute_command` has exactly ONE caller
   (`canary.py:213`, inside `_run_pipeline`, which both halves of the LEGACY
   standalone `run_python_canary` go through). The ISOLATED canary path
   (`run_isolated_canary`), which is what `_run_prepared_lane` actually uses,
   takes a pre-executed `unit.result`. **Question:** if the shipped R3 path
   never reaches `execute_command`, is B029's defect confined to a code path
   no lane uses — in which case the fix is still correct but the CLI-driven
   test the ruling asks for cannot reach it, and the honest deliverable is a
   docstring correction plus the threading, not a lane test? Generation 3
   should resolve this by measurement before writing code.

## Generation 2 — what is NOT done, and why

**Three of phase 1's ten items are untouched: B028 (item 4), B029 (item 5),
B024 (item 8).** They are the three that need either a new end-to-end harness
(B028's slow-command CLI test), a measurement that has not been made (B029,
decision ask 4), or an image/wheelhouse investigation with a
land-nothing branch (B024). Generation 2 checkpointed at the E-008 boundary
with the gate green rather than starting a fourth item it could not finish;
BRIEF-2 §3-§5 carries the seams for all three, with two corrections to
BRIEF-1's own numbers that generation 3 should not re-derive.

Phase 2 and phase 3 are untouched. No `!` commit marker exists on the branch.

---

# Generation 3

The controller ruled generation 2's four decision asks as **DA-R3..DA-R6**
(controller log `ba741c3b`). This generation's first item is the two that
reopen B053.

## B053 follow-ups — DA-R3 and DA-R4 → A-414

**Rulings applied.** DA-R3: the emitter's contract is a MESSAGE, not an
exception; the five (six, counting both `DIRTY_TREE` sites) refusal sites
that carried a bare `(status, reason_code)` compose their message where the
fact is known and go through the same emitter, and "every refusal reachable
through `assay run` prints exactly one line" holds without qualification.
DA-R4: an announcement about a refusal the verdict does not carry is not
correct as landed; defer that ONE site to where the final claim is chosen; no
general buffer.

### Acceptance, with file:line evidence

| box | evidence |
|---|---|
| `DIRTY_TREE`, snapshot path, names the uncommitted paths | `src/assay/runner.py:3782-3803` — `dirty = git.dirty_paths(...)` is bound, and the message carries `', '.join(dirty)`; test `test_a_dirty_tree_names_the_uncommitted_files_on_a_higher_rigor_lane` |
| `HEAD_CHANGED` names BOTH revisions | `src/assay/runner.py:3804-3817` — `observed_head` bound and both it and `commit` interpolated; test `test_a_head_that_moved_names_both_commits` |
| `MISSING_EXTERNAL_TOOL` names the tool | `src/assay/runner.py:4050-4069` — names `tool`, `adapter.name` and the whole declared list; test `test_a_missing_external_tool_names_the_tool` |
| `env_required` names the variables | `src/assay/runner.py:4213-4235` — `missing_required` interpolated twice (the list, then the set-these remedy); test `test_a_missing_required_env_var_names_the_variable` |
| bad `--shard` echoes the spec | `src/assay/runner.py:4278-4296` — `--shard {shard!r}` plus the INDEX/COUNT form; test `test_a_malformed_shard_names_the_spec_it_could_not_parse` |
| `DIRTY_TREE`, direct-R0 path | `src/assay/runner.py:4359-4380` — its own message, distinct from the snapshot path's because the reason the dirt matters differs (the live tree is labelled with `commit`); test `test_a_dirty_tree_names_the_uncommitted_files_on_a_direct_r0_lane` |
| one emitter, not six spellings | every one of the six calls `runner.announce_refusal` (`src/assay/runner.py:307`); the format lives in exactly one `print` |
| exactly ONE line per refusal | every new test asserts `len(_refusal_lines(...)) == 1`, not `>= 1` |
| DA-R4: the superseded refusal is silent | `src/assay/runner.py:2851-2856` records `r2_deferred_early_error` and does NOT announce; `:3194-3200` announces only inside `elif r2_early_claim is not None` |
| DA-R4: the surviving refusal still speaks | `test_a_surviving_early_r2_refusal_is_announced_once` — the matched control, which passes on BOTH sides of the change |
| no wire change | `git show --stat` on the commit: nothing under `verdict.py`, `verify.py`, `src/assay/schemas/` or the drift-guard carve-assets |

### Transcript — the DA-R4 defect, measured before it was fixed

`equivalence_artifact` is a `sql`-only key (P34/W4), so the reproduction is a
real SQL R2 lane, built in a scratch repository and driven through
`assay.cli.main` at the pre-fix tip `10d9390d`:

```
$ python -c "from assay.cli import main; ...  ['run','package','--file',...,'--verdict-json','-']"
assay: ERROR/EXEC_FAILED: the baseline declared judge.mutation.equivalence_artifact
  '.assay/schema-dump.sql' but its own command did not write it -- R2 needs the
  baseline's own bytes to prove any mutant equivalent, so no mutant can even be
  attempted
{ ... "claims": [
    {"reason_code": "COMMAND_FAILED", "rigor": "R0", "source": "computed",
     "status": "FAIL", "verified_by_assay": true},
    {"reason_code": "COMMAND_FAILED", "rigor": "R2", "source": "computed",
     "status": "FAIL", "verified_by_assay": true} ], ... }
```

The stderr line names a refusal (`ERROR`/`EXEC_FAILED`) that appears nowhere
in the document — the exact irreconcilability DA-R4 names. The lane's command
was `exit 7`; with `exit 0` the same lane produces `ERROR`/`EXEC_FAILED` as
its R2 claim and the line is correct, which is why the two tests are a
matched pair rather than a single negative.

### Transcript — red-first

Run in the worktree with ONLY `tests/test_refusal_announcement.py` modified
and no source change (so the "pre-fix tree" is the branch tip `10d9390d`
itself; no scratch worktree was needed):

```
$ python -m pytest tests/test_refusal_announcement.py -q -p no:randomly
FAILED ... ::test_a_dirty_tree_names_the_uncommitted_files_on_a_higher_rigor_lane
FAILED ... ::test_a_dirty_tree_names_the_uncommitted_files_on_a_direct_r0_lane
FAILED ... ::test_a_head_that_moved_names_both_commits
FAILED ... ::test_a_missing_external_tool_names_the_tool
FAILED ... ::test_a_missing_required_env_var_names_the_variable
FAILED ... ::test_a_malformed_shard_names_the_spec_it_could_not_parse
FAILED ... ::test_an_early_r2_refusal_the_verdict_discards_is_never_announced
7 failed, 10 passed
```

Every DA-R3 failure was `assert 0 == 1` — zero refusal lines where one was
required — and the DA-R4 failure was `assert not True` over the list
`["assay: ERROR/EXEC_FAILED: the baseline declared
judge.mutation.equivalence_artifact ..."]`. With the fix: **17 passed**.

The 10 that passed at the pre-fix tip are generation 2's 9 plus
`test_a_surviving_early_r2_refusal_is_announced_once`, which is a CONTROL: it
must pass on both sides, or the DA-R4 pair would prove only that the
announcement was deleted rather than deferred.

### Docs disposition

| file | change |
|---|---|
| `docs/CONSUMERS.md` | the "which refusals carry no line" paragraph is replaced by "There is no exception to 'exactly one line'", with all five real messages quoted verbatim, plus the complement DA-R4 establishes: a refusal whose claim the verdict discards prints nothing, so every line on stderr corresponds to a refusal in the document beside it |
| `docs/DESIGN-GUIDE.md` | the §6 B053 section's "What still prints nothing, and why" is replaced by two subsections — "The emitter's contract is a MESSAGE, not an exception (DA-R3/A-414)", which states plainly that A-409's §5 reasoning was backwards (§5 forbids inventing a value that is not known, not writing down one that is), and "A line about a refusal the verdict does not carry is worse than silence (DA-R4/A-414)" |
| `CHANGES.md` | one Fixed bullet (B053, A-414) |
| `nyxloom-trove/4-backlog.md` | B053's "Known limit" paragraph struck through (not deleted — A-409 still cites it) and a "Resolution addendum" block with four ticked boxes |

### What a reviewer should push on

- **Are all six really reachable, and did I pick the right entry point for
  each?** Four go through `assay.cli.main`; `HEAD_CHANGED` and
  `MISSING_EXTERNAL_TOOL` go through `runner.run_lane` directly, because the
  CLI resolves `commit` from `HEAD` itself (so the two can never disagree
  under it) and chooses the adapter from the lane's declared language. If R-1
  can construct a CLI-level reproduction of either, the test should move.
- **The DA-R4 deferral is one site, by hand.** There is no mechanism
  preventing a FUTURE early-R2 refusal from being added outside the
  `result.outcome is Outcome.PASS` guard and announced eagerly. The guard
  against that is the comment at `runner.py:2845-2857` and this REPORT, not
  a type. A reviewer may reasonably ask for the eager sites to be inverted
  too, at the cost DA-R4 declined.
- **`env_required` vs. the infrastructure refusal.** Both are
  `ERROR`/`BAD_LANE_CONFIG`, and generation 2 already covered the
  infrastructure one. Check they do not double-print on a lane that trips
  both — they cannot (the infrastructure refusal happens during plan
  resolution, which the `env_required` refusal precedes and returns from),
  but that ordering is worth confirming.
- **Message wording is not a wire contract** — none of these six strings is
  parsed by any test beyond the fact it names (a path, a revision, a tool, a
  variable, a spec). That is deliberate: DA-D2 (c)'s `detail` field, in phase
  2, is where this text becomes something a consumer may see structurally.

### What I did NOT do, and why

- **No new reason code for any of the six.** A-138/A-170 keep the enumeration
  closed; the sentence was what was missing, not the code.
- **No general deferred-print buffer** — DA-R4 explicitly forbids it, and
  only one site can be superseded.
- **No wire `detail` field** (DA-D2 (c)) — phase 2.

## B028 — a lane-wide `LANE_TIMEOUT` writes no verdict artifact (DA-D10 → A-415)

**Ruling applied.** One outer catch per higher-rigor entry point and one for
direct R0's own loop; the lane-wide `LANE_TIMEOUT` becomes the refusal claim
the existing refuse path already builds; the reserved `--verdict-json` is
WRITTEN; cleanup of a half-built snapshot is attempted and its failure
recorded via the existing cleanup-failure path, never masking the timeout;
tested red-first through the installed CLI with `budget_seconds = 1` and a
real slow command.

### The measurement that shaped the fix — and corrects the entry

B028 was filed 2026-08-25 on the finding that "a plain `sleep 30` against a
1s budget … exits non-zero with no verdict artifact". Re-measured at
`21bdf19d` through `assay.cli.main`, with a real budget and a real slow
command and no stubbed clock:

| dispatch | lane | reserved `--verdict-json` | exit |
|---|---|---|---|
| `_run_higher_rigor_lane` | `rigor = ["R0","R1"]` | **written**; `assay verify` exit 0; `outcome = BUDGET_EXCEEDED`, `reason_code = LANE_TIMEOUT`, R0 **and** R1 claims present | 4 |
| direct R0 (A-189) | `rigor = ["R0"]` | **never created** | 4 |

```
$ python -c "from assay.cli import main; ... ['run','package','--file',...,'--verdict-json','<path>']"
assay: BUDGET_EXCEEDED/LANE_TIMEOUT: the lane-wide deadline expired
REAL_EXIT=4
ls: cannot access '<path>': No such file or directory
```

So half of what B028 describes was fixed at some point between the filing and
this wave — `_run_higher_rigor_lane`'s single outer `try`
(`src/assay/runner.py:3819`) already spans base resolution, the scratch root
and the whole snapshot block, and its `except AssayError` already returns a
refusal verdict. **That is why the fix adds exactly one boundary and not
two**, and why the two higher-rigor tests in this module are labelled
regression guards rather than red-first proofs: a reviewer cannot otherwise
tell "already covered" from "untested".

### Acceptance, with file:line evidence

| box | evidence |
|---|---|
| a decision on where the boundary belongs | A-415, naming the per-call-site alternative and the measured reason it loses (~16 `remaining()` reads across three modules, several added after B025, none self-wrapped) |
| direct-R0 boundary, one catch | `src/assay/runner.py:4465-4551` — one `try` spanning `execute_plan` and the post-command guard; `except AssayError` at `:4512` |
| the post-command guard is unchanged, only moved | `src/assay/runner.py:4552` `_finish_direct_r0_lane`, carrying A-175/A-178's comment block and both terminals verbatim |
| the command's own evidence survives the timeout | `result_holder` at `src/assay/runner.py:4489`; the `assemble_verdict` branch passes `result=result_holder[0]`, so argv, timing and output tails are in the document |
| nothing to assemble from → the existing refuse path | the `refuse_lane` branch at the end of the same handler, identical in shape to this function's other pre-work refusals |
| reserved `--verdict-json` WRITTEN, through the installed CLI | `test_a_direct_r0_lane_that_runs_out_of_time_still_writes_its_verdict` asserts `destination.exists()` after `main([...,"--verdict-json",str(destination)])` |
| the artifact is schema-valid | `test_the_timed_out_verdict_is_one_assay_verify_accepts`, parametrised over BOTH dispatch paths, asserts `main(["verify", …]) == 0` |
| never masks the timeout | `src/assay/runner.py:3609` (the pair is now a parameter, defaulting to A-193/A-194's) and `:3916` (the `LANE_TIMEOUT` branch); tests `test_a_cleanup_that_failed_because_time_ran_out_says_so` and its `GIT_FAILED` control |
| B053's one line still appears exactly once | `test_the_refusal_names_the_deadline_on_the_diagnostics_stream` — the new catch is a new conversion site, and it announces once, not twice |
| no wire change | nothing under `verdict.py`, `verify.py`, `src/assay/schemas/` or the drift-guard |

### Transcript — red-first

```
$ python -m pytest tests/test_lane_timeout_writes_a_verdict.py -q -p no:randomly
FAILED ... ::test_a_direct_r0_lane_that_runs_out_of_time_still_writes_its_verdict
FAILED ... ::test_the_timed_out_verdict_is_one_assay_verify_accepts[rigor0]
FAILED ... ::test_a_cleanup_that_failed_because_time_ran_out_says_so
3 failed, 4 passed
```

with the fix, **7 passed**. The four pre-fix passes are controls that must
pass on both sides: the two higher-rigor regression guards, the B053
one-line check, and the `GIT_FAILED` control. The `LANE_TIMEOUT` cleanup
failure showed exactly the masking DA-D10 forbids:

```
E  AssertionError: Claim(rigor='R1', ..., status=<Outcome.ERROR>, reason_code=<ReasonCode.GIT_FAILED>)
```

Broader sweep after the fix, `-k "r0 or runner or cli_run or timeout or
budget"`: **535 passed, 1 skipped in 127.80s**.

### The one test double, and why A-334 does not reach it

`_factory_raising_on_exit` is a `scratch_root_factory` whose teardown raises.
It stands in for no external system: it is assay's own seam, and it exists
because the state DA-D10 names — cleanup failed *after* the lane's work
completed — cannot be steered into deterministically by any real budget. The
claim under test is assay's own disposition rule (which pair the replaced
claim carries), not any tool's behaviour, which is precisely the line A-334
draws.

### What a reviewer should push on

- **Is the direct-R0 boundary wide enough?** It starts at `execute_plan` and
  ends at the last `assemble_verdict`. Everything BEFORE it (evidence, HEAD,
  the pre-run dirt guard, plan resolution) reads `deadline.remaining` too. A
  timeout there still escapes. That is deliberate — those sites precede any
  command and every one of them already has its own refusal terminal — but a
  reviewer should try to construct a budget tight enough to expire inside
  `git.repo_top` and see what happens.
- **`mutation.py` and `canary.py`'s `remaining()` reads** are inside
  `_run_higher_rigor_lane`'s boundary and were not separately proven here. A
  mutation lane with a 1s budget is worth running.
- **The `LANE_TIMEOUT` narrowing is by reason code, not by outcome.** A
  future `BUDGET_EXCEEDED` carrying some other code would still be laundered
  to `GIT_FAILED`. `BUDGET_EXCEEDED`'s reason-code set is currently
  `{LANE_TIMEOUT}` alone, so the two are equivalent today; if that set ever
  widens the check should widen with it.

### What I did NOT do, and why

- **No per-call-site wrappers** — DA-D10 and the entry both reject them.
- **No new reason code and no schema change** — the `BUDGET_EXCEEDED`/
  `LANE_TIMEOUT` pair already exists and already renders.
- **No fake clock anywhere in the CLI tests** — DA-D10 asks for a real
  `budget_seconds` and a genuinely slow command.

## B029 — the R3 canary's infrastructure wiring (DA-D11 → DA-R6 → A-416)

**Ruling applied.** DA-R6: measure first. If the misattributed
`ERROR`/`BAD_LANE_CONFIG` R3 claim reproduces on the shipped isolated-canary
path, fix where that path builds the side-run's plan. If it does not, thread
the two parameters through `execute_command` anyway (the legacy
`run_python_canary` path is public), correct the docstring, land the
CLI-driven test as a regression guard, and mark B029 RESOLVED-by-measurement
with the transcript, noting that its premise was confined to the legacy path.

**It did not reproduce. The second branch was taken.**

### The measurement, in full

Scratch repository, real git, real `pytest`, driven through
`assay.cli.main` at `dd8f4d2c`. The lane:

```toml
rigor = ["R0", "R3"]
argv = ["python", "-m", "pytest", "tests", "-q"]

[lanes.package.infrastructure]
ASSAY_B029_FACT = "derived:deploy.cgroup_parent"

[lanes.package.judge.canary]
mechanism = "import-break"
target = "pkg/mod.py"
```

with `ciu.global.toml` (gitignored, as ciu itself keeps it — A-293) carrying
`[deploy] cgroup_parent = "assay-b029.slice"`, and a suite containing

```python
def test_infrastructure_fact_is_present():
    assert os.environ["ASSAY_B029_FACT"] == "assay-b029.slice"
```

Result:

```
package: PASS (exit 0)
[{"rigor": "R3", "status": "PASS", "source": "computed",
  "verified_by_assay": true,
  "canary": {"mechanism": "import-break", "target": "pkg/mod.py",
             "control_outcome": "PASS", "transformed_outcome": "FAIL",
             "expected_reason_code": "COMMAND_FAILED",
             "observed_reason_code": "COMMAND_FAILED",
             "description": "inserted `raise AssertionError(\"assay-canary-import-break\")` at line 1"}}]
```

**Why this is evidence and not a tautology.** A verdict-only assertion (the
R3 claim is `PASS`) would prove nothing about the fact: a canary whose
control half lost the variable would fail for that reason and render some
other claim. The suite inside the canary REFUSES when the fact is absent, so
`control_outcome: PASS` cannot be produced by a side-run that did not have
it. That single field is the measurement.

**What B029 got wrong, precisely.** The entry says `execute_command` "has
never had anywhere to forward these params TO ... only the canary's own
side-run plan does not [resolve them]". The first clause was true. The second
conflates two entry points: `canary.run_python_canary` (legacy, standalone,
public, goes through `execute_command`) and `canary.run_isolated_canary`
(what `runner._run_prepared_lane` actually uses, takes a pre-executed
`unit.result` from the snapshot-unit machinery, never reaches
`execute_command`). Generation 2 raised exactly this as decision ask 4 before
writing any code, and it was right.

### Acceptance, with file:line evidence

| box | evidence |
|---|---|
| a decision on whether the side-run should resolve infrastructure | A-416 — yes, and it already did on the shipped path |
| the two parameters threaded | `src/assay/runner.py:828` (`execute_command`'s signature) and `:884` (forwarded into `resolve_command_plan`); `src/assay/canary.py:188` (`_run_pipeline`), `:225` (`run_python_canary`), forwarded at both `_run_pipeline` call sites |
| both default to `None` | asserted mechanically by `test_the_legacy_standalone_canary_forwards_the_same_two_parameters` via `inspect.signature`, so no existing caller changes |
| the docstring no longer states a defect that is not live | `src/assay/runner.py:840-870`; the same test asserts `"accepts no" not in doc` |
| a CLI-driven test with a resolvable `derived:` fact | `tests/test_r3_canary_sees_infrastructure.py::test_an_r3_lane_with_a_resolvable_derived_fact_judges_its_canary` |
| the R3 claim is PASS/FAIL, never `ERROR`/`BAD_LANE_CONFIG` | the same test asserts `status == "PASS"` and `reason_code is None` |
| documented for consumers | `docs/CONSUMERS.md`, the infrastructure section: both halves of an R3 canary run with the resolved facts |
| no wire change | nothing under `verdict.py`, `verify.py`, `src/assay/schemas/` or the drift-guard |

Neighbouring-suite check: `test_r3_canary_sees_infrastructure`,
`test_canary_python_pipeline`, `test_runner_run_lane_r3`,
`test_canary_p23_isolated_edges` — **23 passed in 75.68s**.

### What a reviewer should push on

- **The regression guard is labelled, not disguised.** There is no red-first
  transcript for B029 and this REPORT says so. If R-1 believes a red proof is
  reachable, the shape would be a lane that drives `run_python_canary`
  directly with a `derived:` fact — that IS red before this commit, and was
  left out because DA-R6 asked for the CLI-driven lane test and the
  signature/docstring check, not for a second harness around a legacy path.
- **`infrastructure_environment` is threaded but never exercised with a
  non-`None` value in these tests.** Only `infrastructure_source` is, via
  the CLI's own `ciu.global.toml` resolution. The pair is passed together
  everywhere it exists, so this is a completeness gap, not a correctness one.
- **The measurement used one mechanism** (`import-break`). A second
  mechanism (`uncovered-line`, which needs R1 declared alongside) would
  exercise `_run_pipeline`'s R1 half; that path takes no infrastructure at
  all, by design, since `evaluate_r1` reads artifacts rather than running a
  command.

### What I did NOT do, and why

- **I did not close B029 as not-a-defect.** `run_python_canary` is public
  API and its docstring named the defect in assay's own words; a second,
  poorer infrastructure world reachable from a public function is a trap
  whether a lane takes it today or not. DA-R6 says so explicitly.
- **I did not touch `run_isolated_canary`.** It was measured correct.

## B024 — wiring pyflakes/ruff into the registered gate (DA-D15): MEASURED, NOTHING LANDED

**Ruling applied — its escape hatch.** DA-D15: "inside the image if the tools
are there, else inside `run-venv` from the offline wheelhouse if the closure
already carries them; **if neither is possible without a network fetch, write
the decision ask and land nothing** (A-198's hash-bound closure is not to be
loosened for a linter)."

### The three checks, in BRIEF-2 §5's order

1. **The image.** `docker run --rm tester-unified:local sh -lc 'python -m
   pyflakes --version; ruff --version; python -m ruff --version'` →
   `No module named pyflakes` / `ruff: not found` / `No module named ruff`.
   Neither tool, by either invocation.
2. **The offline wheelhouse.** `gate/distribution/build-wheelhouse/` holds
   five wheels — `packaging-26.3`, `setuptools-84.0.0`,
   `setuptools_scm-10.0.5`, `vcs_versioning-2.2.4`, `wheel-0.47.0` — and
   `gate/distribution/build-requirements.txt` pins exactly those five with
   `--hash=sha256:`. No linter.
3. **Whether there is any other ingress.** There is not:
   `tools/tester-unified-gate.sh:53-56` and `:82` install
   `--no-index --find-links "$distribution/build-wheelhouse"`, and `:126`
   installs the built wheel `--no-index --no-deps`.

**Therefore neither route is possible without a network fetch**, and the
third possibility — adding the tool to the image — is a change to
`tester-unified/Dockerfile`, which is **outside `assay/**`** and which this
wave's implementer is explicitly forbidden to touch. B024's own 2026-08-25
note already records why that is a cross-project change: the image's closure
is derived from ciu/cmru/topos/nyxloom/cgroup-profiler's extras and every one
of them gates through `tester-unified:local`.

**Nothing was landed.** No phase in `tools/tester-unified-gate.sh`, no line in
`build-requirements.txt`, no wheel in `build-wheelhouse/`. The backlog entry
carries the transcript; the acceptance box stays unticked and is annotated
BLOCKED.

### Decision ask (B024 / DA-D15)

> `pyflakes` and `ruff` are in neither the `tester-unified:local` image nor
> the hash-bound offline wheelhouse, and the gate has no other ingress
> (`--no-index` everywhere). Three ways forward, all of which need a ruling
> the implementer is not allowed to take:
>
> **(a) Add `pyflakes` to the shared image.** `tester-unified/Dockerfile` is
> outside `assay/**` and shared by five other products' gates; a rebuild
> re-risks every one of them. This is what B024's own filing proposed, and it
> is a controller/operator change, not an assay one. `pyflakes` has zero
> dependencies, which matches assay's own purity bar (A-005) and makes the
> image delta minimal.
>
> **(b) Add `pyflakes` to assay's own build wheelhouse.** One fetch, one
> hash, one committed wheel, one line in `build-requirements.txt` — the same
> shape A-198/P24 already established for the five build wheels, and no other
> project is touched. The question is whether A-198's closure is *for* the
> build only, in which case a linter does not belong in it, or *for whatever
> the gate needs offline*, in which case it does. DA-D15's wording ("if the
> closure already carries them") suggests the former; the mechanism does not
> care.
>
> **(c) Run the linter outside the gate** — a pre-commit hook, or a cmru
> step — accepting that it is then not part of the registered gate's verdict.
> This is the option B024 exists to reject: nothing in the gate runs a linter
> at all, which is how two `NameError`-on-every-call defects shipped.
>
> **Recommendation, offered not taken: (b).** It is confined to `assay/**`,
> it costs one wheel, it keeps `--require-hashes` intact (the closure stays
> hash-bound; it simply gains a member), and it makes the lint phase a real
> part of the gate's verdict, which is the whole of B024's ask. (a) is
> strictly larger blast radius for the same outcome; (c) does not answer the
> filing.

### What a reviewer should push on

- **Verify the negative.** R-1's stated push is "plant an unused import and
  expect the gate to go red". With nothing landed, the gate will NOT go red,
  and that is the correct current state — but a reviewer should confirm the
  three checks above rather than take them on trust, because "nothing landed"
  is exactly the shape a skipped item also has.
- **Is `ruff` reachable another way?** It is a single static binary with no
  Python dependency; a vendored binary in the repo would be a different kind
  of closure question (a committed executable rather than a hashed wheel).
  That was not explored, because DA-D15 names the wheelhouse and the image
  and nothing else.

---

# Generation 4

## B024 — the gate lints its own source (DA-D15 → **DA-R7** → A-417)

The decision ask above was ruled by the controller as **DA-R7**: option (b),
**pyflakes only**, in its **own** closure. Options (a) (the shared image) and
(c) (outside the gate) were rejected; `ruff` was dropped from DA-D15's
original "pyflakes and ruff". This section records what was built against
that ruling and what was measured while building it.

### The sweep was re-run first, not inherited

B024's original sweep is from 2026-08-25, and DA-R7 explicitly warns it may
have drifted. It had not — for `src/assay`. Measured 2026-09-02 with the
same `pyflakes==3.4.0` the gate now installs, from `assay/`:

| tree | findings | note |
|---|---|---|
| `src/assay` | **0** | the tree the phase judges |
| `gate/` | **0** | measured, but out of DA-R7's stated scope; not wired |
| `tests/` | **31** across 19 modules | plus one non-finding, below |

```
$ python -m pyflakes tests | grep -cE '^tests/[^ ]+\.py:[0-9]+:[0-9]+: '
32
$ python -m pyflakes tests | grep -E '^tests/[^ ]+\.py:[0-9]+:[0-9]+: ' \
    | grep -v '^tests/fixtures/' | wc -l
31
$ … | cut -d: -f1 | sort -u | wc -l
19
```

The 32nd line is `tests/fixtures/mutation/python/broken.py:8:12: invalid
syntax` — a **deliberately** unparseable fixture the mutation suite needs in
order to prove how assay reports a source file it cannot parse. pyflakes can
never pass over it, so a `tests/` phase would need an explicit
`tests/fixtures/` exclusion as well as a 19-file sweep. DA-R7's "and `tests/`
if clean" therefore resolves to **not `tests/`**, and the sweep is filed as
**B062** with the per-class breakdown (25 unused imports, 5 unused locals, 1
redefinition; **no undefined names**, so none is a latent `NameError` of the
class that filed B024).

### Acceptance, with file:line evidence

| DA-R7 clause | evidence |
|---|---|
| `gate/distribution/lint-requirements.txt` with `pyflakes==<pin> --hash=sha256:…` | the file, one line: `pyflakes==3.4.0 --hash=sha256:f742a7dbd0d9cb9ea41e9a24a918996e8170c799fa528688d40dd582c8265f4f` |
| `gate/distribution/lint-wheelhouse/` holding that one pure-Python wheel | `pyflakes-3.4.0-py2.py3-none-any.whl`, 63551 bytes; asserted against the manifest and the pin by `tests/test_distribution_gate.py:577` |
| fetched ONCE here with `pip download pyflakes --no-deps -d …` | transcript below |
| sha256 recorded in the requirements file AND in a manifest beside `build-wheelhouse-manifest.json` | `gate/distribution/lint-wheelhouse-manifest.json`, `schema_version: 1`, same shape as the build manifest |
| installed `--no-index --require-hashes` into a THIRD venv, `lint-venv` | `tools/tester-unified-gate.sh:84-101` (`build_lint_venv`), asserted by `tests/test_distribution_gate.py:604` |
| never `build-venv` or `run-venv`; A-198's five-wheel assertion byte-for-byte untouched | `build_offline_closure_venvs` (`:47-73`) is unchanged in this commit's diff; `tests/test_distribution_gate.py:619-621` asserts it mentions neither `pyflakes` nor `lint` |
| `python -m pyflakes src/assay` | `tools/tester-unified-gate.sh:119` — over `$scratch/clone/assay/src/assay` |
| as a phase AFTER the suite | called at `:623-624`, after `run_self_hosted_lane` and `run_independent_witness`; ordering asserted by `tests/test_distribution_gate.py:630` |
| its own `ASSAY_GATE_PHASE` marker | `:121`, `ASSAY_GATE_PHASE=pyflakes-clean` |
| pyflakes' whole rule set is the F-rule set | stated in the A-row and at `tools/tester-unified-gate.sh:106-109`; there is no rule-selection flag and no config file, so there is nothing to drift |
| ruff dropped | A-417's rejected-alternatives column |
| sweep any pyflakes findings in `src/assay` first | 0 findings; nothing to sweep (table above) |
| a planted unused import must turn the gate red — prove it once in a scratch run and record the transcript | transcript below, plus `tests/test_distribution_gate.py:641` and `:671` as permanent tests |

### Transcript — the fetch (this devcontainer has a network; the gate never will)

```
$ pip download pyflakes --no-deps -d <scratch>/pyflakes-dl
Collecting pyflakes
  Downloading pyflakes-3.4.0-py2.py3-none-any.whl.metadata (3.5 kB)
Downloading pyflakes-3.4.0-py2.py3-none-any.whl (63 kB)
Saved <scratch>/pyflakes-dl/pyflakes-3.4.0-py2.py3-none-any.whl
Successfully downloaded pyflakes

$ sha256sum pyflakes-3.4.0-py2.py3-none-any.whl
f742a7dbd0d9cb9ea41e9a24a918996e8170c799fa528688d40dd582c8265f4f
$ stat -c %s pyflakes-3.4.0-py2.py3-none-any.whl
63551
$ python -c "<read the wheel's own METADATA>"
Requires-Python: >=3.9
Requires-Dist: None          # no dependencies: the closure is one file
```

### Transcript — the planted unused import turns the phase red

Run against the gate's **own** functions, sourced from the real script (the
entry-point dispatch stripped, and the hardcoded `/opt/tester-venv/bin/python`
swapped for this cockpit's interpreter — the same substitution
`tests/test_distribution_gate.py`'s `gate_functions` fixture makes), with a
real copy of `src/assay` standing in as the private clone:

```
$ build_lint_venv <scratch>/b024scratch <worktree>/assay/gate/distribution
Processing ./gate/distribution/lint-wheelhouse/pyflakes-3.4.0-py2.py3-none-any.whl
  (from -r <worktree>/assay/gate/distribution/lint-requirements.txt (line 1))
Installing collected packages: pyflakes
Successfully installed pyflakes-3.4.0

--- CLEAN RUN ---
$ run_lint_phase <scratch>/b024scratch
ASSAY_GATE_PHASE=pyflakes-clean
CLEAN_EXIT=0

--- PLANTED UNUSED IMPORT IN verdict.py ---
$ run_lint_phase <scratch>/b024scratch
<scratch>/clone/assay/src/assay/verdict.py:1:1: 'os' imported but unused
<scratch>/clone/assay/src/assay/verdict.py:57:1: from __future__ imports must occur at the beginning of the file
tester-unified-gate: pyflakes reported findings in src/assay (see the lines above)
PLANTED_EXIT=1
```

Note the second line of the red run: planting `import os` at the top also
displaced the module's `from __future__ import annotations`, so pyflakes
reported two findings, not one. That is incidental to the proof — the phase
`die`s on ANY finding — but it is what the transcript actually shows, so it
is recorded rather than trimmed.

### Transcript — red-first

Written before the wiring, proven red in a **detached scratch worktree** at
the branch tip `b90ca598` (the shared stack forbids a bare `git stash`):

```
$ git worktree add --detach <scratch>/b024red HEAD
Preparing worktree (detached HEAD b90ca598)
$ cp assay/tests/test_distribution_gate.py <scratch>/b024red/assay/tests/
$ cd <scratch>/b024red/assay && python -m pytest tests/test_distribution_gate.py \
    -q -p no:randomly -k "lint or pyflakes or planted or undefined_name or shipped_source"
FAILED …::test_lint_requirements_pin_the_wheels_that_are_actually_committed
FAILED …::test_the_lint_closure_is_a_third_venv_and_never_the_build_or_run_venv
FAILED …::test_the_lint_phase_runs_after_the_suite_and_marks_itself
ERROR  …::test_a_planted_unused_import_reddens_the_lint_phase
ERROR  …::test_an_undefined_name_reddens_the_lint_phase
ERROR  …::test_the_shipped_source_tree_is_pyflakes_clean
3 failed, 15 deselected, 1 warning, 3 errors in 0.73s
$ git worktree remove --force <scratch>/b024red
```

The three ERRORs are the `lint_venv` fixture: at `HEAD` there is no
`build_lint_venv` to call and no wheelhouse to install from. Green on the
branch:

```
$ python -m pytest tests/test_distribution_gate.py -q -p no:randomly
21 passed, 1 warning in 27.59s
$ python -m pytest tests/test_distribution_gate.py \
    tests/test_docs_examples_and_vocabulary.py \
    tests/test_distribution_build_release.py -q -p no:randomly
92 passed, 1 warning in 54.79s
```

### Design choices inside the ruling, and why

DA-R7 fixes the shape; three sub-decisions were still open, and each is
recorded here because a reviewer will (rightly) ask about it.

1. **It lints the private exact-OID clone, not the bind-mounted worktree.**
   Same reason A-198 builds the wheel from the clone: a gitignored stray
   `.py` in the caller's tree must be able neither to redden the phase nor to
   launder it. The alternative — `$worktree/assay/src/assay` — is the same
   bytes in the normal case and silently is not in exactly the case a gate
   exists for.
2. **The phase runs after the suite, not before it.** DA-R7 says "AFTER the
   suite" and this is not a reinterpretation, but the cost is real: a lint
   failure now costs a full container run to discover. That is the right
   trade — a linter that reddens the gate before the tests have spoken buys a
   five-second answer at the cost of the one the reviewer needs — and it is
   why `tests/test_distribution_gate.py:695` brings the same assertion
   forward into the ordinary suite, where `pytest tests` catches it in
   seconds.
3. **`gate/` was measured clean but is NOT linted.** DA-R7 names
   `src/assay`. Widening on my own initiative would put un-ruled scope into a
   commit whose whole subject is a ruling; the measurement is recorded here
   and in the script's own scope comment so a later widening starts from a
   number rather than a guess.

### Docs disposition

| doc | change |
|---|---|
| `docs/DESIGN-GUIDE.md` §14 | "Two venvs, not one" → "Two venvs for the wheel, not one", plus a new "And a third for the linter (B024/A-417)": why neither existing venv was the right home, the one-file closure, the clone-not-worktree and after-the-suite choices, and that the scope is a measurement |
| `CHANGES.md` | a new `### Changed` bullet under `[Unreleased]` |
| `README.md` | untouched — the gate's internal phases are not a consumer-facing surface, and README's gate paragraph does not enumerate phases |
| `docs/CONSUMERS.md` | untouched — nothing a consumer of assay's verdicts can observe changed |

### What a reviewer should push on

- **Plant the import for real.** The transcript above and
  `tests/test_distribution_gate.py:641` both exercise `run_lint_phase`
  directly. Neither runs the whole container. R-1's own push — commit an
  unused import into `src/assay` and run the registered gate end to end —
  is still worth doing once, and is the only proof that the call site at
  `:623-624` is reached on a run where every earlier phase passes.
- **Check that `build-venv` really is untouched.** The claim "A-198's
  five-wheel closure assertion is byte-for-byte what it was" is a claim about
  a diff; read `git show <commit> -- assay/tools/tester-unified-gate.sh` and
  confirm the only changes are additions.
- **Check the wheel's provenance.** The committed wheel was fetched from
  PyPI on this devcontainer. A reviewer with a network can re-download
  `pyflakes==3.4.0` and confirm the sha256; a reviewer without one can at
  least confirm the manifest, the pin line and the file on disk agree
  (`tests/test_distribution_gate.py:577` does exactly this, which is the
  point of that test).
- **`lint-venv` is built inside the container with `--network=none`.** The
  pip install is `--no-index --find-links` against a committed directory, so
  it cannot reach out — but the `python -m venv` that precedes it must
  bootstrap pip from `ensurepip`'s bundled wheels. That already works twice
  in this script (`build-venv`, `run-venv`), so a third is not a new risk;
  the registered gate run is what proves it.

### What I did NOT do, and why

- **Did not add `ruff`.** DA-R7 drops it. Recorded in A-417's rejected
  alternatives with the reason (a ~10 MB platform-specific binary wheel for a
  rule set that is a re-implementation of pyflakes' entire rule set).
- **Did not sweep `tests/`.** 31 findings across 19 modules, two of whose
  three classes need per-site judgement rather than deletion. Filed as B062
  with acceptance criteria.
- **Did not lint `gate/`.** Measured clean; out of DA-R7's stated scope.
- **Did not touch `tester-unified/Dockerfile`** or anything outside
  `assay/**`.
- **Did not add a baseline or ratchet.** B024's own "Suggested approach"
  offered one; with `src/assay` clean it would record nothing and could only
  become a place for findings to hide. Recorded as a rejected alternative in
  A-417.

## Generation 4 — gate transcript

**GATE-VERIFIED COMMIT: `7c9e8dd1`.** One run, first try, green, with B024's
new phase present and running last. Verdict read in a SEPARATE step from the
log's own markers:

```
COMPLETE_MARKERS=1          (ASSAY_REGISTERED_GATE_COMPLETE=1, exactly one)
GATE_EXIT=0
BAD=0                       (grep -c -E 'FAILED|DIRTY_TREE|Traceback')
Created wheel for assay: filename=assay-4.1.1.dev16+g7c9e8dd1-py3-none-any.whl
  size=526691 sha256=cc1116d7591e3f5f7c35bee40ed5c5e439f5453c3070dfed48edb4c881c147b4
tester-unified: PASS (exit 0)
  commit: 7c9e8dd142cfb8c6057655218846fa3aed680c5c
ASSAY_GATE_PHASE=wheel-installed
ASSAY_GATE_PHASE=attestation-hardened
ASSAY_GATE_PHASE=verdict-v5-accepted
ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
ASSAY_GATE_PHASE=verdict-v6-v7-v8-hard-cut-verified
ASSAY_GATE_PHASE=verdict-v9-successors-verified
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_GATE_PHASE=pyflakes-clean
```

`ASSAY_GATE_PHASE=pyflakes-clean` is the end-to-end proof the scratch
transcript could not give: the phase runs in the real container, from the
committed one-wheel closure, after every other phase, on a run where all of
them passed. The wheel name carries the judged commit.

**No whole-suite run preceded this gate**, under the host-load throttle
(below). The targeted runs were `tests/test_distribution_gate.py` (21 passed)
and that module plus `test_docs_examples_and_vocabulary.py` and
`test_distribution_build_release.py` (92 passed). Generation 3's last
whole-suite figure was 3985 passed / 20 skipped; this generation adds 6 tests
to `test_distribution_gate.py`, so the expected figure at the next
whole-suite run is **3991**. That is an arithmetic expectation, **not a
measurement** — generation 5 must measure it, not quote it.

## Generation 4 — the host-load throttle (controller directive, binding)

The host's 1-minute load reached **85 on 8 cores** and degraded a production
game server sharing the host, from this wave's own concurrent work (R-1's
pytest plus a gate container). The controller capped the running gate
container to 3 CPUs live; it completed green, slower. Measured recovery
across the rest of the run: **85 → 10.28 → 7.45** (1-minute average).

Binding for the rest of the wave: never `pytest -n`/xdist (serial only,
`nice -n 19 ionice -c 3`, targeted files, whole suite at most once per
checkpoint); never two gate containers at once (check `docker ps | grep
tester-unified` is empty first, then
`docker update --cpus=3 $(docker ps -q --filter ancestor=tester-unified:local)`
right after launching); no build/pip/wheel step concurrent with a suite run.

## Generation 4 — B004's ciu assets, RE-CAPTURED (DA-D7), with the measured delta

DA-D7 requires the W2 frozen ciu assets to be re-captured before anything is
built against them, on the stated ground that ciu 7.10.1 emits
`schema_version: 2`. Done. **The delta is real but much smaller than the
ruling assumes, and one clause of the ruling's premise is wrong.**

### Transcript — the capture

```
$ cd /workspaces/dstdns && ciu version
ciu 7.10.1
$ ciu provenance --json > <scratch>/ciu-provenance.json 2> <scratch>/ciu-provenance.err
EXIT=2                    # stderr EMPTY; exit 2 is the mismatch signal, not an error
$ sha256sum <scratch>/ciu-provenance.json
e7fa23dab5cc5e08e2d8156c82a16c2f4ed2742c9b9657805c96508ba68765af
$ wc -c
3512
```

Frozen in the repository as
`nyxloom-trove/carve-assets/W2/ciu-provenance-live-mismatch-ciu-7.10.1.json`
— a **new** asset beside the 6.0.3 ones, never a rewrite of them, which is
that MANIFEST's own stated rule. The full delta table is in the MANIFEST
addendum; the summary:

| | frozen 6.0.3 asset | live 7.10.1 capture |
|---|---|---|
| `schema_version` | `1` | **`2`** |
| top-level keys | `schema_version`, `instance`, `commit_under_test`, `tree_state`, `containers`, `overall` | **identical** |
| per-container keys | `name`, `image`, `labelled_revision`, `status` | **identical** |
| containers | 20 | 20 |
| statuses | 16 `unlabelled`, 4 `mismatch` | **16 `unlabelled`, 4 `mismatch`** |
| `overall` | `mismatch` | `mismatch` |
| `instance` | `dstdns-98535c` | `dstdns-98535c` |

**The correction.** The wave prompt says ciu 7.10.1 "now emits
`schema_version: 2` with an `unlabelled` container status", which reads as if
`unlabelled` arrived with the bump. It did not: the frozen **6.0.3 /
schema-1** asset already carries sixteen `unlabelled` entries. Measured
directly by parsing both documents. So the adjudicator's status vocabulary
needs no widening for the observed set; the ONLY schema-relevant change is
the version integer.

Everything else that moved is a fact about dstdns and its vendor images, not
about ciu's document shape: `commit_under_test` `016a2674` → `a9b10791`;
four `labelled_revision` values changed as upstream images moved
(`otel-aggregator`/`otel-collector-node` `1400269f…` → `f8178323…`,
`skywalking-ui` `9fc54aa1…` → `19ca126d…`); six `image` fields flipped
between a tag and a bare image id; one container swapped in dstdns's own
compose (`webapp-ui` → `pwmcp`).

### What this changes for the carve, and what it deliberately does not

W2 §5.4's refusal table (carve line 230) makes `schema_version` being the
integer `1` a PASS condition and anything else `ERROR`/`FORMAT_MISMATCH`.
That is the one place the bump bites, and it is a **product call** the carve
does not answer: accept `{1, 2}`, accept `2` only, or accept a
lane-declared version. **I did not decide it** — see the decision ask below.

**Still true, and still B004's real blocker:** `overall` is pinned at
`mismatch` on this host for the reason the 6.0.3 note gives (ciu compares
every running container's `org.opencontainers.image.revision` against its own
repository's short hash, and the labelled ones carry upstream vendor
revisions). The green-path oracle still has no live-host witness;
`ciu-provenance-green-reference.json` remains the only real `verified-match`
document, producer-pinned by ciu's own test.

### Decision ask (B004 / DA-D7, for the controller)

> W2 §5.4 refuses any `schema_version` that is not the integer `1`. ciu now
> emits `2`, and the two documents are otherwise structurally identical on
> this host (same keys, same status vocabulary, same `overall`). Should the
> adjudicator (a) accept `{1, 2}` and refuse anything else, (b) accept `2`
> only — a hard cut matching assay's own hard-cut doctrine for its verdict
> schema, at the cost of refusing any consumer still on a ciu 6.x host, or
> (c) take the accepted version from the lane declaration? The carve names no
> rule for a bumped producer version, and A-334 forbids me inventing one.

## Generation 4 — what is NOT done, and why

- **Phase 2 was not started.** No A-row for B050/B051/B052/B053-`detail`/
  B004/B007/F015 exists yet; `verdict.py`, `verify.py`, `src/assay/schemas/`
  and the drift-guard carve-assets are untouched and no commit carries `!`.
  The branch is still releasable on v9. The seams generation 4 located for
  that work are in BRIEF-4 §5, so generation 5 does not re-derive them.
- **R-1's round-1 fix package was not started.** It arrived from the
  controller while this generation's gate was running, with the explicit
  instruction to cut rather than begin it if at/past the E-008 threshold —
  which generation 4 was. It is BRIEF-4's first work item, and R-1's report
  is now in the repository verbatim at
  `nyxloom-trove/reports/assay-WAVE-D-v10-REVIEW-R1-round1.md`.
- **The ciu `schema_version` question was not decided.** Decision ask above.

---

# Generation 5 — R-1 round 1's fix package

Commits: `e44c1056` (R-1's FINAL report, verbatim), `8895ffbf` (the fix
package, A-418..A-424), `e3ae8ada` (B063), plus the checkpoint records commit.
**FIX-TIP for R-1 round 2: `e3ae8ada`**, gate-green, reviewed as
`93188912..e3ae8ada`.

Phase 2 was NOT started. This generation reached its E-008 checkpoint at the
green gate on the fix tip, which is the coherent boundary the clause names.

## BLOCKER 1 (DA-R8, A-418) — the POST-command guards announce

### The red proof

`_refusal_lines()` — the module's own predicate, R-1's
`grep -cE '^assay: [A-Z_]+/[A-Z_]+: '` minus B018's judge-provenance notice.
Detached scratch worktree at `e44c1056` (never a stash), the three new tests
copied in, product code untouched:

```
FAILED tests/test_refusal_announcement.py::test_a_command_that_dirties_the_tree_blames_the_command_on_a_direct_r0_lane
FAILED tests/test_refusal_announcement.py::test_a_command_that_moves_head_names_both_revisions_on_a_direct_r0_lane
FAILED tests/test_refusal_announcement.py::test_a_command_that_dirties_the_snapshot_blames_the_command_on_an_r1_lane
3 failed, 17 deselected, 1 warning in 1.29s
```

Every one failed identically on `assert len(lines) == 1` → `assert 0 == 1`:
the silence R-1 measured, reproduced from the branch's own tests. Green after
the fix: `tests/test_refusal_announcement.py` **20 passed**.

### The sentence, and why it is not the pre-run one

`runner.post_command_refusal` (`runner.py:352`) composes both, so the two
dispatch paths cannot drift. DIRTY_TREE:

> the lane's own command left N uncommitted file(s) in `<repo>` — assay
> observed that tree CLEAN at `<commit>` immediately before starting the
> command, so this dirt is the command's own output and re-running after a
> commit or a stash reproduces it. Point the command's output at the artifact
> path the lane declares (assay reserves it for exactly this), or at a
> gitignored path, so what assay measured stays the commit it judges.
> Affected: `<paths>`

HEAD_CHANGED:

> the lane's own command moved HEAD in `<repo>` from `<pre>` to `<post>` —
> assay judges the commit it resolved BEFORE the command ran, never one the
> command created while it ran, so the verdict would name a commit that is no
> longer what this repository is at. Remove the commit step from the lane's
> command.

The tests assert `"the lane's own command"` and, for DIRTY_TREE,
`"commit or a stash reproduces it"`, so an edit that reintroduces the pre-run
remedy here fails.

### What did NOT change, and the three claims

The A-178 observation order is untouched on both paths: exactly one
`dirty_paths` call, `head_rev` ONLY on the clean branch, dirt keeping
precedence. The only change is that the two facts are KEPT instead of
evaluated for truthiness and dropped. No claim, no reason code, no wire field
moved.

The three claims R-1 named — `CHANGES.md:32`,
`tests/test_refusal_announcement.py`'s DA-R3 section header and
`runner.py`'s DA-R3 comment — **stay as written and are now true**, per DA-R8
("no narrowing").

## BLOCKER 2 (A-419) — a guard that can go red

The replacement asserts the resolved VALUE, at both ends of the threading:

- `runner.execute_command(...)` → `result.plan.env_effective[FACT] == VALUE`
  **and** `result.outcome is Outcome.PASS`, where the child is
  `python -c "import os; import pkg.mod; assert os.environ[FACT] == VALUE"` —
  so PASS is itself evidence the value reached the process.
- `canary.run_python_canary(...)` → `control_outcome is PASS` (the control
  half runs that same argv), `transformed_outcome is FAIL`,
  `observed_reason_code is COMMAND_FAILED`.

The lane declares `ASSAY_B029_FACT = "derived:deploy.cgroup_parent"` against a
real, gitignored `ciu.global.toml` written after the seed commit (A-293's
shape), so the fact must genuinely resolve.

**Red proof, detached scratch worktree at `e44c1056`, one mutant at a time:**

| mutant | what was deleted | result |
|---|---|---|
| unmutated | — | **3 passed** |
| `m1` | the two forwards in `runner.execute_command`'s `resolve_command_plan` call | **2 failed, 1 passed** |
| `m2` | the two forwards in `canary._run_pipeline`'s `execute_command` call | **1 failed, 2 passed** |
| `m1`+`m2` | both | **2 failed, 1 passed** |

The failure in every case is
`AssayError: lane declares a derived infrastructure fact but no
infrastructure_source was supplied to resolve it against`, raised out of
`resolve_command_plan` — exactly the misattributed refusal B029 filed, now
reachable from a test rather than only from a prediction.

`m2` alone failing ONLY the canary test is the part worth recording: the two
halves of the threading are independently guarded, which a single combined
mutant could not show.

## SF-1 (DA-R9, A-420) — and the field that was unavailable

**DA-R9's contingency clause fired, and the escape point is EARLIER than the
ruling assumed.** Measured by wrapping `LaneDeadline.remaining` and printing
the stack on the raise, `budget = "0.001s"`, R0-only lane through
`assay.cli.main`:

```
  File ".../src/assay/cli.py", line 289, in main
  File ".../src/assay/cli.py", line 596, in _cmd_run
  File ".../src/assay/cli.py", line 680, in _run_reserved
    commit = git.head_rev(lane_file.project_root, remaining=deadline.remaining)
  File ".../src/assay/git.py", line 564, in head_rev
  File ".../src/assay/git.py", line 463, in run
  File ".../src/assay/git.py", line 510, in _run_bytes
  File ".../src/assay/git.py", line 483, in _run_raw
  File ".../src/assay/git.py", line 418, in _resolve_repo
  File ".../src/assay/git.py", line 268, in _run_bounded
  File ".../src/assay/git.py", line 198, in _sample_remaining
EXIT 4
```

That is `cli.py`'s OWN `head_rev`, before `commit` exists and before
`run_lane` is entered at all. A handler placed only around `runner.run_lane` —
DA-R9's letter — reads correct and measures red: with it alone, both `0.001s`
tests still failed with *"the reserved --verdict-json was never written: exit
4"*.

**THE FIELD THAT WAS UNAVAILABLE: `Verdict.commit`.** DA-R9 says "the verdict
carries what is known and the REPORT records exactly which field". It is
`commit`, and the choice made — recorded here so the controller can overrule
it — is to **read** it rather than carry a placeholder:

```python
try:
    commit = git.head_rev(lane_file.project_root)   # no deadline
except AssayError:
    raise exc from None                             # the ORIGINAL timeout
```

Reasoning, in the code as a comment and here: a commit label is an IDENTITY,
not a measurement; `budget` bounds the lane's work, and the
artifact-production tail (`write_verdict`, the summary) already runs past the
deadline on every timed-out lane; and a fabricated or sentinel label is the
one thing this project must never emit. If that read fails in turn, the
original timeout propagates unchanged and `main()`'s handler owns the case
exactly as before.

**Both handlers are scoped to `LANE_TIMEOUT` alone.** A test asserts that a
non-timeout `AssayError` out of `run_lane` still propagates and writes NO
artifact: a bug laundered into a verdict is the silent green this project
exists to remove.

**The second handler (around `run_lane`) is DA-R9's literal instruction and is
kept**, because a deadline can still expire inside `run_lane` above its own
two catches (`git.repo_top`). That window is real but milliseconds wide, so
racing for it would be a flaky test; it is exercised by injecting the raise at
that exact seam (`monkeypatch.setattr(runner, "run_lane", …)` raising the real
`BUDGET_EXCEEDED`/`LANE_TIMEOUT` pair). That is a stub of assay's OWN function
— **A-334 is about test doubles standing in for EXTERNAL systems**, and the
thing under test here is the CLI's handler, with the exception built from the
real `LaneDeadline` vocabulary.

`tests/test_lane_timeout_writes_a_verdict.py`: **12 passed**; the two `0.001s`
parametrisations were red before the fix with the exact message above.

## SF-2 / SF-3 / SF-4 / SF-5

- **SF-2 (A-421).** `runner.py:4045` `except OSError as exc:` now composes
  `AssayError(f"snapshot preparation or cleanup failed: {exc}", ERROR,
  GIT_FAILED)` and announces it at `:4052`. The `except RuntimeError` beside
  it is untouched — it deliberately re-raises when there is no outcome, which
  R-1 agreed is right.
- **SF-3 (A-422).** `_report_probe_refusal` (`runner.py:509`) now calls
  `announce_refusal` instead of printing the format string by hand. The
  probe's `stderr`/`stdout` tails stay as indented `  probe stderr: …` context
  lines BELOW the one line — folding them into the message would break the
  one-refusal-one-line contract the counting tests assert.
  `tests/test_environment_preflight.py` green.
- **SF-4 (A-423).** `mutation.py:1647` `try`/`finally`: both `close()` calls
  now run even when `equivalence_reservation.consume()` raises on B049's new
  path.
- **SF-5 (A-424).** KEPT, and **documented as insurance, not covered code**,
  in both docstrings (`statement_attribution.py:243`, `:364`) and here:
  **no real artifact reaches either carry today.** Both rebuilds are behind
  `if file_cov.blocks is None: continue`; only `go-cover` populates `blocks`,
  only `coverage-istanbul-json` populates `contradictory_branch_lines`. R-1's
  mutant `m5` deletes both carries and the statement-attribution and
  istanbul-contradictory modules stay green (41 passed, identical to the
  unmutated baseline). Nobody should read these two lines as measured.

## B063 — filed, not fixed

R-1's measurement, recorded verbatim in the entry: `test_python_qualification`
(`:42`), `test_runner_snapshot_selection` (`:805`) and
`test_distribution_build_release` compute `REPO_ROOT = PROJECT_ROOT.parent`
and `git -C` against it, so any copy of the tree outside the vbpub checkout
contributes a **constant 11 failures and 13 errors** regardless of the copy's
contents — the noise floor R-1's mutation sweep had to read its evidence
against. Id note in the LOG: the controller's package text said "B062", which
was already taken on this branch; B063 is the correct id and the controller
had itself corrected to it.

## Gate

GREEN on `e3ae8ada`, first try. Markers, wheel and phases in the LOG. Twelve
phases, ending `pyflakes-clean`; both v9 schema phases passed; `tester-unified:
PASS (exit 0)` at `commit: e3ae8ada1c4b00364aa9c3e8e320ea7ee9a40e45`.

## Decision asks for the controller

1. **SF-1's unbounded commit-label read (NEW).** `cli._run_reserved`'s
   pre-`commit` `LANE_TIMEOUT` handler re-reads `git.head_rev` with **no
   deadline** to label the refusal, because at that point the label is exactly
   the fact DA-R9 predicted would be missing. Rejected alternatives: a
   sentinel label (inventing a commit id) and leaving the window unhandled
   (DA-R9's letter met, its purpose not). **If a bounded read is preferred it
   needs a bound to be stated, and there is no non-invented one available
   after the budget is gone** (DESIGN-GUIDE §5) — so this is a product call,
   not an implementation choice. Nothing else in the package depends on it,
   and reverting it costs one `except` block and two test parametrisations.
2. **The ciu `schema_version` ask from BRIEF-4 §4 is ANSWERED** by DA-R12
   (accept the integer set `{1, 2}` through one parser). Recorded so it is not
   counted twice; it is carried into phase 2's A-rows, not into this package.
3. **B063 is filed, not fixed**, per the wave's scope. If the controller wants
   the suite runnable from a copy inside this wave, that is a three-module
   change R-1 would have to re-verify, and it should be said explicitly.

## What a reviewer should push on (this package)

- The post-command guards on a lane whose command does BOTH (dirties AND
  commits): dirt keeps precedence by A-178, so the line must be the
  DIRTY_TREE one, and there must still be exactly one.
- `post_command_refusal`'s two branches over a path containing a space, a
  newline or a non-ASCII byte — `git.dirty_paths` reads `-z` for exactly that
  reason, and the message `', '.join(...)`s the results.
- The `0.001s` tests on a slower or faster host: the assertion is about the
  artifact existing, not about WHERE the deadline fired. Check they are not
  accidentally passing through the `run_lane` handler rather than the
  `head_rev` one — the stack above says which fires today.
- Whether the unbounded `head_rev` in the timeout handler can hang, and
  whether "the artifact tail already runs past the deadline" is a good enough
  argument for it. This is decision ask 1.
- That `_report_probe_refusal`'s fold did not change the emitted text (the
  format string was byte-compatible) and that the tails still print below the
  line, not inside it.
- That the injected-at-the-seam timeout test is not doing the work the two
  `0.001s` tests should be doing — it is a second, narrower case, and the
  `0.001s` pair is the real one.
- That nothing in this package touched `verdict.py`, `verify.py`,
  `src/assay/schemas/` or the drift-guard carve-assets, and that no commit on
  the branch carries `!`. (`git diff --name-only 93188912..e3ae8ada` over
  those paths returns only generation 4's two W2 ciu re-capture files.)

## What I did NOT do, and why

- **Phase 2.** Not started. The checkpoint clause names the green gate on the
  fix tip as a cut boundary, and the controller resumes R-1 round 2 on the
  FIX-TIP word while phase 2 proceeds behind it — which is generation 6's
  work, with BRIEF-4 §5's seam table and DA-R12 already in hand.
- **B063's fix.** Filed only, per the ruling.
- **Splitting the package into seven commits.** The seven fixes interleave in
  `runner.py`, `decisions.md` and `CHANGES.md`; splitting by path would have
  put hunks under commits that did not make them. Each fix carries its own
  A-row, and the LOG names which seam belongs to which.
- **A whole-suite host run.** The registered gate runs it, once, per the host
  throttle. The targeted runs are listed in the LOG.

---

# Generation 6 — A-425, A-426 (SF-6), A-429, the phase-2 design rows, B064

**Gate-verified commit: `bfb55e3f` (GREEN).** Phase 2's CUT was not started.

## What landed, item by item

| commit | item | ruling | A-row |
|---|---|---|---|
| `ba2f1133` | the `LANE_TIMEOUT` commit-label read is bounded | DA-R13 | A-425 |
| `b69a9248` | the last silent terminal in `runner.py` announces | DA-R15 (R-1 round 2's one condition) | A-426 |
| `f254b702` | the gate's own `assay run` carries `--resume --progress`; **design rows** for B050 and B053's `detail`; **B064 filed** | operator directive 2026-09-02 (run-gate SPEC R-38); DA-D6; DA-D2 (c) | A-429; A-427, A-428 |
| `bfb55e3f` | the gate's `assay` stubs read `--verdict-json` from argv | — (A-429 follow-up) | — |

## A-425 — the evidence

- The unbounded call DA-R13 ruled on is gone: `git.head_rev(...,
  remaining=grace.remaining)`, the grace being a `runner.LaneDeadline`
  constructed directly because `LaneDeadline.start` rejects a non-positive
  budget (`runner.py:198-206`) and the grace-expired test needs `0.0`.
- **The bound is observable, which is the whole point.** Same code path, one
  number changed: at the default grace the identical lane writes its verdict
  carrying the real `HEAD`; at `0.0` it writes nothing and says why.
- **Measured red**, in a detached scratch worktree carrying the exact
  pre-A-425 shape (the `remaining=` argument deleted, everything else
  identical): 2 failed / 14 passed, the failure message being
  "a verdict was written without a commit label that could be read".
- No test double: `label_grace_seconds` is a keyword-only parameter with the
  constant as its default, and git, the repository, the lane and the budget
  are all real (A-334).

## A-426 — the evidence, and the measurement DA-R15 asked for

- **Not reachable from `assay run`.** The only `RuntimeError` that reaches
  `_run_higher_rigor_lane`'s handler comes from
  `isolation.prepare_snapshot`'s exit guard (`isolation.py:1800`), which
  fires when a materialization is still open at context exit — i.e. when
  assay's own code leaked one. No lane, argv, tool or repository state can
  cause it. So `CHANGES.md`'s "every refusal reachable through `assay run`,
  with no exceptions" (A-414) was already true, and stays true; SF-6 closes a
  site that is *beyond* that claim rather than a counter-example to it.
- Covered at the seam in the two halves DA-R15 named: the real leak
  (`test_isolation.py`, real repository, no double, pinning the sentence) and
  the handler (`test_refusal_announcement.py`, the same exception class
  carrying that pinned sentence, in through `scratch_root_factory` — assay's
  own cleanup seam, the one A-193/A-194's rule is about).
- The `raise` side is untouched and has its own control test: no line is
  printed and the `RuntimeError` propagates. B053's contract is one line per
  REFUSAL, not one per exception.
- Red-proved against the pre-fix `runner.py`: 1 failed / 21 passed.

## A-429 — and what the gate caught

The change is three tokens in the gate script, and it went **RED** on the
registered gate: 4 failed / 3997 passed. Three `assay` stubs in
`test_distribution_gate.py` wrote their verdict to `"$5"` — the positional
slot the path occupied until the two new flags moved it to `$7`. Worth
recording for the reviewer, because it is the case for gating a three-token
change: a source-reading assertion would have stayed green while the stubs
silently wrote nothing.

Fixed at the cause (`bfb55e3f`): a shared preamble scans the argv for
`--verdict-json` and takes the token after it, which is the real CLI's own
contract. The fixture docstring's positional note had already had to move once
before; it cannot now.

## Phase 2 — where it actually stands

**Designed (A-rows written, no code):** B050's `judgment.r2.fail_under`
(A-427) and B053's `claim.detail` (A-428).

**NOT designed, and therefore blocking the cut:** B004's
`PROVENANCE_UNVERIFIED` + the §5.4 narrowing (DA-D7 + DA-R12), B007's
`targets`/`aggregation`/per-attempt payload (DA-D8), F015's claim shape
(DA-D9). The wave prompt's rule — *"Never write the `!` commit before every
wire change exists as an A-row"* — is why no cut was attempted, and why the
branch is still releasable on v9.

## Decision asks

1. **F015's claim kind is a real product call and I did not make it (DA-D9).**
   A claim is keyed by `rigor`, the enum is exactly `R0..R3`, the ladder is
   ORDERED in shipped code (`_replace_highest_higher_rigor_claim_with_git_failed`
   picks "the highest declared higher-rigor claim"), and `RIGOR_LEVELS`
   (`config.py:145`) canonicalises a lane's declaration against that order.
   Adding `R4` therefore does not merely add a claim kind — it makes
   fail-before/pass-after *the highest tier*, and hence the claim replaced on
   a cleanup failure, ahead of R3. That may well be right (F015 asserts a
   strictly more specific property than R3's canary), but it is a ruling, not
   an implementation detail. The alternative shapes are: a non-ordered claim
   kind, which the `claims`-keyed-by-rigor model does not currently admit; or
   riding R3 with a second `judgment` block, which would put two different
   mechanisms under one claim. **Please rule before generation 7 designs it.**
2. **B007's target-list bound must be MEASURED before it is chosen** (DA-D8),
   and one materialisation is ~2 isolated snapshots per attempt. Generation 6
   did not run that measurement (no worktree writes were possible during the
   three gate runs, and the measurement wants its own quiet host window). It
   is generation 7's first phase-2 act, and it needs a gate-free window under
   the host rule — flagging it so it is scheduled, not squeezed.
3. **B064's B007 coupling is recorded in B064 but not yet in B007's A-row**,
   because B007's A-row does not exist yet. The controller's instruction
   ("note that coupling in B007's A-row, one sentence") is carried forward in
   BRIEF-6 §3 as a required element of that row.

## What a reviewer should push on (generation 6's own work)

- A-425: that `0.0` really exercises the BOUND and not merely an early
  return — delete the `remaining=` argument and confirm the two grace-expired
  tests are the only ones that fail (they were, 2F/14P).
- A-425: that the grace-expired path really writes nothing, including no
  partial file at the reserved path.
- A-426: that the announcement is on ONE side of the fork, by deleting the
  `if outcome_holder:` guard and checking the control test goes red.
- A-429: that the argv-log test would catch a flag being dropped from the
  script (it reads the stub's received argv, not the script's text).
- The two design rows: whether A-428's head-kept truncation and the
  character-bound/byte-bound split are stated precisely enough to implement
  without a second ruling.

## What I did NOT do, and why

- **The v10 cut.** Three of the five wire changes are undesigned; cutting
  before them is the one thing the wave prompt forbids outright.
- **B050/B051/B052/B053-`detail`/B004/B007 implementation.** All ride the cut.
- **F015's design.** Blocked on decision ask 1 — improvising a rigor-ladder
  change is exactly the product call the prompt says to hand back.
- **A whole-suite host run.** Three registered-gate runs covered it; the
  targeted host runs are in the LOG.

---

# Generation 7 — the last three design rows, and B007's measured bound

## B007's target-bound measurement (DA-R17) — the numbers, and how they were taken

Taken as generation 7's FIRST act, exactly as DA-R17 orders, in a gate-free
window: `docker ps --format '{{.Image}}'` listed no `tester-unified:local`
(64 containers, all dstdns/ciu/estate infrastructure), and the host load
average was `3.87 5.30 7.22`. Run once, three iterations, under
`nice -n 19 ionice -c 3`.

**It is not a test double (A-334 does not even apply — the subject is assay's
own substrate, not an external system, but the standard is met anyway).** The
script imports `assay.isolation` from this worktree's `src/` and calls
`prepare_snapshot`, `SnapshotRepository.materialize` and
`SnapshotRepository.materialize_replacement` — the exact three calls
`canary.run_isolated_canary` makes (`canary.py:480`, `canary.py:552`) — against
a REAL repository (this worktree, at `ed287d73`), a real project prefix
(`assay`) and a real tracked target blob (`assay/src/assay/canary.py`,
33,940 bytes). Script kept at `scratchpad/measure_b007.py`.

Two setup facts, recorded because they cost time and will cost the next
person the same:

- `snapshot_selection = "repository"` FORBIDS `unsafe_symlink_omissions`
  (`config.py:753`), and `_build_manifest` (`isolation.py:1545`) refuses a
  symlink whose target is absolute. vbpub's HEAD carries three of them, all
  in `topos/tests/fixtures/inspect_files` (`_danger/passwd_link`,
  `cgroup_escape/.../passwd_escape`, `cgroup_nonreg/.../memory.current`) —
  found with `git ls-tree -r HEAD` filtered on mode `120000` and an absolute
  blob body. The measurement therefore ran under
  `"repository-minus-unsafe-symlinks"` with those three declared.
- The control and transform snapshot contexts in `run_isolated_canary` are
  **sequential, not nested** (`canary.py:479-544` closes before `:551`
  opens), so peak disk for one target is ONE snapshot.

| quantity | iteration 0 | 1 | 2 |
|---|---|---|---|
| `prepare_snapshot` (once per lane) | **4.071 s** | — | — |
| `materialize` enter | 1.063 s | 1.000 s | 0.894 s |
| `materialize` enter→exit | 1.260 s | 1.182 s | 1.260 s |
| `materialize_replacement` enter | 1.314 s | 1.358 s | 1.271 s |
| `materialize_replacement` enter→exit | 1.502 s | 1.511 s | 1.486 s |
| snapshot bytes / files | 96,023,420 / 3,757 | identical | identical |
| replaced snapshot bytes | 96,037,902 | identical | identical |

**Derived, and this is what A-432 uses:** one canary TARGET = one control
materialisation + one transform materialisation = **~2.76 s** of
materialisation (1.26 + 1.50, enter-to-exit, so cleanup included) plus two
full runs of the lane's own command. Peak disk ≈ **96 MB**.

**The bound follows the number, never the reverse (DA-R17).** The smallest
budget any shipped worked example declares is `5m`
(`docs/DESIGN-GUIDE.md:2127`; the others are 10m, 15m, 20m, 20m, 20m, 30m,
45m). `MAX_CANARY_TARGETS = 8` is the largest round N whose materialisation
floor stays under a tenth of that smallest budget: 8 × 2.76 = **22.1 s** =
7.4 % of 5 minutes, 0.8 % of 45 minutes. `MIN_CANARY_TARGETS = 1`.

## The three design rows

| row | item | ruling applied | the thing most likely to be missed |
|---|---|---|---|
| **A-430** | B004 | DA-D7 narrowed by DA-R12 | the §5.4 narrowing lands in **both** layers this time (the carve deferred the schema half to "whichever bump carries `PROVENANCE_UNVERIFIED`" — this is that bump); `mismatch → NO_MEASUREMENT`, never `FAIL`; the green path's only witness stays `ciu-provenance-green-reference.json` |
| **A-431** | carve W0's corrections | — | append-only: A-O12, A-257 and A-344 are NOT edited; this row is A-O12's erratum |
| **A-432** | B007 | DA-D8 + DA-R17 | `LANE_SCHEMA_VERSION` stays 2 because the singular `target` SURVIVES on the lane (exactly-one-of with `targets`); it does NOT survive on the wire, where the singular normalises to a one-element array; `aggregation` is present on the wire **iff** the lane declared the plural spelling, so its absence is checkable rather than defaulted |
| **A-433** | F015 | DA-D9 + DA-R16 | `R4` is the HIGHEST rung and therefore the claim `_replace_highest_higher_rigor_claim_with_git_failed` replaces first — DA-R16 rules that honest; the reserved reason code `RED_FIRST_UNPROVEN` lands in the v10 enum now and is rendered in phase 3 |

Acceptance boxes are NOT ticked for B004, B007 or F015 — these are design
rows, and nothing is implemented yet.

## Decision asks (generation 7)

1. **`RED_FIRST_UNPROVEN`'s outcome class: `NO_MEASUREMENT` (as designed, per
   DA-D9's literal words) or `FAIL`?** DA-D9 says the mechanism's every
   non-conforming case is "`NO_MEASUREMENT`-class with an existing or reserved
   code", and A-433 follows it. But the case that matters most — *the declared
   test PASSED at the broken commit* — is not a measurement failure: assay
   materialised both commits, ran both tests, and learned that the test does
   NOT discriminate the fix. By every analogy in this codebase that is a
   judged `FAIL` (it is precisely `CANARY_SURVIVED` one tier up), and calling
   it `NO_MEASUREMENT` tells a gate "nothing was measured" about a lane that
   measured exactly what it set out to. **Why it must be ruled inside this
   cut, not in phase 3:** the code's SET membership (`REASON_CODES[...]` and
   `$defs/reason_codes/...`) is a schema fact. Changing it after 5.0.0 ships
   is a v11. Changing it before the merge is free. Recommended split, if the
   controller wants one: `FAIL`/`RED_FIRST_UNPROVEN` for "the test passed at
   the broken commit" and "the test failed at HEAD", `NO_MEASUREMENT` with the
   EXISTING codes for the mechanism's own failures (`GIT_FAILED`,
   `TARGET_NOT_MEASURED`, `MISSING_EXTERNAL_TOOL`, `DIRTY_TREE`) — which is
   what DA-D9's "existing" half already anticipates. **A-433 is written to
   DA-D9 as ruled; a ruling either way is a one-line change to the row's
   successor and two lines in the cut.**
2. **`all` does not short-circuit on FAIL (A-432).** Ruled by me on cost
   (bounded at 22.1 s) and diagnostic value (a multi-target `all` lane exists
   to enumerate every surviving probe). It is stated and testable, not silent,
   but the controller may prefer symmetry with `any`. Flagging it because
   R-2's prompt names "the 2N bound" and this is the choice that makes the
   worst case exactly 2N rather than "2N or less".
3. **Sharing one control materialisation across targets was REJECTED**
   (A-432), costing ~1.26 s per extra target — up to 8.8 s on a full 8-target
   lane. Rejected because per-attempt independence is what the payload, the
   short-circuit bookkeeping and B064's per-target resume keying all rest on.
   Recorded here so the controller sees the price that was paid for it.

## What a reviewer should push on (generation 7's additions)

- That the B007 measurement really drove the shipped code path and not a
  re-implementation of it: read `scratchpad/measure_b007.py` against
  `canary.py:479-560`.
- That 8 is derived and not chosen: the arithmetic is in A-432 and above, and
  the `5m` anchor is a real line in a shipped doc.
- A-432's bookkeeping rules: whether `verify.py` can really re-derive the
  short-circuit pattern from the document alone, in both aggregations, with a
  terminal attempt in the middle.
- A-433's `broken_commit_source`: whether recording it is enough, or whether a
  lane that inherits its base should be refused outright.

## What I did NOT do, and why (generation 7)

- **The v10 cut.** See the checkpoint note in the LOG — the five wire changes
  now all exist as A-rows, which is the precondition; the cut itself is
  generation 8's first act, and cutting it half-way at a context boundary is
  the one thing the checkpoint clause forbids by name.
- **Any implementation of B050/B051/B052/B053-`detail`/B004/B007.** All ride
  the cut.
- **Any lane-file or consumer change.** `LANE_SCHEMA_VERSION` stays 2 and
  `inventory_schema` stays 1 in every row written here.
