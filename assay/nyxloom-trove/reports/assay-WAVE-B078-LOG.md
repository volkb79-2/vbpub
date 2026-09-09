# assay wave B078 — checkpoint 1 implementer LOG (2026-09-08/09)

> **Round-1 review: REJECT, 4 blockers — all four fixed. See
> "Round-1 repair" at the end**, which supersedes parts of the section "The
> one place I did not follow the design document" below: that correction was
> half right (moving the branch into `execute_plan` — confirmed correct by
> the reviewer and kept), and half wrong on a load-bearing fact (I claimed
> dstdns's `ui_unit` is an R0-only lane; it declares `rigor = ["R0", "R1"]`,
> so the path I wired is not the one RG-45 reproduces on). The original text
> is left standing, uncorrected, so the reasoning that produced the defect is
> readable rather than tidied away.

Branch: `feat/assay-b078-r0-structured-report-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b078-r0-structured-report`
Spec: `nyxloom-trove/R0-STRUCTURED-REPORT-DESIGN.md` (SR-0..SR-6) +
`WAVE-PROMPT-2026-09-08-b078-r0-structured-report-checkpoint1.md`
Backlog: `nyxloom-trove/4-backlog.md` `## B078`

Gate: **`tester-unified` GREEN** on `c80d94d451b92154394ecb48b845b21e6e7478c5`
— `ASSAY_REGISTERED_GATE_COMPLETE=1`, `run-gate: lane 'tester-unified' exit 0`,
all 13 `ASSAY_GATE_PHASE=` markers present. Verdict read in a separate step
from the log itself, never a piped exit code.

## Commits, in order

| Hash | What |
|---|---|
| `60b83a0c` | `src/assay/result_reports/` — the reader registry, the format-agnostic completeness core, the `vitest-json` reader, `tests/test_result_reports.py` |
| `5b2cecea` | `[lanes.X.result_report]` — `ResultReportConfig`, loader, `Lane` field, `as_declared()` round-trip, `tests/test_config_result_report.py` |
| `6073749c` | The R0 wiring: reserve/arm/consume + the three-way branch in `runner.execute_plan`, both R0 call sites, `tests/test_runner_result_report.py`, `make_lane(result_report=...)` |
| `286320db` | `docs/DESIGN-GUIDE.md` §6, `docs/CONSUMERS.md` (worked pasteable lane), `CHANGES.md`, backlog acceptance boxes |
| `c80d94d4` | Two findings from the full local suite: the reader's `json.loads` now catches `RecursionError`; `test_self_hosting.py`'s pinned universal-PASS mutation repinned to the new FAIL-branch spelling |

## The two design questions the spec left open

### 1. Exact TOML key names — `[lanes.<name>.result_report]` with `format` and `path`

SR-2's illustrative shape, adopted verbatim. Three things settled it:

- **Nesting.** `result_report` as a sub-table of the lane matches
  `[lanes.X.isolation]` and `[lanes.X.judge.coverage]` exactly. SR-2 named
  `[lanes.X.pins.assay]` as the estate precedent; in-repo, `isolation` is the
  closer one (same file, same loader, same `as_declared()` contract), and both
  point the same way.
- **`format`, not `reporter`/`kind`.** `judge.coverage.format` and
  `judge.mutation.format` already mean "which document shape is this, closed
  against a shipped registry" (A-007: declared, never sniffed). Reusing the
  word means a consumer who has written one assay lane already knows what this
  key does. `reporter` would have named the *producer*, which is a different
  axis this design does not model (and `judge.coverage.producer` already owns
  that word for a real distinction — see B045).
- **`path`, not `artifact`.** `artifact` is taken, and taken for something
  with a different grammar: every `*_artifact` field in this file is
  **project-root**-relative and containment-checked by resolving it against
  that root (`_validate_artifact_path`). The result report is relative to the
  directory the command **runs in** (the lane's `cwd` when declared), because
  that is what the runner's own `--outputFile` resolves against. Two different
  roots must not share one word — A-271's "two path grammars, not one,
  deliberately" is the same argument one field over.

Both keys are **required**; there is no defaulting. A format with no path
names nothing to read and a path with no format cannot be parsed, so neither
has a meaning alone, and this module's own rule ("a default is legitimate only
when it is a policy choice correct in the absence of information") admits
neither.

`format` is closed against `assay.result_reports.RESULT_REPORT_FORMATS`, which
is **derived from the reader registry** rather than hand-listed beside it — a
hand-listed copy is how a format becomes declarable before a reader exists,
and `tests/test_config_result_report.py::test_the_config_vocabulary_is_the_reader_registry`
asserts the two are literally the same object.

`LANE_SCHEMA_VERSION` **stays 2**. A-432's own precedent, in this file's own
words: an additive optional lane field does not bump it, and a lane that omits
the table carries `None` and emits no key.

### 2. Where the reader module lives — a new `src/assay/result_reports/` package

The migration surface offered three: mirror `adapters/`, a new
`report_adapters/`, or a `format` dispatch inside `runner.py`. All three were
rejected in favour of a fourth that already exists in this codebase twice.

- **Not `adapters/`.** That package is the LANGUAGE registry: keyed by
  language, every member implementing the whole `LanguageAdapter` protocol
  (mutant generation, canary injection, coverage-key normalisation, statement
  spans). A test-report reader shares neither the key space (a *format*, and
  one format spans languages — Jest and vitest write the same document) nor
  the protocol. Putting it there means either widening `LanguageAdapter` with
  methods its five real members cannot answer, or keeping an unrelated kind of
  object in a namespace whose name promises the first kind.
- **Not `report_adapters/`.** It would be a second convention for a thing this
  repo already has a convention for.
- **Not a dispatch inside `runner.py`.** `runner.py` is 5,500 lines and is the
  one module that must stay runner-agnostic (`_command_heartbeat`'s own
  docstring says so about a much smaller intrusion). Per-format parsing there
  is exactly what `coverage_parsers/` was extracted out of.
- **Chosen: `result_reports/`,** built to look identical to
  `coverage_parsers/` and `mutation_parsers/` — sibling packages, one module
  per artifact FORMAT, a `model.py` of normalized types, a tiny uniform
  protocol (`FORMAT` + `read(raw) -> ReportSummary`), a registry in
  `__init__.py`, and a strict DAG (no sibling imports, nothing imports back
  into `runner`).

The split inside the package is what makes Checkpoints 2 and 3 small: a reader
turns one format's bytes into a `ReportSummary` or raises `ReportUnusable`;
`model.verify_complete` applies SR-2's bar to every format identically. Adding
`pytest-json-report` is a module plus one line in `_READERS`. The core is
tested against a synthetic non-vitest summary
(`tests/test_result_reports.py::test_the_core_refuses_every_incomplete_shape`)
precisely so "a core that can only be exercised through one format" — which
would not be a core — fails visibly.

## Decisions taken inside the ruled constraints

**No `Claim.detail`.** SR-6 left this to me and suggested the free-text route
to avoid A-050's closed-enum stop-and-ask. It cannot be taken:
`Claim._check_detail` **forbids `detail` on a PASS claim** ("a PASS carries no
detail"), and the PASS direction — a verified-complete report overriding a
non-zero exit — is precisely the case worth annotating. A FAIL-only detail
would be an asymmetric note about the uninteresting half. The provenance goes
where it can go instead, unforced: a report-driven PASS keeps the **real
non-zero `returncode`** and the **bounded stdout/stderr tails** on the
artifact, so the disagreement it resolved is visible without any schema
surface at all. `Verdict.result_stdout_tail`'s own docstring already allows a
PASS to carry them.

**No new `ReasonCode`, no `Outcome`/`EXIT_CODES` change, no `run-gate.py`
change, no retry, no CPU-quota primitive, no dstdns-side argv edit, no
Checkpoint 2/3 reader.** All as ruled.

## The one place I did not follow the design document

The design and the wave prompt both say "R0 wiring in `execute_command`",
citing `runner.py:1140-1142`. Those lines are `execute_command`'s **docstring**
describing A-073; the rule itself is implemented one function below, in
`execute_plan` (`runner.py:1066-1088` pre-change).

That distinction is load-bearing, not pedantic: **the shipped R0-only path
does not go through `execute_command` at all.** `run_lane`'s direct branch
(`runner.py:~5299`) calls `execute_plan` itself, and that is the path
dstdns's `ui_unit` lane — RG-45's own confirmed live reproduction, an R0-only
`kind = "assay"` lane — actually runs on. Wiring only `execute_command` would
have shipped a feature that passes every unit test and does nothing for the
case it was built for.

So the branch lives in `execute_plan`, behind a keyword-only
`result_report=None` parameter, and exactly two callers pass it:
`execute_command` (the design's named site, and the public API) and
`run_lane`'s direct R0-only path. `execute_plan`'s other callers — every R2
candidate re-execution, R3's canary control/transform halves, the
`environment_command` probe — keep the default and are unchanged **by
construction**, which is a stronger form of SR-1's promise than a rule each
call site has to remember.
`tests/test_runner_result_report.py::test_run_lane_direct_r0_path_applies_the_tiebreak`
covers the shipped path end to end, with an adjacent must-fail control.

Flagged here rather than silently absorbed, per the wave prompt's own
instruction. It is a correction to the design document's site reference, not a
change to anything the design decided.

## Two things the full local suite found (neither predicted)

1. **`test_untrusted_json_parse_sweep.py`** — the new reader's `json.loads`
   was unguarded. A result report is an untrusted third-party artifact, and a
   pathologically nested one blows CPython's stack inside the decoder. Here it
   is worse than a crash-vs-refusal question: an uncaught `RecursionError`
   escapes R0's terminal mapping entirely, where every other malformed shape
   is a quiet fallback. Fixed with the one-line widening the sweep asks for,
   plus a test that actually feeds it 200,000 nested arrays.
2. **`test_self_hosting.py`** — A-131's pinned universal-PASS mutation
   targeted the two literal field lines of R0's FAIL constructor, which this
   wave turned into a conditional pair. Repinned to the new spelling (same
   branch, same two fields, same mutation), and `runner.py`'s explanatory
   comment moved *above* the call so the pinned lines stay adjacent and the
   pin stays a surgical one-line edit. A comment sitting between them would
   have made the pin brittle against reflow.

Both are recorded because they are the kind of thing a "the feature works"
summary would have omitted.

## Known limitation, stated rather than discovered later

Assay does **not** create the report's parent directory.
`safeio.reserve_output`'s `create_missing_parents` is contractually reserved
for callers that own an ephemeral assay-managed snapshot (B006(b)), and R0
runs in the consumer's live tree. A lane declaring
`path = ".assay/report.json"` in a tree with no `.assay/` therefore falls back
to A-073 — safe, but silent. Mitigated by documentation (CONSUMERS.md
recommends a project-root path, which always works) and asserted as a stated
fact by
`tests/test_runner_result_report.py::test_a_report_in_a_directory_that_does_not_exist_yet_falls_back`.
If a future consumer needs a subdirectory path, the honest fix is a widened
`safeio` contract, not a quiet `True` at this call site.

*(Round-1 update: this now applies to R0-ONLY lanes only. The snapshot
baseline path legitimately passes `create_missing_parents=True` — see the
repair section below.)*

---

# Round-1 repair (2026-09-09)

Review: `assay-WAVE-B078-REVIEW-round1.md` (`27b66c25`) — **REJECT, 4
blockers.** All four fixed. Gate re-verified green on the repair commit.

| Commit | Blockers |
|---|---|
| `388f23a2` | 1 — wire the R0 command of every R1+ lane |
| `f535f04e` | 3 (canary exclusion) + 2 (the wiring sweep that answers it) |
| `92803df3` | 4 (`returncode` is not a verdict field) + OBS 4, OBS 5 |

## Blocker 1 — what I actually got wrong

The reviewer split my design-doc correction in two, and they were right to.

**The half that held:** `runner.py:1140-1142` really is a docstring, the rule
really lives in `execute_plan`, `execute_command` really is not called by
`runner.py` at all, and moving the branch into `execute_plan` behind a
keyword-only `result_report=None` really was the right structure. Kept
unchanged.

**The half that did not:** I justified wiring the *direct* path by asserting —
three times, as settled fact — that dstdns's `ui_unit` is an R0-only lane. It
is not. `/workspaces/dstdns/assay.toml` declares `rigor = ["R0", "R1"]`, which
I did not check before writing it down. An R1 lane never reaches the direct
branch; `run_lane` dispatches it to `_run_higher_rigor_lane` →
`_run_prepared_lane` → `_execute_snapshot_unit`, and *that* baseline unit's
`CommandResult` is what `build_r0_claim` turns into the R0 claim.

So the checkpoint shipped green and did nothing for the bug it was written
for. The uncomfortable part is that this is the *same* failure I had just
correctly diagnosed one layer up and written a paragraph about — I caught
"the design points at a path the repro does not take", then landed on a
different path the repro does not take, because I stopped verifying at the
point where the story became satisfying. The lesson worth keeping is narrower
than "check your facts": **a claim that makes the rest of your reasoning work
is the one to verify first, not last.**

**The fix, per the controller's D-1 ruling.**
`_execute_snapshot_unit` gains a `result_report=None` parameter it only
*forwards*; `_run_prepared_lane`'s baseline call site is the sole caller that
passes anything. Deriving it from the lane inside that function would have
been fewer lines and silently wrong: that engine is shared with R2 candidate
re-executions and R3's canary halves, so the tiebreak would have reached all
three. Taking it from the caller keeps "only the lane's own R0 command opts
in" true by reading call sites rather than by trusting a docstring.

The snapshot half also passes `create_missing_parents=True` (also D-1).
B006(b) reserves that opt-in for a caller that KNOWS it owns an ephemeral
assay-managed checkout, which this one does and the direct path does not. It
matters more here than for coverage: a snapshot is a tracked-only checkout, so
an untracked output directory never exists in it — without this, a
subdirectory report path would fall back to A-073 on *every* run rather than
only the first.

**Tests:** three new end-to-end R0+R1 cases (the RG-45 shape; the
report-names-failures-over-a-zero-exit direction; the created parent
directory), each with a must-fail control. Verified load-bearing by removing
the one-line wiring and confirming exactly those three turn red, then
restoring.

The three false statements are corrected at all three cited locations.

## Blocker 3 — the invariant my own prose asserted was false

`canary._run_pipeline` (the engine both halves of the legacy standalone
`run_python_canary` run through) calls `execute_command`, which I had made
forward `lane.result_report` unconditionally. Three places in this branch —
`execute_plan`'s docstring, DESIGN-GUIDE §6, and this LOG — said canary halves
were "unchanged by construction". They were not. I wired a public-API function
and never traced its callers, which is exactly the omission that produced
blocker 1 in a different function.

Per the controller's D-2 ruling: **excluded.** `canary.py` now passes
`result_report=None` explicitly. That needed a sentinel default on
`execute_command`, because `None` is itself a legitimate value of
`Lane.result_report` and "take the lane's declaration" must stay
distinguishable from "consult no report at all" at the call site — the
`_ISOLATION_UNSET` pattern the test harness already uses for the same reason.

## Blocker 2 — answered by construction, then pinned mechanically

With the baseline unit wired, every lane shape's R0 claim consults the
declaration: R0-only through the direct branch, R1/R2/R3 through
`_run_prepared_lane`'s baseline. There is no shape left where `result_report`
is accepted at load and silently inert, so **no load-time rigor refusal is
needed** — the condition the reviewer's prescription made it conditional on
does not arise.

But "I traced the call graph and it's fine" is precisely the assurance that
failed in round 1, so it is not the deliverable.
`tests/test_result_report_wiring_sweep.py` proves it mechanically instead: an
AST sweep over every `execute_plan`/`execute_command` call site in
`src/assay/`, each of which must either pass `result_report=` or appear in
`EXCLUDED_SITES` with a written reason. A `REQUIRED_SITES` half catches the
opposite direction — a site that *stops* passing it, which is what round 1
was, and which a presence-only sweep structurally cannot see. Canary's
exclusion is pinned by *value*, so flipping it to `lane.result_report` fails
there rather than nowhere.

It earned itself immediately: it located the mutation call site under a
function name I had guessed wrong when writing the exclusion list.

## Blocker 4 — documentation of a field that does not exist

CONSUMERS.md told consumers a report-driven `PASS` "still records the real
`returncode`" on the verdict. It does not — `returncode` lives on the
in-process `CommandResult` and on the progress stream's `command_finished`
event, and appears nowhere in `verdict.schema.json` or `verdict.py`. I wrote
that sentence from the code I was editing without checking what
`assemble_verdict` actually threads, and it was the one paragraph a consumer
reads to decide whether this feature is auditable.

Per the controller's D-3 ruling: fixed by **correcting the documentation, not
by adding a verdict field** (that would be a real schema change, and this wave
is scoped to none). What replaces it is true, and per OBS 4 is now stated as a
usable detection method rather than left implicit: an ordinary green run omits
the output tails, so **a `PASS` carrying `result_stdout_tail`/
`result_stderr_tail` IS a `PASS` that overrode a non-zero exit code** — and
those tails are the failing run's own output, which is what an auditor wants.
The exit code itself remains available via `--progress`. The matching code
comment in `runner.py` is corrected too.

## OBS 2 and OBS 5

- **OBS 2:** the vitest reader joins `test_untrusted_json_parse_sweep.py`'s
  pinned list (now nine). The derived sweep already covered it — that is how
  the missing `RecursionError` guard was caught — but the pinned list exists
  to notice a site that *disappears*, which a derived sweep cannot.
- **OBS 5:** DESIGN-GUIDE's "the tiebreak applies to the lane's own R0 command
  alone" was false in both directions before this repair. It is now accurate,
  and spelled out exhaustively: three consulting sites (one per lane shape)
  and a named reason for each of the three exclusions, with a pointer to the
  sweep that pins the list.

## OBS 1 and OBS 3 — read, deliberately not actioned

- **OBS 1** (`verify_complete`'s `finished` check is unreachable for the only
  shipped format, since the vitest reader refuses a missing `success` itself).
  Correct, and left as is: it is defence-in-depth for Checkpoints 2/3, the
  reviewer agreed the split is right, and the unit test covers the bullet
  directly. Collapsing the two would make the core format-specific.
- **OBS 3** (a lane author who mistypes a report directory gets zero signal).
  Narrowed by blocker 1's fix — it now applies to R0-only lanes only. The
  reviewer's suggestion of a `--progress` `command_finished` note saying "a
  declared report was not usable" is a good one and is explicitly Checkpoint
  2's, not this wave's.

## Round-2 fix-verification condition (close-out)

ACCEPT-conditional, one test-only repair, now done.

`test_a_declaring_lane_produces_identical_canary_behaviour` did not test what
its name says. Its lane ran a bare `exit 1`, which writes no report, so both
sides fell back to A-073 and the equality held regardless of what
`_run_pipeline` did with the declaration — the reviewer demonstrated it by
flipping `canary.py` to `result_report=lane.result_report` and watching the
test pass anyway. Blocker 3's behavioural enforcement was therefore resting
entirely on the AST value-pin in the sweep.

It now uses the module's own `shell_writing(vitest_document(total=140,
failed=0), exit_code=1)` — the RG-45 shape — so the two sides can only agree
if the canary genuinely ignores the declaration. It additionally pins the
shared value (`FAIL`/`COMMAND_FAILED`) so the equality cannot be satisfied by
both sides moving together, and asserts the report file was actually written
so the test cannot quietly regress to proving nothing. Re-verified under the
reviewer's own mutation: this test and the sweep pin both go red, and both go
green again on revert. No shipped code moved.

The sting is that the sibling seam test's docstring, fifty lines above, warns
in as many words that "a behavioural test could pass while the input was
silently being consulted and happening not to change the outcome" — I wrote
that sentence and then wrote the test it describes. Knowing the failure mode
by name is not the same as checking for it; the check is a mutation, and it
costs about a minute. That is the third time in this wave the same shape of
error has appeared (an unverified premise, an untraced caller, an unmutated
test), which is the pattern worth carrying forward, not the individual bugs.

## The backlog tick

Reverted to `[ ]`. I ticked my own acceptance box in round 1, and ticked it on
work that did not do what the box claims. It belongs to fix-verification. The
fault-injection tick the reviewer judged earned is left standing.
