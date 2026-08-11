# Schema v5 — the design, and what it deliberately does not do

> **SCHEMA OWNERSHIP — A-237's NARROWING IS SUPERSEDED (A-251).** A-182's
> original one sentence stands again: the schema owns **every locally
> expressible rule**, requiredness of evidence included. A payload-free PASS no
> longer validates — four `claim.allOf` branches now require the payload a
> judged status was judged from (R1 PASS/FAIL ⇒ `coverage`; R2 PASS ⇒
> `mutation`; R3 PASS/FAIL/INCONCLUSIVE ⇒ `canary`; `MUTANTS_SURVIVED` ⇒
> `mutation`), and `verify.py` carries the same rule as an independent witness.
>
> A payload-free R2 **FAIL** is still valid, and must stay so: it is A-116's own
> truthful propagation shape. So are the payload-free ERROR / NO_MEASUREMENT /
> BUDGET_EXCEEDED terminals.
>
> Why the narrowing fell: it claimed the schema does not own requiredness, while
> the shipped schema had owned it in three places since P21/P33 (branches 7, 8
> and 9, keyed on `NO_MUTANTS`, `MUTANT_LIMIT_EXCEEDED` and
> `ALL_MUTANTS_EQUIVALENT`). Both of A-240's justifications for leaving the gap
> open are on record as wrong — the first false, the second measured against an
> overbroad branch set. Read **A-251** for the closure, **A-252** for the
> in-place-tightening rule it is bound by, and A-237/A-240 only for the trail.
>
> **PRECEDENCE (A-231).** This document was written before three pre-dispatch
> repair rounds. Where it conflicts with `carve-assets/P33/verdict.schema.v5.json`
> or with a named decision (A-221, A-223 – A-230), **the locked schema and the
> decision win.** Two known conflicts are corrected inline below and marked
> SUPERSEDED; treat any other divergence the same way and raise it rather than
> following the prose.

Author: C-sol-1 (design authority under A-216), 2026-08-11.
Anchor: main `5a7af3f6`. Supersedes nothing; v4 artifacts are never upgraded in
place (A-138/A-170 — a schema version is a consumer migration).

v5 exists to make SQL mutation expressible and to give Go's eventual
statement-position helper an honest place in the artifact. The operator has
explicitly accepted that a v6 may be needed once the Go re-carve resumes; this
document therefore records what is **designed**, what is **deliberately
deferred**, and the **named trigger** for each deferral. Deferrals are not
oversights and should not be re-litigated as such.

Evidence base: the P27 probe (`carve-assets/P27/`), its CR-opus-0 review, and
the B001 assessment. Nothing here rests on a capability nobody has specified.

---

## V5-1 — Hoist `language`, `source_roots` and `base` out of `judgment.r1`

**This is the keystone change, and it is a bug fix rather than an
accommodation.**

In v4, `judgment.r1` *requires* `language`, `source_roots` and `base`, and
`judgment` permits `{"r2": {...}}` with no `r1` at all. A-192 already makes
`R0,R2` a legal rigor declaration. So a v4 `R0,R2` lane — Python or Go, today,
with no SQL involved — produces an artifact that records **no language, no
source roots, and no comparison commit**, even though R2 mutation is scoped to
changed lines against exactly that commit. SQL is simply the first language for
which `R0,R2` is the *only* honest declaration, which is why the hole becomes
unavoidable rather than merely reachable.

`base` must hoist for the same reason `language` must: mutation targets changed
lines (P12/A-116), so it consumes the same resolved comparison commit R1 does.

New required object, present whenever `judgment` is present.
> **SUPERSEDED IN PART by A-223(a).** `base` inside `judgment_resolved` is
> **conditional**, not unconditionally required: required iff `judgment` carries
> `r1` or `r2`, and forbidden otherwise (A-227 closed the only-if half).
> `JUDGE_FIELDS_BY_RIGOR` carries `base` for R1/R2 and not for R3, so an `R0,R3`
> lane genuinely has none. The `required` list in the fragment below is stale;
> the locked schema is authoritative.

```json
"judgment_resolved": {
  "description": "What was judged, shared by every computed tier above R0. Hoisted out of judgment.r1 in v5 because an R0,R2 lane (legal since A-192) renders no r1 and would otherwise record no language, source roots, or comparison base at all.",
  "type": "object",
  "required": ["language", "source_roots", "base"],
  "properties": {
    "language": {"type": "string", "minLength": 1},
    "source_roots": {
      "type": "array", "minItems": 1,
      "items": {"type": "string", "minLength": 1}
    },
    "base": {
      "description": "The full resolved comparison commit, never a symbolic ref.",
      "type": "string", "minLength": 1
    }
  },
  "additionalProperties": false
}
```

`judgment` gains `"resolved": {"$ref": "#/$defs/judgment_resolved"}` and adds
`"required": ["resolved"]`. `judgment_r1` drops those three keys and keeps
`coverage_format`, `coverage_artifact`, `fail_under`, `allow_excluded` — which
are genuinely R1 policy rather than lane facts.

Consequence for the migration: every v4 fixture carrying `judgment.r1` moves
three keys. Mechanical, but it touches every expected artifact in the repo, and
that cost is the reason this is a package rather than a patch.

## V5-2 — Language-qualified mutation operators

v4's `mutation_operator` is a closed four-value enum
(`compare-swap`, `boolop-swap`, `bool-const-flip`, `falsy-swap`), all Python
expression-level. SQL's operators cannot be honestly spelled in it, and a flat
extension would let a Python lane declare `drop-check`.

v5 makes the vocabulary namespaced and closed **per language**:

```json
"mutation_operator": {
  "description": "A language-qualified operator name. The prefix must equal judgment.resolved.language; that equality is cross-object and therefore lives in the model and the raw verifier, not here (same convention as mutant_outcome's byte-order note).",
  "oneOf": [
    {"enum": ["python:compare-swap", "python:boolop-swap",
              "python:bool-const-flip", "python:falsy-swap"]},
    {"enum": ["sql:drop-check", "sql:drop-unique", "sql:drop-not-null",
              "sql:drop-foreign-key", "sql:weaken-delete-action",
              "sql:drop-trigger", "sql:widen-check-in"]}
  ]
}
```

Three deliberate choices:

**Go gets no operators.** ~~Not an omission — P29 owns the Go mutation helper,
and until it lands a Go R2 declaration must fail at load rather than run and
report nothing.~~
> **SUPERSEDED by A-221/A-225.** `go:*` carries three operators —
> `go:compare-swap`, `go:boolop-swap`, `go:bool-const-flip` — transcribed from
> `_COMPARE_SWAP` under A-112, with no `falsy-swap` analogue. And the
> load-versus-runtime argument in the struck text is wrong about the shipped
> product: `_resolve_declared_adapters` refuses an unregistered language before
> execution either way. This matches the design guide's existing "Go returns `UNSUPPORTED`
until P29 lands its helper". When P29 arrives it adds a third `enum` branch; that
is an additive change to a `oneOf`, which is the one shape here that a future
package can extend without a v6.

**`sql:weaken-delete-action`, not `restrict-to-cascade`.** The operator class is
"replace a referential action with a strictly weaker one", covering
`RESTRICT`→`CASCADE` and `RESTRICT`→`NO ACTION`. Naming the class rather than one
instance keeps the catalogue honest and finite.

**Python's four are renamed, not aliased.** `compare-swap` becomes
`python:compare-swap`. There is no back-compatible spelling, which is precisely
what makes this a version bump rather than a widening.

`mutant_outcome.operator` and `judgment.r2.operators` both keep
`$ref: mutation_operator` and inherit the qualification for free.

**Site representation is unchanged, and that is the good news.** v4's
`mutant_outcome` identity is already `(path, start_byte, end_byte,
replacement_sha256, operator)` over zero-based half-open UTF-8 byte offsets.
That *is* the source-oriented byte-span design A-215 preferred, so SQL needs no
new site shape. The B001 assessment's conclusion that the byte-span approach is
sound is confirmed by the schema already fitting it.

## V5-3 — A fifth mutation bucket: `equivalent`

The write-side lossiness found in the B001 assessment: mutating tracked DDL bytes
does not guarantee the deployed schema changed. A migration-chain project can
have migration 3's `CHECK` overwritten by migration 17, and idempotent
`CREATE … IF NOT EXISTS` DDL is a no-op against an already-provisioned database.
Such a mutant *survives*, and v4 would report it as "no test asserts this
constraint" — which is false, because the constraint is still enforced.

`mutation` gains a fifth bucket. Because v4's `mutation` is
`additionalProperties: false`, this cannot be added within v4:

```json
"equivalent": {
  "description": "Mutants proven to produce no change in the judged artifact, established by byte comparison of the declared equivalence artifact (V5-3). NOT survived: a no-op mutant is evidence about the mutation, not about the tests. Excluded from the mutation-score denominator.",
  "type": "array",
  "items": {"$ref": "#/$defs/mutant_outcome"}
}
```

added to `required`, and `judgment_r2` gains:

```json
"equivalence_artifact": {
  "description": "A project-relative path the lane's own declared command writes after applying a mutant, whose bytes Assay compares against the baseline run's. For SQL this is a schema-only dump. Assay compares two artifacts; it never opens a database connection or receives a DSN.",
  "type": "string", "minLength": 1
}
```

**Both-present-or-both-absent, following A-209's attestation pair:** if
`equivalence_artifact` is not declared, the `equivalent` bucket must be empty.
No declaration, no equivalence claims — an implementation may not infer
equivalence from anything else.

This is the one addition that pays for itself twice: it defuses the
migration-fold false-survival *and* it keeps Assay's no-DSN boundary exactly
where A-215 put it, because comparing two command-written artifacts requires no
database access at all.

Score arithmetic — `killed / (killed + survived)`, with `equivalent` excluded
from both — is cross-object and belongs to the model and raw verifier, per the
existing convention for `candidate_count`/`total` arithmetic.

## V5-4 — Kill attribution, made visible rather than closed

For SQL, `survived` is sound inference and `killed` is not. dstdns's own
witnessed case: a test asserting `pytest.raises(RestrictViolationError)` passed
against a schema with `ON DELETE RESTRICT` removed entirely, because a trigger on
the same table raised the same SQLSTATE. A mutant can be killed by something
other than the assertion meant to be testing it.

**What a schema can and cannot do here, stated plainly.** Assay cannot verify the
database's causality — it observes the declared command's exit status and
declared artifacts, not which constraint fired. So v5 does **not** close this
gap. What it does is stop the gap being invisible, which is the A-208 lesson
(bind the tuple, do not report the boolean alone):

`judgment_r2` gains a **required** discriminator:

```json
"kill_attribution": {
  "description": "Whether kills in this run are attributed to a declared mechanism or merely observed. 'unattributed' is honest and common; it means a killed mutant proves only that the suite failed, not that it failed for the reason the mutant created.",
  "enum": ["declared", "unattributed"]
}
```

plus optional `kill_signal_artifact` (a project-relative path the command writes
naming the mechanism that refused), and `mutant_outcome` gains an optional
`kill_signal` string.

Discipline: `kill_attribution: "declared"` requires `kill_signal_artifact` and
requires every `killed` entry to carry `kill_signal`;
`kill_attribution: "unattributed"` forbids both. Assay verifies that
correspondence, which is real checking — it just is not a causality proof, and
the artifact now says which one it is.

This also states a **consumer obligation** A-215 omitted: a project qualifies for
attributed SQL R2 only if its tests surface the refusing mechanism. Otherwise it
gets `unattributed`, and its mutation score must be read accordingly.

## V5-5 — Helper provenance as a first-class fact

Go's eventual statement-position oracle (A-217) and SQL's parser are the same
architectural shape: an adapter shelling out to a language tool that must be
exact. The P27 probe's whole pinning exercise existed because a symbolic
toolchain reference makes a verdict unreproducible — `pinned-environment.json`
records image digests for precisely that reason. The artifact currently records
`argv_*`/`env_*` for the **lane command** and nothing for an adapter's helper.

New optional top-level `helpers`:

```json
"helpers": {
  "description": "Every external helper an adapter actually invoked to produce a claim payload. external_tools (A-013) declares what a lane needs; this records what ran. A claim whose payload depended on a helper requires the corresponding entry, so a coverage or mutation claim is reproducible against a known tool identity.",
  "type": "array",
  "items": {
    "type": "object",
    "required": ["role", "tool", "resolved_path", "identity"],
    "properties": {
      "role": {"enum": ["statement-positions", "mutation-sites", "executable-code"]},
      "tool": {"type": "string", "minLength": 1},
      "resolved_path": {"type": "string", "minLength": 1},
      "identity": {
        "description": "What the tool reports about itself, verbatim (e.g. 'go version go1.25.12 linux/amd64'). Never inferred from a package name or a tag.",
        "type": "string", "minLength": 1
      }
    },
    "additionalProperties": false
  }
}
```

`role` is closed at exactly the three helper jobs that exist or are ruled:
`statement-positions` (A-217's Go oracle), `mutation-sites` (SQL's parser, and
P29's Go helper), `executable-code` (Go's existing `has_executable_code`).
Designing it once, now, is what the operator asked for; adding a fourth role
later is an additive enum change.

**Correspondence rule, widened (A-243).** A `helpers` entry requires *either* a
claim carrying the payload that role produces, *or* a claim whose terminal names
that helper's own failure. Only `MUTATION_DISCOVERY_FAILED` qualifies for the
second arm: `MUTATION_UNSUPPORTED` means nothing was invoked and
`MISSING_EXTERNAL_TOOL` means the helper could not have been, so both must still
refuse a `helpers` entry. The rule is cross-object and therefore lives in
`verdict.py`/`verify.py`, **not** in the schema (A-182 as narrowed above). This
reuses A-183's reasoning for keeping `judgment.r2` beside `MUTATION_UNSUPPORTED`:
the provenance is exactly what a consumer needs to interpret the refusal. Still
open for P34: a helper that ran successfully on a run that then failed for an
unrelated reason.

## V5-5a — the seam `statement-positions` is invoked through (A-239)

The `statement-positions` role above had no seam to be called through when v5
was designed (A-235). The accepted shape, which P27's re-carve starts from
rather than re-litigating:

1. **`go_cover.py` emits block extents as an explicit representation** — an
   optional `blocks` field or sibling type — instead of pre-merging them into
   line sets. P27's re-carve designs it; A-084 gives the extension to the
   package that proves the need.
2. **Statement positions arrive through a NEW protocol hook.** Never by
   overloading `statement_spans`, whose contract is "called ONLY for
   unattributed lines" (frozen by A-097/A-101) — that gates in the wrong
   direction, rescuing gaps where Go needs to *demote fabrications*, and its
   type carries no columns.
3. **The intersection is a pure, language-free core function**: given block
   extents plus statement positions, compute statement-granular line sets. Same
   division of labour P07 established for spans.

Built **Go-specific, not shared infrastructure** — there is no third consumer to
amortize against (Istanbul is already statement-precise, SQL has no coverage
tool). The rejected alternative — correcting at the adapter/evaluate boundary
with no shared-model change — is not merely messier but information-theoretically
insufficient: a statement on a shared block boundary (A-218's column-1 case) is
collapsed to "executed" *during parsing*, and the per-block columns a recovery
would need are already gone.

## What v5 deliberately does NOT do

**No SQL R3 space, by construction.** R3's `uncovered-line` canary needs R1
coverage, and A-192 already forbids that combination without R1. DDL has no
coverage tool, so SQL lanes are `R0,R2` and no truthful SQL canary mechanism is
currently known. v5 reserves no SQL-shaped R3 fields. If a mechanism is later
found, that is a v6 question and should be, rather than half-supported space
sitting in the schema inviting a plausible-looking implementation.

**No per-mutant time bound.** I checked this rather than assuming it: v4's
`mutation.budget_exceeded` is already a **per-mutant** bucket, so a mutant that
exhausts budget is already attributable to itself rather than collapsing into a
lane-level `BUDGET_EXCEEDED`. The attribution I was concerned about in the B001
assessment already exists. What is missing is only a *declared* per-mutant
ceiling, and the lane deadline (A-212) plus `max_mutants` already bound total
work. I have an argument that SQL mutants are expensive and **no measurement**,
and adding a field on an argument rather than evidence is exactly what the P27
probe taught. Deferred.

> **Named v6 trigger:** if SQL's real R2 probe shows a single mutant can exhaust
> the lane deadline — provisioning hanging, a migration chain stalling — such that
> one bad mutant destroys a whole run's evidence, `judgment.r2` needs a declared
> `per_mutant_seconds`. Measure before adding.

**No change to `mutant_outcome`'s site identity.** It already fits SQL.

**No back-compatibility shim.** v4 artifacts are not upgraded in place, per
A-138/A-170. Consumers migrate.

## Migration surface, for the carve that follows

`src/assay/schemas/verdict.schema.json`, the model (`verdict.py`), the raw
verifier (`verify.py`), the conformance vocabulary
(`tests/test_verdict_conformance.py`), and **every expected-artifact fixture and
carve asset carrying `judgment.r1` or a bare operator name** — including
`carve-assets/P25/expected/*`, `carve-assets/P26/expected/*`, and P27's
`missing-tool-v4-template.json`, which will need a v5 sibling. The three
hoisted keys and the operator renaming are mechanical; their breadth is the
package's real cost and the reason it is carved separately from the SQL adapter.
