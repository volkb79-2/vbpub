# run-gate-P03 — RG-27 lane invocation history — chronological LOG

Package: `run-gate-P03`, worktree `.worktrees/run-gate-P03-lane-history`,
branch `feat/run-gate-P03-lane-history`, based on `main` @ `332af5a1`.
One entry per commit, each naming the commit it describes.

---

## Orientation (before the first commit)

Read in order: `vbpub/AGENTS.md` (full), `run-gate-project/README.md`,
`SPEC.md` §1-§5 + the full `R-xx` index, `CONSUMERS.md` headings + the
adoption steps, and `KNOWN_ISSUES_TODO_BACKLOG.md` `## RG-27` in full.
Highest existing requirement id was `R-35`, so the new surface takes `R-36`.

Two facts checked against real git BEFORE writing the storage code, because
both would have shipped as silent defects on an assumption:

1. `git check-ignore .run-gate` on a directory that does not exist yet exits
   **1 (not ignored)** even under a `.run-gate/` pattern — the trailing-slash
   pattern needs a directory to match. Asking about `.run-gate/history.json`
   exits 0 in every case (nonexistent, existing). Consequence: the ignore
   query must name the FILES, or recording is silenced on every correctly
   configured project's FIRST run and only starts working on the second.
2. `git check-ignore a b` exits **0 when ANY argument matches**. Reading that
   exit status as "both are ignored" is the false-certification shape
   AGENTS.md names — it would certify a store whose lock file still dirties
   the tree. The verdict is therefore read from the reported PATHS.

Both are pinned by tests (`test_directory_ignore_pattern_works_on_the_very_first_run`,
`test_partially_ignored_store_is_still_refused`).

---

## `1687b60d` — feat(run-gate): RG-27 lane invocation history + the `history` query verb (rev 30)

Implementation, tests and docs in one commit (AGENTS.md: user-facing docs are
part of the change, not a follow-up).

**Code** (`run-gate.py`, rev 29 → 30): `[history] keep` config table +
validator + R-09-style shadowing resolver; the git-state samplers
(`worktree_is_dirty`, `git_operation_in_progress`, `head_commit`), the
ignore guard (`history_written_paths`, `paths_are_git_ignored`), the record
lifecycle (`start_run_record`/`finish_run_record`), the store
(`load_history_store`, `_apply_record`, `_write_history_store`,
`_record_invocation`, `record_invocation`), the stats (`duration_stats`,
`lane_history_report`) and the query verb (`cmd_history` + printers).
`history` added to `_RESERVED_POINTER_VERBS`; `usage()` gained the verb, the
`--json` flag and a "lane history" contract block; `main()` gained the verb
dispatch and the recording hooks.

**Tests**: 54 new across five classes — `TestHistoryConfigPolicy`,
`TestHistoryRollingSeries` (trap 1), `TestHistoryEligibilityGuard` (trap 2),
`TestHistoryStoreSafety`, `TestHistoryEndToEnd`, `TestHistoryQueryVerb`.

**Docs**: SPEC `R-36` (a-i) + `R-01`/`R-06`/`R-08` amendments and a Rev 8
header line; README feature bullet + subcommand list; CONSUMERS "What each
lane costs" (pasteable adoption, both output shapes, the two-slot table, the
worktree-scoping answer) + adoption step 5; CHANGES `[Unreleased]` entry; the
repo-root `.gitignore`.

**Mutation probe before committing**: 13 controlled wrong implementations
applied one at a time to the real file, suite run, file restored
byte-identical. All 13 caught, zero survivors.

**Gate: RED, and correctly so.** `./run-gate.py selftest` →

```
359 passed  (334 at this commit), 2 skipped
diff-coverage FAIL: 194/254 changed executable lines covered (76.4% < 100.0% floor)
run-gate: lane 'selftest' exit 1
```

Diagnosis: this commit extracted `_run_selected_lane()` out of `main()` to
give the recording window "a single entry and a single exit". That dedented
~60 pre-existing lines, every one of which the floor then counts as CHANGED —
and none of them was ever covered IN-PROCESS (they are reached only through
subprocess `run_tool` invocations, which coverage does not measure). The
failure was almost entirely about lines this package never touched in
substance. Fixed in the next commit rather than papered over with
`# pragma: no cover`.

---

## `afcdb39f` — fix(run-gate): record inline in `main()`, and cover the wiring in-process

1. Reverted the `_run_selected_lane()` extraction. The recording now hangs
   off `main()`'s EXISTING `try/except` with **no re-indentation at all**:
   `record = None` before the try, the record opened where the invocation
   begins, one `flush_run_record()` call on each of the three exits (normal
   return, `except GateError` refusal, `except BaseException` abort).
   `flush_run_record` closes and persists in one step precisely so those
   three sites cannot drift, and no-ops on `None` so no caller branches. The
   success-path flush also moved OUTSIDE the shared-infra `finally` —
   telemetry must not extend a lock another gate is blocked on.
   `git diff main -U0` afterwards shows no moved-line hunks.
2. Added `TestHistoryInProcess` (9) and `TestHistoryDegradedInputs` (16):
   the same surfaces driven through `main()` in-process, which is what
   reaches the printer and the wiring where coverage can see them; plus every
   "could not determine" path (git absent, git exit 128 outside a repo, a
   repo with no commits, a relative `--git-dir`, a wrongly-shaped or missing
   store, empty stats), each proving the answer is INDETERMINATE rather than
   a convenient invention.

**Gate: GREEN.** Verbatim (`./run-gate.py selftest`, verdict read in a
separate step from `$?`, never a pipe tail):

```
359 passed, 2 skipped, 2 warnings in 46.14s
diff-coverage OK: 229/229 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: artifact: /workspaces/vbpub/.worktrees/run-gate-P03-lane-history/run-gate-project/coverage.json
run-gate: lane 'selftest' exit 0
GATE_EXIT=0
```

**Live dogfood** — the tool recorded its own two gate runs, and the store
survives the next run's clean-tree check (`git status --porcelain` empty):

```
$ ./run-gate.py history selftest
lane selftest
  latest:  pass exit 0  47.7s  afcdb39fe805  2026-08-31T03:18:54Z
  history: 2 of at most 10 commit(s), oldest first
    COMMIT        OUTCOME   DURATION  STARTED
    1687b60dd08a  fail         56.4s  2026-08-31T03:12:34Z
    afcdb39fe805  pass         47.7s  2026-08-31T03:18:54Z
    passes: n=1 median 47.7s (min 47.7s, max 47.7s)
    completed (passes + fails): n=2 median 52.0s (min 47.7s, max 56.4s)
```

That red run at `1687b60d` is the `R-36c` design call paying for itself on
its first day: it is a COMPLETED fail, its 56.4s is real measured cost, and
it is **longer** than the green run — the coverage gate ran to a verdict
rather than short-circuiting. A pass-only series would have lost it; a merged
series would have reported 52.0s as "what selftest costs" when the answer for
a passing run is 47.7s. Both series are published; run-gate picks neither.

**Mutation probe re-run** against this final shape, extended to 15 mutants
(two new ones for the abort and refusal record sites). All 15 caught, zero
survivors, source restored byte-identical (sha256
`4dd29b66c03a2b99c73d42e3718e5c51c8b9ce28458582307af6f1cd536343d8`).

---

## `docs(run-gate): RG-27 FIXED + run-gate-P03 LOG/REPORT` — `dbaccfe1`

(Hash filled in by the round-2 commit below; a commit cannot carry its own.)

Backlog `RG-27` marked FIXED in both the status table and the entry body,
with the actual design decisions (verb name, storage location and format, the
concurrency mechanism, and both open questions' resolutions), and this LOG +
the REPORT filed. No code change.

Gate re-run at this commit — verdict read in a separate step, verbatim:

```
359 passed, 2 skipped, 2 warnings in 46.09s
diff-coverage OK: 229/229 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: lane 'selftest' exit 0
GATE_EXIT=0
```

(The run BEFORE this commit refused correctly — `run-gate: refusing to judge
a dirty tree: … has 3 uncommitted change(s)`, exit 2 — which is itself the
`R-36e` window working: that refusal landed in `latest` with outcome `error`
and was kept out of history.)

### Nothing was BLOCKED

The two questions the entry warned might have no clean answer both did:

- **Storage location**: the judged tree, not the repo root — `R-21` already
  relocates the effective project dir into the judged worktree, so the
  scoping fell out of an existing invariant instead of needing a new one.
- **Concurrent-write safety**: answered by SCOPE first (two worktrees address
  two files and never meet) and arbitration only for the residual case,
  which keeps the lock small enough to be bounded — and a bounded lock is
  what lets telemetry stay best-effort.

---

## `fix(run-gate): RG-27 round-2 review — B1/B2/S1/S2` — the branch tip

Named by subject: this commit introduces its own LOG entry, so it is the one
that cannot carry its own hash. `git log --oneline -1` on
`feat/run-gate-P03-lane-history` resolves it.

Adversarial review returned **ACCEPT-conditional**. Independent
re-verification confirmed the substance — all three named mutants re-run, the
two `git check-ignore` behaviors re-checked against real git, concurrency
re-tested with 16 real separate processes racing one store (16/16 recorded,
zero lost), and coverage confirmed not gamed (`_run_selected_lane` genuinely
absent at HEAD, zero new `# pragma: no cover` in the diff). Two blockers and
two cheap recommendations came back.

**B1 — `history` silently ignored `--worktree`.** `cmd_history` was dispatched
before `resolve_repo_and_worktree` and got the raw `project_dir`, so the write
side honored the flag and the read side did not: tree A's medians reported
under tree B's name. Fixed by HONORING it (read the selected tree's store) and
DISCLOSING the tree in both output forms. Resolution stays opt-in so an
unflagged query keeps working where git cannot.

While writing the error-path test I found a **second hole in my own fix**:
`resolve_repo_and_worktree` takes the override verbatim by design (`R-02`),
and a READ has no downstream to fail in — so `history --worktree /nonexistent`
computed a store path under a tree that is not there and answered
`(not written yet)`. Silence presented as tree B's answer: B1 again, through
the error path. Non-directory now refuses (exit 2), non-work-tree refuses
(exit 3 with git's own line). This is worth recording as a pattern: the fix
for a silent-substitution bug has its own silent-substitution path, and only
the negative test finds it.

**B2 — Ctrl-C during the telemetry write became an uncaught `KeyError`.**
Reproduced. `flush_run_record` evaluated `finish_run_record(...)` as an
argument (outside `record_invocation`'s try) and the pop was unconditional;
the normal-path flush runs inside `main()`'s try and can take seconds (a
`git check-ignore` subprocess plus up to the 5s lock bound), so an interrupt
there hit the `BaseException` handler, which flushed the same consumed record
and raised. Fixed with both offered remedies — a `_flushed` sentinel staked
before the work, and a None-safe start stamp — plus the matching eligibility
clause (no duration = not a measurement).

**S1** `--json` refused by name outside `history`. **S2** the reserved-name
change flagged as a load-time BREAKING CHANGE in CHANGES + CONSUMERS. **N1**
comment added. **Write-up correction**: mutant B's real contrast is median
10.0 vs **mean 40.0**, not "max 100.0" — the test was right, my prose was not.
S3/S4/N2 logged and not chased, as directed.

**Mutation probe extended to 20** (P-T for B1 ×2, B2 ×2, S1). All 20 caught,
zero survivors, source restored byte-identical
(`a63c2717e69f273508a7d6e0c17fa107536963581f5f03a57fefe83a92c6ca00`).

**Gate: GREEN.** Verbatim, verdict read in a separate step:

```
376 passed, 2 skipped, 2 warnings in 43.86s
diff-coverage OK: 268/268 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: lane 'selftest' exit 0
GATE_EXIT=0
```
