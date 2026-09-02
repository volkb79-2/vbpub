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

## Decision asks

*(none yet)*
