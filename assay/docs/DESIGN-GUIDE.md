# assay — design guide (non-normative reasoning)

> **Why this file exists.** `nyxloom-trove/decisions.md` records *what* was
> decided, one line each. This file records *why*, and the abstractions the
> decisions rest on. A decision without its reasoning gets re-litigated or,
> worse, silently widened by someone who never saw the argument against it.
> Written 2026-08-06 from the design session that scoped v1; base revision
> `d87f028b`.

---

## 0. The one invariant

> **assay never renders a judgement it cannot make deterministically.**

Every exclusion in §7 and every tier in §3 follows from this. When a proposal
arrives that does not obviously belong, test it here first.

## 1. What assay is, in one paragraph

assay answers **HOW TO JUDGE** a change. It reads a project's declared lanes,
runs one declared command, judges the result against declared rigor levels,
and emits one machine-readable verdict per lane per commit. It does not choose
what to run (the project's lane file does) and it does not choose where to run
(an environment tool such as `ciu` does). It is a stand-alone library and CLI
with **zero runtime dependencies** — stdlib only.

## 2. Why it exists: four copies, and each one is the sole holder of something

`coverage_gate.py` exists four times across the estate and every copy has
diverged. The usual DRY argument is not the point; the *evidence* is:

| Copy | Lines | Sole holder of |
|---|---|---|
| `dstdns/scripts/coverage_gate.py` | 823 | multi-line-statement attribution (`statement_spans` + `attribute_line` + the `unclassified` third bucket, incl. decorators and match-case patterns); comma-separated multi-prefix `--source`; the `_is_test_path` skip |
| `topos/tools/coverage_gate.py` | 316 | directory-boundary prefix matching (`topos/src/topos` must not match `topos/src/topos_evil`); `_validate_cov_record` — malformed coverage JSON raises instead of yielding a silent green |
| `srdm/tools/covergate` (Go) | ~1000 | the `NoCode` bucket (a file with zero instrumentable functions leaves the ratio rather than counting uncovered); a `Considered` count so a 0/0 pass explains itself; block→line expansion with executed-wins-on-overlap; an already-parameterised `Evaluate(added, coverage, sourcePrefix, ext, failUnder, isTestFile, hasCode)` |
| `nyxloom/src/nyxloom/coverage_gate.py` | 472 | nothing — now a strict subset of dstdns's Python behaviour |

Two corrections to the P90 handoff's inventory, recorded because they change
what "take the union" means:

1. `--allow-excluded` and the NO-MEASUREMENT guard are **not** dstdns-only.
   nyxloom has both (`e03b0715`, `c7da4416`); srdm has NO-MEASUREMENT
   (`checkMeasurable`, `exitNoMeasurement = 3`). The handoff's 455-line figure
   predates them.
2. **topos is thinnest by line count and still holds two checks nobody else
   has.** "Thinnest" is not "least", and an extraction that treats it as the
   floor loses real behaviour.

**The union contains a policy contradiction, not only feature gaps.** topos's
failure text ends *"or mark a genuinely unreachable line with `# pragma: no
cover`"* — the exact act nyxloom and dstdns fail the gate for. A merged tool
must pick a side. assay resolves it by making `allow_excluded` a **required**
per-lane field, so each project states its position at adoption instead of
inheriting whichever copy it happened to fork.

**srdm's Go rewrite is the sharpest signal in the set.** A consumer needed the
capability badly enough to build it again in another language rather than adopt
a tool it could not consume standalone. The lesson is about *adoption cost*: a
capability priced at "install a library, write a config file" gets adopted; one
priced at "adopt an orchestrator first" gets re-implemented. That is observed
history, not theory.

And srdm's `Evaluate` signature is the strongest evidence the adapter boundary
is real: a Go author, working independently, factored out exactly the
parameters (`ext`, `isTestFile`, `hasCode`, `sourcePrefix`) that a
`LanguageAdapter` protocol needs. Two independent discoveries of the same seam.

## 3. The three tiers of evidence

assay carries evidence in three tiers. Conflating them is how a testing tool
grows into a policy engine or an LLM harness.

| Tier | assay's role | Determinism | Examples |
|---|---|---|---|
| **1 — COMPUTED** | derives the verdict itself, in-process | full | R0–R3: coverage, canary, mutation, serial/parallel parity, fail-before/pass-after |
| **2 — ADJUDICATED** | **invokes** a declared third-party tool and applies a **declared threshold** to its structured output | deterministic *given the tool* | SAST, SBOM/vulnerability, license, DAST, accessibility budget, visual regression |
| **3 — ATTESTED** | ledgers evidence produced entirely elsewhere: validates shape, binds to commit, checks staleness — **never verifies** | none, and the artifact says so | adversarial review, production replay, ClusterFuzzLite findings |

Tier 2 does not violate §0: the judgement is the *tool's*, and assay's
contribution is a declared threshold, which is deterministic. The hazard to
watch is a missing or crashed scanner — that must be `ERROR` or
`NO_MEASUREMENT`, **never** `PASS`.

Tier 3 exists because the alternative is worse than it looks. TESTING-
METHODOLOGY is explicit that *"a runner cannot infer intent"* and that *"review
judges meaning"*. If assay simply ignores non-mechanical evidence, a verdict
artifact showing R0+R1+R2 green reads as *"this change is fine"* when the only
method that could have caught the defect never ran. **That is the 0/0-is-100%
bug at the level of the evidence table**, and it deserves the same guard.

What makes an attestation more than a rubber stamp — all of it mechanical:

- it names the commit it reviewed, which must be the commit under test or an
  ancestor of it; an unrelated or descendant commit cannot satisfy the record;
- it records its producer (model id or human identity), so a consumer can weigh
  it;
- assay diffs the paths the attestation claims to have reviewed between an
  ancestor attested commit and the commit under test. Byte-identical reviewed
  paths remain current; a changed reviewed path renders
  `NO_MEASUREMENT/STALE_ATTESTATION`. **assay cannot judge a review, but it can
  prove a review is stale.**
- the evidence entry always carries `verified_by_assay: false`. assay never upgrades an
  attestation.

Asynchronous `PENDING` evidence is deferred until claim-level enforcement is
designed. It is not a seventh outcome hidden inside an evidence entry.

## 4. The boundary with ciu, and why they are not one tool

D7's split is WHERE (ciu) / WHAT (the project) / HOW (a testing library).
Merging assay into ciu was considered and rejected. The ergonomic argument for
merging turns out to be empty — `ciu test --lane package` reads identically
whether the tools are merged or whether `ciu test` resolves WHERE and reads
assay's verdict artifact. What remains:

**The decisive argument is topological, and it is visible in ciu's own gate
today:**

```
docker run … tester-unified:local  bash -c 'cd {worktree}/ciu && pytest && python -m …coverage_gate …'
└─────────── ciu, OUTSIDE ───────────┘        └────────── assay, INSIDE ──────────┘
```

The coverage gate must sit next to the coverage artifact and the source tree —
inside the container. ciu issues `docker run` — outside it. Merging means
either shipping a container orchestrator into every test image, or splitting
the merged tool back in half at runtime. That is a boundary in the runtime
topology, not a preference.

Supporting arguments: ciu's dependency closure would grow with every technique
a consumer adopts (D7's own point); ciu changes when Docker/cgroups/compose
change while assay changes when a method is added, so merging couples two
independent release rhythms; and srdm/netcup-api-filter would have to adopt an
orchestrator to get a coverage floor.

Note also that ciu's gate currently reaches into a sibling project's source
tree — `PYTHONPATH=../nyxloom/src python -m nyxloom.coverage_gate` — which is
the concrete defect assay's existence deletes.

**The reciprocal discipline, taken from D7's own closing line:** if ciu ever
needs to grow a rigor score, or assay ever needs to grow a network name, the
split was drawn wrong — **reopen this note rather than widening either tool.**

## 5. Defaults doctrine (dstdns AGENTS §4.2a), applied

> *A default is legitimate only when it is a policy choice that is correct in
> the absence of information. It is a hazard the moment it substitutes for a
> fact that exists somewhere else.* Prefer **DERIVE**, then **READ**, then
> **FAIL**. Never invent.

This is not decoration; it changed four v1 decisions.

**`source_roots` is required, with no default.** All four copies ship anti-
pattern #1 — a literal standing in for a fact that lives authoritatively in the
project's layout: `default="src/nyxloom"`, `default="topos/src/topos"`,
`DEFAULT_SOURCE = "libs/common/src,applications/controller/src,…"`,
`-source internal -module srdm`. If any is wrong it measures the wrong tree and
**passes**. A library cannot ship any of them; it must read them.

**Nonexistent `source_roots` fail at load time.** Apply the §4.2a test — *if
this is wrong, does anything fail loudly?* A typo'd source root matches no
changed file, so the gate returns 0/0 PASS forever. That is a laundering gate,
and none of the four copies guards it.

**Coverage format is READ and cross-checked by DERIVATION.** The lane declares
it (it is a fact of the lane's own argv: `--cov-report=json` vs `lcov`), and
assay sniffs the artifact (`mode:` → go-cover, `{"files":` → coverage.py JSON,
`SF:` → lcov, `<coverage` → cobertura) and **refuses on mismatch**. A lane whose
argv changed format and was not updated fails loudly instead of mis-parsing.

**`_derive_test_command` is deleted, not ported.** nyxloom's mutation gate maps
`src/<mod>.py → tests/test_<mod>.py`. That is a naming convention, not a
language fact, and it is anti-pattern #2 verbatim — the consumer inventing on
absence. The lane declares the argv; mutation runs that. This is a deletion for
doctrine, not for aligning copies, and it is the one place v1 deliberately does
not take the union.

**`env` is declared-only, with an explicit `env_passthrough` allowlist.** The
first proposal was "augment the ambient environment", which is quietly
incoherent: at S3 the lane runs in a container, where the host shell's
environment is *already* not inherited — Docker supplies the image's `ENV` plus
explicit `-e`. "Augment" would make the same lane file mean different things at
S1 and S3. The base comes from the execution context (a fact of WHERE), never a
value assay invents. A bare `argv[0]` is accepted only when `PATH` appears
explicitly in `env` or `env_passthrough`; otherwise the lane fails to load with
`BAD_LANE_CONFIG`. This prevents Python/libc's implementation-default executable
search from becoming an undeclared input. An argv containing `/` needs no PATH.

## 6. The verdict contract

**Three channels, none duplicating another's authority.** The **exit code is
the verdict** — a consumer checking only the exit code must never be wrong.
The **artifact is for machines**. **stdout is for humans.** The artifact adds
diagnosis; it never changes the outcome.

### Six outcomes

| Exit | Outcome | Meaning | Already exists as |
|---|---|---|---|
| 0 | `PASS` | verdict rendered, floor cleared | all four copies |
| 1 | `FAIL` | verdict rendered, adverse | all four copies |
| 2 | `ERROR` | assay broke — bad config, unreadable artifact, exec failure | `CoverageGateError` (3 copies) |
| 3 | `NO_MEASUREMENT` | preconditions make any verdict vacuous | `NoMeasurementError` (3 copies) |
| 4 | `BUDGET_EXCEEDED` | lane ran, exceeded its declared budget | `gate_runner`'s 124 sentinel |
| 5 | `INCONCLUSIVE` | ran; instrument rendered no judgement | `gate_canary.inconclusive`, `INCONCLUSIVE_NO_MUTANTS` |

None is invented — each already existed as a concept somewhere in the estate.
**2–5 are all not-a-pass and all block a merge**; the split drives diagnosis and
retry policy (a CI system may retry 4; it must never retry 3).

A required `reason_code` names the cause without proliferating exit codes. The
enumeration is **closed** — an implementer that needs a code not listed here
must stop and ask, never invent one:

| Outcome | `reason_code` |
|---|---|
| `PASS` | the key is **omitted**, not null. A pass has no cause to name. |
| `FAIL` | `UNCOVERED_LINES`, `EXCLUDED_LINES`, `UNCLASSIFIED_LINES`, `MUTANTS_SURVIVED`, `CANARY_SURVIVED`, `COMMAND_FAILED` |
| `ERROR` | `GIT_FAILED`, `UNREADABLE_ARTIFACT`, `FORMAT_MISMATCH`, `BAD_LANE_CONFIG`, `EXEC_FAILED` |
| `NO_MEASUREMENT` | `DIRTY_TREE`, `BASE_IS_HEAD`, `EMPTY_COVERAGE`, `MISSING_ATTESTATION`, `STALE_ATTESTATION` |
| `BUDGET_EXCEEDED` | `LANE_TIMEOUT` |
| `INCONCLUSIVE` | `NO_MUTANTS`, `CANARY_INCONCLUSIVE` |

A single-member enum (`BUDGET_EXCEEDED`) is deliberate rather than a smell: the
field is required on every non-PASS outcome, so a consumer switching on
`reason_code` never has to special-case an outcome that lacks one.

### Nailing NO MEASUREMENT — the guard is what is *absent*

`"0/0 changed executable lines covered (100.0%)"` reads identically whether the
gate measured everything and found nothing, or measured nothing at all. Three
distinct causes produce the second, and they are not all tree-state:

| Cause | What it really is | Guarded by |
|---|---|---|
| uncommitted changes under the source roots | tree state | 3 of 4 copies |
| `base` resolves to HEAD | **ref resolution** — the tree is fine | 3 of 4 copies |
| coverage artifact well-formed but zero files | the measurement itself was vacuous | **none of the four** |

So `DIRTY_TREE` would name only the first and `WRONG_TREE` would misname the
second. What they share is the switchable fact: *no delta was measured, so no
percentage means anything.* **`NO_MEASUREMENT` outranks `FAIL` in the rollup
because in all three cases the delta being judged is not the delta under test.**

**When `outcome == NO_MEASUREMENT`, the coverage block is omitted entirely, not
zeroed.** Emitting `{"covered": 0, "changed_executable": 0, "pct": 100.0}`
beside it rebuilds the exact ambiguity one layer up: a consumer reading `pct`
and ignoring `outcome` gets `100.0`. The existing copies avoid this only
incidentally (they never reach `evaluate`). Making it a schema rule — *no
percentage exists unless a measurement produced one* — is what stops the bug
returning through the artifact.

The mirror case matters as much: a **legitimate** 0/0 pass *does* emit the
numbers, plus srdm's `considered` count so it explains itself — *"3 changed
file(s) under the roots, none contributing executable non-test lines (0/0)"*.
Identical strings for the two cases is what started all of this.

**P20 makes “artifact absent” explicit and binds output to one invocation
(A-174).** A clean command that produces no declared coverage file is
`NO_MEASUREMENT/EMPTY_COVERAGE`, the same truthful absence as a well-formed
profile containing zero files. It is not an unreadable artifact and may not be
replaced by a stale pre-run file. The runner reserves the project-relative
output before execution, unlinks an unchanged prior regular file only after all
refusals pass, and consumes the new output exactly once through its held parent
descriptor. Reads are nonblocking, regular-file-only, no-follow, and bounded to
16 MiB before UTF-8 decoding. A path precheck followed by `read_text` is not
equivalent: it can reopen a swapped parent/object and has no work bound.

**Post-command dirt precedes every higher-rigor judgment (A-175).** The initial
clean-tree guard proves what existed before the command; it says nothing about
what the command left behind. Immediately after the command, before consuming
coverage or starting R1/R2/R3, Assay checks the whole Git-visible repository.
If it is dirty, the real R0 command claim remains when higher rigors exist and
all declared higher claims become `NO_MEASUREMENT/DIRTY_TREE`; an R0-only lane
uses that terminal on R0 itself. Assay never cleans the consumer tree to make a
claim true. P23 moves baseline/repeated execution into committed snapshots,
which is the later boundary that also removes ignored/untracked inputs from
those executions.

### Rollup precedence

`ERROR > NO_MEASUREMENT > BUDGET_EXCEEDED > FAIL > INCONCLUSIVE`, and `PASS`
only when every declared claim passed. `ERROR` and `NO_MEASUREMENT` outrank
`FAIL` because they invalidate everything computed beneath them.
`BUDGET_EXCEEDED` outranks `FAIL` because the run was truncated, so the finding
may be partial.

### Computed rigor and external evidence are separate axes

TESTING-METHODOLOGY's thesis is *"record the evidence actually obtained rather
than reducing quality to one coverage percentage."* A lane declaring
`["R0","R1","R2"]` can pass R0, pass R1 and be `INCONCLUSIVE` on R2; a scalar
verdict flattens that into a lie. `claims[]` therefore carries exactly one
**computed** entry per `declared_rigor` level.

Adjudicated and attested evidence are not rigor levels. The sibling
`declared_evidence[]` and `evidence[]` arrays are keyed by the explicit pair
`(source, key)` and must cover each other exactly. Thus *"adversarial-review was
declared but rendered no judgement"* cannot look like *"adversarial-review was
never declared"*, and no external review is forced into a fictitious R3 slot.
The attested branch records `producer`, `attested_commit`, and `reviewed_paths`
when an attestation was obtained, while always recording
`verified_by_assay: false`. The adjudicated sibling shape is reserved in v2;
the first real integration adds its payload and registry behavior.

### Transparency of what actually ran

The verdict records `argv_declared`, `argv_appended`, `argv_effective`,
`env_declared` and `env_effective` on **every** outcome. A run with a non-empty
`argv_appended` sets `argv_modified: true`, and the lane must carry
`allow_argv_append` explicitly for appending to be permitted at all. **assay
never derives flags** — they come from the lane or the caller, never from assay
inspecting the diff. Deriving them would be impact-based selection, which is
the caller's domain (§7).

**Repeated rigor consumes one immutable effective command plan (A-155).**
The declared/appended/effective argv and declared/effective environment are
not merely audit fields on R0: they are the process inputs for every mutant
and both canary halves. Re-reading `Lane` inside a scratch directory loses
caller appends and resolved passthrough values, producing a judgment about a
different command than the artifact records. Relocation changes only the
snapshot root beneath the plan's project-relative working-directory identity.

**Git itself is a controlled input (A-173).** Assay resolves one absolute Git
executable from the caller's declared `PATH`, supplies a replacement environment
with no ambient repository/config/object selectors, ignores replacement refs,
and anchors substantive commands with explicit resolved `--git-dir` and
`--work-tree` values. `-C` alone is not an identity boundary: a repository-local
`core.worktree` can redirect topology, and a replace ref can rewrite the object
graph without changing the displayed ref spelling. Diff additionally disables
external diff and textconv execution; commit disables configured signing in
addition to all hooks. All output remains raw bytes until the
single UTF-8 decoder, so locale and universal-newline behavior cannot silently
change path or patch identity.

### Binding the effective judge policy (v3)

A percentage alone does not let an independent consumer re-derive a status:
schema v2 recorded `Coverage.pct` but not the `fail_under`/`allow_excluded`
policy that turned it into `PASS`/`FAIL`, nor the full resolved comparison
commit the diff was measured against — a post-series adversarial review
found `assay verify` accepting a `PASS` claim reporting 0% coverage as a
direct consequence. Schema v3 adds top-level `scope`/`enforcement` (already
static `Lane` attributes, present whenever a lane resolved, exactly like
`argv_declared`) and a `judgment` object recording the resolved policy
behind whichever claims rendered a real computed judgment. `judgment.r1`
is present if and only if the R1 claim carries a `coverage` payload — an
independent consumer with a coverage percentage and no policy learns
nothing more from it than schema v2 already gave them.

`judgment.r2`/`r3` were reserved, closed shapes at v3's own introduction,
on the reasoning that R2 and R3 *status* is already re-derivable from
`Mutation`'s and `CanaryResult`'s own fields alone. P18 and P19's CLI
wiring populated them, and **A-148 then extended the same
if-and-only-if rule to both** (`Verdict._check_judgment_matches_claims`,
and independently in `verify.py`): presence agrees with the payload-bearing
claim in both directions, every operator a mutation payload names must be
one `judgment.r2.operators` declared, and a canary payload's `mechanism`
must be the one `judgment.r3` records. Re-derivable *status* was never the
question — what the policy record adds is which policy produced it, and a
recorded fact tied to nothing is exactly the "take it on trust" gap this
schema exists to close. **One field remains untied and deliberately so:
`judgment.r3.target` (A-152).** `CanaryResult` carries no target, so no
rule inside v3 can witness it being wrong; closing it needs a `canary.target`
field and therefore a v4 migration, which A-138 makes a consumer's decision
rather than a producer's.

**The post-P19 adversarial review makes v4 the next pre-adoption contract
(A-157).** P21 adds that canary target; all killed-mutant identities (a count
cannot prove which sites/operators were killed); a required recorded
`max_mutants`; an explicit `reported`/`unavailable` exclusion capability; and
model/schema/raw-verifier parity for the closed operator vocabulary and time
interval. It also names output-write and mutation-limit terminals. These are
batched into one migration before an external consumer exists; v3 remains the
current implementation until P21 lands and is never upgraded in place.

**The cap owns its discovery seam (A-180).** P21 cannot truthfully record
`max_mutants` while an adapter first materializes an unbounded tuple containing
one full mutated source file per candidate. The v4 migration therefore also
lands the common/Python `MutationSite` protocol: selected operators plus a
remaining `max+1` capacity in, a bounded ordered tuple of UTF-8 byte-span and
replacement descriptors out. Full replacement text is materialized only for a
submitted unit. Outcome identity is built directly from that syntax site; a
minimal diff of original and replacement files is not equivalent (`<` to `<=`
looks like a zero-width insertion, and shared suffixes shrink token spans).
P23 consumes this seam for snapshot execution; P29 implements it for Go.

**Validation layers claim only what they can express (A-182).** Schema, model,
and raw verifier independently close local v4 grammar and vocabulary. The model
and raw verifier independently enforce target equality, max+1 arithmetic,
cross-bucket identity uniqueness, and parsed timestamp order; Draft 2020-12 has
no `$data` relation with which to pretend those cross-object comparisons are in
the schema. Timestamp order compares offset-aware instants, never serialized
strings. A foreign v1-v3 document is rejected with one version-only diagnostic
before any current-shape check.

**A judged status carries the payload it judged.** Re-derivation only bites
where there is something to re-derive, so the cheapest evasion of it is not
a contradictory payload but no payload: a `PASS` claim with its `coverage` /
`mutation` / `canary` block simply deleted leaves the rollup in perfect
agreement and nothing to check. So the model refuses to construct one at
every level where a producer proves it impossible — R1 `PASS`/`FAIL`,
R2 `PASS` and the two mutation-only reason codes, and any R3 status that is
a *judgement of* a canary rather than a report that the canary machinery
never ran. This is the same discipline as A-025's "absent means unknowable,
empty means known-and-empty", read in the other direction: a status that
claims knowledge must say what it knows it from.

### Consumption without linking

Versioned JSON plus a **JSON Schema shipped as data**, so ciu, a CI system or
nyxloom validates against a file rather than importing a package. The artifact
carries `schema_version: 3` (an integer, bumped on any breaking shape change) and
`assay_version`.

**A version bump is a migration for the consumer, never an upgrade by the
producer.** `assay verify` refuses any `schema_version` but its own, with a
single diagnostic naming the version and nothing else — it does not read the
rest of a foreign artifact, because every later complaint would be a
consequence of the version rather than an independent defect (a v2 artifact
otherwise reports a bare `KeyError` on a v3 field its producer had never
heard of). It never coerces, defaults, or in-place upgrades a stale
artifact: an artifact records what one run of one assay judged, so the only
honest way to obtain a v3 verdict is to produce one.

nyxloom's existing `GateResult` is a strict subset: six REQUIRED fields
(`gate_id`, `phase`, `commit`, `exit_code`, `started`, `ended`) plus an
optional `environment` — verified against `nyxloom/src/nyxloom/types.py`, which
also carries optional `artifacts` and `output_tail`. So nyxloom consumes the
artifact by reading six keys and may read a seventh; an implementer must not
read "six keys" as licence to omit `environment`.

**Emitted on every outcome, including `ERROR`.** Precedent: the remote-mutation-
audit reference already requires `events.jsonl` and `summary.json` *"even when
a baseline, mutant, or teardown fails."* Without it, a consumer cannot
distinguish "assay errored" from "assay never ran", and the second is a
transport failure that must fail closed.

The physical output channel is the one exception nobody can wish away: if the
declared destination cannot be reserved, Assay cannot put an artifact there.
P21 detects that before running the lane, emits a stable
`OUTPUT_WRITE_FAILED` diagnostic/exit, and never invents a fallback path.
The reservation is descriptor-owned and leaves no persistent sibling temp
across consumer execution (A-181): it proves parent write access with a
create/remove probe, holds the observed absent-or-regular destination identity,
then revalidates and atomically replaces from a fresh temp after the lane. A
relative verdict path belongs to the CLI process cwd, not to the measured
project. An object that appears or changes after reservation is preserved and
the process exits ERROR; it is never overwritten to make emission succeed.

## 7. What assay must never become

| Creep | The line |
|---|---|
| **a test runner** | never discovers, selects, orders, parallelises or retries tests. Executes one declared argv and reads what it produced. Flags may be *appended by the caller* and are recorded verbatim (§6); they are never *derived* by assay. |
| **a CI system** | no scheduling, triggers, queues or webhooks. Impact-based lane selection belongs to the caller. |
| **a reporting dashboard** | no history, trends, storage, server or cross-run aggregation. One artifact, one lane, one commit. Anything longitudinal consumes those artifacts; assay never retains them. |
| **a coverage tool** | never instruments, traces or computes global coverage. Global/branch floors stay `coverage --fail-under`'s job. |
| **an LLM-mediated reviewer** | Tier 3 exists precisely so this stays out. A model dependency makes the gate non-deterministic, and a non-deterministic gate is not a gate. |
| **a policy engine** | Tier 2 applies a *declared threshold* to structured output. No expressions, rule DSLs or conditionals. If a lane needs a rule language, that rule belongs in the tool being adjudicated. |
| **an environment tool** | no container, network, image, instance or provisioning knowledge, permanently. `[…where]` is data assay parses and never interprets. |

## 8. Adoption: the ratchet argument

**Changed-line coverage is a ratchet, not an audit.** netcup-api-filter sits at
30.17% global line coverage and can still adopt `fail_under = 100.0` on
*changed* lines on day one, without writing a single test for legacy code — it
binds only what a diff touches. So "ciu/cmru/mdt need more tests generally"
does not gate adoption, and adoption is the mechanism that stops that debt
growing.

The line:

- **in scope for adoption:** declare `assay.toml`; swap the gate argv; verify
  the new verdict matches the old on the same commits; record the canary result.
- **out of scope:** writing tests to raise global coverage.
- **the bridge:** the canary result *at adoption* measures each project's floor.
  A project whose gate does not reject the import-break canary has a LAUNDERS
  finding — diagnostic output of adoption, and the ranked input to separate
  per-project remediation packages.

> **Adoption declares and verifies; it does not remediate.** Conflating the two
> is how adoptions stall — each consumer becomes an open-ended test-writing
> project instead of a bounded swap.

## 9. Self-hosting without circularity

If a bug made assay return `PASS` unconditionally, an assay-gates-assay setup
would sail through. **The independent oracle is pytest, not assay**: a suite of
fixture projects, each carrying its expected verdict artifact, asserted by
pytest — which knows nothing about assay's verdict logic. A universal-PASS bug
fails every fixture assertion at once. `assay verify` (canary against assay's
own lane) is a useful second layer but is **not independent**, and must be
documented as such rather than presented as the proof.

Bootstrap on a clean checkout is two ordered steps answering different
questions:

```
git clone && pip install -e .[test]
pytest                      # proves assay is RIGHT — no assay-as-gate involved
assay run --lane package    # proves this CHANGE is covered — uses assay
```

No circularity: the first establishes correctness, the second establishes
coverage of a diff. assay's own lane argv *is* `pytest`, so the gate
transitively re-runs its own independent oracle.

**One gated lane: `tester-unified`.** A `local` bare-pytest lane was considered
and rejected — *"greens from the interactive cockpit are explicitly not a ship
signal"*, and a cockpit lane manufactures exactly that pathway. The standalone
claim is instead proven **inside** the gated suite: a test builds a clean venv,
installs only assay, and asserts `assay run` works against a fixture project.
That is O1 discharged mechanically, with no cockpit-green pathway. Bare
`pytest` remains a documented developer convenience and explicitly **not**
evidence.

**Zero runtime dependencies** (stdlib only: `tomllib`, `json`,
`xml.etree.ElementTree`, `ast`, `re`, `subprocess`, `pathlib`, `argparse`) makes
that scratch-venv test trivially offline. assay consumes coverage.py's *output*
and never imports it; likewise pytest.

## 10. Fixture projects, and the Go toolchain constraint

Fixtures are hello-world projects carried in assay's own test data, each with an
expected verdict artifact covering all six outcomes and every `reason_code`.

- **Committed static data:** real source files plus **pre-generated** coverage
  artifacts (coverage.py JSON, cobertura XML, `go test -coverprofile` output).
  Parsing a profile and injecting a canary into Go source are pure text
  operations, so **assay's suite needs no Go toolchain** — mandatory here, since
  this devcontainer has none.
- **Runtime-materialised git fixtures:** dirty-tree, base-is-HEAD and
  merge-commit cases are `git init`'d into `tmp_path` at test time. Committing a
  git repo inside a git repo is the alternative, and it is worse.
- A documented regeneration script for a host that *does* have Go, so profiles
  are reproducible rather than mystery bytes.
- **A disposable copy of real srdm is the abstraction proof (A-159).** Tiny
  modules keep parser failures legible; P26–P28 additionally run the installed
  product over selected changes in `shared-ramdisk-depot-manager` and compare
  R1 with its independent `tools/covergate`. This validates a real module,
  repository nesting and package topology without migrating or editing srdm.

**Go stays out of the devcontainer.** The cockpit currently has no Go toolchain
at all, so — in srdm's Dockerfile's own words — *"it cannot even pretend"* to
produce a ship signal. Adding one would manufacture the cockpit-green pathway
§9 exists to close, and would create a version that can drift from the build
container. Instead, srdm's `gate/Dockerfile` `unit` target (generic:
`golang:1.25`, run-uid identity, prewarmed std cache, `GOTOOLCHAIN=local`,
caches outside the bind mount) is promoted to `vbpub/tester-unified-go`; srdm's
`e2e` target (systemd-in-Docker, privileged — genuinely srdm-specific) layers on
top of it; and a `tools/go` wrapper gives local ergonomics against that image's
*pinned* version. `tester-unified` is untouched, as its own reasoning demands.

## 11. Where language-specificity actually lives

The core is language-free. Everything language-bound is confined to leaves, and
the split runs along **two independent axes** — which is why parsing does not
live in the adapter.

**Format and language are not the same axis.** TypeScript emits lcov *or*
cobertura *or* Istanbul JSON. lcov is emitted by C, C++, Rust, PHP and
TypeScript. Python emits coverage.py JSON *or* lcov *or* cobertura. Binding a
parser to an adapter copies the lcov parser into every adapter that can emit
lcov — the four-copies divergence, one layer down. So: **a parser registry keyed
by format, and a `LanguageAdapter` keyed by language.**

The registry's output type carries one distinction the current copies lack:

```
FileCoverage(executed, missing, excluded: frozenset[int] | None)
```

`None` ≠ empty set. coverage.py has `excluded_lines`; a Go cover profile has no
such concept. Without the distinction, a Go lane reports *"0 changed lines
excluded by pragma — verified"* when the format simply cannot say. That is the
NO-MEASUREMENT discipline one level down, and it falls out for free.

Adapter surface (pure where it can be — nyxloom's `inject_*` currently writes
the file; in assay they return text, so adapters are testable with no
filesystem):

```
name, source_globs, excluded_dir_names, requires_span_attribution, external_tools
is_test_path(rel)                      has_executable_code(rel, text)
normalize_coverage_key(key)            statement_spans(text) -> spans | None
inject_import_break(text)              inject_uncovered_line(text)
generate_mutants(text, lines) -> mutants | UNSUPPORTED
```

`UNSUPPORTED` must render as **`INCONCLUSIVE_NO_MUTANTS`, never green** —
TESTING-METHODOLOGY already names that outcome for zero selected mutants, so an
adapter that cannot mutate inherits an established third outcome rather than
inventing one.

Adapters **may** shell out (Go's `has_executable_code` genuinely needs to parse
Go), but must declare it in `external_tools` so a lane's prerequisites are
checkable up front; a missing tool is `NO_MEASUREMENT` for that check, never a
guess. Note srdm's asymmetry, learned the hard way: a wrong `true` causes a
**false failure** (its first run flagged 94 lines across four comment-only
`doc.go` files), a wrong `false` causes a **silent excuse**.

Path normalisation splits across both axes and belongs on both sides: the
prefix-boundary reconciliation (topos's fixed `_rel_to_source`) is universal and
lives in the core; the language-specific prefix strip (Go's module path,
srdm's `stripModulePrefix`) is an adapter hook.

## 12. Lane file structure = D7's three questions, literally

The file's shape carries the boundary rather than asserting it in prose. Top
level is **WHAT** (the project's own declaration), `[…judge]` is **HOW** (assay
reads it), `[…where]` is **WHERE** (an environment tool reads it). A project
adopting only assay writes no `where`; one adopting only ciu writes no `judge`.

```toml
schema_version = 1

[lanes.package]
scope = "S1"; rigor = ["R0","R1","R2"]; enforcement = "gate"
argv = ["pytest", "tests/unit", "-q", "--cov-report=json:cov.json"]
env = { MOCK_MODE = "true" }
env_passthrough = []
budget = "5m"
allow_argv_append = false

[lanes.package.judge]
language = "python"
source_roots = ["libs/common/src", "applications/controller/src", "scripts"]
fail_under = 100.0
allow_excluded = false
coverage = { format = "coverage-py-json", artifact = "cov.json" }
mutation = { jobs = 4, max_mutants = 200, operators = ["compare-swap","boolop-swap","bool-const-flip","falsy-swap"] }
canary = { mechanism = "uncovered-line", target = "libs/common/src/pkg/mod.py" }

[lanes.package.where]
service = "test-runner"; instance = "worktree"
```

**Declared rigor is enforced, not merely recorded.** `R1` makes all five of
`judge.{coverage, fail_under, allow_excluded, source_roots, language}` required
to load; `R2` additionally requires `judge.mutation`; `R3` additionally requires
`judge.canary`. A lane claiming R1 with no coverage config fails at parse time.
Each of those three sub-tables is CLOSED, and each is cross-checked at load
time against the vocabulary its own module owns: `coverage.format` against
`assay.coverage.FORMAT_REGISTRY` (A-068), `mutation.operators` against
`assay.mutation.MUTATION_OPERATORS`, and `canary.mechanism` against
`assay.canary.CANARY_MECHANISMS` (P19). `canary` declares exactly one
`mechanism` and one project-relative `target`, never a plural list — one R3
claim is one mechanism execution, because the verdict contract carries a
single canary payload (v3 and planned v4 alike) and collapsing several results into it would report a judgement
nobody made. **A declared `uncovered-line` canary only ever reaches its own
expected reason on a lane that also declares R1** (A-150): `UNCOVERED_LINES`
comes from the R1 evaluation and from nowhere else, so an R0+R3 lane can
report that mechanism as having survived and never as having been caught.
**Scope stays an unverifiable declared claim** — assay cannot check S1-vs-S2 —
but naming it is precisely what made dstdns's gap visible, so it is required and
honest about being a claim.

**Every lane declares R0 (A-154).** R2 and R3 execute variants of the declared
command and therefore have no honest meaning without its baseline identity;
making that baseline implicit would omit a claim Assay actually ran. R1/R2/R3
remain independently selectable after R0. An `uncovered-line` R3 lane also
declares R1 because only R1 can produce its expected `UNCOVERED_LINES` cause.

**One budget covers the lane (A-160).** The singular `budget` spans snapshot
construction, baseline, evaluation, every mutant, and both canary halves; it is
not reset per subprocess. `max_mutants` is the independent deterministic work
cardinality ceiling. Repeated executions start from the resolved commit's
tracked Git objects with the complete repository topology and project prefix
(A-161), never a working-tree copy containing ignored stale evidence. Ignored
or untracked files are not implicit inputs.

Note the consequence, because it is easy to get backwards: an **R0-only lane has
no `[judge]` table at all**, and therefore declares no `source_roots`,
`fail_under` or `allow_excluded`. Those five are conditionally required, not
unconditionally required. A-018's "required per-lane field" means *per-lane
rather than a global CLI flag* — it does not mean "at the lane's top level".

**Closed vocabularies**, so a loader can reject rather than guess:

| Field | Values |
|---|---|
| `scope` | `S0` `S1` `S2` `S3` `S4` (TESTING-METHODOLOGY §Axis 1) |
| `rigor` | list of `R0` `R1` `R2` `R3` (§Axis 2) |
| `enforcement` | `gate` `advisory` |
| `judge.coverage.format` | a key the parser registry knows (§11) |
| `budget` | a duration string, **parsed at load**, not merely present — a malformed budget discovered at run time is a failure the config layer could have caught |

**`source_roots` are relative to the directory containing `assay.toml`** — the
project root, not the repo root. The lane file must not need to know where it
sits inside a monorepo, and a project that gets vendored one level deeper should
not silently start measuring nothing. Reconciling that against git's own
spellings (`git diff --relative` is cwd-relative; `git status --porcelain` is
always repo-top-relative) is exactly what the core's prefix-boundary normaliser
is for — see §11, and note that nyxloom's copy routes status paths through the
normaliser for this reason while dstdns's does not.

**A lane file declares what exists, not a target architecture.** dstdns's
methodology table names five lanes; its own honest-state paragraph records that
only two are declared and that `package` runs at S1, not S2. So dstdns's file
carries **two** lanes and the other three stay in the doc until they are real.
That is the honest-state discipline mechanised rather than restated.

**Matrix lanes are two lanes, not a `matrix` field.** netcup-api-filter's
`[gates.unit]` runs the same argv against `test-runner:local` and
`test-runner:py39` in a bash `for` loop. They have genuinely different WHERE and
can render different verdicts; a loop that hides which image failed is the same
opacity being removed.

## 13. Adoption order, and what each consumer proves

Before consumer migration, P24 qualifies the versioned installed wheel
against a disposable current Topos tree and its independent changed-line gate
(A-162). That is evidence that an existing Python project can obtain the same
R1 answer, not a claim that Topos has adopted Assay. The real adoption package
is carved later in Topos's own trove, after its active wave permits a stable
input revision.

| # | Consumer | Proves |
|---|---|---|
| 1 | **topos** | **faithful replacement, mechanically** — the only migration where old and new gate run side by side on the same commits and are required to agree. Its two unique behaviours must survive, so "did we take the union correctly?" stops being a review question |
| 2 | **ciu** | deletes the `PYTHONPATH=../nyxloom/src` cross-project hack; first R3 consumer, since its gate already declares `canary-verified` |
| 3 | **dstdns** | the elaborated copy, five source roots; `webapp-ui-react` is the eventual TypeScript forcing function. Its 255-failure narrowing must not be perturbed |
| later | **netcup-api-filter** | standalone adoption in the lived sense, plus **cobertura** (it emits `coverage.xml`, not coverage.py JSON) and three lanes at three scopes |
| later | **cmru**, **mdt** | near-mechanical after ciu; mdt unconfirmed (no `tests/` tree found) |
| separate | **srdm** | Go adapter validated by fixture; srdm's own migration is an independent decision hinging on whether Python enters its toolchain |

topos precedes netcup because netcup has no coverage gate today, so a correct
verdict and a plausible one are indistinguishable there — no baseline. Doing
topos first means later adoptions are backed by a tool already proven faithful,
rather than being where you discover it is not.

**"Adapter validated" ≠ "consumer migrated."** The P90 handoff conflates them.
The Go adapter is proven against committed fixtures; srdm migrating is a
separate call.

---

## Appendix — arguments considered and rejected

| Proposal | Rejected because |
|---|---|
| `lanes.toml` (tool-neutral filename) | ciu reads nothing today; `ciu test` is a sketch, explicitly *"does not exist today"*. Naming for an absent consumer is the same "implies capability it does not have" failure, applied to a filename. Neutrality is preserved in the *schema* (`[…where]`), not the name. |
| `pyproject.toml [tool.assay.lanes]` | srdm is Go and has no `pyproject.toml`; fails language-independence on day one. |
| Merge assay into ciu | §4 — the container boundary. Ergonomics are identical either way. |
| ciu declares assay an optional extra | D7 explicitly forbids ciu importing the testing library; an optional import is still a link that hardens. |
| `env` augments the ambient environment | Incoherent at S3, where the container already does not inherit it. Same lane file would mean different things at different scopes. |
| A `local` bare-pytest gated lane | Manufactures a cockpit-green pathway. The standalone claim is proven *inside* the isolated gate instead. |
| Go toolchain in the devcontainer | Same cockpit hazard, plus version drift from the build container; unblocks nothing (fixtures ship pre-generated). |
| Keep `_derive_test_command` | §4.2a anti-pattern #2. The lane declares the argv. |
| Flat verdict with optional detail blocks | *"R2 declared but rendered no judgement"* becomes indistinguishable from *"R2 never declared"*. |
| Fold `BUDGET_EXCEEDED` into `ERROR` | A slow lane and a broken lane get the same code and the same retry policy. |
| Rename `NO_MEASUREMENT` to `DIRTY_TREE` | Names only one of its three causes; `BASE_IS_HEAD` is ref resolution, not tree state. A required `reason_code` gives the specificity without proliferating exit codes. |
