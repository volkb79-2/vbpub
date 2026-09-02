# assay Wave D (v10) — BRIEF-1 (generation 1 → generation 2)

Written at generation 1's E-008 checkpoint. Cumulative seam map: what is
DONE with hashes, what is NEXT as a literal task list, the load-bearing
file:line seams generation 2 needs, the gate state, the next free ids, and a
retention prompt.

Read this INSTEAD of re-deriving. Everything below was verified by running
it, not by reading about it.

---

## 1. Where the branch stands

- Worktree `/workspaces/vbpub/.worktrees/assay-wave-d-v10`, branch
  `feature/assay-wave-d-v10`, forked from `main` at `a4a865da`.
- **Tip:** see §6 (the brief commit is the tip; the gate-verified commit is
  `3b2b8e62`).
- **Phase 1 items DONE: 1 of 10.**

| # | item | ruling | status |
|---|---|---|---|
| 1 | **B049** | DA-D1 | **DONE** — `3b2b8e62`, A-408, gate state in §6 |
| 2 | B054 | DA-D3 | NOT STARTED — design worked out below, §3 |
| 3 | B053 (a)+(b) | DA-D2 | NOT STARTED — seams mapped below, §4 |
| 4 | B028 | DA-D10 | NOT STARTED |
| 5 | B029 | DA-D11 | NOT STARTED |
| 6 | B060 | DA-D14 | NOT STARTED |
| 7 | B056 | DA-D13 | NOT STARTED |
| 8 | B024 | DA-D15 | NOT STARTED |
| 9 | B055 | DA-D12 | NOT STARTED |
| 10 | B009 | DA-D16 | NOT STARTED |

Phase 2 and phase 3: untouched. **Nothing under `verdict.py`, `verify.py`,
`src/assay/schemas/`, or the drift-guard carve-assets has been modified**, so
the branch is still releasable on v9 exactly as the wave prompt requires.

## 2. What landed (item 1, B049 / DA-D1 / A-408)

Commit `3b2b8e62` `fix(assay): a replaced output directory is named, not read
as EMPTY_COVERAGE (B049, A-408)`.

The one seam: `assay.safeio.OutputReservation._refuse_if_parent_was_replaced`
(`src/assay/safeio.py:285`), called from `consume()` (`src/assay/safeio.py:276`)
inside the existing `try`, so the `finally` still closes and nulls the held
descriptor on the refusal path. `os.fstat(parent_fd).st_nlink == 0` →
`ERROR`/`UNREADABLE_ARTIFACT` naming the directory, the cause and the remedy.

Because the guard is in `consume()`, all five reserved-artifact reads inherit
it: `runner.py:2241` (coverage), `runner.py:2256` (SQL R2
`equivalence_artifact`), `runner.py:2286` (ingested mutation report),
`mutation.py:1642` (per-mutant equivalence), `mutation.py:1192` (kill signal).
The two `mutation.py` sites propagate rather than absorb (`:1644-1647` catches
into `decode_error`, `:1654-1655` re-raises).

Tests: `tests/test_safeio_replaced_output_directory.py`, 8 tests, red-first
(4 failed / 4 passed with `safeio.py` stashed to pre-fix; 8 passed with it).
Docs: `README.md:264,269`, `docs/CONSUMERS.md:622,633`, and a new
DESIGN-GUIDE section "A replaced output directory is named, not folded into
EMPTY_COVERAGE" (the README anchor to it is checked by
`tests/test_docs_examples_and_vocabulary.py`, green).

## 3. NEXT — item 2, B054 (DA-D3). Design already done; do not re-derive it

**The ruling.** Per file: an istanbul record whose `branchMap` contradicts its
own `statementMap`/`s` classification is a defect of THAT FILE. A file with
no line in the judged set is **skipped and NAMED on the diagnostics stream**
(never silently). A file INSIDE the judged set refuses
`ERROR`/`UNREADABLE_ARTIFACT` naming the file and the arc line (today's
message, per file). A-357's refusal on an unrecognised arc TYPE is untouched.
No `excluded_files` wire list (DA-D3 rejects it).

**Today's behaviour, located.** `src/assay/coverage_parsers/coverage_istanbul_json.py:314-322`:
`_parse_record` builds `FileCoverage(executed=…, missing=…, excluded=None,
branches=branches)` inside a `try`, and turns the dataclass's own `ValueError`
(invariants 3 and 5 — an arc on a line no statement extent covers; a covered
arc on a line whose statement count is 0) into
`_malformed("record for {path!r}: its 'branchMap' arcs contradict its own
'statementMap'/'s' line classification -- {exc}")`. That is raised at PARSE
time, before anything knows the judged set, so it kills the whole verdict.

**The precedent to mirror, exactly** — A-405's `//line`-remapped-file rule.
Read these three before writing a line of code; the shape is already settled
in this codebase and DA-D3 explicitly invokes it:

- `src/assay/coverage_parsers/model.py:419` —
  `FileCoverage.line_directive_remapped`, a **derived** per-file property
  (derived, "never stored", because a stored flag can disagree with the
  records and every rebuild layer would have to remember to carry it).
- `src/assay/evaluate.py:144` `_refuse_line_directive_remapped`, and its two
  callers `evaluate.py:490` (changed-lines mode, after `_is_considered`) and
  `evaluate.py:1071` (whole-target mode). The asymmetry is stated there in
  the north star's own words: a file outside the judged set is invisible to
  the verdict by construction.
- `src/assay/statement_attribution.py:203` and `src/assay/runner.py:962-983` —
  the "skip it, and keep the skip derivable one layer up" half.

**The one place the precedent does NOT transfer, and the decision it forces.**
`line_directive_remapped` is derivable from `blocks`, which every rebuild
carries. A contradictory `branchMap` is derivable from nothing once the
offending `branches` are dropped — and they MUST be dropped, or
`FileCoverage.__post_init__` refuses construction. So generation 2 has to
either (a) store the offending arc lines on `FileCoverage` (e.g.
`contradictory_branch_lines: frozenset[int] | None`, with the derived
boolean beside it, and carry it through every rebuild —
`statement_attribution.attribute_statements` and
`runner._normalized_profile_files` are the rebuild sites; grep
`FileCoverage(` to find them all), or (b) keep the defect OUT of the model and
return it as a second channel from the parser (a `(profile, defects)` pair),
which changes the parser-registry signature that every format shares.
**(a) is the smaller blast radius and matches the model's existing habit of
carrying per-file facts; take it unless something argues otherwise, and
record the choice as an A-row naming (b) as rejected.** Note the `__post_init__`
invariants must exempt the field (it is metadata about arcs that were
dropped, not arcs).

**The diagnostics half.** `evaluate.py` has no stream today. The stream is
threaded as `diagnostics: TextIO | None` from `cli.py:781` (`diagnostics=err`)
through `runner.run_lane` (`runner.py:3778`) → `_run_higher_rigor_lane`
(`runner.py:3563`) → `_run_prepared_lane` (`runner.py:2629`), and the two
existing writers are `runner.py:365` (`_report_probe_refusal`) and
`runner.py:2973-2977`. **Do not thread a stream into `evaluate.py`** — that
module is deliberately pure. Instead have `evaluate` surface the skipped
files as data on its return (or collect them where the profile is normalised
in `runner.py`) and let `runner` print them, the same way the helper-envelope
finding at `runner.py:2973` does. This also gives B053(b) its second
customer, so **do B053 before or together with B054 if the ordering makes the
stream work land once** — the wave prompt's order is B054 then B053, but the
stream plumbing is shared and generation 1 did not commit to either order
being load-bearing. Say which you chose in the LOG.

**Test the ruling asks for** (R-1 will ask for exactly this): a **two-file**
istanbul artifact — one defective file OUTSIDE the judged set (verdict still
PASSes on the strength of the changed-lines diff, and the file is NAMED on
diagnostics), one defective file INSIDE it (`ERROR`/`UNREADABLE_ARTIFACT`
naming the file and the arc line). Real istanbul fixtures already exist:
`tests/test_coverage_istanbul_real_fixtures.py`,
`tests/test_coverage_istanbul_branch_arcs.py`,
`tests/test_coverage_parsers_coverage_istanbul_json.py`, and
`tests/fixtures/`. The contradiction is reproducible by hand from a real
record (B054's own witness is dstdns's `analytics.ts` line 215, an ordinary
braceless single-statement `if`); a hand-built istanbul document is legitimate
here — A-334 forbids a test double standing in as evidence about an EXTERNAL
system, and the claim under test is assay's own disposition rule, not
Vitest's behaviour. Still, prefer a fixture derived from a real
`coverage-final.json` if one is already committed.

Docs for B054: `docs/CONSUMERS.md`'s JavaScript-lanes section currently
implies a broad `src/**` include is the standard shape — after the fix that
becomes true again, and the paragraph should say what a consumer sees for a
defective file inside vs. outside the judged set. DESIGN-GUIDE gets the WHY
(per-file disposition, and why not an `excluded_files` wire list).

## 4. NEXT — item 3, B053 (a)+(b) (DA-D2). Seams mapped

**(a) CLI, stderr.** `cli.py`'s ONE `except AssayError` that prints the
detail wraps only `_resolve_declared_adapters` (`cli.py:724`, printing at
`cli.py:729`-ish: `print(f"assay: {exc.outcome}/{exc.reason_code}: {exc}",
file=err)`); a second identical print sits on the attestation-timeout path
(`cli.py:693`+). `_print_run_summary` (`cli.py:~800`) is the three-line
summary and must stay unchanged.

**The catch generation 1 verified and generation 2 must not miss:** most
refusals never propagate to `cli.py` at all. `runner.py` has **21**
`except AssayError` handlers that convert the error into a refusal `Verdict`
or a refusal `Claim` (`grep -n 'except AssayError' src/assay/runner.py`), so
a handler at the CLI boundary alone cannot see them. The single whole-lane
seam is `runner.refuse_lane` (`runner.py:1584`), reached from
`_run_higher_rigor_lane`'s plan-resolution catch (`runner.py:3592`) and
others; per-claim refusals build `Claim` objects at the ~15 remaining sites.

**Therefore the honest reading of DA-D2 (a)+(b) is ONE emitter, not two.**
Add a single helper — generation 1 suggests `runner.announce_refusal(exc, *,
diagnostics)` printing exactly `assay: {outcome}/{reason_code}: {message}` —
call it wherever an `AssayError` becomes a verdict or a claim, and let
`cli.py` keep passing `diagnostics=err` (`cli.py:781`) so the CLI gets the
line on stderr for free. Refactor `cli.py`'s two existing prints to call the
same helper so the text cannot drift. **Print exactly once**: R-1's stated
push is "the stderr line appears exactly once for every refusal class
reachable through `assay run` (enumerate them from `errors.py`)", so build
that enumeration from `src/assay/errors.py`'s `ReasonCode` and write a test
per reachable class, counting lines. Record the "one emitter, called at every
conversion site" choice as an A-row naming "a single `try` at the CLI
boundary" as the rejected alternative — with the reason: it cannot see the
21 internal conversions, which is the whole defect B053 filed.

**Do NOT touch the wire.** DA-D2 (c) — the per-claim `detail` field — is
phase 2, not phase 1.

## 5. NEXT — items 4-10, in the wave prompt's order

4. **B028 / DA-D10.** One outer catch per higher-rigor entry point
   (`_run_higher_rigor_lane`, `runner.py:3529`) and one for direct R0's own
   loop (inside `run_lane`, `runner.py:3749`): a lane-wide `LANE_TIMEOUT`
   becomes the refusal claim the existing refuse path already builds, the
   reserved `--verdict-json` is WRITTEN, cleanup of a half-built snapshot is
   attempted and its failure recorded via the existing cleanup-failure path
   (never masking the timeout). Test red-first **through the installed CLI**
   with `budget_seconds = 1` and a real slow command. `LaneDeadline.remaining`
   is `runner.py:215`; it is called from ~16 sites across `runner.py`,
   `mutation.py`, `canary.py` — the ruling is explicitly ONE outer catch per
   entry point, NOT per call site.
5. **B029 / DA-D11.** Thread `infrastructure_source`/`infrastructure_environment`
   through `runner.execute_command` (`runner.py:743`, whose docstring
   currently says it accepts neither) into `canary.py`'s two call sites.
   CLI-driven test with a resolvable `derived:` fact asserting the R3 claim
   is PASS/FAIL on the canary, not `ERROR`/`BAD_LANE_CONFIG`. The
   misattribution happens at `_run_higher_rigor_lane`'s catch around
   `run_isolated_canary` (~`runner.py:2262` per the entry; re-verify the
   line).
6. **B060 / DA-D14.** `gate/distribution/build_release.py` writes
   `zipapp-staging/` NEXT TO `--outdir` and never removes it → build under a
   `TemporaryDirectory`. Outcome test: a build with `--outdir <repo>/assay/dist`
   leaves no untracked path. Small and self-contained; a good one to land
   early if a checkpoint is close.
7. **B056 / DA-D13.** Option 1: correct
   `tests/test_verdict_schema_is_packaged.py`'s module docstring to the
   measured truth and assert the OUTCOME. Apply the same to
   `tests/test_go_helper_is_packaged.py` — which is ALREADY in that shape, so
   **verify, do not re-write** (the ruling says so explicitly).
8. **B024 / DA-D15.** Wire `pyflakes` and `ruff` (pyflakes-equivalent rule
   set ONLY: F-rules, no style rules — name which in the A-row) into
   `assay/tools/tester-unified-gate.sh` as a phase AFTER the suite; inside
   the image if the tools are there, else inside `run-venv` from the offline
   wheelhouse **if the closure already carries them**. If neither is possible
   without a network fetch: **write the decision ask and land nothing** —
   A-198's hash-bound closure is not to be loosened for a linter. Check
   `assay/gate/distribution/build-wheelhouse/` and the image before deciding.
   R-1 will plant an unused import and expect the gate to go red.
9. **B055 / DA-D12.** Ruling only: leave the Go line-granularity limit as a
   documented limit (alternative 1 of three), no wire field. A-row naming all
   three alternatives; CONSUMERS' Go paragraph states "statement-granular to
   the line, not to the statement" beside what a Go R1 claim means; the test
   that asserts today's behaviour
   (`test_lit_go_drops_the_fabricated_signature_but_still_launders_line_four`)
   **stays**.
10. **B009 / DA-D16.** Docs only. Item 1 verbatim (assay.toml's role). Item 2
    describes the distribution model **AS MEASURED TODAY** — grep
    `dstdns/tools/assay`, `cmru/cmru.toml`'s S15 pin, `ciu`, `nyxloom`,
    `run-gate` and say which vendors a pinned `.pyz`, which bakes, which
    builds in-repo; do NOT prescribe the "vendoring retired" future unless
    you find it is already true. Item 3: a one-line forward pointer, no more.

Then: gate green on the phase-1 tip, LOG/REPORT/BRIEF, return — the
controller dispatches R-1 on that tip while generation 3 starts phase 2.

## 6. Gate state

The registered gate was run **detached** from `/workspaces/vbpub`:

```
setsid nohup bash -c '{ bash assay/tools/tester-unified-gate.sh \
  /workspaces/vbpub/.worktrees/assay-wave-d-v10; echo GATE_EXIT=$?; } \
  > <log> 2>&1' &
```

**Trap generation 1 hit, recorded so generation 2 does not repeat it:** the
first launch was written as `S=… && setsid … &`, which backgrounds the WHOLE
`&&` list — the variable was set only in the background subshell, the
follow-up `ls` in the parent silently looked at the wrong path, and a second
launch was issued on the assumption the first had failed. **Two gate
containers then ran concurrently, both appending to the same log**, which
would have produced two `ASSAY_REGISTERED_GATE_COMPLETE=1` markers in one
file — a log that cannot be read as a verdict. Both were killed
(`docker kill`), the log deleted, and a single run relaunched. Always
`pgrep -af 'tester-unified-gate.sh'` and `docker ps | grep tester-unified`
after launching, and expect exactly one.

**Verdict for `3b2b8e62`:** recorded in
`assay-WAVE-D-v10-LOG.md` under generation 1 (read there — it is written from
the log's own markers in a separate step, never from the launcher's exit
status).

## 7. Next free ids (checked against `main` at `a4a865da`, not assumed)

```
$ git show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
```

Generation 1 allocated **A-408** and filed no new backlog entry.
**Next free: A-409, B062.** Re-run those two commands against `main` before
allocating — another branch may have landed in between; the wave prompt
requires saying so in the LOG.

## 8. Rules generation 1 was held to, and passes on

- File edits through the Edit tool, never `sed`/python rewrite scripts
  (operator directive).
- `git commit -F <msgfile> --only -- <paths>`; trailers
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01RJ3wqoyy8ZzHmj7ZK1qEnJ`.
- **No `!` marker in phase 1.** The one `feat(assay)!:` belongs to the phase-2
  cut and nowhere else.
- Commit before you gate (an untracked file is `DIRTY_TREE`).
- Read the gate verdict in a SEPARATE step from the log's own markers; a
  harness exit status is never the job's status.
- `decisions.md` is APPEND-ONLY from A-408; backlog ids from B062.
- A-334: no test double as evidence about an external system.
- Touch ONLY `assay/**`.

## 9. Retention prompt for generation 2 (self-authored)

> **KEEP:** the branch/worktree identity and tip; that phase 1 item 1 (B049 /
> DA-D1 / A-408) is DONE at `3b2b8e62` with its gate state; the §3 B054
> design (the A-405 precedent at `model.py:419` / `evaluate.py:144,490,1071`,
> the "derived flag does not transfer — store the arc lines" decision and its
> rejected alternative, the rebuild sites); the §4 B053 seam map (21 internal
> `except AssayError` conversions in `runner.py`, so ONE emitter called at
> every conversion site, not a CLI-boundary `try`; `diagnostics` threading
> `cli.py:781` → `runner.py:3778` → `:3563` → `:2629`, writers at `:365` and
> `:2973`); the §5 task list verbatim; the gate launch recipe and the
> concurrent-launch trap; next free ids A-409 / B062; §8's rules.
>
> **DROP:** the file-by-file reading trail generation 1 used to find those
> seams; the full text of the phase-1 backlog entries (re-read the one entry
> you are working on, not all ten); the B049 test transcripts (they are in
> the REPORT); the CHANGES.md and README wording debates.
>
> **DO NOT** start phase 2, touch `verdict.py`/`verify.py`/the schema/the
> drift-guard, or use a `!` commit marker, until every phase-1 item is landed
> and the gate is green on that tip.
