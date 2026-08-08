# P19 — isolated R3 CLI pipeline — LOG

**Status:** DONE (merged)
**Branch:** `feat/assay-P19-isolated-r3-cli-pipeline`
**Worktree:** `/workspaces/vbpub/.worktrees/assay-P19-isolated-r3-cli-pipeline/assay`
**Base:** `91d29390` (`docs(assay): A-O17 -- one AssayError still escapes evaluate_r1, assigned to P22`)
**Handoff `input_revision`:** `48771e48c7b2ed7ed937cbe07e193718c6f242bb` — unchanged
between that commit and `91d29390`; nothing in `src/assay/` moved in between.

Implemented and self-reviewed by an implementer session; reviewed, repaired and
merged by the controller. One structural defect, one absent oracle, one false
recorded impossibility and one misleading public name were found in review and
repaired before merge — recorded as A-149–A-152. See "Controller review", below.

**Sol finding 1 is now CLOSED.** Declared Python R0, R1, R2 and R3 lanes all run
and are judged end to end through the installed console script. R3 proves one
declared canary in a disposable copy of the consumer's repository, which is
never staged, committed, or written to.

**A note on P17.** There is still no `assay-P17-r1-cli-pipeline-LOG.md`, and
this LOG does not invent one, for the same reason P18's did not: reconstructing
a package's own record after the fact would be exactly the plausible-but-
unverified artifact this project exists to refuse. P17's information lives in
its four commit bodies, in A-139–A-144, and in the "Carried in from P17"
sections of the P18/P19 handoffs. Two of three now have a LOG; P17 remains the
gap, recorded rather than papered over.

## What was built

- `src/assay/config.py`: `judge.canary` stops being an opaque `Mapping` and
  becomes a closed, validated `CanaryConfig` — exactly `mechanism` and
  `target`, never a plural list (work item 2: one R3 claim is one mechanism
  execution, and schema v3 carries a single canary payload). `mechanism` is
  cross-checked at LOAD time against `assay.canary.CANARY_MECHANISMS` through a
  DEFERRED import inside the loader, the identical reasoning `_load_mutation`
  already gives for `MUTATION_OPERATORS` one field over. `target` must be a
  normalized, project-relative path to a real, ordinary file contained beneath
  one of the lane's own declared source roots; absolute, empty, traversing,
  symlinked (either the target itself or a root it escapes through),
  non-existent, and directory targets are each refused with their own
  diagnostic. `_as_opaque_table` — the last opaque passthrough in the loader —
  is gone.
- `src/assay/canary.py`: new `run_isolated_canary`, the CLI-facing R3 entry
  point. Two prerequisite refusals first (a test-path target, per the adapter's
  own `is_test_path` — the one part of the table `config.py`'s
  adapter-agnostic loader cannot check itself; and an unclean consumer repo,
  since the copy it is about to take BECOMES the control). Then the consumer's
  whole repository top is `shutil.copytree`d — `.git` included, so a symbolic
  `judge.base` still resolves inside the copy — into a `TemporaryDirectory`
  this function owns end to end, and the existing, unmodified
  `run_python_canary` runs against the copy. `_project_prefix` locates the
  project inside the copy for the subdirectory-project layout (A-145), an
  independently written second copy of `mutation.project_prefix` rather than an
  import from a forbidden module. `CANARY_MECHANISMS` is derived from
  `_EXPECTED_REASON_BY_MECHANISM`'s own keys, so the config vocabulary and the
  dispatch table cannot drift. (Controller: `_relocate_source_roots`, A-149;
  the function's name, which was `run_isolated_python_canary`.)
- `src/assay/runner.py`: `run_lane` gains its R3 block, after R1 and R2 and
  UNCONDITIONAL — never gated on R0's own outcome the way R2 is, because R2
  reuses that result as its baseline and R3 reuses nothing of it. An
  `AssayError` from the prerequisite refusals becomes a payload-free R3 claim,
  the identical shape R1/R2's own guard sequences use; otherwise
  `build_canary_claim` builds the real one and `judgment.r3` is populated
  alongside it. Final `ended` is extended a third time.
- `src/assay/cli.py`: `"R3"` added to `_built_in_registry`'s ONE existing
  `PythonAdapter` entry, and `"R3"` to `_ADAPTER_BEARING_LEVELS` — the whole
  registry change, exactly as A-139 and A-145's carried-in note predicted. The
  `--help` capability sentence moved with it (A-146 made that a red, not a
  docs chore).
- `src/assay/verdict.py` / `src/assay/verify.py`: **A-148 closed.**
  `judgment.r2` and `judgment.r3` now carry the same construction-time
  correspondence check `judgment.r1` already had, in BOTH places — the model
  (`Verdict._check_judgment_matches_claims`) and the independent raw-document
  consumer (`verify._check_judgment_matches_claims`). Presence agrees with the
  payload-bearing claim in both directions; every operator a mutation payload
  names must be one `judgment.r2.operators` declared; a canary payload's
  `mechanism` must be the one `judgment.r3` records. The two stale "RESERVED;
  populated by a future package" docstrings P18 had already falsified are gone.

The implementer's own self-review found and fixed one real gap before
reporting: the raw-JSON and reconstruction-time versions of the new R2/R3
correspondence checks were worded closely enough that a test proving the raw
check was independently reachable still passed with that check disabled. Both
were reworded and reverified. That is the mechanical class the self-review step
exists to catch, caught.

## Controller review

The branch arrived gate-green at 1826 passed, 100% statement AND branch
coverage. Four findings.

### A-149 — the isolated canary judged the copy by the consumer's own paths

**The defect.** `judge.source_root_paths` are RESOLVED, ABSOLUTE directories
under the consumer's project root. `run_isolated_canary` handed the lane
through to a pipeline running inside the scratch copy without respelling them.
Nothing raised. `evaluate_coverage`'s source-root boundary simply rejected
every changed file, `considered` fell to 0, `pct` became a vacuous 100.0, and
R1 returned PASS having measured nothing — the laundering-gate shape A-016 and
A-035 already name for a typo'd source root, reached by relocation instead of
by typo.

**What it cost, in the product.** `uncovered-line` — one of exactly two
mechanisms — could never PASS. Its expected reason is `UNCOVERED_LINES`, which
only R1 produces; with R1 vacuous inside the copy, the transformed half PASSed
and every real run of that mechanism reported `CANARY_SURVIVED`. `import-break`
appeared to work only because its transformed half fails at R0, before R1 is
consulted at all — but its control half was PASSing for a reason unrelated to
the one its own oracle named.

**Why no oracle saw it.** Every R3 fixture in the package declared `R0`+`R3`
alone, except one that declared `R0`+`R1`+`R2`+`R3` with the `import-break`
mechanism. The single configuration that could expose it — `uncovered-line`
with R1 declared alongside R3 — was the one nobody wrote (A-150).

**Repair.** `canary._relocate_source_roots` respells `source_root_paths`
against the scratch project root before the lane is handed on, and its
docstring enumerates why no other field needs it. Two new oracles: a unit-level
`run_lane` test that proves `uncovered-line` PASSes for `UNCOVERED_LINES`
through the isolated pipeline, and an installed-wheel complete artifact for the
same lane — the only place end to end where the copy's own R1 half is
load-bearing.

### A-150 — half of O3 had no witness

O3 says "import-break and uncovered-line each PASS only for their specific
expected reason". `uncovered-line` appeared twice in the shipped artifact set,
both times as a SURVIVOR on a lane where a PASS was structurally impossible. A
mechanism can only be proved where its expected reason is producible; the
fixture must declare the rigor level that produces it. Now ruled, and stated in
DESIGN-GUIDE §12 so a consumer configuring an R0+R3 lane learns it before
running one.

### A-151 — a recorded impossibility that was not one

`test_standalone.py` recorded that a genuine wrong-observed-cause is not
producible through the real, un-mocked adapter, and that only a mislabeled
adapter subclass reaches it. It is producible: `import-break` injected into a
module the lane's own tests never import leaves R0 passing, and R1 catches the
injected line as uncovered — observed `UNCOVERED_LINES` against an expected
`COMMAND_FAILED`. Reproduced, then added as both a unit test and an
installed-wheel complete artifact. The argument's flaw needs no reproduction to
see: the mechanism/expected-reason pairing fixes what is EXPECTED, never what a
run OBSERVES. A wrong impossibility is worse than a silent gap because it
propagates as fact — P24's handoff had already been told to trust this one.
A genuine NO-OP transform remains unreachable, and that argument does hold: both
injectors change the text unconditionally, which their own tests check.

### The name — `run_isolated_python_canary` → `run_isolated_canary`

The function takes `adapter: LanguageAdapter` and never names `PythonAdapter`;
`run_lane` calls it for whatever adapter resolved. P24 inherits it for Go and
cannot rename it (`runner.py` is not in its `scope.touch`), so the rename
happened here, where both files are in scope. Naming a language into a
language-neutral entry point is the same class of misdeclaration the whole v1.1
series exists to remove, pointed inward at the codebase instead of outward at a
consumer.

### A-152 — `judgment.r3.target` stays untied, deliberately

A-148's check ties `judgment.r3.mechanism` to `Claim.canary.mechanism` because
the payload carries a mechanism. It carries no target, so nothing inside schema
v3 can witness `target` being wrong. Closing it needs a `canary.target` field
and therefore a v4 migration, which A-138 makes a consumer's decision.
Recorded as an accepted, named gap (A-O18 in `STATE.md`) rather than
improvised around: inferring the target from `judgment.r1.source_roots` or from
the canary description string would be a rule that looks like verification and
is not.

## The nine work items, against the tests that hold them

Read against the tests, numbered, because P18's item 7 was simply absent and
nothing in the commits said so.

| # | Work item | Where it is held |
|---|---|---|
| 1 | closed `judge.canary` table; containment, traversal, symlink, existence, unknown keys | `test_config_canary.py` — 16 tests, one per rejection plus the round trip. Test-path rejection is deliberately NOT here (it is adapter-specific and this loader carries no adapter knowledge); it lives in `canary.run_isolated_canary` and is held by `test_runner_run_lane_r3.py::test_r3_refuses_a_test_path_target_as_a_payload_free_claim`. |
| 2 | one R3 claim is one mechanism execution, never a plural list | `CanaryConfig` has no list field to declare; `test_config_canary.py::test_canary_with_an_unknown_key_is_rejected` closes the table against one being added by config. |
| 3 | copy before the control run; never write in the consumer worktree | `test_runner_run_lane_r3.py` (fingerprint after every terminal case) and four `test_standalone.py` artifacts through the real wheel. |
| 4 | cause sensitivity; broken control, unknown, no-op inconclusive; wrong cause survives | `test_canary_python_pipeline.py` (unit, unchanged from P09) plus, new here, the wrong-cause and uncovered-line-PASS cases with the REAL adapter (A-151, A-150). |
| 5 | reuse P17's argv/env/base and installed adapter; `ended` after both halves | `test_ended_covers_r3s_own_completion_not_only_r0s` (seven clock reads, enumerated); argv/env fidelity is held by the complete-document comparisons. |
| 6 | complete installed-wheel artifacts for six named shapes | `test_standalone.py`: PASS (import-break), PASS (uncovered-line, R1 declared), broken control, survivor, wrong cause, malformed configuration — six, each a whole-document `==`. The seventh shape work item 6 does not name, a genuine no-op transform, is recorded in the suite as unreachable with its argument (A-147's rule, applied and this time checked — A-151). |
| 7 | fingerprint consumer HEAD, index, tracked/untracked paths and bytes | `head()` plus `git status --porcelain` after every terminal case. Reviewed and kept in preference to the raw-filesystem `_tracked_file_hashes` the R2 tests use: an empty porcelain plus an unchanged HEAD is byte-exact for tracked content, and it does not mistake a real `pytest` run's own self-ignoring cache directories for pollution. |
| 8 | break each property separately; run the real gate; record A-067 counts | Below. The implementer did not run this set; the controller did. |
| 9 | tie `judgment.r2`/`r3` to their claims in both `verdict.py` and `verify.py` | `test_verdict_judgment.py` (six new cases: both directions at both levels, plus the operator and mechanism cross-checks) and `test_verdict_conformance.py` (the same rules on the raw document, independently reachable). |

`README.md` has been in every rigor-wiring package's `scope.touch` since P17
and does not exist in this project. Not created here: consumer-facing
documentation is P21's O3, which pins a wheel version and sha256 and is the
package that actually owns it. Recorded so its absence is not read as an
oversight three packages running.

## A-067 — break the property, count the failures

Each property was broken separately against the merged tree and the real suite
run; the source was restored between mutations. A count of zero would mean the
property has no discriminating oracle.

Run in the devcontainer against the merged tree, one property at a time,
source restored between mutations. Baseline: **1830 passed, 1 skipped, 1
deselected**. The deselection is
`test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape`,
which pins `assay_version` and fails outside the gate image for A-069/A-124's
already-documented reason — it fails identically on `main`, and the real gate
run below covers it.

| # | Property broken | Failing tests |
|---|---|---|
| 1 | scratch isolation — run the canary against the consumer's own repo instead of the copy | **9** |
| 2 | source-root relocation (A-149) — hand the copy the consumer's own absolute roots | **4** |
| 3 | `judge.canary` closure — accept unknown keys | **1** |
| 4 | mechanism vocabulary — accept any mechanism string | **2** |
| 5 | target containment — drop the source-root boundary | **2** |
| 6 | target symlink refusal | **1** |
| 7 | test-path refusal (adapter-specific, `canary.py`'s own) | **1** |
| 8 | consumer-clean precondition — copy a dirty tree as the control | **1** |
| 9 | expected-reason comparison — accept any transformed non-PASS | **8** |
| 10 | control gating — judge the transform against a broken control | **11** |
| 11 | final timing — leave `ended` at R0/R1/R2's own value | **1** |
| 12 | A-148 correspondence, model side (`Verdict` construction) | **6** |
| 13 | A-148 correspondence, raw document (`verify.py`, independent) | **6** |
| 14 | registry widening — Python no longer declared at R3 | **7** |

No property scores zero. Three observations worth carrying:

- **Row 2 is the repair's own oracle, and it is worth reading against row 1.**
  Breaking isolation outright (row 1) trips nine tests, because the consumer's
  repository visibly changes. Breaking only the PATHS the copy is judged by
  (row 2) trips four — and before the repair it tripped **zero**, because the
  four tests that catch it are the ones added in review. A defect that leaves
  the observable side effects correct and only corrupts the measurement is the
  quiet half of this class, and it is the half a fingerprint oracle cannot see.
- **Rows 3, 6, 7, 8 and 11 score exactly 1.** Each is a single-purpose refusal
  with a single test, which is proportionate — but it also means each has
  exactly one witness, so deleting that one test silently retires the property.
  Recorded rather than padded: adding a second assertion of the same fact would
  raise the number without raising the evidence.
- **Rows 12 and 13 score identically (6 each) and do not overlap.** That is
  what the implementer's own self-review fixed before reporting: the two
  layers' messages had been worded closely enough that a test proving the raw
  check was independently reachable passed with that check disabled. Six and
  six, from two independent layers, is the shape A-148 asked for.

## Gate output (real `tester-unified` Docker container)

Run by parsing `nyxloom-trove/nyxloom.toml`'s own `[gates.tester-unified]`
`argv` and executing it verbatim — never a transcript, never a hand-typed
approximation of it. The gate builds a wheel offline from this tree, installs
it into a scratch venv, runs `assay run tester-unified` through the installed
console script (self-hosting, P14), and then runs
`tests/test_self_hosting.py` through the container's OWN interpreter as the
independent oracle.

```
tester-unified: PASS (exit 0)
  commit: 67533325890e94360a6cc3a373c6bf4df383fce3
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
.......                                                                  [100%]
7 passed in 11.41s
EXIT 0
```

The gate's own `argv` reports an exit code and nothing else, so counts and
coverage were measured in a SECOND run INSIDE `tester-unified:local` itself
(not in the devcontainer, whose venv carries different pins):

```
TOTAL   3070 stmts   0 miss   1256 branch   0 partial   100%
1831 passed, 1 skipped in 125.70s
```

**1831 passed, 1 skipped, 100% statement AND branch coverage** (3070
statements / 1256 branches), up from P18's 1781 / 2941 / 1188. The one test
deselected in the devcontainer run above passes here — it pins `assay_version`,
which only resolves correctly inside the gate image (A-069/A-124).

**A first attempt at this gate returned `NO_MEASUREMENT`/`DIRTY_TREE` (exit
3)**, with the controller's own repairs uncommitted. That is A-140 working
exactly as ruled — and worth recording, because it means the gate cannot be
run against a working tree, only against a commit.

Re-run a second time, independently, directly against `main` after the merge
(the project's own twice-per-package discipline): `tester-unified: PASS (exit
0)`, commit `d1e1f258`.

## What could not be honored as written

- **Work item 6's "no-op" artifact.** Not producible through the installed CLI
  with the real adapter: both injectors change the text unconditionally, so no
  input produces a no-op. Recorded in the suite with the argument (A-147); the
  unit-level proof with a fake adapter remains.
- **Work item 1's "duplicates".** `judge.canary` declares exactly one target,
  so there is no list in which a duplicate could appear. Not a gap — a clause
  that work item 2's own "never a plural list" made vacuous.
- **`judgment.r3.target`'s correspondence check.** A-152; needs schema v4.

## What this changes for the packages that follow

- **P24** (`scope.touch` amended, notes rewritten and ratified). Gains
  `src/assay/cli.py` — the third instance of A-144's carve shape. Inherits
  `run_isolated_canary` under its corrected name, A-149's relocation rule,
  A-150's "declare the level your mechanism's reason comes from", and A-151's
  correction of the wrong-cause impossibility it had already been told to
  trust.
- **P20/P22/P23** unaffected in scope. A-149's rule is worth reading for any
  package that runs a judgement inside a copy; P23's mutants run the lane's
  command in a copy but never evaluate coverage there, so it does not bite.
- **DESIGN-GUIDE §6 and §12** updated by the controller (both are in every
  implementer's `scope.forbid`): the `judgment.r2`/`r3` "reserved shape"
  paragraph is now accurate, `judge.canary`'s closed shape appears in the
  reference lane, and A-150 is stated where a consumer configuring a lane will
  meet it.
