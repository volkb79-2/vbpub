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
