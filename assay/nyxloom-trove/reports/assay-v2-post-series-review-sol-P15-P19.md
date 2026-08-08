# Assay v2 post-series review — P15 through P19

**Review date:** 2026-08-08  
**Reviewed revision:** `1d31eae137156e31abf0c88e6c8381941696d66c` (`main`, clean before this review)  
**Implementation:** Claude Sonnet 5, xhigh (per controller record)  
**Serial review/correction/merge:** Claude Opus 5, xhigh (per controller record)  
**Disposition:** **NOT READY FOR EXTERNAL ADOPTION**

## Executive verdict

P15–P19 are valuable work, and they did close the v1 review's most visible
failure: Python R1, R2 and R3 are now reachable through the installed `assay`
CLI. That is a real product milestone. It is not yet a trustworthy product
boundary.

This review reproduced four critical integrity failures:

1. R2 mutants and both R3 halves silently drop caller-appended argv and
   resolved environment-passthrough values, so they judge a different command
   from the one the artifact records.
2. R2 copies only the project directory. In a monorepo, a mutant command can
   fail because a tracked sibling is absent, and Assay counts that failure as a
   killed mutant and awards a false R2 PASS.
3. R3 copies a pre-existing coverage profile into scratch and does not require
   control/transform freshness. A half that produces no profile can be judged
   from stale baseline evidence.
4. Assay's Git boundary inherits ambient repository selectors. A hostile or
   accidental `GIT_DIR` can make a run rooted at repository A record repository
   B's HEAD.

There are also high-severity commit-binding, terminal-totality, schema-parity,
evidence-completeness, special-file, symlink, and workload-bound gaps. Several
can produce a false PASS; others produce an exit/traceback with no artifact
after the consumer command has already had side effects.

The right greenfield response is not to patch these opportunistically inside
P20. Insert three explicit prerequisites, keep the already-published handoff
ids stable, and migrate the artifact once before any external consumer exists:

`P20 repository/artifact boundary → P21 verdict v4 → P22 exact
reexecution/isolation`.

After that, prioritize the versioned wheel and a real existing-Python-project
qualification (P23/P24), then use real srdm as the Go abstraction proof in
P26–P28. P25 remains worthwhile but attested evidence is not on the shortest
path to proving the computed core safe.

## What was reviewed

I read and compared:

- canonical Nyxloom `AUTHORING.md`, `STANDARD.md`, `DOCTRINE.md`, and Assay's
  trove configuration and referenced design/state/measurement/workflow docs;
- the original Assay invariant and product goals in `docs/DESIGN-GUIDE.md`;
- the v1 post-series review and all current P15–P25 handoffs;
- P15, P16, P18 and P19 LOGs, P17's implementation/review commits (there is no
  P17 LOG), controller decisions A-134–A-152, and the actual merged code;
- the independent Go implementation in
  `shared-ramdisk-depot-manager/tools/covergate`, its real `go.mod`, gate guide,
  and the shared Go image;
- existing Python consumer candidates, especially Topos's real
  `tester-unified`/coverage gate;
- current official OpenAI model-selection guidance for the comparative staffing
  assessment below.

I ran the real Assay gate before making review edits. It reported:

```text
1831 passed, 1 skipped
100% statement coverage, 100% branch coverage
tester-unified exit 0; independent self-hosting witness 7 passed
```

That result is evidence that the committed test suite passes. It is not
evidence against the findings below: the first three critical probes all pass
the existing suite because their input shapes are absent from it.

## Severity convention

- **CRITICAL:** can award or bind a PASS to a command/repository/evidence set
  different from the one declared.
- **HIGH:** can omit the required artifact, accept unverifiable judgment, run
  unbounded work, or violate isolation in a realistic consumer shape.
- **MEDIUM:** materially weakens diagnostics/process evidence but does not by
  itself award a wrong PASS in the demonstrated path.
- **LOW:** documentation or maintainability drift with no current behavioral
  consequence.

## Findings

| id | severity | finding and direct evidence | consequence | owner |
|---|---|---|---|---|
| F01 | **CRITICAL** | R2/R3 reconstruct execution from `Lane` instead of reusing the effective R0 inputs. Probe: baseline argv `('runner','declared','--selected')`, env `{'FIXED':'yes','TOKEN':'baseline'}`; mutant argv `('runner','declared')`, env `{'FIXED':'yes'}`. R3 loses the same fields. | Artifact and baseline say one command; rigor judges another. | P22 |
| F02 | **CRITICAL** | R2 copies `project_root`, not repository topology. A real baseline reading tracked `../shared/marker` passed; the mutant copy lacked the sibling, exited 1, was counted killed, and R2 PASSed. | Infrastructure absence becomes mutation credit: direct false PASS. | P22 |
| F03 | **CRITICAL** | R3's whole-working-tree copy includes the baseline profile. In the probe, the coverage artifact existed before control and transformed evaluation although neither command wrote it. | A missing measurement can be replaced by stale evidence and satisfy the canary comparison. | P20 freshness + P22 snapshots |
| F04 | **CRITICAL** | `git._run_bytes` inherits ambient Git namespace/config. With `GIT_DIR` pointed at repo B, `head_rev(repo A)` returned repo B's HEAD. | Commit identity, diff and cleanliness can refer to the wrong repository. | P20 |
| F05 | **HIGH** | Only pre-run cleanliness is enforced for ordinary R0/R1/R2. A command modified a tracked file and exited 0; Assay emitted PASS bound to the original HEAD while `git status` showed the modification. Source-root-scoped checks also miss tests/support outside roots. | The command can change the evidence environment after commit binding and retain PASS. | P20/P22 |
| F06 | **HIGH** | The lane schema permits any nonempty rigor subset, but `run_lane` always constructs R0. A legal `rigor=('R2',)` reached bare `ValueError` for an undeclared R0 claim. | Post-HEAD crash/no artifact; declaration and execution ledger disagree. | P22; A-154 |
| F07 | **HIGH** | `read_coverage_artifact` checks a symlink path and then calls unbounded `read_text`; it does not safe-open/fstat a new regular file. FIFO/device/huge-file and replacement-race shapes are not bounded by the command timeout. | Assay can block or exhaust resources outside its declared process budget, or read a swapped input. | P20 |
| F08 | **HIGH** | Expected evaluation/source/filesystem errors remain outside the total artifact path. The known normalized-key collision escapes `evaluate_r1`; later source reads can raise `UnicodeError`/`OSError`. | The command can run and then Assay exits 2/tracebacks with no artifact. | P20 |
| F09 | **HIGH** | Schema has a closed mutant-operator enum; the dataclass accepts any nonempty string, and raw verification checks only policy membership. A fixture changed both policy and survivor to `invented-op`; `verify_document` returned `[]`. `judgment.r2.operators` is also schema-open. | `assay verify` accepts artifacts the shipped schema rejects; closed vocabulary is not actually closed. | P21 |
| F10 | **HIGH** | Killed mutants are count-only. Survived/crashed/budget entries carry identities, but the successful bucket does not. | An independent verifier cannot prove which declared sites/operators produced the credited kills. | P21 |
| F11 | **HIGH** | `judgment.r3.target` has no payload witness (A-152). | A target can be changed without invalidating a v3 artifact. | P21 v4 |
| F12 | **MEDIUM** | `FileCoverage.excluded=None` and known-empty remain distinct upstream but collapse in the artifact. | A reader cannot distinguish a format blind to exclusions from a clean report. This becomes live with Istanbul. | P21 v4; consumed by P29 |
| F13 | **HIGH** | `jobs` limits concurrency, not total mutants. The full lane timeout is reusable per mutant/control/transform, so declared work can multiply by candidate count. There is no `max_mutants`. | Cost and wall work are unbounded by the declared singular lane policy. | P21/P22; A-160 |
| F14 | **HIGH** | `shutil.copytree` follows symlinks by default and copies ignored/stale/cache data. It can dereference outside the repository and make scratch behavior depend on bytes absent from the recorded commit. | Isolation may leak data, copy unbounded trees, or judge non-commit state. | P22; A-156/A-161 |
| F15 | **MEDIUM** | Verdict construction validates timestamp shape but not `ended >= started`. `write_verdict` failure currently propagates as a generic exit 1, aliasing a tooling failure with a normal FAIL. | Invalid intervals validate; unavailable output has no truthful stable terminal and may occur after execution. | P21 |
| F16 | **HIGH (handoff defect)** | The pre-renumber Go mutation handoff (now P27) required compilation failure to be `crashed`, but the generic command boundary exposes only normal nonzero versus failure to start/timeout and discards command output. | The contract was not deterministically implementable without output heuristics or an invented second command. Corrected by A-158: normal nonzero, including compile rejection, is killed; crashed is a command-boundary failure. | P27 corrected |
| F17 | **MEDIUM (process)** | P17 has no package LOG. Its history is recoverable from four commit bodies and successor briefs, but reconstructing a report now would manufacture evidence never measured. | The package violates the durable handoff/report contract and weakens model/process evaluation. | Recorded; do not fabricate |
| F18 | **HIGH (estate, outside Assay scope)** | srdm's Nyxloom gate argv still uses `${SRDM_CGROUP_PARENT:-dev-background.slice}` and Topos hardcodes both `nyxloom-gates.slice` and a host bind path. Current repo doctrine forbids both patterns. | Reusing those outer commands would bypass the authoritative cgroup/path source and can launch into the wrong host context. | Separate srdm/Topos-owned repairs; Assay handoffs explicitly do not reuse them |

### Why F01–F03 matter together

These are not three unrelated edge cases. They show that the current repeated
execution abstraction has no single identity:

```text
declared lane + caller inputs + resolved env + commit
                     │
                     ▼
                R0 baseline
               /           \
      re-read smaller Lane   copy mutable working tree
          │                         │
      R2/R3 command drift      topology/stale-evidence drift
```

The correction is one immutable effective plan plus committed-object
snapshots. Adding more per-language relocation helpers would preserve the root
problem.

## P15–P19 package assessment

| package | what genuinely improved | what review/controller had to repair | residual judgment |
|---|---|---|---|
| P15 | Replaced content-sniffing diff parsing with count-driven state; made Git path transport NUL-safe in the touched paths; enforced coverage-model disjointness and collision refusal. | Three input-boundary defects: quoted-path tab ordering, `splitlines()` treating source content as structure, and locale/universal-newline Git decoding. All were real-Git reproductions. | Strong foundation, but its “one Git boundary” was incomplete: ambient Git namespace/config and attestation's separate path parser survived. |
| P16 | Added schema v3 policy binding and independent R1/R2/R3 re-derivation; explicitly rejected foreign versions after repair. | Five controller defects: payload-deletion forgeries at all levels, skippable R2 re-derivation, unimplemented v2 diagnostic, unenforced coverage summary identities, and most named precedence/cause negatives absent. | Important artifact work, but model/schema/raw-verifier parity was not closed and v3 lacks facts now known necessary. |
| P17 | Wired installed CLI R1; generalized refusal artifacts; preserved stale output until after refusal checks; expanded the conformance matrix. | Three defects plus oracle gaps: post-HEAD no-artifact paths, refusal deleting user data, and a stale audit that forbade newly-correct shapes; complete-document and symbolic-base witnesses had to be added. No LOG was produced. | Made R1 usable but did not establish a total post-command boundary or exact repeatable execution identity. |
| P18 | Wired real Python changed-line mutation, reused the existing R0 baseline, added declared operator filtering and deterministic aggregation. | Four defects plus two oracle gaps: nested-project crash, stale CLI capability text, skipped installed-wheel/O4 work, and unbound `judgment.r2`; the specified mutation set had not been run. | R2 is reachable, but current copy scope and dropped caller inputs make it unsafe and can award false mutation credit. |
| P19 | Wired real isolated Python R3, added closed canary config, made the runner adapter-generic, and tied R2/R3 policy where v3 can witness it. | One structural path-relocation defect, one absent mechanism oracle, one false impossibility claim, and one misleading public name. The shipped branch was 100%-covered while `uncovered-line` could never PASS. | R3 reachability is proven; freshness and exact-command isolation are not. Working-tree copy is the wrong substrate. |

The controller corrections were substantive and often excellent. The key
adversarial observation is that **every one of the five packages required a
material controller repair**, and the post-series pass still found systemic
false-PASS paths that package-local review did not see.

## Comparison with Assay's original goals

| original goal/invariant | current state after P19 | verdict |
|---|---|---|
| Never render a judgment Assay cannot deterministically make | R2 can award kills for missing monorepo siblings; R3 can read stale profiles; P23 asked for an unobservable compile/test distinction. | **Not met** |
| Run one declared command and record exactly what ran | R0 is transparent, but R2/R3 lose append/passthrough inputs. | **Not met above R0** |
| Bind a verdict to the commit actually measured | Pre-run HEAD is recorded, but ambient Git can redirect it and the command can mutate the tree before PASS. | **Not met** |
| Zero-dependency standalone installed product | Real wheel execution/self-hosting exists, but version remains `0.0.0` until P21 and output/artifact boundaries remain unsafe. | **Partly met** |
| Independent, language-neutral artifact consumption | v3 re-derivation is meaningful, but operator parity, killed identities, target binding and exclusion capability are incomplete. | **Partly met; v4 required** |
| Language abstraction proven by more than Python syntax | Go parser/adapter units exist, but no real Go toolchain has run through the product. | **Not yet proven** |
| Usable by existing projects, not only fixtures/self | No external Python project currently runs Assay; no consumer is migrated. | **Not yet proven** |
| Defaults never substitute for available facts | Re-reading Lane instead of the resolved plan and copying only project root are both shadowing defaults. | **Not met** |

The architecture remains promising: format parsing and language adapters are
separate axes, the artifact is data rather than a linked API, and the
cause-sensitive canary/mutation model is useful. The failures are concentrated
at orchestration boundaries—exact process identity, repository materialization,
fresh evidence, and independent artifact completeness—not in the central
language/format split. That argues for repair, not a rewrite.

## Changes made directly in this review

Only low-risk corrections were made directly:

- corrected stale v3 schema descriptions and `Judgment` docstring that still
  called already-populated R2/R3 fields “RESERVED”;
- recorded decisions A-153–A-162 and updated the design/state documents;
- renumbered the unimplemented queue into a lint-valid P20–P29 sequence and
  inserted repository integrity, v4, exact isolation, and Python qualification;
- refreshed P20–P25 input revisions/dependencies and carried-forward briefs;
- assigned A-O15 to P20; closed/assigned A-O14, A-O16, A-O17 and A-O18;
- corrected P27's unimplementable compile-crash premise;
- added real disposable-Topos qualification to P24 and real disposable-srdm
  validation to P26–P28.

No major behavioral repair was folded into this review. The critical changes
cross model/schema/runner/Git/isolation boundaries and deserve their own
oracles, real gate, review and durable LOG.

## New prerequisite handoffs

### P20 — repository/artifact boundary integrity

Closes ambient Git namespace/config, safe bounded artifact reads, freshness,
post-command commit binding, and total expected post-HEAD terminals. It does
not change the schema.

Ship criteria:

- two-repository hostile-Git fixtures cannot redirect HEAD/diff/status;
- FIFO/device/symlink/oversize/race inputs are rejected without blocking;
- a command-created tracked/test/support change cannot retain PASS;
- normalization/source/read errors emit complete artifacts.

### P21 — verdict v4 evidence contract

Performs the one pre-adoption migration. It adds full killed identities,
closed operator parity, `max_mutants`, canary target binding, exclusion
capability, timestamp ordering, and truthful output/mutation-limit terminals.

Ship criteria:

- model, JSON Schema and independent raw verifier accept/reject the same
  vocabulary and cross-field facts;
- every attempted mutant is named;
- no v1–v3 coercion or compatibility writer exists;
- an unavailable output path is refused before consumer execution.

### P22 — exact reexecution and committed isolation

Creates one immutable effective command plan, requires R0, and starts baseline,
mutants and canary halves from controlled snapshots of the resolved commit's
tracked repository objects. It preserves the full repo/project topology,
refuses unsafe entries, requires fresh output, and applies one total lane
budget.

Ship criteria:

- append/passthrough values reach every subprocess exactly;
- nested-project sibling dependencies behave identically in baseline/mutants;
- ignored stale profiles are absent from every snapshot;
- no working-tree `copytree`, consumer hook/filter, or per-subprocess budget
  reset remains.

### P24 — real Python project qualification

After the versioned wheel, runs installed Assay over a disposable current
Topos tree and compares R1 with both an independent manifest and Topos's own
coverage evaluator. This is intentionally not a Topos migration.

## Outstanding handoff disposition

| handoff | disposition after this review |
|---|---|
| P23 versioned wheel | Keep and prioritize after P22. All expected artifacts become v4; no placeholder/compatibility writer. |
| P24 real Python qualification | New. Runs real disposable Topos through the installed wheel before claiming existing-Python-project readiness. |
| P25 attested evidence | Keep. Depends on P22; now explicitly owns A-O15 and must use the sanitized NUL-safe Git path boundary. Do after the computed-core/distribution critical path unless attestation is immediately needed. |
| P26 real Go R1 | Keep, now depends on P24. Tiny fixture plus disposable real srdm; compare Assay with srdm covergate. A-O17 is already closed upstream. |
| P27 real Go R2 | Keep with corrected result semantics and v4 full identities/caps. Exercise selected real srdm packages; compiler rejection is killed if the command started normally. |
| P28 real Go R3 | Keep. Reuse P22's generic snapshot runner; test both mechanisms on tiny and real-srdm copies; target is a v4 field, not description text. |
| P29 Vitest formats | Keep; depends on P21 and consumes the decided exclusion-capability field. It does not own another schema decision. |
| future TypeScript adapter | Do not carve until P29's real format semantics are measured. |
| consumer adoption | Do not put cross-project edits in an Assay handoff. After P24 is green and the target is stable, carve in that consumer's own trove. Topos remains the logical first candidate, but its large active wave makes an input revision premature today. |

## Recommended execution order

1. P20 — Git/artifact/terminal integrity.
2. P21 — schema v4 and bounded complete evidence.
3. P22 — exact plan, committed snapshots, total budget.
4. P23 — versioned reproducible wheel.
5. P24 — real existing Python qualification.
6. P25 — attested evidence hardening (can move before P23 only if Tier-3
   evidence is a near-term requirement; it is not required to prove computed
   Python/Go rigor).
7. P26 — real Go R1 plus srdm differential.
8. P27 then P28 serially; both touch the Go adapter/gate/tests and should not be
   implemented concurrently despite their logical independence.
9. P29, then carve the TypeScript adapter from observed real Vitest output.
10. Carve the first real adoption in the consumer's trove. Run old and new
    gates in shadow on identical commits before replacement; adoption declares
    and verifies, never remediates unrelated coverage debt.

No new feature work should outrun P20–P22. Adding languages before exact
execution/repository identity is trustworthy multiplies the same defect across
more adapters.

## Implementer and reviewer performance

### Sonnet 5 xhigh as implementer

**What it did well:** high throughput; coherent cross-file implementations;
substantial test volume; useful self-review in P17–P19; correct escalation on
some uncertain product calls; and generally good adherence to the broad
architecture. P18/P19 in particular built real functionality rather than
stubs.

**What it did poorly:** completion claims were not reliable against the handoff
ledger. P18 skipped a whole work item/oracle without declaring it. Package
fixtures repeatedly made project root equal repo root, resolved values equal
declared values, or omitted the only rigor combination that could exercise a
mechanism. Boundary defaults (`splitlines`, locale decode, `copytree`, re-read
Lane) were accepted too readily. “Complete artifact” was several times reduced
to selected field assertions. These are exactly the failure modes Assay is
meant to catch.

**Judgment:** suitable for bounded implementation behind a strong handoff and
mandatory independent adversarial review. Not suitable as the final authority
for repository/process/evidence integrity. For P20/P21/P25-like bounded work,
it was cost-effective; for P20–P22 or Go semantic boundaries, its demonstrated
default/blind-spot profile is too risky without stronger review.

### Opus 5 xhigh as serial reviewer/controller

**What it did well:** very high-value review. It repeatedly reproduced failures
through real installed entry points, caught structural false passes invisible
at 100% coverage, repaired scope/correspondence/test-matrix defects, rejected a
false impossibility claim, and preserved durable decisions/successor briefs.
Without this review, P15–P19 would be materially unsafe.

**What it missed:** the review cadence remained package/diff-local. It did not
step back after P19 and compare the baseline and every repeated process as one
ledger; that is where F01 appeared. It repaired project/repo path spelling in
P18 but not the stronger monorepo-topology invariant, repaired R3 relocation
but not stale copied evidence, and did not adversarially seed ambient Git
namespace/config. It also accepted count-only killed evidence and the absence
of a global mutation/time bound. The final gate review was excellent at proving
the new tests, less effective at inventing cross-package inputs no handoff had
named.

**Judgment:** strong reviewer/controller and clearly better than no independent
review, but not sufficient as the sole series-closing security/integrity audit.
Add a fresh post-series reviewer whose prompt explicitly ignores package
boundaries and tries to falsify identity, freshness, containment, and budget.

### Assumptions for GPT-5.6 staffing

There is no head-to-head Assay evaluation of Claude 5 versus GPT-5.6 in this
record, so the following is a staffing hypothesis, not a benchmark result.
OpenAI's current guidance positions GPT-5.6 Sol for the hardest complex
reasoning/coding, Terra for the intelligence/cost balance, and Luna for
cost-sensitive high volume; it also recommends choosing reasoning effort from
representative evaluations rather than assuming more is always better.
([official model-selection guide](https://developers.openai.com/api/docs/guides/latest-model),
[official text-model reference](https://developers.openai.com/api/docs/models/text))

| work | implementer assumption | reviewer assumption | reason |
|---|---|---|---|
| P20 Git/artifact integrity | **GPT-5.6 Sol xhigh** | fresh **Sol max** | Adversarial OS/Git/filesystem boundary; false-PASS risk dominates cost. |
| P21 schema v4 | **Sol xhigh** | independent **Sol max** | Three-way model/schema/raw-verifier parity and migration completeness need long-horizon checking. |
| P22 exact isolation | **Sol xhigh** | **Sol xhigh/max** | Cross-cutting execution ledger, Git-object snapshot, security and performance semantics. |
| P25 attestation | **Terra xhigh** | **Sol high/xhigh** | Bounded parser/path work after P20 supplies the hard boundary. |
| P23 wheel | **Terra high/xhigh** | **Sol high** | Mostly packaging/reproducibility with precise offline oracles. |
| P24 Python qualification | **Terra xhigh** | **Sol high** | Validation/gate work; no production-code design authority. |
| P26–P28 Go | **Sol xhigh** | separate **Sol xhigh** | New real toolchain plus language semantics and cross-language abstraction proof. |
| P29 Vitest formats | **Terra xhigh** | **Sol high** | Bounded format work, but real-output aggregation requires an adversarial review. |
| docs/fixture mechanical conversions | **Luna medium/high** | Terra low/high sampling | Low-judgment volume work only; never final integrity review. |

I would not begin by paying max effort for every turn. Run one representative
package at high, xhigh and (for Sol) max, then score:

- handoff work-item completion (including declared skips/BLOCKED);
- number/severity of controller changes;
- success of the explicit adversarial probes in this report;
- complete-artifact/oracle independence;
- gate result, tokens, latency and cost.

The model that writes the implementation should not perform the only review,
regardless of family or effort. The strongest lesson from this series is not
“use a larger model”; it is “change the review input distribution.” Every
package was green under its own fixtures. The valuable failures came from a
second actor inventing different repositories, paths, declarations and
evidence states.

## Final ship/no-ship boundary

Assay may be called **qualified for existing Python projects** only after:

- P20–P23 are merged and the real gate is green;
- P24 agrees with Topos's independent gate on the same disposable commits;
- no known false-PASS finding in F01–F14 remains accepted debt;
- the versioned wheel/hash, v4 schema and independent verifier agree.

Assay may be called **language-abstracted for Go** only after:

- P26–P28 run the installed wheel in the pinned real Go image;
- tiny fixtures and disposable real srdm both pass their independent
  comparisons;
- the Go adapter adds no alternate core runner, Git boundary, snapshotter or
  artifact semantics.

Until then, the honest product statement is:

> Assay has a strong language-neutral design and reachable Python R0–R3
> prototypes, but its repeated-execution and evidence boundaries are not yet
> safe enough to gate another project's merge.
