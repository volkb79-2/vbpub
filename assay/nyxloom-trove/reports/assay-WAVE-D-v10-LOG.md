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

### Gate run, generation 2

**One run, first try, GREEN, on `c80b3452`.** Launched detached from
`/workspaces/vbpub`, worktree committed clean and left untouched for the
whole run (BRIEF-1 §6's second trap). BRIEF-1 §6's first trap was avoided by
putting the log path as a literal INSIDE the `bash -c` string rather than
assigning a shell variable in front of a backgrounded list. Exactly one gate
process and one `tester-unified` container were confirmed within 8s of
launch:

```
$ pgrep -af 'tester-unified-gate.sh'
439461 bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-d-v10
$ docker ps --format '{{.ID}} {{.Image}} {{.Names}}' | grep -i tester-unified
edfd632eaecf tester-unified:local crazy_moser
```

Verdict read in a SEPARATE step from the log's own markers, never from the
launcher's status:

```
$ grep -c 'ASSAY_REGISTERED_GATE_COMPLETE=1' <log>   -> 1
$ grep 'GATE_EXIT=' <log>                            -> GATE_EXIT=0
$ grep -c -E 'FAILED|DIRTY_TREE|Traceback' <log>     -> 0
Created wheel for assay: filename=assay-4.1.1.dev9+gc80b3452-py3-none-any.whl
  size=522688 sha256=c67b74feae1ccb866c40b1b810fdd5ec9e4d38be267d81e0b56789d8d8927b0c
tester-unified: PASS (exit 0)
  commit: c80b34521150c82a8dc87760e987b54e2f977c55
ASSAY_GATE_PHASE=verdict-v6-v7-v8-hard-cut-verified
ASSAY_GATE_PHASE=verdict-v9-successors-verified
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
GATE_EXIT=0
```

The wheel name carries the judged commit (`gc80b3452`), which is the commit
the lane reports and the tip that was gated. **`c80b3452` is the
gate-verified commit of generation 2.**

The v9 schema phases both passed, which is the mechanical confirmation that
phase 1 is still releasable on v9 — no `verdict.py`, `verify.py`, schema or
drift-guard file was touched by any of this generation's three commits.

### 7. `docs(assay): Wave D generation 2 checkpoint -- BRIEF-2 and the green gate on c80b3452`

- No product code, no test change. `nyxloom-trove/reports/assay-WAVE-D-v10-BRIEF-2.md`
  (new) and this LOG's gate entry.
- **A docs-only successor to the gate-verified tip.** The gate-verified
  commit stays `c80b3452`; nothing executable changed after it, so re-gating
  for a brief would reproduce the same result at ~13 minutes' cost.
  Generation 3 gates its own first product commit.

### A trap of generation 2's own, recorded

`git commit` was issued after `cd /workspaces/vbpub` — the MAIN checkout, not
the worktree. It failed on a pathspec (the new test file is untracked there)
rather than committing to `main`, which was luck, not design. **Every git
command belongs in the worktree (the tool's default cwd); the only thing that
belongs in `/workspaces/vbpub` is the gate launch.** The same `cd` also made
`git log --oneline -1` report main's tip, which is how it was noticed that
`main` had moved to `9b0bca62` — see the id re-check in BRIEF-2 §8.

## Generation 3

Next-free-id re-check before allocating, run from the worktree against
`main` (which had moved again, to `ba741c3b`, the controller's own log entry
— assay's ledgers untouched):

```
$ git show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
```

Generation 2 allocated through A-413, so **A-414** is this generation's first
free row; no new backlog entry filed at this commit.

### 8. `fix(assay): the last six silent refusals speak, and a superseded one stops (B053, A-414)`

- Items: **B053 follow-ups**, rulings **DA-R3** and **DA-R4** (the controller's
  rulings on generation 2's decision asks 1 and 2).
- **DA-R3.** Six refusal sites that called `refuse_lane`/`refuse_all` with a
  bare `(status, reason_code)` literal now compose their sentence where the
  fact is known and hand it to the SAME `announce_refusal`:
  `src/assay/runner.py:3792` (`DIRTY_TREE`, snapshot path),
  `:3806` (`HEAD_CHANGED`), `:4056` (`MISSING_EXTERNAL_TOOL`),
  `:4219` (`env_required`), `:4284` (bad `--shard`),
  `:4369` (`DIRTY_TREE`, direct-R0 path). An `AssayError` is built at each
  purely to carry the message through the one emitter — no new exception
  type, no new reason code, no wire change.
- **DA-R4.** The `equivalence_artifact` early-R2 refusal (A-279) is no longer
  announced where its claim is built. `runner.py:2855` records it in a new
  `r2_deferred_early_error` local; `runner.py:3194-3200` announces it only in
  the `elif r2_early_claim is not None` branch — the point where the early
  claim actually SURVIVES into the document. Deferred for that one site only;
  no general buffer (every other early-R2 refusal is set inside the
  `result.outcome is Outcome.PASS` guard and cannot be superseded).
- **The DA-R4 defect was reproduced before it was fixed**, through the
  installed CLI on a real SQL R2 lane (`equivalence_artifact` is a `sql`-only
  key, P34/W4) whose command exits 7 without writing the declared artifact:

  ```
  assay: ERROR/EXEC_FAILED: the baseline declared judge.mutation.equivalence_artifact
    '.assay/schema-dump.sql' but its own command did not write it -- ...
  ...
  "claims": [ {"reason_code": "COMMAND_FAILED", "rigor": "R0", ...},
              {"reason_code": "COMMAND_FAILED", "rigor": "R2", ...} ]
  ```

  The line and the document disagreed, which is exactly DA-R4's objection.
- **Red-first**, run in the worktree with only the test file added and no
  source change: **7 failed / 10 passed**. The 10 are generation 2's 9 plus
  the new DA-R4 control (an early refusal that SURVIVES was already announced
  correctly — that one must pass on both sides or the pair proves nothing).
  Every failure was `assert 0 == 1` on the refusal-line count, or
  `assert not True` on the superseded line. With the fix: **17 passed**.
- Changed: `src/assay/runner.py`, `tests/test_refusal_announcement.py`,
  `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `CHANGES.md`,
  `nyxloom-trove/decisions.md` (A-414), `nyxloom-trove/4-backlog.md` (B053
  resolution addendum; the "known limit" paragraph struck through, not
  deleted, because A-409 still cites it).
- No `verdict.py` / `verify.py` / schema / drift-guard file touched; no `!`.

### 9. `fix(assay): a lane that runs out of time writes its verdict, and a timeout is never called GIT_FAILED (B028, A-415)`

- Item: **B028**, ruling **DA-D10**.
- **Measured before anything was changed** (at `21bdf19d`, through
  `assay.cli.main`, `budget = "1s"`, `argv = ["/bin/sh","-c","sleep 30"]`,
  no stubbed clock), and the measurement CORRECTS the entry's 2026-08-25 one:

  | dispatch | reserved `--verdict-json` | exit |
  |---|---|---|
  | higher-rigor (`R0`+`R1`) | **written**, `assay verify` accepts it | 4 |
  | direct R0 (`R0` only) | **never created** | 4 |

  So `_run_higher_rigor_lane`'s single outer `try` (`runner.py:3819`) was
  already the boundary DA-D10 asks for, and only the direct-R0 half of the
  ruling was outstanding. BRIEF-2 §3 guessed the catch belonged beside
  `runner.py:3776`; reading that handler showed it was already correct, and
  that its real gap was the OPPOSITE one (below).
- **The direct-R0 boundary:** `src/assay/runner.py:4465-4551`, spanning
  `execute_plan` and the post-command dirt/HEAD guard. That guard moved
  verbatim into a new `_finish_direct_r0_lane` (`runner.py:4552`) so that
  sixty lines of A-175/A-178 reasoning did not have to be re-indented into a
  `try`; nothing in it changed but its address.
- **Never masks the timeout.**
  `_replace_highest_higher_rigor_claim_with_git_failed` (`runner.py:3609`)
  gained `status`/`reason_code` keyword parameters defaulting to
  A-193/A-194's `ERROR`/`GIT_FAILED`; `_run_higher_rigor_lane`'s existing
  `except AssayError` (`runner.py:3916`) passes the refusing error's own pair
  when it is a `LANE_TIMEOUT`. Cleanup after a completed run that failed
  because the lane ran out of time is a timeout, not a Git failure.
- **Red-first:** `tests/test_lane_timeout_writes_a_verdict.py` against
  `21bdf19d` — **3 failed / 4 passed**; with the fix, **7 passed**. The four
  pre-fix passes are deliberate controls: the two higher-rigor regression
  guards (already correct) and the `GIT_FAILED` control that proves
  A-193/A-194's rule is narrowed rather than replaced.
- Broader regression sweep, `-k "r0 or runner or cli_run or timeout or
  budget"`: **535 passed, 1 skipped** in 127.80s.
- Changed: `src/assay/runner.py`,
  `tests/test_lane_timeout_writes_a_verdict.py` (new), `docs/CONSUMERS.md`,
  `docs/DESIGN-GUIDE.md`, `CHANGES.md`, `nyxloom-trove/decisions.md`
  (A-415), `nyxloom-trove/4-backlog.md` (B028 acceptance ticked, RESOLVED).
- No `verdict.py` / `verify.py` / schema / drift-guard file touched; no `!`.

### 10. `fix(assay): the canary side-run resolves infrastructure; B029's premise corrected by measurement (B029, A-416)`

- Item: **B029**, ruling **DA-D11** as re-scoped by **DA-R6** ("MEASURE
  FIRST").
- **The measurement, at `dd8f4d2c`, through `assay.cli.main`.** A real R3
  lane: `rigor = ["R0","R3"]`, `judge.canary.mechanism = "import-break"`, a
  real gitignored `ciu.global.toml`, `[infrastructure] ASSAY_B029_FACT =
  "derived:deploy.cgroup_parent"`, and a pytest suite that asserts
  `os.environ["ASSAY_B029_FACT"] == "assay-b029.slice"`:

  ```
  package: PASS (exit 0)
  "rigor": "R3", "status": "PASS",
  "canary": {"control_outcome": "PASS", "transformed_outcome": "FAIL",
             "observed_reason_code": "COMMAND_FAILED"}
  ```

  **It does NOT reproduce.** The control half ran the fact-asserting suite
  and passed, which is direct evidence the shipped side-run sees the lane's
  infrastructure world. Generation 2's decision ask 4 was right: the shipped
  path is `run_isolated_canary`, which takes an already-executed
  `unit.result` and never reaches `execute_command`.
- **DA-R6's second branch taken.** `runner.execute_command`
  (`runner.py:828`) and `canary._run_pipeline` / `canary.run_python_canary`
  (`canary.py:188`, `:225`) now take and forward
  `infrastructure_source`/`infrastructure_environment`, defaulting to `None`.
  `execute_command`'s docstring, which named the defect as live, states the
  measurement instead.
- **The test is labelled a regression guard, not a red-first proof**, because
  there was nothing red: `tests/test_r3_canary_sees_infrastructure.py`, 2
  tests. Suite check including every neighbouring canary module
  (`test_canary_python_pipeline`, `test_runner_run_lane_r3`,
  `test_canary_p23_isolated_edges`): **23 passed in 75.68s**.
- A trap worth recording: the first two measurement runs refused
  `NO_MEASUREMENT`/`DIRTY_TREE` because `--verdict-json` was written INSIDE
  the scratch repository. The new DA-R3 message named the offending file
  (`Affected: v.json`) and the diagnosis took one read instead of a bisect —
  commit 8's own value, observed in the field within the hour.
- Changed: `src/assay/runner.py`, `src/assay/canary.py`,
  `tests/test_r3_canary_sees_infrastructure.py` (new), `docs/CONSUMERS.md`,
  `CHANGES.md`, `nyxloom-trove/decisions.md` (A-416),
  `nyxloom-trove/4-backlog.md` (B029 RESOLVED BY MEASUREMENT).
- No `verdict.py` / `verify.py` / schema / drift-guard file touched; no `!`.

### 11. `backlog(assay): B024's gate wiring is blocked -- measured, nothing landed (DA-D15)`

- Item: **B024**, ruling **DA-D15** — its escape hatch, taken.
- Three checks, in order: the `tester-unified:local` image carries neither
  `pyflakes` nor `ruff` (by either invocation); the offline wheelhouse holds
  exactly five hash-pinned build wheels and no linter; the gate installs
  `--no-index` from that wheelhouse and has no other ingress. The image's
  `Dockerfile` is outside `assay/**` and this wave forbids touching it.
- **Nothing landed.** No phase in `tools/tester-unified-gate.sh`, no line in
  `gate/distribution/build-requirements.txt`, no wheel in
  `gate/distribution/build-wheelhouse/`. The decision ask is in the REPORT
  with three options and a recommendation the implementer does not take.
- Changed: `nyxloom-trove/4-backlog.md` (B024 acceptance box annotated
  BLOCKED + a dated measurement note with the transcript),
  `nyxloom-trove/reports/assay-WAVE-D-v10-REPORT.md`, this LOG. No source,
  no test, no CHANGES bullet — there is nothing for a consumer to read about.

### Gate run, generation 3

**One run, first try, GREEN, on `93188912` — the PHASE-1 TIP, 10 of 10 items
resolved.** Launched detached from `/workspaces/vbpub` exactly as BRIEF-2 §7
shows (the log path a literal inside the `bash -c` string), worktree
committed clean and left untouched for the whole run. Exactly one gate
process and one `tester-unified` container confirmed 20s after launch:

```
$ pgrep -af 'tester-unified-gate.sh'
664156 bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-d-v10
$ docker ps --format '{{.ID}} {{.Image}} {{.Names}}' | grep -i tester-unified
05617a469ec7 tester-unified:local dazzling_yalow
```

Verdict read in a SEPARATE step from the log's own markers:

```
$ grep -c 'ASSAY_REGISTERED_GATE_COMPLETE=1' <log>   -> 1
$ grep 'GATE_EXIT=' <log>                            -> GATE_EXIT=0
$ grep -c -E 'FAILED|DIRTY_TREE|Traceback' <log>     -> 0
Created wheel for assay: filename=assay-4.1.1.dev14+g93188912-py3-none-any.whl
  size=526694 sha256=087415a9227f86ce9eb9ce7b0b1084911b5d083e19a7f855930cf2a1c6a299f2
tester-unified: PASS (exit 0)
  commit: 931889122cf663469a81e4db6e5e990c43d0263d
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
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
GATE_EXIT=0
```

The wheel name carries the judged commit (`g93188912`), which is the commit
the self-hosted lane reports and the tip that was gated. **`93188912` is the
gate-verified PHASE-1 TIP.**

Both v9 schema phases passed — the mechanical confirmation that phase 1 is
still releasable on v9: no `verdict.py`, `verify.py`, schema or drift-guard
file was touched by any of this generation's four commits, and no commit on
the branch carries `!`.

Whole suite, worktree-local, immediately before the gate: **3985 passed, 20
skipped in 538.51s**, zero failures (generation 2's figure was 3968; the 17
added are 8 for B053's follow-ups, 7 for B028, 2 for B029).

### 12. `docs(assay): Wave D generation 3 checkpoint -- phase 1 complete, BRIEF-3, green gate on 93188912`

- No product code, no test change.
  `nyxloom-trove/reports/assay-WAVE-D-v10-BRIEF-3.md` (new) and this LOG's
  gate entry.
- **A docs-only successor to the gate-verified tip**, as generation 2 did.
  The gate-verified commit stays `93188912`; nothing executable changed after
  it, so re-gating for a brief would reproduce the same result at ~40
  minutes' cost.

### An environment note of generation 3's own

A foreground `sleep` is blocked in this harness and a long `Bash` call is
moved to the background, so polling the gate log by hand does not work — the
wall clock barely advanced across several apparent waits. **Arm a `Monitor`
with `until grep -q 'GATE_EXIT=' <log>; do sleep 30; done` at launch time and
let it fire.** That is what finally reported this run.

---

## Generation 4

Next-free-id re-check before allocating, run from the worktree against `main`
(which had moved again, to `72bc041f`, the controller's own Wave D log entry
recording phase 1 complete and DA-R7 — assay's ledgers untouched):

```
$ git log --oneline -1 main
72bc041f docs(assay): Wave D controller log -- phase 1 complete (gate PASS on 93188912), DA-R7 ruled (B024 lint closure), R-1 and generation 4 dispatched
$ git show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
```

Generation 3 allocated through A-416, so **A-417** is this generation's first
free row, and **B062** the first free backlog id. Both are used by the commit
below.

### 13. `fix(assay): the registered gate lints its own source, from its own hash-bound closure (B024, A-417)`

- Item: **B024**, ruling **DA-R7** (controller, superseding DA-D15's escape
  hatch, which generation 3 correctly took).
- **Option (b) of the three the decision ask offered: pyflakes only, in its
  own closure.** Nothing else was landed and nothing outside `assay/**` was
  touched; the shared `tester-unified` image is unchanged.
- What landed:
  - `gate/distribution/lint-requirements.txt` (new) — one line,
    `pyflakes==3.4.0 --hash=sha256:f742a7db…`.
  - `gate/distribution/lint-wheelhouse/pyflakes-3.4.0-py2.py3-none-any.whl`
    (new) — 63551 bytes, sha256
    `f742a7dbd0d9cb9ea41e9a24a918996e8170c799fa528688d40dd582c8265f4f`,
    fetched ONCE on this networked devcontainer with `pip download pyflakes
    --no-deps` (the gate itself never has a network). pyflakes has no
    dependencies and is `py2.py3-none-any`, so the closure is one file with
    no platform matrix.
  - `gate/distribution/lint-wheelhouse-manifest.json` (new) — the same
    sha256/size beside `build-wheelhouse-manifest.json`, same
    `schema_version: 1` shape.
  - `tools/tester-unified-gate.sh:84` `build_lint_venv` — a **third** venv,
    `$scratch/lint-venv`, installed `--no-index --require-hashes` and
    version-asserted. **`build_offline_closure_venvs` is untouched**, so
    A-198's five-wheel assertion at `:59-72` is byte-for-byte what it was.
  - `tools/tester-unified-gate.sh:117` `run_lint_phase` — `python -m
    pyflakes "$scratch/clone/assay/src/assay"`, i.e. the **private exact-OID
    clone**, not the bind-mounted worktree; `die` on any finding; `:121`
    emits `ASSAY_GATE_PHASE=pyflakes-clean`.
  - Called at `:623-624`, after `run_independent_witness` — after the suite,
    as DA-R7 requires.
- **The sweep was re-run before wiring** (B024's original sweep is from
  2026-08-25 and could have drifted). It had not: `src/assay` → 0 findings,
  `gate/` → 0 findings. `tests/` → **31 findings across 19 modules** plus
  `tests/fixtures/mutation/python/broken.py`, a deliberately unparseable
  fixture pyflakes can never pass — so DA-R7's "and tests/ if clean" resolves
  to "not tests/", and the sweep is filed as **B062** rather than folded in.
- **Red-first**, in a detached scratch worktree at `b90ca598` (never a bare
  `git stash`) with only the new tests copied in:

  ```
  $ git worktree add --detach <scratch>/b024red HEAD
  $ cp assay/tests/test_distribution_gate.py <scratch>/b024red/assay/tests/
  $ cd <scratch>/b024red/assay && python -m pytest tests/test_distribution_gate.py \
      -q -p no:randomly -k "lint or pyflakes or planted or undefined_name or shipped_source"
  3 failed, 15 deselected, 1 warning, 3 errors in 0.73s
  ```

  The three failures are the static assertions (no pin file, no
  `build_lint_venv`, no `pyflakes-clean` marker); the three errors are the
  `lint_venv` fixture, which cannot build a closure that does not exist at
  `HEAD`. Green on the branch: `21 passed` for that module,
  `92 passed` for it plus `test_docs_examples_and_vocabulary.py` and
  `test_distribution_build_release.py`.
- **The planted-import proof DA-R7 asked for, as a scratch run of the gate's
  OWN functions** (function definitions sourced from the real script, the
  hardcoded `/opt/tester-venv` interpreter swapped for this cockpit's, a real
  copy of `src/assay` as the clone):

  ```
  --- CLEAN RUN ---
  ASSAY_GATE_PHASE=pyflakes-clean
  CLEAN_EXIT=0

  --- PLANTED UNUSED IMPORT IN verdict.py ---
  <scratch>/clone/assay/src/assay/verdict.py:1:1: 'os' imported but unused
  <scratch>/clone/assay/src/assay/verdict.py:57:1: from __future__ imports must occur at the beginning of the file
  tester-unified-gate: pyflakes reported findings in src/assay (see the lines above)
  PLANTED_EXIT=1
  ```

  The same proof is a permanent test:
  `tests/test_distribution_gate.py:641` (unused import) and `:671`
  (undefined name) assert the non-zero exit, the named file and line, and the
  ABSENCE of the marker; `:695` runs the identical locked pyflakes over the
  real shipped `src/assay`, so a finding surfaces in `pytest tests` instead
  of only after a nine-minute container run.
- One existing assertion moved with the code:
  `tests/test_distribution_gate.py`'s `gate_functions` fixture counts uses of
  `/opt/tester-venv/bin/python` in the function definitions and asserts the
  count. `build_lint_venv` resolves the same base prefix the other two venvs
  are cut from, so the count is **4**, not 3 — the fixture's own docstring
  invites exactly that update.
- Docs: `docs/DESIGN-GUIDE.md` §14's "Two venvs, not one" becomes "Two venvs
  for the wheel, not one" plus "And a third for the linter", which states why
  neither existing venv was the right home, why the clone rather than the
  worktree, why after the suite, and that the scope is a measurement.
  `CHANGES.md` gains a `### Changed` bullet.
- **No schema work in this commit**: `verdict.py`, `verify.py`,
  `src/assay/schemas/` and the drift-guard carve-assets are untouched, and
  the commit carries no `!`. The phase-1 fallback release can carry it.

### Registered gate — GREEN on `7c9e8dd1`, with the new phase present

Launched exactly as BRIEF-2 §7 shows (log path a literal inside the `bash -c`
string; worktree committed clean and left untouched for the whole run).

**A launch-check correction worth passing on.** BRIEF-1 §6 says to confirm
exactly one gate process and one container. Two `tester-unified` containers
were running at launch — but the second was **reviewer R-1's**, on its own
worktree `.worktrees/assay-wave-d-r1`, which is expected and is not the
BRIEF-1 hazard (two gates appending to ONE log). `pgrep -af
'tester-unified-gate.sh'` shows the worktree path in the argv; check THAT,
not the count. Also: `docker ps --format '{{.Names}}'` never matches
"tester-unified" — container names are random (`hardcore_euler`), so grep the
IMAGE. Twice this generation that mistake read as "my gate died".

Verdict read in a SEPARATE step from the log's own markers:

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

The wheel name carries the judged commit (`g7c9e8dd1`). **`7c9e8dd1` is the
gate-verified commit**, and `ASSAY_GATE_PHASE=pyflakes-clean` is B024's new
phase, running last, in the real container, from the committed one-wheel
closure — the end-to-end proof the scratch transcript could not give.

Both v9 schema phases passed again: no `verdict.py`, `verify.py`, schema or
drift-guard file was touched, and no commit on the branch carries `!`.

**No whole-suite run preceded this gate** — see the throttle below. The
targeted runs were `tests/test_distribution_gate.py` (21 passed) and that
module plus `test_docs_examples_and_vocabulary.py` and
`test_distribution_build_release.py` (92 passed).

### HOST LOAD THROTTLE — controller directive, 2026-09-02, binding for the rest of the wave

The host's 1-minute load reached **85 on 8 cores** and degraded a production
game server sharing it, from this wave's own concurrent work (R-1's pytest
plus a gate container). The controller capped this generation's running gate
container to 3 CPUs live (`docker update --cpus=3`); it finished green,
slower. **Load fell 85 → 10.28 → 7.45 over the rest of the run.** The rules,
which generation 5 inherits:

1. **Never `pytest -n` / xdist.** Serial only, prefixed `nice -n 19 ionice -c
   3`. Prefer targeted test files; run the whole suite **at most once per
   checkpoint**.
2. **Never two gate containers at once.** Before launching, check `docker ps
   | grep tester-unified` is EMPTY and wait if it is not — R-1 runs one too.
   Immediately after launching, run
   `docker update --cpus=3 $(docker ps -q --filter ancestor=tester-unified:local)`.
3. **No build/pip/wheel step concurrently with a suite run.** (B024's
   `pip download` is tiny and exempt.)

### 14. `docs(assay): Wave D generation 4 checkpoint -- B024 landed and gate-green, R-1 round 1 filed, BRIEF-4`

- No product code, no test change. Records only:
  `nyxloom-trove/reports/assay-WAVE-D-v10-REVIEW-R1-round1.md` (R-1's round-1
  report, copied **verbatim** per the controller's package step (a)),
  `nyxloom-trove/reports/assay-WAVE-D-v10-BRIEF-4.md` (new),
  `nyxloom-trove/carve-assets/W2/ciu-provenance-live-mismatch-ciu-7.10.1.json`
  (new frozen asset) with its `MANIFEST.md` addendum, this LOG's gate entry,
  and the REPORT's generation-4 sections.
- **A docs-only successor to the gate-verified tip**, as generations 2 and 3
  did. The gate-verified commit stays `7c9e8dd1`; nothing executable changed
  after it.
- **Checkpoint taken here on the controller's instruction.** R-1's round 1 on
  the phase-1 tip `93188912` came back **NOT ACCEPT** (2 blockers, 5
  should-fixes) while this generation's gate was running. The controller
  ruled the fix package lands on this branch BEFORE the v10 cut, and that
  since generation 4 is at/past its E-008 threshold it should cut rather than
  start the package — one owner at a time on this worktree. BRIEF-4 carries
  the package as generation 5's FIRST work item.
- **The ciu re-capture DA-D7 demands was done here** rather than deferred,
  because it needs a live ciu 7.10.1 and a running dstdns instance, both of
  which were available. Transcript and the measured schema-1-vs-schema-2
  delta are in the REPORT and in the W2 `MANIFEST.md` addendum.

---

## Generation 5

### 15. `docs(assay): R-1 round 1's FINAL report, verbatim` — `e44c1056`

- Records only. Generation 4 had committed the 756-line interim copy R-1 had
  written at the time; R-1's FINAL report — with the serialized targeted
  mutation reruns landed — is **839 lines**. The repo copy is now that text,
  byte-for-byte (`cp` of the scratchpad original; `git diff --stat` = 136
  insertions, 53 deletions).
- What the final version adds, and why it mattered to this generation's work:
  `m3`/`m4`/`m6`/`m7` RED as expected; **`m1`/`m2` GREEN**, corroborating
  BLOCKER 2 at BOTH ends of the threading; **`m5` GREEN**, so SF-5 stands as
  insurance rather than being struck.

### 16. `fix(assay): R-1 round 1's fix package -- both blockers and all five should-fixes (A-418..A-424)` — `8895ffbf`

**One commit, seven A-rows.** Stated in the commit message and repeated here
because it is a deviation from "each fix is a commit": the seven fixes
interleave inside `runner.py`, `decisions.md` and `CHANGES.md`, so splitting
by path would have put hunks under commits that did not make them. Each fix
still carries its own A-row.

| ruling | A-row | what landed |
|---|---|---|
| DA-R8 (BLOCKER 1) | **A-418** | both POST-command dirt/HEAD guards announce, on both dispatch paths |
| — (BLOCKER 2) | **A-419** | the signature-only B029 test replaced by a value assertion, proven red |
| DA-R9 (SF-1) | **A-420** | `LANE_TIMEOUT`-scoped handlers in `cli._run_reserved` |
| — (SF-2) | **A-421** | the silent `OSError` → `GIT_FAILED` says what failed |
| — (SF-3) | **A-422** | `_report_probe_refusal` folded onto the one emitter |
| — (SF-4) | **A-423** | `try`/`finally` around the two per-mutant reservation reads |
| — (SF-5) | **A-424** | the `statement_attribution` carries documented as insurance |

**Seams touched** (line numbers as of `8895ffbf`):

- `src/assay/runner.py:352` new `post_command_refusal` — the ONE composer both
  dispatch paths use, so the two sentences cannot drift.
- `src/assay/runner.py:2165-2166` `SnapshotUnitResult.post_dirty` /
  `.post_observed_head`; kept at `:2422-2423`; returned at `:2502`; announced
  at `:2901-2909` in `_run_prepared_lane` (`where=baseline_snapshot.root`).
- `src/assay/runner.py:4683` `_finish_direct_r0_lane`, now with
  `diagnostics: "TextIO | None" = None` at `:4696`, threaded at its single
  call site (`:4629`); the guard keeps both facts at `:4748-4749` and
  announces at `:4757`.
- `src/assay/runner.py:4045` `except OSError as exc:` → composed and
  announced at `:4052`.
- `src/assay/runner.py:509` `_report_probe_refusal` → `announce_refusal`;
  the probe's stderr/stdout tails still print as indented context lines below
  the one line.
- `src/assay/cli.py:685` the `head_rev` call now inside a `try` (`:684`),
  with the `LANE_TIMEOUT` handler below it; `:827` the `try` around
  `runner.run_lane` with the second handler.
- `src/assay/mutation.py:1647` `try` / `finally` around the two reservation
  reads and both `close()` calls.
- `src/assay/statement_attribution.py:243`, `:364` — the two carries, with
  their docstrings now stating the measurement.

**Tests, all red-first:**

- `tests/test_refusal_announcement.py` +163 lines, three CLI-level tests
  (post-command DIRTY_TREE on direct R0, post-command HEAD_CHANGED on direct
  R0, post-command DIRTY_TREE on the snapshot path). Red proof in a detached
  scratch worktree at `e44c1056`: **3 failed**, each on
  `len(_refusal_lines(...)) == 1` → `assert 0 == 1`. Green after: 20 passed.
- `tests/test_r3_canary_sees_infrastructure.py`: the hollow test replaced by
  two value tests. Red proof by deleting each forward in turn (detached
  scratch worktree, never a stash) — the four runs are in the REPORT.
- `tests/test_lane_timeout_writes_a_verdict.py` +134 lines, four tests
  (`0.001s` per dispatch path; the injected-at-the-seam pair; the
  non-timeout-is-never-laundered guard). Red proof: "the reserved
  --verdict-json was never written: exit 4" on both `0.001s`
  parametrisations. Green after: 12 passed.

**Targeted suites run (serial, `nice -n 19 ionice -c 3`, never xdist):**

```
test_refusal_announcement.py                                     20 passed
test_r3_canary_sees_infrastructure.py                             3 passed
test_lane_timeout_writes_a_verdict.py                            12 passed
+ test_coverage_istanbul_contradictory_branch_arcs.py
+ test_statement_attribution_go_witnesses.py
+ test_environment_preflight.py + test_cli_run.py
+ test_runner_run_lane.py + test_runner_run_lane_r3.py          148 passed
test_mutation_classification / _judge / _isolation,
test_safeio_replaced_output_directory, test_output_reservation,
test_runner_p23_cleanup_and_budget                               67 passed
test_environment_preflight, test_runner_evaluate_r1,
test_mutation_python_pipeline, test_cli_lanes                    54 passed
python -m pyflakes src/assay                                      0 findings
```

**No whole-suite host run.** The registered gate runs it, once, per the
throttle.

### 17. `backlog(assay): B063 -- three test modules git -C PROJECT_ROOT.parent` — `e3ae8ada`

- R-1's filing, with its measurement (11 failed + 13 errors, **constant** on
  any copy of the tree outside the vbpub checkout, from
  `tests/test_python_qualification.py:42`,
  `tests/test_runner_snapshot_selection.py:805` and
  `tests/test_distribution_build_release.py`). **Not fixed in this wave.**
- **Id check, re-run before allocating, as the wave prompt requires.** The
  controller's package text said "file as B062"; B062 was already taken on
  this branch by generation 4's `tests/` pyflakes sweep (A-417), and the
  controller corrected itself to B063 in the controller log.

```
$ git -C <worktree> show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git -C <worktree> show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
$ git -C <worktree> rev-parse --short main
c35baa9e
```

`main` has moved again (`72bc041f` → `c35baa9e`) and assay's two ledgers are
still untouched on it. **Allocated this generation: A-418..A-424, B063. Next
free: A-425, B064.**

### Registered gate on the fix tip — GREEN, first try

Log: `scratchpad/gate-gen5.log` (this session's scratchpad,
`/tmp/claude-1003/-workspaces-vbpub/e35fad96-4fc2-4781-a1ca-9318989f44a3/scratchpad/gate-gen5.log`).
Launched from `/workspaces/vbpub` after checking `docker ps` showed **no**
`tester-unified:local` container (IMAGE column, not names — names are random)
and `pgrep -af tester-unified-gate.sh` showed none; the container
(`436c0affc8a6`) was capped with `docker update --cpus=3` immediately after
launch (`NanoCpus` re-read as `3000000000`). The worktree was untouched for
the whole run. Verdict read in a SEPARATE step:

```
COMPLETE_MARKERS=1          (ASSAY_REGISTERED_GATE_COMPLETE=1, exactly one)
GATE_EXIT=0
BAD=0                       (grep -cE 'FAILED|DIRTY_TREE|Traceback')
wheel: assay-4.1.1.dev20+ge3ae8ada-py3-none-any.whl
        size=530841 sha256=55a5bee13489546a7ff7471d1f4031d2cc0abc1bfe5c7de062900ca095dfb976
tester-unified: PASS (exit 0)
  commit: e3ae8ada1c4b00364aa9c3e8e320ea7ee9a40e45
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=

ASSAY_GATE_PHASE=wheel-installed
ASSAY_GATE_PHASE=attestation-hardened
ASSAY_GATE_PHASE=verdict-v5-accepted
ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
ASSAY_GATE_PHASE=verdict-v6-v7-v8-hard-cut-verified
ASSAY_GATE_PHASE=verdict-v9-successors-verified
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
ASSAY_GATE_PHASE=cmru-b006a-qualified
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_GATE_PHASE=pyflakes-clean
```

The wheel name carries the judged commit (`ge3ae8ada`). Both v9 schema phases
passed again (`verdict-v6-v7-v8-hard-cut-verified` over 18 frozen templates,
`verdict-v9-successors-verified`), and B024's `pyflakes-clean` still runs
last: nothing under `verdict.py`, `verify.py`, `src/assay/schemas/` or the
drift-guard carve-assets was touched, and no commit on the branch carries `!`.

**FIX-TIP: `e3ae8ada`.** R-1 round 2 reviews `93188912..e3ae8ada`.

Host load through the run: 5.05 → 5.97 at launch, container pinned at ~97 % of
its 3-CPU cap, 194 MiB. No second gate, no xdist, no host whole-suite run.

### 18. `docs(assay): Wave D generation 5 checkpoint -- the R-1 fix package landed and gate-green on e3ae8ada, BRIEF-5`

- Records only: this LOG's generation-5 entries and gate transcript, the
  REPORT's generation-5 sections, and
  `nyxloom-trove/reports/assay-WAVE-D-v10-BRIEF-5.md` (new).
- A docs-only successor to the gate-verified tip, as generations 1–4 did.
  **The gate-verified commit stays `e3ae8ada`**; nothing executable changed
  after it.
- **Checkpoint taken here, at the E-008 boundary the clause names** (green
  gate). Phase 2 was not started: the controller resumes R-1 round 2 on the
  FIX-TIP word, and BRIEF-5 hands phase 2 to generation 6 with BRIEF-4 §5's
  seam table intact and DA-R12 already ruled.

---

## Generation 6 (2026-09-02) — A-425, A-426/SF-6, A-429, phase-2 design rows A-427/A-428, B064

### 19. `ba2f1133 fix(assay): bound the LANE_TIMEOUT commit-label read by a documented grace (DA-R13, A-425)`

- `cli.LABEL_GRACE_SECONDS = 2.0` with the reason in its own docstring;
  `runner.LaneDeadline` constructed directly (its `start` classmethod rejects
  a non-positive budget) and passed as `remaining=grace.remaining` to the one
  `git.head_rev` A-420 left unbounded (`cli.py`, the `LANE_TIMEOUT` handler).
- Grace expiry → **no verdict**, one emitted line naming the commit label and
  the grace, outcome/reason code (hence exit code) still the original
  timeout's. Any OTHER Git fault re-raises the original timeout unchanged.
- Keyword-only `label_grace_seconds` threaded `_cmd_run` → `_run_reserved`,
  defaulting to the constant; the grace-expired tests pass `0.0` **through
  the parameter**, never a stub — git, repository, lane and budget all real.
- Tests in `tests/test_lane_timeout_writes_a_verdict.py`: the existing
  `0.001s` probe (both dispatch paths) now also asserts the REAL `HEAD`; a
  default-grace control; the `0.0` path on both dispatch paths (no artifact,
  one line, the line names the label and the grace); one test pinning the
  constant's value, default and keyword-only kind.
- **Red-proof** (detached scratch worktree, the `remaining=` argument deleted
  and nothing else — the exact A-420 shape): **2 failed / 14 passed**,
  "a verdict was written without a commit label that could be read". Green
  after: 16 passed; `test_cli_run` + `test_refusal_announcement` +
  `test_lane_timeout_writes_a_verdict` together, 74 passed.

### 20. `b69a9248 fix(assay): the last silent terminal in runner.py announces (DA-R15/SF-6, A-426)`

- R-1 round 2's single ACCEPT-condition. `_run_higher_rigor_lane`'s
  `except RuntimeError` announces through `announce_refusal` on the
  `if outcome_holder:` side ONLY; the `raise` side is untouched, with a
  control test asserting nothing is announced and the error propagates.
- **Reachability MEASURED:** the only raiser is
  `isolation.prepare_snapshot`'s leak guard (`isolation.py:1800`), which
  fires only when assay's OWN code leaves a materialization open — so the
  site is NOT reachable from `assay run`, and A-414's "no exceptions" claim
  was already true and stays true. Covered at the seam in two real halves per
  DA-R15: a real leak in a real repository pinning the exception and its
  sentence (`tests/test_isolation.py`), and the handler driven with that same
  pinned sentence through `scratch_root_factory` — assay's own cleanup seam.
- A trap worth keeping: an unreferenced generator-based context manager is
  closed the instant `__enter__` returns, which silently un-leaks the leak.
  The isolation test binds it and says why.
- **Red-proof** against the pre-fix `runner.py` (detached scratch worktree):
  **1 failed / 21 passed**. Green after: 106 passed across both modules.
- Also lands R-1's round-2 report verbatim (416 lines) at
  `reports/assay-WAVE-D-v10-REVIEW-R1-round2.md`, per the controller.

**GATE 1 — GREEN on `b69a9248`.** Log
`scratchpad/gate-gen6.log`: `COMPLETE_MARKERS=1`, `GATE_EXIT=0`, `BAD=0`
(zero `FAILED|DIRTY_TREE|Traceback`), wheel
`assay-4.1.1.dev23+gb69a9248-py3-none-any.whl` (size 532340, sha256
`e61227ecb00b6d108c39b8bb271ae2b0a6c84ad99b17528c03f451a74dfbf125`),
`tester-unified: PASS (exit 0)` at
`commit: b69a92485e285c0a7a38e49add6aa8fd63261926`, twelve phases ending
`ASSAY_GATE_PHASE=pyflakes-clean`; both v9 schema phases passed
(`verdict-v6-v7-v8-hard-cut-verified` over 18 frozen templates,
`verdict-v9-successors-verified`, 47 passed). Host load 5.1–6.5 throughout,
container capped at 3 CPUs immediately after launch.

### 21. `f254b702 feat(assay): the gate's own assay run carries --resume --progress (A-429); design rows A-427/A-428; file B064`

- **A-429** (operator directive 2026-09-02, estate policy; run-gate SPEC
  R-38 / RG-33, run-gate rev 33): `tools/tester-unified-gate.sh`'s
  `assay run tester-unified` gains `--resume --progress
  "$scratch/progress-tester-unified.jsonl"`. Both are no-ops on this R0 lane
  by assay's own contract; the point is a uniform invocation shape. The
  progress path stays in `$scratch` — never the worktree, where an untracked
  file is a self-inflicted `DIRTY_TREE`. One test in
  `test_distribution_gate.py`'s read-the-script shape; one CONSUMERS
  paragraph stating the same rule for consumer gates.
- **A-427 / A-428 — DESIGN ONLY, no wire change.** B050/DA-D6's
  `judgment.r2.fail_under` and B053/DA-D2 (c)'s `claim.detail`, each
  specified field-by-field with the three places, the presence rules, the
  rejected alternatives, and (for `detail`) the byte-vs-character bound split
  and the head-kept truncation with its reason.
- **B064 filed, not implemented** (progress/resume beyond R2), with the
  measured answer per tier and the B007 coupling that B007's own A-row must
  state in one sentence. `main` re-checked before allocating: `b36c6925`,
  last id `## B061`, so B064 was free on both sides.

**GATE 2 — RED on `f254b702`, and correctly so.** `scratchpad/gate-gen6b.log`:
`GATE_EXIT=1`, **4 failed / 3997 passed / 20 skipped in 557.61s**. All four
were `test_distribution_gate.py` self-hosted-lane tests whose `assay` stubs
write their verdict to `"$5"` — the positional slot the path occupied until
A-429's two flags moved it to `$7`. The stubs wrote nothing; the assertions
failed on a missing `verdict.json`. **This is the gate doing its job on a
change to the gate script itself.**

### 22. `bfb55e3f test(assay): the gate's assay stubs read --verdict-json from argv, not $5 (A-429 follow-up)`

- Fixed at the cause, not by renumbering: a shared `_VERDICT_PATH_FROM_ARGV`
  preamble scans the argv for `--verdict-json` and takes the token after it —
  the real CLI's own contract — in all three stubs. The fixture docstring's
  "the artifact path the stub writes to is `$5`" note had already had to move
  once (when `--require-judge-provenance` landed) and would have moved again;
  now it cannot.
- The argv-log assertion additionally pins `--resume`/`--progress`/the path on
  the invocation the stub RECEIVED — a stronger statement than the
  source-reading test beside it.
- `pytest tests/test_distribution_gate.py`: **22 passed**.

**GATE 3 — GREEN on `bfb55e3f`.** Log `scratchpad/gate-gen6c.log`:
`COMPLETE_MARKERS=1`, `GATE_EXIT=0`, `BAD=0`, wheel
`assay-4.1.1.dev25+gbfb55e3f-py3-none-any.whl` (size 532337, sha256
`262e9c4b0ecaae5cc822d45c10cf982d781bad07a4e892554fbde58304ebe7a0`),
`tester-unified: PASS (exit 0)` at
`commit: bfb55e3f3b267050fff47d670f48e35a08a19d87`, twelve phases ending
`pyflakes-clean`, both v9 schema phases green (18 frozen hard-cut templates;
47 successor templates). **GATE-VERIFIED COMMIT: `bfb55e3f`.**

Host discipline through generation 6: never `pytest -n`; every host run
`nice -n 19 ionice -c 3` and targeted; one gate container at a time — gate 3's
launch waited on a foreign `run-gate-vbpub-assay-*` container that was already
running and was NOT touched; every container of ours capped to 3 CPUs within
seconds of launch. Load stayed 5.1–7.4.

### 23. `docs(assay): Wave D generation 6 checkpoint — A-425/A-426/A-429 gate-green on bfb55e3f, BRIEF-6`

- Records only: this LOG's generation-6 entries, the REPORT's generation-6
  sections, and `reports/assay-WAVE-D-v10-BRIEF-6.md` (new).
- A docs-only successor to the gate-verified tip, as every generation of this
  wave has done. **The gate-verified commit stays `bfb55e3f`.**
- **Checkpoint taken here, at the E-008 boundary the clause names** (green
  gate). The v10 cut was NOT started: no commit on the branch carries `!`, and
  nothing under `verdict.py`, `verify.py`, `src/assay/schemas/` or the
  drift-guard carve-assets has been modified. The branch remains releasable
  on v9.

## Generation 7

### 24. `docs(assay): the last three phase-2 design rows — B004, B007 (measured), F015/R4 (A-430..A-433)`

- **First act, per DA-R17: B007's target bound MEASURED**, in a gate-free
  window (`docker ps --format '{{.Image}}'` showed no `tester-unified:local`;
  load average `3.87 5.30 7.22`). Script:
  `scratchpad/measure_b007.py`, run `nice -n 19 ionice -c 3`. It drives the
  SHIPPED substrate — `isolation.prepare_snapshot` +
  `SnapshotRepository.materialize` / `materialize_replacement`, the two calls
  `canary.run_isolated_canary` makes at `canary.py:480` and `canary.py:552` —
  against this worktree's own repository at `ed287d73`, project prefix
  `assay`, replacement target `assay/src/assay/canary.py` (33,940 bytes),
  three iterations. Two setup facts worth recording: the spec needed
  `snapshot_selection = "repository-minus-unsafe-symlinks"` with the three
  absolute-target symlinks vbpub's `topos/tests/fixtures/inspect_files` tree
  carries declared as `unsafe_symlink_omissions` (`repository` mode FORBIDS
  omissions and `_build_manifest` refuses the absolute symlink), and the
  measurement is of a REAL 96 MB / 3,757-file monorepo commit, not a fixture.
  **Numbers (all three iterations):** `prepare_snapshot` **4.071 s**, once per
  lane; `materialize` **0.894 / 1.000 / 1.063 s** to enter and **1.26 / 1.182
  / 1.26 s** enter-to-exit; `materialize_replacement` **1.314 / 1.358 /
  1.271 s** to enter and **1.502 / 1.511 / 1.486 s** enter-to-exit; snapshot
  **96,023,420 bytes / 3,757 files**, replaced snapshot **96,037,902 bytes**.
  **One canary TARGET therefore costs ~2.76 s of materialisation** (control
  enter-to-exit + transform enter-to-exit) plus two full runs of the lane's
  command, and **peak disk is ONE snapshot (~96 MB), not two** — read at
  `canary.py:479-544` and `:551-560`: the control and transform contexts are
  SEQUENTIAL, not nested.
- **A-430 (B004, DA-D7 as narrowed by DA-R12)** — `PROVENANCE_UNVERIFIED` in
  the `NO_MEASUREMENT` set (four places: `errors.py`'s enum and
  `REASON_CODES[NO_MEASUREMENT]` at `:203-215`; the schema's flat
  `$defs/reason_code` at `:593` and `$defs/reason_codes/NO_MEASUREMENT` at
  `:650`; plus `verify.py`'s independent pairing statement, A-182); the §5.4
  narrowing in BOTH layers (`Evidence.__post_init__` at `verdict.py:2773-2781`
  and the schema `else` at `verdict.schema.json:2463`, which today forbids the
  attestation payload and leaves `verified_by_assay` unconstrained for
  `adjudicated`); ONE parser accepting `schema_version ∈ {1, 2}` with named
  refusals for `3`, `"2"` and absence; **the green path's only witness stays
  `ciu-provenance-green-reference.json`**, said in the row.
- **A-431** — the three ledger corrections the carve's W0 owes: A-O12's
  `declared_unverified` claim is false (re-measured: the string is in neither
  `src/`, `docs/` nor the schema); §B004's "no schema change" is true only of
  A-255's `env_effective` route; A-O12's disposition is unchanged in substance.
  Recorded as a LATER row, never an edit — `decisions.md` is append-only.
- **A-432 (B007, DA-D8 + DA-R17)** — the measurement above, then
  `MAX_CANARY_TARGETS = 8` derived from it (8 × 2.76 s = **22.1 s**, 7.4 % of
  the smallest budget any worked example declares, `5m` at
  `docs/DESIGN-GUIDE.md:2127`); `targets`/`aggregation` on the lane with
  exactly-one-of against the surviving singular `target`
  (`LANE_SCHEMA_VERSION` stays **2**, existing R3 lanes load byte-unchanged);
  `judgment.r3` becoming `{mechanism, targets, aggregation?}` on the wire with
  the singular spelling normalising to a one-element array; `$defs/canary`
  becoming `{mechanism, attempts[]}` with a required `disposition` and a
  CLOSED three-member `not_attempted_reason` vocabulary; `any` short-circuits
  on the first PASS and `all` does not short-circuit on FAIL, both with their
  reasons; refusals/`INCONCLUSIVE`/budget exhaustion terminal, never
  aggregated; `verify.py` recomputing the aggregation AND the bookkeeping
  hand-transcribed; the `verdict.py` cross-check generalised to a pairwise
  in-order equality; B005's `config.py:1240-1257` rule generalised over the
  list. **The B064 sentence the controller required is in the row.**
- **A-433 (F015, DA-D9 + DA-R16)** — `R4` as the next rung: `RIGOR_LEVELS`
  gains it, declaration stays non-contiguous (`config.py:1089` requires an
  R0-led ordered SUBSEQUENCE, not a ladder), `judgment.r4` =
  `{tests, broken_commit, broken_commit_source}`, the claim carries BOTH
  outcomes, `verify.py` re-derives `PASS` iff `before != PASS and after ==
  PASS`, a W6 template pins the shape before any producer exists, and the
  not-proven case takes **`RED_FIRST_UNPROVEN`**, reserved in the v10
  `NO_MEASUREMENT` set in this cut and rendered in phase 3 (the
  `MISSING_EXTERNAL_TOOL` reservation pattern, A-013/A-086/A-144). **A
  decision ask is filed against that code's SET membership** — see the REPORT.
- Ids re-checked against `main` immediately before allocating, as the rules
  require: `git show main:…/decisions.md | grep -o '^| A-[0-9]*' | tail -1` →
  `A-407`; `git show main:…/4-backlog.md | grep -o '^## B[0-9]*' | tail -1` →
  `B061`; `git rev-parse --short main` → **`6917423d`** (main moved again from
  BRIEF-6's `af98e1f0`; assay's two ledgers are still untouched on it).
  Allocated **A-430..A-433**. Next free: **A-434**, **B065** (no new backlog
  entry was needed this step).
- **No wire change has been made yet.** This commit is `decisions.md` +
  records only; nothing under `verdict.py`, `verify.py`, `src/assay/schemas/`
  or the drift-guard carve-assets is touched, and no commit carries `!`. The
  branch is still releasable on v9. **All five phase-2 wire changes now exist
  as A-rows (A-427, A-428, A-430, A-432, A-433), which is the precondition the
  wave prompt puts on writing the cut.**
- Commit `26b38cc4`.

### 25. `docs(assay): Wave D generation 7 checkpoint — A-430..A-433, B007 measured, BRIEF-7`

- Records only: LOG entry 24 and this one, the REPORT's generation-7 section,
  and `reports/assay-WAVE-D-v10-BRIEF-7.md` (new).
- **NO GATE RUN THIS GENERATION, deliberately.** Generation 7 landed no code:
  `git diff --stat bfb55e3f HEAD` before this commit was four files, all under
  `assay/nyxloom-trove/` (`decisions.md`, BRIEF-6, LOG, REPORT), 739
  insertions and zero deletions. A registered-gate run on a records-only
  commit proves nothing and costs the shared 8-core host ~25 minutes, which
  the HOST LOAD rule exists to avoid; generation 6 set the same precedent for
  its own docs-only checkpoint commit. **The gate-verified commit stays
  `bfb55e3f`.** Generation 8's first gate judges the cut.
- Host discipline through generation 7: no gate container launched, no pytest
  run at all, one measurement script under `nice -n 19 ionice -c 3` in a
  window where `docker ps` showed no `tester-unified:local` and load was 3.87.
- **Checkpoint taken here, at a coherent boundary the clause names** (commit +
  LOG/REPORT write, on a branch with no red anything). The v10 cut was NOT
  started — deliberately: §3.2 of BRIEF-7 measures its blast radius (4 test
  modules construct `CanaryResult`, 14 pass `canary=`, 56 name the schema
  version, over a ~4000-test suite), and the clause forbids cutting a
  checkpoint mid-schema. Starting it with the calls remaining would have
  guaranteed exactly that.

## Generation 8 (fresh Opus, seeded by BRIEF-7)

### 26. `docs(assay): A-434 — DA-R18 amends A-433, RED_FIRST_UNPROVEN is a judged FAIL`

- `decisions.md` gains **A-434** only, as a later append-only row (A-408):
  DA-R18's corrected set membership for `RED_FIRST_UNPROVEN`, carried into the
  cut that follows. Ids re-checked against `main` at `48c48599` immediately
  before allocating: `main`'s last decision row is `A-407` and its last
  backlog id `B061` (assay's two ledgers still untouched on `main`), so the
  branch's own `A-433`/`B064` are the real high-water marks and **A-434** was
  free.
- The row settles what DA-R18 left to the implementer: the HEAD-side judged
  FAIL does **not** reuse `FAIL`/`COMMAND_FAILED`, on the R3 precedent
  (`canary.py:734-756` maps neither of its own two runs onto R0's code), on
  the which-side ambiguity a bare `COMMAND_FAILED` would leave on a claim
  whose before-run is *expected* to fail, and on `errors.py`'s own stated rule
  that which mechanism refused is the distinction the project keeps. Both
  judged halves therefore carry `FAIL`/`RED_FIRST_UNPROVEN`, discriminated by
  the two recorded outcomes and by `detail` (A-428).
- No code. No gate (records-only; the gate-verified commit stays `bfb55e3f`
  until the cut is judged).

### 27. `feat(assay)!: verdict schema v9 -> v10 — the integrity cut (B050/B053/B004/B007/F015)`

- **`b2fd09f3`. THE CUT. The only `!` commit on this branch, and the gate is
  GREEN on it.** 97 files, +8840/-532.
- Contents, against BRIEF-7 §3.1 (a)-(g): the JSON Schema (`$id`
  `urn:assay:schema:verdict:10`), `verdict.py` (the dataclasses and
  `VERDICT_SCHEMA_VERSION = 10`), `verify.py`'s independent re-derivation,
  `errors.py`'s two new reason codes, `config.py`'s `RIGOR_LEVELS` gaining
  `R4`, the producer-side re-wiring in `canary.py`/`runner.py`, the
  `carve-assets/W6/` drift guard, `tools/tester-unified-gate.sh`'s new phase,
  `docs/DESIGN-GUIDE.md` §6, and `CHANGES.md`'s `[Unreleased]` breaking entry.
  The test-suite blast radius rides the same commit because the suite must be
  green at the cut: 48 verdict fixtures plus ~24 modules.
- **Six wire changes**, each an A-row before a line of it was written:
  `judgment.r2.fail_under` (A-427), `claim.detail`/`detail_dropped_bytes`
  (A-428), `PROVENANCE_UNVERIFIED` + the `adjudicated ⇒ verified_by_assay:
  false` narrowing (A-430), the ordered bounded canary `targets` list with a
  closed `aggregation` and a per-attempt payload (A-432), and `R4`/`red_first`
  /`RED_FIRST_UNPROVEN` (A-433 as amended by A-434).
- **W5 is KEPT byte-for-byte.** `git diff` over `carve-assets/W5/` is empty
  across the whole commit; the hard-cut sweep in W6's suite is what proves its
  seven v9 documents are now REFUSED rather than migrated, and the gate's
  `verdict-v6-v7-v8-v9-hard-cut-verified` phase reports "hard-cut guard passed
  for 25 frozen templates".
- **Three gate-only consumers were found by the gate, one per run**, and this
  is the generation's transferable lesson: `pytest tests/` is green on this
  branch with **20 tests skipped**, and those 20 are exactly the harnesses that
  read a real produced artifact inside the tester-unified image.
  - Run **a** (`gate-gen8a.log`, `GATE_EXIT=1`): `gate/python/qualify_topos.py`
    still pointed `_EXPECTED_ROOT` at `carve-assets/W5/expected` and hardcoded
    `schema_version != 9` twice. This is the P25 Topos harness's FIFTH
    generation advance (P33 → W1 → W2 → W4 → W5 → W6) and its own header
    records the rule.
  - Run **b** (`gate-gen8b.log`, `GATE_EXIT=1`): `topos-qualified` now passes,
    and `gate/python/qualify_cmru_b006a.py` fails on the next phase reading a
    flat `canary.control_outcome` that is `canary.attempts[0].control_outcome`
    now.
  - Run **c** (`gate-gen8c.log`): **GREEN.**
- **Gate verdict on `b2fd09f3`** — `/tmp/…/scratchpad/gate-gen8c.log`:
  `GATE_EXIT=0`; exactly ONE `ASSAY_REGISTERED_GATE_COMPLETE=1`; ZERO
  `FAILED|DIRTY_TREE|Traceback`; wheel
  `assay-4.1.1.dev30+gb2fd09f3-py3-none-any.whl` (the judged commit is in the
  wheel name); all TWELVE phases in order —
  `wheel-installed`, `attestation-hardened`, `verdict-v5-accepted`,
  `lane-schema-v2-successors-verified`,
  `verdict-v6-v7-v8-v9-hard-cut-verified`,
  **`verdict-v10-successors-verified`** (79 passed in 1.10s),
  `judge-provenance-bound-to-the-installed-wheel`, `self-hosted-lane-passed`,
  `topos-qualified`, `cmru-b006a-qualified`,
  `independent-self-hosting-passed`, `pyflakes-clean`.
- Host discipline: three gate runs, each launched only after `docker ps
  --format '{{.Image}}'` showed no `tester-unified:local` and `pgrep -af
  tester-unified-gate.sh` was clear, each capped with `docker update
  --cpus=3` within seconds of launch (`6117465f707a`, `0ad19f33176e`), never
  two at once. Every local pytest was serial under `nice -n 19 ionice -c 3`
  and targeted at the module under repair; the whole suite ran once, before
  run a. Load average stayed in the 5-7 band it was already in.

### 28. `docs(assay): Wave D generation 8 checkpoint — the v10 cut is green, BRIEF-8`

- Records only, plus the DA-R21 note. LOG entries 27/28, the REPORT's
  generation-8 section, `reports/assay-WAVE-D-v10-BRIEF-8.md` (new), and the
  two one-line PLANNED notes DA-R21 asks for on `3-roadmap.md`'s M7 and
  `2-product-definition.md`'s F015 — F015's wire shape is in v10, its
  IMPLEMENTATION is the post-v10 plan's E-4, and neither file may read as done
  or proven.
- **No gate this commit, deliberately** (records + two doc notes; the
  gate-verified commit stays **`b2fd09f3`**), on the precedent generations 6
  and 7 set for their own docs-only checkpoints.
- **Checkpoint taken at the boundary the E-008 clause ranks HIGHEST: a green
  registered gate.** The seven post-cut items (B050 → B051 → B052 → B053
  `detail` → B004 → B007 → CONSUMERS migration notes) were NOT started, so no
  half-done item is left behind; BRIEF-8 hands them to generation 9 in order
  with their measured seams.
- Ids: re-checked against `main` before allocating, as the rules and both
  controller messages require. `git show main:…/4-backlog.md | grep -o '^## B[0-9]*'
  | tail -1` → **B068**, so the next free branch id is **B069**. **No B-id was
  allocated this generation** and no backlog entry was needed. `main`'s last
  decision row is still `A-407`; the branch's own high-water mark is
  **A-434**, allocated in entry 26, so the next free A-id is **A-435**.

## Generation 9 (fresh Opus, seeded by BRIEF-8; DA-R22..DA-R24 in the prompt)

### 29. `test(assay): a local tripwire for the gate harnesses' contract pins (B069, A-435)`

- **DA-R24's item 0, landed before B050 exactly as the ruling sequences it** —
  B007 will replace a `carve-assets/W6/expected/` template the same harnesses
  read, so the tripwire has to exist first.
- New `tests/test_gate_harness_version_pins.py` (4 tests), new backlog entry
  **B069** (filed RESOLVED, with the measurement and the rejected gate phase),
  new decision row **A-435**, and a `### Testing` bullet under CHANGES.md
  `[Unreleased]` — the section did not exist under `[Unreleased]` yet; every
  shipped release has one, so it was added rather than folded elsewhere.
- **The scanner is a pure function over text, which is what makes red-first
  expressible with no checkout.** `scan_pins(text, path=…)` /
  `stale_pins(text, path=…, schema_version=…, generation=…)` are called three
  ways: over the real `gate/python/*.py` (must be empty), over a fixture copy
  of the pre-cut harness lines (must report both families), and — as the
  REPORT's measurement, not a committed test — over the genuine
  `b2fd09f3^:gate/python/qualify_topos.py`, where it reports **all three** real
  stale pins that cost generation 8 two gate runs: `:92` `W5`, `:848` and
  `:905` `schema_version != 9`.
- **Both real-tree tests assert the FOUND set is non-empty before asserting the
  stale set is empty.** A text-scanning test's characteristic failure mode is
  the pattern rotting off its subject and passing vacuously; asserting the
  scanner still finds something is the control that makes the green mean
  anything.
- Two families are deliberately not scanned and both have their own regression
  test: the lane-file `schema_version = 2` inside a TOML template string
  (`qualify_cmru_b006a.py:116`, `qualify_dstdns_sql.py:923` — `LANE_SCHEMA_VERSION`
  is a separate contract that stays 2 across this cut) and prose references to
  frozen earlier generations (`qualify_topos.py:71`, `:811`). `P25` is a
  carve-asset directory but not a `W<n>` generation, and the test proves it is
  not read as generation 25.
- Local, serial, targeted: `nice -n 19 ionice -c 3 python -m pytest
  tests/test_gate_harness_version_pins.py -q` → **4 passed** in 0.41s. **No
  gate run this commit** — it adds one local test file and three record files,
  touches no `src/` and no wire shape, and the gate-verified commit stays
  `b2fd09f3`; the gate runs on the first commit that changes product code
  (B050).
- Ids re-checked against `main` immediately before allocating, as the rules
  require: `git show main:assay/nyxloom-trove/4-backlog.md | grep -o '^## B[0-9]*'
  | tail -1` → **B068** (so **B069** was free and is now taken);
  `main` is still at `A-407`, the branch at `A-434`, so **A-435** was free and
  is now taken. Next free: **A-436** / **B070**, re-check before use.

### 30. `feat(assay): judgment.r2.fail_under becomes a floor that is TAKEN (B050, A-436)`

- **DA-R22 applied literally, including its "no second formula" clause.**
  `mutation.judge_mutation` regains `fail_under: float = 100.0`; its
  `survived` branch is now `if mutation.survived and mutation_pct(mutation) <
  fail_under:`. `mutation_pct` (`mutation.py:2174`, shipped by B046) is the
  one and only score in the package, and `verify._check_r2_rederivation`
  calls the same two functions after reading `judgment.r2.fail_under` off the
  document.
- **Threading, one read:** `build_mutation_claim` gained the same
  keyword-only parameter and passes it straight through;
  `runner._run_prepared_lane`'s ingested branch supplies
  `lane.judge.mutation.fail_under`, which is the SAME attribute
  `runner._build_ingested_judgment_r2` writes onto the wire — so the document
  cannot record a floor other than the one that judged it.
- **`config._load_ingested_mutation`'s `if fail_under != 100.0:` refusal is
  DELETED** (its message named B050 as the field a lower floor needs; the
  field exists). The `0.0 <= fail_under <= 100.0` check above it stays, and
  `test_a_fail_under_outside_the_percentage_range_is_refused` still proves it.
- **A-223d's equivalence terminal now states the guard it used to inherit** —
  `not killed and not survived and equivalent`. See A-436 (4) and **decision
  ask 1**: DA-R22's "falls through to the existing terminals" is unambiguous
  except here, where the terminal's stated rule (`killed + survived == 0`,
  A-223d's own words) and its code had been allowed to differ because
  `survived` could not be non-empty on that branch. A met floor makes it
  non-empty. The restored guard is A-223d as written.
- **Acceptance witnesses — the document the v9 build could not produce:**
  `tests/test_verdict_conformance.py::test_verify_accepts_an_ingested_r2_PASS_with_recorded_survivors_at_a_met_floor`
  (synthetic, plus
  `test_verify_rejects_that_same_PASS_when_the_recorded_floor_is_not_met` as
  the control that isolates the floor as the only cause) and, end to end over
  the **committed real StrykerJS artifact**,
  `tests/test_runner_ingested_r2.py::test_a_declared_floor_the_real_report_MEETS_produces_a_verified_pass`:
  21 killed / 88 survived = **19.2660550458%**, declared floor 19.0, R2
  `PASS` with all 88 survivors recorded, `verify_document(...) == []`.
  `test_the_same_run_at_the_default_floor_is_the_unchanged_fail` shares that
  fixture and is the regression witness for "no shipped outcome changed".
- `tests/test_config_ingested_mutation.py`'s
  `test_a_sub_hundred_fail_under_is_refused_naming_the_wire_gap` was replaced
  by `test_a_sub_hundred_fail_under_LOADS_and_is_carried_to_the_judge` — the
  same lane text asserting the opposite outcome.
- **The gate was grepped BEFORE this commit, per BRIEF-8 §3.** `grep -rn
  fail_under gate/python/` returns exactly two hits, `qualify_topos.py:449`
  and `qualify_cmru_b006a.py:141`, and both are the **R1 coverage** floor in a
  `[lanes.*.judge]` table, not `judge.mutation.fail_under`. Neither gate lane
  is ingested (cmru's is native, with `[…judge.mutation] jobs = 1`), so no
  gate harness is on B050's blast radius.
- `src/assay/schemas/verdict.schema.json` was NOT touched: W6's
  `test_shipped_schema_is_byte_identical_to_the_locked_v10_asset` freezes it,
  the field and its producer fork already landed in the cut, and B050's
  producer needs no shape change. The verifier's failure message gained a
  suffix naming the floor it read; the three existing substring assertions in
  `test_verdict_conformance.py` are prefix matches and stay green.
- `docs/CONSUMERS.md:1281`'s "**`fail_under` must be `100.0` in this
  release**" paragraph is replaced by what the field now does. The four worked
  lanes keep `100.0` and stay legal.
- Local, serial, targeted: `nice -n 19 ionice -c 3 python -m pytest` over
  `test_verdict_conformance.py test_mutation_judge.py
  test_config_ingested_mutation.py test_verify_layer_independence.py
  test_verdict_mutation_artifacts.py test_verdict_interval_and_unsupported.py`
  → **306 passed**; then `test_runner_ingested_r2.py test_runner_run_lane_r2.py
  test_verdict_judgment.py test_runner_assemble_verdict_judgment.py
  test_refusal_announcement.py` → **161 passed**, and
  `test_runner_ingested_r2.py` alone → **23 passed** after the two new tests.
  The gate runs once at the checkpoint. (It ran over B069 + B050 only, not
  B050 + B051 — see entry 31: B051 is BLOCKED.)
- **GATE GREEN on this commit**, `gate-gen9a.log`, read in a separate step:
  `GATE_EXIT=0` (one), `ASSAY_REGISTERED_GATE_COMPLETE=1` (one), zero
  `FAILED|DIRTY_TREE|Traceback`, wheel
  **`assay-4.1.1.dev33+g962211cd-py3-none-any.whl`**, all twelve phases
  including `verdict-v10-successors-verified`.

### 31. `docs(assay): Wave D generation 9 checkpoint — B069 + B050 gate-green, B051 BLOCKED, BRIEF-9`

- Records only. LOG entries 29-31, the REPORT's generation-9 section, and
  `reports/assay-WAVE-D-v10-BRIEF-9.md` (new).
- **B051 is BLOCKED and nothing was improvised in its place.** DA-D4 rules
  `discarded` means "listed" — which `mutation.ingest_mutation_report` already
  implements (`mutation.py:1845`, `:1968`, shipped by B046) — and then asks
  `verify._check_ingested_r2_agrees_with_its_payload` to re-derive it. **It
  cannot.** A discarded mutant is absent from every bucket
  (`mutation.py:1967-1969` `continue`s past the assignment), absent from
  `candidate_count` (both it and `total` are set to `attempted`,
  `mutation.py:1992-1997`, and `Mutation._check_arithmetic`
  `verdict.py:1684-1703` FORBIDS `candidate_count != total` outside the limit
  sentinel), and its line is absent from `lines_without_candidates`
  (`mutated_lines.add` precedes the discard `continue`, correctly). So the
  count is not recoverable from the document, and every bound that catches the
  `9999` reproduction (`discarded <= total`, `<= candidate_count`) refuses a
  TRUTHFUL report that could not compile most of its mutants — which is the
  exact report the field exists to make visible. B051's own entry says this in
  its "Why this is not fixable in Wave B" section. Written up as **decision ask
  2** with three routes; none chosen.
- **B052 was deliberately NOT started in B051's place.** DA-R23 orders B051
  immediately after B050; reordering unasked is the silent product call the
  BLOCKED clause forbids. Flagged as decision ask 3.
- **Decision ask 1** records the one text-versus-code gap DA-R22's "falls
  through to the existing terminals" ran into: A-223d's guard, restored to its
  stated form in entry 30 and changing no shipped outcome.
- **No gate this commit, deliberately** (records only; the gate-verified commit
  stays **`962211cd`**), on the precedent generations 6, 7 and 8 set for their
  docs-only checkpoints.
- **Checkpoint taken at the boundary the E-008 clause ranks HIGHEST: a green
  registered gate.** No half-done item is left behind — B069 and B050 are
  complete, and B051 was never started because it is blocked.
- Ids: **A-435** and **A-436** allocated (next free **A-437**); **B069**
  allocated (next free **B070**). `main` re-checked immediately before each
  allocation: `git show main:assay/nyxloom-trove/4-backlog.md | grep -o '^## B[0-9]*'
  | tail -1` → **B068**; `main`'s decision high-water mark is still **A-407**.
  Re-check before allocating anything further; main wins on ids at merge.

### 32. `docs(assay): judgment.r2.discarded is DECLARED, NOT VERIFIED -- B051 resolved by ruling, B070 filed (A-437)`

- **DA-R26's route 1, landed exactly as ruled, in four places rather than the
  three DA-R26 named.** The fourth is `verify.py`'s own docstring — DA-R26
  asked for "`verify.py`'s own statement of what it does NOT check", and
  `_check_ingested_r2_agrees_with_its_payload` had no such section at all; it
  now carries one, plus a five-line comment at the check site so a reader who
  arrives at `discarded = r2.get("discarded")` does not have to scroll up to
  learn why the check stops there.
- **The `9999` reproduction was re-run on this tip and is NOT refused, by
  ruling.** Over `carve-assets/W6/expected/ingested-r2-v10-template.json` with
  the acceptance suite's own `@STARTED@`/`@ENDED@` substitutions: baseline
  `discarded` is `0`; `candidate_count == total == 109`; 21 killed / 88
  survived; the clean document gives `verify_document(...) == []`; with
  `discarded = 9999` it gives `verify_document(...) == []` — **accepted**; the
  same at `10000` (the schema's own maximum) is likewise accepted; and the
  `-1` negative control still produces its two named failures (`a count of
  invalid mutants cannot be negative` plus the schema's `0..10,000` range).
  The reason is in A-437 and in the backlog row: a discarded mutant is outside
  the document by DA-D4's "listed" semantics, and every bound that catches
  9999 refuses a truthful high-discard report.
- **Schema + W6 copy, description bytes only.** The `description` of
  `$defs.judgment_r2.properties.discarded` gained the declared-not-verified
  statement; `nyxloom-trove/carve-assets/W6/verdict.schema.v10.json` was
  re-taken with `cp` and re-checked with `cmp` in the same commit, as
  `test_shipped_schema_is_byte_identical_to_the_locked_v10_asset` requires.
  **No `type`, `enum`, `required`, bound, fork or `$id` moved** — `git diff`
  on both files is a one-line change to a single `description` string — so
  this is NOT a wire change and did NOT take a second `!` commit. The branch
  still carries exactly one, `b2fd09f3`.
- **W6 MANIFEST**: DA-R26 said to update the MANIFEST line "if it records the
  copy's hash". It does not — it records the copy's *provenance* ("a byte copy
  … verified with `cmp`, not trusted from a paste"), which this commit made
  incomplete rather than wrong. That row now records the one post-cut
  amendment, what moved (description bytes), what did not, and why it needed
  no second `!`.
- **Three new tests, all over the REAL StrykerJS-artifact document** the
  `ingested_document` fixture in `tests/test_verify_ingested_r2.py` produces
  through an actual `runner.run_lane`:
  `test_an_inflated_discarded_count_is_ACCEPTED_deliberately` (the only
  assertion in that module that a mutated document is accepted — it asserts
  the inflation is beyond the whole payload first, so it cannot pass
  vacuously, and its docstring says a future change that starts refusing this
  owes B070's field first);
  `test_an_inflated_discarded_count_cannot_move_the_R2_status` (DA-R23's
  sentence as an assertion: `discarded` is not in the payload at all,
  `total == candidate_count ==` the bucket sum, and the raw verifier's
  `judge_mutation` re-derivation still agrees at 9999); and
  `test_the_schema_says_discarded_is_declared_not_verified` (the three-place
  discipline as a machine check, asserting `producer_tool`'s own wording is
  still there to be matched).
- **Gate grepped BEFORE the run, per BRIEF-8 §3**: `grep -rn discarded
  gate/python/` returns **zero** hits, so no gate harness is on this change's
  blast radius; the only `description` hits under `gate/python/` are a
  `qualify_dstdns_sql.py` dataclass field. The B069 tripwire
  (`tests/test_gate_harness_version_pins.py`) was run locally and is green —
  nothing here moves `VERDICT_SCHEMA_VERSION` or the newest `W<n>`.
- Local, serial, targeted: `nice -n 19 ionice -c 3 python -m pytest
  tests/test_verify_ingested_r2.py` → **26 passed** (23 before the three new
  tests), then `tests/test_gate_harness_version_pins.py
  nyxloom-trove/carve-assets/W6/test_acceptance_v10.py
  tests/test_verdict_conformance.py` → **258 passed**.
- **B051 → RESOLVED BY RULING**, with a Resolution section, a status line at
  the top of the entry, and every acceptance box dispositioned: two ticked as
  landed, one struck through as ruled-not-constructible with the three
  file:line seams, one struck through as the waived witness clause, and one
  added for DA-R23's shared sentence. **B070 filed** as the v11 candidate,
  carrying the "free before 5.0.0, a schema bump after" sentence DA-R26
  required, both candidate shapes (list the mutants / an ingested-only
  in-scope count) with the trade between them, and the note that neither
  closes the un-listed half.
- Ids re-checked against `main` immediately before allocating: `git show
  main:assay/nyxloom-trove/4-backlog.md | grep -o '^## B[0-9]*' | tail -1` →
  **B068**, so **B070** was free and is now taken; `main`'s decisions high-water
  mark is still **A-407**, the branch's was **A-436**, so **A-437** was free
  and is now taken. Next free: **A-438** / **B071**, re-check before use.

### 33. `feat(assay): non-repudiation tier three -- an ingested report's source must be the commit's own bytes (B052, A-438)`

- **DA-D5 built as written, at the seam it names.**
  `mutation._check_report_source_matches_commit` runs inside
  `ingest_mutation_report`, immediately after `_resolve_report_paths` and
  BEFORE the bucketing loop, and reads through
  `isolation.SnapshotRepository.read_regular_file`. `runner._ingest_r2_report`
  gained `prepared` and `deadline` and passes them as `repository` and
  `read_timeout`; both are REQUIRED keyword-only parameters with no default,
  so the strongest of the three tiers is not the one a caller can forget.
  There is exactly one caller in the tree (`grep -rn "ingest_mutation_report("`
  → `runner.py` only).
- **Tier order is a dependency, not a preference:** content depends on
  anchoring, because the committed blob can only be read once the report's own
  file key has been resolved to its repo-top-relative spelling. And it runs
  before the bucketing loop because everything that loop computes — byte
  spans, line numbers, `lines_without_candidates` — is derived from the very
  text the check is about.
- **The normalisation is a named constant with its reasoning attached**
  (`_CONTENT_TIER_NORMALISATION`, `_normalise_source_for_compare`): line
  endings folded to `\n`, one trailing newline ignored, everything else
  byte-exact, compared in BYTES with the report's `source` re-encoded UTF-8.
- **The repository, not the materialised checkout** — on the precedent
  `_read_prepared_source_text` already set. A file read off the working tree
  could have been rewritten by the lane's own command between then and now,
  which is one of the three causes this check exists to name.
- **One sub-decision DA-D5 did not spell out, recorded rather than assumed:** a
  measured path the commit does not track is the SAME refusal, not the
  `GIT_FAILED` `read_regular_file` raises. "The commit has no such content" is
  cause 3 in its most literal form, and surfacing git's wording would report a
  repository failure for a report defect. In A-438 (4) and in the REPORT.
- **Seven new tests, all over the REAL committed StrykerJS artifact**, in
  `tests/test_runner_ingested_r2.py` — the module went 23 → 30 passed:
  `test_a_byte_identical_report_still_passes_the_content_tier` (the
  non-vacuity control), `test_a_stale_report_source_is_refused_naming_the_file_and_the_causes`,
  `test_a_REWRITTEN_source_is_refused_and_that_is_the_ruling`,
  `test_CRLF_line_endings_are_not_a_content_mismatch`,
  `test_one_trailing_newline_either_way_is_not_a_content_mismatch`,
  `test_a_SECOND_trailing_newline_IS_a_content_mismatch` (the BOUND — the half
  that makes the normalisation a contract), and
  `test_a_measured_file_the_commit_does_not_track_is_the_same_refusal`. The
  refusal text is read through a `diagnostics=` stream (B053/A-409), so the
  assertions are about the sentence a consumer actually sees.
- **One test had to be re-authored to stay about B052.** Inserting the stale
  line at the TOP shifted every mutant below it and `_parse_mutant` refused
  first ("location.start names line 8 column 52, which is past the end of that
  line"). Appending instead leaves every recorded position valid against the
  report's own text, so the content tier is the only thing that can refuse it.
  The reason is in the test's docstring, not just here.
- **The other 23 tests in that module now run through the new tier and are
  unchanged**, which is the real non-vacuity proof: the honest lane, over the
  real artifact, survives the check.
- Gate grepped BEFORE the run: `grep -rn "ingest|mutation-report-json|stryker"
  gate/ tools/` returns one hit, an unrelated SQL corpus comment. No gate
  harness runs an ingested lane, so nothing under `gate/python/` is on this
  change's blast radius.
- Local, serial, targeted: `tests/test_runner_ingested_r2.py` → **30 passed**;
  `tests/test_verify_ingested_r2.py tests/test_verdict_mutation_artifacts.py
  tests/test_mutation_judge.py tests/test_config_ingested_mutation.py
  tests/test_runner_run_lane_r2.py` → **100 passed**.
- **No wire change**: no schema edit, no W6 edit, no dataclass field. Still
  exactly one `!` commit on the branch.
- Ids: **A-438** allocated (next free **A-439**); no new B-id. `main`
  re-checked: backlog high-water **B068**, decisions high-water **A-407**.

### 34. `docs(assay): Wave D generation 10 checkpoint -- B051 + B052 gate-green, BRIEF-10`

- Records only. LOG entries 32-34, the REPORT's generation-10 section, and
  `reports/assay-WAVE-D-v10-BRIEF-10.md` (new).
- **GATE GREEN on `83c31f18`** (B052), read in a SEPARATE step from
  `gate-gen10a.log`: `GATE_EXIT=0` (exactly one), one
  `ASSAY_REGISTERED_GATE_COMPLETE=1`, **zero** `FAILED|DIRTY_TREE|Traceback`,
  wheel **`assay-4.1.1.dev36+g83c31f18-py3-none-any.whl`** — the judged commit
  — and all twelve phases: `wheel-installed`, `attestation-hardened`,
  `verdict-v5-accepted`, `lane-schema-v2-successors-verified`,
  `verdict-v6-v7-v8-v9-hard-cut-verified`,
  **`verdict-v10-successors-verified`**,
  `judge-provenance-bound-to-the-installed-wheel`, `self-hosted-lane-passed`,
  `topos-qualified`, `cmru-b006a-qualified`,
  `independent-self-hosting-passed`, `pyflakes-clean`. The container was
  capped to 3 CPUs on the first poll iteration; no other gate container or
  `tester-unified-gate.sh` process was running when it launched.
- Full local suite, once, serial, at the checkpoint: **4091 passed, 20
  skipped**, 407 s (generation 9's run was 4081/20 — the delta is this
  generation's ten new tests).
- **No gate on THIS commit, deliberately** (records only; the gate-verified
  commit stays **`83c31f18`**), on the precedent generations 6-9 set for their
  docs-only checkpoints.
- **Checkpoint taken at the boundary the E-008 clause ranks HIGHEST: a green
  registered gate**, and on the controller's mid-turn instruction not to start
  another item. No half-done item is left behind: B051 and B052 are complete,
  and B053 was never started.
- **Four of the eight post-cut items are done; four remain** in DA-R27's
  order: B053 `detail` producers → B004 `PROVENANCE_UNVERIFIED` producer →
  B007 multi-target canary loop → CONSUMERS "Migration notes (v9 → v10)".
  **Nothing is blocked**, and generation 10 raises no decision asks.
- Ids: **A-437** and **A-438** allocated (next free **A-439**); **B070**
  allocated (next free **B071**). `main` re-checked immediately before each
  allocation: backlog high-water **B068**, decisions high-water **A-407**.
  Main wins on ids at merge.
