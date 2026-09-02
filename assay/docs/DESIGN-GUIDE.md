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

The lane declares Tier-3 input under HOW, not as another rigor level (A-209):
`judge.attestation_dir` names one contained project-relative input directory
and `judge.evidence` is the ordered closed list of `(source,key)` identities.
The two fields are both present or both absent and no location is derived. This
pair is legal even on R0-only because external evidence and computed rigor are
separate axes; it does not make R0 consume coverage/mutation/canary policy.

Asynchronous `PENDING` evidence is deferred until claim-level enforcement is
designed. It is not a seventh outcome hidden inside an evidence entry.

## 3a. Why the rigor levels are not redundant — three techniques, three defect classes

The most common objection to R2 and R3 is "we already have 100% coverage". The
answer is not that coverage is bad; it is that **the three techniques detect
disjoint classes of defect, and none of them finds the others'.**

| technique | the question it answers | the defect it alone catches |
|---|---|---|
| **R1 — coverage** | did this line *run*? | code no test exercises at all |
| **R2 — mutation** | would anything *notice* if this line were wrong? | assertions that execute the right code and prove nothing about it |
| **R3 — canary** | does the harness *report* a failure it was given? | a suite whose failures never reach the verdict |

That middle row is the one consumers underestimate, so it is worth a measured
example from this estate's own tooling rather than a hypothetical. A release
tool's change set reached a passing suite at **100% statement and branch
coverage** and its mutation campaign still found **six surviving mutants** —
one of them in the single line deciding whether a pinned version was stale.
Coverage was never wrong; it was answering a different question.

**Two shapes account for most survivors, and one of them is not a test defect.**

**A weak assertion.** Asserting an exception's *type* proves nothing when two
different causes raise the same type — a mutant that skipped one `raise` fell
through to a second, raised the same class, and the test passed. Assert what
distinguishes the causes, or arrange for the mutated path to *succeed* where the
correct path refuses.

**An equivalent mutant, which no test can kill.** Given

```python
if   pinned == highest:  ...   # current
elif pinned <  highest:  ...   # stale
```

the `elif` is reachable only when the values differ, so `<` and `<=` are
semantically identical there. No assertion can separate them.

**This is a defect in the code's shape, not a gap in the tests, and it is the
exact mirror of A-124/A-131.** There, a branch that cannot fire is a defect
wearing thoroughness as a disguise. Here, an *operator that cannot matter* is
the same defect — a redundant guard has made the comparison non-discriminating.
The repair is to restructure so every operator discriminates (lead with `<`,
then `>`, let equality fall to `else`), after which the ordinary equality test
kills both mutants. Coverage cannot see either defect; only mutation can.

This is also why `ALL_MUTANTS_EQUIVALENT` is a loud `INCONCLUSIVE` terminal and
not a pass: a run in which every mutant was provably inert has told you nothing
about your tests, and saying so is the only honest available answer.

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

This boundary remains one-way by construction. A lane may declare an optional
`environment_command` probe: `assay run` executes that zero-exit argv in the
invoking environment *before* repository, snapshot, or lane work and refuses on
failure. The probe lets a lane name "this command is meaningful here" without
giving assay container mechanics or letting a wrong dependency closure
masquerade as a product failure.

The refusal keeps ONE distinction (B032/A-321): a probe that exhausts its own
preflight cap reports `BUDGET_EXCEEDED`/`LANE_TIMEOUT` (exit 4), because gates
routinely retry a timeout and hard-fail a config error, and collapsing the two
makes them do the wrong thing on a real timeout. Every other probe failure -- a
missing binary, a nonzero exit, a signal death -- means the same actionable
thing and keeps rendering `ERROR`/`BAD_LANE_CONFIG` (exit 2). What separates
those is written to stderr as free text rather than widening the closed
reason-code vocabulary (A-138/A-170): the refusal names the lane, the cause, and
the declared wrapper to run via, which is what B010 asked for and what
`8a2a4731` shipped as 0 bytes. The cap itself is `runner.PROBE_BUDGET_SECONDS`
(30 s), applied as `execute_plan`'s `timeout=` argument -- the value it actually
reads -- so a hung probe can no longer spend the lane's whole declared budget
before the lane's own command starts.

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
`SF:` → lcov, `<coverage` → cobertura, `"statementMap"` →
`coverage-istanbul-json`) and **refuses on mismatch**. A lane whose
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

**`env_required` is the subset whose ABSENCE refuses the lane (A-254).** A
passthrough name that is not set is silently dropped — `env_effective` copies a
name only when the source has it — which is correct for an optional input and
actively wrong for an *identity*. A lane that passes a container image revision
or an instance id through in order to RECORD it in the artifact otherwise emits
a clean `PASS` carrying no identity at all, and nothing anywhere says so: the
absence of a value read as the absence of a requirement, which is A-025's rule
broken one layer down.

Naming a passthrough variable in `env_required` makes it a precondition. The
refusal is `ERROR`/`BAD_LANE_CONFIG`, decided **before any Git work**, so a
dirty tree cannot mask a missing identity; a name in `env_required` that
`env_passthrough` does not declare is refused at *load*, because no environment
could ever satisfy it. This is what lets an environment invariant hold by
construction rather than by hope:

```toml
schema_version = 2

[lanes.integration]
scope = "S3"
rigor = ["R0"]
enforcement = "gate"
argv = ["pytest", "tests/integration", "-q"]
env = {}
env_passthrough = ["PATH", "CIU_INSTANCE_ID", "CIU_IMAGE_REVISION"]
env_required    = ["CIU_INSTANCE_ID", "CIU_IMAGE_REVISION"]
budget = "20m"
allow_argv_append = false
```

Both names then appear verbatim in the artifact's `env_effective` on **every**
outcome where the lane resolved, refusals included (A-036), so *which
environment produced this verdict* is answerable from the artifact instead of
asserted by whoever ran it. Two caveats that matter:

* **`env_effective` is recorded, not verified.** assay copies the value; it does
  not compare it against anything. It is transparency, not a claim — see §3 on
  what makes something evidence.
* **Pass identities through, never secrets.** Everything in `env_passthrough`
  that is present lands in the artifact in cleartext. That is the point for an
  instance id and a disaster for a token.
* **(B025) One exception, flagged rather than silent.** A refusal whose OWN
  cause is an unresolvable infrastructure declaration cannot safely record
  the real `env_effective` — neither the infrastructure fact nor any
  `env_passthrough` name could be completed — so it falls back to exactly
  `lane.env` (a true, if partial, subset) and sets a sibling top-level
  `env_effective_incomplete: true`. Every other verdict, including every
  other refusal, implicitly means `false`; this is the one case where
  "every outcome where the lane resolved" is honest about `env_effective`
  only alongside that flag.

### A whole-target `target` names a regular file, never a directory (A-260)

B005's whole-target judge (§6) applies this same doctrine one layer down, and
the applied form is stricter than `source_roots` because the failure mode is
worse. `source_roots` fails loudly at load time when it names a path that does
not exist; a `whole_target` `target` must additionally refuse the shape that
*does* exist but silently under-measures.

A draft of B005 let a target name a directory, expanding it to every
adapter-recognised source file beneath it, with the anti-vacuity guard applied
to the expansion rather than to each declared file. That is anti-pattern #2 —
the *consumer* (the expansion) inventing coverage for files it never actually
checked are measured. Concretely: a directory expanding to 36 files, of which
one appears in the coverage artifact, **passes** — the other 35 go silently
unjudged, which is `--cov`'s own vacuity hole with a first-class judge wrapped
around it, precisely the hole B005 exists to close. The rule was withdrawn
before shipping (see `nyxloom-trove/W1-CARVE-branch-coverage-and-whole-target.md`
§5's Declaration bullet, kept struck through rather than deleted, so the next
proposal to relax this starts from why the last one failed rather than from a
blank page).

What ships instead: `evaluate._resolve_whole_target` refuses a non-regular-file
target — a directory, a symlink, anything but a tracked regular source file
under a declared source root — with `ERROR`/`BAD_LANE_CONFIG` naming it. A
target present in `targets` but absent from the coverage artifact, or present
with zero executable lines, is `NO_MEASUREMENT`/`TARGET_NOT_MEASURED` rather
than a vacuous 0/0 PASS — the anti-vacuity guarantee that is the entire point
of B005: **every declared target is either measured, or the lane refuses**,
never silently absent from what was judged. The accepted cost is real: a
consumer owning 25 modules names 25 paths, and a file somebody adds is not
judged until it is declared — the honest failure direction, since an
undeclared file is visibly missing from `targets`, where an unmeasured file
under directory expansion was invisibly present.

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

The six outcome categories were not invented—each already existed as a concept
somewhere in the estate. V4 adds reason names only where a now-reachable fact
would otherwise collapse into a different category. **2–5 are all not-a-pass
and all block a merge**; the split drives diagnosis and retry policy (a CI
system may retry 4; it must never retry 3).

A required `reason_code` names the cause without proliferating exit codes. The
enumeration is **closed** — an implementer that needs a code not listed here
must stop and ask, never invent one:

| Outcome | `reason_code` |
|---|---|
| `PASS` | the key is **omitted**, not null. A pass has no cause to name. |
| `FAIL` | `UNCOVERED_LINES`, `UNCOVERED_BRANCHES`, `EXCLUDED_LINES`, `UNCLASSIFIED_LINES`, `MUTANTS_SURVIVED`, `CANARY_SURVIVED`, `COMMAND_FAILED` |
| `ERROR` | `GIT_FAILED`, `UNREADABLE_ARTIFACT`, `FORMAT_MISMATCH`, `BAD_LANE_CONFIG`, `EXEC_FAILED`, `OUTPUT_WRITE_FAILED`, `MUTATION_DISCOVERY_FAILED` |
| `NO_MEASUREMENT` | `DIRTY_TREE`, `HEAD_CHANGED`, `BASE_IS_HEAD`, `EMPTY_COVERAGE`, `BRANCH_UNAVAILABLE`, `TARGET_NOT_MEASURED`, `MISSING_ATTESTATION`, `STALE_ATTESTATION`, `MISSING_EXTERNAL_TOOL` |
| `BUDGET_EXCEEDED` | `LANE_TIMEOUT`, `MUTANT_LIMIT_EXCEEDED`, `SNAPSHOT_LIMIT_EXCEEDED` |
| `INCONCLUSIVE` | `NO_MUTANTS`, `MUTATION_UNSUPPORTED`, `CANARY_INCONCLUSIVE`, `ALL_MUTANTS_EQUIVALENT` |

**(B026 N-4, decided 2026-08-25) A refusal's diagnosis is `reason_code`
alone — never a free-text field — and that is deliberate, not an
oversight, even though it produces a three-way asymmetry a consumer must
know about:**

- `assay run` refused **before** output reservation (a bad `--operators`
  value) prints a one-line diagnostic to stderr and writes **no** artifact
  (A-181).
- `assay run` refused **after** output reservation but before the command
  runs (a bad `--shard`, an unresolvable infrastructure declaration) writes
  a real, schema-valid artifact and prints **nothing** to stderr — the
  artifact's `reason_code` is the only cause a consumer gets.
- `assay plan`'s equivalent refusals still raise `LaneConfigError`, printed
  via `main()`'s own handler — a third shape again.

No single invocation gives a consumer both the exit code and a free-text
cause. Closing this would mean either widening the closed `ReasonCode`
enum above (a **deliberate**, non-quick decision per A-138/A-170 — every
consumer's own schema copy would have to accept the new member) or
plumbing a detail string out of `run_lane`, whose public return type is
`Verdict` alone, through every caller that treats it as such. Both are real
API commitments, not one-line fixes; a consumer that needs the cause
today already has it — the reason vocabulary above is closed precisely so
every member is a real, previously-decided fact, and `BAD_LANE_CONFIG`
already says enough to know which lane-declaration class of problem this
is even without the free-text string a `LaneConfigError` would have
carried.

### Bounded command-output tails

A non-PASS terminal that captured process output may carry four optional
top-level fields: two 64 KiB UTF-8 tails (`result_stdout_tail`,
`result_stderr_tail`) and their paired head-side dropped-byte counts. Empty
tails mean "captured and empty"; absent tails mean no command-output evidence
applies. The bound is measured after decoding because `subprocess.run(text=True)`
is the production boundary; undecodable bytes are already replacement characters
by then. This is diagnosis, not proof: claim status still comes from exit status,
declared artifacts, and the existing rigor pipeline.

**(B027) The one path `text=True` does not decode for you is a timeout.**
`subprocess.TimeoutExpired.stdout`/`.stderr` is `bytes`, not `str`, even under
`text=True` — CPython does not run the exception path through the same
text-decode step a normal `communicate()` return gets. The timeout handler
tolerantly decodes (`errors="replace"`, matching the policy above) before
building the tail, so a mutant-induced timeout reaches `BUDGET_EXCEEDED`/
`LANE_TIMEOUT` with a real verdict artifact rather than an uncaught
`AttributeError`. A caller reserving `--verdict-json` must still check the
invoking `assay` process's own exit status, not artifact presence alone, to
tell a fresh terminal from a stale one left by an earlier run at the same
path — a non-zero exit means whatever is on disk did not come from this
invocation.

**(A-277) `ALL_MUTANTS_EQUIVALENT` was missing from this table from the moment
v5 introduced it (A-223d) until wave 2 found it.** It fires when `killed +
survived == 0` while `equivalent` is non-empty — every mutant the analysis
produced turned out to be semantically identical to the original, so the suite
was never given anything to catch. That is not a pass: rendering it green is
A-026/A-035's 0-of-0-is-100% bug, which is why it has its own terminal rather
than folding into `NO_MUTANTS`. The two differ in what they say about the
analysis — `NO_MUTANTS` means discovery found no site to mutate, this means it
found sites and every one of them was inert.

The omission is worth recording rather than quietly fixing, because this table
declares itself **closed** and tells implementers to stop and ask for anything
not listed — so for two schema versions it was authoritative and wrong. A-270's
vocabulary check (§16) originally derived four vocabularies and not this one;
`test_every_reason_code_is_documented` now closes that, which is the only
reason the next omission will be caught by the gate instead of by a reader.

**(wave-1) Three additions, each a distinct new terminal, not a repurposed
old one.** `UNCOVERED_BRANCHES` ranks identically to `UNCOVERED_LINES` in the
outcome precedence but is never the same sentence: "which mechanism refused"
is the distinction this project exists to keep, one layer up from B001's own
false-PASS story. `BRANCH_UNAVAILABLE` and `TARGET_NOT_MEASURED` are
`NO_MEASUREMENT`, not `ERROR` — the lane is well-formed and the command ran
cleanly; what is missing is measurability of a *declared* thing (branch data,
or one named target), the same category `DIRTY_TREE`/`BASE_IS_HEAD` already
occupy. See the two subsections below for why each exists.

The outcome set stays fixed while the reason vocabulary grows only for named,
reachable terminals. The field is required on every non-PASS outcome, so a
consumer switching on `reason_code` never has to special-case an outcome that
lacks one.

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
zeroed.** Emitting `{"covered": 0, "executable": 0, "pct": 100.0}`
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
claim true. P22 lands the committed-object snapshot substrate those executions
run on (below); P23 moves baseline/repeated execution onto it, which is the
boundary that also removes ignored/untracked inputs from those executions.

**A snapshot is proven leaf-for-leaf before yield.** The committed manifest is
the write contract: after materialization, every non-omitted regular-file entry
must exist as a regular file and every symlink entry as a symlink. This is in
addition to clean status and index-tree equality; it catches a future writer
regression even for a path Git status could not report. Untracked caller residue
such as `__pycache__/` is not part of the commit and therefore never enters the
snapshot.

### A replaced output directory is named, not folded into EMPTY_COVERAGE

**(B049/A-408.)** P20's held-parent-descriptor discipline above buys the
TOCTOU cover that a re-open by name cannot, and it has one blind spot: a
tool that does `rm -rf <dir> && mkdir <dir> && write <artifact>` — Vitest's
own default `coverage.clean = true`, and a common build-step convention far
beyond it — writes a complete artifact into a *different* inode that merely
answers to the same path. The held descriptor still refers to the orphaned
original, so `consume()`'s basename lookup raises `FileNotFoundError` and
returns `None`. Every caller reads that `None` as the truthful absence
above: `NO_MEASUREMENT`/`EMPTY_COVERAGE` for a coverage read, a `crashed`
mutant for `mutation.py`'s absent read. Both messages then assert a
checkable falsehood over an artifact that was complete on disk at the
declared path the whole time — the exact "a check is only as strong as what
it actually compares" shape AGENTS.md names, one layer down: the *absence*
being reported is real, but the *conclusion drawn from it* is not.

The fix is one `fstat` on a descriptor the reservation already owns:
`st_nlink == 0` is the exact, name-free witness that this directory inode
has been unlinked while an open descriptor keeps it alive. It is checked at
`consume()` time, so it costs nothing during the command and, unlike a
re-open by name, adds **no new TOCTOU surface at all** — which is why it,
rather than B049's option (1), is what ships. The refusal is
`ERROR`/`UNREADABLE_ARTIFACT`, a value the frozen schema already reserves
for "an object that exists but cannot be trusted … or a race the caller's
own reservation already detected": no new reason code, no schema change
(A-138/A-170).

Because the check lives in `OutputReservation.consume` itself, every
reserved artifact inherits it by construction — the coverage read, the SQL
R2 `equivalence_artifact`, the ingested mutation report, and `mutation.py`'s
per-mutant equivalence and kill-signal reads. Rejected alternatives, from
B049's own list: re-opening the parent chain by name (loses the P20 cover
this module exists to provide), and documenting the constraint only (which
says nothing about tools other than Vitest that share the convention). The
`clean: false` guidance stays in README/CONSUMERS as a *recommendation* —
it keeps the lane on the fast path — but forgetting it is now diagnosable
rather than silently mis-diagnosed.

### Every refusal says WHY, exactly once, through one emitter (B053/A-409)

The closed `(outcome, reason_code)` vocabulary is a wire contract and stays
one (A-138/A-170): a consumer switches on it without parsing prose, and no
free-text field is added to the document for this. But every layer that
refuses already *composes* a sentence — which artifact, which declaration,
which target — and that sentence was thrown away at the moment the
`AssayError` became a refusal `Claim` or `Verdict`. The operator was left
with `ERROR`/`BAD_LANE_CONFIG` and a guess.

`runner.announce_refusal(exc, *, diagnostics)` is the one emitter. It writes
exactly `assay: {outcome}/{reason_code}: {message}` and nothing else, to the
caller's own `diagnostics` stream — the same stream `environment_command`'s
probe refusal (A-322) and B033's whole-target note already use. `assay.cli`
passes its own stderr, so the CLI gets the line for free; a library caller
passes its own stream and assay never touches the process's stderr.

**Why one emitter called at ~15 conversion sites, and not one `try` at the
CLI boundary.** The obvious shape — wrap the `run` command in a handler and
print whatever escapes — cannot see the defect. `assay.runner` converts an
`AssayError` into a refusal claim or verdict at ~15 places and RETURNS a
document; almost nothing propagates to the CLI. A boundary handler would
therefore print for the handful of structural errors that DO escape (which
already printed, from `cli.py`'s own two prints) and stay silent for exactly
the refusals B053 filed. Both of those CLI prints are now calls to the same
emitter, because two spellings of one line is how the two drift apart.

**Exactly once.** The call belongs where the error becomes a claim or a
verdict, not where it is caught and stored: a coverage-read failure on a lane
that declares no R1 never becomes a claim at all, and announcing it would be
a line with no document behind it. Where one error refuses two levels — an R2
orchestration fault also refuses R3 — it is announced at the first only.

**The emitter's contract is a MESSAGE, not an exception (DA-R3/A-414).** The
first landing of this rule left five sites out — `DIRTY_TREE` (twice),
`HEAD_CHANGED`, `MISSING_EXTERNAL_TOOL`, the `env_required` refusal and the
bad-`--shard` refusal — because each called `refuse_lane`/`refuse_all` with a
bare `(status, reason_code)` literal and there was no `AssayError` whose
message could be copied. That reasoning was backwards. Every one of those
sites *holds the fact* at the moment it refuses: the offending paths from
`git.dirty_paths`, the two disagreeing revisions, the tool that is not on
`PATH`, the unset variable, the spec the operator typed. Composing the
sentence there is composing it at the point of knowledge, which is what §5
asks for; the thing §5 forbids is inventing text somewhere the fact is *not*
known. Each of the five now builds an `AssayError` for the message and hands
it to the same emitter, so **every refusal reachable through `assay run`
prints exactly one line, without qualification.**

**A line about a refusal the verdict does not carry is worse than silence
(DA-R4/A-414).** One early-R2 refusal is decided before the lane's own
command outcome is known: the baseline declared an `equivalence_artifact` and
did not write it (A-279). If the command then FAILED, claim assembly discards
that early claim in favour of the command's own — so announcing at the point
the early claim is built printed a sentence a consumer could not reconcile
with the document in its hand. The announcement is deferred to the point
where the surviving claim is CHOSEN. Deferred for that one site only: every
other early-R2 refusal is decided inside the `result.outcome is PASS` guard
and cannot be superseded, so no general deferred-print buffer exists — a
buffer would make "announced exactly once" a property of a mechanism instead
of a property of each site.

The per-claim `detail` field (DA-D2 (c)) is a separate, later decision: it
puts this text ON the wire, which is a schema change and belongs to a cut.

### A self-contradictory coverage record is one FILE's defect (B054/A-410)

A-405 established the rule for a Go file whose `//line` directive makes its
recorded positions virtual: refuse it if the lane judges it, ignore it if the
lane does not, because code outside the judged set is invisible to the
verdict by construction. B054 is the same rule reached from JavaScript.

`@vitest/coverage-istanbul` statically instruments every file a
`coverage.include` glob matches, including files no test imports, and for an
ordinary braceless single-statement `if` it can write a `branchMap` arc on a
line the same record's `statementMap`/`s` never classifies. Refusing the
whole artifact for that made ONE never-executed file cost every other file's
correct data — the exact inverse of what `changed_lines` mode promises a
consumer adopting coverage incrementally.

**Where the fix could NOT follow A-405, and what that forced.**
`line_directive_remapped` is a DERIVED property: it reads `blocks`, which
every rebuild carries, so a stored flag could never disagree with its
records. A contradicting arc has no such anchor — the arc must be dropped
(`FileCoverage` refuses construction otherwise) and once dropped there is
nothing left to derive from. So `contradictory_branch_lines` is STORED, and
the two rebuild sites in `statement_attribution` carry it explicitly, with a
comment saying why a rebuild that forgot it would launder a defective record
into a clean one. The rejected alternative was a `(profile, defects)` parser
return: it changes the signature every format's parser shares in order to
carry a fact only one format can produce.

**`branches` stays a `BranchCoverage`, never `None`, even when every arc was
dropped.** `None` means "this FORMAT cannot express arcs at all" (A-008).
Saying that about an istanbul artifact would be false, and would silently
change the profile's branch capability.

**No wire field.** The not-judged set is invisible by construction, so an
`excluded_files` list on the verdict would put a fact on the wire that no
consumer asks for and that the north star says is not part of the judgment.
The file stays nameable in three places instead: the diagnostics line, the
refusal message when it IS judged, and the field itself.

### A lane that runs out of time still produces a document (B028/A-415)

`LaneDeadline.remaining()` raises `BUDGET_EXCEEDED`/`LANE_TIMEOUT` the moment
the lane-wide budget is spent, and it is the ONE seam every timing check in
this codebase reads through — about sixteen call sites across `runner.py`,
`mutation.py` and `canary.py`. A raise from any of them used to escape
uncaught on the direct-R0 dispatch, so a lane whose command simply outlived
`budget_seconds` exited 4 with **nothing written to a reserved
`--verdict-json`**: assay said "you ran out of time" on stderr and handed the
consumer no document saying it. That is the same defect B025 fixed for a
narrower cause, and P17's rule — every post-HEAD-resolution terminal path
emits a complete artifact — covers both.

**One outer catch per dispatch entry point, never one per call site.** The
sixteen timing checks are a moving target; the two entry points are not. A
wrapper per call site is a rule every future timing check has to remember,
and the ones added since B025 was written did not. `_run_higher_rigor_lane`
already had its boundary (a single `try` spanning base resolution, the
scratch root and the whole snapshot block) and was measured correct before
anything was changed; the direct-R0 tail now has the matching one.

**What exists at the moment of the timeout is the design question**, and the
answer is: whatever the command left. If the command already ran, its result
is real evidence — argv, timing, output tails — and the verdict keeps it,
carrying the refusing pair as the R0 claim. If the deadline expired before it
ran, there is no result to assemble from and `refuse_lane` builds the
complete refusal artifact exactly as every other pre-work refusal does.

**A timeout is never relabelled.** When cleanup after a completed run fails,
A-193/A-194 replace the highest higher-rigor claim with `ERROR`/`GIT_FAILED`.
If that cleanup failed *because the lane ran out of time*, `GIT_FAILED` names
a Git failure that did not happen and buries the one actionable fact. The
cleanup-failure path is unchanged in shape and now carries the refusing
error's own pair. Every other cause keeps `GIT_FAILED`.

### Mutation resume and sharding (B012)

Mutation state lives outside each ephemeral replacement snapshot. One bounded
JSON record per completed candidate is keyed by the same deterministic digest as
the plan; resume treats an absent record as pending and refuses a stale source
identity rather than sampling changed source with an old result. A stale
`schema_version` is the one required field NOT folded into that digest, so it
gets the opposite disposition (B021): treated as absent and silently rerun, a
routine format bump never fails the whole lane the way a genuinely tampered
record does. Shards assign by keyed digest of the candidate ID. Their merge is
a manifest-level set proof: exact index coverage, one schema/lane/commit/count,
and duplicate-free IDs—not bucket-count arithmetic.

### Infrastructure fact injection (B013)

Infrastructure declarations are resolved at the plan boundary, in the invoking
process, before repository or snapshot work. The two closed sources are the
ambient environment and rendered CIU state; both are explicit, bounded, and fail
loudly on absence, emptiness, or a malformed dotted path. Injection is
declared-only: an injected name cannot collide with fixed or passthrough names
— enforced both at lane-load time and, since B022, again at plan-resolution
time, so a `Lane` built without going through the loader cannot reach the
runtime unprotected — and no ambient value reaches the child unless the
infrastructure table names it. A resolved value is also bounded in length
(`MAX_INFRASTRUCTURE_VALUE_BYTES`, B022): a value this large would otherwise
fail late and opaquely at `E2BIG` on exec instead of refusing by name.

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
`env_declared` and `env_effective` on **every** outcome — with one flagged
exception (B025, §"Inject infrastructure facts" above): a refusal whose OWN
cause is an unresolvable infrastructure declaration records `env_effective`
as `lane.env` alone, paired with `env_effective_incomplete: true`, since
the real value could not be safely completed. A run with a non-empty
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

### Repeated execution runs on committed objects, not the working tree (A-161/A-184–A-187)

P22 lands `assay.isolation`, the substrate R2/R3 repeated execution is built
on. The claim it exists to make attackable: *assay can prepare one bounded,
inert, byte-faithful repository seed from a full commit and use it concurrently
for independent base and replacement repositories without ever returning to
consumer-controlled Git state.*

**Why the commit rather than the tree.** Copying the working tree carries
whatever happens to be lying in it — a stale coverage artifact from a previous
run, a build cache, a FIFO — and makes repeatability depend on facts the
recorded commit does not contain. Copying only `project_root` is worse: a
monorepo project whose tests read a tracked sibling passes in the real
repository and fails in every mutant. The snapshot therefore reconstructs the
**complete SHA-1 reachable closure of one full commit**, preserving repository
topology and project prefix, and nothing that is merely present on disk.

**Why a prepared seed rather than a snapshot function.** The obvious stateless
`materialize_snapshot(spec)` shape leaves exactly two implementations once full
history is required: re-pack the whole closure per repeated unit, or share the
source through an alternate or hardlink. The first multiplies cost by the
mutant count (the live vbpub closure measured 26,074 objects / 273,578,621
uncompressed bytes at the P21 anchor, behind a ~22 MiB pack); the second
destroys isolation. `prepare_snapshot` transfers once into an unexposed private
seed; `SnapshotRepository` then serves concurrent independent contexts from it.
Removing the source `.git` after preparation must change nothing — that is both
the security boundary and the performance property.

**Why raw objects rather than a checkout.** `git checkout` and `git archive`
both execute a committed `filter` driver, and checkout additionally runs hooks.
The carver's tracer witnessed exactly this: a real `git archive` executed the
hostile filter and still produced no private repository, while raw object
transfer executed nothing. Assay therefore parses raw tree objects itself
(`<mode> SP <name> NUL <20-byte-oid>`) and writes blob bytes directly. Supported
modes are exactly tree, regular, executable and symlink; a gitlink, any other
mode, a `.git` component, a duplicate path, a non-UTF-8 name, or a symlink that
does not resolve **lexically** inside the snapshot root is `ERROR/GIT_FAILED`
before any seed is exposed. Symlink containment is decided lexically on purpose:
`Path.resolve()` would answer about the host filesystem and would accept a link
that only escapes once its target exists.

**External and incomplete source topologies are refused, not worked around.**
Clearing ambient `GIT_ALTERNATE_OBJECT_DIRECTORIES` is insufficient, because a
repository-local `objects/info/alternates` file is repository *content* and
still participates. P22 refuses a non-empty or non-regular alternates file,
grafts, shallow, partial-clone/promisor, and non-SHA-1 stores, and adds
`GIT_NO_LAZY_FETCH=1` plus disabled commit-graph/multi-pack-index for this trust
boundary. An *empty* alternates file is ordinary and is not refused: the rule is
about content, not existence.

**Every bound is fixed and refuses its own limit+1** — objects, tree entries,
per-path bytes, total path bytes, blob bytes, total object bytes, and
transferred pack bytes — as `BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED`, never a
truncated or partially-runnable tree. Entry and total-path bounds are separate
from the object bound because one blob may be referenced by arbitrarily many
paths. The pack is relayed through assay so its compressed size is *counted*
rather than trusted, and `pack-objects` runs without `--revs` so the transferred
set cannot differ from the set assay inventoried.

**Replacement is repo-top-relative, whole-blob, and deterministic.** A
replacement path names a tracked regular blob in the prepared commit even when
`project_prefix` is not `.` — re-interpreting it under the project root is the
false-PASS this boundary is written against. `expected` is compared byte-for-byte
with the committed blob first; a mismatch, absent path or non-regular target is
`ERROR/MUTATION_DISCOVERY_FAILED`, because a frozen mutation site that no longer
names the syntax it claimed is a discovery failure, not a snapshot failure. The
child is written with `commit-tree` under a fixed `Assay <assay@invalid>`
identity at 946684800 +0000 with fixed message bytes, so identical inputs give
an identical OID on any host and under any consumer's ambient author. All
materialized paths get fixed modes and a fixed mtime for the same reason: git
records none of them, so anything taken from the clock would make two snapshots
of one commit differ.

**The lane budget stays singular (A-160).** Every P22 call takes the caller's
current *remaining* lane seconds and converts them once into one internal
monotonic deadline; there is no default or unbounded timeout, and expiry kills
the process group assay owns and renders `BUDGET_EXCEEDED/LANE_TIMEOUT`. P23
prepares once per lane and passes remaining seconds before every call, so
neither package can silently reset the budget.

### Higher rigor consumes the prepared seed; it never re-derives it (A-188–A-196)

P23 is the sole caller of `assay.isolation` for R1/R2/R3 lanes. `run_lane`
resolves one `CommandPlan` and one `LaneDeadline` (A-193) before it opens
`isolation.prepare_snapshot`, then materializes one baseline repository plus
one independent repository per mutant and per canary half from that single
prepared seed — direct R0-only lanes never touch P22 and keep the pre-P22
live-tree path (A-189). There is no fallback in either direction: a lane that
declares R1, R2 or R3 cannot silently run against the live tree just because a
snapshot bound was tight, and a direct lane is never routed through P22 for
"extra" rigor it did not declare.

**Snapshot data is addressed by path, never by OID (A-191).** Mutation source
bytes are read through `SnapshotRepository.read_regular_file` at the tracked
repo-top-relative path, and whole expected/replacement blobs are handed to
`materialize_replacement` the same way — never through a cache keyed by blob
OID. Two paths that happen to share one blob remain two independent reads and
two independent replacements; collapsing them by content would silently drop
one path's mutant.

**A disposable snapshot is not a license to leave dirt (A-195).** Every unit
whose result is used — baseline, each executed mutant, and both canary
halves — is checked once for uncommitted changes and, on a clean tree, for a
moved `HEAD`, against that unit's own expected commit. `DIRTY_TREE` and
`HEAD_CHANGED` stop further use of that unit's result; every live child closes
before the prepared seed it came from, and a `AssayError` raised inside a unit
is never papered over by a normal-exit `RuntimeError` from the surrounding
cleanup — the first real error is the one the verdict remembers.

**Scratch and pack-space cost is bounded by formula, not by a free-space
probe (A-194).** A prepared seed is transferred exactly once per lane. For `U`
attempted units (baseline plus every mutant plus both canary halves that
actually run), total pack-write I/O across the lane is bounded by
`(U + 1) * max_pack_bytes` — the one-time seed transfer plus one independent
pack per unit. Peak simultaneous pack space is bounded by
`(1 + max(1, jobs)) * max_pack_bytes` — the seed plus at most `jobs` live
children at once, since P23 submits mutation work in waves of size `jobs` and
awaits each wave before starting the next. The conservative bound on
materialized tree size for one live child is `max_entries * max_blob_bytes`.
Hardlinking a child to the seed to avoid this cost is forbidden — it would
destroy the isolation P22 exists to provide. There is no preflight free-space
check: querying available bytes ahead of a write cannot promise the write will
still fit by the time it happens, so a real scratch I/O failure is left to
surface as P22's own `ERROR/GIT_FAILED` rather than being predicted and
pre-empted by a guess.

**A symbolic `judge.base` is resolved before any snapshot exists.** P22 never
preserves refs or branch names — only the reachable closure of one resolved
commit. A lane's declared `judge.base` (a branch name, `HEAD~1`, or similar)
is therefore resolved once, against the consumer's real repository, through
the same merge-base computation `git.resolve_base` already performs for the
direct path — never a bare `rev-parse` against the symbolic name. Resolving
only the ref and not the merge-base would fail whenever the base branch has
commits the prepared commit's history does not: the snapshot only ever
contains one commit's closure, so a diverged base is unreachable inside it
unless the *merge-base* (necessarily an ancestor of the prepared commit) is
what gets baked into the snapshot's own diff and canary judgments.

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
rule inside v3 could witness it being wrong; closing it needed a
`canary.target` field and therefore a v4 migration, which A-138 makes a
consumer's decision rather than a producer's. **P21 landed it: `canary.target`
exists, and `Verdict` plus the raw verifier now require it to EQUAL
`judgment.r3.target`.** A-152/A-O18 are closed.

**v4 IS the current contract (A-157/A-170), landed by P21 as the one
pre-adoption migration.** It adds that canary target; all killed-mutant
identities (a count cannot prove which sites or operators were killed); a
required recorded `max_mutants` plus the `candidate_count` that makes a
pre-submission refusal provable; an explicit `reported`/`unavailable`
exclusion capability; and model/raw-verifier parity for the closed operator
vocabulary and the time interval. It also names the output-write,
mutation-limit, mutation-discovery, capability-absence, head-moved,
snapshot-limit and missing-tool terminals. Exactly one schema is active in a
released build: producers emit v4 only, and v1-v3 are rejected with a single
version diagnostic rather than upgraded in place.

**Layer ownership is explicit, and no layer is credited with a relation it
cannot express (A-182).** The shipped JSON Schema owns every LOCALLY
expressible rule -- enums, ranges, requiredness, string grammar, and
reason/payload conditionals inside one object. It does NOT own cross-object
arithmetic (`candidate_count` against `judgment.r2.max_mutants`), cross-object
equality (`canary.target` against `judgment.r3.target`), or temporal ordering
(`ended >= started`): Draft 2020-12 has no `$data`, so saying "all three
layers reject" would be another hollow contract. Those live in the Python
model AND, independently worded, in `assay.verify`'s raw-document checks.

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
carries `schema_version: 8` (an integer, bumped on any breaking shape change),
`assay_version`, and — when the running build can identify itself — the
optional `judge_provenance` block (B018/A-327).

**`assay_version` names a version; `judge_provenance` names a build.** Any
process can print a version string, so a consumer that resolved a judge
artifact, verified its digest and then received a verdict had nothing binding
the second to the first (CIU's V8 testing-gate proposal §11.3 is the concrete
ask). `judge_provenance` records the distribution name, exact version,
artifact kind (`wheel`/`zipapp` — a release publishes both, with different
digests, so the kind is recorded rather than guessed), digest algorithm, and
the artifact's own lowercase sha256. It is derived per invocation form from a
handle that form actually exposes, each MEASURED rather than assumed: a
zipapp's `zipimport.zipimporter.archive` is hashed directly; a wheel install
reports the sha256 the installer wrote into PEP 610 `direct_url.json`, which
is the only statement about the wheel that survives its own installation. A
process whose imported code lies outside the distribution it found is refused
too — reporting an installed wheel's digest for source imported over it would
be a record that is not absent but false, which is worse.

**The field is optional and its absence is meaningful, which is the whole
design.** A source checkout has no build artifact; hashing `src/` and calling
the result an artifact digest is exactly the laundering the field exists to
prevent, so an unidentifiable invocation records NOTHING — never four of five
fields — and announces the reason on the diagnostics stream. Making the
identity mandatory instead would have forced either an invented digest or a
tool that cannot run from a checkout at all. A consumer that genuinely needs
the binding says so per invocation with `assay run
--require-judge-provenance`, which refuses before any lane work rather than
producing evidence it cannot attribute.

**A version bump is a migration for the consumer, never an upgrade by the
producer.** `assay verify` refuses any `schema_version` but its own, with a
single diagnostic naming the version and nothing else — it does not read the
rest of a foreign artifact, because every later complaint would be a
consequence of the version rather than an independent defect (a v2 artifact
otherwise reports a bare `KeyError` on a v3 field its producer had never
heard of, and a v3 artifact would report four missing v4 fields). The version
check therefore runs BEFORE required-field or foreign-shape inspection. It
never coerces, defaults, or in-place upgrades a stale artifact: an artifact
records what one run of one assay judged, so the only honest way to obtain a
v4 verdict is to produce one.

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

### Two R1 modes, one claim per lane (A-260)

R1 always answers "is this measured?", but wave 1 lets a lane pick *which*
lines the question is about: `judge.mode = "changed_lines"` (absent means
this — the only mode that existed before wave 1, so no existing lane needs an
edit) measures the `base..HEAD` diff; `mode = "whole_target"` measures one or
more explicitly declared files (§5, above), with no base and no diff at all.

**`mode` is a LANE-level scope, read by R1 and R2 alike (A-325).** A lane
declaring `mode = "whole_target"` and `judge.targets` scopes BOTH tiers to
those declared files: R1 asserts its floor over them, and R2 mutates them
whole instead of scoping mutation to the `base..HEAD` diff. That is why
`judge.base` is refused as inert config on a whole-target lane of any rigor
and any language, and why `judgment.resolved.base` is absent from a
whole-target verdict — no tier resolved a comparison commit. A declared target
that fails a containment gate (outside `source_roots`, absent at the judged
commit, inside an excluded directory, not adapter-recognised source, or a test
path) is REFUSED `ERROR`/`BAD_LANE_CONFIG` at both tiers, naming the target
and the gate on the diagnostics stream; neither tier silently narrows the
declared set, because a PASS over a silently narrowed set is the vacuity hole
this mode exists to close.

**This is a MODE of the one R1 claim, not a second rigor level, and not an
"R1.5".** `claims[]` carries exactly one computed entry per `declared_rigor`
level ("Computed rigor and external evidence are separate axes", above), and
`_check_claims_cover_declared_rigor` enforces one claim per level as a closed
invariant. Inventing a second R1 shape would either break that invariant or
require a new level nobody asked assay to define, for a mode switch that
changes only *which lines feed the same arithmetic*, not what kind of evidence
is produced. A consumer wanting both a changed-line gate and a whole-module
floor declares **two lanes**, each with its own one claim; the verdict
distinguishes them by `judgment.r1.mode`, required in the artifact even though
optional in the lane file — the lane file records what a human declared, the
artifact records what actually judged, and that asymmetry is `judgment`'s
whole reason for existing (P16).

`judge.base` is forbidden under `whole_target`, full stop (A-325 — this
paragraph previously carved out "unless the lane also declares R2", which was
already false when `whole_file_r2` shipped). A whole-target claim resolves no
diff at any tier, so recording a `base` would imply a comparison that never
happened. `JUDGE_FIELDS_BY_RIGOR` stays the single source for this — the
required-field set becomes mode-dependent rather than duplicated into a second
table — and an `R0,R1,R2` lane in whole-target mode declares no `base` and
records none.

**And at schema v8, the artifact can finally CHECK all of that for an `R0,R2`
lane (B035/A-329).** A-325's correction was right and incomplete in one
specific way: it wrote the rule to the part the document could witness, and
for a lane declaring no R1 that part was empty. `judgment.r1.mode` was on the
wire; `judgment.r2` had no `mode` at all, so nothing distinguished a
diff-based R2 (which must carry a base) from a whole-target one (which must
not) — and the shape with no R1 is not a corner case, it is every SQL lane,
including dstdns's `cw2b_schema`. Both halves therefore went unenforced
exactly where they mattered most, and a diff-based `R0,R2` verdict that
omitted the base it was scoped against was accepted by 2.4.2 where 2.4.1 had
refused it.

`judgment.r2` now records `mode` (required, like R1's) and `targets`
(required, non-empty, unique and sorted iff `mode = "whole_target"`, forbidden
otherwise — R1's contract word for word). The model, `verify.py`'s raw layer
and the packaged schema each read the lane's mode off `r1` if present and off
`r2` otherwise, and enforce `base` required-or-forbidden for every shape.
Where BOTH tiers are present their modes must agree and their target sets must
be equal: one judgment records one lane's scope, and an ambiguous premise
cannot carry a rule. `targets` also closes B035's smaller half — a
whole-target verdict names mutated files only through `mutation.*[].path`, so
without it a declared target that legitimately produced zero mutation sites is
indistinguishable from one that was never considered.

This is the one thing in the v7→v8 cut that *required* a bump rather than a
widening, and the reason is mechanical rather than semantic: the packaged
schema sets `additionalProperties: false` on the `judgment.r2` object, so a
released consumer holding a v7 copy would reject any document carrying the new
keys. B014's own 6→7 bump turned on the identical fact.

**Who supplies the base is separable from whether one is required
(B019/A-328).** `judge.base_source = "request"` lets a lane require
changed-line judging while delegating the base's IDENTITY to the invoking gate
request (`assay run --request-base REF`), so one static lane declaration stays
correct across every branch and worktree while an orchestrator owns branch
awareness. The request-supplied value is not a second code path: it is a
second *source* for the same argument, resolved by the same
`_resolve_declared_base` merge-base contract and recorded once in
`judgment.resolved.base`, so nothing downstream can tell which owner supplied
it — and nothing downstream needs to. The two owners are mutually exclusive
and every disagreement is refused by name, applying A-062's inert-config rule
rather than inventing a precedence order: whichever side lost a precedence
contest would be configuration nothing reads. `base_source` is itself refused
wherever `judge.base` would be (R0/R3-only lanes, whole-target lanes), because
a policy about an inert field is inert.

### Branch coverage is judged whenever the artifact reports it (A-258)

Not opt-in, and deliberately so: a changed line that is a branch source with
an untaken arc lowers `pct` in *every* lane whose coverage artifact carries
branch data, including a lane that declared R1 before wave 1 shipped. The
alternative — judge branches only when a lane explicitly asks — was rejected
because it inverts who is trusted with the floor: the *artifact* already
measured the arc, and reporting a line-only PASS over data that disagrees is
exactly the laundering this project exists to remove. This is also why the
change lands with a schema major bump (v6) rather than quietly inside v5: it
changes what PASS **means** for an existing R1 lane whose argv already passes
`--cov-branch`, which is a compatibility fact a reader needs before upgrading,
not an implementation detail.

`pct` becomes the COMBINED line+branch percentage the moment branches are
present — `(covered + branches_covered) / (executable + branches_total)`,
exactly `coverage.py`'s own `summary.percent_covered` under `--cov-branch`.
`covered`/`executable` stay line-only and the branch side gets its own two
integers, so a consumer can re-derive `pct` from the payload alone rather than
trusting a pre-combined number. When branch capability is `"unavailable"`,
`branches_total` is 0 and the formula degenerates to today's line-only value
with no special case.

A floor missed purely because of branches renders `FAIL`/`UNCOVERED_BRANCHES`,
never `UNCOVERED_LINES`: which mechanism refused is exactly the distinction
this project exists to keep (the reason-code table, above; B001's false-PASS
story one layer up).

### `require_branch` governs absence, never presence (A-259)

`judge.require_branch` (default `false`, legal on any R1 lane) guards against
exactly one failure: an argv edit that quietly drops `--cov-branch`, turning a
line+branch gate into a line-only gate that still says PASS, with nothing in
the verdict admitting the rigor dropped. With it `true`, an artifact whose
branch capability is `"unavailable"` renders `NO_MEASUREMENT`/
`BRANCH_UNAVAILABLE` — payload-free, decided before any evaluation, beside
`check_empty_coverage` in the same guard sequence rather than inside the
arithmetic, because "can this even be measured" is a measurability question,
not something the four-way union should have to special-case.

It is asymmetric on purpose. `require_branch` never *demotes* a capable
artifact: when branches ARE reported, they are always judged (A-258, above) —
there is no lane-level opt-out of real evidence the artifact already
produced. The flag only ever answers "is it acceptable for this lane to fall
back to line-only", never "should branch data count when present". Naming it
`require_branch` rather than, say, `judge_branches`, is deliberate: the
latter would read as a toggle over presence, which is the exact silent
downgrade this key exists to forbid.

### Snapshot selection: an affirmative materialisation boundary, not a sandbox (B006a)

Every R1/R2/R3 lane now declares `[lanes.X.isolation]` — required the moment
a lane claims R1, R2 or R3, refused on an R0-only lane, with no default and
no inference from where `assay.toml` happens to sit (inferring it was
considered and rejected: it would silently re-scope every existing R1+
consumer, whose lane files all sit in subdirectories, so a lane whose tests
read a sibling path would begin failing for a reason nothing in its config
mentions — the exact failure class this item exists to remove).

`snapshot_selection` is closed to two values. `"repository"` materialises the
whole resolved commit, as every R1+ lane has always run. `"repository-minus-
unsafe-symlinks"` additionally omits exactly the declared, commit-validated
symlink leaves that P22's existing hermeticity guard ("Repeated execution
runs on committed objects", above) would otherwise refuse for the WHOLE tree
regardless of `source_roots` — one tracked absolute symlink anywhere in a
monorepo (Topos's deliberate `/etc/passwd` fixtures, in this estate) used to
fail every R1+ lane in every unrelated project permanently. The exact
property, quoted rather than paraphrased because a paraphrase drifts toward a
stronger claim than the mechanism delivers:

> For each higher-rigor unit using omission mode, assay initially hands the
> command a private worktree in which every declared, commit-validated
> P22-unsafe symlink is absent and every other P22-supported tracked path
> from the resolved commit is materialised.

**What this is not, stated because a security-adjacent claim that overstates
its mechanism is worse than no claim.** It is not a project ownership
boundary — safe symlinks and ordinary files under sibling projects remain
materialised, so CMRU's repository-root reads (`cmru.project.sample.toml`,
`cmru.release.sh`) need no declaration at all. It is not a confidentiality,
filesystem, execution or network sandbox: the executed command is still a
bare `subprocess.run(cwd=snapshot.project_root)`, and a mount-namespace or
Landlock sandbox — which would deliver that stronger property — is **rejected
here, not deferred** (§7, below, and A-030); that property belongs to the
execution environment, never to this library. And it does not remove the
omitted symlink's blob from the private Git object closure: `git show
HEAD:<omitted path>` still reads its target string, and a command that clears
the `skip-worktree` bit itself (`git checkout`, `git worktree add`) can
restore an omitted leaf — measured, not theoretical, and unavoidable once
B006.3's own requirement to retain the complete resolved commit is honoured.
What assay guarantees is narrower and provable: *it* never materialises the
path.

An earlier draft of this design (`nyxloom-trove/W1-CARVE-branch-coverage-and-
whole-target.md` §1, marked dead in place rather than deleted) instead scoped
the snapshot to a declared project prefix plus an explicit `inputs`
allowlist. It failed three independent adversarial reviews — 8, then 9, then
11 blocking findings, diverging rather than converging — because a finite
`inputs` list cannot prove it enumerates every real dependency (CMRU's own
suite reads repository-root files no source-root scoping would have found),
and because relaxing a directory-shaped input to expand automatically
reopened the exact per-file vacuity hole B005 exists to close, one mechanism
over. The shape that shipped instead omits only symlink leaves P22 would
already refuse, so it can never hide a source file, a test, or a B005 target
— the vacuity guarantee stays structurally shut without an enumeration anyone
has to keep complete by hand.

**A duplicated compatibility fact, stated here because it is the reason a
consumer cannot silently straddle both versions.** The lane schema bump to 2
is separate from verdict schema v6: it is a hard cut for the same reason v6
is — interpreting an old missing `[isolation]` table as repository mode would
give one `schema_version` two meanings (the prohibited shadowing default, §5
above), and an old binary cannot parse the new table regardless. A v2 assay
refuses a v1 lane file's now-required table; a v1-pinned assay cannot read a
v2 file's `[isolation]` table at all. See the consumer guide's ordered
adoption step for the commit-ordering consequence this creates.

## 7. What assay must never become

| Creep | The line |
|---|---|
| **a test runner** | never discovers, selects, orders, parallelises or retries tests. Executes one declared argv and reads what it produced. Flags may be *appended by the caller* and are recorded verbatim (§6); they are never *derived* by assay. |
| **a CI system** | no scheduling, triggers, queues or webhooks. Impact-based lane selection belongs to the caller. |
| **a reporting dashboard** | no history, trends, storage, server or cross-run aggregation. One artifact, one lane, one commit. Anything longitudinal consumes those artifacts; assay never retains them. |
| **a coverage tool** | never instruments, traces, or computes a coverage percentage itself — it only judges numbers a real coverage tool already produced. `mode = "whole_target"` (§6, above) lets a lane assert a floor over an explicitly DECLARED file rather than only a diff, but the file list is a lane's own declaration, never a discovered or globbed set, and the percentage is still `coverage.py`'s own arithmetic re-consumed, not re-derived. An undeclared floor over the whole project stays `coverage --fail-under`'s job. |
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
  artifacts (coverage.py JSON, cobertura XML, `go test -coverprofile` output,
  and — B036 — two real `vitest run --coverage` documents, one per provider,
  produced outside this repository from the committed `tests/fixtures/coverage/
  probe-js` project; assay's suite needs no Node toolchain for the same reason
  it needs no Go one).
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

B036 is that claim landing twice over. `coverage-istanbul-json` — istanbul's
own `coverage-final.json`, emitted natively by nyc/istanbul, by Jest, and by
BOTH of Vitest's coverage providers — was added as a fifth FORMAT with no
language attached, and the `javascript` adapter was added as a fourth LANGUAGE
covering `.js`/`.jsx`/`.ts`/`.tsx` with no format attached. Neither change
touched the other's module, the protocol, the core, or the registry.

The registry's output type carries one distinction the current copies lack:

```
FileCoverage(executed, missing, excluded: frozenset[int] | None,
             branches: BranchCoverage | None)
```

`None` ≠ empty set. coverage.py has `excluded_lines`; a Go cover profile has no
such concept. Without the distinction, a Go lane reports *"0 changed lines
excluded by pragma — verified"* when the format simply cannot say. That is the
NO-MEASUREMENT discipline one level down, and it falls out for free.

**`branches` (wave-1, A-257) keeps exactly the same discipline one field
over.** `BranchCoverage(by_line: Mapping[int, tuple[int, int]])` — source line
to `(covered_arcs, total_arcs)` — is `None` when the format cannot express
branch arcs at all (a Go cover profile: statement counts, no arcs, ever;
`go-cover`'s parser sets it unconditionally and a test asserts that as a
*measured* property of the format, not an omission that later looks like an
oversight, A-O16). `coverage-istanbul-json` is the case that made this a THREE-way distinction
rather than a two-way one (A-344): the format HAS a `branchMap`, but its real
producers disagree about what that map means — on one source file
`@vitest/coverage-istanbul` reports 6 arcs across 3 typed branch nodes while
`@vitest/coverage-v8` reports 4 single-location "branches" that are really
v8's own executed/unexecuted ranges. Through schema v8 a lane declared the
format and never the producer, so this format reported `None`
unconditionally: a number whose meaning depends on an undeclared fact is the
`declared_unverified`-class lie.

**Since v9 the producer IS declared (B045), and the parser takes it.** For a
producer in `vocabulary.ARC_BEARING_COVERAGE_PRODUCERS` — today exactly
`istanbul` — the arcs are read; for every other producer, and for an
undeclared one, `branches` stays `None`. The parameter is on all five
parsers' `parse(text, *, producer)` signature, keyword-only with no default,
so a caller that forgets it gets a `TypeError` rather than a silently
arc-less profile. This does NOT make the capability sniffable: the producer
is a *declaration*, checked against the artifact only in the fail-closed
direction — a v8-range document declared as `istanbul` is REFUSED
(`ERROR`/`UNREADABLE_ARTIFACT`) on its first `"branch"`-typed entry rather
than being read as something it is not.

Alongside the arcs, `branches` is a `BranchCoverage` with an EMPTY `by_line` for a real
branch-tracking artifact's file that happens to have no branches — the exact
trap `lcov` proves is real: `coverage.py` emits `BRF`/`BRH` for one file and
nothing at all for a branch-free sibling in the SAME artifact, so capability
is decided once for the whole artifact, never per file (a per-file rule would
call that single, correct artifact "mixed" and refuse it).

**An artifact's branch DETAIL is authoritative over its capability METADATA
(A-265), and disagreement is a refusal, never a silent resolution either
way.** The obvious alternative — "trust the metadata" — has a false-PASS hole:
an artifact whose `meta.branch_coverage` is absent or `false` but whose arc
arrays are genuinely present would read as `"unavailable"`, silently
discarding real branch evidence, and a lane with `require_branch = false`
would then report a line-only PASS over an artifact that had measured
branches all along — making A-258's "judged whenever reported" false in
exactly the case metadata is wrong. **Arc identities are validated for
uniqueness and executed/missing disjointness BEFORE aggregation into per-line
`(covered, total)` counts**, because aggregation throws the identities away:
a tampered artifact that simply repeats one covered arc inflates the
numerator, and once the (also tampered) stated totals are bumped to match, no
per-line check downstream can tell — the same reasoning `FileCoverage`
already applies to its three independent executed/missing/excluded arrays as
adversarial input (above).

Adapter surface (pure where it can be — nyxloom's `inject_*` currently writes
the file; in assay they return text, so adapters are testable with no
filesystem):

```
name, source_globs, excluded_dir_names, requires_span_attribution, external_tools
is_test_path(rel)                      has_executable_code(rel, text)
normalize_coverage_key(key)            statement_spans(text) -> spans | None
inject_import_break(text)              inject_uncovered_line(text)
generate_mutation_sites(text, lines, operators, limit) -> sites | UNSUPPORTED
```

Four adapters implement it today, and each reaches only the rigor levels this
build actually wires it to (§7 — an adapter existing is not a capability):

| `judge.language` | reached here | notes |
|---|---|---|
| `python` | R1, R2, R3 | the reference adapter; `requires_span_attribution = True` (coverage.py's multi-line-statement gap, recovered by a real AST walk) |
| `sql` | R2 only | a stdlib lexer over DDL; no coverage tool exists for it, so no R1, and A-192 forbids R3 without R1 |
| `javascript` | R1 only | `.js`/`.jsx`/`.ts`/`.tsx` under one name (A-340). R2 waits on B037's native-vs-ingest ruling, so `generate_mutation_sites` is unconditionally `UNSUPPORTED`; R3 is an unwired fast-follow |
| `go` | nothing | ships and is tested, but no producer path is wired at any level (A-172/A-217) |

**`javascript` needs no span attribution, and that too was measured rather
than assumed (A-342).** Istanbul's `statementMap` carries each statement's own
`[start.line, end.line]` EXTENT, so the parser expands a multi-line statement
across its own lines — innermost extent wins, ties resolve by max count —
recovering exactly the interior lines rule 3b exists to recover, from the
artifact rather than from a re-parse. Python needs an AST walk for the same
recovery only because `coverage.py`'s artifact does not carry extents. The
rule is load-bearing and not a refinement: in real
`@vitest/coverage-istanbul` output an `if` statement's extent has count 1
while its own never-taken `return` inside it has count 0, so a go-cover-style
"executed wins" merge would report a provably-unexecuted line as covered.

This does **not** mean every line is classified, and A-342's original wording
saying so was corrected by its own round-1 review: a line no statement extent
covers at all — a function signature line and a function-level closing brace
under the babel instrumenter, a comment under any of them — stays unclassified
and takes rule 4, exactly as an untracked line does for every other format
here. Measured: 23 such non-comment lines in the committed istanbul fixture,
against 29 statement start lines and 54 lines classified after expansion.

**Format is one axis; PRODUCER TRUSTWORTHINESS turns out to be a third one
(A-346).** Two producers of `coverage-istanbul-json` disagree not only about
what `branchMap` means (above) but about whether a line ran at all:
`@vitest/coverage-v8` reports never-executed lines as executed after any
conditional expression, measured on two major versions, while
`@vitest/coverage-istanbul` is correct on every case measured. Nothing in the
document distinguishes a true execution count from a false one, so this is not
guardable in a parser and deliberately is not guarded — sniffing the producer
from the artifact's shape is the §5 declaration-versus-sniffing collapse. It
is handled where an undeclarable fact has to be handled: in the documentation
that tells a consumer which producer to run, with committed witness artifacts
and a test that fails if the defect is ever fixed. B040.

### Go statement positions come from the SOURCE, never from the profile (A-217/A-239/A-397)

Every other adapter in this project is pure text processing. The Go one shells
out, and that asymmetry is worth the paragraph it costs, because it is the one
place where "confine language-specificity to a leaf" stopped being enough.

**The problem is an impossibility proof, not a rough edge.** A `go test
-coverprofile` record is `<path>:<startLine>.<startCol>,<endLine>.<endCol>
<numStmts> <count>` — a positional EXTENT plus a statement CARDINALITY, and
never the statements' own positions. The shipped parser expanded that extent
with `range(start, end + 1)`, which reports function signatures, closing
braces, `case` labels and continuation lines as executable code. That is not
merely imprecise: two gofmt-clean files (`carve-assets/P27/witness/collision-colA.go`
and `collision-colB.go`) compile under the pinned toolchain and emit a
byte-identical profile while their statements begin on different lines —
`{4,6}` versus `{4,5}`. A rule reading only the profile is a function of the
profile; identical input forces identical output; the two correct answers
differ; so **every profile-only rule is wrong on at least one of them.**

**Three options were considered; the operator ruled option 2.** (1) A better
line-range heuristic — excluded by the proof above, and its cost is not
confined to the permissive direction: a brace-only diff inside an untested
function reports 0/1 and FAILs at `fail_under = 100.0` where statement truth
is 0/0. (2) A source-side oracle. (3) A column-granular verdict field — a
schema migration owned elsewhere, and forbidden here. Full reasoning: A-217.

**Why the oracle is a Go subprocess and not a Python parser.** The algorithm
that PRODUCES those blocks is `cmd/cover`'s own instrumenter, and A-217's
implementation note is "adapt, do not invent". Assay ships that segmentation
verbatim (`assay/helpers/go/stmtpos/`, stdlib-only, no `require` lines, run
under `GOPROXY=off`) and runs it with the real toolchain. The rejected
alternative — a hand-written Python approximation of Go's grammar — is not
comparable to P08's hand-written Go lexer, and the difference is the direction
of failure: `has_executable_code` may be conservative, so a wrong answer there
is a false FAIL that fails closed, whereas a wrong statement position is a
wrong published verdict with no fail-closed direction at all.

**Why a NEW protocol hook, not `statement_spans`.** `statement_spans` is
called ONLY for lines that fell into no coverage bucket (A-097/A-101, frozen).
It gates in the wrong direction — it RESCUES lines nothing claimed, whereas Go
needs to DEMOTE lines a block claimed wrongly — and its type is line-only,
while the entire reason this hook exists is that `28.22,29.2` and `29.2,31.3`
are two different blocks a line-only key would fuse. So `statement_blocks` is
a fourth deliberate extension, added by the package that proved the need
(A-084), with `requires_statement_attribution` as its gate (A-397).

**Why the join is on the whole extent, never positional containment.**
`cmd/cover` ends a block at the START position of its own last statement, so
consecutive blocks genuinely SHARE a position. A statement beginning at a
shared position belongs to the first block only: a closed interval matches
both, a half-open one matches neither. The structure is not recoverable from
positions, so the oracle reports each block's own statement list and the join
is on the block (A-391).

**Why a disagreement refuses instead of guessing.** The instrumenter is
deterministic, so re-running it over the same source must reproduce the same
extents. Every disagreement therefore means something real — a profile from a
different revision than the source, a file edited between the run and the
judgment, a different toolchain — and attributing anyway would publish a
verdict about lines that are not the lines that ran. DERIVE / READ / **FAIL**,
at its third branch. (It also turned the toolchain delta between the frozen
witnesses and this wave's probe from an assumption into a measurement: all
eight profiles joined exactly, which they could not have done had the
instrumenter changed.)

**Why the correction is a GUARD, not a step someone remembers.** An
uncorrected block profile parses cleanly, produces well-formed line sets and
yields a plausible percentage — it is simply about the wrong lines. That is
the **masked default** in its purest form: rendered harmless by every context
that runs the correction, and therefore invisible to testing, surfacing only
on the path that skips it. So `CoverageProfile.statement_attributed` has
exactly one producer (the join itself), and `evaluate_coverage`/
`evaluate_targets` refuse when an adapter requires attribution and the profile
reports it never happened (A-392). Two alternatives were rejected: emitting
EMPTY line sets until corrected fails loudly but with the wrong sentence,
reporting `NO_MEASUREMENT` over a complete artifact — "absence for emptiness",
a false certification rather than a missing one; and relying on the runner's
call order is not a check at all.

**A guard with a way around it is not a guard (A-406).** The paragraph above
was true and the guard was still bypassable, because the runner had one branch
that set `statement_attributed = True` without any oracle: "this profile
carries no block extents at all, so it is already statement truth, vacuously".
That reads as harmless until you notice `judge.language` and
`judge.coverage.format` are independent by design. A Go lane may declare
`format = "lcov"`, and lcov CONVERTED from a Go coverprofile carries exactly
the naive block expansion this whole section exists to refuse — signatures,
closing braces and `case` labels as executable lines — and arrives with no
blocks, so the vacuous branch marked it attributed and the guard passed it.
No oracle ran and no `helpers` entry was written; nothing said so.

The branch is deleted. A language judged from block extents is now refused at
CONFIG LOAD if its declared format carries none — `ERROR`/`BAD_LANE_CONFIG`,
naming the language, the format and `go-cover` with its producers — so the
lane never runs; and the runner keeps a second, independent refusal for a
library caller that hand-builds a `Lane`. The case the deleted branch sounded
like it was protecting, an EMPTY profile, was never its case at all: that
terminates one step earlier at `check_empty_coverage`, `NO_MEASUREMENT`/
`EMPTY_COVERAGE`, and is now pinned by a test that also asserts no `helpers`
entry and not a PASS. The general lesson is the one this project keeps
relearning: a branch whose comment explains why it is safe deserves the same
scrutiny as one that has no comment, because the explanation is where the
unexamined assumption lives.

**What it does NOT fix, recorded rather than glossed.** An uncovered statement
sharing a physical LINE with a covered one is still promoted to `executed` —
`lit.go`'s `f := func() int { return 7 }` carries two counted statements that
both genuinely begin on line 4, and the line did run. That is line
granularity's own limit, which coverage.py shares; removing it needs a
column-granular wire field. Asserted as unfixed by a test, so a future change
that does fix it goes red rather than quietly contradicting this paragraph
(A-393, backlog B055).

**A guessed fact about `cmd/cover` that `cmd/cover` disproved, and what the
correction cost (A-405).** The parser and both block types asserted that "a
1-based source position is never below 1" about all four coordinates of a
cover record. That is true of the LINE and false of the COLUMN. `go/token`
gives a position remapped by a `//line file:line` directive that names no
column `Column == 0` by design, and `cmd/cover` says so itself, in the comment
that introduces the `dedup` helper (go1.25.14, `cover.go:1055-1060`):
"positions can repeat when there is a line directive that does not specify
column information … See issues #27530 and #30746. Tests are
TestHtmlUnformatted and TestLineDup." So the toolchain both documents the case
and names the corpus that exercises it — and assay refused that corpus's real
profile with "column number 0 in '100.0' is not positive". This is the exact
class of defect §5's *cite a source for every convention* exists to prevent,
found in the wave built to remove it, by an adversarial reviewer who ran the
toolchain instead of reading the code.

The invariant is now stated truly, with the citation, at all three sites. The
interesting half is what to DO with such a file, because relaxing the bound
alone would have been worse than the refusal it replaced. A `//line`-remapped
file's records name the DIRECTIVE's line numbers: Go's own `TestLineDup`
fixture is 24 lines long and its profile reports lines 100 to 105. `git diff`
names physical lines. So a permissive parser plus an unchanged evaluator gives
a file that measures 0 executable of 0 changed and disappears into a clean
percentage — the laundering gate this section's own guard exists to close, and
the north star's "0/0 is never 100%".

The disposition is the north star's other rule, applied literally: *pre-existing
code outside the diff is invisible to the verdict by construction, because it
is not what is under review.* A remapped file is flagged per FILE (any record
with a zero column flags it; within such a file physical and virtual positions
are indistinguishable, so the conservative direction is the only honest one).
If the lane does not judge it — no changed line inside `source_roots`, not a
declared target — it is IGNORED: its records contribute nothing and the lane
proceeds, which is what stops one goyacc-generated file from taking down R1
for a whole project. If the lane DOES judge it, the lane refuses
`ERROR`/`BAD_LANE_CONFIG`, naming `//line` as the cause, the file, and the
remedy. `BAD_LANE_CONFIG` rather than `UNREADABLE_ARTIFACT` because nothing is
wrong with the artifact — it is byte-for-byte what `go test` wrote — and
blaming it would send a consumer to re-run tests over a lane-configuration
fault, the same misdirection `go_modfile._refuse` was written to avoid.

**The oracle is STAGED out of the artifact, and that is a consequence of how
assay is installed rather than a convenience (A-403).** `go run .` takes a
working DIRECTORY, so the first design resolved the helper's location from
`__file__` and reasoned that "a wheel install always materialises package data
as real files". True — and the shipped **zipapp** is not a wheel install. Its
package data are zip members, `is_file()` answers False, and the adapter
refused with "assay's Go statement-position oracle is missing from the
installation" for an oracle the archive contained all along. That is the
install path that matters most: a Go gate image built on `golang:1.25` has an
interpreter but no pip and no ensurepip, so the zipapp is the ONLY way assay
gets in (A-402), which makes the shape that could not run the oracle the shape
every Go consumer runs. The files are now read through `importlib.resources`
— the same call `verdict.schema_text` already used for the shipped JSON Schema
— and written into a `TemporaryDirectory` for the duration of the call, on ONE
path for every installation shape. The single path is the load-bearing half:
a branch taken only inside a zipapp would never be exercised by the registered
gate, which installs a wheel, so the consumer's own path would be proven by
nothing but an opt-in qualification run.

**Which toolchain judged a verdict is recorded, and the ROLE is the runner's
to name (B047 item 5, A-397).** A Go lane's verdict carries a `helpers[]`
entry — `role: "statement-positions"`, `tool: "go"`, the resolved path, and an
`identity` the toolchain reported about ITSELF from inside the program it
compiled. The adapter layer returns a `HelperInvocation` carrying no role at
all: `statement_blocks` is the only protocol method that produces one, so
which of `HELPER_ROLES` it belongs to is a fact of the CALL SITE. An adapter
that could name its own role could claim `mutation-sites` from a coverage
hook, and the schema's correspondence rule would then demand an R2 payload for
work that produced an R1 one — a refusal naming the wrong thing. That
correspondence rule (`verdict.supported_helper_roles`) has exactly one
definition, read both by `Verdict`'s own validation and by the runner, for the
reason A-385/A-367 give about coverage keys: two copies can disagree, and the
disagreement's shape here is a verdict the producer built and the schema then
refuses with an uncaught `ValueError`.

### A Go lane's module path is DERIVED from its `go.mod`, never declared (A-404)

§5's defaults doctrine names four copies' worth of anti-pattern #1, and one of
the four is `covergate`'s own `-source internal -module srdm`: *a literal
standing in for a fact that lives authoritatively in the project's layout … A
library cannot ship any of them; it must read them.* When the Go adapter
became reachable through the CLI, that flag came back wearing a new name.

The mechanics force the question. A Go cover profile keys every record by the
package's IMPORT path — `<module path>/<dir>/<file>.go` — while `git diff`
names the same file relative to the repository top. Something must supply the
module path, and no fixture layout dodges it: an import path is the module
path plus the directory, so the key equals the repo-relative path only for an
empty module path, which `go.mod` forbids. **Every** real Go module hits this,
and the failure is not loud: an unstripped key resolves to a file that does
not exist, under no source root, so the lane measures nothing and — but for
the oracle's own file-existence check — would have measured 0/0 and PASSED.
That is the laundering gate §5 opens with, arrived at from the other side.

Three shapes were on the table and the difference between them is doctrine,
not taste. A **declared lane key** (`judge.module_path`) had the most recent
precedent — A-328 added `judge.base_source` as an optional key with no
lane-schema bump — and is the one rejected: A-007's selection rule asks *whose
fact is this*, and answers by observing that the coverage FORMAT is a fact of
the lane's own argv (so it is declared, and sniffed as a cross-check) while
the module path is a fact of `go.mod`. A-328 is not a counter-precedent
either: `base_source` delegates a fact that genuinely is the caller's — which
base this gate request compares against — and A-328's own reasoning, *refuse
precedence between two sources of one fact, because whichever loses is config
nothing reads*, argues against a declared key sitting beside a derivation. A
restatement-that-must-agree would catch nothing here, because `go test` read
the same `go.mod`. A **per-lane registry** was rejected on measurement:
`_built_in_registry` is read by the lane inventory (A-349), by the docs gate
(A-400) and by every language resolution.

What landed is **derivation, through one narrow protocol member**.
`LanguageAdapter.for_project(repo_top, project_root)` returns the adapter to
use for that project; every adapter but Go returns `self`, and `GoAdapter`
returns a copy carrying the module path parsed out of the nearest `go.mod` at
or above the project root. The core calls it once per lane, at the one point
where it holds both anchors and before anything reads the profile, so the key
join, the statement oracle and both evaluate modes are handed the same object
— the alternative, binding for one consumer and not the others, would
recreate the two-spellings drift A-385/A-367 rule against inside the fix for
it. The core still does not know what a `go.mod` is.

Three consequences are stated rather than left to be met:

* **The parser reads the `module` directive and nothing else.** Its lexical
  rules — `//` comments only, `/* */` a syntax error, bare or `"`-quoted
  arguments and never backquoted, an identifier ending at `//` — are
  transcribed from `go1.25.14`'s own vendored
  `golang.org/x/mod/modfile/{read.go,rule.go}`, read inside the gate image
  rather than recalled. A general `go.mod` parser would be a second
  implementation of a file format assay cannot keep in step with a toolchain
  it does not ship.
* **A project root in no Go module refuses `ERROR`/`BAD_LANE_CONFIG`** — an
  existing code, chosen because a Go judge declared over a directory that is
  not a Go module is a lane/tree mismatch, the same shape as `evaluate.py`'s
  "the project root is not contained by its own repository". A new reason code
  would have been an enum widening, and the enum is closed (A-050).
* **A profile key outside the derived module refuses**, naming the key, the
  derived module path and the `go.mod` it came from. This replaces a
  misattributed message that blamed staleness ("the profile and the working
  tree are not the same revision") for what was an unstripped prefix; the
  staleness message survives for its actual subject, a key that IS under the
  module and still names no file. One Go module per lane, `cwd` is that
  module's root, and no `go.work` support: nested modules never appear in
  `go test ./...`'s own output, and a project root above several modules
  surfaces as this refusal rather than as a silent partial measurement.

### Mutation is source-oriented

An Assay mutation adapter describes a change to **tracked source bytes**; it
does not mutate a running language environment. The adapter receives current
file text, changed physical lines, the selected operator vocabulary, and a
remaining candidate bound. It returns small byte-span `MutationSite`
descriptors. The language-free core applies one descriptor to one immutable
committed-object snapshot and runs the lane's one already-declared command plan
against that snapshot.

This boundary applies even when the language's tests need substantial external
state. P34's SQL/DDL adapter (below) learns SQL syntax through a stdlib-only
lexer — never a parser, never a helper process — and replaces a constraint or
trigger declaration in tracked DDL; the project's declared command then
provisions a fresh test database and applies that mutated schema. Assay and
the adapter do not receive a DSN, connect to the database, choose an image, or
manage rollback. Those facts remain project/environment-owned inputs to the
declared command. A component that instead introspects and mutates a live
database is a separate producer whose structured result may be consumed as
Tier-2 adjudicated evidence; it is not a `LanguageAdapter` shortcut around the
source/snapshot contract.

**Ingested R2 (B046, schema v9): the same sentence with the producer
substituted.** For a language Assay ships no mutation engine for, R2 may be
computed over a report a foreign mutation tool wrote — and this is not a new
trust boundary, it is the one R1 has always had. R1 ingests foreign evidence
by construction: the lane's argv runs a coverage tool inside the private
snapshot, the tool writes an artifact at a declared path, and Assay reads it
through a FORMAT-keyed registry and computes the judgment. Ingested R2 is that
paragraph with "mutation" substituted. Assay never invokes the tool itself and
never accepts a report it did not watch being produced: the lane's own argv
runs it inside the snapshot, so the report is bound to the resolved commit
exactly as `coverage-final.json` is (A-161), and the declared artifact is held
through the same single-owner reservation, so a committed or stale report
cannot satisfy the path.

**Scope stays Assay's computation, never the tool's.** The foreign tool
mutated whatever its own configuration told it to; which of those mutants
COUNTS is decided by Assay's own rule — under `changed_lines` a mutant counts
iff its start line is an added line of the resolved diff, under
`whole_target` iff its file is a declared target. Reading the tool's own score
would be judging by a scope the lane never declared.

**The tier statement, which is the whole reason this is expressible at all.**
A verdict distinguishes Tier-1-computed-natively from
Tier-1-computed-over-ingested-evidence, and it does so in the document rather
than in a consumer's assumptions: `judgment.r2.producer` is required and is
either `native` or `ingested`, and it FORKS the object. Under `ingested`,
`jobs`, `max_mutants`, `operators` and `equivalence_artifact` are **forbidden**
— they are Assay's own policy, and Assay chose none of it for a run it did not
orchestrate. Filling them from the report would put the foreign tool's
configuration on the wire under Assay's name, the same declared-versus-verified
conflation A-230a keeps `helpers[]` clean of. The producer's own identity is
recorded in `judgment.r2.producer_tool`, copied verbatim from the report and
documented as **declared by artifact, not verified** — it is not a `helpers[]`
entry, because `helpers[]` records tools Assay itself invoked. The operators
the tool actually applied are not lost by any of this: they are on the wire,
one per mutant, namespaced under a prefix Assay owns (`stryker:<mutatorName>`,
admitted by an open pattern branch beside the three closed per-language enums)
so a foreign name can never be confused for a native one.

Consequently, R2 judges the selected mutation catalogue over the changed
tracked source in scope. It never silently upgrades itself into a whole-project
or whole-deployed-schema audit. Language-specific operator catalogues and an
R2-without-R1 adapter remain explicit product-design questions, not values an
adapter may invent locally (A-215).

Execution is observable without becoming a second verdict. When the caller asks
for it with `assay run --progress PATH` (B031/A-320), R2 appends a compact
NDJSON event to PATH after the baseline and after every candidate completes.
The destination is the CONSUMER's, never derived: `8a2a4731` wrote
`.assay/<lane>.progress.jsonl` into the live worktree unconditionally, which
broke assay's own clean-tree precondition on the very next run of the same
lane, and interpolated an unvalidated lane name while doing it. Nor does the
verdict name the destination back: the caller chose it, the same way it chooses
`--verdict-json`'s, and the one grammar a verdict path field can carry
(repo-tree-relative) can only express the location this design forbids. An
optional
`judge.mutation.budget_per_candidate` bounds one candidate's command; its
timeout uses the existing `budget_exceeded` bucket rather than widening the
closed reason-code vocabulary. The separate `assay plan` command performs the
same discovery against a private commit snapshot and emits deterministic
candidate identities and runtime estimates, but never runs the lane command or
a mutant.

`UNSUPPORTED` is adapter-wide capability absence, never invalid source or an
unrecognised individual construct. It renders as payload-free
**`INCONCLUSIVE/MUTATION_UNSUPPORTED`**, never green; a supported analysis that
finds zero sites instead renders `INCONCLUSIVE/NO_MUTANTS` with an exact
zero/zero mutation payload (A-183). This absent-versus-empty distinction keeps
“we cannot analyse this language” from masquerading as measured evidence that
nothing was mutable. Python raises the typed discovery failure for invalid
syntax; SQL raises the analogous `MutationDiscoveryError` for an unterminated
string, dollar quote or block comment (below); Go returns `UNSUPPORTED` until
P29 lands its helper.

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

### Two path grammars, not one — deliberately (A-271)

`judge.targets` and `isolation.unsafe_symlink_omissions` refuse a
non-canonical spelling outright at load — `./x`, `x//y`, `x/`, an interior
`.` — while `safeio.reserve_output` (the coverage artifact, and any
CLI-supplied destination) accepts and normalises the same shapes. A-145
requires every boundary to say WHICH spelling it speaks (project-relative vs
repo-top-relative); it does not require every boundary to reject a
non-canonical form, and this is the deliberate asymmetry rather than an
inconsistency to "fix" into uniformity.

The strictness has one specific job: `targets` and
`unsafe_symlink_omissions` are **lists that get aggregated or compared for
exactness**. `src/good.py` and `src//good.py` are two distinct strings naming
one file — a raw-string uniqueness check accepts both, and TOML's own
`uniqueItems`-style schema check accepts both, so a whole-target lane would
count one well-covered target TWICE, inflating the aggregate enough to carry
a poorly covered sibling over the floor. That is a false PASS reachable from
a plausible typo, and `unsafe_symlink_omissions` is strict for the identical
structural reason: it is compared for exactness against the materialised
skip-worktree set, and a spelling that "means" the same path but does not
match it byte-for-byte silently fails to omit anything.

The coverage artifact has no such exposure. It is exactly ONE path, aggregated
with nothing else, and `PurePosixPath` normalisation is lexical with no
traversal risk — every actually escaping form (`..`, absolute, a symlinked
component) is still refused loudly regardless. Tightening it would also reach
the CLI's `--verdict-json`, where `./out.json` is an idiomatic, harmless
spelling; refusing it would be user-hostile for a safety property that path
has no way to lose. Measured before ruling, not asserted: no live lane in the
estate uses a `./`-prefixed spelling today, so accepting it costs nothing real.

### SQL/DDL mutation: a stdlib lexer, not a database connection

**Why a lexer and not a parser or an external helper.** No SQL parser or
linter exists on this host or inside the shared gate image, so route (ii) —
add a dependency to the shared test image — starts by re-risking every other
project's gate before locating a single byte span, which A-005's
zero-runtime-dependency claim exists to make unnecessary. What the seven
operators need is not grammar; it is knowing which bytes are *code* — a bare
keyword regex over raw file bytes produces phantom matches inside comments,
string literals and dollar-quoted bodies (measured: 13.3% phantom over a real
316KB DDL corpus, and every real `ON DELETE RESTRICT` site in that corpus was
a phantom under the naive rule). The fix is a two-phase *mask*, not a parser:
walk the source once, classify every byte as code or not-code, and recurse
exactly one level into each dollar-quoted body — real projects put their
idempotent DDL there, and a body left opaque loses real sites, including both
of a real corpus's only two `ON DELETE RESTRICT` foreign keys. A parser would
buy nothing further, because none of the seven operators needs to know what
*kind* of statement it is in — only where its own span starts and ends.

**Fail closed, not fail open.** An unterminated string, dollar quote, or
block comment raises `MutationDiscoveryError` (`ERROR`/
`MUTATION_DISCOVERY_FAILED`) rather than silently discovering sites in the
valid prefix of a file real PostgreSQL would refuse outright. A discovery
routine that degrades gracefully on malformed input turns a measurement gap
into evidence that looks clean.

**`language = "sql"` resolves at R2 only.** There is no SQL R1 (DDL has no
coverage tool to report changed-line execution against) and no SQL R3 (A-192
forbids a canary without R1 to attribute it against) — settled by the same
rigor-ladder discipline that governs every other language, not a gap SQL
happens to have. That single registry fact is also what makes
`has_executable_code`/`normalize_coverage_key`/`statement_spans`/
`inject_import_break`/`inject_uncovered_line` provably unreachable through
the shipped CLI: nothing at R0 or R1 or R3 ever resolves an adapter for a
language this build's one registry entry names R2-only, so none of those
five methods is ever called — they raise `NotImplementedError` rather than
carry dead logic.

**What this buys you, stated exactly, and what it does not.** For each
mutant it reports, assay proves that exactly one byte span of one tracked,
changed DDL file — located outside every comment, string literal and quoted
identifier, at both the outer and the dollar-quoted lexical level — was
replaced by a recorded replacement, and classifies that mutant using only the
project-declared command's exit status and the bytes of the two files the
lane itself declared. That is mechanical, and it is the whole claim. It does
**not** give you:

1. Proof that a mutant is valid DDL (a widened integer `IN`-list with a
   string literal is DDL real PostgreSQL refuses; nothing in assay can tell
   that from a mutant the tests happened to kill).
2. Proof that the operator name matches what actually changed in the
   database catalog (`sql:drop-check` and `sql:widen-check-in` produce an
   *empty* delta over `pg_constraint`'s names — only a schema dump sees the
   change).
3. Proof that each mutant was judged against an isolated database (a mutant
   applied to a database that already carries the un-mutated schema exits
   0 with the mutation never having happened — the next subsection is the
   refusal that converts that into an honest terminal rather than a false
   pass).
4. Verification of a kill's cause (with kill attribution `declared`, assay
   records verbatim whatever string the project's own command wrote; it
   never checks that string against the mutation that produced it).
5. A whole-schema audit (sites come only from changed lines in tracked files
   under the declared `source_roots`, further bounded by `max_mutants`).
6. Any connection to a database, ever (A-215): no DSN, no catalog read, no
   provisioned image.

### Mutant classification needs more than exit status (the equivalence artifact)

Every other language's R2 classification reads exit status alone: the
command passed, so the mutant survived; it failed, so it was killed. That
mapping is silently wrong for SQL, because a non-zero exit from a DDL apply
command does not mean "a test caught the mutation" — it can mean "the
mutated DDL was never valid in the first place" (a widened `IN`-list against
the wrong literal type, measured to fail with `invalid input syntax for
type integer`), which is not a kill, it is a **crashed mutant an exit-status
mapping would misreport as a kill**.

So when — and only when — a lane declares
`judge.mutation.equivalence_artifact`, classification becomes a function of
`(exit outcome, artifact presence, artifact bytes)` rather than exit status
alone:

| exit outcome | equivalence artifact | bucket | why |
|---|---|---|---|
| `PASS` | absent | `crashed` | the lane declared an artifact its command did not write; nothing was measured |
| `PASS` | present, **≠** baseline | `survived` | the mutated schema was built and the suite did not notice |
| `PASS` | present, **=** baseline | `equivalent` | the mutant provably changed nothing |
| `FAIL` | present, ≠ baseline | `killed` | the mutated schema was built, and something refused it |
| `FAIL` | present, = baseline | `equivalent` | it never mutated (residue, or a never-firing guard); the failure is about something else |
| `FAIL` | absent | `crashed` | the schema never got built — an invalid mutant, **not a kill** |

Two properties matter more than any one row. **The table contains zero SQL
knowledge** — exit status, file presence, byte equality, nothing else — so
it stays in the language-free core rather than becoming a SQL special case.
And **it is inert for every existing lane**: with no `equivalence_artifact`
declared, the original exit-status-only mapping applies completely
unchanged, so no Python lane's verdict moves by one bit — a property this
package proves with a byte-identical-verdict test rather than asserting it.

**Why `equivalence_artifact` is REQUIRED on a SQL lane rather than optional.**
The tempting shape is "opt in, like everything else". Rejected, because the
two failure modes are not symmetric. Without it, the single most likely way
a real consumer gets isolation wrong — a mutant applied to a database that
already carries the previous run's residue — surfaces as `survived`, and
`survived` is an assertion about *the consumer's own test suite* that is
false: assay would say "no test asserts this constraint" about a constraint
that was never actually removed. That is worse than a missing feature; it is
exactly the class of false statement this whole project exists to remove.
With the artifact declared, the identical run is `equivalent` instead, and if
every mutant lands there the claim is loud and non-green —
`INCONCLUSIVE`/`ALL_MUTANTS_EQUIVALENT` — rather than a quiet false pass.

### The consumer command order is one token wide: apply, dump, then test (A-279)

**Requirement.** A SQL lane's project-declared command must write
`equivalence_artifact` **after the schema has been fully and successfully
applied, and regardless of whether the test step that follows passes or
fails.** The canonical shape is:

```
apply && dump && test
```

**never** `apply && test && dump`.

**Consequence of getting the order backwards.** A kill *is* the test step
exiting non-zero. Under `apply && test && dump`, shell `&&` short-circuits
the instant `test` fails, so `dump` never runs. assay's own
`safeio.reserve_output(...).arm()` has already unlinked any pre-existing
artifact file before the command started, so the equivalence artifact is now
simply absent. The classification table above reads `(FAIL, absent)` as
`crashed`, never `killed` — and `judge_mutation` ranks `crashed` above every
other bucket, so **one such mutant renders the entire lane
`ERROR`/`EXEC_FAILED`.** The feature's headline outcome — a real kill — could
never be produced under this ordering, and the very first mutant a
consumer's suite genuinely caught turns the lane red for a reason that reads
as "assay is broken" rather than "your command is ordered wrong".

This is not a hypothetical failure mode; it is measured on the shipped CLI,
not merely reasoned about. `nyxloom-trove/carve-assets/W3/MANIFEST.md`
freezes two repositories, identical in DDL, lane and mutant, differing only
in this one ordering, both driven through the real `assay run`:

| consumer command | `killed` | `crashed` | lane outcome |
|---|---|---|---|
| `apply && dump && test` | **1** | 0 | `PASS`, exit 0 |
| `apply && test && dump` | **0** | 1 | `ERROR`/`EXEC_FAILED`, exit 2 |

assay cannot verify this ordering itself — it sees two files, not a
pipeline — which is exactly why it is documented as a requirement with its
consequence rather than left to be discovered the first time a real kill
turns a lane red. See
[the consumer guide](CONSUMERS.md#the-command-order-is-one-token-wide-apply-dump-then-test-a-279)
for the worked shape, including how to make the companion `pg_dump`
reproducibility obligation red-on-violation in your own gate rather than
trusted silently.

## 12. Lane file structure = D7's three questions, literally

The file's shape carries the boundary rather than asserting it in prose. Top
level is **WHAT** (the project's own declaration), `[…judge]` is **HOW** (assay
reads it), `[…where]` is **WHERE** (an environment tool reads it). A project
adopting only assay writes no `where`; one adopting only ciu writes no `judge`.

```toml
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0","R1","R2","R3"]
enforcement = "gate"
argv = ["pytest", "tests/unit", "-q", "--cov-report=json:cov.json"]
env = { MOCK_MODE = "true" }
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["libs/common/src", "applications/controller/src", "scripts"]
fail_under = 100.0
allow_excluded = false
base = "origin/main"
coverage = { format = "coverage-py-json", artifact = "cov.json" }
mutation = { jobs = 4, max_mutants = 200, operators = ["python:compare-swap","python:boolop-swap","python:bool-const-flip","python:falsy-swap"] }
canary = { mechanism = "uncovered-line", target = "libs/common/src/pkg/mod.py" }
attestation_dir = ".assay/attestations"
evidence = [{source = "attested", key = "adversarial-review"}]

[lanes.package.where]
service = "test-runner"
instance = "worktree"
```

**Declared rigor is enforced, not merely recorded.** `R1` in its default
`changed_lines` mode makes all six of `judge.{coverage, fail_under,
allow_excluded, source_roots, language, base}` required to load (`base`
resolves nothing and is instead FORBIDDEN under `mode = "whole_target"` with
no R2 declared — above, "Two R1 modes, one claim per lane"); `R2` additionally
requires `judge.mutation`; `R3` additionally requires `judge.canary`. A lane
claiming R1 with no coverage config fails at parse time.
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

**Every lane declares R0 (A-154), in canonical order (A-192).** R2 and R3
execute variants of the declared command and therefore have no honest meaning
without its baseline identity; making that baseline implicit would omit a claim
Assay actually ran. R1/R2/R3 remain independently selectable after R0 — but
`rigor` is an *ordered subsequence* of `R0,R1,R2,R3`, refused at load time
otherwise. `R0,R2` and `R0,R1,R3` load; `R2`, `R1,R0` and `R0,R3,R2` do not.
"R0 is present and first" alone would leave a declaration order different from
the fixed order the runner executes and constructs claims in, which is either
an artifact-order mismatch or an implementer's choice about which of the two
wins. An `uncovered-line` R3 lane also declares R1 because only R1 can produce
its expected `UNCOVERED_LINES` cause — that prerequisite is checked at load
too, not discovered at run time.

**One budget covers the lane (A-160/A-212).** CLI starts its singular monotonic
deadline before resolving HEAD. It spans attestation reads/Git checks,
repository bootstrap and cleanliness, snapshot construction, baseline,
evaluation, every mutant, and both canary halves; it is not reset per
subprocess or per Git command. `max_mutants` is the independent deterministic
work-cardinality ceiling. Repeated executions start from the resolved commit's
tracked Git objects with the complete repository topology and project prefix
(A-161), never a working-tree copy containing ignored stale evidence. Ignored
or untracked files are not implicit command inputs; a declared ignored
attestation record is separately consumed once through its bounded safe-input
boundary before execution.

Note the consequence, because it is easy to get backwards: an **R0-only lane
without external evidence has no `[judge]` table at all**, and therefore
declares no `source_roots`, `fail_under` or `allow_excluded`. An R0-only lane may
instead carry exactly the both-present `attestation_dir`/`evidence` pair
(A-209), because those fields consume Tier-3 evidence rather than claiming a
computed rigor. Every computed judge field remains inert and forbidden on R0.
The five R1 fields are conditionally required, not unconditionally required.
A-018's "required per-lane field" means *per-lane rather than a global CLI
flag* — it does not mean "at the lane's top level".

**Closed vocabularies**, so a loader can reject rather than guess:

| Field | Values |
|---|---|
| `scope` | `S0` `S1` `S2` `S3` `S4` (TESTING-METHODOLOGY §Axis 1) |
| `rigor` | an R0-led ordered subsequence of `R0` `R1` `R2` `R3` (§Axis 2, A-192) — never an arbitrary list |
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

Before consumer migration, P25 qualifies the versioned installed wheel P24
produces (§14) against a disposable current Topos tree and its independent
changed-line gate (A-162). That is evidence that an existing Python project
can obtain the same R1 answer, not a claim that Topos has adopted Assay. The
real adoption package is carved later in Topos's own trove, after its active
wave permits a stable input revision.

**The qualification found a real adoption precondition rather than hiding it.**
Pinned Topos commits three absolute `/etc/passwd` symlinks as security-test
fixtures. Assay's A-186 committed-object boundary must refuse those paths for
every higher-rigor lane; filtering them inside Assay would weaken the product's
escape boundary. P25 therefore deletes exactly those three links only in its
disposable prospective consumer baseline, retains all five contained relative
links, and proves the full 2,923-test answer is unchanged. Actual adoption must
make that Topos-owned change (prefer constructing the hostile links under
`tmp_path`) before enabling Assay. Thus P25 proves Python/R1 and installed-wheel
compatibility for the exact prospective state while explicitly proving that the
unmodified current Topos tree is not directly adoptable (A-202).

P25 also keeps two wheel roles separate (A-205): the gate's current P24-built
run-venv wheel runs the full suite so later Assay changes remain externally
qualified, while a reproducible clean-tagged `1.2.5` fixture exercises P24's
release-manifest and pip hash path on a targeted smoke. The copied Topos
evaluator receives the exact bounded coverage bytes Assay consumed inside its
otherwise-ephemeral snapshot; it never consumes an expectation derived from
Assay's verdict (A-204).

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

## 14. Versioned wheel distribution (P24)

A-198–A-201 close the last unversioned corner: before P24, every wheel built
in `tester-unified` was `0.0.0`, because `setuptools-scm` was never part of
the build closure and setuptools silently falls through to a placeholder when
no plugin supplies a real version. A placeholder version is unsafe for a real
consumer to depend on — it cannot distinguish one release from the next — so
this is a distribution-safety fix, not a cosmetic one.

**The five-wheel closure is exact, not "whatever the resolver picks."**
`[build-system].requires` names `setuptools==84.0.0`, `wheel==0.47.0`,
`setuptools-scm==10.0.5`, `packaging==26.3`, `vcs-versioning==2.2.4` — the
*complete* transitive closure (`wheel` and `setuptools-scm` both need
`packaging`; `setuptools-scm` also needs `vcs-versioning`), hash-bound in
`gate/distribution/build-requirements.txt` and installed with pip's
`--no-index --require-hashes` from a committed wheelhouse. A network-disabled
resolver cannot invent a missing transitive dependency, so the old
three-package declaration was never a real closure — it built only because the
ambient interpreter's own bare `setuptools` was reachable via `PYTHONPATH`,
which is exactly the cockpit-green pathway §9 already rejects for the
standalone claim, recurring one layer down in the *build* tools instead of the
*runtime* ones.

**Four identities, one honest producer each:**

| source shape | identity | is it a release? |
|---|---|---|
| clean, tracked, tagged `assay-v1.2.3` | exactly `1.2.3` | yes — the only shape a release manifest may describe |
| same tree, one tracked mutation after the tag | `setuptools-scm`'s own `.dev…+g….d…` | no — a clean manifest must refuse it |
| tracked `pyproject.toml` + `src/**`, no `.git` at all | the declared `fallback_version = "0.1.0"` | no — a source-distribution witness only |
| the real, untagged vbpub clone (what the gate actually builds) | `setuptools-scm`'s own non-placeholder development identity | no — self-hosting development build |

None of these is a manual version file, an environment pretend-version
variable, or `0.0.0`. `src/assay/__init__.py`'s `importlib.metadata.version`
read (with its `0+unknown` source-import fallback) is untouched by any of
this — a wheel's version is a build-time fact about bytes on disk, never a
runtime guess.

**The gate builds from a private clone, never the live worktree.** A `cp -a`
of `src/**` followed by `git add` is not the same as `git ls-files` — it also
picks up whatever `__pycache__`/`*.egg-info`/`build/` residue happens to sit
in the working tree, and a *reproducible* contaminated wheel is still
contaminated (P24's own first probe did exactly this: a byte-identical but
582,556-byte wheel instead of the correct 232,651 bytes). So the registered
gate records the worktree's exact HEAD OID, makes a `--no-local
--no-checkout` clone of the worktree itself (no hardlinks/alternates back to
the caller's object store), sparse-checks out only `assay/`, and checks out
that exact OID detached — verifying the clone's own HEAD before building.
Ignored residue in the caller's tree structurally cannot reach a clone built
from committed objects.

**Verification is not installation.** `gate/distribution/release_wheel.py` is
a standalone, stdlib-only tool a consumer runs *before* Assay is installed:
`manifest` derives a closed four-field JSON document
(`schema_version`/`filename`/`version`/`sha256`) from one already-built
release wheel; `verify` re-derives the same facts (streamed sha256, then the
wheel's own bounded METADATA — exactly one `assay-<version>.dist-info/
METADATA` member, `Name`/`Version` re-checked independently of the hash) and,
only on success, prints one PEP 508 requirement line:

```
assay @ file:///abs/path/assay-1.2.3-py3-none-any.whl --hash=sha256:<64hex>
```

A separate check followed by an ordinary `pip install <path>` has a check/use
race — nothing stops the bytes at that path from changing between the two
opens. Feeding the verifier's own line into `pip install --no-index
--require-hashes -r <that file>` closes the gap: pip rechecks the identical
sha256 against the bytes it actually opens, so verification and installation
are bound to the same artifact by construction rather than by discipline.

**Two venvs for the wheel, not one.** `build-venv` gets the hash-checked
five-wheel closure and nothing else; `run-venv` gets the built wheel installed
with `--no-index --no-deps` and nothing else. The build closure never leaks
into the runtime venv's `sys.path`, so "zero runtime dependencies" is checked
against the *installed* artifact (`importlib.metadata.requires("assay")`,
extras aside) rather than merely asserted from the pinned `dependencies = []`
in `pyproject.toml`.

**And a third for the linter (B024/A-417).** The gate lints its own source: a
phase after the suite runs `pyflakes` over the private clone's `src/assay` and
emits `ASSAY_GATE_PHASE=pyflakes-clean`. The obvious place to put a linter is
one of the two venvs that already exist, and both are wrong. `run-venv` would
add a package to the environment whose whole job is to show that the installed
artifact needs nothing; `build-venv` would add a sixth wheel to a closure whose
five-pin assertion is a claim about *what can enter the wheel* — the assertion
would still pass, while quietly meaning less. So the linter gets `lint-venv`,
built from its own one-wheel wheelhouse
(`gate/distribution/lint-{requirements.txt,wheelhouse/,wheelhouse-manifest.json}`)
installed the same way the build closure is, `--no-index --require-hashes`.
pyflakes was fetched once on a networked host and committed; it is pure Python
with no dependencies, so that closure is one 63 KB file and needs no platform
matrix. The gate's network-disabled ingress is unchanged.

Two smaller choices in the same phase, for the same reason the wheel is built
from a clone: it lints the **private exact-OID clone**, so a gitignored stray
`.py` in the caller's worktree can neither redden the phase nor launder it;
and it runs **after** the suite, because a linter that reddens the gate before
the tests have spoken buys a five-second answer at the cost of the one the
reviewer needs. The rule set is pyflakes' whole rule set, which is exactly the
F-rule set other tools re-implement — undefined names, unused imports and
locals, redefinitions, placeholder-free f-strings — so there is no rule
selection to configure and none to drift. Scope is `src/assay`; `tests/` is
excluded on a recorded measurement (B062), not on taste.

## 15. Real Python-project qualification harness (P25)

§13 states the product claim (qualification, not adoption) and the exact
three-symlink adoption precondition; this section is the mechanism that
proves it. `gate/python/qualify_topos.py` runs inside the registered gate,
between `run_self_hosted_lane` and `run_independent_witness`, against the
CURRENT run-venv wheel `run_self_hosted_lane` already proved.

**One disposable baseline, reconstructed per scenario, never the real
checkout.** `git archive --format=tar` exports only the pinned commit's
`.gitignore` and `topos/` tree into a fresh scratch directory; the exact
three absolute `/etc/passwd` symlinks are verified present and deleted, the
five relative contained symlinks are verified retained, and the exact
966-minus-3 tracked set is `git add -f`'d (never ordinary `add`, which
silently drops four tracked-but-ignored Docker fixtures under the carried
root `.gitignore`) to a fixed-identity, fixed-date commit. That commit is
`base`; the scenario's own probe/test/wrapper/`assay.toml` land in one more
commit on top, which is `HEAD`. Same content plus the same fixed identity
reproduces the identical baseline OID regardless of which scenario runs on
top of it — a real, checked property, not an assumption.

**Two Assay owners, never conflated.** The gate's own `current_assay`
(`$scratch/run-venv/bin/assay`) runs the full 2,923-test suite plus every
integrity negative, so future Assay changes stay externally qualified. A
separate, hash-installed, clean-tagged `1.2.5` release venv — built the same
way `install_locked_release` proves any consumer could — runs one targeted
smoke. Neither route ever selects a wheel by glob, rebuilds one at runtime,
or substitutes for the other.

**Three independent witnesses per common-semantics scenario.** Installed
Assay emits its own v4 verdict; a bounded, non-interpreting wrapper copies
the exact coverage bytes Assay consumed inside its ephemeral snapshot to an
external path *after* pytest exits zero (Assay's own snapshot is destroyed
before an outside process could read it otherwise); and the unmodified,
committed `topos/tools/coverage_gate.py` parses that same copy against the
identical `base..HEAD` diff. That third witness is compared by its NUMBERS
against the carver-owned hand manifest, never by its `passed` flag alone:
Topos defines `pct = 100.0` whenever `changed_executable == 0`, so a scenario
that measured nothing would "agree" with a truthful 5/5 run on the boolean —
and the release smoke, which carries no complete-artifact template, is exactly
where that would have gone unnoticed. `compare_complete_artifact` then does ONE
whole-document equality check against a locked template, normalizing only
the fields whose real value it already checked separately (version, commit,
base, timestamps, the declared/effective environment, the witness/log
paths) — never a status-only or field-by-field comparison a forged-but-
self-consistent artifact could pass.

**The integrity matrix runs for real, not as a checklist.** Each frozen
terminal — a missing coverage profile, a dirty consumer, a symbolic base
that resolves to HEAD, a lane command that leaves tracked dirt or commits
inside its own snapshot, a wrong-but-existing source root, and a forged
universal-PASS artifact — is exercised against the real pinned Topos tree
and the real installed Assay, and each check is its own oracle: it raises
unless the observed terminal (or comparator rejection) exactly matches the
frozen expectation. One asymmetry is deliberately never treated as a
mismatch: Topos cannot express exclusion provenance, so `allow_excluded =
false` correctly produces Assay `FAIL/EXCLUDED_LINES` against a Topos
`PASS` — recorded as the expected capability gap, not compared as a
terminal.

---

## 16. Where documentation lives, and why it merges with the code (A-270)

§11 says where language-specificity lives. This says where *knowledge about the
product* lives, for the same reason: when a question has no single home, every
document grows a partial answer and they drift apart.

**Three documents, one job each.**

| document | its one job |
|---|---|
| **`README.md`** | **WHAT** assay does — the user-facing feature surface. A reader deciding whether assay solves their problem stops here. |
| **`docs/DESIGN-GUIDE.md`** (this file) | **WHY** it does it that way — choices, rejected alternatives, implementation reasoning, and the arguments that must not be re-had. |
| **`docs/CONSUMERS.md`** | **HOW** to adopt it — worked examples and real use cases, in a form an adopter can paste. |

The boundaries are load-bearing in both directions. A README that argues its
own rationale becomes a second, diverging design guide — and it had already
started. A design guide that lists features becomes a second, staler README.
So: **a README feature links here rather than re-arguing; this file explains
rather than enumerating; CONSUMERS.md shows rather than describes.**

**Documentation merges with the change, not after it.** A capability is not
shipped when its code is green; it is shipped when someone who does not know it
exists can find it, understand why it works that way, and adopt it. Any work
item that adds, removes or changes a user-facing capability, a public config
key, a closed vocabulary value, or a compatibility fact is **incomplete until
all three are in sync**, and each such work item names the affected documents in
its own file list — a doc obligation that is not written down is a doc
obligation that is skipped.

**Three checks make this a test rather than an intention**, because "we will
remember" is exactly the check that cannot fail, and a check that cannot fail is
this project's most expensive recurring defect (A-124, A-131):

1. every TOML example in all three documents **parses with the shipped loader**
   and declares the current `LANE_SCHEMA_VERSION`;
2. every value of every **closed public vocabulary a consumer must type** —
   `isolation.snapshot_selection`, `judge.mode`, the rigor levels, the coverage
   `format` registry, the closed `ReasonCode` vocabulary (A-277), and
   (P34/A-287) `judge.mutation.operators` scoped to every REGISTERED
   language's own catalogue — appears in at least one of the three, so a
   capability cannot ship undocumented;
3. every DESIGN-GUIDE anchor the README links to **resolves**.

A documentation example is a claim, and A-232 applies to it unchanged: it is
evidence only if something executed it.

**Why this is a ruling and not a habit.** Wave 1's plan went through a carve,
three failed adversarial review rounds, a complete recarve and an independent
review — and every one of them missed that this README's headline bullet said
"changed-line coverage, *not* whole-project coverage" while the wave was
shipping precisely the whole-target mode that sentence denies, and that
`CONSUMERS.md` appeared in no work item at all while its adoption steps had gone
stale against a newly mandatory `[isolation]` table. The trove documents were
immaculate throughout, because the process touches them daily. The documents
facing a human adopter were touched only when someone remembered, and nobody
did.

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
