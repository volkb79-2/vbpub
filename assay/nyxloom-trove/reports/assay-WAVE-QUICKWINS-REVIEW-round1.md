# assay — B068 + quick-wins wave, adversarial review, round 1 (2026-09-08)

Reviewer: fresh session, no relationship to the implementer, no memory of the
wave. Branch under review: `fix/assay-b068-quickwins-2026-09-08`, tip
`7bcf089a`, 8 commits over `main` @ `950046d9`.

Reviewed from an **independent detached worktree**
(`/workspaces/vbpub/.worktrees/assay-b068-review`), never the implementer's.
The registered gate was re-run from scratch here; the implementer's gate run
was not reused as evidence. Every load-bearing claim in the LOG/REPORT was
re-derived from the code and from live reproductions before being accepted.

---

## Verdict: **ACCEPT-conditional**

Every one of the six code changes is correct, minimal, genuinely red-first,
and green on my own independent gate run. B068's headline claim — that the
backlog entry's own R0/R1-vs-R2 "discriminator" is false — is **true**, and I
re-derived it end to end rather than taking the implementer's word for it.

The one condition is **not** in the shipped code. It is in the *sweep record*
the wave's own acceptance criterion asked for: B072's acceptance box says
"a one-time sweep ... so this does not recur a third time — named here even
if none are found", and the sweep as recorded is **incomplete and states a
false conclusion**. I reproduced two further live instances of the identical
gap, in code paths that read documents assay demonstrably did not write. See
**BLOCKER-1**.

Nothing else rises above should-fix / nit.

---

## What I re-derived independently (method)

| claim | how I checked it | result |
|---|---|---|
| B068's premise (R0/R1 vs R2 divergence) | built a real `git worktree add` + moved the main `.git` away; ran `main`'s `assay.cli.main(["run", …])` for an `rigor=["R0"]` lane and an `rigor=["R0","R1","R2"]` lane in the same tree | **premise refuted, confirmed** — byte-identical `ERROR/GIT_FAILED` for both |
| B068's fix | same fixture, branch code | both lanes get the new named message; git's `fatal:` retained |
| B072 crash is real | `git show main:assay/src/assay/attestation.py` extracted to a scratch tree; `parse_attestation("["*100000+"]"*100000)` | `RecursionError` escaped on `main`; `AssayError`/`UNREADABLE_ARTIFACT` on the branch |
| B074 crash is real | same, `verify.verify_text` | `RecursionError` escaped on `main`; failure list on the branch |
| B062's 31 findings | `git archive main assay/tests` → `pyflakes tests` | exactly 25 unused imports + 5 dead locals + 1 redefinition (+ the `broken.py` syntax line) — the count is right |
| B062 on the branch tip | `pyflakes tests` in my worktree | one line only: `tests/fixtures/mutation/python/broken.py:8:12: invalid syntax` |
| B062's two "needs judgement" classes | `grep -rn importorskip` over `main`'s tests; read all 5 dead-local sites | both classes genuinely empty; the 4 `head_rev =` fixes keep the side-effecting call and drop only the binding — correct |
| B063 in place | ran the three modules in my worktree | 68 passed, 9 skipped, all 9 the pre-existing `/opt/tester-venv` skips — matches the LOG exactly |
| B063 outside the checkout | fresh `cp -r` of the branch's `assay/` to a non-repo scratch dir, full suite | **4161 passed, 77 skipped, 0 failed, 0 errors** — better than the LOG's own quoted numbers |
| the two `host`→`bare-host` reds are pre-existing | ran `main`'s `test_cgroup_parent.py` verbatim against the (unchanged) `run-gate.toml` | `AssertionError: assert 'bare-host' == 'host'` — **genuinely red on `main`**, not caused by this branch |
| every new test is red-first | reverted `git.py`, `attestation.py`, `verify.py`, `mutation.py` and `tools/tester-unified-gate.sh` to `main` in a scratch copy, re-ran the four new modules + the three new gate-lint tests | **29 of 36 fail**; the only 7 that pass are the explicitly-labelled controls ("unchanged behaviour" guards) — no hollow tests |
| schema invariants | grep | `VERDICT_SCHEMA_VERSION = 10`, `assay.toml schema_version = 2`, `LANE_INVENTORY_SCHEMA_VERSION = 1`; `verdict.py`, `assay.toml`, `schema.json` untouched by the diff |
| registered gate | `./run-gate.py --worktree <my worktree> tester-unified`, verdict read from log markers in a separate step | see **Gate** |

---

## BLOCKER-1 — B072's required sweep is incomplete, and the record states a falsehood

**Where:** `assay/nyxloom-trove/4-backlog.md` B074 "What was measured" table
(≈ line 7510) and its Resolution (≈ line 7620); mirrored in
`reports/assay-WAVE-QUICKWINS-LOG.md:88-97` and
`reports/assay-WAVE-QUICKWINS-REPORT.md:90-125`; and asserted-as-closed by
`assay/tests/test_verify_recursion_depth.py:130-152`.

The record says three things that are not true:

1. **"all 7 `json.loads`/`json.load` sites in `src/assay`."** There are
   **11** parse call sites, not 7. The table lists 6 and omits 5:

   | omitted site | guard on the branch tip |
   |---|---|
   | `src/assay/coverage_parsers/coverage_py_json.py:98` | `except json.JSONDecodeError` **only** |
   | `src/assay/coverage_parsers/coverage_istanbul_json.py:229` | `except json.JSONDecodeError` **only** |
   | `src/assay/mutation_parsers/mutation_report_json.py:123` | `(json.JSONDecodeError, RecursionError, ValueError)` |
   | `src/assay/mutation_parsers/mutation_report_json.py:140` | `(json.JSONDecodeError, RecursionError, ValueError)` |
   | `src/assay/adapters/go_stmtpos.py:293` | `(UnicodeDecodeError, json.JSONDecodeError)` |

2. **"`verify_text` is the only remaining site that parses a document assay
   did not write"** (B074, "What was measured") and **"three untrusted
   sites, all three now guarded"** (B074 Resolution). Both are false. A
   coverage artifact is written by the *target project's own* coverage
   tooling and read by `assay run` at R1+ — the same "produced entirely
   outside assay" class B072's own rationale uses for attestations. I
   reproduced the crash **through the real dispatch entry point**, not the
   bare parser, on the branch tip:

   ```
   $ PYTHONPATH=src python -c '
   from assay.coverage import load_coverage_profile
   deep = "["*100000 + "]"*100000
   load_coverage_profile("{\"files\": " + deep + "}",
                         declared_format="coverage-py-json")'
   RecursionError
   ```
   and identically for `declared_format="coverage-istanbul-json"` with a
   `{"statementMap": …}` wrapper. The documents pass each format's own
   `sniff()` (a deliberate cheap substring check), so the
   `FORMAT_MISMATCH` pre-filter does not stop them. This is the *same*
   process-killing traceback instead of a judged
   `ERROR`/`UNREADABLE_ARTIFACT`, in a path a lane reaches on every R1 run.

3. **"a source-level sweep guard asserting all THREE untrusted-JSON sites
   still carry the identical clause, so a fourth variant cannot appear
   unnoticed"** (`test_verify_recursion_depth.py:130-152`, and the same
   sentence in the LOG and B074's Resolution). The test iterates a
   **hard-coded 3-element tuple** of file paths. It cannot detect a fourth
   site, a new site, or a variant — and in fact a variant already exists
   in-tree that it does not see: `mutation_report_json.py` carries the same
   three names in a **different order**
   (`JSONDecodeError, RecursionError, ValueError`), so the branch's stated
   goal ("a reader comparing them should find one shape rather than three
   variants") is already not met at 5 sites in 2 orderings.

**Why this is a blocker rather than a nit.** B072's third acceptance box
exists specifically so the gap "does not recur a third time". The sweep it
mandated was performed, found a third instance, and then *declared itself
complete* — leaving two live fourth/fifth instances behind a written claim
that there are none. A future sweeper reading the table will not re-derive
it; that is exactly the "don't trust a claim about an external system without
re-deriving it" failure the branch's own B068 finding is a monument to. The
shipped code on this branch is fine; the *record* is what must not merge as
written.

**Any of these discharges it** (reviewer does not choose):
- widen `coverage_py_json.py:98` and `coverage_istanbul_json.py:229` the same
  one-line way (cheapest, and it makes the sweep's own claim true); or
- file the two as their own backlog entry with the same measured repro, and
  correct the table, the site count, and the two false sentences; and
- either way, either make `test_every_untrusted_json_parse_site_now_catches_RecursionError`
  actually enumerate (walk `src/assay` for `json.loads`/`json.load` and assert
  the guard set) or reword its docstring so it stops claiming a property it
  does not have.

---

## Per-item findings

### B068 — ACCEPT

Independently re-derived and correct.

- `cli._run_reserved` calls `git.head_rev(lane_file.project_root)` at
  `src/assay/cli.py:728`, unconditionally, and nothing in `_cmd_run` or
  `_run_reserved` branches on `lane.rigor` before it. The `_resolve_repo`
  callers are `git.py:580` (`head_rev`), `git.py:656` (`repo_top`) and
  `git.py:1520` — none of them rigor-dependent. **There is no R0/R1-vs-R2
  divergence.** My live reproduction (severed real linked worktree, `main`'s
  code) produced the byte-identical `fatal: not a git repository: (null)`
  refusal for an `R0`-only lane and an `R0/R1/R2` lane. The backlog entry's
  premise is refuted, and the branch says so plainly in the entry itself —
  correct behaviour for a refuted premise.
- `_linked_worktree_gap` (`src/assay/git.py:404-476`) is genuinely
  diagnostic-only: it is consulted solely on the `returncode != 0` branch of
  `_resolve_repo`, returns `None` on every non-linked-worktree cause, and
  never raises (OSError → `None`). The raw git `fatal:` is prefixed, not
  replaced. The `repo_top/.git` marker is already proven to be a directory or
  regular file by `_nearest_git_marker`, so the `is_file()` branch really does
  mean "gitfile".
- All 14 tests fail against `main`'s `git.py`. The tier-independence is pinned
  through the real CLI, parametrized over both lanes.

Nits (no action required):
- `test_an_oversized_marker_file_is_refused_without_reading_it_all`
  (`tests/test_git_linked_worktree_gap.py:229`) does not actually assert the
  "without reading it all" half — it only asserts the message. The bound is
  real (`read(_MAX_GITFILE_BYTES + 1)`), the test name overstates what it
  measures.
- `test_an_unreadable_marker_file_says_nothing_rather_than_guessing`
  monkeypatches `Path.open` globally rather than using a real `chmod 000`
  file. Acceptable for a pure-diagnostic branch, but it is the one place in
  this module where a collaborator stands in for a real filesystem condition.
- The LOG says the two lanes produce a "byte-identical refusal"; the test
  asserts substring membership, not byte equality. The stronger claim is true
  (I measured it) but is not what is pinned.

### B072 — ACCEPT (the fix), see BLOCKER-1 (the sweep)

- `attestation.py:239-240` widening is correct and minimal. `RecursionError`
  is a `RuntimeError` subclass, so the old tuple genuinely missed it —
  reproduced on `main`, fixed on the branch.
- `tests/test_attestation_recursion_depth.py` (6): 4 of 6 fail against `main`;
  the 2 that pass are the explicitly-labelled premise guard and the
  legible-document control. Both real consumer hops
  (`load_attestation_file`, `load_attested_evidence`) are exercised, and
  `__cause__` is asserted to be a `RecursionError` so the fix cannot go
  silently inert. No hollow assertions.

### B062 — ACCEPT

- `pyflakes tests` on the branch tip: **one** line, the deliberate
  `broken.py` fixture. `pyflakes src/assay` and `pyflakes gate`: clean.
- The `main` baseline is exactly 25/5/1 as claimed.
- Both "needs judgement" classes verified empty by me, not accepted on
  assertion: `grep -rn importorskip` over `main`'s `tests/` returns nothing,
  and every one of the 5 dead locals is genuinely dead — the four
  `head_rev = git_repo.commit_all(...)` fixes keep the commit-creating call
  and drop only the binding (the honest fix), and `lane` in
  `test_mutation_executor_bound.py:285` is unreferenced. No `_ = value`, no
  `# noqa`, no file-level suppression anywhere in the diff.
- The gate widening is proven red-first *by me*: with `main`'s
  `tools/tester-unified-gate.sh` swapped in,
  `test_a_planted_unused_import_in_a_TEST_module_reddens_the_lint_phase` and
  `test_a_clone_with_no_tests_tree_refuses_rather_than_linting_nothing` both
  fail. The empty-expansion refusal is a real guard against the silent
  scope-shrink it names.
- `find -H … -prune` + `mapfile -d ''` is the right shape; `-H` follows only
  the named root, which is what makes the in-suite symlink variant of
  `test_the_shipped_source_tree_is_pyflakes_clean` work.

Nit: `test_the_fixtures_tree_is_pruned_and_an_unparseable_fixture_stays_green`
also passes against `main`'s script (which never looked at `tests/` at all),
so on its own it does not measure the prune. It is a valid green-side control
and the red-side proof exists separately — noting it only so nobody later
mistakes it for the widening's proof.

### B063 — ACCEPT

- The ruling (skip-with-a-named-reason) is implemented as ruled.
  `conftest.requires_parent_repository` asks **git** (`rev-parse
  --show-toplevel`) and compares the *toplevel* to `REPO_ROOT.resolve()`
  rather than testing for a `.git` marker — correct for linked worktrees and
  submodules, and it correctly does **not** skip in my own detached review
  worktree (68 passed / 9 skipped in place, all 9 the pre-existing
  `/opt/tester-venv` skips — matches the LOG exactly).
- The per-test (not module-level) marking of
  `test_runner_snapshot_selection.py` is the right call: outside the checkout
  that module still runs 11 tests and skips only its 2 embargo tests, with the
  skip reason naming the missing parent repo.
- The two `environment = "host"` → `"bare-host"` repairs are **genuinely
  pre-existing reds on `main`**, verified by running `main`'s own
  `test_cgroup_parent.py` against the (branch-unchanged) `assay/run-gate.toml`:
  `assert 'bare-host' == 'host'`. `f62642c6` ("estate-wide sweep of
  environment=\"host\" lanes broken by RG-43") is the commit that moved the
  lane; neither assertion followed. Not caused by this branch.

Nit (documentation only): `tests/conftest.py:105` and the LOG say skipping
`test_runner_snapshot_selection.py` module-wide "would hide **~60**" tests
that do not read the monorepo. The module collects **13** items (10 `def
test_`, one parametrized), of which 11 run outside the checkout. The
*reasoning* is right; the number is off by ~5×.

**Controller-relevant observation, not a defect of this branch:** because the
`tester-unified` lane's own pytest run does not ignore `test_cgroup_parent.py`,
`main`'s registered gate has been **red since `f62642c6` (2026-09-03)**. Any
"main gate green" claim dated after that is stale. This branch is the repair.

### B071 — ACCEPT

- Scope held exactly as ruled. `_crash_diagnostic_tails`
  (`src/assay/mutation.py:806-857`) returns `{}` for every bucket but
  `crashed`; `killed`/`survived`/`budget_exceeded` are untouched and asserted
  so. `write_progress`'s payload is not touched (I diffed the call site at
  `mutation.py:1791-1802` — only `_write_mutation_state_record`'s dict gains
  the spread).
- **No `assay verify` or wire-schema contact**: the B071 commit `b12ec9f2`
  touches only `src/assay/mutation.py`, `docs/CONSUMERS.md` and the new test
  module. `verdict.py`, `verify.py`, `schema.json` and `assay.toml` are not in
  it.
- No new `None` hazard: `run.result` is already dereferenced by
  `_classify_mutant_result(...)` two lines above the new call, behind the
  existing `if run is None: continue`.
- The size argument is sound. `json.dumps` with `ensure_ascii=True` escapes
  any codepoint `> 0x7e`, so `"\x7f"` really is the worst case *per encoded
  byte* (6 output bytes per 1 input byte; a non-BMP char is 12 output bytes
  per 4 input bytes = 3, i.e. cheaper). 2 × 64 KiB × 6 = 768 KiB < 1 MiB. The
  test's serialization (`sort_keys=True, separators=(",",":")`) matches
  `_write_mutation_state_record`'s actual `json.dump` call verbatim.
- The headline test really does drive `run_mutation` end to end with an
  `equivalence_artifact` lane whose mutated DDL apply fails, and asserts the
  real Postgres sentence out of the written record. The only stub is
  `process_runner`, which is this suite's established seam.

Nit: `CommandResult.__new__(...)` + `object.__setattr__` in the three helper
unit tests bypasses the real constructor. Harmless for a pure function, but it
means those three would not notice a `CommandResult` field rename.

### B074 — ACCEPT (the fix), see BLOCKER-1 (the "all three sites" claim)

- `verify.py:2583` widening is correct; crash reproduced on `main` and gone on
  the branch, both bare and through `cmd_verify`'s two arms and `cli.main`.
- 6 of the 8 tests fail against `main`; the 2 that pass are the labelled
  controls. `test_a_legible_document_still_reaches_verify_document` asserting
  equality with a direct `verify_document(...)` call is the right way to prove
  "this is a catch, not a validation change".
- **`provenance.py:137`'s judgment is affirmed correctly.** I checked the
  input: `provenance.py` reads `direct_url.json` from the *installed
  distribution's* `.dist-info`, written by pip; the enclosing function is
  best-effort and returns `None` on `OSError`, `ValueError`, non-dict and
  missing members. Treating it as a trusted input and leaving the narrow guard
  is defensible and the reasoning as written is accurate. (Note that leaving
  it narrow is *also* what makes the "one shape at every site" claim in
  BLOCKER-1 item 3 unattainable as stated.)

Nit: the sweep table's line numbers have drifted against the branch tip —
`verify.py:2562` is now 2583, `mutation.py:838` is now 890. Cosmetic, but a
sweep table's whole value is that the next reader can find the sites.

---

## Disclosed process slips — assessed

**1. `docker update --cpus=3` against two peer containers.** As disclosed, and
as harmless as claimed *so far as it is still checkable*: both
`dstdns-p128-assay-3135` and `-runner` currently report
`NanoCpus=3000000000`, i.e. exactly the estate-standard cap, so the write was
a no-op in effect. I cannot retroactively prove their value immediately before
the command, but the estate's own standing rule is to cap every gate container
to `--cpus=3` at launch, which is consistent. It remains a real discipline
slip (writing to a container this agent did not launch) and the disclosure is
the right response; no lasting harm found.

**2. Blurred commit boundary.** Confirmed and as small as claimed. `e426c29f`
(the B063 commit) contains two `4-backlog.md` hunks: the B063 Resolution
(correct for that commit) and the B071 Resolution (belongs with `b12ec9f2`).
Both hunks are **documentation only** — no code from B071 rode along, the
B071 code is entirely in `b12ec9f2`. I checked the B071 Resolution text
against the code as landed and it describes it accurately. Understating
nothing.

**3. Not disclosed but worth recording:** the wave prompt's binding constraint
"`assay verify` is unaffected by every item above" was overridden mid-wave by
controller ruling for B074. The LOG and REPORT both state this plainly at the
top rather than burying it. Correct handling.

---

## Hollow-test scan

Every test function added by this branch was read, and the four new modules
plus the three new gate-lint tests were re-run against `main`'s source in a
scratch copy. Result: **29 of 36 fail against the unfixed code.** The 7 that
pass are, in full:

| test | why it legitimately passes unfixed |
|---|---|
| `test_attestation_recursion_depth.py::test_the_fixture_is_genuinely_inside_the_size_bound` | premise guard on the fixture, not on the fix |
| `test_attestation_recursion_depth.py::test_a_well_formed_attestation_at_the_same_site_is_unaffected` | labelled control |
| `test_verify_recursion_depth.py::test_an_ordinarily_malformed_document_is_unchanged` | labelled control |
| `test_verify_recursion_depth.py::test_a_legible_document_still_reaches_verify_document` | labelled control |
| `test_mutation_state_crash_tails.py::test_a_killed_candidates_record_is_unaffected_in_shape_and_size` | labelled control (absence assertion) |
| `test_mutation_state_crash_tails.py::test_a_survived_candidates_record_is_unaffected_too` | labelled control (absence assertion) |
| `test_mutation_state_crash_tails.py::test_a_crashed_record_still_resumes` | round-trip guard, not a tails assertion |
| `test_distribution_gate.py::test_the_fixtures_tree_is_pruned_…` | green-side control (see B062 nit) |

No bare `assert result`-shaped tests. No mocked collaborator standing in for
what should be a real call, with the two nits noted above
(`monkeypatch.setattr(Path, "open", …)` in B068's unreadable-marker test, and
`CommandResult.__new__` in B071's three helper tests). Every remaining
`monkeypatch` is an *injection to pin which exception is caught*, which is the
point of those tests, not a substitute for the real path — the real path is
exercised separately in the same module in both cases.

---

## Schema invariants — confirmed unchanged

- `src/assay/verdict.py:287` → `VERDICT_SCHEMA_VERSION = 10`
- `assay/assay.toml:21` → `schema_version = 2`
- `src/assay/cli.py:1273` → `LANE_INVENTORY_SCHEMA_VERSION = 1`
- `git diff main...HEAD -- assay/src/assay/verdict.py assay/assay.toml
  assay/src/assay/schema.json` is **empty**.

`CHANGES.md` is deliberately untouched (the `[Unreleased]` block is empty by
the project's own standing housekeeping rule; cmru generates the dated entry
at release). Correct.

---

## Gate & measurements (my own runs)

Host discipline: `docker ps` and `pgrep -af tester-unified-gate.sh` before
starting — no gate container and no gate process were running; load average
2.72. My gate container (`compassionate_cray`) was capped with
`docker update --cpus=3` immediately after it started
(`docker inspect` → `NanoCpus=3000000000`). No peer container was touched.
Every direct pytest run was `nice -n 19 ionice -c 3`.

### Registered gate — **GREEN**, independently, at `7bcf089a`

```
./run-gate.py --worktree /workspaces/vbpub/.worktrees/assay-b068-review tester-unified
```

Verdict read from the log's own markers **in a separate step**, never from a
piped exit code (LESSONS L4):

```
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

All **12** `ASSAY_GATE_PHASE` markers present and in order —
`wheel-installed`, `attestation-hardened`, `verdict-v5-accepted`,
`lane-schema-v2-successors-verified`, `verdict-v6-v7-v8-v9-hard-cut-verified`,
`verdict-v10-successors-verified`,
`judge-provenance-bound-to-the-installed-wheel`, `self-hosted-lane-passed`,
`topos-qualified`, `cmru-b006a-qualified`, `independent-self-hosting-passed`,
`pyflakes-clean` — with **zero** `ASSAY_GATE_DIAGNOSTIC` lines. The
self-hosted lane's own verdict line reads `tester-unified: PASS (exit 0)`.

Note on the judged tip: my worktree is detached at **`7bcf089a`**, the last
commit that touches any code or test. The branch head has since advanced to
`83d5ebda`, which adds 55 lines to
`reports/assay-WAVE-QUICKWINS-CONTROLLER-LOG.md` and nothing else — I checked
its `--stat`. The gate verdict above therefore covers every judged byte.

### B063's acceptance measurement — re-run by me, and it is *better* than reported

Fresh `cp -r` of the branch's `assay/` into a scratch directory outside any
git repository (`git rev-parse` there: `fatal: not a git repository … up to
mount point /`), full suite, `nice -n 19 ionice -c 3`:

```
4161 passed, 77 skipped, 1 warning in 362.43s (0:06:02)
```

**Zero failed, zero errors.** The LOG quotes `2 failed, 4143 passed, 77
skipped in 446.07s` — that measurement was evidently taken before the two
`host`→`bare-host` repairs in the same commit landed. The skip count matches
exactly (77). Against R-1's baseline of `11 failed, 3956 passed, 18 skipped,
13 errors`, the acceptance box is met with room to spare.

In place (my worktree, inside the checkout), the three modules are unchanged:
`68 passed, 9 skipped`, and all 9 skips are the pre-existing
`requires the tester-unified image's own /opt/tester-venv` ones — **none** are
B063 skips. Reproduced exactly as the LOG states.

---

## Summary of required action

**BLOCKER-1** — correct the sweep record (and/or widen the two coverage
parsers), and stop `test_every_untrusted_json_parse_site_now_catches_RecursionError`
from claiming an enumeration it does not perform. Repro for the two live
instances is in this report; both are one-line fixes if that route is chosen.

**Should-fix (not blocking):**
- `tests/conftest.py:105` and the LOG: "~60" → 11 (13 collected items).
- B074's sweep-table line numbers (`verify.py:2562` → 2583, `mutation.py:838`
  → 890).
- The LOG's B063 numbers (`2 failed, 4143 passed … 446.07s`) are stale — the
  final tip measures `4161 passed, 77 skipped, 0 failed, 0 errors in 362.43s`.
  The acceptance box is *more* comfortably met than claimed; the quoted
  numbers should be refreshed so the record matches the shipped tip.
- Duplicated horizontal rule at `4-backlog.md:7424` and `:7426` (a stray
  `---` left after B072's Resolution). Cosmetic.

**Nits, take or leave:** the three test-name/collaborator observations noted
per item above.

Nothing in this review asks for a change to shipped behaviour. The six fixes
themselves are all correct as landed.

---
---

# Fix-verification (round 1 blocker) — 2026-09-08

Same reviewer, same independent worktree
(`/workspaces/vbpub/.worktrees/assay-b068-review`), updated in place to the
new tip. Scope: `7ec114c6..12b4a3ed` — `93e6f7fc` (the fix), `001a1f24` and
`12b4a3ed` (the record).

## Verdict: **ACCEPT**

BLOCKER-1 is discharged, and discharged wider than I raised it. The sweep is
now genuinely complete, the guard test genuinely cannot pass vacuously, the
retraction is in writing rather than silently applied, and my own
from-scratch registered-gate run is GREEN at `12b4a3ed`.

Nothing is left outstanding. Two cosmetic nits are recorded at the end; both
are comment text, neither blocks anything.

## 1. Is the sweep actually complete? — re-derived, not read

I wrote my **own** AST walk (independent of theirs: different algorithm —
nearest-enclosing-`Try`-by-parent-map rather than a handler stack, and it
also matches a bare `loads`/`load` name that theirs does not) over
`src/assay/**/*.py`. Result:

**11 parse call sites. 8 guarded. 3 trusted with written reasons. Zero
unaccounted.**

| site | enclosing fn | catches `RecursionError`? |
|---|---|---|
| `adapters/go_stmtpos.py:293` | `_read_document` | yes (**new**) |
| `adjudication.py:154` | `evaluate_provenance` | yes |
| `attestation.py:239` | `parse_attestation` | yes |
| `coverage_parsers/coverage_istanbul_json.py:229` | `parse` | yes (**new**) |
| `coverage_parsers/coverage_py_json.py:98` | `parse` | yes (**new**) |
| `mutation_parsers/mutation_report_json.py:123` | `sniff` | yes (pre-existing) |
| `mutation_parsers/mutation_report_json.py:140` | `parse` | yes (pre-existing) |
| `verify.py:2583` | `verify_text` | yes |
| `mutation.py:890` | `_load_validated_state_record` | no — **TRUSTED**, reason stated |
| `provenance.py:137` | `_installed_wheel_digest` | no — **TRUSTED**, reason stated |
| `verdict.py:501` | `load_schema` | no (no `try` at all) — **TRUSTED**, reason stated |

My enumeration and theirs agree site-for-site, function-for-function. The
backlog's new 11-row table matches both. I also confirmed there is **no**
aliased import anywhere in `src/assay` (`from json import …`, `import json as
…`, `orjson`/`ujson`/`simplejson`: all absent), so the collector's
`json.loads`/`json.load` attribute match covers the entire real population
today, not just the part it happens to look at.

The three trusted dispositions hold up on inspection: `verdict.py` reads
assay's own shipped package resource, `provenance.py` reads pip's own
`direct_url.json` (affirmed twice now), and `mutation.py:890` reads a record
assay itself wrote via `_write_mutation_state_record` earlier in the same
run, bounded by `MUTATION_STATE_RECORD_LIMIT`. B071's new tails are JSON
*strings*, so they add at most one level of depth — they do not make that
file a depth risk.

## 2. Are the three new fixes real? — reproduced against the pre-fix code

Extracted `main`'s versions of the three parsers into a scratch tree and ran
each against a 100,000-deep document:

```
coverage-py-json        -> *** RecursionError ESCAPED ***     (via load_coverage_profile)
coverage-istanbul-json  -> *** RecursionError ESCAPED ***     (via load_coverage_profile)
go_stmtpos._read_document -> *** RecursionError ESCAPED ***
```

Same three on the branch tip:

```
coverage-py-json        -> AssayError  ERROR / UNREADABLE_ARTIFACT
coverage-istanbul-json  -> AssayError  ERROR / UNREADABLE_ARTIFACT
go_stmtpos              -> AssayError  ERROR / UNREADABLE_ARTIFACT
```

The `go_stmtpos.py` site the controller found independently is **confirmed
real** — it was catching `(UnicodeDecodeError, json.JSONDecodeError)` and a
`RecursionError` walked straight through it. Keeping `UnicodeDecodeError`
first is correct (the `.decode("utf-8")` runs before `json.loads`), and
`test_undecodable_helper_output_is_still_refused_the_same_way` pins that arm
— it is a genuine control, and it passes against the pre-fix code, which is
exactly what a control should do.

Red-first, measured by reverting the three parsers to `main` in a scratch
copy of the branch: **9 failed / 86 passed** across the four affected test
modules. All five new behavioural tests are red without the fix; four sweep
assertions go red too.

## 3. Can the guard test pass vacuously? — five mutation experiments

I did not take the AST walk's word for itself. Baseline on the tip:
`12 passed`. Then, one mutation at a time, each reverted after:

| # | mutation | expected | observed |
|---|---|---|---|
| A | strip `RecursionError` from `coverage_py_json.parse`'s clause | red | **red** — `test_every_untrusted_json_parse_site_catches_RecursionError` + the by-name pin `[identity3]` |
| B | re-order `verify.py`'s clause to `(RecursionError, JSONDecodeError, ValueError)` | green (order-independent) | **green**, 12 passed |
| C | add a brand-new `src/assay/newpkg/newparser.py` with `except json.JSONDecodeError` — *the exact blind spot the old test had* | red, naming the new file | **red**: `src/assay/newpkg/newparser.py:6 (in parse_something)` |
| D | remove the `try` around `coverage_istanbul_json.parse`'s `json.loads` while keeping the function name | red | **red** — derived sweep **and** the by-name pin `[identity4]` |
| E | silently widen a TRUSTED site (`provenance.py`) so its allowlist entry is stale | red | **red** — `test_no_trusted_entry_is_stale` |

(A sixth attempt — renaming the guarded `parse` function — fails even harder:
the format registry raises `AttributeError` at import, so the whole suite
cannot collect. Loud, if not via this test.)

Experiment **C** is the one that matters: it is precisely the failure the
original hard-coded tuple could not see, and the rewritten guard sees it and
names the file and function. `test_the_sweep_finds_a_plausible_population`
(`>= 10` sites, plus one from each of the three subpackages the old glob
missed) closes the vacuous-walk hole. **The guard is real.**

One residual, theoretical only: a future site written as `from json import
loads; loads(x)` would evade the collector's attribute match. There is no
such import in `src/assay` today and the project style is uniform, so this is
a note for the next person, not a finding.

## 4. The record

The retraction is written where the false claim stood, not applied silently
— block quotes marked `RETRACTED` in both B072's and B074's resolutions, the
root cause named (`src/assay/*.py` does not descend), the corrected 11-row
table with a per-site disposition, and the trust bar written down as a
uniform rule instead of a per-site judgment call. The LOG even retracts its
own commit message ("the third and **last** site") rather than rewriting
history. That is the right handling.

All four of my should-fix nits are done and I verified each: `~60` → "the
other 11 collected items"; the drifted line numbers corrected *and* the table
now says why they drift (which is why `TRUSTED_SITES` keys on `(module,
function)` instead); the B063 numbers re-measured at the final tip
(`4178 passed, 77 skipped, 0 failed, 0 errors` — consistent with my own
`4161 passed, 77 skipped` at the previous tip plus exactly the 17 tests this
commit adds); and the duplicated `---` at `4-backlog.md` is gone (re-checked
with an adjacency scan — no adjacent rules anywhere in the file).

## 5. My own gate — **GREEN at `12b4a3ed`**

Run from scratch in my own worktree; no other gate container or
`tester-unified-gate.sh` process was present at launch (a peer `ciu` gate had
finished first and I waited for it); my container (`competent_dhawan`) capped
to `--cpus=3` immediately after start (`docker inspect` →
`NanoCpus=3000000000`); no peer container touched. Verdict read from the
log's own markers **in a separate step**:

```
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

All **12** `ASSAY_GATE_PHASE` markers, in order, **zero**
`ASSAY_GATE_DIAGNOSTIC` lines. `pyflakes-clean` is included — which now also
covers the four new/changed test modules under B062's widened scope, and I
separately confirmed `python -m pyflakes tests` still emits only the
deliberate `broken.py` line.

## Remaining nits (cosmetic, non-blocking, no re-review needed)

1. `src/assay/coverage_parsers/coverage_py_json.py:104` — the new comment
   says the refusal is `ERROR`/`MALFORMED_COVERAGE`. There is no
   `MALFORMED_COVERAGE` reason code; `_malformed()` raises
   `ERROR`/`UNREADABLE_ARTIFACT`, which is what the test correctly asserts.
   Comment text only.
2. `tests/test_coverage_parsers_coverage_py_json.py` — the docstring on
   `test_a_pathologically_deep_document_is_unreadable_not_a_raise` ends
   "Driven through the real `load_coverage_profile` entry point, not the bare
   parser", but that test calls the bare parser; it is the *next* test that
   drives the registry. The sentence is in the wrong docstring.

## ACCEPT

The blocker is discharged, the fix is wider and better-evidenced than what I
raised, the guard that replaces the unfit one is proven by mutation rather
than asserted, the record retracts its own error in writing, and the
registered gate is green on my own independent run. **This is my merge
signal.**
