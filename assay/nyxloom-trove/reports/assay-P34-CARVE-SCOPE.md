# P34 — carve SCOPE (not a carve, not dispatchable)

**Author:** C-sol-1, 2026-08-11. **Anchor:** main `0239513a`.

> **THIS IS NOT A JIT CARVE.** No asset here is locked, no hash is recorded, no
> packet exists. It is the pre-carve scope: what P34 inherits, what it must
> probe before freezing anything, and the two things that could still change its
> contract. **P34 must not be dispatched for pre-dispatch review** until the
> operator rules on the pending item in §4.
>
> **P34 is also gated by ship** (A-248). Ship is implemented but not published
> (A-249/A-250): the first real release still needs `main` pushed and explicit
> authorisation. Carving P34 is allowed; dispatching it is not, on two counts.

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
| **A-241's verify fix** | **ALREADY DONE** — `a7c16d0c`/A-245 | **Nothing.** Fable's Addendum 3 says A-241 "stays scheduled with P34's verify touch"; that is now stale. P34 must not re-fix it. Its caveat was also answered: Fable asked for a `run_lane` control-flow read to settle `BASE_IS_HEAD`'s co-occurrence, and A-245 records that read — `BASE_IS_HEAD` sits inside `if r2_declared and result.outcome is Outcome.PASS`, so it requires a passing baseline, while `DIRTY_TREE`/`HEAD_CHANGED`/`GIT_FAILED` do not. |

## 3. What P34 must PROBE before freezing anything

B001's probe list minus what is already answered. Three remain, and the first is
the one that decides the package.

### 3a. The parser, and the A-005 collision — THE central question

assay ships **zero runtime dependencies, stdlib only** (A-005), enforced
mechanically. A DDL parser is therefore one of exactly two things, and they are
not close in consequence:

- **(i) A stdlib-only bounded DDL scanner inside assay.** No new dependency, no
  new reachability. But hand-writing SQL parsing is the kind of work that looks
  cheap for seven `DROP`-shaped operators and is not: the operators must find a
  *byte span* in tracked source, and a scanner that mis-locates a span produces
  a mutant that is not the mutation it claims. Bounded scope helps — only
  changed tracked DDL, only these seven operators — but the honesty of every
  `sql:` claim rests on it.
- **(ii) An external helper declared in `external_tools`.** Matches V5-5's
  `helpers` provenance design, which exists for exactly this shape, and A-243
  already rules on its failure terminal. **But it makes
  `NO_MEASUREMENT/MISSING_EXTERNAL_TOOL` reachable for the first time in
  assay's history** — verified: that reason code has no producer anywhere in
  `src/`, only the enum member and the `REASON_CODES` entry.

**That is a cross-package collision the carve must surface rather than
resolve silently.** A-246 recorded that effective-PATH reachability hopped
P22 → P26 → **P27**, and said P27's re-carve must claim it explicitly because
A-217's oracle would be assay's first real external tool. If P34 takes route
(ii), **P34 becomes the first external-tool consumer and inherits that
reachability instead** — and A-246's assignment needs revisiting by decision,
not by whichever package writes the code first. Route (i) leaves A-246 intact.

The probe must therefore report which route it takes *and* what that does to
A-246, as a single finding.

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

## 4. What could still change P34's contract — DO NOT DISPATCH UNTIL RULED

**Pending operator decision: whether to close the hollow-PASS/FAIL schema gap
before P34.** Fable's round-4 recommendation
(`reports/assay-v3-post-P33-review-fable.md`, Addendum 3 Part 2) is to close it,
~75/25, as a **small standalone carver-owned change before P34's dispatch,
not folded into P34** — on the ground that the narrowed doctrine (A-237/A-240)
is factually false about the shipped schema, which already enforces requiredness
in three places (`NO_MUTANTS`, `MUTANT_LIMIT_EXCEEDED`,
`ALL_MUTANTS_EQUIVALENT`).

Why it matters to P34 specifically, rather than being a neighbouring concern:

- It would tighten the **schema** P34's SQL artifacts validate against, and
  `SCHEMA-V5-DESIGN.md`'s SQL template is the keystone expected artifact P34
  freezes. A carve frozen before the ruling would lock a template against a
  contract that then moves.
- Fable's own named flip condition is **"if the operator already expects a v6 in
  P34's near wake"** — and `SCHEMA-V5-DESIGN.md` names `per_mutant_seconds` as a
  reopen trigger that real SQL runs could fire. So P34's own probe results could
  decide *how* the gap gets closed (v5 in-place vs fold into v6). That
  dependency runs both ways, which is exactly why the ruling comes first.

Nothing in this scope document depends on the answer. Everything in §2 and §3
stands either way; only the freeze does not.

## 5. Model and review

Per `handoffs/README.md`: class 2d, **Sonnet xhigh** implementing, **fresh Opus
xhigh** for the mandatory pre-dispatch adversarial review, commissioned by the
controller and not by this carver (A-216). This is Fable's last round in the
wave — carve/review reverts to CR-opus-0 only from here.
