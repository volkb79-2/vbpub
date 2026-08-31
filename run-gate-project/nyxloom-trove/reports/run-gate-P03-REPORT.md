# run-gate-P03 — RG-27 lane invocation history — REPORT

**Package:** `run-gate-P03` · branch `feat/run-gate-P03-lane-history` (from
`main` @ `332af5a1`) · commits `1687b60d`, `afcdb39f`, + this docs commit.
**Backlog:** `RG-27` → FIXED. Retriage of ciu `CIU-55` (kept there as a
superseded pointer; nothing in ciu was touched by this package).
**Tool revision:** `__revision__` 29 → **30**. **SPEC:** new `R-36` (a-i),
amendments to `R-01`, `R-06`, `R-08`, header Rev 8.

---

## 1. What was built

`run-gate` now records what it already sees first-hand — it is the layer that
actually starts each lane — and publishes it through a query verb. Two slots
per lane, with deliberately different contracts:

| slot | holds | updates |
|---|---|---|
| `latest` | the most recent invocation, **whatever happened to it** — pass, fail, tool error, Ctrl-C, dirty tree, mid-rebase | every invocation, always |
| `history` | a curated trend series keyed by **(lane, commit)**, bounded to the last `keep` commits | only when the measurement is trustworthy |

Surface added: `./run-gate.py history [LANE] [--json]`; a `[history] keep`
config table; a `<project>/.run-gate/history.json` store.
**Explicitly not built:** any rigor/defer policy. run-gate measures and
persists; a controller reads and decides. That boundary is stated in `R-36`'s
first paragraph, in the module comment, in `cmd_history`'s docstring, and in
CONSUMERS.

## 2. Every design decision, and why

### 2.1 Verb name: `history` (positional), flag `--json`

The project's verbs are lowercase positional words (`doctor`,
`validate-pointers`); its discovery surfaces are flags (`--list`,
`--check-env`, `--dry-run`). This reads the world and reports — the `doctor`
shape — so it is a positional verb, not a flag. `history` is the backlog
entry's own vocabulary ("bounded history", "history entries"), and the
`latest` slot presents naturally as the head of the same report rather than
needing a second verb. `--json` mirrors the machine/human split the project
already uses (`--list` machine, `--help` human) and the `assay lanes --json`
convention run-gate already consumes.

Rejected: `stats` (too generic, and the verb's most-used answer is the
single `latest` record, which is not a statistic); `timings` (drops the
outcome half, which is half the record); a `--history` flag (it takes an
optional lane argument and produces a multi-line report — that is a verb).

Consequence handled: `history` joins `doctor`/`validate-pointers` as a
RESERVED lane name (refused at load, `R-08`) and as an exempted positional
in the `validate-pointers` pointer collector. No estate project declares a
lane by that name.

### 2.2 Storage location: `<effective project dir>/.run-gate/history.json`

Requirements were: run-gate-owned, gitignored, per-instance, and safe when
two gates run at once.

**Per-instance means per (judged worktree × project), and it is DERIVED, not
invented.** `R-21` already relocates the *effective project dir* into the
judged tree, so anchoring the store there gets both scopings free:

- **per worktree** — `--worktree B` writes B's measurement into B's store.
  Anchoring at `repo` (the checkout owning the shared `.git`, i.e. MAIN for
  any linked worktree) would have every parallel worktree gate writing one
  file, which is both the contention hazard and a false attribution: `R-21`
  exists because judging tree A while pointed at tree B is a silent
  false-PASS, and writing A's measurement under B's identity is the same
  mistake in the telemetry.
- **per project** — lane names collide across the estate (`selftest` exists
  in several projects); a repo-level store would merge them.

It also matches the neighbourhood: `.assay/verdict-*.json` and declared
`artifacts` already resolve against the effective project dir.

**Format: JSON, not TOML.** Decisive and concrete: run-gate is stdlib-only by
contract (`README` "Distribution"), and the stdlib has no TOML *writer* —
`tomllib` is read-only. Hand-rolling TOML emission for a file the tool
rewrites after every run is a bug farm. `json` was already imported (RG-25's
inventory probe), so the import surface did not grow;
`test_no_stdlib_violations` is untouched.

**Rejected: `/tmp/run-gate/` (the `RUN_GATE_EVIDENCE_DIR` neighbourhood).**
Evidence is a post-mortem artifact for one failure; history is a trend series
that must accumulate across ~10 commits, i.e. days. `/tmp` is tmpfs on many
hosts and swept on reboot, so the series would silently reset — the failure
mode being "the data quietly is not there", which is the worst kind. It also
could not be per-worktree without inventing a key.

**Rejected: an env-var relocation override.** More surface, more docs, more
tests, and the un-ignored case is already handled loudly (§2.4). If a
copied-script repo genuinely cannot ignore a path, that is a backlog entry
with a real use case attached, not a speculative knob.

### 2.3 Concurrent-write safety — scope first, arbitration second

This is the question RG-27 said needed "an explicit design answer, not an
assumption", so it is answered in layers:

1. **Cross-worktree contention is eliminated by construction**, not
   arbitrated: two worktrees' gates address two different files. A lock that
   arbitrates a collision is strictly worse than a layout that has none —
   the lock has to be correct forever, the layout is correct once.
2. **The residual case is real**: two lanes of ONE project, in ONE tree, in
   parallel (a conjunction gate, or an operator running two lanes at once).
   They do a read-modify-write of one JSON file, which without mutual
   exclusion is last-writer-wins — N concurrent recorders leave 1 entry, not
   N (`test_concurrent_recorders_lose_no_entries`, mutant I).
3. **Mechanism**: an exclusive `flock` on a **sibling** lock file
   (`.run-gate/history.lock`, `O_NOFOLLOW`, 0600 — matching
   `acquire_shared_locks`' precedent) held across the whole read-modify-write,
   plus write-temp-then-`os.replace`.
   - **Sibling, not the store itself**, and this is load-bearing: the store
     is REPLACED by rename, so its inode changes on every write. A lock taken
     on the store would guard an inode nobody writes next — the classic
     lock-the-renamed-file bug. Pinned by
     `test_the_lock_is_a_sibling_file_not_the_store_itself` (which also
     catches the non-atomic-write mutant J).
   - **Atomic rename is also what lets readers take no lock at all**: a
     reader sees the whole old file or the whole new one, never a
     half-written middle. `test_readers_take_no_lock` runs the query verb
     while a writer holds the lock.
4. **The wait is BOUNDED (5s), unlike `R-29`'s shared-infra lock which blocks
   forever on purpose.** That lock protects the CORRECTNESS of the run; this
   one protects a measurement. A gate that hangs waiting to write telemetry
   has inverted the priority — so a held lock degrades to a warning and the
   lane's verdict stands.

### 2.4 The store must be git-ignored — and that is CHECKED, not documented

Writing into the judged tree has one sharp edge: an un-ignored store leaves
the tree dirty, and the NEXT lane's `clean_tree` check then refuses on
yesterday's telemetry. CONSUMERS already carried a "gitignore the artifacts"
adoption step (`R-32`) — i.e. exactly the check that cannot fail, which
AGENTS.md names as this estate's most expensive recurring defect.

So run-gate asks git before every write and **refuses to write** an
un-ignored store, printing the remedy. Telemetry cannot be the cause of a
mysterious dirty-tree refusal. Two details in that check were verified
against real git rather than assumed, and both would have been silent
defects:

- **It asks about the FILES, never the bare directory.** `git check-ignore
  .run-gate` on a directory that does not exist yet exits 1 ("not ignored")
  even under a `.run-gate/` pattern — the trailing-slash pattern needs a
  directory to match. Asking about the bare directory would have silenced
  recording on every correctly-configured project's FIRST run and started
  working on the second: a bug that looks like flakiness. Mutant H; pinned by
  `test_directory_ignore_pattern_works_on_the_very_first_run`.
- **The verdict is read from the reported PATHS, never the exit status.**
  `git check-ignore a b` exits 0 when ANY argument matches. Reading that as
  "both are ignored" is precisely AGENTS.md's false-certification shape — the
  message would say "safe to write" while the comparison established only
  "at least one of these is safe", certifying a store whose LOCK file still
  dirties the tree. Mutant G; pinned by
  `test_partially_ignored_store_is_still_refused`.
- Run **without** `--no-index` on purpose: the question is not "do the ignore
  rules match" but "would writing here dirty the tree", and a TRACKED path
  dirties it whatever `.gitignore` says (`test_tracked_store_counts_as_not_ignored`).
- git failing (OSError) or answering 128 returns `None` = indeterminate, and
  indeterminate REFUSES. Fail-closed toward not dirtying the tree.

Root `.gitignore` gained `.run-gate/`; CONSUMERS adoption step 5 and the new
section carry the copied-script-repo obligation.

### 2.5 Open question A — does a COMPLETED FAIL belong in history?

**Resolved: YES**, agreeing with the entry's own reading — but with a
qualification that is doing the real work, and I would not ship the plain
"yes" without it.

*Why yes*: a completed fail ran the same container start, the same
collection, the same suite up to the failure. Its duration is real measured
cost of exercising the lane, and dropping it would systematically thin the
series of exactly the lanes under active development — the ones a
defer-policy most needs data about.

*Why the plain yes is not safe*: a failing lane can SHORT-CIRCUIT. This
project's own selftest lane is `pytest … && coverage_gate`; a red pytest
never reaches the coverage gate. So fails and passes are not samples of the
same quantity, and averaging them understates the lane's true cost in exactly
the direction that makes a "cheap, always run it" decision wrong.

*Resolution*: fails join history **carrying `"outcome": "fail"`**, and the
reported statistic is **split** — `stats.passes` and `stats.completed`, both
published, in both output forms. run-gate hands over both series and picks
neither; picking is policy, which is out of scope. `R-36c`;
`test_completed_fail_joins_history_carrying_its_outcome` asserts the split
numerically (a `passes` median of 30.0 next to a `completed` median of 16.5).

This paid off on day one: at `1687b60d` the gate failed with a coverage
verdict and took **56.4s**, LONGER than the green run's 47.7s. A pass-only
series loses that point; a merged series reports 52.0s as "what selftest
costs" when the answer for a passing run is 47.7s.

### 2.6 Open question B — what else must be excluded, and how is "unknown" handled

Eligibility is a **conjunction**, evaluated once, with the failing reason
stored on the entry (`excluded_reason`, printed by both output forms —
diagnostics, not a silent drop):

1. the lane completed and reported its own status;
2. the tree was clean **at the moment the run started**;
3. no git operation in flight (`rebase-merge`, `rebase-apply`, `MERGE_HEAD`,
   `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `BISECT_LOG`);
4. HEAD resolved to a full commit sha.

Three sub-decisions inside that:

- **"Could not determine" EXCLUDES** for (2)-(4). A wrong trend entry is
  invisible; a missing one shows up in `count`. Folding "I could not tell"
  into "clean" would be the absence-for-emptiness anti-pattern in its
  dangerous direction (mutant E).
- **Dirtiness is sampled independently of the lane's `clean_tree` POLICY.**
  The discriminator is whether the tree WAS dirty, never whether dirt was
  permitted — keying on the flag would silently halve the series of every
  `clean_tree = false` lane (nyxloom's, for one) even on perfectly clean
  runs. `test_dirt_tolerance_is_not_the_discriminator`.
- **Sampled BEFORE the lane, not after.** A lane may commit, stash, or leave
  artifacts; sampling afterwards would let a lane retro-disqualify its own
  valid measurement. `test_tree_state_is_sampled_before_the_lane_not_after`.

Also decided: `history` is keyed by (lane, commit), so a **re-run of the same
commit REPLACES its entry** and moves it to the tail (eviction then means
"least recently measured"). Appending instead would let ten re-runs of one
commit evict nine other commits from a ten-deep window, quietly destroying
the "last N commits" property (mutant K).

And: the reported statistic is the **MEDIAN**, with min/max/count, never the
mean. The named trap is one slow outlier reading as the lane's permanent
cost, and the mean is precisely the statistic that permits it; `max` still
publishes the outlier, because an outlier is information — just not the
typical cost (mutant B).

### 2.7 Recording window, and best-effort discipline

An invocation begins at the clean-tree refusal and ends at the lane's own
exit status. Earlier failures (unknown lane, bad key) are configuration
errors that name no invocation, and record nothing. `--dry-run` records
nothing — no lane started, so nothing was measured. A refusal or an abort
inside the window updates `latest` and re-raises/returns unchanged: "why did
my gate not run?" is exactly what a diagnostics slot is for.

Recording is best-effort throughout: unignored store, held lock past the
bound, corrupt store, write error — each degrades to ONE warning line on
stderr (never a traceback, `R-04`) and never changes the lane's exit status.
`test_write_failure_is_one_warning_never_a_traceback` asserts the line count.

`flush_run_record()` closes and persists in one step so `main()`'s three
exits (return, `except GateError`, `except BaseException`) cannot drift in
how they record, and no-ops on `None` so no caller branches on dry-run.

## 3. The gate — verbatim

Command: `cd run-gate-project && ./run-gate.py selftest`, exit status read in
a **separate step** from a marker in the log file, never a pipe tail
(AGENTS.md "Read the exit status from the job, never from the wrapper";
LESSONS L4). Final run, at `afcdb39f`:

```
run-gate: rev 30 | lane selftest | env built-in 'host'
run-gate: budget 20m (advisory)
...
359 passed, 2 skipped, 2 warnings in 46.14s
diff-coverage OK: 229/229 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: artifact: /workspaces/vbpub/.worktrees/run-gate-P03-lane-history/run-gate-project/coverage.json
run-gate: lane 'selftest' exit 0
GATE_EXIT=0
```

The 2 skips are pre-existing and unrelated (`TestWheelPackaging`, local
`setuptools_scm` 10.2.1 ≠ pinned 10.0.5). RG-29's `cmru/run-gate.toml` assay
pin was already fixed on `main`; `TestPointerLinkageEstate` is green.

The gate was RED at `1687b60d` (194/254 = 76.4% diff coverage) for a reason
that had nothing to do with the feature: an `_run_selected_lane()` extraction
dedented ~60 pre-existing, never-in-process-covered lines into the diff. The
extraction was reverted rather than papered over with `# pragma: no cover`.
Full account in the LOG.

## 4. Evidence the two named traps are actually caught

Both traps have dedicated test classes, and both classes were verified by
**controlled wrong implementation** — 15 mutants applied one at a time to the
real `run-gate.py`, suite run, source restored and sha256-verified byte
identical. **All 15 caught, zero survivors.**

| mutant | first test to go red |
|---|---|
| **A** latest-only, no rolling series — *the entry's trap 1 verbatim* | `TestHistoryRollingSeries::test_series_survives_across_commits_not_just_the_last` |
| **B** mean instead of median (the outlier becomes the typical cost) | `…::test_one_slow_outlier_does_not_become_the_typical_cost` |
| **C** dirty tree no longer excluded — *the entry's trap 2 verbatim* | `TestHistoryEligibilityGuard::test_dirty_run_never_overwrites_the_commits_history_entry` |
| **D** aborted/errored runs join history | `…::test_aborted_run_updates_latest_only` |
| **E** "could not determine dirtiness" treated as clean | `…::test_undeterminable_cleanliness_excludes_rather_than_assumes` |
| **F** mid-rebase HEAD no longer excluded | `…::test_mid_rebase_run_updates_latest_only` |
| **G** ignore-check reads the exit status, not the reported paths | `TestHistoryStoreSafety::test_partially_ignored_store_is_still_refused` |
| **H** ignore-check asks about the bare directory | `…::test_the_lock_is_a_sibling_file_not_the_store_itself` |
| **I** no mutual exclusion on the read-modify-write | `…::test_concurrent_recorders_lose_no_entries` |
| **J** non-atomic write (truncate in place) | `…::test_the_lock_is_a_sibling_file_not_the_store_itself` |
| **K** re-run of one commit appends instead of replacing | `TestHistoryRollingSeries::test_rerun_of_one_commit_replaces_its_entry_not_the_window` |
| **L** history recorded on `--dry-run` too | `TestHistoryEndToEnd::test_dry_run_records_nothing` |
| **M** recording writes into the invoking checkout | `…::test_worktree_override_records_into_the_judged_tree` |
| **N** an abort is not recorded before it re-raises | `TestHistoryInProcess::test_main_records_an_abort_and_re_raises_untouched` |
| **O** a refusal is not recorded (`latest` goes stale on the interesting run) | `TestHistoryEndToEnd::test_a_clean_tree_refusal_lands_in_latest_only` |

**Trap 1 (latest-only / no rolling stat)** is caught twice over, at both
levels it can occur: structurally (mutant A — the store keeps one entry where
three commits ran) and statistically (mutant B — the series exists but a
single 100s outlier among 10s runs is reported as the typical cost; the test
asserts `median == 10.0` while `max == 100.0`).

**Trap 2 (an aborted/dirty run corrupting a commit-keyed entry)** is caught
in its literal form and in three siblings that would produce the same
corruption by other routes. The literal test records a clean 10.0s pass on
commit C, then a **dirty** 999.0s run on the SAME commit, and asserts that
C's history entry still reads 10.0s and that `latest` moved to 999.0s with
`history_eligible: false` and a reason naming the dirt — i.e. both halves of
the contract at once.

Live confirmation beyond the fixtures: the store now holds this package's own
two real gate runs (a completed fail at `1687b60d` and a pass at `afcdb39f`),
and `git status --porcelain` is empty afterwards — the ignore guard works on
the real repo, not just on fixtures.

## 5. Test inventory (79 new)

`TestHistoryConfigPolicy` (11) · `TestHistoryRollingSeries` (6, trap 1) ·
`TestHistoryEligibilityGuard` (8, trap 2) · `TestHistoryStoreSafety` (14) ·
`TestHistoryEndToEnd` (7) · `TestHistoryQueryVerb` (9) ·
`TestHistoryInProcess` (9) · `TestHistoryDegradedInputs` (15).
Suite total 334 → 359 passing.

## 6. Files touched

| file | why |
|---|---|
| `run-gate-project/run-gate.py` | the feature; rev 29 → 30 |
| `run-gate-project/tests/test_run_gate.py` | 79 new tests |
| `run-gate-project/SPEC.md` | `R-36` (a-i); `R-01`/`R-06`/`R-08` amendments; Rev 8 header |
| `run-gate-project/README.md` | feature bullet (WHAT + why-link) + subcommand list |
| `run-gate-project/CONSUMERS.md` | "What each lane costs" (HOW: pasteable adoption, both output shapes, the two-slot table); adoption step 5 |
| `run-gate-project/CHANGES.md` | `[Unreleased]` → Added |
| `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` | RG-27 FIXED note |
| `.gitignore` (repo root) | `.run-gate/` |
| `run-gate-project/nyxloom-trove/reports/run-gate-P03-{LOG,REPORT}.md` | this record |

Nothing outside this list was touched. `ciu/KNOWN_ISSUES_TODO_BACKLOG.md`
(CIU-55) was deliberately NOT edited — it is another project's file and the
entry says the pointer is kept there as-is.

## 7. Follow-ups a reviewer may want filed (not filed by me — out of scope)

1. **Retention is bounded by COUNT, never by age.** A lane run once a quarter
   keeps a year-old measurement in its median. Whether that wants a
   `max_age` companion is a policy question, and policy is out of scope here.
2. **No estate-wide roll-up.** Answering "which lanes are expensive across
   the estate" means reading N per-project stores; that is a consumer-side
   aggregation, and deliberately not run-gate's job.
3. **The `history` verb reports the CURRENT worktree's store only.** A
   controller comparing worktrees points the verb at each in turn. A
   `--worktree`-aware query would be a small addition if a real consumer
   asks; nothing does yet.
4. **v8 absorption.** When ciu §4.3.2 absorbs run-gate into `ciu gate`, the
   store's scoping question reopens (ciu holds per-instance identity
   directly). `R-36f` states the current answer's reasoning explicitly so
   that conversation starts from the argument, not from the code.
