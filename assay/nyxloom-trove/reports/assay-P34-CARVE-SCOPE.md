# P34 — carve SCOPE (not a carve, not dispatchable)

**Author:** C-sol-1, 2026-08-11. **Anchor:** main `0239513a`.

> **THIS IS NOT A JIT CARVE.** No asset here is locked, no hash is recorded, no
> packet exists. It is the pre-carve scope: what P34 inherits, what it must probe
> before freezing anything, and what has since been ruled.
>
> **UPDATED 2026-08-11 at `6750e7c1`: both of §4's gates are now CLEARED.** The
> hollow-PASS/FAIL ruling landed (A-251/A-252 — closed, not folded into a v6),
> and the `MISSING_EXTERNAL_TOOL` collision is ruled (A-253 — P34 owns the
> preflight). **The remaining gate is operational, not decisional: ship is
> implemented but unpublished (A-249/A-250), and A-248 puts P34 behind it.**
> The carve itself is now the next unit of work; it is not in this document.

## 1. What P34 delivers

A **source-oriented SQL/DDL `LanguageAdapter`** plus real-PostgreSQL
qualification. B001's authoritative disposition fixes the shape, and it is not a
database-connected adapter:

```text
tracked DDL + changed lines
  -> SQL parser/helper -> bounded MutationSite byte spans
  -> assay's immutable replacement snapshots
  -> the unchanged project-declared argv
  -> project-owned fresh test-database provisioning and schema tests
```

assay receives no DSN, opens no connection, and mutates no shared database. The
adapter learns syntax; the language-free core keeps bounds, isolation,
execution and verdicts.

## 2. What P34 INHERITS — decided, not to be re-litigated

| inherited | ruling | what the carve owes |
|---|---|---|
| The operator vocabulary | v5 freezes exactly seven: `sql:drop-check`, `sql:drop-foreign-key`, `sql:drop-not-null`, `sql:drop-trigger`, `sql:drop-unique`, `sql:weaken-delete-action`, `sql:widen-check-in` | Implement these and only these. A eighth is an additive enum change, not a P34 liberty. |
| The protocol shape | **A-242**: the flat seven-method `LanguageAdapter` stays; the five SQL-meaningless methods **raise** rather than return a plausible value, per `verify.py`'s `_BASELINE_NEVER_READ` precedent | **One sentence** distinguishing that from `GoAdapter.statement_spans` returning `None`, which is *correct by contract* under Go's declared `requires_span_attribution = False`. The two look identical in a diff and mean opposite things. |
| Helper provenance on failure | **A-243**: a payload-free `helpers` entry is permitted for `MUTATION_DISCOVERY_FAILED` **only** | Both layers, plus the negative: a `mutation-sites` helper beside `MUTATION_UNSUPPORTED` must still be refused. The successful-helper-on-unrelated-failure sub-case is deliberately still open and belongs to this carve, looking at real SQL runs. |
| No SQL R3 | `SCHEMA-V5-DESIGN.md`: R3's `uncovered-line` canary needs R1 coverage, A-192 forbids R3 without R1, and DDL has no coverage tool. SQL lanes are `R0,R2` | Nothing. B001 probe item 5 is answered; do not reopen it to be thorough. |
| Refused-until-produced fields | A-227/A-230: `kill_signal_artifact` and `equivalence_artifact` are refused at config load **until P34 supplies producers** | P34 is that package. Supplying a producer means the refusal is lifted for SQL — and the refusal for every other language must survive. |
| B001 probe items 2 and 3 | Answered by v5 and A-242 respectively | Do not re-run them. |
| The schema P34's keystone template freezes against | **A-251/A-252**: four new `claim.allOf` branches require the payload a judged status was judged from, and A-182's original doctrine is restored | The SQL keystone template must satisfy them — an `R0,R2` SQL lane reporting `MUTANTS_SURVIVED` now needs its `survived` bucket at the SCHEMA layer, not only in the model. **A-252 also binds P34: if its probe wants a further v5 tightening, that needs its own ruling — this precedent is spent** (A-236b was the first, A-251 the second). |
| `MISSING_EXTERNAL_TOOL`'s producer | **A-253**: P34 owns the effective-PATH preflight, unconditionally, superseding A-246's assignment to P27 | Build it **whether or not** the DDL parser turns out to be external. If P34 goes stdlib-only, the preflight is still built and tested against a declared-empty `external_tools` tuple — which is the honest control anyway, and it is what lets P27's re-carve merely *declare* its helper. |
| **A-241's verify fix** | **ALREADY DONE** — `a7c16d0c`/A-245 | **Nothing.** Fable's Addendum 3 says A-241 "stays scheduled with P34's verify touch"; that is now stale. P34 must not re-fix it. Its caveat was also answered: Fable asked for a `run_lane` control-flow read to settle `BASE_IS_HEAD`'s co-occurrence, and A-245 records that read — `BASE_IS_HEAD` sits inside `if r2_declared and result.outcome is Outcome.PASS`, so it requires a passing baseline, while `DIRTY_TREE`/`HEAD_CHANGED`/`GIT_FAILED` do not. |

## 3. What P34 must PROBE before freezing anything

B001's probe list minus what is already answered. Three remain, and the first is
the one that decides the package.

### 3a. The parser, and the A-005 collision — THE central question

assay ships **zero runtime dependencies, stdlib only** (A-005), enforced
mechanically. A DDL parser is therefore one of exactly two things:

- **(i) A stdlib-only bounded DDL scanner inside assay.** No new dependency. But
  hand-writing SQL parsing looks cheap for seven `DROP`-shaped operators and is
  not: the operators must find a *byte span* in tracked source, and a scanner
  that mis-locates a span produces a mutant that is not the mutation it claims.
- **(ii) An external helper declared in `external_tools`.** Matches V5-5's
  `helpers` provenance design, and A-243 already rules its failure terminal.

**The ownership half of this is now settled and no longer a finding: A-253 gives
P34 the `MISSING_EXTERNAL_TOOL` preflight either way.** So the probe's job is
narrowed to the engineering question — which route produces honest byte spans —
rather than also having to negotiate a cross-package boundary mid-carve. Report
the route and the span-fidelity evidence; the reachability question is closed.

### 3b. Fresh-database isolation, owned by the project command (B001 item 4)

Cleanup, baseline, and false-kill defences. The property to prove is that each
mutant is judged against a provably isolated database state; the mechanism
belongs to the project's declared argv, not to assay. The probe needs a real
false-kill attempt — a mutant that "passes" because a previous mutant's
residue is still in the database is the failure mode, and it will not show up
in a fixture that runs one mutant.

### 3c. Exact dstdns evidence at a pinned revision (B001 item 6)

B001 names real files: `scripts/schema-gate.sh`, `tests/schema/`,
`docs/proposals/cw2-p85-wave/REVIEW-CW2A.md` findings C2/C3/C14, and a mock
lane at `libs/common/tests/test_backend_foundations.py:117-129` where "a syntax
error on line 2 would pass". Pin the revision the way P25 pinned Topos and P27
pinned image digests — a symbolic reference makes the qualification
unreproducible.

## 4. What could still change P34's contract — NOTHING DECISIONAL REMAINS

Both items that stood here are ruled:

- **The hollow-PASS/FAIL gap: CLOSED** (A-251), in the schema and the raw
  verifier, and deliberately *not* folded into a hypothetical v6. Fable's flip
  condition was checked against `SCHEMA-V5-DESIGN.md`'s own text and does not
  hold: the `per_mutant_seconds` trigger says "Measure before adding" and is
  conditional on a measurement P34's probe has not taken. So P34 freezes against
  a schema that has just been tightened and is not expected to move again —
  which is the position a keystone template should be frozen from.
- **The `MISSING_EXTERNAL_TOOL` collision: RULED** (A-253), to P34.

What remains is operational: **ship is implemented but unpublished**
(A-249/A-250 — `cmru release --project assay --dry-run` correctly refuses while
`main` is 103 commits ahead of `origin/main`), and A-248 sequences P34 behind it.
That is a publish-and-authorise step, not a design question.

**One thing P34's carve must NOT do:** re-touch A-241's partition (A-245). That
rule is closed, it is a different mechanism at a different layer, and the
layering split is now exact — hollow-green lies are schema-refused (A-251),
wrong-reason lies are verify-refused (A-245), and the model backstops both.

## 5. Model and review

Per `handoffs/README.md`: class 2d, **Sonnet xhigh** implementing, **fresh Opus
xhigh** for the mandatory pre-dispatch adversarial review, commissioned by the
controller and not by this carver (A-216). This is Fable's last round in the
wave — carve/review reverts to CR-opus-0 only from here.
