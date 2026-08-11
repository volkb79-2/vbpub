---
kind: north-star
schema_version: 1
---

# assay — north-star

**The problem.** Every project in this estate had a test gate, and every gate
answered a slightly different question, in a slightly different way, because
each was a fork of the last. Four diverged `coverage_gate.py` copies (nyxloom,
dstdns, topos) plus a full Go re-implementation (srdm's `covergate`) is not a
tooling accident — it is what happens when a capability is priced at "adopt an
orchestrator first" rather than "install a library and write a config file". A
consumer needed changed-line coverage badly enough to *rewrite it in another
language* rather than take a dependency it could not consume standalone.

Worse than the duplication is what those gates report. Whole-file coverage
answers a question nobody asked: it moves when unrelated files move, and stays
still when the lines a change actually introduced are untested. A gate that
reports 87% and a gate that reports "the eleven lines you added are covered"
are not two precisions of the same claim; they are different claims, and only
one of them is about the change under review. And a mutation counter that
treats any non-zero exit as "killed", with no timeout, reports a number that
cannot distinguish a real kill from a crash from a hang.

**The mission.** assay answers exactly one question — *how do you judge a
change?* — and answers it the same way for every project, every language, and
every commit, in a form a machine can check afterwards. It does not choose what
to run (the project's `assay.toml` does) and it does not choose where to run
(an environment tool such as `ciu` does).

The one invariant, from which every exclusion follows:

> **assay never renders a judgement it cannot make deterministically.**

## The strategic contract — what assay guarantees a consumer

- **One machine-readable verdict per lane per commit.** The verdict artifact is
  the *only* external contract (A-029). Not the exit code alone, not stdout, not
  a receipt the producer wrote about itself.
- **Rigor is declared, not discovered.** A lane states `R0`–`R3` as a
  prefix-closed ladder and gets exactly what it declared. There is no implicit
  upgrade and no silent downgrade: a lane that declares R2 and cannot measure
  it says so, in the artifact, with a closed reason code.
- **Refusal is a first-class outcome.** `NO_MEASUREMENT`, `ERROR`,
  `BUDGET_EXCEEDED` and `INCONCLUSIVE` are real verdicts with real exit codes,
  never coerced into `PASS` or `FAIL`. A dirty tree, a moved HEAD, a missing
  tool, a blown budget and an unparseable source file each have their own
  terminal. **0/0 is never 100%.**
- **A verdict can be re-checked by something that did not produce it.**
  `assay verify` reads an artifact and answers whether it is schema-conformant
  and internally self-consistent — re-deriving each claim's status from its own
  payload rather than trusting the status the producer wrote. The two halves of
  the product disagreeing about one document is a defect, and it is one the
  suite is built to catch.
- **A schema version is a consumer migration, never an in-place upgrade**
  (A-138/A-170). An artifact from another version is *rejected*, with the
  version named as the single actionable sentence — never coerced, never
  silently read with the wrong reader.
- **Adoptable standalone.** `pip install assay`, write a lane, run it. No
  orchestrator, no daemon, no service. That price is the whole point: it is the
  one lesson srdm's rewrite already taught.

## The technical bets — the ideas assay will not compromise on

- **Zero runtime dependencies, stdlib only** (A-005), enforced mechanically
  rather than by convention. A gate that a project cannot install offline into
  an arbitrary image is a gate that project will fork instead.
- **Changed-line, not whole-file.** The unit of judgement is the set of lines a
  change introduced, resolved from a real `git diff` against a declared base.
  Pre-existing uncovered code outside the diff is invisible to the verdict by
  construction, because it is not what is under review.
- **Three tiers of evidence, never conflated.** Tier 1 assay *computes*
  (R0–R3). Tier 2 assay *invokes* a declared third-party tool and applies a
  *declared threshold* to its structured output — the judgement is the tool's.
  Tier 3 assay *ledgers*: it validates shape, binds to a commit and checks
  staleness, and **never verifies**. Conflating these is how a testing tool
  becomes a policy engine or an LLM harness. Neither is on the roadmap; both
  are named exclusions.
- **Real gates, not receipts.** Judgement runs the project's own declared
  command, and correctness claims are proved by writing real inputs to disk and
  driving the shipped entry point — not by re-running the fixtures the
  implementation was written against.
- **Two independently-verified layers** (A-071). Every artifact rule that can
  be expressed in the schema is expressed there *and* in the dataclass model;
  cross-object rules that JSON Schema 2020-12 genuinely cannot express live in
  the model and the raw verifier. One witness per rule is how a rule dies
  quietly.
- **A closed vocabulary, derived not transcribed.** Outcomes and reason codes
  are a closed partition, and re-derivation reads the partition rather than a
  second hand-maintained table that could drift from the vocabulary it checks.
- **Isolation by committed objects, not by copying a working tree.** Higher
  rigor runs against a snapshot built from the commit's own reachable object
  closure, with declared ceilings. A mutant runs against an immutable
  replacement, and a unit that leaves Git-visible state behind stops the lane
  rather than contaminating the next one.
- **One budget per lane, started once.** A single deadline governs evidence
  loading, HEAD resolution, the command, and every mutant — never a fresh
  per-phase timeout that lets a lane spend its budget several times over.
- **The language boundary is a protocol, not a fork.** A `LanguageAdapter`
  declares what its language needs; the core stays language-free. This seam was
  discovered twice independently — assay's protocol and srdm's Go `Evaluate`
  signature factored out the same four parameters.

## What assay is deliberately not

It is not a test runner, a test framework, a policy engine with a rule
language, an LLM-mediated reviewer, an orchestrator, or a CI system. It does
not remediate test debt it finds. It does not upgrade artifacts in place. Every
one of those exclusions follows from the single invariant above, and each is
recorded with the argument that produced it rather than as a taste.
