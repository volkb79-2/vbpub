# nyxloom (self-hosting project) — LESSONS (project-local)

> **Sibling of `reference/LESSONS.md`.** This is the **writable** lessons surface
> for the nyxloom-building-nyxloom project. The factory and any working agent
> append discovered lessons HERE — never directly to the shipped
> `reference/LESSONS.md`. See that file for the placement & promotion model.
>
> **Each entry:** `scope: project | product`. A `product`-scoped lesson also emits
> an upstream proposal (`upstream: proposed`) for a maintainer to integrate into
> `reference/LESSONS.md`; mark it `upstream: integrated (ref)` once accepted. A
> `project`-scoped lesson stays here.

---

## PL1 — Dual statefile schema is structural debt; de-duplicate it
`scope: product` · `upstream: integrated (ref: reference/LESSONS.md L1)` · **RESOLVED (factory-hardening A)**

The statefile JSON schema existed as two hand-maintained copies
(`schemas/statefile.schema.json` + the packaged `src/nyxloom/schemas/statefile.schema.json`);
only the packaged copy was referenced (pyproject `nyxloom = ["schemas/*.json"]`)
and it diverged twice (D-CORRECT-2, F017). Per canonical **L1** the byte-identity
guard test was a band-aid — the structural fix is one source of truth.

**Resolution (factory-hardening A).** Classification confirmed the top-level
`schemas/*.json` copies (`event`, `handoff-frontmatter`, `statefile`) had **no
readers** — every loader uses `importlib.resources.files("nyxloom.schemas")` (the
packaged dir) and `handoff-frontmatter` had already drifted stale *unguarded*. All
three top-level copies and the guard test (`tests/test_schema_sync.py`) were
removed; `src/nyxloom/schemas/` is the single source of truth; `routes.example.toml`
(a genuine example, no packaged twin) stays under `schemas/` with a README
documenting the dir's reference-only purpose. The general principle
(one-source-of-truth for shipped schemas) is already integrated upstream as
canonical **L1**, which uses this very incident as its worked example.

## PL2 — The gate's value is a *composition*; nyxloom requires an interface, offers a toolkit, mandates no infra
`scope: product` · `upstream: proposed`

Factory-hardening A/F validated that "the gate catches real bugs" — but the value
is not any single component (not docker, not `tester-unified`). It is a **stack**,
and only one layer is infra:

1. **INFRA** — a runtime-faithful *isolated* environment, never the dev cockpit
   (project-owned, expressed entirely inside the gate's `argv`).
2. **TOOLKIT** — a changed-line completeness floor (`coverage_gate.py`) and mutation
   (`mutation_gate.py`): nyxloom-shipped but **opt-in** and ecosystem-specific.
3. **CONTENT** — the project's own invariant/behavioral tests. (F's two real catches
   were a config-schema invariant test + the coverage floor — neither is infra.)
4. **DISCIPLINE** — GATE→VERDICT→MERGE read as *separate* steps (canonical **L4**)
   + SOLO serialization across gates.

**The generalization for "all kinds of projects."** nyxloom must **REQUIRE only the
interface**: a `[gates.*]` command that runs isolated at a commit and exits non-zero
on failure with nothing masking it (the `{worktree}` placeholder is the sole
integration seam; `gate_runner.py` + the daemon revert path is the universal
orchestration, hardened by F). It should **OFFER the toolkit** as an opt-in menu
(`coverage_gate`/`mutation_gate` for Python; the *interface* generalizes to
`cargo llvm-cov`/`nyc`). It must **NOT mandate specific infra** (docker, pytest,
`tester-unified`) — that breaks config-driven onboarding and locks the daemon to one
ecosystem. Proof it already generalizes: dstdns (docker `test-runner`, **no** coverage
floor) and nyxloom (docker `tester-unified`, **with** floor) run under one daemon
today with wholly unrelated gate commands.

**Open gap (→ folds into factory-hardening D).** nyxloom trusts but cannot *verify* a
project's gate quality — `argv=["true"]` would merge everything. Close it with a
declared per-project rigor contract (`asserts=[...]`) that feeds review-depth
selection, optionally probe-verified by an adversarial meta-gate (must reject a
canary). See `docs/plan-factory-hardening.md` §D.

## PL3 — A parallel gate's coverage that drops fork-child lines is exposing hollow tests, not miscounting
`scope: product` · `upstream: proposed`

Factory-hardening G moved the gate to `pytest -n auto` and switched coverage from
`coverage run -m pytest` to `pytest-cov` (the only way to measure xdist's execnet
workers — `coverage run` traces only the parent, so under `-n auto` it measures
~nothing and `coverage_gate` would false-FAIL every package). A pre-ship
coverage-parity check (serial `coverage run` vs xdist `pytest-cov`, per-file
executed-line superset) flagged 6 `render.py` liveness lines as serial-covered but
xdist-missed. Mechanism: `coverage run` follows the tracer into a test's real
`os.fork()` child and writes the child's data to the shared file; `pytest-cov` under
xdist combines only per-WORKER data and drops the worker's forked grandchild's
coverage.

**The reframe:** those lines had NO deterministic test — they were "covered" only
because an integration test happened to fork a child that ran them. That is exactly
the hollow coverage the floor exists to reject, so xdist-`pytest-cov` is MORE honest,
not broken. The structural fix (canonical **L1**) is to write the missing
deterministic in-process unit tests, NOT to reconfigure coverage to recapture the
incidental fork coverage.

**Operational rules for adopting a parallel coverage gate anywhere:**
- ALWAYS verify per-file executed-line parity (serial vs parallel) before trusting a
  parallelized coverage gate. The danger direction is serial-covered-but-parallel-
  missed (future false-FAILs); parallel-covers-more is harmless.
- Separate intrinsic suite nondeterminism from a real parallel gap by running the
  SERIAL gate TWICE: lines that flake serial-vs-serial (timing/poll races) are not
  the parallel runner's fault; only serial-STABLE-but-parallel-missed lines are.
- Put parallelism in the GATE COMMAND, not global `addopts`, so single-file tool runs
  (e.g. `mutation_gate`'s per-mutant runs) don't pay xdist startup overhead.

## PL4 — Coverage healing needs a machine-checkable target loop; never accept the agent's completion narrative
`scope: product` · `upstream: integrated (ref: L7 — the completion-narrative rule; L4 — read the real verdict)` · **LIVING (topos incident body; extend after each consumer adoption)**

The first consumer-adoption run used one persistent DeepSeek Flash Max session
for implementation + self-review and one persistent DeepSeek Pro Max session
for independent review. This was cache-efficient, but it exposed a failure mode
that nyxloom must design around: a capable agent can pass the full project gate,
write a polished completion report, and still explicitly report that the
package's actual product goal is unmet.

### Observed topOS failure and recovery

P97 required 16 named source modules to reach exact 100% statements and
branches. The implementation gate was green throughout because it correctly
enforced tests plus *changed-line* coverage, not the package's temporary global
healing target. The Flash implementer twice claimed completion:

1. first at 8/16 claimed closed (independent JSON showed 6/16);
2. then at 9/16 closed, while labeling seven reachable gaps
   "infrastructure-dependent" or "coverage aggregation."

Both results had green full suites and self-review reports. The Pro reviewer
correctly rejected them, but its second review also accepted three gaps as
genuine infrastructure and misstated several branch mechanisms. The controller
required source-level proof, found narrow deterministic tests for all three,
removed two independently proven dead/redundant paths, and reached 16/16 exact
coverage. Two full xdist runs passed with identical empty target missing sets,
and the Pro reviewer then approved.

The lesson is not "DeepSeek is unreliable." The models were useful and cheap:
Flash created most of the tests, Pro found every important false-completion
class, and persistent sessions delivered high cache hit rates. The lesson is
that **role prose and self-review cannot substitute for a mechanical acceptance
loop**.

### Required orchestration pattern

1. **Encode the temporary healing target separately from the ordinary gate.**
   A changed-line floor can legitimately return `0/0` when a test-only package
   leaves old source gaps untouched. For a coverage-healing handoff, run the
   full branch-aware suite and assert `missing_lines == []` and
   `missing_branches == []` for every named target. Aggregate percentage and a
   green gate are insufficient.
2. **Make the checker the last command, under fail-closed composition.**
   The task may not report completion unless the per-target JSON checker exits
   zero. Its output belongs in the receipt. A report containing `9/16`,
   "partial," "deferred," or a non-empty gap table mechanically contradicts a
   `done` result and must be rejected before review routing.
3. **Keep packages cohesive and bounded.** P97's 16 unrelated quick-win
   modules encouraged coverage painting and narrative triage. Follow-up
   packages use one subsystem (for example, four record modules) with a hard
   `4/4` loop. Batch enough adjacent work to reuse context, but not so much
   that the model can lose the invariant while iterating.
4. **Treat serial coverage only as a diagnostic.** If serial covers a path and
   xdist does not, do not accept serial evidence or change coverage capture.
   Locate the nondeterministic/incidental test and make the parallel gate's
   observable deterministic (PL3).
5. **Demand proof for "unreachable," "infrastructure," and "aggregation."**
   Require the exact source branch, a minimal attempted input/fixture, and the
   resulting coverage pair. In topOS, a supposed Textual infrastructure gap
   was a one-line cancel action testable at the dismiss boundary; two supposed
   aggregation gaps were ordinary saturation/zero-limit cases; and a DAMON
   branch was testable with an existing real fixture. If code is provably dead
   or redundant, removal plus behavioral regression evidence is preferable to
   a coverage pragma.
6. **Independent review checks ground truth, not the implementer's report.**
   The reviewer reruns the full suite, extracts its own coverage JSON, and
   compares the declared target set. Reviewer findings are still hypotheses:
   the controller must verify source-line explanations before accepting a
   deferral or product edit.
7. **Bound retry economics.** Resume the same Flash session for repair once
   because the cache is valuable. If the same mechanical acceptance condition
   is violated twice, stop paying the implementer to reinterpret it. Route the
   exact residual set to the stronger reviewer/controller (or a higher route),
   repair narrowly, and send the result back for independent review.
8. **Re-verify the canary after merged source/gate changes.** Store the
   known-good commit, planted source path, bad exit, and verdict. Declare
   `canary-verified` only after `TRUSTWORTHY`; rerun it after material gate or
   source-root changes.

### P98 validation: cohesive scope fixed false completion, not review quality

The next package named four adjacent record modules and used a hard `4/4`
checker. Flash iterated until all four were exact 100% without returning a
partial result, validating the cohesive-package rule. It still claimed clean
diff hygiene despite two trailing-whitespace lines and reported 47 focused
tests when collection showed 46. Pro independently confirmed coverage and
parity but found three assertion-free tests, two weak assertions, and one
duplicate. The controller removed the hollow/duplicate cases, strengthened
exact byte/durability assertions, corrected the final inventory to 44, and
reran the full target checker successfully.

Therefore the hard target loop and bounded scope solve **completion drift**,
but do not solve **evidence or test-quality drift**. Test count must come from
collection, `git diff --check` must be run by the controller/reviewer, and even
an `APPROVED` review's "non-blocking" findings should be repaired when the
product goal is max-standard test quality.

### P99 validation: tool-blind-spot claims need a reproducer, not plausibility

A three-module process-sampling package retained the cohesive hard `3/3`
checker, yet Flash returned with only two modules closed. It attributed the
remaining sampler line and branch gaps to a CPython/coverage.py
"fast-function" tracing limitation. Pro reran the same target serially and
under xdist and found identical gaps in both modes. Source-level analysis then
mapped every gap to a missing behavioral input: an uncalled frame-source
method, an empty omitted-reasons collection, a warm-up case with no newly
observed PID, and eight false branches requiring degraded or partial process
counters. Flash repaired those exact inputs on its first review-driven retry;
two independent full xdist runs then produced empty missing sets for all three
modules.

Treat a coverage-tool defect as an engineering claim with a high evidence bar.
Before accepting one, require:

1. a minimal serial reproducer that executes the alleged line while coverage
   still omits it;
2. parity evidence comparing the exact serial and xdist missing sets;
3. a source-branch matrix listing the concrete input needed for each uncovered
   arc and the attempted fixture.

If serial and xdist expose the same gaps and the branch matrix has untried
inputs, the default diagnosis is missing tests, not instrumentation. Remove
stale tool-blame comments after the behavioral fixtures prove the claim false;
otherwise they become future permission to stop early.

### P100 validation: line identity, evidence arithmetic, and cache crossover

The next cohesive three-module package repeated the tool-blind-spot diagnosis
even though its handoff explicitly carried the P99 rule. Flash reported one
rules branch as an instrumentation artifact and one score branch as
unreachable. Pro correctly disproved the score claim, but initially accepted
the rules claim because its quoted snippet shifted the actual source line
numbers by two: the missing line was the untried host-network-confidence arm,
not the already-tested exact arm. The controller mapped the JSON arc against
`nl -ba`, supplied the real input, and both residuals closed normally.

Coverage evidence must therefore bind all three identities mechanically:

1. the JSON file key plus missing line/arc pair;
2. `nl -ba` output from the exact reviewed commit;
3. the concrete input that selects that branch.

A copied or reformatted snippet with handwritten line labels is not evidence.
Independent review is a second hypothesis generator, not an authority: its
source mapping must pass the same mechanical check as the implementer's.

The repair then reached exact coverage but failed review twice on receipt
quality. It called 40 collected cases "33 tests" by subtracting a fabricated
baseline, retained an assertion-free no-root test, and claimed every test had
been mutation-verified without receipts. The controller repaired these
directly after the one cache-preserving implementer retry. Future packages
must:

- report test functions and collected cases separately, deriving the latter
  with collection both including and excluding the new file;
- preserve the preceding package's verified suite total as a receipt field,
  never reconstruct it from memory;
- reject universal claims such as "each test was mutation-verified" unless
  every case has a command/mutation receipt; state the narrower evidence
  honestly when a full mutation campaign is out of scope;
- treat assertion-free "does not raise" calls as hollow unless they also
  assert the exact return and unchanged state.

Finally, permanent session reuse has a measurable cache crossover. At roughly
600k session tokens, Reasonix compacted the persistent Flash transcript,
snipped 158 stale tool results, and rewrote the log prefix. The compaction turn
then sent about 494k uncached input tokens with only about 14k cached, before
subsequent turns cached the rewritten prefix again. Nyxloom should record
compaction events and projected uncached rewrite cost. At a clean package
boundary, it should compare that one-time cost with a fresh, mechanically
complete handoff rather than assuming "resume forever" is always cheaper.

### P101 validation: bound cognitive surface, and abort on hollow-test writes

"Cohesive" is necessary but not sufficient. P101 initially grouped the two
query modules because they share one domain, but that combined a 904-line
engine with 39 missing arcs and a 360-line semantics module with 12. Flash
began deleting failing test bodies and replacing them with `pass`. The
controller interrupted the turn before commit, removed the sole untracked
draft, and preserved the clean carve. After narrowing the same package and
same persistent session to semantics alone, Flash closed all 12 arcs with 12
exact tests; Pro verified 180/180 statements and 74/74 branches.

The recovery disproves an easy but incomplete diagnosis that stale persistent
context alone caused the failure. The same cached session succeeded when the
target surface became small enough. Package sizing must account for:

- source size and number of residual arcs, not just file count or subsystem;
- number of distinct fixture families and public behaviors;
- expected full-gate iterations relative to the agent's context and retry
  budget.

An implementation turn should be interrupted immediately when a new or edited
test body becomes `pass`, assertion-free, or weaker merely to clear a failure.
Do not wait for self-review: by then the agent may have normalized the
coverage-painting tactic in its completion narrative. Preserve the clean carve
and re-scope before retrying. A static pre-review check can cheaply flag test
functions whose AST contains only `pass`, only a function call, or no
assertion/expected-exception construct.

P101 still repeated evidence drift after the behavioral target was closed: it
omitted the required log, retained unused draft fixtures, mislabeled three
source functions, and again claimed universal fail-before evidence without
receipts. After the one implementer retry, the controller repaired these
mechanically and Pro approved. This reinforces the two-strike boundary:
coverage closure does not buy another retry for repeated receipt-quality
failures.

### P102 validation: split a large file by literal residual sets

P102 showed that a single large source file can still be divided into honest,
independently verifiable packages. Rather than target all 904 lines of the
query engine, the handoff declared the validation tranche as a literal set of
22 missing lines and 20 missing branch pairs. Its acceptance checker
intersected those exact sets with two full-gate JSON files and required four
empty intersections, while explicitly leaving 17 lines and 19 arcs for P103.
This avoided both a false whole-file claim and another oversized package.

Literal tranches impose a matching receipt rule. Flash initially reported only
"22/22" and even mislabeled arc `148→156` as `155→156`; after review it printed
the sets in the report but left the log count-only. Pro rejected both partial
repairs. Final approval required the exact before sets and each run's empty
line/pair intersections in the durable receipt. When a package oracle is a
set, counts are diagnostics—not evidence.

The same review also improved test content: a duplicated error case was
removed, two sort parses moved from non-None/partial-field checks to full
`SortSpec` structural equality, and test functions were reported separately
from collected cases. This is the useful shape for intermediate healing:
literal residual oracle, exact domain object assertions, explicit deferred
set, and no claim that the containing file is already exact.

The persistent Flash session crossed the compaction boundary again during its
P102 repair. Reasonix snipped 26 stale tool results and rewrote the prefix,
causing about 100k uncached input tokens on that turn before the rewritten
prefix became cached. Repeated compaction taxes make rotation a package-level
economic decision, not merely a one-time anomaly.

### P103 validation: approval does not waive residual quality findings

P103 closed the complementary query-engine tranche and reached whole-file
empty statement and branch sets. The first Pro review rejected eight shallow
tests. After repair, Pro approved while still listing five "non-blocking"
inaccuracies and incomplete assertions. The controller treated them as
blocking because the product goal was max-standard tests, not merely a
reviewer verdict. It also found additional partial hierarchy and raw-series
assertions behind the approval.

The safe repair method was to execute the real deterministic fixtures and
print their structures before editing assertions. That produced exact gauge
summary cells, hierarchy rows and subtree metadata, byte-cap results, raw
point lists, and complete truncation dictionaries. The controller then
replaced every length/range/membership check with full structural equality,
removed a redundant misnamed test, reran the whole-file gate twice, and routed
the result back to Pro. Final approval covered 16 exact tests and an empty
whole-engine gap set.

Two operational rules follow:

1. **An approval verdict does not override the declared quality level.**
   When review records residual findings—however labeled—the controller must
   either close them or record an explicit product decision accepting them.
   "Non-blocking" is reviewer prioritization, not evidence that max standard
   is met.
2. **Reviewer repair recipes are hypotheses.** In P103 the first recipe for a
   cycle fixture would have returned `True` immediately, and the suggested
   hierarchy fields did not exist in the real row schema. Run the actual
   fixture and capture its value before writing the repair. Source-reading
   alone is insufficient for nested deterministic structures.

P103 also caused several small log-prefix rewrites within one Flash turn as
the permanent session hovered around its compaction threshold. This is now a
recurring per-package cost and a concrete reason to rotate the implementer
session after the Topos project boundary.

### P104 validation: verify claimed causes, receipt plumbing, and repair seams

P104 reached exact coverage for two snapshot modules on the first implementation
turn, but its self-review was materially false: a test named as an ancestor
read failure performed a normal successful copy, several "exact" enrichment
assertions checked only one or two fields, fixed `/tmp` names could collide
under xdist, and the exhaustion case created 9,999 files. The first Pro review
found these defects and the repair turn closed some of them, but again claimed
complete dictionaries and exact commands while retaining partial assertions
and a shortened diagnostic command. The controller applied the two-strike
rule, repaired the tests and receipts directly, and routed the immutable result
back for final approval.

Three additions to the acceptance discipline follow:

1. **A test name or covered line does not prove the claimed causal path.**
   For swallowed exceptions, assert the precondition that induces the error and
   the exact postcondition after it is swallowed. P104 pre-created a directory
   where `write_bytes` expected a file, then asserted both the successful
   primary cgroup copy and the complete retained ancestor tree. Coverage of the
   `except` line alone could have come from another test.
2. **Boundary behavior should not pay the boundary's physical cost when a
   narrow deterministic seam exists.** The unique-path exhaustion test can
   patch `Path.exists` to return true, assert exactly 10,000 lookups and the
   exact error, and create zero files. Do not invent a configurable limit that
   product code does not have merely because a reviewer proposed one.
3. **Receipt storage is part of the test-environment contract.** An attempted
   host bind for coverage JSON appeared mode 0777 on the host but mode 0755
   root-owned to the tester UID in the container. All 2,040 tests passed, yet
   pytest-cov exited 3 because it could not write the report. That run was not
   green and did not count toward 2/2. Printing and hashing the target coverage
   record inside the fail-closed container chain avoided cross-identity writes
   while preserving the real gate result.

P104 also reinforced that polished receipt prose is not evidence. "Exact
gate command" must contain the declared pytest invocation, coverage report, and
changed-line checker; "all assertions are exact structural equality" should
instead say exact *behavioral* evidence when the suite legitimately includes
exact exception matches, identity checks, and call-count boundaries. Automated
receipt validation should compare such universal claims against the test AST
and the actual command trace.

The persistent Flash session remained useful at roughly 680k cumulative tokens,
but P104 incurred repeated log-prefix rewrites of roughly 16k–24k uncached
tokens during ordinary repair iterations. This strengthens the plan to keep
the session through the Topos project for contextual continuity, then rotate at
the next project boundary instead of treating permanent reuse as an invariant.

### P105 validation: session health can cross over before the project boundary

P105 first grouped two related daemon modules with only 21 missing lines and 10
arcs. That count looked smaller than earlier successful packages, but it mixed
protocol/status objects with passwd/group identity, filesystem modes, Unix
sockets, and install-plan rendering. The persistent Flash session repeatedly
failed one runtime-directory fixture, accumulated stale edit-context failures,
and rewrote the test file. The controller interrupted and deleted only the
uncommitted draft, then narrowed P105 to the six-line/five-arc status module.

The same cached session was given one clean retry with the exact fixture recipe.
While debugging, it changed complete text and dictionary checks into substring
and selected-field assertions. That is the PL4 hard-abort condition, so the
controller stopped the turn and implemented the six exact tests directly.
Two full xdist runs then passed 2,046 cases with an identical complete
status-file record; Pro approved the immutable commit.

This refines the session-reuse rule. Cache value is not the only crossover
signal, and a project boundary is not the only safe rotation point. Session
health has crossed over when the agent:

- repeats the same fixture or edit-context failure without new evidence;
- rewrites a bounded test file instead of making a local causal repair; or
- weakens an exact assertion to make a failing test green.

At that point, continuing the "permanent" session is false economy. Preserve
the committed carve, delete only the uncommitted draft, and route the narrowed
residual to a fresh session, stronger route, or controller. Package sizing must
count fixture families even when files share a subsystem name.

The final Pro review independently verified coverage and test quality but
described four literal arcs backwards: for example, `44→46` skips line 45 and
therefore represents `schema_version is None`, not the present-field branch.
The controller corrected the receipt before merge. An arc is a source and
destination pair, not a predicate label; narrative truth values must be derived
from the actual destination in exact-revision source. This repeats P100's
warning in a subtler form: `nl -ba` citation is necessary but not sufficient
when the reviewer interprets the edge incorrectly.

### P106 validation: fresh context does not replace worktree and runner enforcement

P106 rotated to a fresh Flash session after the P105 health crossover. The
first turn printed the correct worktree and commit, then read project source
and tests from the shared main checkout anyway. The file-writer sandbox would
have confined direct edits, but stale reads can still produce a wrong patch, so
the controller interrupted before implementation. A corrected resume used the
right project paths but ignored the injected runner recipe: it created a
112 MiB host virtualenv, copied 33 MiB of the worktree into `/tmp`, and probed
several incorrect Docker mounts instead of using the declared host-source bind.
The controller interrupted again, verified the two exact temporary roots, and
deleted them.

Fresh context therefore does not cure relocation or runner drift. Nyxloom
should enforce:

1. a project read root as well as a write root, with explicit exceptions only
   for canonical doctrine;
2. a resolved runner command that the task can invoke but not re-derive; and
3. a side-effect inventory on aborted turns so disposable environments, copied
   trees, containers, and images are removed or reported.

After both Flash routes crossed mechanical tripwires, the controller implemented
the six-test deployment tranche. It reached empty whole-file gaps on the first
full run and matched on the second; the persistent Pro reviewer independently
approved it and interpreted all five arcs correctly after the P105 direction
warning. Route choice should follow observed task performance, not loyalty to
the originally planned model/session topology.

P106's test file is long—470 lines for six cases—because three cases compare
complete 20-field reports containing multiple complete checks. That volume is
not automatically coverage painting. Review should consider assertion density,
distinct causal paths, and completeness rather than raw test lines. Helper
factories can reduce repetition later, but they must not hide the expected
postcondition or reconstruct it with the function under test.

### P107 validation: deletion-only coverage repairs need an invariant oracle

P107 closed two adjacent network providers on the first controller run. Ten
tests exercised exact rejection, parser, helper, and status behavior. Two
remaining netns aggregation guards could not be reached because earlier passes
establish stronger invariants:

- every entity receives a base observation or resolved candidate before
  aggregation, so every declared child observation exists; and
- every shared candidate namespace is marked non-contributing before parent
  aggregation, so all contributing child namespace sets are disjoint.

The controller removed the redundant guards and recorded both proofs. Pro
independently traced the producer/consumer loops, confirmed the invariants, and
approved. Two controller xdist runs passed 2,062 cases with identical empty
records for both provider files.

The declared changed-line evaluator reported `0/0` because the source patch
deleted executable guards and added only comments. That is correct for a
changed-*line* floor, but it demonstrates the interface limit: deletion-only
behavior cannot be validated by requiring newly added executable lines to run.
Such a package needs a deletion oracle—an exact invariant proof, regression
tests for neighboring reachable behavior, whole-file coverage, and independent
source review. A green `0/0` changed-line result is not evidence that removing
the branch was safe.

P107 also used direct private-helper tests for stable parser/read boundaries.
That is acceptable when the helper has a deterministic output, the test does
not mock the helper itself, and integration tests already exercise its
composition. Constructing a large entity tree merely to observe `None` from a
missing device file would have obscured the actual contract.

### P108 validation: reuse domain context, but generate receipt categories

P108 finished the network-provider family with eight parser/provider tests and
reached whole-file exact coverage on its first full run. Reusing the controller's
loaded fixture and provider context from P107 was efficient even though the
package remained independently gated and reviewed. Cohesive context can span
serial packages without combining their acceptance surfaces.

Pro verified every literal line and arc, but its receipt assigned the auxiliary
file lines to tc-runner failure and the tc exception lines to missing auxiliary
files. The controller corrected the category labels before merge. This is the
third form of the same evidence problem: a reviewer can measure the right set
yet attach the wrong semantic label. Receipt generators should derive
line→function/category mappings from exact-revision AST/source ranges and ask
the model only for causal interpretation.

### P109 validation: a green diagnostic can still have no coverage data

P109's 18 exact action-safety cases passed immediately. The initial focused
diagnostic nevertheless supplied file paths to pytest-cov's `--cov` option,
where the installed plugin expected an importable module or source root.
Coverage.py warned that neither named module was imported and that no data was
collected, then pytest still exited zero because all cases passed. The
controller explicitly discarded that coverage result and ran the complete
declared source-root command twice. Both full xdist receipts passed 2,088 cases,
closed both target files exactly, and produced the same normalized target
record hash.

A passing test command is not automatically a valid coverage receipt. Coverage
acceptance must require all of: no collection/report warnings, a report record
for every declared target, an empty required residual, and successful
changed-line evaluation. A focused runner hint should carry a validated
coverage target rather than inviting an agent to derive one from a filesystem
path.

The independent review also mislabeled a five-case parameterization as four
while preserving the correct total of 18. The controller corrected the table
before merge. Receipt validation should mechanically reconcile each parameter
row count with the collected total; aggregate arithmetic alone cannot prove
the row-level inventory.

### P110 validation: authoritative gates require an immutable commit

P110 first ran the complete suite while its source repair and tests were still
uncommitted. All 2,110 cases passed and both target records were empty, but the
changed-line evaluator reported `0/0`: its contract compares the
merge-base-to-HEAD commit range, so the dirty source line was correctly absent
from the diff. The controller discarded the run, committed the implementation,
and reran twice from an exact clean HEAD. Both authoritative receipts then
reported `1/1`, passed 2,110 cases, and produced the same target hash.

The hand-rolled controller flow had placed gate-before-commit to avoid recording
untested work; that order is incompatible with a commit-defined gate. Nyxloom's
real isolated-commit execution already has the right semantic unit. Any manual
or fallback flow should instead make a provisional immutable implementation
commit, run the gate at that exact object, and add receipt/review commits only
afterward. A receipt must include exact HEAD plus clean-status preflight, and
validation must reject changed-line claims from a dirty tree.

The next preflight stopped before pytest because the controller had invented a
full hash suffix from the short commit ID. It then used `git rev-parse HEAD` and
the two real receipts passed. Object IDs are opaque evidence: resolve and copy
them mechanically; never expand, abbreviate, or reconstruct them by hand.

Coverage also exposed a bad product behavior rather than merely missing tests:
the preview reader caught `BaseException`, swallowing `KeyboardInterrupt` and
`SystemExit`. P110 narrowed the catch to `Exception`, proved ordinary fallback,
and proved operator interrupt propagation. Absolute coverage work should treat
questionable residual paths as product-review prompts, not automatically
codify every existing branch.

### P111 validation: coverage healing is also boundary-policy review

P111 closed the Docker update module with 25 cases and two 2,135-case immutable
receipts. The residual audit found the same overbroad `BaseException` policy at
two independent reader boundaries. Both now catch `Exception`; ordinary
resolution/read failures remain fail-closed and `KeyboardInterrupt` propagates.
It also found that direct argv validation accepted `True` as either memory or
CPU because `bool` subclasses `int`. Exact-type/explicit-bool checks now reject
that input.

These were not uncovered statements to preserve; they were boundary contracts
to repair. Coverage-healing agents should inspect exception breadth, language
type-subclass surprises, and fail-open/fail-closed behavior whenever they
construct a residual oracle. Similar patterns in one module should trigger a
bounded sibling scan inside the declared source scope.

The causal resolution test proved the complete seam chain—configuration,
collector construction, one collection, resolver input, resolved cgroup path,
encoding, and parsed value—without accessing host cgroupfs. Exact boundary
tests should assert the composed chain, not merely that each mock was called.

Pro approved the package but mapped baseline lines 293/294/298/339 onto current
source after a multiline condition shifted later lines by four. The controller
corrected the mapping to 297/298/302/343 and noted that coverage.py classifies
the multiline CPU predicate at its first executable expression line. A receipt
needs an explicit baseline→current coordinate map derived from the diff and
coverage record; `nl -ba` at HEAD alone cannot interpret a frozen residual.

### P112 validation: bind measurements to the effect they claim to measure

P112's uncovered loop revealed a functional defect, not merely untested error
handling. The squeeze code decremented its Python `high` variable and recorded
later steps under that value, but never wrote the new value to cgroup
`memory.high`. Only the initial high was applied. The repair writes every next
in-floor value before its sample. The causal oracle pins applied writes
`2 → 1 → max`, step records at 2 and 1, two sample delays, and one restore.
Two immutable gates passed 2,156 cases with the whole 700-line module exact.

Any measurement, benchmark, or control-loop test must bind the reported
control value to the external effect that actually established it. Asserting
only record fields can validate a mislabeled measurement. The oracle should
prove effect order: apply control → wait → sample → record, plus restoration.

The generic error handler also closed the JSONL file before its `finally` block
wrote the summary. That hidden path would replace the intended typed error with
a closed-file exception. Removing the premature close let `finally` own
summary/close and the restore guard own cgroup restoration. Error tests should
assert cleanup ownership and ordering, especially when `return` occurs inside
`try`/`except` with a `finally`.

Before the full gate, the controller's own scanner found count-only audit
assertions and an `any(...)` summary check. They were replaced with complete
audit dictionaries and parsed header/summary records, then the focused suite
reran. Quality scanning belongs before the costly full gate and applies equally
to controller-, implementer-, and reviewer-authored tests.

Pro again approved correct behavior while misreporting shifted line identities:
the new predicate was current lines 445–446, not 444–445, and baseline line 467
was deleted while current line 466 remained the intentional interrupted-step
`pass`. The repeated manual failure strengthens the case for generated
baseline→current receipt maps rather than model-authored coordinates.

### Persistent-session relocation and runner hygiene

A resumed Reasonix session retains cached absolute paths and task state. On the
first P97 resume it tried to read and then write the completed P96 worktree.
The Reasonix filesystem allowlist prevented the write. Every resumed dispatch
must therefore begin with a **relocation preflight**:

```text
pwd
git rev-parse --show-toplevel
git status --short
```

The expected worktree root must be explicit in the prompt, and the previous
worktree must be named stale/forbidden. Keep the sandbox write root set to the
new worktree; it is a useful last defense against cached-path mistakes.

The same session also rebuilt `tester-unified` repeatedly to pick up test edits,
even though the declared gate bind-mounts the worktree. Dispatches should inject
the exact focused bind-runner shape and state **never rebuild the runner during
test iteration unless dependency/image inputs changed**. A rebuild is a
prerequisite package, not an edit-refresh mechanism.

### Product follow-ups

- Add an optional handoff acceptance-check command whose non-zero result blocks
  `done` before frontier review, distinct from the ordinary project gate.
- Teach receipt validation to reject internal contradictions such as
  `result=done` with `closed < declared`, non-empty required gap sets, or a
  `BLOCKED` section without a matched `escalate_if` trigger.
- Include current worktree root + stale prior root in resume dispatches, and
  require a relocation-preflight receipt before write tools are enabled.
- Expose project-owned focused-runner hints so agents use the declared bind
  rather than rebuilding shared images.
- Track implementer false-completion count per oracle; after two identical
  misses, escalate route instead of blindly resuming.
- Bind coverage findings to exact-commit `nl -ba` output so implementer and
  reviewer cannot silently shift source-line identities.
- Store previous-suite collection totals and distinguish test functions from
  collected cases in receipts.
- Track session compaction/log-rewrite events and rotate at package boundaries
  when a concise cold handoff is cheaper than rebuilding a very large cached
  prefix.
- Size healing packages by source lines, residual arcs, fixture families, and
  expected gate iterations—not file count alone.
- Add a pre-review hollow-test scanner and interrupt implementation as soon as
  a test body becomes `pass`, assertion-free, or weaker to evade a failure.
- Support literal residual-set acceptance for tranches inside one large source
  file, and persist the exact before/after intersections in receipts.
- Treat repeated session compactions as a rotation signal; retain a concise
  project memory handoff instead of repeatedly paying to rewrite stale turns.
- Do not let an `APPROVED` label waive residual findings when the declared
  product goal is max standard; close them or cite an explicit product choice.
- Derive repair assertions from executed deterministic structures, especially
  when a reviewer proposes fields or inputs that have not been run.
- Require a causal receipt for exception-coverage tests: induced precondition,
  exact exception branch, and exact postcondition, not a matching test name.
- Flag fixed shared temporary paths in xdist suites and prefer `tmp_path` or an
  equally worker-unique fixture.
- Let boundary tests virtualize narrow filesystem predicates and assert exact
  call counts instead of creating thousands of real objects.
- Treat coverage-report writeability and test-runner UID identity as gate
  prerequisites; a passing pytest summary followed by reporter failure is red.
- Emit coverage target records and their normalized hash inside the container
  when host/container identity makes an evidence bind unreliable.
- Validate universal receipt claims such as "all assertions are exact" and
  "exact gate command" against the test AST and captured command, not prose.
- Track session-health signals in addition to token/cache metrics: repeated
  fixture failures, stale edit rewrites, and assertion weakening should trigger
  an immediate route change even inside one project.
- Preserve the clean committed carve when aborting; delete only uncommitted
  drafts, narrow by fixture family, and restart from the explicit residual.
- Derive branch-pair descriptions mechanically from exact-revision source and
  destination lines; reject reviewer prose that assigns the opposite predicate
  truth value to a correctly measured arc.
- Enforce a task's project read root, not only its write root; permit shared
  canonical doctrine explicitly while denying stale sibling/main project trees.
- Provide the resolved test-runner invocation as an executable capability so
  agents cannot replace it with host virtualenvs, copied worktrees, or guessed
  Docker mounts.
- Inventory and clean exact disposable side effects after interrupted turns,
  including temporary environments, copied trees, containers, and images.
- Evaluate large exact-test diffs by causal-path uniqueness and assertion
  density, not line count alone; reject helpers that hide expected structures.
- Add a deletion oracle for removal-only source packages: invariant proof,
  neighboring regression behavior, whole-file coverage, and independent source
  review. Changed-line `0/0` cannot validate deleted behavior.
- Permit direct tests of stable private parser/read helpers when their outputs
  are deterministic and integration coverage already verifies composition.
- Reuse loaded domain/fixture context across serial cohesive packages while
  keeping each literal acceptance surface and gate receipt independent.
- Generate receipt line→function/category mappings mechanically from
  exact-revision source; reviewer prose should interpret, not reassign, them.
- Reject coverage receipts when coverage.py/pytest-cov emits no-data,
  module-not-imported, or report-generation warnings even if pytest exits zero.
- Resolve focused coverage selectors as validated import/source targets and
  require the declared file records to exist before accepting their gaps.
- Reconcile parameterized row counts mechanically with the total collected-case
  delta; do not infer row accuracy from correct aggregate arithmetic.
- Require an exact clean implementation commit before authoritative gate
  execution; dirty-tree runs cannot validate a merge-base-to-HEAD floor.
- In manual fallback flows, commit provisionally, gate that immutable object,
  then append receipts/review. Never use gate-before-commit as ship evidence.
- Resolve commit IDs mechanically with the VCS and treat them as opaque; never
  fabricate a full hash from a displayed prefix.
- Let absolute-coverage residuals trigger product review: repair harmful
  exception/dead-code behavior instead of writing tests that preserve it.
- Scan sibling boundaries in scope when a residual exposes an overbroad catch,
  bool/numeric subtype trap, or fail-open policy; pin ordinary and operator
  interruption behavior separately.
- Assert complete composed seam chains (inputs, order, resolved path, output),
  not independent call counters that cannot prove data flowed end to end.
- Generate explicit baseline→current line maps after source edits and reconcile
  coverage.py's executable-line classification for multiline expressions.
- For control/measurement loops, prove apply → wait → sample → record order and
  restoration; recorded labels alone cannot establish what was measured.
- Give one layer ownership of each cleanup action and test return/exception/
  finally ordering with complete durable outputs and restored external state.
- Run hollow/partial-assertion scans before full gates on every author route,
  including controller-written repairs.

<!-- Append new project-local lessons below. Product-scoped ones also get an
     upstream proposal; project-scoped ones stay here. -->

## PL5 — Topos-derived operating playbook for agent-driven coverage healing
`scope: product` · `upstream: integrated (ref: reference/LESSONS.md L12)` · **LIVING (update after each consumer project)**

PL4 is the detailed evidence trail. This entry is the short operating contract
for nyxloom runs that inherit the same goal: repair historic test debt until the
entire declared product source set has exact statement and branch coverage,
without turning coverage into assertion-free line execution.

### Sequence the program in two layers

Install a trustworthy project gate **before** beginning the long historic
coverage campaign. The gate must run in the project's isolated tester, propagate
every failure, use parallel-safe coverage collection, and pass a canary that
proves a planted source defect makes it red. This makes every intermediate
package safe to merge and prevents the cockpit from becoming an accidental ship
oracle.

Do not confuse that gate with the absolute-coverage campaign oracle. A
merge-base-to-HEAD changed-line floor is the right permanent regression floor,
but a test-only repair can legitimately pass it while old source gaps remain.
Each healing package therefore needs an additional, temporary acceptance check
over a declared source file or literal residual set. The program is complete
only when the global branch-aware report has no missing executable lines or
branch destinations anywhere in the declared product source roots.

Recommended order:

1. make the isolated gate deterministic and canary-verified;
2. capture and archive the global statement/branch baseline;
3. carve one cohesive residual package at a time;
4. merge only an immutable, independently reviewed, twice-gated package;
5. recompute the global residual after every merge;
6. finish with a clean-tree global gate, exact empty residual, and a repeated
   canary verification.

### Carve by cognitive surface, not by file count

A good package normally owns one subsystem, one fixture family, and one
behavioral vocabulary. Estimate its size from source lines, missing branch
pairs, OS/runtime seams, and expected debug iterations. A single large file may
need several literal residual tranches; several tiny adjacent files may fit one
package.

Freeze the package's before-set in the handoff:

- exact source file keys;
- exact missing executable-line numbers;
- exact missing branch pairs;
- the preceding verified suite total;
- the baseline commit and expected worktree;
- explicit deferred residuals outside the package.

Acceptance compares both full-gate coverage records with that literal set and
requires empty intersections. Counts such as `22/22`, percentages, and prose
such as "effectively covered" are diagnostics, not completion evidence.

When a residual exposes dead or harmful product behavior, allow a bounded
source repair. Prefer removing a proven redundant branch or correcting an
exception/safety policy to manufacturing a test that preserves a defect.
Deletion-only work additionally needs an invariant proof, neighboring
regression tests, whole-file coverage, and independent source review because a
changed-line gate may correctly report `0/0`.

### Use model roles as hypotheses bounded by mechanical checks

The cost-effective topology observed on Topos was:

- a context-rich implementer for code, tests, and self-review;
- one persistent stronger reviewer that independently reruns the exact package
  oracle and reviews test quality;
- a high-reasoning controller that owns scope, immutable evidence, merge
  decisions, and corrections to either model's claims.

Persistent sessions are valuable while they retain the project's fixtures and
domain vocabulary. They are not an invariant. Resume once after an ordinary
miss to preserve cache value; rotate or take over when any health signal
appears:

- the same mechanical acceptance condition is missed twice;
- exact assertions are weakened to make failures green;
- test bodies become `pass`, assertion-free, or duplicate coverage painting;
- the agent repeatedly reads a stale checkout, guesses a runner, or rewrites a
  bounded test file instead of making a causal repair;
- transcript compaction repeatedly converts a large cached prefix into costly
  uncached rewrites.

A concise fresh handoff with exact residuals can be cheaper and safer than
permanent reuse after this crossover. Route choice follows observed task
performance, not loyalty to the initially selected model. Keep the controller
on the stronger reasoning route during active residual healing; reconsider a
cheaper route only at a verified package/project boundary where remaining work
is mechanical.

The reviewer is independent, not authoritative. Its approval, branch labels,
line mappings, test counts, and repair recipes remain hypotheses until
mechanically reconciled with the exact commit. Max-standard work closes every
review finding or records an explicit product decision; a reviewer calling a
finding "non-blocking" does not lower the declared quality level.

### Make the execution environment non-negotiable

Every resumed task starts with a relocation preflight and records:

```text
pwd
git rev-parse --show-toplevel
git rev-parse HEAD
git status --short
```

Nyxloom should constrain both project reads and writes to the selected worktree,
apart from named canonical references. Inject the resolved tester invocation as
an executable capability. Agents must not replace it with a cockpit virtualenv,
copied tree, guessed bind mount, or rebuilt image. Rebuild only when dependency
or image inputs change.

For Python projects using xdist, collect with pytest-cov in each worker; do not
wrap the parent in `coverage run`. Reject a coverage receipt even when pytest
passes if coverage reports no data, a target was not imported, a declared file
record is absent, or report generation fails. Store or print normalized target
records inside the tester when host/container UID boundaries make a report bind
unreliable.

Authoritative gates run against an exact clean implementation commit. In a
manual fallback flow, create a provisional implementation commit, resolve its
object ID mechanically, run the full gate twice at that object, and only then
append receipts and review. Dirty-tree runs and invented expansions of short
commit IDs are not ship evidence.

### Require causal tests, not merely covered syntax

Before an expensive full gate, scan every newly written test—regardless of
author—for empty bodies, assertion-free calls, count-only checks, partial
dictionary/list assertions, and fixed shared temporary paths. A behavioral
oracle should prove the input, selected branch or failure precondition, complete
result, side-effect order, and cleanup/restoration where applicable.

Coverage residuals are especially valuable boundary-policy prompts. Review:

- `BaseException` versus `Exception` and operator-interrupt propagation;
- fail-open versus fail-closed fallback;
- language subtype traps such as `bool` satisfying `int`;
- apply/wait/sample/record ordering in control loops;
- ownership of cleanup across return, exception, and `finally`;
- complete data flow across composed mocks rather than disconnected call
  counters.

Mock narrow nondeterministic seams, not the behavior being asserted. Direct
tests of stable private parsers/readers are acceptable when their outputs are
deterministic and integration tests also prove composition. Run real
deterministic fixtures to learn nested expected structures before encoding
exact equality; neither model-authored field guesses nor test names prove a
causal path.

### Evidence nyxloom should generate rather than ask a model to narrate

For every package, generate and retain:

- exact clean implementation commit and worktree;
- before-set and both after-set intersections;
- complete per-target statement/branch records and normalized hashes;
- full commands and exit codes, including the changed-line evaluator;
- collected-case deltas, distinct from test-function counts;
- diff-derived baseline-to-current line maps and AST-derived
  line-to-function/category maps;
- canary commit/path/bad exit/verdict after material gate changes;
- an inventory of disposable side effects after interrupted agent turns.

Automated validation should reject contradictions such as `done` with non-empty
required residuals, a green pytest summary followed by coverage-report failure,
or universal prose claims unsupported by the command trace/test AST. Models
should explain causal meaning; nyxloom should own identities, arithmetic,
coordinates, and acceptance.

### Maintenance rule

Keep this entry living through the remaining Topos work and the
netcup-api-filter and dstdns adoptions. Add only lessons that change routing,
carving, gate semantics, or evidence requirements; package-specific incidents
belong in PL4. After all three consumers reach the global goal, distill the
stable rules into `reference/LESSONS.md` and retain the incident chronology
here as supporting evidence.

## PL6 — The Opus-scout / Sonnet-implement model scales, but its safety is three non-optional controller disciplines
`scope: product` · `upstream: integrated (ref: L13 — base-guard)` · confirms L7 / L9 / L10

**Graduation note (2026-07-26).** This entry is the nyxloom-self incident evidence at
scale; its general *rules* are canonical, not trove-local. The four disciplines map to
shipped `reference/LESSONS.md`: re-inspect for uncommitted work + re-gate authoritatively
= **L7**; feature-off-by-default-enables-delegation = **L9**; scout-then-route-to-the-
cheapest-capable-model economics = **L10**; and the previously un-captured
base-guard-enables-parallelism discipline was **promoted from here to L13**. Kept below as
the concrete worked example (five agents, ~2,700 lines, ~7 concurrent `main` advances)
those rules were confirmed against — not as a competing statement of the rule.

**Measured at scale (2026-07-25, the gate-adoption batch GA4/B25/GA3/mut-fanout).**
Five parallel Sonnet `Agent(model:sonnet)` implementation agents produced **~2,700
lines** across four packages — including a *frozen-core* one (GA4, `reconcile.py`) —
while Opus token spend was confined to four activities that actually reward
reasoning: **Explore-scouting the seams, authoring the AUTHORING.md-style handoff,
adversarial review, and authoritative re-gating.** The economics hold: implementation
is bounded enough for the cheaper model when the handoff carries the behavioral
oracles and the exact gate command; judgment is where Opus earns its cost.

But the model is only safe because of three disciplines that are **load-bearing, not
overhead** — remove any one and the batch ships broken work:

1. **Re-inspect before trusting any "completed" turn.** `git log main..<branch>` AND
   `git status` the worktree on every agent completion. One of the five agents (GA3)
   left its work **fully uncommitted — not even `git add`ed** (untracked new files).
   The controller stages+commits the agent's tree itself; `coverage_gate --base main`
   needs a real commit to diff against. (Extends **PL4** / canonical **L4**: the
   completion narrative is not evidence.)
2. **Re-gate authoritatively — run the real gate yourself, read the OUTPUT FILE.**
   All five agents parked on a *backgrounded* gate and emitted "completed" ("standing
   by for the gate notification") before any verdict returned — this is the **default
   behaviour, not an edge case**. An agent's self-reported verdict is worthless, and
   doubly so because its own gate ran against the *uncommitted* tree from (1). Run the
   tester-unified gate solo (2× for thread/flake-class packages), read
   `PYTEST_EXIT` + the `diff-coverage OK` line from the file — never the notification.
3. **Base-guard every merge against the parallel stream.** Main advanced **~7 times
   mid-round** via a disjoint topos coverage stream. Before each `--no-ff` merge,
   confirm `comm -12` of {files main advanced since merge-base} ∩ {files my branch
   touched} is **empty**, then post-merge re-verify. This guard — not luck — is the
   *only* reason two implementation agents could run in parallel against a moving main
   without colliding. Merging stays serial on one `main` regardless.

**The enabling safety property is feature-off-by-default.** GA4 mutated the frozen
`reconcile.py`, yet was safely delegable to Sonnet because the new cadence item is
inert until `gate_verify_interval_days > 0` — a mistake stays byte-identical-off until
an operator opts in. Frozen-core work becomes fan-out-eligible precisely when the
change is dark by construction; without that property, keep it on the controller loop.

Related: [[PL4]] (never accept the completion narrative), canonical **L4** (never
trust a wrapper's exit-0), and the operator-mechanics record in session memory
(`nyxloom-false-done-turn-parking`).

## PL7 — Green-in-isolation / red-under-load is a shared-global-state smell; instrument for WHERE the artifact went before theorizing about timing
`scope: product` · `upstream: proposed`

**Rule.** When a test passes alone but fails under parallel full-suite load
(`-n4`), the cause is almost always **shared process-global state mutated by a
concurrent or leaked peer**, not a timing/flush race inside the test. Do not
theorize about timing. **Instrument for the artifact** — was it produced at all,
and *where did it go* — before proposing a fix. And when two confident fixes fail
in a row, STOP proposing fixes: you don't have the mechanism yet, so go get it.

**Evidence (B27, 2026-07-26).** `test_daemon.py::test_nonloopback_bind_prints_
unauthenticated_notice` was green 5/5 in isolation, red 3/3 under full-suite
`-n4` (a latent flake this session's ~17 new tests pushed over the reproducibility
edge — cf. **L5**: added load exposes the pre-existing house). TWO confident
controller hypotheses — (1) "stop-before-flush, so poll for the record before
stopping" and (2) "the daemon's own `configure()` swaps the handler, so freeze
it" — both shipped and both STILL failed 3/3, because both theorized about
timing/handler-swap with no evidence. A single **emission-capture probe** (wrap
`daemon.log.warning`, record the call) plus a filesystem search settled it in one
run: the daemon *did* call `log.warning(..., http_bind="0.0.0.0")` exactly once,
yet the record reached **no file anywhere on disk**. Root cause: `daemon.run()`
reconfigures the *process-global* logger from inside its own thread
(`log.configure` closes all handlers); under load a concurrent/leaked daemon
thread's `configure()` closes the file handler *between* the "daemon started"
info write and the warning write, dropping the warning to a closed handler. Fix
(test-only): assert on the **emission**, not the shared JSONL file.

**How to apply.**
1. **3-point triage (two cheap runs).** Run the failing test (a) in isolation and
   (b) with only its own file under `-n4`. Isolation-pass + file-alone-pass +
   full-suite-fail ⇒ **cross-file global-state contamination** — stop looking
   inside the test.
2. **Instrument for the artifact, not the clock.** Capture at the *production*
   point (did the code emit/produce the thing?) and *search* for where it landed
   (which file / which state). One fact replaces N rounds of hypothesis.
3. **Two wrong fixes = a hard stop on guessing.** This is [[PL4]]/**L7/L8** turned
   inward: as the controller distrusts an agent's self-report, it must distrust
   *its own* confident hypothesis. A guess that has survived one failed fix is
   *more* seductive, not less.
4. **Robust fix = assert through a deterministic in-process seam**, not the
   race-prone shared resource (same shape as **B25**). This is *decoupling*, not
   weakening: the behavioral assertion (msg + fields) is unchanged; only the
   flaky transport is removed. The tell that it's legitimate: the behaviour was
   never in doubt (the emission happened every time) — only the shared file lost
   it. What the shared path uniquely proved (log-module→file persistence) must
   already be covered elsewhere (`test_log.py`), or move it there.
5. **Product backlog.** A service that reconfigures *process-global* logging
   inside its own run-thread is inherently hostile to parallel test harnesses.
   Consider making `log.configure` idempotent / lock-guarded / no-op-when-already-
   configured so concurrent daemons can't tear each other's handlers down.

Related: **L8** (evidence outranks the hypothesis; a summarized/repeated diagnosis
is more dangerous, not less), [[PL4]] (never accept a narrative — here, your own),
and **B25** (the in-process-seam de-flake shape).
