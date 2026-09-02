# assay Wave D (v10) — implementer LOG

One entry per commit, in order. Branch `feature/assay-wave-d-v10`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-d-v10`, from `main` at `a4a865da`.

Wave prompt: `nyxloom-trove/WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md`.
Report: `assay-WAVE-D-v10-REPORT.md`. Briefs: `assay-WAVE-D-v10-BRIEF-<n>.md`.

## Generation 1

**Id allocation, checked against `main` before filing** (the wave prompt's own
rule): `git show main:assay/nyxloom-trove/decisions.md | grep -c '^| A-'` and
the `4-backlog.md` id sweep on `main` at `a4a865da` both agree with this
branch — the last decision on main is **A-407** and the last backlog entry is
**B061**, so this generation allocates from **A-408** and, if it files
anything, from **B062**. No collision with a concurrent branch was found.

### 1. `fix(assay): a replaced output directory is named, not read as EMPTY_COVERAGE (B049, A-408)`

- Item: **B049**, ruling **DA-D1** (option 4).
- Changed: `src/assay/safeio.py` (the guard, in `OutputReservation.consume`
  via the new `_refuse_if_parent_was_replaced`), `tests/test_safeio_replaced_output_directory.py`
  (new, 8 tests), `README.md`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`,
  `CHANGES.md`, `nyxloom-trove/decisions.md` (A-408),
  `nyxloom-trove/4-backlog.md` (B049 acceptance boxes + RESOLVED).
- Red-first: with `src/assay/safeio.py` stashed to the pre-fix state,
  `pytest tests/test_safeio_replaced_output_directory.py -q` →
  `4 failed, 4 passed` (the 4 that fail are the four new-behaviour
  assertions; the 4 that pass are the legitimate-state controls, which must
  pass on both sides). With the fix restored: `8 passed`, and
  `tests/test_safeio.py` (45 tests) unchanged green.
- No `verdict.py` / `verify.py` / schema / drift-guard file was touched —
  phase 1 stays releasable on v9.

### 2. `docs(assay): Wave D generation 1 checkpoint -- BRIEF-1, and the gate discipline it cost`

- No product code. `nyxloom-trove/reports/assay-WAVE-D-v10-BRIEF-1.md` (new),
  plus this LOG's gate entries and the REPORT's gate transcript.

### Gate runs, generation 1

**Run 1 — VOID, not a verdict.** Launched as `S=… && setsid … &`, which
backgrounds the whole `&&` list: the variable existed only in the background
subshell, the parent's follow-up `ls` looked at the wrong path and appeared
to show a failed launch, and a second launch was issued. **Two gate
containers ran concurrently, both appending to one log.** Both were killed
(`docker kill sleepy_wing unruffled_germain`), the log deleted. No verdict is
claimed from it — an interleaved log cannot be read as one.

**Run 2 — RED, and the cause was mine.** Single run, `3b2b8e62`, from
`/workspaces/vbpub`. The suite itself was entirely green (`3944 passed, 20
skipped in 567.14s`) and every schema phase passed
(`ASSAY_GATE_PHASE=verdict-v9-successors-verified`), but the self-hosted
assay lane refused:

```
tester-unified: NO_MEASUREMENT/DIRTY_TREE (exit 3)
  commit: 3b2b8e62b0cbc341fcc9def1302b0a8cc2998e15
ASSAY_GATE_DIAGNOSTIC=worktree-untracked-by-assays-own-query
assay/nyxloom-trove/reports/assay-WAVE-D-v10-BRIEF-1.md
```

`GATE_EXIT=1`, zero `ASSAY_REGISTERED_GATE_COMPLETE=1` markers. **Cause: I
wrote BRIEF-1 into the worktree while the gate was running**, so an untracked
file existed when the self-hosted lane read the tree. That is the wave
prompt's own "commit before you gate (an untracked file is DIRTY_TREE)" rule,
broken by writing a file DURING the run rather than before it. The lesson is
narrower than the rule as written and worth stating: **the worktree must stay
untouched for the whole gate run, not merely be clean at launch.**

**Run 3 — GREEN.** Single run on `299d18a0` (the clean tip after run 2's
cause was committed), launched detached from `/workspaces/vbpub`, worktree
untouched for the whole run. Verdict read in a SEPARATE step from the log's
own markers, never from the launcher's status:

```
$ grep -c 'ASSAY_REGISTERED_GATE_COMPLETE=1' <log>   -> 1
$ grep 'GATE_EXIT=' <log>                            -> GATE_EXIT=0
$ grep -c -E 'FAILED|DIRTY_TREE|Traceback' <log>     -> 0
Created wheel for assay: filename=assay-4.1.1.dev5+g299d18a0-py3-none-any.whl
  size=517257 sha256=3b469a2b62be3e370f0b64ce5294fb6671b53c7bf72ddbce19c325e9823aae00
tester-unified: PASS (exit 0)
  commit: 299d18a0e6e76fb2372af6b919b845f76558cfb3
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
```

The wheel name carries the judged commit (`g299d18a0`), which is the commit
the lane reports and the tip that was gated. **`299d18a0` is the
gate-verified commit of generation 1.**

### 3. `docs(assay): record the green gate on 299d18a0`

- No product code, no test change. This LOG's run-3 entry, the REPORT's gate
  transcript, and BRIEF-1 §6's gate state.
- **This commit is a docs-only successor to the gate-verified tip.** The
  gate-verified commit stays `299d18a0`; nothing executable changed after it,
  so re-gating for a changelog entry would only reproduce the same result at
  ~12 minutes' cost. Generation 2 gates its own first product commit.

## Generation 2

**Id allocation, re-checked against `main` before allocating** (BRIEF-1 §7's
own instruction, because another branch may have landed in between):

```
$ git -C /workspaces/vbpub show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git -C /workspaces/vbpub show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
$ git -C /workspaces/vbpub log --oneline -1 main
a4a865da docs(assay): Wave D dispatched -- ...
```

Unchanged from BRIEF-1: `main` is still `a4a865da`, the last decision on main
is **A-407** and the last backlog entry **B061**. Generation 1 took A-408, so
this generation allocates from **A-409** and, if it files anything, from
**B062**. No collision with a concurrent branch.

**Item order, and why it is not the wave prompt's.** The wave prompt lists
B054 (item 2) before B053 (item 3); BRIEF-1 §3 explicitly leaves the order to
generation 2 because both items write to the same `diagnostics` stream and
the plumbing should land once. **B053 was done first.** Its emitter is the
smaller, self-contained piece and it establishes the one format; B054's
per-file skip notice is then a second writer on a stream whose contract is
already fixed and tested, rather than two half-specified writers landing
together. Nothing in B054 turned out to depend on B053's emitter (the skip
notice is not a refusal), so the order was a convenience, not a constraint.

### 4. `fix(assay): every refusal says WHY, once, through one emitter (B053 a+b, A-409)`

- Item: **B053** halves (a) and (b), ruling **DA-D2 (a)+(b)** as read by the
  controller in **DA-R1**. Half (c) (the wire `detail` field) is phase 2 and
  is untouched.
- Changed: `src/assay/runner.py` (the emitter `announce_refusal` at `:307`,
  `__all__`, `evaluate_r1`'s new `diagnostics` parameter, 13 call sites),
  `src/assay/cli.py` (three existing prints refactored onto the emitter),
  `tests/test_refusal_announcement.py` (new, 9 tests),
  `tests/test_cli_run.py` (one test whose docstring asserted the OLD silence
  — inverted, with the reason), `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`,
  `CHANGES.md`, `nyxloom-trove/decisions.md` (A-409),
  `nyxloom-trove/4-backlog.md` (B053 resolution block for (a)+(b); the entry
  stays OPEN for (c)).
- **Red-first**, against the pre-fix tree in a detached scratch worktree
  (`git worktree add --detach <scratchpad>/prefix-b053 36ac802c`, the new test
  file copied in — no `git stash` anywhere): **6 failed, 3 passed**. The 3
  that pass on both sides are deliberate controls: the
  `REASON_CODES`-completeness check (a property of `errors.py`, not of the
  fix), the CLI-boundary refusal (which already printed — that is exactly why
  a boundary-only handler is not the fix), and the no-`diagnostics` default.
  With the fix: **9 passed**.
- **Whole suite**, worktree-local, `pytest tests/ -q -p no:randomly`:
  **3960 passed, 20 skipped in 526.62s**. One pre-existing test failed on the
  first pass and was corrected rather than worked around —
  `test_run_refuses_a_missing_required_infrastructure_env_var_without_crashing`
  asserted, in its own docstring, that this refusal "did NOT gain a stderr
  message of its own". That silence is the defect B053 filed; the test now
  asserts the line and that it names `mynet` and the missing variable.
- No `verdict.py` / `verify.py` / schema / drift-guard file touched; no `!`.

### 5. `fix(assay): a self-contradictory istanbul branchMap is one file's defect, not the verdict's (B054, A-410)`

- Item: **B054**, ruling **DA-D3** plus the controller's **DA-R2** accepting
  BRIEF-1 §3 option (a) (store the offending arc lines on `FileCoverage`).
- Changed: `src/assay/coverage_parsers/model.py` (the new
  `FileCoverage.contradictory_branch_lines` field, its positivity check and
  the docstring saying why it is stored where `line_directive_remapped` is
  derived), `src/assay/coverage_parsers/coverage_istanbul_json.py`
  (`_contradictory_branch_lines`, `_without_lines`, and the isolation at the
  construction site), `src/assay/statement_attribution.py` (both rebuild
  sites carry the field), `src/assay/evaluate.py`
  (`_refuse_contradictory_branch_arcs` + its two call sites, beside A-405's),
  `src/assay/runner.py` (`_announce_contradictory_branch_records`, called
  from `evaluate_r1`), `tests/test_coverage_istanbul_contradictory_branch_arcs.py`
  (new, 7 tests), `tests/test_coverage_istanbul_branch_arcs.py` (the two
  tests that asserted the OLD verdict-wide refusal, rewritten to assert the
  new disposition), `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `CHANGES.md`,
  `nyxloom-trove/decisions.md` (A-410), `nyxloom-trove/4-backlog.md` (B054
  resolution block, RESOLVED).
- **Red-first**, against the pre-B054 tip `440d5da9` in a detached scratch
  worktree with the new test file copied in: **5 failed, 2 passed**. The two
  that pass on both sides are controls — the judged-file refusal (whose
  disposition is deliberately UNCHANGED; only its origin moved from the
  parser to `evaluate`) and the all-clean lane. With the fix: **7 passed**.
- `evaluate.py` stays pure: it takes no stream and returns the refusal as an
  `AssayError`. The skip notice is written by `runner`, from a fact carried
  on the profile — so DA-R2's "evaluate.py stays pure" holds without a
  return-channel change (see the REPORT for why naming EVERY defective record
  rather than only the skipped ones is a superset, not an invention).
- No `verdict.py` / `verify.py` / schema / drift-guard file touched; no `!`.

### 6. `fix(assay): the release builder writes nothing outside --outdir; three rulings recorded as docs (B060, B056, B055, B009)`

Four items in one commit because three of them are documentation and a
ruling, and the fourth is a five-line builder change with its own outcome
test; splitting them would produce three commits nobody can gate separately.

- **B060 / DA-D14 / A-411.** `gate/distribution/build_release.py`'s
  `build_zipapp` stages under a `tempfile.TemporaryDirectory` instead of
  `outdir.parent / "zipapp-staging"`. Outcome test:
  `tests/test_distribution_build_release.py::test_a_build_writes_nothing_outside_its_own_outdir`,
  which required changing the `built` fixture so each real build targets a
  `dist/` inside an otherwise-empty directory — the shape
  `--outdir <repo>/assay/dist` has. The test never builds into the real
  repository: doing that during the suite would itself dirty the tree the
  self-hosted gate lane judges, which is the very failure B060 is about.
- **B056 / DA-D13 / A-412.** Option 1. `tests/test_verdict_schema_is_packaged.py`'s
  module docstring no longer states the measurement A-396 refuted, and
  `test_pyproject_declares_the_schema_as_package_data`'s failure message —
  which carried the same refuted claim — now says why the declaration is
  kept. `tests/test_go_helper_is_packaged.py` was VERIFIED, not rewritten, as
  DA-D13 instructs: its docstring already states the corrected position and
  it already asserts the outcome.
- **B055 / DA-D12 / A-413.** Ruling + docs only. CONSUMERS' Go point 4 now
  opens with "a Go R1 claim is statement-granular TO THE LINE, not to the
  statement".
  `test_lit_go_drops_the_fabricated_signature_but_still_launders_line_four`
  is untouched, as the ruling requires.
- **B009 / DA-D16.** Docs only. New CONSUMERS section "What `assay.toml` is,
  and what it is not". **Item 2's premise was measured and found FALSE**: the
  entry says per-repo vendoring "is RETIRED as the estate pattern", and all
  four consumers vendor a pinned `.pyz` today (ciu 3.2.0, cmru 2.3.0, nyxloom
  4.0.0, dstdns 4.0.0, each through `run-gate.toml`'s `assay_command`).
  DA-D16 says explicitly not to prescribe that future unless it is already
  true, so the docs describe the vendored pin as the pattern to copy and name
  the image-bake direction as unshipped.
- Also corrected in CONSUMERS while passing: the Go section's paragraph
  saying a judge-phase refusal's text "reaches only a caller that invokes the
  evaluation layer itself" and naming B053 as unfixed. B053 shipped in commit
  4 of this generation, so that paragraph was false as written.
- No `verdict.py` / `verify.py` / schema / drift-guard file touched; no `!`.
