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
