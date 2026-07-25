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
`scope: product` · `upstream: proposed` · **LIVING (extend after each consumer adoption)**

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

<!-- Append new project-local lessons below. Product-scoped ones also get an
     upstream proposal; project-scoped ones stay here. -->
