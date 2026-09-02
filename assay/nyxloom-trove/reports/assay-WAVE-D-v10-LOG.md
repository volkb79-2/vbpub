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
