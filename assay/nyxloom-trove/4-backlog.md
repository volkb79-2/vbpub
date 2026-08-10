---
kind: backlog
schema_version: 1
items:
  - {id: B001, title: "SQL/DDL LanguageAdapter — R2/R3 for PostgreSQL schema. Proposed 2026-08-10 from dstdns CW2a; see the rationale below.", type: feature, component: adapters, context_estimate: medium}
---

# assay — backlog

Items proposed but not carved. One line each in the frontmatter; rationale
below, so a proposal cannot be adopted without the argument that produced it.

---

## B001 — a SQL/DDL adapter, and why PostgreSQL projects are the cheapest place to prove R2/R3

**Proposed by:** dstdns, 2026-08-10, out of the CW2a corpus-schema wave.
**Status:** proposal only. Nothing here has been discussed with assay's owner,
and it is deliberately filed rather than built — assay is mid-wave.

### The claim

A `LanguageAdapter` for SQL DDL would give assay a consumer class where **the
correctness surface is enumerable from the artifact itself**, with no parser and
no instrumentation. That inverts the usual economics: R2 (mutation) is normally
the expensive tier, and for schema it is the *cheap* one.

### Why schema is different from code

For code, finding mutation sites needs a parser and knowing what ran needs a
coverage tool. A database answers both by introspection:

- every mutation target is a row in `pg_constraint` / `pg_trigger` / `pg_index`;
- every kill signal is a `constraint_name` PostgreSQL hands back on violation.

And the operator set is **finite and closed**, which matters directly for §0's
determinism invariant — the mutation set can be *complete* rather than sampled:

| operator | what a surviving mutant proves |
|---|---|
| drop a `CHECK` | no test asserts the value domain |
| drop a `UNIQUE` / unique index | no test asserts the identity rule |
| drop `NOT NULL` | no test asserts required-ness |
| drop a `FOREIGN KEY` | no test asserts referential lineage |
| `ON DELETE RESTRICT` → `CASCADE` / `NO ACTION` | no test asserts the deletion policy |
| drop a trigger | no test asserts the enforced invariant |
| widen a `CHECK ... IN (...)` by one value | the vocabulary is asserted by shape, not by content |

The adapter surface in DESIGN-GUIDE §11 fits almost unchanged:
`generate_mutation_sites(text, lines, operators, limit)` over DDL text,
`inject_import_break` → make the script fail to apply, `inject_uncovered_line` →
drop one constraint. `statement_spans` maps to statement boundaries in a `.sql`
file.

### Our three use cases — all real, all from one week

Each of these is a defect that shipped into a *reviewed, frozen* contract and
was caught late or by luck. Each is a one-line mutation an adapter would have
caught mechanically.

**1. `ON DELETE RESTRICT` → the deletion-policy operator.**
A `BEFORE DELETE` trigger added to make a complete corpus version undeletable
also fires on FK cascade, so any corpus that ever had a complete version became
permanently undeletable — escapable only by `DISABLE TRIGGER` — and it silently
turned the table's own `ON DELETE CASCADE` into dead DDL. Two document reviews
and three hand-probes missed it; an independent auditor found it only by
*running a delete*. A `RESTRICT→CASCADE`-class mutation over the declared FKs
would have surfaced it without anyone thinking to look.

**2. The false-PASS an exception type cannot catch.**
A test asserting `pytest.raises(RestrictViolationError)` on a pinned-version
delete **passed against a schema with `ON DELETE RESTRICT` removed entirely**,
because a trigger on the same table raises the same SQLSTATE. "It raised" is not
evidence of *which* mechanism refused. The fix was asserting
`exc.constraint_name`, and the general form is exactly a surviving mutant: drop
the FK, suite stays green. This one is notable because it was written *after*
reading a review about false-PASS oracles, by someone looking for them.

**3. Ordering and idempotency as artifact conformance.**
`run-schema.sh` applies numbered scripts with a bare glob and pinned no
collation, so under glibc `03a-create-job-history.sql` sorts before
`03-create-tables-core.sql` and fresh init dies on `relation "jobs" does not
exist`. Production survived only because the db-init base image is Alpine, whose
BusyBox shell sorts bytewise — an ambient property nothing stated or tested. A
sibling defect: two bare `ALTER TABLE ... ADD CONSTRAINT` broke the directory's
documented idempotence promise.

Neither is a mutation finding; both are **distribution-shaped conformance**
properties of an ordered artifact set — "applies from scratch, in a
deterministic order, twice" — closer to `gate/distribution`'s wheel contract
than to R2. Worth noting because a DDL adapter's consumers will want both, and
they are different mechanisms.

### What must stay out, per §7

**Provisioning.** Getting a real PostgreSQL with the project's init scripts
applied is *"no container, network, image, instance or provisioning knowledge,
permanently"*. dstdns solved it with `scripts/schema-gate.sh` (~120 lines:
throwaway container on the app network, image DERIVED from the running stack so
the lane runs on the engine the app runs on, grants applied by the real grant
script, disposed via `trap` on every exit path). That belongs to the project or
to ciu, and the lane file's `[…where]` stays data assay parses and never
interprets. An adapter would receive a DSN, never make one.

### Why "SQL coverage" is the wrong frame — recorded so it is not re-proposed

There is no line-coverage analogue for DDL, and §7 already excludes assay
*computing* coverage. The nearest honest measurement is **declared constraints
versus constraints ever exercised**, as a set difference over `constraint_name`s
observed during a run — deterministic, needs no instrumentation. But it is
strictly weaker than mutation, because a never-fired constraint may be untested
*or* unreachable-by-design, and that ambiguity is the kind §0 rejects. Mutation
resolves it without ambiguity: if dropping it does not turn the suite red, it is
untested. **Mutation subsumes the coverage idea here; only R2/R3 should be
proposed.** Forcing R1 to fit would be the same error as carrying a spec section
into a context whose preconditions changed.

### The synergy argument, stated plainly

Every project in this estate that owns a PostgreSQL schema — dstdns today,
plausibly others — currently has exactly two lanes available for schema
behaviour, and neither works: a mock lane where a constraint claim can only be
asserted by grepping `.sql` text (dstdns ships that shape at
`libs/common/tests/test_backend_foundations.py:117-129`, where a syntax error on
line 2 would pass), and a live lane that is declared but not run. **A schema
change is therefore the least-verified change a project makes, while being among
the most irreversible.**

A DDL adapter would not just serve those projects; it would be assay's cheapest
possible proof that the R2/R3 machinery is sound, because the mutation set is
complete rather than sampled and every kill is a named constraint rather than an
inferred line. That is an argument for it being an *early* consumer, not an
exotic one.

**Evidence available on request:** dstdns `main` — `scripts/schema-gate.sh`,
`tests/schema/`, `docs/proposals/cw2-p85-wave/REVIEW-CW2A.md` (findings C2, C3,
C14).
