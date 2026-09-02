---
kind: backlog
schema_version: 1
items:
  - {id: B001, title: "SQL/DDL source-mutation adapter. IMPLEMENTED and RELEASED (wave 3, assay-v2.1.0): judge.language = \"sql\" at R2 only, seven sql:* operators on a stdlib-only two-level DDL lexer, equivalence_artifact REQUIRED, qualified against real PostgreSQL 18.4 at a pinned dstdns revision. No verdict-schema change.", type: feature, component: adapters, context_estimate: medium, folds_into: F013}
  - {id: B002, title: "Adopt cmru for assay's release process. COMPLETE: implemented 2026-08-11 (A-249/A-250), and the last open step -- the first real release -- is discharged by two cmru-cut releases, assay-v2.0.0 and assay-v2.1.0. cmru now owns snapshot/gate/tag/build/publish and generates the dated CHANGES.md entry. Five findings from the 2.1.0 run are filed as cmru KI-12..KI-16.", type: feature, component: distribution, context_estimate: medium, folds_into: F014}
  - {id: B003, title: "Ship a zipapp (.pyz) beside the wheel as a second release artifact. COMPLETE: publication waited on B002's release step, which landed; both assay-v2.0.0 and assay-v2.1.0 publish assay-<version>.pyz with a .sha256 sidecar, and dstdns consumes the zipapp. Measured bonus: the .pyz is byte-reproducible across independent builds at different commits, while the wheel is not.", type: feature, component: distribution, context_estimate: small, folds_into: F014}
  - {id: B004, title: "Provenance as VERIFIED evidence, not merely recorded: ciu provenance --json as assay's first Tier-2 adjudicated integration. CARVED, REVIEWED and DEFERRED (wave 2, A-275/A-276). CIU-20 has SHIPPED and is no longer the blocker; the blockers are now (1) one new ReasonCode, PROVENANCE_UNVERIFIED, reserved by name and awaiting a schema bump another item pays for, and (2) ciu CIU-39 (was CIU-28, renumbered 2026-08-19) -- provenance compares vendor images ciu never built, so verified-match is unreachable on any live host. The recorded half already ships via A-254.", type: feature, component: evidence, context_estimate: medium}
  - {id: B005, title: "A whole-module / per-callable coverage judge — an R1 mode that asserts a coverage FLOOR over a declared owned module (or callable span) independent of the base..HEAD diff. Consumers running method-reconciliation programs need whole-method rigor the changed-line judge cannot express; today they bolt it on with --cov-fail-under in the argv, invisible to the verdict. IMPLEMENTED (wave 1, judge.mode = \"whole_target\"): shipped, gated, documented, and proven end to end through the real CLI — a target absent from the artifact refuses NO_MEASUREMENT/TARGET_NOT_MEASURED rather than reporting 100% of zero.", type: feature, component: evaluate, context_estimate: medium}
  - {id: B006, title: "B006(a): explicit, commit-validated omission of unsafe symlink leaves for monorepo R1/R2/R3 lanes — never an unsafe-symlink ignore, and NOT the withdrawn project-boundary design A-269 replaces; B006(b): assay-owned artifact parents created inside the private snapshot. IMPLEMENTED (wave 1): both shipped, gated, documented, and qualified end to end — CMRU makes genuine R0/R1/R2/R3 claims while Topos's tracked /etc/passwd fixtures stay in place.", type: bug, component: isolation, context_estimate: large}
  - {id: B007, title: "Ordered, bounded, explicitly declared multi-target R3 canary — try several declared source files so a gate is not cleared merely because one arbitrarily chosen module is never imported. Proposed by nyxloom 2026-08-17 while adopting assay. ASSESSED AND DEFERRED out of wave 1: the first post-v6 schema item (v7), with five design findings recorded for its carver. No automatic discovery or ranking.", type: feature, component: canary, context_estimate: large}
  - {id: B010, title: "assay run executes the lane argv in the invoking environment with no way to declare WHERE the lane is valid -- in the dstdns devcontainer cockpit `assay run auth` cannot execute at all (the suite imports fastapi.routing.iter_route_contexts, absent from the cockpit's FastAPI 0.135.1 and present only in the app image's pin), so the lane had to be evidenced by re-running its argv manually in the gate container plus a hand-check of the judge criteria against the artifact. Ask: either document the doctrinal answer (assay runs only in the gate environment; run-gate.py/B009 owns getting it there) or add a lane-level environment preflight that refuses with a clear message instead of surfacing the suite's raw ImportError.", type: feature, component: execution, context_estimate: small}
  - {id: B015, title: "UUID/equality/enum-aware Python mutation operators. NOT in assay-v2.2.0; the shipped Python catalogue remains compare-swap, boolop-swap, bool-const-flip and falsy-swap, so P126's deferred R2 debt remains deferred.", type: feature, component: adapters, context_estimate: medium}
---

# assay — backlog

Items proposed but not carved. One line each in the frontmatter; rationale
below, so a proposal cannot be adopted without the argument that produced it.

---

## B001 — a SQL/DDL adapter, and why PostgreSQL projects are the cheapest place to prove R2/R3

**Proposed by:** dstdns, 2026-08-10, out of the CW2a corpus-schema wave.
**Status:** **CARVED and REVIEWED, wave 3, 2026-08-18 — implementation in
progress.** Superseded the earlier "accepted for a Sol design/probe checkpoint
after P28 and before P29; not accepted for implementation yet". A-215 still
records the source-oriented disposition, which the carve confirms.

> **The carve is `W3-CARVE-P34-sql-adapter.md`; the adversarial review is
> `reports/assay-P34-carve-review-fable.md` (READY WITH CORRECTIONS, five
> blocking findings, none refuted). Read BOTH plus A-279…A-283 before touching
> anything here — five of this section's premises are corrected there.**
>
> Route chosen: a **stdlib-only two-level bounded DDL lexer inside assay**, not
> an external helper. No SQL parser exists on this host *or inside
> `tester-unified:local`*, the image assay's own gate runs in, so the helper
> route begins by re-risking four other products' gates (A-O02); and the span
> problem is a *lexing* problem, not a grammar one — a bare keyword regex over
> real dstdns DDL produces 68 phantom matches out of 512, including **all three**
> of its `ON DELETE RESTRICT` matches.
>
> **Verdict-schema surface needed: NO — and unlike B004, this was verified by
> construction rather than asserted.** The most demanding artifact P34 emits is
> model-valid, schema-valid and raw-verifier-clean at v6 unchanged, confirmed
> twice from independent starting points. `PROVENANCE_UNVERIFIED` (A-276) stays
> reserved; P34 pays for no bump; lane schema v2 is unchanged.
>
> **The correction that matters most (A-279):** this section's own framing and
> the carve both had the consumer command as `apply && test && dump`. That makes
> `killed` **unreachable** — a kill *is* the test failing, so `&&` short-circuits
> and the dump the classification depends on never gets written. It is
> `apply && dump && test`. The feature's headline outcome could not have been
> produced under the documented shape, and no acceptance oracle would have
> noticed, because none of them ever produced a kill.

### Assay-owner disposition — authoritative over the original proposal below

The preferred hypothesis is a **source-oriented SQL `LanguageAdapter`**, not a
database-connected adapter:

```text
tracked DDL + changed lines
  -> SQL parser/helper -> bounded MutationSite byte spans
  -> Assay's immutable replacement snapshots
  -> the unchanged project-declared argv
  -> project-owned fresh test-database provisioning and schema tests
```

This fits Assay's existing Python/Go direction: the adapter learns syntax and
constructs mutations; the language-free core owns bounds, isolation, execution,
and verdicts. The project test command may already provision PostgreSQL, but
Assay and its adapter receive no DSN, open no database connection, and mutate
no shared database. Each mutant must be judged through a fresh or otherwise
provably isolated project-owned database state.

The original proposal's catalog-introspection argument is evidence for the
value and possible operator catalogue, not yet the Assay interface. A live
`pg_constraint`/`pg_trigger` row does not by itself identify a byte span in
tracked changed DDL. Likewise, “complete” can initially mean complete for the
selected operators over the changed tracked DDL Assay was asked to judge; it
must not silently become a whole-schema audit.

The post-P28 probe must settle before P29 freezes the second-language mutation
contract:

1. the SQL parser/helper and exact source-to-schema identity;
2. language-qualified operator vocabulary versus today's global four-value v4
   enum;
3. whether the flat `LanguageAdapter` remains honest for an R2-without-R1
   language or needs capability-specific protocol factoring;
4. fresh-database isolation, cleanup, baseline, and false-kill defenses owned
   by the project command;
5. the truthful SQL R3 mechanism, if any—R3 is not accepted merely because R2
   is promising; and
6. exact dstdns source/gate evidence at a pinned revision.

If source mutation cannot truthfully represent the deployed schema, the
fallback is a reusable external PostgreSQL mutation auditor whose structured
result Assay consumes as Tier-2 adjudicated evidence. That means more logic in
a separate tool and less in Assay; it is a fallback, not the preferred first
route. Ordering/idempotency (“apply from scratch twice in deterministic order”)
remains project-owned R0 artifact conformance in either design.

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
interprets. Under the preferred source-mutation design the project command,
not the adapter, owns any DSN and database lifecycle.

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

---

## B002 — adopt cmru for assay's release process (design checkpoint, NOT landed)

**Proposed by:** the operator, standing intent, scoped here 2026-08-11 by C-sol-1.
**Status:** **COMPLETE.** Implemented 2026-08-11 (A-249/A-250); the one open
step — the first real release — is discharged, twice: `assay-v2.0.0`
(2026-08-17) and `assay-v2.1.0` (2026-08-18) were both cut by
`./cmru.release.sh --project assay`, which owns snapshot → gate → tag → build →
publish and generates the dated `CHANGES.md` entry. A-247 recorded the original
stop-short and A-248 lifted it. The two blockers below became rulings: adopt
cmru's orchestration, decline its build; the release manifest is authoritative
over a `.sha256` sidecar.

> **Reconciled 2026-08-18.** The frontmatter row still read "design checkpoint,
> STOPPED SHORT of landing" two releases after cmru had cut them both — the same
> stale-row shape found in B001, B004, and `CHANGES.md` itself this wave. What
> the adoption is still owed is *upstream*, not here: the 2.1.0 run produced five
> findings, filed as **cmru KI-12…KI-16**. KI-12 is the one with teeth — cmru
> computes its release plan from `git tag --list`, which includes unpushed local
> refs, so a hand-made local tag silently decided a release; and a tag pointing
> at the snapshot commit makes a project permanently unreleasable while looking
> exactly like "unchanged". The operational rule that falls out of it, and that
> binds anyone releasing assay: **never hand-tag a cmru-managed project** — cmru
> owns tag creation, so a manual tag is indistinguishable from a completed
> release. **The plan
below is kept as written, because what it got wrong is worth more than a tidy
record: it treated the mtime normalisation as the whole reproducibility story,
and a real build showed the wheel itself was non-deterministic until
`SOURCE_DATE_EPOCH` was set.**

### What is true today, verified

`assay` **is not in `cmru.toml`** — `grep assay cmru.toml` returns nothing.
Every other Python product in the monorepo is: `ciu`, `cmru`, `nyxloom`,
`topos` all declare `artifacts = ["wheel"]`. So adoption is a genuine addition,
not a migration of an existing entry.

assay owns its own release machinery, and it is the most rigorous in the estate:

* **A-198** — five hash-bound build wheels (`setuptools==84.0.0`,
  `wheel==0.47.0`, `setuptools-scm==10.0.5`, `packaging==26.3`,
  `vcs-versioning==2.2.4`) committed under
  `gate/distribution/build-wheelhouse/`, installed with pip
  `--require-hashes` before `--no-build-isolation`.
* **A-199** — the gate builds from a private no-local sparse clone at the exact
  reviewed OID, because the first real probe committed ignored `__pycache__`
  and egg-info residue and produced a wheel over twice the correct size,
  *reproducibly*.
* **A-200** — a consumable release is bound by the closed manifest
  `{schema_version, filename, version, sha256}` and the standalone stdlib
  helper `gate/distribution/release_wheel.py`, whose successful output is one
  PEP 508 line for pip `--require-hashes` — so pip rechecks the bytes it
  actually opens, closing the check/use race a separate hash check leaves open.

### Blocker 1 — cmru's built-in wheel build is weaker than A-198/A-199

`cmru/src/cmru/handlers.py::cmd_wheel_build` runs
`python -m build --wheel --outdir dist` (line 222), or the same command inside
`wheel-builder:local` when `CMRU_WHEEL_BUILDER_IMAGE` is set — which
`cmru.toml`'s `[env]` sets globally. There is **no** `--require-hashes`, no
`--no-build-isolation`, no committed wheelhouse and no private clone anywhere in
that path. Declaring `artifacts = ["wheel"]` and taking the batteries-included
handler would therefore *silently replace* assay's proven closure with an
unpinned build — a regression in exactly the property P24 exists to establish.

The fix is an explicit `[project.assay.steps.build]` override (cmru's own
documented escape hatch, and what `nyxloom` already does). That is a small edit
but a real decision: it means assay adopts cmru's **orchestration** (tag,
Release, `latest.json`, per-product prefix, isolated release worktree) and
declines its **build**. Worth stating in writing before it is done by habit.

### Blocker 2 — two manifest grammars for one wheel

cmru **S1.3** requires every GitHub-Release profile to upload the artifact plus
a `.sha256` sidecar in `sha256sum -c` format. assay's A-200 manifest is a
different, closed document with a different purpose. They do not conflict
mechanically — both can be assets on one Release — but a consumer needs to be
told which is authoritative, and A-200's answer is unambiguous: the manifest,
because the sidecar cannot feed pip's hash mode. This needs one sentence of
ruling and a line in whatever install instructions the Release notes carry.

### Blast radius — why this stops here

Adoption is not confined to a new `[project.assay]` block. `cmru.toml`'s
`[orchestration]` carries `project_order`, `default_projects`, and a
`[orchestration.step_project_order]` table with four per-step lists. Adding
assay to those lists changes what a bare `cmru release` does for **seven other
products** that share the file. `cmru release` also fetches `origin/main`,
creates a `cmru/release/<id>` worktree, runs each changed project's `run-tests`
gate there, and fast-forwards `origin/main` from the validated branch — so a
misdeclared assay gate step can fail a release that has nothing to do with
assay.

That is shared release/CI machinery with real blast radius, which is precisely
the case the carve rules say to hand back rather than land.

### The concrete plan, ready to carve

1. Rule blockers 1 and 2 (two decisions, no code).
2. Add `[project.assay]` with `prefix = "assay-v"`, `artifacts = ["wheel"]`,
   `cwd = "assay"`, `scm_dist = "assay"`, `version.strategy = "scm"`,
   `bump = "conventional"` — copying `topos`'s shape, which is the closest fit.
3. Add an explicit `[project.assay.steps.build]` invoking assay's own
   hash-bound build, and a `[project.assay.steps.push]` that uploads the wheel,
   its `.sha256` (S1.3) **and** `release-manifest.json` (A-200).
4. Add `[project.assay.steps.run-tests]` pointing at the registered gate lane,
   not at a bare `pytest` — assay's own `assay.toml` lane is the gate.
5. Only then add `"assay"` to `project_order` / `default_projects` / the four
   `step_project_order` lists. Steps 1-4 are inert until this step.
6. First release with `--project assay --dry-run`, then `--project assay`.

---

## B003 — a zipapp (.pyz) beside the wheel, as a second release artifact

**Proposed by:** Fable, round-3 review, 2026-08-11, as the near-zero-cost
answer for consumers with no installed Python package manager (A-O04's srdm
blocker). Scoped and measured the same day.
**Status:** **COMPLETE.** Implemented 2026-08-11 (A-249) as part of
`gate/distribution/build_release.py`; the publication step it waited on landed
with B002, and `assay-<version>.pyz` plus its `.sha256` sidecar are published
assets of both `assay-v2.0.0` and `assay-v2.1.0`. dstdns consumes the zipapp,
not the wheel, so this is the artifact its notify carries a hash for. Two
things this scoping did not find, both caught by building for real: the WHEEL
is also non-reproducible without `SOURCE_DATE_EPOCH`, and `zipapp -m` would
have discarded every non-zero exit code.

> **Reconciled 2026-08-18, with one measurement worth keeping.** The 2.1.0
> zipapp was built twice by independent paths — once locally at `52534ef7`, once
> by cmru at its own release commit `a3ae580d` — and both produced
> `sha256 f2f13021…`, byte-identical. The wheel did **not**: `aff153b7…` locally
> versus `5e883444…` published. So the zipapp is reproducible across commits
> while the wheel is only reproducible within one. That asymmetry favours the
> artifact consumers actually pin, and it is worth preserving deliberately
> rather than by luck — if a future change makes the `.pyz` embed build-varying
> state, a consumer verifying a re-vendored artifact against a recorded hash
> starts failing for no real reason.

### Measured, not assumed — a zipapp really works

Built from the current source tree and driven:

```text
$ python3 -m zipapp /tmp/zapp -o /tmp/assay.pyz -p "/usr/bin/env python3"
$ python3 /tmp/assay.pyz verify tests/fixtures/verdicts/r2_pass_with_judgment.json
exit=0
$ python3 /tmp/assay.pyz verify /tmp/forged.json
assay verify: R2 claim status (ERROR, MUTATION_DISCOVERY_FAILED) names a refusal
reachable only after a PASSING baseline, ...
exit=1
```

`assay run` works too: a real R0 lane in a throwaway git repo produced
`package: PASS (exit 0)` and a verdict its own `verify` accepted.

**Zip-safe by construction:** `grep -rn __file__ src/assay/` finds exactly one
hit, and it is a docstring explaining why paths are NOT derived from it.
`verdict.schema_text()` goes through `importlib.resources.files()`, which
resolves inside the zip — proven by reading it from within the archive:

```text
assay.__version__      = 1.2.5
VERDICT_SCHEMA_VERSION = 4
schema $id             = urn:assay:schema:verdict:4
schema bytes read      = 27949
assay module origin    = /tmp/assay2.pyz/assay/__init__.py
```

### Finding 1 — build it from the WHEEL, never from `src/`

A zipapp built straight from `src/assay` reports `assay 0+unknown`, because
there is no dist-info for `importlib.metadata` to find, so `__init__.py`'s
`PackageNotFoundError` fallback fires. **Every verdict such a zipapp emits
records `"assay_version": "0+unknown"`, and its own `verify` accepts it** — an
artifact that cannot be attributed to a release, accepted silently. A-200
already refuses `0+unknown` as a release identity; this is the same hole one
layer down.

Building from the released wheel fixes it completely:

```text
$ pip install --no-index --no-deps --target /tmp/zapp2 assay-1.2.5-py3-none-any.whl
$ python3 -m zipapp /tmp/zapp2 -o /tmp/assay2.pyz -p "/usr/bin/env python3"
$ python3 /tmp/assay2.pyz --version
assay 1.2.5
```

The dist-info travels inside the archive and `importlib.metadata` reads it. It
also makes the zipapp a strict *derivative* of the audited wheel rather than a
second, independently-assembled artifact — one build closure, two shapes.

Round-tripped against real artifacts, and the version boundary holds:

```text
combined-pass-v4       OK (exit 0)
r1-unavailable-v4      OK (exit 0)
r2-limit-v4            OK (exit 0)
(a v5 artifact)        schema_version 5 is not this verifier's version 4 -> exit 1
```

### Finding 2 — not reproducible until mtimes are normalised

Two identical builds, one second apart:

```text
2a0feb3fa81f045e2f423d727a53f32b7b11c7f3c3bb6a5d5e620a5f3d63504a  ad1.pyz
6ab6ee4778e815d5c6fee6d2bc5cf8297b84a55e84cfa944c100357fb084b907  ad2.pyz
```

`python -m zipapp` takes each entry's timestamp from the filesystem. One
`find <dir> -exec touch -t 198001010000 {} +` before zipping makes it exact:

```text
fdfbe9ef058fe06d9daaf496c4eea8f9dbaf0a07baa2fffb48197dee37a797a8  ad3.pyz
fdfbe9ef058fe06d9daaf496c4eea8f9dbaf0a07baa2fffb48197dee37a797a8  ad4.pyz
```

...and the normalised build still reports `assay 1.2.5` and verifies exit 0. So
cmru **S9.4**'s byte-identical contract is reachable with one line, not a
redesign — but it is not free, and shipping the naive build would put a
non-reproducible asset on a Release page whose whole premise is reproducibility.

### Finding 3 — cmru has no `.pyz` artifact type, and does not need one

cmru's four profiles are `wheel`, `oci`, `tarball`, `bundle` (SPEC S1.3/S1.6).
`bundle` is a **triple** — a deterministic `.tar.xz`, a canonical
`manifest.json`, and a detached Ed25519 `manifest.json.minisig` — so routing a
bare `.pyz` through it means inventing a tar wrapper and provisioning a
minisign key for an artifact that is already a single self-contained file.
**No new artifact-type slot is needed either:** cmru's own README names extra
assets as exactly what an explicit `[project.X.steps.push]` override is for, and
S1.3's `.sha256` sidecar convention applies unchanged. So the zipapp ships as
two additional assets (`assay-<version>.pyz` and its `.sha256`) on the wheel
profile's existing Release.

A first-class `zipapp` profile in cmru would be cleaner for the estate, but that
is cmru's work and should not gate assay.

### The concrete plan, ready to carve

1. A stdlib-only `gate/distribution/build_zipapp.py`, in the same standalone
   register as `release_wheel.py`: takes the verified release wheel plus the
   manifest, installs to a temp dir with `--no-index --no-deps --target`,
   normalises mtimes, writes `assay-<version>.pyz`, and re-emits a `.sha256`.
2. Its oracles, all differential and all already proven reachable above: the
   version is the wheel's version and never `0+unknown`; two builds are
   byte-identical; the packaged schema is read from inside the archive; a real
   artifact verifies exit 0 and one from another schema version exits 1.
3. Wire it into B002 step 3's push step. Not before — the assets need a Release
   to attach to.

---

## B004 — provenance as VERIFIED evidence, not merely recorded

**Proposed by:** dstdns's reconciliation program, 2026-08-11, out of its
real-lane isolation work on ciu's S16 worktree verb ("embed provenance in the
verdict so invariant #18 holds by construction").
**Status:** the RECORDED half shipped the same day (A-254/A-255). The VERIFIED
half was **CARVED, REVIEWED AND DEFERRED in wave 2 (2026-08-17, A-275/A-276)**.
CIU-20 has since shipped — `ciu provenance --json` exists at CIU 6.0.3 — but the
carve found **two new blockers in its place**, and A-256 still rules the adjacent
same-instance question as discipline, not schema.

> **CORRECTION, ruled in A-275: this section's claim below that "no
> verdict-schema change was needed and none proposed" is FALSE of the VERIFIED
> half.** It is true of the RECORDED half (A-255's actual finding) and was
> carried across to the whole item by conflating *no new field* with *no new enum
> value*. The verified step needs exactly one new `ReasonCode`, because
> `_check_reason_code` demands a code for every non-`PASS` outcome, `adjudicated`
> evidence has no payload slot to carry ciu's status string, and none of the 30
> shipped codes truthfully names "the provenance tool returned a non-green
> verdict". This section's own `mismatch → FAIL` sketch **is** the closed-enum
> widening the table below lists as "not proposed".
>
> **The second blocker is external and newly measured:** ciu compares *every*
> running container's OCI revision label against its own repository's short hash,
> including vendor images that stamp their own upstream revision, so `overall` is
> pinned at `"mismatch"` on a correctly built host and `verified-match` is
> unreachable. Filed as a CIU-20 follow-on.
>
> `PROVENANCE_UNVERIFIED` is **reserved by name** (A-276) to ride whichever bump
> B001/P34 or B007 already pays for. B004 unblocks when that code has shipped
> **and** ciu can emit `verified-match`. Design detail:
> `W2-CARVE-B004-provenance-verified.md`; review:
> `reports/assay-B004-carve-review-fable.md`.

### What already ships — do not re-carve this part

A lane declaring `env_required` for a provenance variable puts it in the
artifact's `env_effective` verbatim, on every outcome including refusals, and
the artifact verifies clean. Measured, not designed: a real `S3` lane produced
`env_effective` carrying `CIU_IMAGE_REVISION=1b369e23` and
`CIU_INSTANCE_ID=dstdns-pkgP96`. **No verdict-schema change was needed and none
should be added** (A-255) — true of THIS half only; see the correction above.

### What is blocked, and on exactly what

| step | needs | status |
|---|---|---|
| Recorded, caller-asserted | `env_required` (A-254) | **SHIPPED** |
| Recorded, ciu-**attested** | **ciu CIU-21** — inject the image's own baked `org.opencontainers.image.revision` as an env var | blocked; zero assay work when it lands |
| **Verified** (adjudicated Tier-2 evidence) | ~~**ciu CIU-20**~~ **SHIPPED** at CIU 6.0.3. Now needs (i) `PROVENANCE_UNVERIFIED`, reserved by A-276, and (ii) a **CIU-20 follow-on** scoping the comparison to ciu-built images | blocked on both; carved and deferred, A-275 |
| **Enforced** (refuse on mismatch) | a new `ReasonCode` → closed-enum widening → v6-class | not proposed — **and A-275 rules that the VERIFIED row above needs one too**, so this row was never the only one that did |

The distinction the middle two rows turn on is not cosmetic. Until CIU-21, the
recorded value is **whatever the caller put in the environment** — an assertion,
not an attestation. It is still worth recording (it is the truth about what ran)
but a consumer must not read it as verified, and assay's artifact deliberately
does not claim it is.

### Why the verified step cannot be short-circuited

A-030 is the binding constraint: assay never shells out to docker. At S3/S4 it
runs *inside* the container, and an OCI label is readable only from the daemon
side. So assay structurally cannot compute a provenance verdict, and the only
honest carriers are the evidence arrays. `adjudicated` is the right one — "a
tool's verdict assay records but does not verify" — and it has had zero
integrations since A-034/A-078 reserved it.

### The shape, when CIU-20 lands

* A lane declares `(adjudicated, "image-provenance")` in `declared_evidence`.
* A `--provenance-json <path>` style input, read through the existing bounded
  `safeio` seam, never parsed from prose (A-204: byte-copy, never interpret).
* A closed status mapping: `verified-match` → PASS, `mismatch` → FAIL,
  `not-verified-*`/`refused-*` → NO_MEASUREMENT-class. An unrecognised member is
  **refused**, not guessed, per this project's closed-vocabulary discipline.
* It creates the adjudicator registry A-078 deferred, which is why this is a
  package rather than a patch.

### What it resolves

**A-O12** (provenance verification for S3/S4 lanes) — fully, and A-O12's own
text needs correcting first: its claim that "assay records `declared_unverified`"
is false, verified — that string appears nowhere in `src/`, `docs/` or the
schema, only in A-O12's own row. **A-O10** (which Tier-2 integration is built
first) — provenance is the leading candidate precisely because it has *no
threshold policy question*, which is the thing A-O10 is actually blocked on.

### Sequencing

**Not urgent, because its blocker is external.** It cannot start before CIU-20
exists, and nothing assay controls changes that. When it does become available it
competes with A-O06 (A-244's accepted next capability) and should **not** displace
it — but it is cheap and in-estate, so it is the obvious item after.

---

## B005 — a whole-module / per-callable coverage judge (R1 without a diff) — IMPLEMENTED

**Proposed by:** dstdns's DESIGN-AUTHORITY reconciliation program, 2026-08-16, on
the first package that adopted an R1 coverage lane (`redirect_chain`, a
docstring+dead-code reconcile of `libs/common/src/common/redirect_chain.py`).
**Status:** **IMPLEMENTED, wave 1 (2026-08-17), as `judge.mode = "whole_target"`,
`W1-CARVE-branch-coverage-and-whole-target.md` §5 (A-260).** The whole-module
case shipped; the per-callable-span case named in the original proposal did
not (a `target` names a regular file, never a callable span — see
`docs/DESIGN-GUIDE.md`'s "A whole-target `target` names a regular file, never
a directory" for why the file-level guard is the shipped shape and what a
callable-span capability would need on top of it). **Not yet DONE end to end:**
this is shipped and documented on `assay-B005-B006-coverage-v6`, gated on this
branch, but not yet merged, released, or adopted by a real consumer lane
(dstdns's `redirect_chain` still runs the R0 stopgap described below until it
repins a v2-capable release per `docs/CONSUMERS.md`'s "Adopting a v2-capable
release" and migrates its own lane).

### The claim

assay's R1 judge is **changed-line coverage relative to a base** — by design and
stated plainly in the source: `assay/evaluate.py:42` ("changed-line coverage, not
whole-file coverage"), and `assay/measurability.py`'s module docstring calls
itself "the two changed-line measurability guards" (`DIRTY_TREE`, `BASE_IS_HEAD`).
There is no mode that asserts a coverage floor over a **whole declared module** (or
a **per-callable span**) independent of a diff. A consumer that needs whole-method
rigor — "every branch of every method I own is exercised", not "the lines I touched
this commit are exercised" — cannot express it as an assay judge.

### Why the changed-line judge is the wrong tool for a reconciliation program

dstdns is running a program whose unit of work is *reconciling a method against a
derived intent doc*. The failure mode it must gate against is exactly the one
changed-line coverage permits: a method "reconciled" by editing only its docstring
changes ~zero executable lines, so a `base..HEAD` judge demands nothing of the
method body — the intent doc is asserted by prose and verified by nothing. That is
the precise gap the program's coverage floor (its "D-044") exists to close, and the
changed-line judge structurally cannot close it.

Two concrete blockers we hit, both from `assay run redirect_chain` at a pinned
dstdns revision:

1. **`base=main` + run-from-`main` = `BASE_IS_HEAD`.** A reconcile's whole point is
   to be re-gated *from `main`* after merge (our merge discipline re-runs the gate
   on `main`). An R1 lane with `base=main` refuses there — base resolves to HEAD,
   no diff, `NO_MEASUREMENT/BASE_IS_HEAD`. So the changed-line judge can only run on
   a branch pre-merge, never as the post-merge floor.
2. **A whole-module floor has no home in the judge config.** `source_roots` +
   `fail_under` looks like it should express "this module at 100%", but it is
   filtered through the `base..HEAD` diff, so it only ever judges the *changed*
   lines under those roots. Setting `fail_under=100` over a docstring-only diff
   passes vacuously.

### The stopgap we shipped, and why it wants to be native

We enforce the whole-module floor today with coverage.py's own gate baked into the
lane argv, under assay **R0**:

```toml
argv = ["python","-m","pytest","tests/unit/test_redirect_logic.py","-q",
        "--cov=common.redirect_chain","--cov-branch","--cov-fail-under=100"]
rigor = ["R0"]
```

`--cov-branch` folds branch partials into the percentage; `--cov-fail-under=100`
makes pytest itself exit nonzero below 100% line+branch of the scoped module; assay
R0 (argv-exits-0) is the gate. It runs from `main`, needs no base, and validated
green (`redirect_chain` PASS; 43 stmts/0 miss, 24 branch/0 partial).

It works, but it pushes the rigor decision *out of the verdict*: assay records only
"the command exited 0". The verdict carries no per-file/per-callable coverage
attribution, no measured line/branch counts, and none of assay's own guards apply
to it — no `EMPTY_COVERAGE` detection (a `--cov` that silently measured nothing
would report 100% of zero and pass), no artifact integrity, no `fail_under` in the
recorded claim. The rigor is real but unattested. That inversion — the thing being
judged is invisible to the judge — is exactly what a first-class mode fixes.

### The shape, as a proposal (assay owns the real design)

A judge mode — call it a baseless / whole-target coverage judgment — that:

- takes a **declared target** (a module path, or when the per-callable span feature
  lands, a callable), not a `base..HEAD` diff, as the set of lines it requires;
- asserts `fail_under` over that target's line+branch coverage from the same
  coverage artifact R1 already parses (`coverage-py-json` etc.), reusing the
  existing `EMPTY_COVERAGE` / `UNREADABLE_ARTIFACT` / bounded-read machinery;
- runs with **no base** and therefore from any commit including `main`
  post-merge, sidestepping `BASE_IS_HEAD` entirely (it is not a measurability
  concern for a target that is not a diff);
- records the measured counts and the target in the verdict, so the floor is
  attested evidence, not a hidden argv side effect.

This is close to the "per-callable span" future feature already named in dstdns's
D-044 and in assay's own roadmap chatter; the whole-**module** case is the cheaper
first step and already has a live consumer waiting.

### Evidence available on request

dstdns `main`: `assay.toml` `[lanes.redirect_chain]` (the stopgap form, with a
comment block explaining this exact limitation), `nyxloom-trove/decisions.md` D-044
+ its 2026-08-16 amendment, and the `redirect_chain` PASS verdict at the recorded
commit.

---

## B006 — unsafe-symlink-omission snapshots and in-snapshot artifact parents for monorepo R1/R2/R3 lanes — IMPLEMENTED

**Proposed by:** dstdns's reconciliation program, 2026-08-16; expanded by CMRU's
first R1/R2/R3 consumer qualification, 2026-08-17. **Status:** **(a) and (b)
both IMPLEMENTED, wave 1 (2026-08-17)** — (a) as `isolation.snapshot_selection`
/ `unsafe_symlink_omissions`, `W1-CARVE-B006a-project-scope.md` WI-0 through
WI-4 (A-269; supersedes the project-scoped-boundary design originally proposed
here, see the AMENDED callout immediately below); (b) as the in-snapshot
coverage-artifact parent-chain creation, `W1-CARVE-branch-coverage-and-whole-
target.md` §2. Both are shipped, gated and documented on
`assay-B005-B006-coverage-v6`. **Not yet DONE end to end:** not yet merged,
released, or adopted by a real consumer lane — CMRU's own `assay.toml` is
deliberately UNTOUCHED by this commit (still schema v1, R0-only; its own
higher-rigor qualification is B006(a) WI-5, still open) and must not be read
as proof this item is finished until that lane, the merge, and the release all
land. Until then CMRU may retain R0/direct-coverage evidence, but must not
relabel a project-local stopgap as Assay R1/R2/R3.

> **AMENDED 2026-08-17 by ruling A-269 — read this before the prose below.**
> The requirement stands; **the solution sketched in "(a)" below does not.**
> This item's own words say the sketch is only "one possible implementation" and
> that "exact TOML names are Assay's design decision", and A-269 exercises that
> latitude after the project-prefix design failed three independent adversarial
> reviews at 8 → 9 → 11 blocking findings.
>
> **What ships instead:** the FULL repository snapshot, minus exact,
> commit-validated omissions of the symlink leaves P22 would otherwise refuse —
> `snapshot_selection = "repository" | "repository-minus-unsafe-symlinks"` plus
> `unsafe_symlink_omissions = [...]`. Because everything else is still
> materialised, the `inputs` inventory that point 1 below demands is **not
> needed and not shipped**: CMRU's repository-root reads keep working with no
> declaration at all.
>
> **Consequently, in the numbered contract below:** point 1's project scope,
> owned prefix and additional-inputs list are WITHDRAWN; point 4's five
> containment preflights are WITHDRAWN as unreachable from any loadable
> `assay.toml` (only the coverage-artifact/omission collision survives, and it
> is reachable only through the public `Lane` API); point 5 is met in modified
> form, recording the selected policy and its exact omissions rather than a
> "project" label whose enforceable content was nil; and points 2, 3 and 6 stand
> as written, narrowed by A-267/A-268 — assay never materialises the omitted
> leaf, but the retained closure means the command can restore it, so no
> confinement is claimed.
>
> **One measured correction to the prose below:** Topos carries **three**
> tracked absolute `/etc/passwd` symlinks, not the single `passwd_link` named
> here. A design that handled only the named one would still have failed.

### (a) One absolute-target symlink anywhere in the tree fails every R1+ lane

assay's P22 committed-snapshot walks the **entire reachable tree** of the resolved
commit and refuses any symlink whose target escapes the snapshot root —
`assay/isolation.py:_check_symlink_target` (~:860): "Refuse any symlink that does
not resolve inside the snapshot root", raising `GIT_FAILED` on an absolute target.
This is a correct hermeticity guard in principle. But it is applied to the whole
tree regardless of `source_roots`, so a single tracked absolute symlink in an
unrelated corner of the repo fails **every** R1/R2/R3 lane in **every** package,
permanently, until someone removes it — while R0-only lanes (which bypass the
snapshot) stay green and hide the problem.

In dstdns this was `infra-global/reverse-proxy/etc-nginx/modules ->
/usr/lib/nginx/modules` — a vendored nginx-container artifact with no relationship
to any Python source root. It blocked the program's entire coverage-gating effort
on day one; the only fix was to stop tracking it. A large multi-service monorepo
will routinely carry such artifacts (vendored container configs, toolchain
symlinks) far from the code any given lane judges.

The earlier "source-root scoped walk" / "allow or skip" proposal is withdrawn:
source roots do not include every test dependency, and an ignore list only hides a
path from validation without proving the executed command cannot reach it. The
required design is an affirmative, attested materialisation boundary, specified in
the expanded requirement below.

### The CMRU reproduction and required capability

CMRU makes the failure concrete. Its higher-rigor lane is rooted at `cmru`, but
Assay first materialises the full monorepo commit and correctly refuses Topos's
tracked fixture `topos/tests/fixtures/inspect_files/_danger/passwd_link ->
/etc/passwd`. CMRU neither owns nor needs that path. No source root, coverage
artifact, mutation candidate, canary target, or declared project input is under
`topos`; the failure occurs before the project command runs. R0 passes, proving the
argv is viable but providing no R1+ verdict.

Add an explicit snapshot materialisation mode for R1/R2/R3. Exact TOML names are
Assay's design decision, but the contract must be:

1. A lane explicitly chooses **repository** or **project** scope. Repository scope
   preserves today's full-P22 behaviour. Project scope declares an owned,
   repo-top-relative prefix and a finite list of additional repo-top-relative,
   tracked inputs required by the test command. CMRU needs its own tree plus named
   root release/sample artifacts which its tests deliberately inspect. There is no
   ambient discovery or fallback to the caller checkout.
2. Canonicalise every scope path as a Git-tree path. Refuse absolute paths, `..`,
   empty paths, duplicate/ambiguous overlap, missing/untracked paths, and any path
   resolving outside the selected commit. An in-scope symlink keeps P22's current
   containment check and fails closed.
3. Retain the complete resolved commit, object closure, base resolution, and
   provenance, but materialise only the declared prefix and inputs in a private
   worktree/index. A private full-HEAD index with all non-selected entries marked
   `skip-worktree` is one possible implementation; it must prove a clean checkout
   and that the command cannot read a sibling worktree.
4. Validate before execution that every source root, coverage artifact, mutation
   candidate, canary target, and command working directory is inside the
   materialised boundary. Never broaden scope automatically because a dependency
   is absent.
5. Record scope mode, full commit, project prefix, and canonical expanded input
   set in the verdict. This must be a schema/versioned attestation so reviewers can
   distinguish full-repository from project-scoped evidence.
6. Use a private index/worktree only: flags, generated parents, hooks, and source
   replacements cannot leak into the source checkout, and a nested command cannot
   regain omitted files through environment or relative traversal.

This is deliberately not `exclude = ["topos/**"]`, an `allow_symlinks` escape,
or a best-effort fallback. A project that needs a sibling names it; an unsafe path
inside that named boundary remains a loud P22 refusal.

### Acceptance tests and adversarial oracles

- A fixture repository contains an absolute-target symlink outside project scope.
  Project-scoped R1, R2, and R3 pass through the normal snapshot path, and the
  external target is never materialised or readable. Repository scope still fails
  with P22's existing diagnostic.
- An absolute-target or escaping relative symlink inside the owned prefix or a
  declared input fails before the command; malformed, missing, untracked,
  absolute, and `..` declarations do likewise.
- A named root test dependency is present. Removing it from the explicit inputs
  causes deterministic preflight failure rather than ambient checkout access.
  Tests assert private `HEAD`, `git status`, `git diff`, base diff, and replacement
  semantics remain exact.
- Mutation/canary targets outside scope are refused; in-scope targets change only
  in the private snapshot. A deliberate failure proves no temporary index,
  worktree, or `skip-worktree` flag contaminates the source repository.
- An end-to-end CMRU lane in `tester-unified` makes genuine R1/R2/R3 verdict
  claims while the Topos fixture remains tracked. Its bounded mutation campaign
  kills every non-equivalent mutant and its canary fails for the required coverage
  reason.

Release the feature as a versioned Assay artifact and pin it in CMRU before CMRU
removes its temporary runner evidence. Direct whole-source coverage remains useful
defence-in-depth, but cannot substitute for a verdict that attests what it measured.

### (b) The coverage-artifact parent dir must pre-exist in the snapshot, but the snapshot is tracked-only

`assay/safeio.py:reserve_output` opens the coverage artifact's parent chain with
`O_DIRECTORY|O_NOFOLLOW` and **refuses a missing parent** (`_open_parent_chain`);
it never `mkdir`s. The lane argv runs with `cwd = snapshot.project_root`
(`assay/runner.py` `execute_plan`), and the snapshot contains **tracked files
only**. So the near-universal pattern — write coverage JSON to a conventional
gitignored scratch dir (`.assay/`, `.coverage-out/`, `build/`) — fails with
`UNREADABLE_ARTIFACT`: the dir is gitignored, hence absent from the snapshot, hence
the reservation refuses its missing parent, hence the artifact is never written and
never read.

The failure is opaque: `UNREADABLE_ARTIFACT` reads as "your tests produced no
coverage", when the true cause is "the directory you asked coverage to write into
doesn't exist in my snapshot". dstdns worked around it by tracking an empty
`.assay/.gitkeep` and ignoring only the contents (`.assay/*`, `!.assay/.gitkeep`) —
every consumer with a gitignored coverage-output convention will hit this and need
the same trick.

**Required companion change:** assay owns the artifact path (it reserves it), so it
creates and validates the declared artifact parent chain inside the ephemeral
snapshot before running the argv. The mkdir is confined to Assay-owned output
inside the already-declared scope; it is not permission to create arbitrary missing
paths or climb above scope. A symlinked or escaping parent remains a loud refusal.
The verdict records the requested artifact path, and diagnostics distinguish setup
failure from a genuinely unreadable artifact.

### Evidence available on request

dstdns `main`: commit `c359a6b1` (symlink removal, with the assay error verbatim in
its message), `9f42acdc` (`.assay/.gitkeep` + gitignore), and the two intermediate
`assay run redirect_chain` verdicts showing `GIT_FAILED` then `UNREADABLE_ARTIFACT`
then `PASS`; CMRU at `4b8009d5` has an honest R0 verdict and independent
whole-source coverage gate, while its attempted R1+ run fails on the Topos
`/etc/passwd` fixture before tests execute.

---

## B007 — ordered, bounded, explicitly declared multi-target R3 canary

**Proposed by:** nyxloom, 2026-08-17, while retiring its own coverage, mutation,
canary, verdict and gate-judgment implementations in favour of assay through the
public CLI/verdict boundary. **Status: ASSESSED AND DEFERRED — the first
post-v6 schema item (v7).** Not folded into wave 1. The reasoning is below and
is binding on whoever picks this up.

### The requirement, in the proposer's words

Assay already has the stronger cause-sensitive R3 contract: a known-good control
must PASS and the transformed input must FAIL *for the mechanism's expected
reason*. The one behaviour still unique to nyxloom is that its gate
qualification can try **several source files in a declared order**, so a gate is
not declared to launder known-bad code merely because one arbitrarily chosen
module happens not to be imported or exercised.

Explicitly **not** wanted: nyxloom's automatic source-file discovery and
ranking. It is Python-specific heuristic policy and would become a hidden
default. The operator declares targets; assay executes them deterministically.

Candidate shape (spelling is assay's decision):

```toml
[lanes.<lane>.judge.canary]
mechanism = "import-break"
targets = ["src/pkg/api.py", "src/pkg/service.py", "src/pkg/model.py"]
aggregation = "any"     # explicit, no default
```

Full requirements, oracles and the optional `assay canary qualify` CLI sketch
are in the proposal as filed; the seven numbered requirements and eight
behavioural oracles are adopted here by reference and must not be diluted.

### Verified against the shipped code before assessing

* **`import-break` is real**, not aspirational: `CANARY_MECHANISMS` is exactly
  `{import-break, uncovered-line}` (`canary.py`), *"named identically to
  nyxloom's own `gate_canary.MECHANISM_IMPORT_BREAK`/`MECHANISM_UNCOVERED_LINE`"*
  — so the adoption story is sound and the vocabulary already matches.
* **The singular target is load-bearing in the INDEPENDENT verifier**, not just
  the model: `verdict.py` cross-checks `claim[R3].canary.target` against
  `judgment.r3.target` and refuses a mismatch — *"a canary that answers for a
  different file than the one declared is evidence about nothing the lane asked
  for"*. Requirement 7's "must not be silently reinterpreted" is therefore
  already enforced by shipped code, which is a good sign for the migration.
* **Each attempt costs two isolated materialisations** (control via
  `prepared.materialize()`, transformed via `materialize_replacement()` in
  `run_isolated_canary`). So N targets cost **2N**; under `any` with
  short-circuit typically 2, under `all` always 2N.

### Why it is DEFERRED rather than folded into v6

Judged against the proposer's own test — "if it can safely fit into the
in-progress verdict-v6 work **without destabilising B005/B006**".

1. **Nothing is blocked on it, and two things are blocked behind it.** The
   proposal itself states nyxloom's initial adapter stays v6-compatible and must
   not depend on this feature. Meanwhile dstdns is blocked on B005 (to retire a
   `--cov-fail-under` stopgap) and B006 (to retire two substrate work-arounds),
   both of which are **built and waiting only on release**. Folding in delays a
   shipped capability for one that nobody is waiting on.
2. **The v6 cut is already committed and verified** (99 files, suite 2814
   passed). Reopening it re-runs a proven 41-file migration and re-derives the
   26-node P33 successor suite, for a feature whose payload changes every
   existing R3 fixture shape.
3. **The delta is comparable in size to B005 itself**, not a papercut: a closed
   `aggregation` enum; a closed "why not attempted" vocabulary; an ordered
   per-attempt payload array; the `judgment.r3` policy gaining `targets` +
   `aggregation`; canary sequencing with short-circuit bookkeeping; and —
   largest — requirement 5 forces the aggregation to be **independently
   recomputed in `verify.py`**, which by this project's deliberate discipline
   hand-transcribes rather than importing the model. That logic gets written
   twice, on purpose.
4. **Wave 1 has already been destabilised twice by scope arriving mid-flight**
   (`c7bc9b59`, then `010d1813` rewrote B006 under an in-progress
   implementation), and the direct consequence was three adversarial review
   rounds that diverged 8 → 9 → 11 blocking findings. A third mid-wave widening
   after the schema cut is committed is the same move again.
5. **The "save a schema major" argument is real but much weaker than it looks.**
   Requirement 7 needs a version bump either way, and v6 has not shipped, so an
   older verifier refuses the newer shape under either plan. Crucially the v6
   work leaves behind **reusable migration machinery** — `migrate_v5_to_v6.py`'s
   fail-closed four-bucket classifier, the frozen-successor-suite pattern, the
   `carve-assets/W1/` layout. The first migration had to invent all of that; the
   second inherits it. v7 is a fraction of v6's cost.

### Design findings for whoever builds it — do not rediscover these

* **`aggregation` is not ergonomics, it is the claim, and the verdict must say
  which was made.** Today R3 attests *"the gate catches known-bad code in **this
  named module**"*. Under `any` it attests *"…in **at least one of** these
  modules"* — the same gate-level statement, a strictly weaker per-module one.
  Under `all` it attests the per-module statement for every declared target.
  A reviewer must not be able to read the stronger claim off an `any` verdict.
  **An attestation stronger than its mechanism is what killed three consecutive
  review rounds on B006(a); do not repeat it here.**
* **`any` + short-circuit has a vacuity variant worth closing deliberately.** A
  lane can declare 25 targets, put a trivially-always-imported module first,
  PASS on attempt 1 forever, and never discover the other 24 are unreachable.
  That is honest under `any`'s stated meaning but defeats the *intent* "our gate
  protects these 25 modules". Say so plainly in CONSUMERS.md, and consider
  whether the artifact should surface how many targets have **never** been
  attempted across runs.
* **The bound is a budget control, not hygiene.** At 2N materialisations, an
  `all` aggregation over CMRU's ~25 modules is ~50 isolated snapshots per gate
  run. Measure one materialisation before choosing the maximum, and keep
  requirement 4's rule that budget exhaustion stays its own terminal and is
  never converted into PASS/FAIL.
* **It interacts with B005, which just shipped.** §5 of the wave-1 carve already
  rules that an `uncovered-line` canary on a `whole_target` lane is refused at
  load unless `judge.canary.target` is itself one of `judge.targets`. Under a
  canary target LIST that rule generalises to "every canary target must be in
  `judge.targets`", and it must be specified rather than discovered.
* **The `assay canary qualify` CLI separation is achievable, but only one way is
  honest.** A flag on the normal verdict is not enough — an ad-hoc override
  would then be one field away from looking like committed gate policy. The
  clean boundary is a **distinct document kind** that `assay verify` refuses to
  accept as gate evidence at all, recording the committed lane plus every
  effective override. If that cannot be made clean, the proposal's own
  instruction stands: ship only the committed declaration.

### Sequencing

Wave 1 (B005 + B006) releases first, unchanged. B007 is then the first schema
item after v6 and should be carved together with any other v7-requiring change
so the estate pays one migration, not two — the same argument that made A-262's
rename ride v6 rather than wait.

**MEASURED 2026-08-17, after the v2.0.0 release: there is no partner, and that
inverts the order.** The pairing argument above assumed other planned work
would also need v7. It does not:

* ~~**B004 needs no schema change at all** — its own section says so in writing
  ("no verdict-schema change was needed and none proposed"), and the only
  variant that would need one (enforce-on-mismatch, requiring a new
  `ReasonCode` and therefore a closed-enum widening) is explicitly **not
  proposed**;~~ **WRONG, and the carve proved it (A-275).** §B004's sentence is
  true of the RECORDED half and false of the VERIFIED half; taking it at its
  word is precisely the mistake this note asked each carve to stop making. B004
  needs exactly one new `ReasonCode`, `PROVENANCE_UNVERIFIED`;
* **B001/P34's producer fields are already RESERVED and survived the v6 cut** —
  `_MUTATION_FIELDS_RESERVED_FOR_P34` (`config.py:269`) names
  `kill_signal_artifact` and `equivalence_artifact`, and both are still present
  in `verdict.schema.json` and `verdict.py` at v6. P34 begins producing an
  already-reserved surface rather than widening one.

So B007 alone would force a **second breaking migration on consumers days
after v6 and lane-v2**, for one feature nobody is blocked on — nyxloom's own
proposal states its initial adapter stays v6-compatible and must not depend on
it. **Revised order: B004 (wave 2) → B001/P34 (wave 3) → B007.** That gives
consumers a stable v6 period, and lets B007 accumulate a partner if any of
wave 2 or 3's implementation turns out to want schema surface after all — a
question their carves should each answer explicitly rather than assume.

**UPDATE 2026-08-17, after wave 2's carve and review: B007 HAS ITS PARTNER, and
it is B004.** The bullet above was wrong in exactly the direction this
paragraph hoped for. `PROVENANCE_UNVERIFIED` is reserved by A-276 to ride
whichever bump another item pays for, and B007's ordered multi-target R3 canary
is the item most likely to pay it. So v7, when it comes, carries both — one
migration, two features, which is the outcome the original pairing argument
was written to get. **The order stands** (B001/P34 next), because P34 still
needs no schema surface and B004's implementation is blocked on ciu regardless
of when the code ships. What has changed is that B007 is no longer a solo
migration nobody is blocked on, and its carve should be written knowing it will
be asked to carry a passenger.

## B008 — R1 base resolution on a merge-commit HEAD silently narrows the changed-line floor

**Filed 2026-08-20 (dstdns Fable controller, from ciu's first two production gate runs
of the vendored assay-2.1.0.pyz).** Reproduced, not speculated.

A lane declares `judge.base = "origin/main"`. When HEAD is a **merge commit**
(operator merged origin/main INTO the feature branch, then ran the gate), the
verdict's `judgment.resolved.base` came back as **HEAD^1 (the pre-merge branch
tip)**, not `git merge-base origin/main HEAD`:

- Measured in ciu worktree at merge tip `cbd0f03a` (parents `01abdce2` branch,
  `f882fc24` = origin/main): `git merge-base origin/main HEAD` → `f882fc24`,
  but `resolved.base` → `01abdce2`. R1 therefore judged `HEAD^1..HEAD` — i.e.
  ONLY the content the merge brought in from main (already gated), while the
  branch's own ~2100 inserted lines fell OUTSIDE the changed-line floor. R1
  reported PASS with the floor vacuously narrow — a silent-wrong-scope, not an
  error.
- Non-merge tips resolve correctly (P07 run at `db861ac2` → base `98549075` =
  merge-base, as declared).

**Why it matters:** merging the base branch into a feature branch before
gating is a standard pre-merge validation step; on exactly those runs the
changed-line floor quietly stops covering the feature. The whole-source
100% floor (R0) masks it for lanes that have one — for any lane with
`fail_under < 100`, R1 is the only changed-line protection and it is the run
where it silently evaporates.

**Ask:** resolve `base` as `git merge-base <declared-base> HEAD` uniformly,
merge tips included — or, if first-parent semantics on merge tips is
intentional, surface it loudly in the verdict (a `base_resolution:
"first-parent"` field + a WARN) so a reviewer can tell the floor's actual
scope. **Workaround used:** re-run the gate after merging to main, where
first-parent and merge-base coincide.

**Second reproduction, 2026-08-25 (dstdns P132 implementer, priority
evidence — same mechanism, different judge kind).** Confirms the bug also
collapses **R2 mutation lanes**, not only R1 changed-line coverage as
originally scoped. Reproduced by merging `main` into
`feat/dstdns-P132-worker-io-execution-repair` (`git merge main --no-edit` →
`039d9679`, HEAD now a merge commit), then re-running the four
`worker-execution-admission-r2-*` lanes: all four read
`INCONCLUSIVE/NO_MUTANTS`, down from real PASS/kill counts (9/9 boolop, 8/9
flips) measured on the same source pre-merge. Confirmed directly:
`git diff --stat 55c41dac..039d9679 -- applications/worker-io/src` is empty
— `resolve_base` used HEAD's first parent (the branch's own pre-merge tip,
`55c41dac`) instead of the lane-declared `judge.base`, so the diff assay
measured is "what did pulling main in change" (nothing in the package's own
source), not the branch's actual accumulated work. Same root cause as the
R1 case above (`assay/git.py::resolve_base`), same fix would close both.
**Not a workaround this time** — confirmed self-resolving at final-merge
time (when the branch lands ON main, main's own pre-package tip becomes the
first parent, restoring a real diff), but real evidence quietly went from
"9/9 mutants killed" to "nothing to measure" with zero warning in between,
on a source tree that hadn't actually lost any coverage. Provenance:
`dstdns/nyxloom-trove/reports/dstdns-P132-REPORT.md` §1a/§1b/§2/§2a.

**Status: RESOLVED 2026-08-25 (stabilization wave), by transparency not by
changing `resolve_base`.** The first-parent-on-merge-commit behavior is
documented, deliberate design in `resolve_base`'s own docstring, not an
oversight — so the ask's second option ("surface it loudly") is what shipped:
a new `git.base_resolution_mode(repo)` and an additive `base_resolution:
"merge-base" | "first-parent"` field on `JudgmentResolved`, present exactly
when `base` is. A consumer can now tell which branch fired instead of having
to re-derive it from `git rev-list --parents` themselves. See A-301. The
frozen W3 witness was re-witnessed for the new field (A-304).

**The ask's own wording was "a `base_resolution` field + a WARN"; only the
field shipped, and this is a deliberate scope decision, not an oversight —
recorded here after round 2 review flagged the gap.** This project's outcome
vocabulary has no severity channel between a decided `(outcome, reason_code)`
and silence; a literal "WARN" would mean either a new `Outcome`/`ReasonCode`
(the same "deliberate, not a quick add" bar B026 N-4's closed-enum reasoning
already applies) or a free-text field this project's refusals deliberately
don't carry (B026 N-4, again). `docs/CONSUMERS.md`'s new "Check
`judgment.resolved.base_resolution` after a pre-gate merge" section is the
substitute: not a runtime alert, but the loud, explicit, worked-example
documentation a human reader needs to know to check the field at all — which
is what was actually missing before this round (`base_resolution` appeared in
no consumer-facing doc, only the schema).

## B009 — document assay.toml's estate role + the image-baked distribution model (operator decision 2026-08-20)

Operator interview (dstdns Fable controller session, dstdns ledger D-110), after
ciu-P07 vendored the pyz per the cmru precedent:

1. **Docs ask:** assay's own documentation must state `assay.toml`'s role:
   an **adapter + judgment-policy file for projects that adopt assay** (floors,
   R-levels, isolation, pointing at the project's own entrypoints) — NOT an
   estate-wide lane registry. Projects that cannot adopt assay (host/shell
   tooling like modern-debian-tools-python-debug) declare their gates in a
   project-root `run-gate.sh` without it; assay is invoked FROM such scripts
   where it judges. Requiring "assay-judged before release" is release policy
   per project, not a reason to put assay.toml everywhere.
2. **Distribution ask:** per-repo vendored `tools/assay/*.pyz` (cmru, ciu) is
   RETIRED as the estate pattern. The judge is baked into the tester-unified
   image, built from the monorepo's own assay source via the cmru dependency
   chain (fresh-clone safe — no GitHub artifact required); adopting repos keep
   a version pin their gate verifies against the installed judge. De-vendoring
   carves: ciu CIU-40, cmru equivalent.
3. **Forward note:** async long lanes (mutation campaigns, fuzzing) will be
   additional assay lanes with large budgets, triggered by Buildkite agents on
   remote hosts (and by nyxloom/user/controller alike), all calling the same
   project `run-gate.py` — see B007's multi-target canary for the shape of
   bounded long-running judgment. (D-111 refinement: the entrypoint is a
   shared `run-gate.py` reading a per-project `run-gate.toml` it alone parses (home: `run-gate-project/`);
   assay-judged lanes are referenced there by name — `assay.toml` keeps
   owning judgment, `gates.toml` owns orchestration. One parser, argv for
   every consumer.)

## B010 — `assay run` is unusable when the gate environment is not the invoking environment

**Filed 2026-08-20 (dstdns P111 auth-config-cutover implementer, Mode-B wave).**
**Status:** **PARTIALLY IMPLEMENTED 2026-08-24.** A lane may declare an optional top-level
`environment_command`; `assay run` executes that zero-exit probe in the invoking environment before any
repository/snapshot work and refuses `ERROR`/`BAD_LANE_CONFIG` on failure. This gives consumers an
explicit fail-fast contract for a wrong dependency closure, but does not bake or orchestrate gate
images (the B009/run-gate direction remains).
Provenance: `dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md` §6 (disclosed
caveat) + §9 F6. Reproduced, not speculated — and verified at dstdns `main`
@ `36cb7183` as well as the P111 branch, so it is a standing environment fact,
not a branch regression.

### The observation

`python -m assay.cli run auth` in the dstdns devcontainer cockpit fails at
suite import: the lane's test module (`tests/unit/test_auth.py`) imports
`fastapi.routing.iter_route_contexts`, which is absent from the cockpit's
FastAPI 0.135.1 and present in the app image's pin. Nothing auth-specific about
it: any lane whose suite imports a symbol from the app's pins fails the same
way in that interpreter. dstdns's own doctrine (AGENTS §6, cockpit-vs-gate)
predicts this exactly — the gating environment is the `test-runner` container
(`FROM dstdns-app-base`, dependency closure identical to the app runtime), and
the devcontainer is a cockpit carrying different pins.

### Why this is an assay concern and not only a dstdns one

`assay run` executes the lane's declared argv in the invoking environment, and
`assay.toml` has no way to declare WHERE the lane is valid. Consequence
observed in P111: the coverage lane could not be evidenced by assay's own
verdict at all — the implementer had to re-run the lane's exact argv inside
`test-runner` manually and then mechanically check the judge's criteria
(single target, statements/branches/missing, `allow_excluded`,
`require_branch`) against the emitted coverage artifact by hand. The wrapper
exists precisely to own that judgment, and it cannot be run where the
dependencies are right. This failure mode was at least LOUD (an ImportError);
the dangerous sibling is quiet — a lane that RUNS under wrong pins and judges
the wrong behavior with a green verdict.

### Two resolution readings — both presented

1. **Doctrinal/docs (cheap).** Declare explicitly that `assay run` is only
   meaningful in the project's gate environment, and that getting assay INTO
   that environment is the run-gate layer's job (ciu CIU-40 / this backlog's
   B009 forward note: judge baked into the gate image, `run-gate.py` owning
   the docker/cgroup mechanics). Then the dstdns-side fix is wiring assay
   through `testing-exec.sh` / baking it into the consumer's gate image — the
   B009 tester-unified direction generalized to consumer gate images — and
   assay's documentation states the contract so the next consumer doesn't
   rediscover it via ImportError.
2. **Mechanism.** A lane-level environment preflight: an optional declared
   environment fingerprint (interpreter + selected pinned-package versions, or
   an arbitrary project-declared probe argv) that `assay run` checks before
   executing the suite, refusing with "this lane's declared environment does
   not match the invoking one; run via <declared wrapper>" instead of
   surfacing the suite's raw traceback. Precedent in-house: the P34 SQL
   adapter already ships an external-tool preflight (wave 3) — this
   generalizes the same refuse-early shape to the interpreter environment.
   Explicitly NOT proposed: assay growing container orchestration; per the
   estate's D-111 layering that belongs to `run-gate.py`, with assay keeping
   judgment.

Cross-references: B009 (image-baked distribution + assay.toml's estate role),
ciu CIU-40 (gate-layering refactor / run-gate mini-project).

**Correction 2026-08-25 (review-gap audit, `reports/assay-review-gap-audit-2026-08-25.md`
finding 8a-E):** the "PARTIALLY IMPLEMENTED" `environment_command` mechanism
does not ship the clear-message deliverable this item asked for — a probe
refusal writes **0 bytes of stderr**, not the "this lane's declared
environment does not match the invoking one; run via `<declared wrapper>`"
text quoted above. The consumer gets neither the original raw traceback nor
the promised clear message. Filed separately as **B032**, which also covers
the same probe's budget/outcome misclassification.

**Follow-up 2026-08-25 (B032 remediation, A-321/A-322):** the clear-message
deliverable now ships. A probe refusal writes the lane name, the specific cause
(nonzero exit and its code, an unexecutable command, or a preflight-cap
timeout), and `Run via the declared wrapper: <argv>` to stderr, verified
non-empty through the installed CLI. B010's own status stays **PARTIALLY
IMPLEMENTED**: the image-baking/orchestration half (B009/run-gate) is still
unaddressed and always was a separate direction; what is closed is the
fail-fast probe plus its diagnosis.

## B011 — CONSUMERS.md's cross-tool wiring example teaches the superseded pre-run-gate integration

**Filed 2026-08-22 (vbpub controller session, adversarial review of the
run-gate estate-wide adoption wave `vbpub@4c6eb2b6..91959b3a`; consumer-UX
reviewer, finding "stale against the adopted mechanism").**
**Status:** **FIXED 2026-08-24.** `docs/CONSUMERS.md` now shows the adopted `run-gate.toml`
`kind = "assay"` lane shape and points to run-gate's adoption guide instead of teaching the retired
raw CMRU/tester invocation.

### The observation

`docs/CONSUMERS.md` §"Wiring into a consumer gate" (the section around lines
401–408) shows cmru invoking assay as:

```bash
cmru tester-gate --cwd cmru -- … tools/assay/assay-<version>.pyz run cmru --file assay.toml …
```

Since the adoption (vbpub@4c6eb2b6, 2026-08-22), cmru's shipped release gate
is `["./run-gate.py", "gate"]` (`cmru/cmru.toml [steps.run-tests]`), whose
sub-lanes are declared in `cmru/run-gate.toml` — the SSOT per estate decision
D-110/D-111. The old raw `cmru tester-gate … pyz run …` shape no longer
exists anywhere in cmru's configs. A consumer following assay's own doc
builds an integration pattern the estate has retired; the doc actively
teaches the superseded mechanism.

### Why this is an assay concern

The stale text is in assay's own consumer-facing documentation. run-gate's
docs defer judgment-layer docs to assay (run-gate-project/CONSUMERS.md
"Partner integration notes"), so this page is the one place a new adopter
will look for the wiring — it must show the current shape.

### Proposed fix

Rewrite the example to the adopted two-file split: a `kind = "assay"`
lane in `<project>/run-gate.toml`
(`assay_lane` + explicit `assay_command` + sha256 pin) and a thin consumer
pointer (`argv = ["./run-gate.py", "<lane>"]` in nyxloom.toml / cmru.toml),
linking to run-gate-project/CONSUMERS.md for orchestration mechanics.
Alternatively annotate the old form explicitly as the pre-run-gate
alternative — but showing a dead integration as the primary example is the
failure mode; prefer replacement.

### Oracle

Estate docs-contract discipline: any cross-tool wiring example must match a
config that actually exists on main. Mechanically: a periodic sweep (or a
manual check recorded in the fixing commit) that every `pyz run <lane>`
example in this file corresponds to a live lane declaration in the named
project. Controlled wrong implementation: the current text fails it today.

**Related:** run-gate-project backlog RG-13 item 1 (the missing end-to-end
worked example that should become the canonical stitched version of both
halves).

## B012 — mutation execution observability, planning, resume/sharding, and per-candidate budgets

**Filed 2026-08-23 (dstdns repair program; consumer evidence from P127 admission mutation lanes and the SQL mutation blocker).**
**Status:** **IMPLEMENTED 2026-08-25, REMEDIATED 2026-08-25.** Shipped: baseline/per-candidate progress NDJSON,
`mutation.progress_artifact` in the v6 payload, `assay plan`, deterministic plan IDs, per-file/operator
counts and runtime estimates, optional `budget_per_candidate`, bounded state records under
`.assay/mutation-state/`, CLI operator filtering, zero-based deterministic sharding, and manifest merge
validation. Worker identity is supplied by the executor boundary when present; it is not invented as a
public lane option. **The initial 2026-08-25 implementation was self-reviewed only and shipped four real
defects an independent adversarial review (round 1, same day) found and this remediation fixed:** two
unimported names (`LaneConfigError`, `os`) crashed every `--operators`/`--shard` refusal path with
`NameError`; the CLI's `--shard` was 1-based against the 0-based contract everywhere else and entirely
untested; a sharded verdict recorded the lane's static declaration instead of the executed `--shard`
value, making it indistinguishable from a complete run; and `merge_mutation_shards` accepted
cross-assigned candidates and all-empty shards as valid coverage. See A-296 in `decisions.md` for the
full citations. A genuine design gap the review also found — stale-record disposition is inverted, see
below — is filed separately as **B021**, not fixed in the remediation pass.

### The observation

A dstdns Python R2 lane with 17 candidates timed out three times. Each timeout left the same partial evidence (`4 killed / 3 survived / N unattempted`) with no way to tell which candidate was in flight or whether the lane was hung. A SQL R2 lane failed before its first mutant for an infrastructure reason (see B013) and emitted no per-mutant artifact either. The only available workaround was manually splitting one declared workload into several operator-group lanes and hand-maintaining disjoint/exhaustive coverage bookkeeping.

### Required capabilities

These are implementation-guess-free requirements, not design preferences:

1. **Structured progress events.** Emit NDJSON after baseline and after each candidate: candidate index/total, deterministic candidate ID, path, operator, byte span, replacement hash, worker id, elapsed seconds, outcome bucket on end. Summarize the artifact path in the verdict.
2. **Plan/preflight mode.** `assay plan <lane>` must report total candidates, per-file/per-operator counts, deterministic IDs, measured/estimated baseline runtime, projected serial/wall-clock runtime using `jobs`, without executing the command.
3. **Deterministic candidate IDs.** Stable digest over repo-relative path + original file digest + byte span + replacement digest + operator; required for resume, sharding, survivor reports.
4. **Resume/checkpointing.** Persist one JSON record per completed candidate under `.assay/mutation-state/<candidate_id>.json`; support `--resume`, mutation status, retry-unattempted semantics; invalidate stale records when source hashes change.
5. **Native operator filtering/sharding.** Allow declared subsets of one lane (`--operators ...` or `--shard i/N`) with assignment derived deterministically from candidate IDs, plus a summary merge that refuses non-disjoint/non-exhaustive input.
6. **Per-candidate budget distinct from lane budget.** `budget_per_candidate` marks one candidate `budget_exceeded`; `budget_total` remains the hard lane bound. Current behavior conflates “one slow mutant” with “lane cannot finish”.

### Workaround currently used by consumers

Split lanes by operator group, retain separate verdicts, and require a combined report summing every bucket (`killed`, `survived`, `crashed`, `equivalent`, `budget_exceeded`). Viable, but requires manual set algebra to prove full coverage.

### Acceptance

- [x] progress events emitted and referenced from verdict — **correction
      2026-08-25 (review-gap audit): false.** `mutation.progress_artifact` is
      dead code (no path populates it), unregistered in `verify.py`'s
      reconstruction layer (a real verdict carrying it fails `assay verify`
      as an unknown field), and the NDJSON it writes lands in the consumer's
      live worktree, poisoning the dirty-tree precondition on the next run —
      see **B031**. **Resolved 2026-08-25 (B031/A-320), NARROWED:** progress
      events are emitted, opt-in, to a consumer-named path
      (`assay run --progress PATH`) and are NOT referenced from the verdict —
      `mutation.progress_artifact` was removed rather than wired, because its
      only legal spelling named the worktree location A-292 forbids and the
      `--verdict-json` precedent is that assay does not record back a
      destination its caller chose. This half of requirement 1 is deliberately
      unmet, not met;
- [x] `assay plan` reports deterministic totals/IDs/runtime estimate —
      **correction 2026-08-25 (review-gap audit): false.** `assay plan`
      returns `candidate_count: 0` for every lane unconditionally (a source-
      root relocation bug), and its own test asserts the bug — see **B030**.
      **Re-verified true 2026-08-25 (B030/A-319)** by driving the installed
      CLI against a real lane: `plan`'s `candidate_count` and candidate id
      now equal `assay run`'s own, on the same commit. The runtime estimate
      remains a declaration-derived upper bound, never a measured baseline —
      now documented as such in `docs/CONSUMERS.md` rather than implied to be
      a measurement;
- [x] interrupted lane resumes without rerunning completed candidates;
- [x] shards are provably disjoint and exhaustive;
- [x] per-candidate timeouts do not abort unrelated candidates.
- [x] a stale mutation-state record is detected and does not silently reuse
      wrong evidence — correction 2026-08-25: a stale `source_sha256` reruns
      the candidate silently rather than "refusing" (see B021 for why the
      current disposition is itself backwards and filed separately);
- [x] shard manifests refuse duplicate, missing, cross-assigned, or
      all-empty coverage (assignment-domain check added 2026-08-25 remediation;
      still no shipped producer/consumer for a real shard-summary document —
      see B023).

## B013 — repository-only snapshots cannot provide infrastructure facts required by SQL mutation lanes

**Filed 2026-08-23 (dstdns SQL mutation blocker; consumer evidence from `dstdns-SQL-MUTATION-LANE-BLOCKER.md`).**
**Status:** **IMPLEMENTED 2026-08-25, REMEDIATED 2026-08-25.** Lanes may declare
`[lanes.<name>.infrastructure]`; sources are `required-env:NAME` and
`derived:dotted.path`. Resolution happens in the invoking process at plan
construction, before Git or snapshot work. Missing, empty, malformed, or
colliding names refuse loudly. Resolved values are injected as environment
variables only; snapshots remain free of runtime state. **The initial
2026-08-25 implementation was self-reviewed only and was completely
non-functional: `cli.py` never imported `os` (used unconditionally to read
`os.environ`), so `assay run` crashed with `NameError` on any lane declaring
this table at all, and `infrastructure_source` was hardcoded `None` with no
wiring, so the `derived:` half could not be reached even with the import
fixed.** An independent adversarial review (round 1, same day) found both
gaps by driving the shipped CLI — the merged tests call
`resolve_command_plan`/`run_lane` directly and never exercise `cli`, so
neither was visible to the suite. This remediation adds the missing import
and wires `infrastructure_source` to `lane_file.project_root / "ciu.global.toml"`,
the exact path ciu itself renders and gitignores. The review's three seeded
attacks against the resolution logic itself (dotted-path traversal, malformed
TOML, non-scalar resolution) were all refuted — that logic was correct;
only the CLI plumbing around it was missing. See A-297 in `decisions.md` for
full citations, and **B022** for non-blocking hardening items the same review
found (no dangerous-ambient-env denylist, a passthrough/infrastructure
collision that only the loader — not the runtime — currently refuses). **A
narrower residual gap found while wiring this fix, filed separately as
B025:** an unresolvable infrastructure declaration now refuses cleanly (no
crash) but writes no verdict artifact even when `--verdict-json` is reserved,
because `refuse_lane`'s own internal plan-resolution snapshot hits the
identical failure and cannot be used here without a verdict-shape decision.

### The observation

A dstdns SQL/DDL R2 lane provisions a disposable PostgreSQL server and therefore needs three runtime facts: Docker network name, governed cgroup parent, and the PostgreSQL image used by the deployed stack. Under `snapshot_selection = "repository"`, assay materializes a private snapshot containing committed source only; rendered CIU state such as `ciu.global.toml` is deliberately absent. The lane command cannot read caller/runtime state from inside the snapshot without violating isolation, and cannot invent defaults without violating fail-fast policy. Minimal reproduction: archive HEAD into `/tmp/sql-mutation-lane-repro`, init git, run the wrapper — it fails with `FileNotFoundError` for `ciu.global.toml`.

### Required contract

Allow lanes to declare required infrastructure inputs, resolved OUTSIDE the snapshot by assay/run-gate before command execution:

```toml
[lanes.sql_example.infrastructure]
network = "derived:ciu.deploy.network_name"
cgroup_parent = "required-env:CGROUP_PARENT_DEV_BACKGROUND"
postgres_image = "derived:ciu.service.infra.db_core.postgres.image"
```

Rules:
- resolution happens in the invoking/consumer context, never inside the snapshot;
- empty/unresolvable values fail loudly;
- resolved values are injected into the isolated command as environment variables;
- snapshots remain free of runtime state;
- alternative acceptable solution: a native SQL mutation executor owning disposable database lifecycle/snapshotting.

### Related consumer-side requirements already implemented in dstdns

Disposable server lease/fact files, owner-checked cleanup on exit/interrupt, per-invocation database naming/roles, version-correct readiness probing. These should inform helper-level support so consumers stop reinventing incompatible cleanup contracts.

### Acceptance

- [x] declared infrastructure inputs resolve before snapshot execution;
- [x] missing/unresolvable inputs refuse loudly with named keys;
- [x] isolated commands receive only injected values, no host paths;
- [x] SQL lanes can complete full mutant scope without reading caller state.

## B014 — persist bounded subprocess stdout/stderr in verdicts on COMMAND_FAILED

**Filed 2026-08-23 (dstdns repair program; consumer evidence from CIU v7 proposal §10.1 and P121/P127 debugging sessions).**
**Status:** **IMPLEMENTED 2026-08-24.** `CommandResult` captures bounded
stdout/stderr tails and dropped-byte counts; failed and timed-out verdicts carry
them under optional v7 top-level result fields; PASS and pre-command refusals
omit the contract.

**Update 2026-08-24 (dstdns P128 evidence).** B013's scope is wider than SQL
mutation lanes. Any R1 lane whose wrapper provisions disposable infrastructure
(here: a schema-lineage lane spawning a throwaway PostgreSQL and a sibling
runner container via `docker exec`) hits the same wall — the wrapper runs
inside the snapshot, but the *tests* run in the sibling runner against the
REAL worktree (via bind mount), so the snapshot is never exercised. Coverage is
written to the real tree; the snapshot stays empty of measurement output;
assay then reports `GIT_FAILED` (dirty real tree) or `EMPTY_COVERAGE`. The
B013 contract must cover "wrapper provisions infra in sibling containers that
bind-mount the caller's tree" as a first-class case, or document explicitly
that such lanes cannot use `snapshot_selection = "repository"` and must use
R0 + external coverage attestation instead.

### The observation

When a lane command exits non-zero, the verdict records `FAIL/COMMAND_FAILED` with argv, commit, and timing — but zero command output. During dstdns debugging, a SQL lane failed before its first mutant for an environment-variable forwarding bug; diagnosing it required five manual reproductions because the verdict could not say *why* the command failed. `default_process_runner` already uses `capture_output=True`, so the bytes exist at the failure boundary; they are simply discarded.

### Required behavior

1. `CommandResult` gains optional bounded fields: `stdout_tail`, `stderr_tail` (each ≤64 KiB decoded lossily with replacement characters; dropped-byte counts recorded), populated for every non-PASS outcome and retained on PASS as empty or consumer-opt-in.
2. `assemble_verdict` persists them into the verdict artifact under `result.stdout_tail` / `result.stderr_tail`.
3. Truncation is head-side (keep last N bytes) so final error lines survive.
4. No change to exit codes, outcomes, or reason codes.

### Acceptance

- [x] failing lane verdict contains both tails;
- [x] oversized output truncated head-side with recorded dropped-byte counts;
- [x] PASS verdicts remain unchanged unless opted in;
- [x] existing consumers reading verdicts tolerate the new optional keys.

## B015 — UUID/equality/enum-aware Python mutation operators

**Filed 2026-08-24 from the post-`assay-v2.2.0` release review.**
**Status:** **WITHDRAWN 2026-08-26 (A-326).** Previously marked
`IMPLEMENTED 2026-08-24` as two bounded families,
`python:uuid-equality-swap` and `python:enum-comparison-swap`. That status was
wrong: the 2.1.0→2.3.0 review-gap audit measured the two families to produce a
byte-identical SUBSET of `python:compare-swap`'s own output (87 sites over
`src/assay/**.py`, zero new), co-selection emitted every shared site twice, and
the enum predicate matched any `name.attr` access rather than an enum member.
The one acceptance box that would have caught this — "a real R2 lane
demonstrates kills attributable to each admitted family" — is the one that was
left unchecked while the item was marked IMPLEMENTED. Both operators are
withdrawn from the producer and from lane declaration in the next release; the two names
remain spellable in a schema-v7 artifact until the next schema-version bump so
that verdicts already emitted by 2.3.0/2.4.x keep verifying (A-326). The ask
itself — operators that catch UUID/enum-specific defects `compare-swap` cannot
— is NOT satisfied and is not carried forward: A-326 records why no such
operator is expressible under A-112's reuse-only rule without new,
unmeasured mutation-testing design. See B034.

**Scope boundary:** `budget_per_candidate` (shipped in `assay-v2.2.0`, see B012)
partially mitigates the other P127 blocker — a hanging comparison mutant — by
marking that candidate `budget_exceeded` and continuing. It does **not** help
B015 at all: a bounded timeout says nothing about whether UUID or enum semantics
were actually mutated. These remain independent upstream deliverables.

### The gap

The released Python mutation catalogue is exactly the four qualified members in
`assay/src/assay/vocabulary.py`: `python:compare-swap`, `python:boolop-swap`,
`python:bool-const-flip` and `python:falsy-swap`. The packaged verdict schema
closes the same four names, and `PythonAdapter` implements only their AST site
rules. It has no operator that deliberately perturbs UUID identity/equality
boundaries, broader equality semantics, or enum comparison semantics.

The nearest current behavior is deliberately narrow:

- `compare-swap` already swaps `==`/`!=` and `is`/`is not`, but it is a generic
  token-level swap with no knowledge that an operand is a UUID, enum member or
  other semantic identity.
- `falsy-swap` covers direct falsy returns (`None`, zero, empty bytes,
  empty collections and empty mappings), not object equality.

This is a release-boundary fact, not an implementation oversight discovered
after publication: **B015 is not yet in `assay-v2.2.0`.** The deferred R2 debt
from dstdns P126 therefore remains deferred until these operator families are
designed, bounded and qualified like the existing catalogue.

### Candidate operator families

The eventual catalogue may need more than one closed name; this filing does not
freeze names. At minimum, separate these concerns so each has a measurable site
rule and a distinct failure cause:

1. **UUID-aware equality/boundary mutation.** Recognize expressions whose type
   or construction is a UUID (including likely constructor calls and typed
   parameters where safely derivable) and mutate only exact token boundaries
   such as `==`/`!=`; do not reinterpret UUIDs as strings or invent format
   mutations without measured value.
2. **Enum-aware comparison mutation.** Recognize enum-member access/comparison
  sites and mutate only comparison tokens or member references whose replacement
  remains parseable and semantically distinct. Do not swap unrelated enum types.
3. **General semantic equality.** Only add a third family if its sites are not
  already covered by `compare-swap` and it produces kills that generic swapping
  cannot attribute.

Names, eligibility rules, and whether family 3 earns admission must be decided
by carve evidence. A broad “UUID/enum mode” hidden inside existing operators
would blur attribution and violate the catalogue’s one-site/one-operator
discipline.

### Contract constraints inherited from v6/P33

- Operator names remain language-qualified under the closed `python:*`
  vocabulary.
- Adding names is additive to the schema’s per-language `oneOf`, but adapter
  generation, model vocabulary, docs tests, and verifier agreement must move
  together.
- Every generated mutant remains a valid single-site byte splice at the recorded
  commit; discovery must not replace whole expressions when the catalogue means
  an operator/value token.
- Sites outside the changed-line scope remain out of bounds for R2.

### Required before dispatch

- Define each operator family's exact AST sites and replacement grammar.
- Keep the public `python:*` vocabulary closed and schema/model-consistent.
- Prove generated mutants parse and produce distinct observable behavior.
- Qualify against real consumer code where UUID/equality/enum semantics decide
  test outcomes, not only synthetic AST fixtures.
- Decide explicitly whether any proposed site overlaps `compare-swap` enough to
  be indistinguishable evidence; reject or split accordingly.
- Preserve deterministic candidate IDs across discovery runs.
- Update README, DESIGN-GUIDE and CONSUMERS.md in the same work item.

### Acceptance

- [x] new operators register through the existing adapter protocol;
- [x] their vocabulary is documented and schema-enforced;
- [x] generated mutants are valid single-site Python programs;
- [x] synthetic fixtures prove each eligible/ineligible AST boundary —
      **correction 2026-08-25 (review-gap audit): false.** The shipped suite
      never tests an ordinary attribute comparison (`cfg.debug == True`) —
      the entire false-positive class the "eligible/ineligible boundary" is
      supposed to cover. `_is_enum_member_expression` matches any dotted
      attribute access, not enum members;
- [ ] a real R2 lane demonstrates kills attributable to each admitted family
      — **still unmet, and the reason why: both operators produce a strict,
      byte-identical subset of `compare-swap`'s own sites (87 measured, zero
      exceptions), so no kill can be attributed to either family alone.**
      This is the exact overlap this item's own "Required before dispatch"
      section asked to be decided explicitly before shipping, and was not.
      Filed as the fix-needed successor, **B034**, which also covers
      double-counted co-selected sites;
- [ ] P126's deferred debt is re-evaluated and its disposition recorded.

## B016 — repository snapshot omits committed source files when `__pycache__` exists in the tree

**Filed 2026-08-24 (dstdns P128 R1 blocker; consumer evidence from P128 debugging session).**
**Status:** **NOT REPRODUCIBLE at current HEAD; hardened 2026-08-25, corrected
2026-08-25.** A literal tracked-source/untracked-sibling-cache fixture passes,
and the existing index-tree/clean-status verification already rejects the
described omission. The required post-materialization manifest-presence check
is now explicit, so a future writer regression fails closed even if Git
status were unable to observe it. **The check as first shipped (self-reviewed
only) used `Path.is_file()`, which follows symlinks — a regular-file entry
materialized as a symlink to a byte-identical file would have satisfied it,
the opposite of the "must exist as a regular file" contract it documents. An
independent adversarial review (round 1, same day) found this, along with the
fact that neither the shipped test nor deleting the whole check changed the
test suite's result (0 failures either way) — the check had no demonstrated
reachable failure mode.** This remediation fixes the predicate (`lstat`/
`S_ISREG`, matching every other symlink-vs-regular-file check in this module)
and extracts it into a standalone `_prove_manifest_materialized` function so
it is directly unit-testable without first defeating the stronger
Git-consistency checks that run before it — which, per the same review, is
structurally guaranteed to always happen first in the current architecture,
making this check permanently a defence-in-depth measure rather than a
reachable one, kept for the reason A-291 gives. See A-295 in `decisions.md`.
The historical dstdns end-to-end acceptance is obsolete as proof of the filed
defect; re-run that consumer lane against its current dependency if the
symptom recurs.

### The observation

Under `snapshot_selection = "repository"`, assay 2.3.0 materializes a snapshot
that contains `__pycache__/*.pyc` entries but omits the corresponding tracked
`.py` source files. Reproduction: any commit where `libs/common/src/common/`
contains both tracked `.py` sources and untracked-but-present `__pycache__`
directories. After `prepare_snapshot(...).materialize()`, the snapshot's
`libs/common/src/common/` holds only `__pycache__/` and no `.py` leaves. The
blob objects are present and readable via `git cat-file`; the tree walk finds
them; but `_write_worktree` never writes them.

### Suspected cause

`_write_worktree` builds its write set from `manifest.entries`. When both a
directory entry (tree) and a file entry (blob) exist at overlapping paths — or
when `__pycache__` directories appear as untracked-in-source but
tracked-in-snapshot content — the manifest's `directories` tuple records the
parent, but the blob `entries` for sibling `.py` files are dropped during
materialization rather than written.

### Required contract

- Every tracked regular-file leaf in the commit MUST be materialized in the
  snapshot worktree, regardless of whether sibling `__pycache__` directories
  also appear.
- A post-materialization verification must assert: for every `_Entry` in the
  manifest, the corresponding path exists on disk.

### Acceptance

- [ ] a regression test reproduces the omission with a fixture containing
      both `.py` sources and `__pycache__` siblings;
- [ ] all manifest entries are materialized after the fix;
- [ ] dstdns P128 R1 lane passes end-to-end against the fixed build.
- [x] a sibling-cache fixture proves tracked sources remain materialized;
- [x] every non-omitted manifest leaf is explicitly verified on disk before yield.

---

## B017: Assay dirty-tree check ignores committed .gitignore for coverage artifacts

**Status:** **NOT REPRODUCIBLE; REVERTED 2026-08-25 (A-290).** Item 2 below does
not reproduce: measured on a repository with a **committed** `.gitignore`
containing `/.assay/`, `dirty_paths()` returns the identical (empty) result
under both `--exclude-per-directory=.gitignore` and `--exclude-standard` — a
committed per-directory ignore was already fully honored under A-177, so
switching flags fixed nothing. An initial attempt shipped `--exclude-standard`
anyway (2026-08-25, self-reviewed); an independent adversarial review found it
introduced a real regression instead — `--exclude-standard` also honors
`.git/info/exclude`, a repository-local file reported by nothing else, letting
a personal ignore rule there hide a brand-new untracked source file with no
self-reporting trail, and it broke the pre-existing regression test
`test_git_info_exclude_cannot_hide_untracked_dirt`. Reverted; A-177's
`--exclude-per-directory=.gitignore` stands. If a real reproduction of items 2
or 3 below turns up, it needs a repro attached before any further attempt.

**Filed by:** dstdns controller (2026-08-25)

**Problem.** `assay/git.py:dirty_paths()` uses
`git ls-files --others --exclude-per-directory=.gitignore` instead of
`--exclude-standard`. This means:

1. Only **committed** `.gitignore` files are honored — not `.git/info/exclude`,
   global config, or `$GIT_CONFIG_GLOBAL`.
2. A blanket pattern like `/.assay/` correctly hides measurement output from
   git, BUT the same pattern also hides it from `dirty_paths()`, which means
   assay's own dirty-tree check cannot distinguish "coverage artifact written"
   from "uncommitted source change".
3. Consumers are forced to use narrow per-artifact-type patterns
   (`/.assay/verdict-*`, `/assay/coverage-*`) instead of a simple directory
   ignore, which is fragile and requires updating every time a new artifact
   type is added.

**Suggested fix.** Use `--exclude-standard` in `dirty_paths()` so all standard
exclusion sources (`.gitignore`, `.git/info/exclude`, global config) are
honored. The security rationale for the narrower flag (A-177) should be
revisited: the threat model (a hostile repo adding `*` to its own `.gitignore`
to hide changes) is better addressed by checking whether any **tracked** file
is missing from the working tree, rather than by restricting which exclusion
sources git consults.

**Impact.** dstdns had to split one clean pattern into four narrow patterns,
and still hit issues when new artifact types appeared.

**New reproduction, 2026-08-25 (dstdns P132 implementer, priority evidence —
different mechanism from the reverted flag-switch above, not a re-request of
it.** `assay`/`assay-dlq` fail `NO_MEASUREMENT`/`DIRTY_TREE` on a git tree a
plain `git status --short`/`--porcelain` correctly reports as clean, on
EVERY ciu-registered git worktree tested (dstdns P130 and P132's worktrees
both, independently). Root-caused with a direct Python probe calling
`assay.git.dirty_paths()` in-process: the dirty file is
`ciu.worktree-instance.json`, a CIU-created worktree-registration metadata
file present in every `ciu worktree create`d instance, excluded ONLY via
`.git/info/exclude` (untracked, per-clone) — never via the repo's committed
`.gitignore`. Confirmed deterministic and reproduced twice: `mv` the file out
→ `dirty_paths()` returns `()` immediately; `mv` it back → `DIRTY_TREE`
reappears immediately, in the same shell/cwd/env where plain `git status`
sees nothing throughout.

**This is NOT simply "B017's flag issue again."** `dirty_paths()`'s
`--exclude-per-directory=.gitignore` vs `--exclude-standard` choice (the
already-reverted fix above) is orthogonal to what's actually failing here.
`git.py`'s `_resolve_repo()` (`assay/git.py:390-429`) explicitly anchors
subsequent commands with `--git-dir=<resolved>` where `<resolved>` comes
from `git rev-parse --absolute-git-dir` run at the worktree — for a LINKED
worktree this resolves to the per-worktree private dir
(`<main-repo>/.git/worktrees/<name>/`), not the shared common `.git/info/`
location `info/exclude` actually lives in for a linked worktree setup. The
hypothesis (not yet source-confirmed against `dirty_paths()`'s own git
invocation — worth checking directly): explicit `--git-dir=` anchored at the
private per-worktree dir may not resolve `info/exclude` the same way a
normal auto-discovering `-C <worktree-path>` invocation does, INDEPENDENT of
which exclude flag is passed. If confirmed, this is a linked-worktree-
specific bug in how `_resolve_repo`/its callers anchor git invocations, not
a flag choice — and would need its own fix distinct from (and compatible
with) the security posture that reverted the flag-switch attempt above.

**Impact, worth restating plainly:** this silently converts assay's
dirty-tree gate into a false-positive refusal for every worktree-based
package in dstdns's pipeline — every package currently gets ZERO real
`assay`/`assay-dlq` R0/R1 evidence unless the operator happens to discover
and apply the `mv`-out/`mv`-back workaround by hand (verified working,
documented in `dstdns/nyxloom-trove/reports/dstdns-P132-REPORT.md`).

**Suggested immediate mitigation, orthogonal to the deeper git-dir question:**
CIU could track `ciu.worktree-instance.json` via the repo's COMMITTED
`.gitignore` instead of `.git/info/exclude` — that would make it visible to
`dirty_paths()`'s current, unreverted `--exclude-per-directory=.gitignore`
flag without touching assay's own git-invocation code at all. Filed as a
cross-repo note; CIU owns whether to act on it (dstdns provenance below).

**RESOLVED same day, 2026-08-25 — the git-dir hypothesis is REFUTED and no
assay-side change is needed; the mitigation above is already applied on
dstdns's side.** Directly measured with a from-scratch linked-worktree
repro: `git ls-files --others --exclude-per-directory=.gitignore` (both
auto-discovering `-C <path>` AND explicit `--git-dir=<resolved>
--work-tree=<repo_top>`, matching `_resolve_repo`'s own invocation shape
exactly) return **identical** results in every combination tested — a file
excluded only via `.git/info/exclude` is NOT hidden by either invocation
style, in a linked worktree OR a plain non-worktree checkout. There is no
worktree-specific git-dir-anchoring effect; `_resolve_repo`'s explicit
`--git-dir=`/`--work-tree=` does not change `info/exclude` resolution at
all, confirmed against the real `assay.git.dirty_paths()` function itself,
not just raw git output. The symptom is exactly A-177's documented,
deliberate design: `--exclude-per-directory=.gitignore` never consults
`.git/info/exclude`, on purpose (a personal/local ignore rule must not hide
anything from the dirty-tree check). It reads as "worktree-specific" only
because `ciu.worktree-instance.json` happens to exist solely in worktree
contexts, not because worktree topology changes assay's behavior.

The suggested mitigation was independently verified (a committed
`.gitignore` entry for the exact filename correctly makes
`dirty_paths()` report clean, confirmed against the real function) — **and
dstdns already shipped it the same day**: `dstdns@08b789f5` "fix(gitignore):
track ciu.worktree-instance.json ignore (was local-only)" adds exactly this
line to dstdns's committed `.gitignore`. No further action needed here;
closing this reproduction as resolved upstream. If the symptom recurs,
re-verify against dstdns's current `.gitignore` before re-opening — the most
likely cause of a recurrence is a stale/different commit or branch, not a
regression in this mechanism.

**Provenance:** `dstdns/nyxloom-trove/reports/dstdns-P132-REPORT.md`,
`dstdns/nyxloom-trove/reviews/dstdns-P130-code-review-phase1-r1.md` §B1
(the black-box symptom, found first, from a different worktree),
`dstdns/nyxloom-trove/decisions.md` D-193, `dstdns@08b789f5` (the shipped fix).

**Recurrence 2026-08-29 (dstdns P138 code review), priority evidence — same
mechanism, second filename, same one-line fix.** `run-gate assay`/`assay-dlq`
false-refused `NO_MEASUREMENT/DIRTY_TREE` on `ciu.global.worktree.toml.j2`, a
different CIU-generated per-worktree render input than `ciu.worktree-instance.json`
above, same untracked-outside-committed-`.gitignore` shape. Confirmed the file
is genuinely absent from `.gitignore` before fixing. Fixed identically:
`dstdns@5c8c14c6` adds the line to dstdns's committed `.gitignore`. No new
mechanism, no assay-side change needed — filed here as the anticipated
recurrence this entry's own closing note called for ("if the symptom recurs,
re-verify... most likely cause is a stale/different commit, not a
regression"). Worth CIU's own attention (not filed there, dstdns provenance
only): every new CIU-generated per-worktree render-input filename repeats
this exact gap until it's added to the ignore file by hand, one file at a
time, after someone hits it.

**Recurrence 3, 2026-08-30 (assay B018/B019/B035 wave, in VBPUB'S OWN
worktree) — the first occurrence outside dstdns, and it reds assay's own
registered gate.** `tools/tester-unified-gate.sh`'s self-hosted lane refused
`NO_MEASUREMENT/DIRTY_TREE` in
`/workspaces/vbpub/.worktrees/assay-v8-synergy-wave` with a tree that
`git status --porcelain` reported as completely clean — including the gate's
own newly added post-lane status diagnostic, which printed nothing. The file
is `ciu.worktree-instance.json`, i.e. **recurrence 1's original filename**,
excluded by `/workspaces/vbpub/.git/info/exclude:18` (`/ciu.worktree-instance.json`)
and by nothing committed; `git check-ignore -v` names that source directly,
and `git ls-files --others --exclude-per-directory=.gitignore` — assay's own
query — lists it. `vbpub/.gitignore` does carry `ciu.env` (line 163) but not
this one.

Two things this recurrence establishes that the first two did not:

1. **The fix is per-REPOSITORY, and vbpub never got it.** `dstdns@08b789f5`
   and `dstdns@5c8c14c6` fixed dstdns's committed `.gitignore`. vbpub's was
   never touched, so the identical gap sat unnoticed here until a wave
   happened to run the registered gate from a ciu-created worktree. **Both**
   filenames are present and unignored in this worktree, and the second is in
   a worse state than the first: `ciu.global.worktree.toml.j2` (recurrence
   2's filename) is excluded by NOTHING — not the committed `.gitignore`, not
   `.git/info/exclude` — so it shows as a plain `??` in ordinary `git status`,
   where `ciu.worktree-instance.json` stays hidden.

   **CORRECTED after round-1 review (m1).** This paragraph originally went on
   to say the `.j2` "reds the gate's own pre-flight (`assay has uncommitted
   changes`) before assay is ever reached". That is **wrong**, and it was a
   reconstruction rather than a measurement. The pre-flight is pathspec-limited
   — `git -C "$worktree" status --porcelain=v1 -- assay` — and the `.j2` sits
   at the worktree ROOT, outside `assay/`, so it never reaches it. Re-measured
   with the file restored:

   ```
   plain status:                 ?? ciu.global.worktree.toml.j2
   pre-flight query (-- assay):  (empty)      <- NOT tripped
   assay's own dirty query:      ciu.global.worktree.toml.j2
   ```

   The pre-flight failure actually seen during this wave came from an
   uncommitted edit to `tools/tester-unified-gate.sh`, which IS under `assay/`,
   and was misattributed to this file. **Both files red the LANE via
   `git.dirty_paths`; neither reaches the shell pre-flight.** The operational
   conclusion (move both aside) is unchanged. The diagnosis is not, and a
   recurrence entry whose entire value is an accurate diagnosis must not carry
   a plausible-sounding wrong one into a fourth occurrence.

   The two-line fix (both names in
   vbpub's committed `.gitignore`, alongside the `ciu.env` line already
   there) is NOT taken in this wave: `.gitignore` at the vbpub root is
   outside this branch's assay subtree and belongs to whoever owns the estate
   root, and the brief scoping this wave excludes it. Flagged for the
   reviewer/controller as a two-line follow-up rather than done unilaterally.
2. **This entry's own "if the symptom recurs, re-verify against the current
   `.gitignore`" instruction is now three-for-three**, and each time the
   answer has been the same missing line. The per-file, after-someone-hits-it
   loop that recurrence 2 flagged for CIU's attention is not hypothetical
   anymore — it has now cost three separate investigations across two repos,
   the third of which presented as an unexplained gate failure in a wave that
   had nothing to do with git ignore rules.

Workaround used to get a real gate run in this wave (recurrence 1's, verified
again): move the file out, run the gate, move it back. Assay-side behaviour is
correct and unchanged — A-177's refusal to honour `.git/info/exclude` is the
whole point, and a fix that made assay honour it would reopen the hole that
rule exists to close.

**RESOLVED 2026-08-30, same day, upstream and not by this wave.**
`vbpub@8caf1c24` "fix(vbpub): gitignore CIU per-worktree render artifacts
(B017, third recurrence)" adds BOTH `ciu.worktree-instance.json` and
`ciu.global.worktree.toml.j2` to vbpub's committed `.gitignore`, with a
comment naming this recurrence and the two dstdns precedents — i.e. the exact
two-line fix the note above flagged rather than took. vbpub now carries what
dstdns has carried since `dstdns@08b789f5`/`dstdns@5c8c14c6`. The assay branch
that found it (`feature/assay-b018-b019-b035-v8-synergy`) predates that commit
and therefore does not contain it; a merge in either direction picks it up.

Two refinements to the report above, recorded so the entry is not overstated:

* ~~**The files are TRANSIENT, not permanent dirt.** A ciu invocation observed
  at 01:38 regenerated this worktree's `ciu.env` and removed both render
  inputs outright.~~ **RETRACTED after round-2 review (R2-M3). No ciu ran.**
  That was the round-1 REVIEWER moving both files aside to get a gate run and
  restoring them afterwards — their review's own appendix says so in as many
  words, in a document already read by the time this bullet was written. The
  01:38 `ciu.env` mtime is their `cp` restoring the file, not a regeneration:
  the content is byte-identical to the original, which a regeneration would
  not be, and the file dates from worktree creation (23:07:10) otherwise.
  There is no independent evidence of a ciu process anywhere — the claim was
  inferred from one mtime.

  **This retraction matters because the claim was load-bearing, not
  decorative.** It converted a three-times-recurring, reproducible false
  `DIRTY_TREE` into an "intermittent" one and softened the warning to a future
  reader from *will* hit this to *may*. **Restore the strong reading: on a
  ciu-created worktree whose repository lacks the committed `.gitignore`
  entries, this reproduces every time.**
* **The mechanism itself was directly observed, twice**, and is untouched by
  the retraction above: `git check-ignore -v` named
  `/workspaces/vbpub/.git/info/exclude:18` as the only rule hiding the file,
  and `git ls-files --others --exclude-per-directory=.gitignore` — assay's own
  query — listed it while `git status --porcelain` reported nothing.

This entry's standing instruction is unchanged for a FOURTH occurrence: any new
CIU-generated per-worktree filename repeats the gap until it is added to the
committed ignore file. That is now three filenames across two repositories,
each found the same expensive way.

---

## B018 — CIU V8 preparation: judge provenance in every verdict

**Filed 2026-08-25 from `ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §11.3.**

### Required contract

Every verdict MUST record the producing judge identity, not merely an Assay
version string. Minimum fields are the resolved judge name, exact semantic
version, artifact digest algorithm (`sha256`), and lowercase artifact digest.
For a source-tree invocation these identify the built wheel/zipapp actually
used; a bare source checkout without an identifiable build artifact refuses
rather than inventing a digest.

CIU V8 depends on this to bind LaneResult evidence to the verified judge it
resolved from `[testing.judge]`; without it, central tool resolution can verify
a download but cannot prove which binary emitted the verdict.

**Status:** **FIXED 2026-08-30 (A-327, extended by A-332)**, on branch
`feature/assay-b018-b019-b035-v8-synergy`, unmerged and unreleased at the time
of writing — it ships in the next release, and this line deliberately names no
version until one exists. `judge_provenance` is an optional top-level verdict
object that is **absent-or-complete, never partial**; `assay run
--require-judge-provenance` turns an unidentifiable invocation into an
`ERROR`/`BAD_LANE_CONFIG` refusal before any work runs. The identification
logic was designed against four real invocations (wheel install, zipapp,
bare source checkout, and an installed distribution shadowed by a source tree
on `sys.path`), not against a reading of the `importlib.metadata` docs — the
shadow guard exists because measurement found that a resolvable distribution
does not prove the running code came from it.

### Acceptance

- [x] verdict schema/model records judge name, version, digest algorithm, and
      digest — plus `artifact` (`wheel`/`zipapp`), which names *which* release
      file the digest is of; all five required when the object is present.
      Registered in all three layers in one commit (model `verdict.py:1406`,
      raw verifier `verify.py:1059`/`:1327`, packaged schema `$defs`), closing
      the A-323 class where a field lived in two layers for a whole release;
- [x] distribution invocation records the installed artifact's actual SHA-256
      — proven three ways, two outside pytest: against a built wheel
      (`test_standalone.py`), against a built `.pyz`
      (`test_distribution_build_release.py`), and by the registered gate
      itself, which `sha256sum`s the wheel host-side and compares
      (`ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel`);
- [x] unidentifiable invocation fails loudly rather than emitting a partial
      identity — the field is omitted entirely (A-051 "omitted, never null"),
      the reason goes to the diagnostics stream, and
      `--require-judge-provenance` makes it fatal for callers that require
      attributable evidence. Every refusal path is covered explicitly, not
      just the happy one;
- [~] existing v7 consumers tolerate the optional fields before V8 requires
      them — **NOT MET, and it cannot be, because B035's hard cut ships in the
      same release.** The field is genuinely optional and additive in v7 terms,
      which is what made it safe to add without a bump of its own; but a v7
      consumer handed one of these verdicts refuses the whole document on
      `schema_version` (A-170) long before it reaches `judge_provenance`. This
      criterion assumed B018 would land in a v7 release ahead of the V8 cut,
      and the wave batched them together instead. Annotated rather than ticked
      (see the wave report §2 and the review's N1); the consumer-facing
      consequence is documented in `docs/CONSUMERS.md`.

## B019 — CIU V8 preparation: gate-request-supplied comparison base

**Filed 2026-08-25 from proposal §10.10 and dstdns P128 evidence.**

### Required contract

A lane may declare that changed-line judging is required while delegating the
actual base identity to the invoking gate request, instead of hardcoding
`judge.base` per worktree/branch workflow. The request supplies either a ref or
an already-resolved full commit; Assay still performs merge-base resolution and
records the effective base in the verdict. A missing request base is a
configuration refusal, never a fallback to HEAD or another invented value.

This keeps static lane policy portable across branches/worktrees while letting
CIU V8 own branch-aware orchestration and execution manifests.

**Status:** **FIXED 2026-08-30 (A-328)**, on branch
`feature/assay-b018-b019-b035-v8-synergy`, unmerged and unreleased at the time
of writing — it ships in the next release, and this line deliberately names no
version until one exists. A lane declares `judge.base_source = "request"`; the
invoking gate supplies `assay run|plan --request-base REF`. The design decision
worth reading is that **precedence was refused rather than picked**: with both
present, whichever side lost would be config nothing reads, which is A-062's
named defect class, so declaring both is refused at load. Validated against
CIU §10.10 as written, and dstdns's five `judge.base` lanes migrate by deleting
one line. The one estate lane that pays a real cost is `ciu/assay.toml:49`
(`base = "origin/main"`) — see the migration note in `docs/CONSUMERS.md`.

### Acceptance

- [x] lanes can distinguish declared local base versus requested base policy —
      `judge.base_source`, a closed two-value vocabulary
      (`{"declared", "request"}`, default `"declared"`), `config.py:243`;
- [x] request-provided refs resolve through the same merge-base contract as
      `judge.base` — it is a second *source* for one argument, not a second
      code path: both land in `runner._resolve_declared_base` and record
      `base_resolution` identically. Verified end-to-end by the reviewer
      against a real two-commit repo through the installed wheel;
- [x] absent request base for a required changed-line lane refuses loudly —
      one of three named `LaneConfigError` → `ERROR`/`BAD_LANE_CONFIG`
      refusals, all resolved in `runner.resolve_base_declaration`
      (`runner.py:2018`) and called once above the dispatch, so `run` and
      `plan` cannot drift in what they accept. No fallback to `HEAD` or a
      default branch (A-018, at the request boundary);
- [x] verdicts continue to record the effective resolved base exactly once —
      `judgment.resolved.base`, unchanged in shape; the request-supplied value
      reaches it through the same single write.

## B020 — CIU V8 preparation: SQL mutation template/reset hooks (design first)

**Filed 2026-08-25 from proposal §10.2.**

### Scope boundary

Assay must not become a database provisioner. The ask is a narrow declaration
and lifecycle hook so a CIU-owned prepare step can publish a verified template
or reset strategy and each isolated mutant can consume its own clone without
re-applying schema. Native executor ownership remains out of scope unless a
future decision explicitly reverses it.

### Required design decisions before implementation

- template artifact identity and verification contract;
- per-mutant clone naming/isolation and cleanup ownership;
- savepoint/container reset strategies as declared contracts, not implicit
  behavior;
- failure semantics when a prepare artifact is stale, unverifiable, or already
  consumed;
- relationship to B013 infrastructure facts and existing equivalence artifacts.

### Acceptance

- [ ] written design decision reviewed against snapshot isolation and budget
      rules;
- [ ] no implementation lands until the above five decisions are recorded.

---

## B021 — mutation resume: stale-record disposition is inverted

**Filed 2026-08-25 from an independent adversarial review of B012 (round 1).**

### Problem

`_load_validated_state_record` (`mutation.py`) validates a persisted
`.assay/mutation-state/<candidate_id>.json` record against six required keys.
A mismatched `source_sha256` is treated as an absent record and silently
re-executed (`return None`). Every other mismatched key — `schema_version`,
`candidate_id`, `path`, `operator`, `replacement_sha256` — raises
`MutationStateError`, which fails the **entire lane** as
`ERROR`/`UNREADABLE_ARTIFACT`, not just the one candidate.

This is backwards for the two cases that actually matter in practice:

1. **`source_sha256` reaching the stale branch at all requires the record to
   already contradict its own filename** — the candidate id (and therefore
   the record's path) is itself derived from `path ‖ sha256(source) ‖
   start_byte ‖ end_byte ‖ sha256(replacement) ‖ operator`, so a genuine
   source edit produces a different id and a different path; resume simply
   finds no record there. A record whose `source_sha256` disagrees with its
   own filename is evidence of a corrupted or hand-edited state file, and
   silently re-running it discards that evidence rather than surfacing it.
2. **`schema_version` is the one required key NOT folded into the candidate
   id**, so it is the one field that can legitimately differ without the
   record being corrupt — specifically, a routine bump of
   `MUTATION_STATE_SCHEMA_VERSION`. Today that bump fails every consumer's
   next `--resume` with a lane-wide `ERROR`/`UNREADABLE_ARTIFACT` until they
   manually delete `.assay/mutation-state/`, rather than treating old-format
   records as pending and re-running them like a cache miss.

### Suggested fix

Raise `MutationStateError` on a stale `source_sha256` (tampering evidence);
return `None` (treat as absent, silently rerun) on a stale `schema_version`
(routine upgrade). The other four keys are already correctly folded into the
candidate id and their current "raise" disposition needs no change.

### Acceptance

- [x] a tampered `source_sha256` on an otherwise-valid record raises
      `MutationStateError`, not a silent rerun;
- [x] a `schema_version` mismatch treats the record as absent and reruns the
      candidate, without failing the lane;
- [x] `decisions.md`/`docs/CONSUMERS.md`/`docs/DESIGN-GUIDE.md` are updated
      to state the corrected disposition. **Narrowed from the original
      filing, which also named `README.md`** (flagged, not silently
      dropped, by round 2 review of this wave): `README.md`'s mutation
      section is high-level enough to have never asserted the wrong
      disposition, so there was nothing there to correct — checked
      separately, not assumed.

**Status: RESOLVED 2026-08-25 (stabilization wave).** See A-302.

---

## B022 — B013 infrastructure injection: hardening items found by adversarial review, none blocking

**Filed 2026-08-25 from an independent adversarial review of B013 (round 1).**
None of these are exploitable beyond what a trusted, committed lane file can
already reach via `env_passthrough`; they are filed for hardening, not
because B013 is unsafe as shipped.

1. **No dangerous-ambient-name denylist.** The collision check
   (`config.py`, `_check_infrastructure_declarations` or equivalent) only
   refuses a name already declared in `env` or `env_passthrough` — there is
   no refusal for a lane declaring an infrastructure fact named e.g.
   `LD_PRELOAD` or `PYTHONPATH`. A trusted lane file could already reach the
   same injection via `env_passthrough`, so this widens *where* a value can
   be sourced from (an external `required-env`/`derived` source instead of
   the reviewed lane file itself), not *whether* it can be injected.
2. **Passthrough can silently win a collision at runtime.**
   `resolve_command_plan` applies the `env_passthrough` loop after the
   infrastructure loop and unconditionally overwrites `env_effective[name]`.
   This is unreachable today only because the loader
   (`config.py`) already refuses that name collision at load time; the
   runtime has no defence of its own, so a future loader change could
   silently reopen it. Suggested: make the runtime loop refuse an already-set
   name instead of overwriting.
3. **A TOML integer or boolean `derived:` value cannot be injected at all** —
   `resolve_command_plan` requires the resolved node to be a non-empty
   `str`, so a numeric CIU fact (a port, a limit) refuses rather than being
   stringified. Likely the right call (avoids silently coercing an unintended
   type into an env string), but undocumented; needs a doc line or an
   explicit decision either way.
4. **No bound on a resolved infrastructure value's length** —
   `MAX_INFRASTRUCTURE_FACTS` bounds the *count* of declared facts, not the
   byte length of any one resolved value. A multi-MB string surfacing from
   `ciu.global.toml` would only fail late, at `E2BIG` on exec.

### Acceptance

- [x] a decision recorded on whether to add a dangerous-ambient-name
      denylist, or explicitly accept the `env_passthrough`-equivalence
      argument above — decided: no denylist (A-303);
- [x] runtime passthrough-vs-infrastructure collision refuses instead of
      silently overwriting (A-303);
- [x] numeric/boolean `derived:` handling is either documented as refused or
      given an explicit, tested coercion — decided: stays refused, documented,
      not coerced (A-305);
- [x] a bound (or an explicit decision not to bound) resolved infrastructure
      value length — bounded at `MAX_INFRASTRUCTURE_VALUE_BYTES` (A-306).

**Status: RESOLVED 2026-08-25 (stabilization wave).** All four items closed;
see A-303/A-305/A-306.

---

## B023 — mutation shard merging has no producer or consumer

**Filed 2026-08-25 from an independent adversarial review of B012 (round 1).**

### Problem

`merge_mutation_shards` (`mutation.py`) is exported and its assignment-domain
proof is now correct (B021's sibling fix, round-1 remediation), but nothing
in the shipped CLI produces the shard-summary documents it expects
(`schema_version`, `lane`, `commit`, `shard_index`, `shard_count`,
`candidate_ids`) or consumes its output. `assay plan --shard`'s own output
keys do not match `_SHARD_REQUIRED_KEYS`. A consumer who actually shards a
long lane across workers — B012's stated motivating case — has no shipped way
to combine the resulting per-shard verdicts back into one merged evidence
set; they would have to hand-build the summary documents themselves against
undocumented internal key names.

### Suggested scope

A CLI surface (new subcommand or a `run`/`plan` flag) that emits a
shard-summary document alongside a sharded run's verdict, and a consumer
(subcommand or library function) that merges N summary documents through
`merge_mutation_shards` and reports the result. Also needs: what a caller
does with a merged candidate-id set (is there a further verdict-merge step,
or is the summary itself the deliverable?) — a design question, not just
plumbing.

**Update 2026-08-25 (round 2 review + round-3 fix):** the assignment-domain
check now also refuses a duplicate `(shard_index, shard_count)` pair across
documents (round-2 finding F — two documents claiming the same index used to
merge silently since `covered_pairs` was a plain set). One honest limit
remains and is **inherent to the function's signature, not fixable without a
producer**: a document truthfully reporting a strict subset of its own real
candidates (e.g. 1 of 6 actually assigned) is indistinguishable from a
document whose shard genuinely had only 1 candidate — `merge_mutation_shards`
never learns the full candidate universe for a shard, only what each document
claims, so "exact coverage" can only mean "every required index present
exactly once with internally-consistent candidates," never "every candidate
that shard actually owned." This is round 2's case C; note it here explicitly
rather than let a future round re-discover it as a defect.

### Acceptance

- [ ] `assay run --shard I/N` emits a shard-summary document `assay
      merge-shards` (or equivalent) can consume directly, no hand-building;
- [ ] a real dstdns-shaped multi-shard lane merges end-to-end through the
      shipped CLI, verified by driving it, not by reading the diff;
- [x] `merge_mutation_shards`'s remaining honest limits are documented above:
      it cannot detect a shard reporting a genuine subset of its own
      candidates (case C, inherent), and it cannot prove a candidate id came
      from a real plan rather than a well-formed fabrication absent a
      signed/attested plan artifact (case E, also inherent).

---

## B024 — wire pyflakes/ruff into the registered gate; sweep pre-existing findings first

**Filed 2026-08-25 from an independent adversarial review of B012/B013/B016/B017
(round 1).** `pyflakes src/assay/cli.py` found two unimported names
(`LaneConfigError`, `os`) that made every use of two shipped B012/B013 code
paths crash with `NameError` — a defect a one-second lint pass would have
caught before merge, and did not, because nothing in `tools/tester-unified-gate.sh`
runs a linter at all.

### Why this isn't wired in directly by the same remediation

A first full sweep of `src/assay/` (`python -m pyflakes src/assay/*.py`)
turns up roughly twenty pre-existing "undefined name" findings in
`canary.py` and `mutation.py` (annotation-only references to names like
`LanguageAdapter`, `CommandResult`, `SnapshotRepository`, `LaneDeadline`,
`ProcessRunner` that are never imported — harmless at runtime only because
`from __future__ import annotations` defers all annotation evaluation, but
still a real gap the moment anything calls `typing.get_type_hints` on them),
plus a couple of unused imports (`canary.py`'s `dataclasses.replace`,
`cli.py`'s `hashlib`), all pre-dating this wave (oldest from 2026-08-08).
Wiring an enforcing gate today would immediately fail on unrelated code.

### Suggested approach

Either (a) sweep and fix the pre-existing findings first, then wire pyflakes
(or ruff, which subsumes it) into `tester-unified-gate.sh` as a real phase, or
(b) wire the gate with a checked-in, dated baseline of the current findings
that only fails on anything NEW — a ratchet, not a rewrite. Either way, this
should land before the next wave of packages, not be deferred indefinitely:
H2 (this filing's own trigger) shipped through two full self-review cycles
undetected.

### Acceptance

- [ ] `tester-unified-gate.sh` runs a linter over `src/assay/` as a real
      phase, failing the gate on any finding not in an explicit, dated
      baseline (if the ratchet approach is chosen);
- [x] the pre-existing findings above are either fixed or explicitly listed
      in that baseline with a reason each stays open — **fixed, all of
      them**: `python -m pyflakes` over the whole `src/assay/` tree
      (recursively, not just the top-level `*.py` this filing's own sweep
      checked — one more finding turned up in `coverage_parsers/model.py`)
      is clean, 0 findings, as of 2026-08-25.

**Status: PARTIALLY RESOLVED 2026-08-25 (stabilization wave) — sweep done,
gate-wiring deferred, on purpose, as its own follow-up.** All 30 pre-existing
findings fixed (round 2 review independently recounted the parent commit and
confirmed 30 — the exact figure, not an approximation): four unused imports
(`dataclasses.replace` in `canary.py`, `hashlib` in `cli.py`,
`typing.TextIO` in `runner.py`, `types.MappingProxyType` in
`coverage_parsers/model.py`), one needless `f"..."` prefix with no placeholder
(`runner.py`), one genuinely dead local variable
(`per_candidate_timeout_positions` in `mutation.py`, assigned, never read or
written to again — deleted, not just silenced), and 24 annotation-only
undefined names in `canary.py`/`mutation.py` (real gaps for a type checker,
harmless at runtime only because `from __future__ import annotations` defers
evaluation) — resolved with real imports where safe
(`canary.py`→`.runner`/`.isolation`, no cycle) and `TYPE_CHECKING`-guarded
imports where not (`mutation.py`↔`.runner`/`.adapters.base` import each
other already, so a real import would be circular).

**Gate-wiring deliberately NOT done in this wave.** No project here already
depends on `pyflakes`/`ruff`, so wiring a lint phase into
`tester-unified-gate.sh` means adding the tool to the **shared**
`tester-unified` Docker image (`tester-unified/Dockerfile`'s dependency
closure is currently derived entirely from ciu/cmru/topos/nyxloom/
cgroup-profiler's own `pyproject.toml` extras — assay contributes nothing to
it today) and rebuilding + re-validating that image, which every project
gating through `tester-unified:local` depends on — a cross-project,
shared-infrastructure change, not a local one, and out of scope for a
same-day stabilization pass by this project's own risk posture. Filed as a
narrower, scoped follow-up: add `pyflakes` (zero dependencies itself, matching
assay's own purity bar) to the image, add a phase to
`tester-unified-gate.sh` running it over `$worktree/assay/src/assay`
(static AST analysis — needs no venv, no wheel build, can run early and
cheaply, before any of the wheel-isolation machinery), and confirm no other
consuming project's gate regresses. The sweep above means that phase would
pass clean on day one; nothing here blocks it from landing next.

---

## B025 — a refusal whose OWN cause is an unresolvable infrastructure declaration writes no verdict artifact

**Filed 2026-08-25 (round 1 remediation), rescoped 2026-08-25 after round 2
review + a round-3 partial fix narrowed it.** Not itself a crash — the
refusal is a clean, typed `AssayError` that reaches `main()`'s handler and
prints a one-line message — but unlike every other post-HEAD-resolution
refusal in `runner.py`, it writes **no** verdict artifact even when
`--verdict-json` was reserved.

### Problem

`refuse_lane` (A-036) always calls `resolve_command_plan` a second time
internally, to record the attempted plan in the refusal verdict. Before
B013, `resolve_command_plan` could never raise, so this was always safe. Now
it can: an unresolvable `[lanes.<name>.infrastructure]` declaration raises
`AssayError` from inside `resolve_command_plan`.

**Original filing understated the scope; corrected by round 2 review:** the
trigger is not "the caller is refusing *because of* infrastructure" — it is
"the lane declares a `derived:` fact at all". Any refusal on such a lane,
for any reason, re-triggers the identical resolution inside `refuse_lane`
and can crash. **Round-3 fix (2026-08-25): every `refuse_lane` call site in
`run_lane` now forwards its own `infrastructure_source`/
`infrastructure_environment` parameters** (five call sites; two — the
`dirty_paths` refusal and, now, all the others — previously only one did).
This closes the bug for the entire class of refusal that co-occurs with a
declared-but-*resolvable* infrastructure table — proven: `assay run --shard
9/2` on a lane with a real, readable `derived:` fact now writes a proper
`ERROR`/`BAD_LANE_CONFIG` verdict where it previously crashed uncaught.

**What remains, narrowed:** only the case where the infrastructure
declaration is *itself* what's unresolvable (missing `ciu.global.toml`, a
missing `required-env` var, a malformed dotted path) — there, `refuse_lane`'s
internal `resolve_command_plan` call hits the exact same failure a second
time and still raises uncaught, because there is no *other* infrastructure
state to substitute. Reproduced 2026-08-25 (round-3 verification): a lane
with a `derived:` fact and no `ciu.global.toml` present still writes no
verdict on a bad `--shard`, while the identical lane WITH the file present
now does.

### Why this needs a design decision, not a quick patch

A-036's own stated invariant is "the command's own `CommandPlan` is still
resolved and recorded... a refused run is not an unrecorded one." **Round 2
correction: `argv_effective` never needs omitting** — infrastructure facts
only ever write into `env_effective` (`resolve_command_plan`'s env-building
step); `argv_effective` is assembled independently and is always resolvable.
The remaining question is narrower than originally filed: can a refusal
verdict honestly omit only `env_effective` (or state it partially, minus the
unresolved names) when infrastructure itself is what failed? Candidate
resolutions:

1. Let a refusal verdict honestly omit `env_effective` alone when
   infrastructure resolution is what failed — a smaller schema change than
   originally filed (never touches `argv_effective`).
2. Have `refuse_lane` retry plan resolution treating the lane as if it
   declared no infrastructure table, so the refusal at least records the
   argv and the non-infrastructure env that *would* apply — arguably
   misleading (it is not what would actually have run).
3. Give infrastructure-declaration errors their own dedicated refusal helper
   that never attempts plan resolution, parallel to but distinct from
   `refuse_lane`.
4. ~~Forward the caller's already-resolved `infrastructure_source`/
   `infrastructure_environment` into `refuse_lane`~~ — **done, round 3, all
   call sites in both `runner.py` and `cli.py`** (a follow-up review pass on
   round 3's own fix found two more in `cli.py` the first pass missed — same
   fix, see A-299); resolves every case EXCEPT the one this entry is now
   scoped to (the source itself is the thing that's broken).

**Known gap, not yet closed:** `cli.py`'s attestation `LANE_TIMEOUT` refusal
(the OTHER of the two `cli.py` sites, alongside the adapter-refusal one the
new test exercises) is forwarded by inspection/symmetry but has **no test**
— reverting only that site leaves the full `test_cli_run.py` suite green.
Triggering it needs a real attestation-deadline timeout, not attempted in
any round. Close this alongside whichever acceptance item below lands next.

### Acceptance

- [x] every `refuse_lane` call site in `run_lane` (`runner.py`, 5 sites) AND
      in `_run_reserved` (`cli.py`, 2 sites) forwards
      `infrastructure_source`/`infrastructure_environment` (round 3, both
      passes — see A-298/A-299);
- [ ] the `cli.py` attestation-`LANE_TIMEOUT` forward gets its own test
      (currently unguarded — see "Known gap" above; still open, needs a real
      attestation-deadline timeout to trigger, not attempted this wave
      either — a known, accepted, narrow gap, not a regression);
- [x] a decision recorded on which of options 1-3 handles the remaining case
      (infrastructure itself unresolvable) — decided: a refined option 1
      (A-308) — `env_effective` becomes exactly `lane.env`, paired with a new
      additive `env_effective_incomplete: true` flag, rather than omitting
      `env_effective` outright (`LANE_RESOLVED_FIELDS`'s own "all present or
      all absent" contract rules that out);
- [x] `assay run` on a lane whose OWN infrastructure declaration is
      unresolvable writes a verdict artifact to a reserved `--verdict-json`
      — fixed at all FOUR crash sites found across two rounds (round 1,
      A-308: `_run_higher_rigor_lane`'s primary `resolve_command_plan` call,
      infra is the ONLY thing wrong; `refuse_lane`'s own second,
      recording-only call, infra co-occurs with an unrelated refusal cause
      — round 1's own "BOTH places" claim was wrong, round 2 review found
      two more: `run_lane`'s direct R0-only path, and the `environment_command`
      probe's plan resolution, which never even forwarded infrastructure
      params at all, unconditionally broken for a `derived:` fact regardless
      of resolvability). None gained a stderr message — all four join the
      same silent-on-stderr bucket the `--shard`/`--operators` refusals
      already occupy (B026 N-4), the existing, accepted asymmetry, not a
      new one;
- [x] a test drives this through the installed CLI (not `resolve_command_plan`
      directly) and asserts the artifact exists and is schema-valid — one
      test per crash site (four total, two per round), all verified
      red-first.

**Status: RESOLVED 2026-08-25 (stabilization wave, both rounds), except the
one known, narrow, pre-existing gap noted above (attestation-timeout
forward test) and the DIFFERENT, wider "lane-wide `LANE_TIMEOUT` also
writes no verdict" gap round 2 review found and filed separately as
B028 (same family, bigger blast radius, needs its own design pass).**
See A-308.

---

## B026 — a bad `--shard` refusal names no cause; `judge.mutation.shard_index`/`shard_count` are dead config

**Filed 2026-08-25 from round 2 review of the B012/B013/B016/B017
remediation (findings N-4, N-5).** Two small, unrelated diagnosability gaps
bundled here rather than as two one-line items.

### N-4 — the bad-`--shard` refusal carries no diagnostic

`assay run <lane> --shard 7/2` on a 2-shard lane now returns a clean
`ERROR`/`BAD_LANE_CONFIG` verdict (fixed, round 3 — see A-296/A-298) instead
of crashing, but nothing in the artifact or stdout says the word "shard": the
old code's `f"invalid mutation shard spelling {shard!r}"` message is gone
along with the exception it used to travel in, because `refuse_lane` (like
every other refusal in this module — `missing_required`, `dirty_tree`,
adapter resolution) carries no free-text field, only `(outcome,
reason_code)`.

**The sharper framing (round-3 review correction): this is not just a
`run`-vs-`plan` mismatch, it's *inside* `run` itself.** `run --operators
bogus:x` prints a message to stderr and writes **no** artifact (refused
before output reservation, per A-181); `run --shard 7/2` writes an artifact
and prints **no** message. Neither single invocation gives a consumer both
the exit code AND the cause. `assay plan`'s equivalent refusal (still raises
`LaneConfigError`, printed via `main()`'s handler) is a third, different
shape again.

Restoring the detail has (at least) two real options, not one:
1. A new `ReasonCode` — a closed-enum widening every consumer's schema copy
   would have to accept (A-138/A-170 make this deliberate, not a quick add).
2. Print the diagnostic to stderr without touching the enum or the schema,
   reusing the exact pattern `cli.py`'s adapter-refusal branch already uses
   (`print(f"assay: {exc.outcome}/{exc.reason_code}: {exc}", file=err)`) —
   cheaper, no schema change, but needs the detail string plumbed back out of
   `run_lane` to the CLI layer (it currently only returns a `Verdict`), and
   the artifact itself still would not carry it.

Or accept that `run`'s refusals are, by this project's own design, diagnosed
by reason code alone, and document the resulting three-way asymmetry
(`run`+operators / `run`+shard / `plan`+either) rather than closing it. Needs
a decision, not a guess.

### N-5 — `judge.mutation.shard_index`/`shard_count` select nothing

Lane-declared shard fields (`config.py` validates and echoes them;
`verdict.py`'s `JudgmentR2` has fields of the same name) are read by nothing
in `runner.py`/`mutation.py` — confirmed by `grep`. Before round-3's D-8 fix
they produced a **false** stamp (a verdict claiming a shard identity nothing
enforced); now `judgment.r2.shard_index`/`shard_count` come from the executed
`--shard` CLI value instead, so the lane-declared fields are simply inert —
config validates them, nothing consumes them. Wire them as the default
`--shard` when the CLI flag is absent, remove the config fields, or document
them as reserved-for-future-use — any of the three closes this; leaving it
silent is the A-046 "lane-table-implies-capability" trap.

**Round-3 review addition:** `config.py:1784` independently bounds
`judge.mutation.shard_count` by `MAX_MAX_MUTANTS` (10,000) — a *fourth*
shard-count ceiling, on a constant that merely happens to also equal
`MAX_SHARD_COUNT`. Inert today because this config is the dead field N-5 is
about, but if N-5 is resolved by "wire them as the `--shard` default", this
bound must switch to `MAX_SHARD_COUNT` explicitly or the two can silently
drift apart the next time either constant changes for its own reason.

### Acceptance

- [x] a decision recorded on N-4 (new `ReasonCode` vs. a stderr-only
      diagnostic reusing `cli.py`'s existing print pattern vs. accept and
      document the three-way asymmetry between `run`+operators, `run`+shard,
      and `plan`) — decided: accept and document (A-309); both alternatives
      are real API commitments (widening a closed enum, or changing
      `run_lane`'s `Verdict`-only return contract), not stabilization-wave
      fixes;
- [x] a decision recorded on N-5 (wire / remove / document reserved), and
      `config.py`/`CONSUMERS.md` updated to match — decided: document
      reserved (A-310), on the `MutationConfig.shard_index`/`shard_count`
      fields directly in `config.py`; `CONSUMERS.md` never mentioned these
      fields, so nothing there was stale;
- [x] if N-5 is resolved by wiring, `config.py:1784`'s `MAX_MAX_MUTANTS`
      bound is switched to `MAX_SHARD_COUNT` in the same change — done
      regardless of the wiring decision (A-310): a new `MAX_SHARD_COUNT`
      constant in `config.py` closes the drift risk either way.

**Status: PARTIALLY RESOLVED 2026-08-25 (stabilization wave), matching
B024's own precedent for a decided-but-not-eliminated defect.** N-4's own
heading names two defects — "a bad `--shard` refusal names no cause" and
"`shard_index`/`shard_count` are dead config" — and BOTH remain true by
design after A-309/A-310: N-4 was decided as an accepted, documented
asymmetry rather than closed, and N-5 was decided as accepted-and-documented
reserved config rather than wired or removed. Only the coincidental
`MAX_MAX_MUTANTS`/`MAX_SHARD_COUNT` ceiling coupling round 3 flagged is an
actual code fix (A-310). Marking this `RESOLVED` outright would overstate
what changed; round 2 review of this wave caught the overstatement.

## B027 — a mutant-induced pytest timeout crashes `execute_plan` instead of reaching `BUDGET_EXCEEDED`/`LANE_TIMEOUT`

**Filed 2026-08-25 (dstdns P132 phase-1 code review; provenance below).**

### Observed mechanism (live-reproduced, 5×, deterministic — not a fluke)

`assay run <r2-lane>` for a lane whose `judge.mutation.budget_per_candidate`
elapses on a real mutant (the mutated code genuinely hangs — e.g. an
off-by-one on a `while` loop's guard condition that never terminates) hits
`subprocess.TimeoutExpired` inside `execute_plan`'s `default_process_runner`
call, and assay's OWN handling of that timeout then crashes:

```
subprocess.TimeoutExpired: Command '[... pytest ...]' timed out after 30.0 seconds
  (during handling of the above exception)
AttributeError: 'bytes' object has no attribute 'encode'. Did you mean: 'decode'?
  at assay/runner.py:239 in _bounded_tail, called from assay/runner.py:386 in execute_plan
    (deployed assay-2.3.0.pyz line numbers; current vbpub/assay/src/assay/runner.py
    has the same call at :458 inside `except subprocess.TimeoutExpired as exc:`,
    calling `_bounded_tail(exc.stdout)`)
```

`_bounded_tail(raw: str | None)` (`runner.py:229`) is documented and typed to
receive an already-decoded `str` — its own docstring says *"The input is
decoded by `subprocess` under `text=True`"* — and DESIGN-GUIDE.md's "Bounded
command-output tails" section states the same assumption verbatim: *"The
bound is measured after decoding because `subprocess.run(text=True)` is the
production boundary."* That assumption holds for the NORMAL completion path
(`execute_plan:493-494`, `_bounded_tail(proc.stdout)` off a
`CompletedProcess`) but **not** for the timeout path
(`execute_plan:457-459`): `subprocess.TimeoutExpired.stdout`/`.stderr`
carries whatever partial output `Popen._communicate` had buffered at the
moment the timeout fired, and on that exception path CPython does not run it
through the same text-decode step `communicate()`'s normal return does —
so `exc.stdout` is `bytes`, not `str`, even though `text=True` was passed to
`subprocess.run`. `_bounded_tail` calls `.encode("utf-8")` unconditionally
(line 239/458), which only exists on `str` — `bytes` has `.decode()`, not
`.encode()` — hence the `AttributeError`.

**Reproduced 5 times across two independent contexts** (dstdns P132's
`worker-execution-admission-r2-compare` lane, identically on both unmodified
`main` and a feature branch — a mutant on `admission.py`'s `while not
has_capacity():` guard genuinely hangs past the 30 s
`budget_per_candidate`).

### Consequences (why this is worse than "a lane sometimes reports FAIL")

1. **No verdict is produced.** The process exits 1 (Python's uncaught-exception
   exit code), indistinguishable on exit code alone from a legitimate
   `FAIL/MUTANTS_SURVIVED`.
2. **A stale verdict JSON is left on disk from a PRIOR run**, at the same
   path the crashed run would have written to. A caller that reads
   `.assay/verdict-<lane>.json` without separately checking the invoking
   process's own exit code / stderr will read an old commit's result and
   believe it is current. In dstdns P132's own case this was caught only
   because a human/reviewer compared the verdict's embedded `commit` field
   against the actual `git rev-parse HEAD` and found a mismatch — a
   consumer that trusts the artifact alone has no such tripwire.
3. **A real, documented terminal state exists and is bypassed.**
   DESIGN-GUIDE.md's outcome/reason-code table (the table right above the
   "Bounded command-output tails" section this bug lives in) already
   declares `BUDGET_EXCEEDED`/`LANE_TIMEOUT` for exactly this case — a
   mutant-induced timeout is supposed to be a clean, artifact-producing
   terminal, not a crash that skips the whole verdict pipeline.

### Why assay owns this (not the dstdns consumer)

The crash is entirely inside `assay/runner.py`'s own exception-handling path,
triggered by assay's own subprocess invocation and assay's own bounded-tail
helper — no lane configuration, mutation operator choice, or consumer code
can avoid it once a mutant happens to hang past budget. `budget_per_candidate`
existing at all (B012) means a hanging mutant is an EXPECTED, designed-for
case, not an edge condition a lane author failed to anticipate.

### Proposed contract

1. `_bounded_tail` should accept `str | bytes | None` and decode `bytes`
   itself (`.decode("utf-8", errors="replace")`, matching the tolerant
   decode policy the docstring already describes for the normal path),
   OR the `except subprocess.TimeoutExpired` handler should decode
   `exc.stdout`/`exc.stderr` before calling `_bounded_tail`, so the
   function's documented `str`-only contract stays accurate and the fix is
   localized to the one path that actually receives `bytes`.
2. A timeout must always reach `BUDGET_EXCEEDED`/`LANE_TIMEOUT` per the
   already-published reason-code table — never an uncaught exception.
3. A crashed lane invocation must never leave a verdict artifact from a
   PRIOR run sitting at the current run's expected output path without at
   least a companion signal (a non-zero process exit code already exists,
   but the design-guide should say explicitly that a caller must check it
   rather than trusting a discovered artifact's mere presence — or,
   stronger, the artifact could be removed/renamed before the crashing
   attempt so its absence is unambiguous).

### Behavioral oracle (including a controlled wrong implementation)

A unit test constructing a `subprocess.TimeoutExpired` with a `bytes` `.stdout`/
`.stderr` (reproducing the exact object CPython hands back on this path) and
calling `execute_plan`'s timeout branch (or `_bounded_tail` directly with a
`bytes` argument) must return a `BUDGET_EXCEEDED`/`LANE_TIMEOUT` `Verdict`
carrying decoded tails — not raise. **Controlled wrong implementation this
must catch:** reverting the fix (removing the `bytes`-handling branch, or
re-introducing the bare `.encode("utf-8")` call) must make that exact test
raise `AttributeError` again — i.e., the test must be shown to fail against
today's shipped code before the fix, not just pass against the fixed code.

### Spec section that owns this behavior

`docs/DESIGN-GUIDE.md` — the outcome/reason-code table (`BUDGET_EXCEEDED` /
`LANE_TIMEOUT`) and the immediately-following "Bounded command-output tails"
section, whose stated assumption ("the production boundary" always decodes)
this bug violates on exactly the one path — timeout — that section's own
prose does not carve out an exception for.

### Provenance

Found during dstdns P132 (worker-io execution path repair) phase-1 code
review, confirmed independently by the reviewer:
`dstdns/nyxloom-trove/reviews/dstdns-P132-code-review-phase1-r1.md` §F
("NEW upstream finding — assay 2.3.0 crashes on a mutant-induced pytest
timeout"), disposition `dstdns/nyxloom-trove/decisions.md` D-201 ("file
upstream, not this package's problem"). First independently noticed by the
P132 implementer in an earlier round of the same package
(`dstdns/nyxloom-trove/reports/dstdns-P132-REPORT.md` §1b), then confirmed
as a real (not implementation-caused) defect by the phase-1 reviewer working
from a blind, independent reproduction.

### Acceptance

- [x] `_bounded_tail` (or its timeout-path caller) handles `bytes` input
      without raising;
- [x] a mutant-induced timeout reaches `BUDGET_EXCEEDED`/`LANE_TIMEOUT` with
      a real verdict artifact, never an uncaught exception;
- [x] the regression test constructing a `bytes`-carrying `TimeoutExpired`
      is shown red against pre-fix code, green after;
- [x] a crashed/refused lane run never leaves an ambiguous stale artifact
      at its expected output path (or DESIGN-GUIDE.md documents explicitly
      that callers must check the invoking process's own exit status, not
      artifact presence alone) — documented (the softer option; a crashing
      run's own output path is unchanged, but DESIGN-GUIDE.md's "Bounded
      command-output tails" section now says explicitly that exit status,
      not artifact presence, is what a caller must check).

**Status: RESOLVED 2026-08-25 (stabilization wave).** See A-300.

---

## B028 — a lane-wide `LANE_TIMEOUT` also writes no verdict artifact

**Filed 2026-08-25 (round 2 review of the stabilization wave, finding
N-W3).** Same family as B025 ("a post-HEAD-resolution terminal path emits
no artifact"), a different trigger — filed separately rather than folded
into B025 because the mechanism and the blast radius are both different.

### Problem

`LaneDeadline.remaining()` (`runner.py`) raises a bare `AssayError`/
`BUDGET_EXCEEDED`/`LANE_TIMEOUT` directly whenever the lane-wide deadline
has expired — it is the ONE seam every higher-rigor timing check in this
codebase reads through. Measured: a lane whose command simply runs past
`budget_seconds` (a plain `sleep 30` against a 1s budget, no mutation, no
infrastructure, nothing B025 touches) exits non-zero with **no verdict
artifact** even when one was reserved — identical before and after this
wave's B025 fix, confirmed by driving both trees.

**Wider blast radius than B025's fix reaches.** B025 wrapped four specific
`resolve_command_plan` call sites. `deadline.remaining()` itself is called
from roughly 16 separate sites across `runner.py` (7), `mutation.py` (6),
and `canary.py` (3) — every one of them a place the SAME uncaught-`AssayError`
crash could fire, not just the plan-resolution moment. A general fix likely
means catching `AssayError`/`LANE_TIMEOUT` at ONE outer boundary per
higher-rigor entry point (`_run_higher_rigor_lane`, direct R0's own loop)
rather than wrapping every individual call site the way B025 did for a
narrower, single-cause failure.

### Why this needs its own design pass, not a quick patch

Unlike B025 (where every `resolve_command_plan` raise is knowably
`BAD_LANE_CONFIG`, always the same shape), a `LaneDeadline.remaining()`
raise can fire from inside a Git call, a subprocess wait, or a snapshot
operation already partway through side effects (a materialized snapshot, a
half-written mutation-state record) — an outer catch-and-refuse needs to
reason about what state exists at the moment of the timeout, not just build
a degraded `CommandPlan` the way B025's fallback does. That reasoning has
not been done yet.

### Acceptance

- [ ] a decision recorded on where the outer catch boundary belongs (one
      per higher-rigor entry point vs. per call site);
- [ ] a lane-wide `LANE_TIMEOUT` (measured via a real, short `budget_seconds`
      and a genuinely slow command) writes a real, schema-valid verdict
      artifact to a reserved `--verdict-json`, driven through the installed
      CLI;
- [ ] a test proving the fix is shown red against pre-fix code (matching
      B025's own red-first discipline).

---

## B029 — R3's canary side-run has no infrastructure wiring at all; a resolvable-elsewhere fact reports a misattributed R3 claim

**Filed 2026-08-25 (round 2 review of the stabilization wave, in the course
of verifying B025).** Not B025 itself — B025's four sites all crashed
uncaught with no verdict; this one produces a real, schema-valid verdict
with the wrong cause, which is arguably worse to leave undiagnosed.

### Problem

`canary.py`'s R3 side-run resolves a **second**, independent `CommandPlan`
via `runner.execute_command`, which accepts no `infrastructure_source`/
`infrastructure_environment` parameters at all (`execute_command`'s own
docstring now says so explicitly, corrected in the stabilization wave's
round 2 — see `runner.py`'s step-1 note). This is not a missing forward
the way B025's environment-probe site was; `execute_command` has never had
anywhere to forward these params TO. Confirmed reachable: nothing in
`config.py` forbids an `[infrastructure]` table on an R3 lane, and R3 forces
R1 alongside it, so the lane's MAIN command plan (built through
`_run_higher_rigor_lane`) resolves its infrastructure facts correctly — only
the canary's own side-run plan does not.

**The failure mode is a misattributed claim, not a crash.** `_run_higher_rigor_lane`
already catches `AssayError` from `run_isolated_canary` (`runner.py` around
`:2262`) and converts it into an R3 `Claim` carrying the exception's own
`outcome`/`reason_code` — so a lane declaring a `derived:` fact that resolves
perfectly everywhere else reports `ERROR`/`BAD_LANE_CONFIG` on its R3 claim,
naming the infrastructure declaration as the cause when nothing about it is
actually broken. Worse: a `required-env:` fact would silently SUCCEED on
this path (`resolve_command_plan`'s own default falls back to `os.environ`
when `infrastructure_environment` is `None`), so the two source kinds behave
differently on the exact same lane shape — a `derived:` fact fails, a
`required-env:` fact doesn't, for reasons that have nothing to do with
either fact's own resolvability.

### Why this needs a design decision, not a quick patch

Unlike B025 (where every crash site needed the SAME fallback shape:
"forward the params, or refuse cleanly if that fails"), this is a genuine
missing-feature question: should the canary side-run see the same
infrastructure world as the lane's main command at all? If yes, threading
*infrastructure_source*/*infrastructure_environment* through
`execute_command` into `canary.py`'s two callers needs its own test
coverage (a real R3 lane with a resolvable `derived:` fact, driven through
the CLI, asserting the R3 claim is `PASS`/`FAIL` on the actual canary
outcome, not `ERROR`/`BAD_LANE_CONFIG` on an infrastructure cause that isn't
real). If no — R3 canaries are documented as infrastructure-blind — that
needs stating explicitly in `docs/CONSUMERS.md` and `DESIGN-GUIDE.md` rather
than left to be discovered as a confusing `BAD_LANE_CONFIG` claim.

### Acceptance

- [ ] a decision recorded on whether R3's canary side-run should resolve
      infrastructure facts at all;
- [ ] if yes: `execute_command`/`canary.py`'s two call sites thread
      *infrastructure_source*/*infrastructure_environment* through, and a
      CLI-driven test proves a resolvable `derived:` fact no longer produces
      a false `ERROR`/`BAD_LANE_CONFIG` R3 claim;
- [ ] if no: documented explicitly as a known limitation in `docs/CONSUMERS.md`
      and `docs/DESIGN-GUIDE.md`, so a consumer hitting the misattributed
      claim has somewhere to learn why.

## B030 — `assay plan` reports zero candidates for every lane; its own test asserts the bug

**Filed 2026-08-25, from the 2.1.0→2.3.0 review-gap audit
(`reports/assay-review-gap-audit-2026-08-25.md` §6, finding 8a-A) — the first
independent review of `8a2a4731` (shipped in assay-v2.2.0 as part of B012).**
**Status:** RESOLVED 2026-08-25 (A-319). Fixed by deleting `_cmd_plan`'s
`_relocate_source_roots` call outright -- `plan` never materializes a snapshot,
so there is no snapshot project root to relocate against. Verified through the
installed CLI on a real repository: `plan` `candidate_count` `0` -> `1`,
matching `assay run`'s own count and its candidate id byte-for-byte, and a
`mode = "whole_target"` lane plans instead of failing. The frozen test
assertion is corrected to the true count. See
`reports/assay-B030-B032-remediation-REPORT.md`.

### Problem

`_cmd_plan` (`src/assay/cli.py`) calls `runner._relocate_source_roots(lane,
project_root=lane_file.project_root, scratch_project_root=(prepared.spec.scratch_root
/ "unused"))` — a directory literally named `"unused"` that is never created.
The real `run` path passes `baseline_snapshot.project_root`
(`runner.py:2077`/`:2101`) instead. `resolve_mutation_targets`'s containment
gate (`mutation.py:459`) is an unconditional `is_relative_to(root)` check
against the relocated (nonexistent) roots, so it can never be satisfied —
`assay plan <lane>` returns `candidate_count: 0` for every lane, unconditionally,
and a `mode = "whole_target"` lane fails outright naming a temp dir that never
existed. Root cause confirmed by suppressing only the relocation call, which
makes plan's answer match a real `run`'s candidate count and IDs byte-for-byte.

**The commit's own test asserts the bug as correct behavior:**
`test_plan_reports_candidates_without_executing`
(`tests/test_mutation_progress_budget_plan.py:550`) asserts
`payload["candidate_count"] == 0` against a fixture that genuinely yields one
candidate — it was written to match observed output, not the requirement.
`docs/CONSUMERS.md:517` (added after the bug, in the remediation wave) states
plan "reports deterministic candidate IDs, total/per-file/per-operator counts
… and runtime estimates" — false on `main` today. B012's own acceptance box
"`assay plan` reports deterministic totals/IDs/runtime estimate" is unmet
despite being checked `[x]` — see the correction note on B012 above.

Two later review rounds (`45ea7d0b`, `b97f3aaf`, `21205b78`) touched
`_cmd_plan`'s argument parsing and never once ran it against a real lane with
source roots, so this was never caught.

Beyond the headline bug: `_cmd_plan` never runs `lane.environment_command`
(plans a lane a real `run` would refuse) and plans against HEAD without
`run`'s clean-tree precondition; its runtime estimate falls back to a
fabricated `60.0` s/candidate when `budget_per_candidate` is absent (reported
to three false decimals of precision), and uses the timeout — an upper bound —
as the "estimate" when the key is declared, which is not what B012 requirement
2 ("measured/estimated baseline runtime") asked for.

### Oracle

- `test_plan_reports_candidates_without_executing`'s fixture must assert the
  TRUE candidate count (1, given its current fixture), not 0 — fix the test
  before the code, or the fix will read as a regression;
- a real R2 lane with declared `source_root_paths`, driven through the
  installed CLI, must show `assay plan <lane>` reporting the same
  `candidate_count`/candidate IDs as `assay run <lane> --verdict-json` on the
  same commit;
- a `mode = "whole_target"` lane must plan successfully rather than naming a
  scratch path that never existed.

### Acceptance

- [x] `_relocate_source_roots` (or plan's call site) uses the same project
      root a real run uses — the call was deleted outright (`plan` never
      materializes a snapshot); `assay plan`'s `candidate_count`/candidate
      IDs now match `assay run --verdict-json`'s byte-for-byte on the same
      commit, verified through the installed CLI (A-319);
- [x] the fixture-matching test assertion is corrected to the true count —
      `test_plan_reports_candidates_without_executing`
      (`tests/test_mutation_progress_budget_plan.py:646`) now asserts
      `candidate_count == 1` against its genuinely-one-candidate fixture,
      with a comment recording the correction;
- [x] `docs/CONSUMERS.md`'s plan description is verified true against a real
      run, not left aspirational — `docs/CONSUMERS.md:518-524` states the
      candidate IDs/counts match a real `assay run` and that the runtime
      estimates are a declaration-derived upper bound, never a measurement;
- [x] B012's "assay plan reports deterministic totals/IDs/runtime estimate"
      acceptance box is re-verified, not just re-checked — see B012's own
      corrected box above ("**Re-verified true 2026-08-25 (B030/A-319)**").

## B031 — the R2 progress artifact is written into the consumer's live worktree and poisons assay's own clean-tree precondition; the field is dead and unregistered in `verify.py`

**Filed 2026-08-25, from the 2.1.0→2.3.0 review-gap audit
(`reports/assay-review-gap-audit-2026-08-25.md` §6, findings 8a-B/8a-C/8a-F) —
`8a2a4731` (assay-v2.2.0, part of B012).**
**Status:** RESOLVED 2026-08-25 (A-320, plus A-323 for a fourth instance of
the same registration drift found while verifying the fix). Progress is now
opt-in and consumer-directed (`assay run --progress PATH`), absent by default;
`mutation.progress_artifact` is REMOVED from the dataclass, the wire payload
and the JSON Schema together (it never had a producer, and its only legal
grammar could name nothing but the forbidden worktree location);
`candidate_ids` is registered in `verify.py` AND given its first real producer
on the `--shard` path; `judgment.r2.shard_index`/`shard_count` were found to
have the identical `verify.py` gap and are registered too. The lane-name
traversal gap closes by construction. Verified through the installed CLI: an
R2 lane runs twice in a row clean, and a real sharded verdict round-trips
`assay run` -> `assay verify` green. See
`reports/assay-B030-B032-remediation-REPORT.md`.

### Problem

**(a) Worktree pollution — the R2 lane passes once, then refuses forever.**
`src/assay/runner.py:1892-1894` writes `.assay/<lane>.progress.jsonl`
unconditionally for every R2 lane into `Path(".assay")` — the consumer's real,
live worktree, not the private snapshot. Reproduced on a fresh repo with no
`.assay/` gitignore entry: run 1 passes and leaves `.assay/unit.progress.jsonl`
untracked; run 2, with nothing else changed, fails
`NO_MEASUREMENT/DIRTY_TREE` because `git.dirty_paths()` now returns that path.
This directly contradicts the project's own B006(b) rule ("never the
consumer's real worktree", `runner.py:1895`, `cli.py:29`) and the later A-292
ruling (written for resume state, while this progress artifact was already
doing exactly what A-292 forbids, unreviewed).

**(b) Dead field.** No code path anywhere constructs
`Mutation(progress_artifact=...)` — a real run emits a `mutation` block with
no `progress_artifact` key while the `.progress.jsonl` file sits on disk
unreferenced. B012 requirement 1's "Summarize the artifact path in the
verdict" and its acceptance box "progress events emitted **and referenced
from verdict**" are unmet — see the correction note on B012 above.

**(c) Unregistered in `verify.py` — the exact near-miss shape this audit was
commissioned to look for, LIVE.** `src/assay/verify.py`'s `_reconstruct_mutation`
never reads `progress_artifact` (nor `candidate_ids`, added later by
`7a4f6333`), so `_reject_unknown_keys` rejects any verdict that carries either
field even though both pass JSON Schema validation cleanly:
`assay verify` on such a document fails `schema: unknown mutation field(s):
['progress_artifact']`. The schema `$defs/mutation` placement bug from the
same commit (misfiled onto `$defs/coverage`/`$defs/claim`, not `$defs/mutation`)
was fixed by `7941fdcb`; this reconstruction-layer gap was not.

**Related, same feature (MINOR):** the write path
(`runner.py:1892`, via `progress_writer`, `mutation.py:688`) interpolates the
raw lane name with no path validation and no lane-name grammar in
`config.py` — a lane named `"../../../pwned/esc"` (a legal quoted TOML key)
writes NDJSON three directories above the project root, PASS reported anyway.
The path is also CWD-relative rather than project-relative despite the
schema typing the field as `repo_tree_path`, and the file is opened `"a"` and
never truncated, so successive runs leave one growing file with no run
id/commit/timestamp to disambiguate which run a `candidate_index` belongs to.
Separately, `_progress_event`'s `replacement_sha256` (whole-mutated-file
digest) and the verdict's `MutantOutcome.replacement_sha256` (replacement-text
digest) disagree for the same candidate under the same field name — `plan`'s
digest agrees with the progress artifact, not with the verdict.

### Why this needs a design decision, not a quick patch

Unlike a pure bugfix, "where does R2 progress state live" is the same class
of question A-292 already ruled on for resume state (never the consumer's
real worktree) — this feature needs the same answer applied to itself, plus a
decision on whether `progress_artifact` is worth keeping as a wired,
consumer-visible field (requiring `verify.py` registration + a real
population path) or should be removed from the schema/dataclass entirely
since nothing populates it today.

### Acceptance

- [x] a decision recorded on where progress NDJSON lives (private
      snapshot/scratch area, consistent with A-292, never the real worktree)
      — decided differently than framed: NOT a private snapshot/scratch
      area either, but a consumer-named path (`assay run --progress PATH`),
      absent by default, exactly like `--verdict-json`'s own destination.
      Recorded as A-320, restated (load-bearing verifier-rejection argument)
      as A-324;
- [x] if `progress_artifact` is kept: a real code path populates
      `Mutation(progress_artifact=...)`, `verify.py`'s `_reconstruct_mutation`
      registers it (and `candidate_ids`), and a real `assay run` → `assay
      verify` round-trip is proven green through the installed CLI —
      **N/A: the "if dropped" branch below was taken instead**; `candidate_ids`
      alone got the registration + producer + round-trip treatment this box
      describes;
- [x] if dropped: removed from the schema/dataclass together, not left as an
      inert, unpopulatable field — `mutation.progress_artifact` removed from
      the dataclass, `to_dict`, the JSON Schema and the W2 frozen lock
      together (A-320); schema v7 left unbumped, on the load-bearing
      argument that no released `assay verify` ever accepted a document
      carrying the field either (A-324), not merely that no producer ever
      emitted it;
- [x] a real R2 lane run twice in a row, with nothing else changed, passes
      both times (the DIRTY_TREE regression test) —
      `test_an_r2_lane_run_twice_with_nothing_changed_passes_both_times`
      (`tests/test_environment_preflight.py`), driven through the installed
      CLI;
- [x] the lane-name path-traversal gap is closed (validate against the
      existing lane-name grammar, or add one) — closed by construction: no
      lane name is interpolated into any path any more, since the
      destination is now the consumer's own CLI argument;
      `test_progress_goes_exactly_where_the_consumer_asked_and_nowhere_else`
      covers it.

## B032 — the preflight probe added by B010/B012 discards its own outcome, misreports budget overruns, and B010's "clear message" refusal ships 0 bytes of stderr

**Filed 2026-08-25, from the 2.1.0→2.3.0 review-gap audit
(`reports/assay-review-gap-audit-2026-08-25.md` §6, findings 8a-D/8a-E) —
`8a2a4731` (assay-v2.2.0, B010's `environment_command` mechanism + part of
B012).**
**Status:** RESOLVED 2026-08-25 (A-321 scope, A-322 fix). A probe that
exhausts its cap now reports `BUDGET_EXCEEDED`/`LANE_TIMEOUT`, exit 4; every
other probe failure keeps `ERROR`/`BAD_LANE_CONFIG`, exit 2, with the cause on
stderr rather than in a widened reason-code vocabulary. The 30 s cap is applied
as `execute_plan`'s `timeout=` argument -- the value it actually reads -- and a
probe refusal writes B010's clear message (lane, cause, declared wrapper) to
stderr, non-empty. All three verified through the installed CLI. See
`reports/assay-B030-B032-remediation-REPORT.md`.

### Problem

**(a) Four structurally different probe failures collapse into one
indistinguishable, mislabeled verdict.** `src/assay/runner.py:2786-2807`:
`execute_plan` correctly classifies the probe outcome, then
`probe_result` is discarded and the refusal is hardcoded to
`ERROR`/`BAD_LANE_CONFIG` regardless of what actually happened, with
`argv_effective` recording the lane's real command — which never ran.
Measured: a nonexistent binary, a nonzero exit, and a signal death all report
identically (arguably defensible); but a probe that genuinely exhausts its
own budget (`sh -c "sleep 45"`, `budget = "30s"`) — which `execute_plan`
correctly classifies as `BUDGET_EXCEEDED`/`LANE_TIMEOUT` — is *also* forced
into `ERROR`/`BAD_LANE_CONFIG`, exit 2 instead of the correct exit 4. A gate
that retries on `BUDGET_EXCEEDED` but hard-fails on `BAD_LANE_CONFIG` (the
estate's own run-gate shape) does the wrong thing on a real timeout.

**(b) The probe's own 30s cap is dead code.** `runner.py:2783` sets
`budget_seconds=min(30.0, deadline.remaining())` on the plan, but
`execute_plan` ignores `plan.budget_seconds` and uses its `timeout=` argument,
which is passed the **full** `deadline.remaining()` — a hung probe consumes
the entire lane budget, not the intended 30s cap.

**(c) B010's entire stated deliverable is missing.** B010's ask, verbatim:
"refusing with 'this lane's declared environment does not match the invoking
one; run via `<declared wrapper>`' instead of surfacing the suite's raw
traceback" — a clear message. Reproduced: stderr is **0 bytes** on a probe
refusal. The consumer gets neither the raw traceback (B010's original
complaint) nor a clear message (B010's fix) — just a bare
`BAD_LANE_CONFIG` pointing at the never-run lane argv, which actively
misleads. B010's status should not read as fully addressing its own ask —
see the correction note on B010 above.

### Why this needs a design decision, not a quick patch

The four collapsed causes need a decision on how many distinguishable
outcomes the probe refusal should expose (at minimum: config/exec error vs.
timeout, since gates already branch on that distinction) before the fix is
written, plus the actual message text B010 asked for in the first place.

### Acceptance

- [x] a decision recorded on which probe outcomes must remain
      distinguishable in the emitted verdict (at minimum BUDGET_EXCEEDED vs.
      BAD_LANE_CONFIG) — recorded as A-321: exactly ONE distinction survives
      (timeout vs. everything else), the one gates already branch on; the
      other three causes collapse into `BAD_LANE_CONFIG` deliberately, with
      the diagnosis carried as stderr text (A-322) rather than a
      reason-code widening (A-138/A-170);
- [x] a probe that exhausts its own budget reports `BUDGET_EXCEEDED`/
      `LANE_TIMEOUT`, exit 4, not `BAD_LANE_CONFIG`/exit 2 — verified
      through the installed CLI (A-322);
- [x] `execute_plan` honors `plan.budget_seconds` (or the 30s cap is removed
      from the code, not left silently unenforced) — via its own escape
      hatch: `execute_plan` still never reads `plan.budget_seconds` directly
      (it reads its separate required `timeout=` argument), but the caller
      now derives BOTH `budget_seconds=probe_timeout` and `timeout=
      probe_timeout` from the same `min(PROBE_BUDGET_SECONDS,
      deadline.remaining())` expression, so the cap is enforced where it is
      actually read and is no longer "silently unenforced" — measured: a
      `sleep 45` probe under a `5m` budget went from PASS-after-46s to
      BUDGET_EXCEEDED-after-30s;
- [x] a probe refusal writes B010's actual clear-message text to stderr,
      driven through the installed CLI, non-empty — measured 171-215 bytes
      per lane in the remediation REPORT's before/after transcripts, and the
      timeout message now names whichever bound (the 30s cap or the lane's
      own tighter remaining budget) actually fired, not always the cap
      (round-2 review fix; see the REPORT's Round 2 section);
- [x] B010's status is corrected to reflect what's actually shipped — see
      the correction note on B010 above.

## B033 — SQL whole-target R2 silently drops declared targets that R1 refuses, records a `base` for a comparison that never ran, and a `judge.mode` toggle silently enables/disables the SQL vacuity guard

**Filed 2026-08-25, from the 2.1.0→2.3.0 review-gap audit
(`reports/assay-review-gap-audit-2026-08-25.md` §5, findings ba-A/ba-B/ba-C) —
`ba8908d6` (2026-08-22, whole-target SQL mutation targets). This commit
shipped with zero tests, zero doc updates, and zero decision records
(`decisions.md`'s sessions jump 2026-08-16 → 2026-08-25 across it).**
**Status:** **FIXED 2026-08-26 (A-325)**, on branch
`fix/assay-b033-b034-sql-mutation-operators`, unmerged and unreleased at the
time of writing — it ships in the next release, and this line deliberately
names no version until one exists. All three
defects plus both MINORs; see
`reports/assay-B033-B034-remediation-REPORT.md` for the before/after CLI
transcripts. **Consumer action required:** a whole-target lane may no longer
declare `judge.base` (it is refused as inert config) — dstdns's `cw2b_schema`
lane must delete its `base = "origin/main"` line.

### Problem

**(a) `judgment_resolved.base` is recorded for a comparison R2 never runs.**
`whole_file_r2` (`runner.py:2047-2050`) skips both `check_base_is_head` and
the `git diff` for whole-target R2, but `compares_a_base`
(`runner.py:2295`) remains `judgment_r1 is not None or judgment_r2 is not
None`, so `_build_judgment_resolved` (`runner.py:2328-2332`) still writes a
resolved `base`/`base_resolution` into the artifact. `_build_judgment_resolved`'s
own docstring forbids exactly this ("recording one would be an invented fact
rather than a missing one"). Reproduced against a repo where `HEAD ==
origin/main` (no diff a diff-based R2 would find): the emitted verdict still
carries `base`/`base_resolution: "merge-base"` on an R2 that mutated the
whole file regardless.

**(b) The SQL carve-out reopens the vacuity hole its own error message
names.** `src/assay/config.py:1299` appended `and declared_language != "sql"`
to the guard whose own message reads "a target list under changed-line mode
does nothing and silently declaring one is how a consumer comes to believe a
floor is enforced when it is not." A SQL lane declaring `judge.targets`
**without** `judge.mode = "whole_target"` now loads clean and silently routes
to the diff path — same lane, one line of TOML different, and the inert
`targets` list still reaches the artifact via `JudgeConfig.as_declared()`,
advertising a floor that was never applied. `targets` was also added to the
unconditional surplus exemption (`config.py:1362`), so nothing downstream
catches it.

**(c) R2's whole-target resolver silently drops declared targets; R1's own
resolver refuses the identical shape.** `_mutation_targets_whole`
(`runner.py:1770-1806`) `continue`s silently past an excluded dir, a
non-matching `source_globs`, or a test path. Its R1 counterpart
`_resolve_whole_target` (`evaluate.py:715-780`) **refuses** every one of
those with a named `ERROR`/`BAD_LANE_CONFIG`, and its own docstring explains
why: a target expanding to N files of which only one is measured would PASS
while leaving the rest unjudged — "precisely the vacuity hole this whole mode
exists to close." R2 reopened it one tier down. Reproduced: a lane declaring
`targets = ["db/schema.sql", "db/tests/fixtures.sql"]` mutates only
`db/schema.sql`, reports `FAIL`/`MUTANTS_SURVIVED`, and nothing in the
verdict names the dropped target (`judgment.r2` carries no `targets` field at
all, only `judgment.r1` does) — "judged and clean" is indistinguishable from
"silently skipped" from the artifact alone. A declared target absent at the
judged commit also yields unnamed `ERROR`/`GIT_FAILED`, where R1 names the
target.

**Related (MINOR):** `runner.py:1316` (`config.py:1316` guard requiring
`base` for SQL R1-only lanes) is gated on `"R2" not in rigor`, so it can never
fire on an R2 lane — its only live effect is forcing an R1-only SQL
whole-target lane to declare an inert `base`, inverting the
`docs/CONSUMERS.md:119` rule ("`judge.base` is FORBIDDEN here") for one
language. `whole_file_r2` is also not language-gated — a **Python**
`R0,R1,R2` whole-target lane (a shape `DESIGN-GUIDE.md:904-905` blesses)
silently switches R2 from diff-based to whole-file mutation, undocumented
anywhere (every shipped doc still describes `mode`/`targets` as R1-only).

### Why this needs a design decision, not a quick patch

(a) and (c) both need the SAME kind of ruling B006(b)/A-292 already gave
elsewhere in this codebase (never invent a fact; never silently narrow
scope) applied consistently to R2's whole-target path — a design decision on
what R2's own docstring/contract should say, not just a local patch. (b) is a
scope question: should the vacuity-hole guard apply per-language at all, or
should SQL's whole-target carve-out be expressed a different way that
doesn't require weakening the guard's own stated purpose.

### Acceptance

- [x] a decision recorded on whether R2 whole-target should refuse
      (matching R1) or silently narrow (current, rejected) declared targets
      that fail its containment gates — **A-325: refuse, matching R1**;
- [x] `judgment_resolved.base`/`base_resolution` is omitted when no tier
      that reads a base actually ran;
- [x] the SQL `judge.mode`/`judge.targets` carve-out no longer permits an
      inert, artifact-advertised `targets` declaration with no enforcement —
      the carve-out is deleted; `targets` also leaves the surplus exemption;
- [x] `whole_file_r2`'s language scope (SQL-only vs. any language) is a
      recorded decision and matches the shipped documentation — **A-325:
      `mode` is a lane-level scope read by R1 and R2 in EVERY language**,
      documented in `README.md`, `docs/CONSUMERS.md` and
      `docs/DESIGN-GUIDE.md`;
- [x] a real SQL whole-target R2 lane, driven through the CLI, with one
      target inside scope and one outside, either refuses naming both or
      records both in the verdict — never silently reports on one alone —
      it refuses `ERROR`/`BAD_LANE_CONFIG`, naming the target and the gate
      on the diagnostics stream (transcript in the REPORT).

**Not closed here, filed as B035:** `judgment.r2` still carries no `mode` or
`targets` field, so an `R0,R2` whole-target artifact cannot witness its own
scope. The model, the raw verifier and the JSON Schema therefore enforce the
`base` rule only where `judgment.r1.mode` is present to witness it.

## B034 — B015's two "semantic" Python mutation operators add zero mutation coverage beyond `compare-swap`, mislabel ordinary attribute comparisons as enum comparisons, and double-count every co-selected site

**Filed 2026-08-25, from the 2.1.0→2.3.0 review-gap audit
(`reports/assay-review-gap-audit-2026-08-25.md` §1, findings B015-A/B/C) —
`126ef577`/`6324548d` (B015, marked IMPLEMENTED 2026-08-24; see the
correction note on B015 above). This is the fix-needed successor to B015
itself, filed separately per this project's own convention (cf. B021 out of
B012).**
**Status:** **FIXED 2026-08-26 by WITHDRAWAL (A-326)**, on branch
`fix/assay-b033-b034-sql-mutation-operators`, unmerged and unreleased at the
time of writing — it ships in the next release, and this line deliberately
names no version until one exists.
See `reports/assay-B033-B034-remediation-REPORT.md` for the
before/after CLI transcripts and for why the redesign path was evaluated and
rejected on A-112/A-221 grounds rather than on difficulty.

### Problem

**(a) Zero new mutations.** `_semantic_comparison_sites`
(`src/assay/adapters/python.py:712-761`) flips `ast.Eq`/`ast.NotEq` exactly
the way `_compare_swap_sites` already does for the same two operators, with
no operand-type restriction on either side. Measured over `src/assay/**.py`:
87 B015 sites, **zero** that `compare-swap` does not already produce
identically (same byte span, same replacement bytes). A consumer adopting
both families — the shape `docs/DESIGN-GUIDE.md:1477`'s own example now
declares — gets no additional mutation coverage of any kind. B015's own
"Required before dispatch" list explicitly asked to "decide explicitly
whether any proposed site overlaps `compare-swap` enough to be
indistinguishable evidence; reject or split accordingly" — no such decision
exists (no A-number, no carve, no review report), and the one acceptance box
that would have caught this ("a real R2 lane demonstrates kills attributable
to each admitted family") is the one left unchecked while the item was
marked IMPLEMENTED.

**(b) Every co-selected site is emitted twice.** `_candidate_sites`
(`python.py:779-784`) concatenates `_compare_swap_sites` and
`_semantic_comparison_sites` results with no de-duplication;
`MutationSite.identity` (`mutation.py:317-322`) includes the operator name,
so the duplicate-identity guard does not fire. A real R2 lane declaring both
families on a one-line change (`if cfg.debug == True:`) produces `total: 2`
for one distinct mutation — identical span, identical replacement digest,
attributed to two different operators — accepted cleanly by `assay verify`.
Consequence: inflated `mutation.total`/`candidate_count`, a `--max-mutants`
budget consumed roughly 2x, lane wall-clock roughly doubled on eligible
sites, and a verdict that misreports which operator family actually
killed/survived a mutant.

**(c) The enum predicate matches any attribute access, not enum members.**
`_is_enum_member_expression` (`python.py:705-710`) is `Attribute(value=Name)`
and nothing more — matches `self.count`, `cfg.debug`, `path.suffix`, `os.sep`;
its own docstring's second example (`enums.Color.RED`,
`Attribute(value=Attribute)`) is in fact *rejected* by the predicate it
documents. All 87 measured B015 sites in assay's own source are this
false-positive class — the project's own code compares no enum member
anywhere. The shipped test suite never tests an ordinary attribute
comparison (only a true-positive enum access and a rejected `Call`), so the
entire false-positive class is untested.

**Related (MINOR, same feature):** a stale `left` carried across loop
iterations (`python.py:761`) mis-splices the wrong `==` token in a mixed
comparison chain (`f() == g() == cfg.x`) — `_compare_swap_sites` recomputes
`left` per index and does not have this bug. No decision record exists for
this vocabulary extension (`grep -c "B015" decisions.md` = 0), despite
`vocabulary.py`'s own docstring describing the per-language operator set as
closed by construction and governed by A-numbered rulings.

### Why this needs a design decision, not a quick patch

This is the exact question B015's own filing required be answered before
dispatch and wasn't: do these operator families earn a place in the closed
`python:*` vocabulary at all? A local fix to (b)/(c) does not resolve (a) —
if the sites are a strict subset of `compare-swap`'s output, no
de-duplication or predicate tightening produces new coverage; the operators
either need a genuinely distinct site rule (e.g., something `compare-swap`
provably cannot express) or should be withdrawn from the schema/vocabulary,
which is itself a governed, A-numbered decision per A-112/A-114/A-220/A-221.

### Acceptance

- [x] a decision recorded (A-numbered) on whether
      `python:uuid-equality-swap`/`python:enum-comparison-swap` remain in the
      vocabulary, are redesigned to produce genuinely distinct sites, or are
      withdrawn — **A-326: withdrawn**;
- [x] if kept: n/a — not kept;
- [x] co-selection with `compare-swap` no longer double-counts a shared
      site — the family produces no sites at all, so the shared span is
      emitted exactly once, by `compare-swap`, guarded by a test that
      compares the identity list of `compare-swap` alone against
      `compare-swap` + both withdrawn names;
- [~] if withdrawn: `docs/DESIGN-GUIDE.md`'s example and
      `MUTATION_OPERATORS_BY_LANGUAGE`'s PRODUCER role are reverted, and
      B015's status is corrected. The schema `oneOf` and the catalogue
      MEMBERSHIP are deliberately **not** reverted in this wave — A-326
      applies A-324's own test and finds the opposite answer to A-320's:
      released `assay verify` builds ACCEPT a v7 document naming either
      operator (re-measured, exit 0, transcript in the REPORT), so removing
      the spellings would invalidate real artifacts and needs the next
      schema-version bump. The names are inert (`vocabulary.
      WITHDRAWN_MUTATION_OPERATORS`), refused at load and by `--operators`,
      and produced by nothing.

## B035 — an `R0,R2` whole-target verdict cannot witness its own judging scope, so the `base` rule is unenforceable there

**Filed 2026-08-26 from B033's own fix (A-325).**
**Status:** **FIXED 2026-08-30 (A-329)**, on branch
`feature/assay-b018-b019-b035-v8-synergy`, unmerged and unreleased at the time
of writing — it ships in the next release, and this line deliberately names no
version until one exists. This is the wave's one **v7 → v8 schema cut**: it was
batched alone as the version-bumping item precisely because it is the break,
and B018/B019 rode with it because they are additive/config-only. `judgment.r2`
now carries `mode` (required) and `targets` (optional), so the exemption A-325
had to carve out for an `r1`-absent document is gone and the base rule is
enforced for every shape — including the `R0,R2` one it most needed and least
covered.
**Priority note (round-2 review of the B033 wave):** this is not a neutral
deferral. A-325 had to STOP enforcing the old `base` rule for `R0,R2`
documents to make honest whole-target R2 artifacts verifiable at all, so a
diff-based `R0,R2` verdict that omits the base it was scoped against is
accepted today where 2.4.1 refused it (both directions measured in
`reports/assay-B033-B034-remediation-REPORT.md`). dstdns's `cw2b_schema` —
its only R2 lane — is exactly that shape. B035 should ride the next
schema-version bump rather than wait for one to happen along.

### Problem

`judge.mode` is a lane-level scope: a whole-target lane judges declared files
whole at R1 and R2 alike and reads no comparison commit at either tier, so
`judgment.resolved.base` must be ABSENT (A-325). `judgment.r1` records its
`mode` on the wire, so for any lane declaring R1 the model
(`Judgment.__post_init__`), the raw verifier
(`verify._check_base_matches_the_tiers_present`) and the packaged JSON Schema
can all enforce both halves of that rule — required under `changed_lines`,
forbidden under `whole_target`.

`judgment.r2` records neither `mode` nor `targets`. So for an `R0,R2` lane —
every SQL lane, and dstdns's `cw2b_schema` specifically — nothing on the wire
distinguishes a diff-based R2 (which MUST carry a base) from a whole-target R2
(which must NOT). All three layers therefore enforce neither half for that
shape, and a foreign document can record either way. The producer is correct;
nothing downstream can check it.

Second, smaller half of the same gap: a whole-target R2 verdict names the files
it mutated only through `mutation.*[].path` entries, so a target that produced
zero sites is invisible. R1 records `judgment.r1.targets`; R2 records nothing
equivalent. A-325's refusal makes a silently NARROWED set impossible, but a
declared target that legitimately yields no mutants still cannot be told from
one that was never considered.

### Why this needs a schema change, not a patch

Both halves need a new field on `judgment.r2` (`mode`, and `targets`
mirroring `judgment.r1.targets`). The packaged schema sets
`additionalProperties: false` on that object, so a released consumer holding a
v7 schema copy would reject any document carrying it: this is a
schema-version bump (v7→v8), not an additive fix — the same rule B014's own
6→7 bump followed for four optional fields. It should ride the next bump
alongside whatever else needs one, not force one on its own.

### Acceptance

- [x] `judgment.r2` records the mode it judged under, and the declared
      target set when that mode is `whole_target` — `verdict.py:1727`/`:1734`,
      mirroring `judgment.r1`;
- [x] the model, `verify.py` and the schema enforce the `base` rule for an
      `R0,R2` lane, not only for lanes declaring R1 — enforced in all three
      layers per A-182, against an r1-else-r2 witness, with the raw verifier's
      wording deliberately unlike the model's so a copy-paste stub cannot
      satisfy both;
- [x] `carve-assets/W2`'s frozen schema copy and acceptance suite move with
      the bump — **as a NEW frozen generation, not an edit to W2**: W2 stays
      frozen at v7 (that is the convention W1→W2 established), and
      `carve-assets/W4/` carries the v8 schema copy, a 40-node acceptance
      suite, six migrated templates and a `MANIFEST.md`. W4 rather than W3
      because `W3/` was already taken; the `W<n>` names are wave identities,
      not schema versions (A-330).

---

## B036 — a JavaScript/TypeScript `LanguageAdapter` for changed-line coverage (R1), first consumer dstdns's React UI

**Status: IMPLEMENTED 2026-08-30 on `feature/assay-b036-js-adapter`; awaiting
adversarial review and merge.** Decisions A-340..A-345; report at
`nyxloom-trove/reports/assay-B036-js-adapter-REPORT.md`. Two follow-ups filed
out of it — **B038** (branch arcs and the type-only-module gap, both blocked
on whether a lane may declare its coverage PRODUCER) and **B039** (an
unbounded line expansion of the same shape in the pre-existing `go-cover`
parser). Every acceptance box below is ticked with its own file:line evidence
in the report's acceptance table.

**Filed 2026-08-30.** dstdns's new UI (`applications/webapp-ui-react` —
React 19 + Vite 6 + TypeScript 5.8, test runner Vitest 3.2, no coverage
provider installed yet, no assay lane declared yet) currently has zero
changed-line coverage discipline: `docs/spec-react-ui-e2e.md` explicitly
scopes coverage as non-gating for the Playwright e2e suite it's extending,
and there is no gate at all for the Vitest unit/component layer. This is
greenfield, not a repair — no existing lane breaks, nothing regresses.

This must land as a genuinely general adapter, parallel to Python/Go/SQL —
dstdns is the first consumer, the same relationship SQL had to B001. Nothing
in this item is dstdns-specific; a lane declaring `judge.language =
"javascript"` must work for any JS/TS project assay is pointed at.

### Why this is a small, low-risk item — checked, not assumed

`assay.coverage`'s own module contract (`src/assay/coverage.py`'s docstring)
states plainly: **coverage format is declared data, not language
knowledge** — `judge.coverage.format` selects a parser from
`FORMAT_REGISTRY`, and the registry already holds four parsers for formats
spanning multiple languages (`cobertura`, `lcov`, `go_cover`,
`coverage_py_json`). Adding a fifth format is a self-contained parser
module under `coverage_parsers/`, not a redesign of the fail-under/branch
math, which is already format-agnostic.

The registry/rigor-wiring precedent (`src/assay/registry.py`,
`RegistryEntry.rigor`) already supports registering an adapter for a
STRICT SUBSET of R1/R2/R3 — this is exactly how Python itself first shipped
(R1 only, per `registry.py`'s own docstring, before P18/P19 added R2/R3
later) and exactly how SQL ships today (R2 only, `cli.py:265`). The single
wiring point is `src/assay/cli.py:261-265`'s `new_registry(...)` call. This
item follows the Python-at-R1-only precedent exactly: register the new
adapter for `frozenset({"R1"})` only. R2 (mutation) and wiring R3 into the
registry are explicitly deferred — R2 to **B037** (filed alongside this
item, design-first, same shape as B020), R3 as a natural, low-risk
fast-follow once R1 has shipped and the canary injection methods are
proven, not part of this item's acceptance.

**No schema-version bump is needed.** Because R2 is not being registered for
this language at all, `MUTATION_OPERATORS_BY_LANGUAGE`
(`src/assay/vocabulary.py`) needs no new entry (not even an empty one, unlike
Go's — Go registers no rigor level either, but P29 already reserved its
future R2 namespace; this item can defer that same reservation to B037
rather than pre-committing an empty vocabulary now). `judge.language` itself
has no closed hardcoded list to extend — `assay.config` never validates a
language name against a fixed set; `registry.get_adapter` is the sole
point of truth, and an unregistered name is already refused there
(`registry.py`'s own documented O2 guarantee). This item touches no verdict
field, no `Judgment`/`JudgmentR1`/`JudgmentR2` shape, and no packaged schema.

### Required design decisions before implementation

1. **The `judge.language` string.** Recommend `"javascript"` as the umbrella
   name covering `.js`/`.jsx`/`.ts`/`.tsx` — matching how `"python"` and
   `"go"` don't split by dialect/runtime, and matching how most polyglot
   coverage/lint tooling groups the whole family under one label. This is a
   real, effectively-permanent naming choice (schema strings are consumer-
   facing) — record it as a decisions.md entry with reasoning, don't just
   pick it silently.
2. **`normalize_coverage_key`'s actual job for this format.** Istanbul's
   `coverage-final.json` keys its per-file map by **absolute filesystem
   path**, not a repo-relative or package-qualified one — a different shape
   of mismatch than Go's package-qualified import path (`go_cover.py`'s own
   precedent for "the artifact's own path spelling differs from git-diff
   spelling"). Read `coverage_parsers/go_cover.py` and `coverage.py`'s own
   docstring on the universal-boundary/language-specific-strip split before
   deciding whether the absolute-to-repo-relative conversion belongs in the
   new parser (matching how each existing parser already normalizes its own
   artifact's path spelling) or in `normalize_coverage_key` — check where
   the existing four parsers actually do this today rather than assuming.
3. **Whether `requires_span_attribution` is really `False` for this
   adapter.** Unlike `coverage-py-json` (line-granular, needing Python's own
   AST-based span-widening for unattributed lines), Istanbul's coverage map
   already reports statement/branch/function extents directly. Verify this
   holds for real Vitest+`@vitest/coverage-v8` output (the provider isn't
   even installed in `webapp-ui-react` yet — you'll need to add
   `@vitest/coverage-v8` as a devDependency and configure
   `test.coverage.reporter: ['json']` in `vite.config.ts` to produce a real
   artifact to test against) before committing to `False` the way Go's own
   file warns its `False` claim was **"settled, not assumed"** only after a
   real probe (A-172/A-217) — don't skip that step here.
4. **Canary injection shape for JS/TS** (`inject_import_break` /
   `inject_uncovered_line`, needed regardless of R3 registration timing
   since the Protocol requires real implementations, not stubs, for every
   adapter). JS/TS has an executable module top level like Python — a
   top-level `throw new Error(...)` is the direct analogue of Python's bare
   `raise`; a never-called top-level function covers the uncovered-line
   half. Should be mechanical, but confirm against `adapters/python.py`'s
   and `adapters/go.py`'s own implementations for the exact contract
   (placement after leading imports, description string conventions).

### Acceptance

- [x] a new `coverage-istanbul-json` (or equivalently named — match the
      naming decision above) parser registered in `FORMAT_REGISTRY`, parsing
      a real `coverage-final.json` produced by a real `vitest run --coverage`
      against `webapp-ui-react` (or an equivalent minimal fixture project),
      not a hand-written fixture alone;
- [x] a new adapter (`adapters/javascript.py` or matching the naming
      decision) implementing all seven `LanguageAdapter` protocol methods,
      `generate_mutation_sites` returning `"UNSUPPORTED"` (Go's own
      precedent — legal to spell, not yet backed);
- [x] registered in `cli.py`'s `new_registry(...)` for `frozenset({"R1"})`
      only;
- [x] `is_test_path` correctly excludes Vitest's own conventions
      (`*.test.ts(x)`, `*.spec.ts(x)`, `__tests__/`) and `excluded_dir_names`
      excludes `node_modules` and build output dirs at minimum;
- [x] `.d.ts` type-only declaration files are handled correctly by
      `has_executable_code` (they contain no executable code at all — the
      NoCode case, not a coverage gap);
- [x] a real end-to-end test: a lane declaring `judge.language =
      "javascript"`, `R1` only, against a real (fixture or `webapp-ui-react`
      itself) TS/TSX project, correctly reports changed-line coverage
      pass/fail:
- [x] refusal paths covered explicitly: an unrecognised `judge.language`
      value, a malformed/truncated `coverage-final.json`, a `judge.language
      = "javascript"` lane declaring `R2` (must refuse — not registered for
      this build);
- [x] `README.md`/`CONSUMERS.md`/`DESIGN-GUIDE.md` document the new language
      and format the same way Python/Go/SQL already are;
- [x] the real registered gate (`tools/tester-unified-gate.sh`), not just
      `pytest tests/`, run green before this is called done.

---

## B037 — JavaScript/TypeScript mutation rigor (R2): design first, do not implement against this entry directly

> **RESOLVED 2026-08-31 by B046** (Wave B, assay-4.0.0 / schema v9). B037's
> three open decisions were answered by B046's ratified design and are now
> implemented: the lane's own argv runs StrykerJS inside the private snapshot
> and assay ingests the report (neither "shell out" nor "accept a
> CIU-supplied report"); non-repudiation rests on the snapshot's commit
> binding plus four named refusals; and the foreign tool's mutator taxonomy
> is DATA under a `stryker:` namespace assay owns, never a mapping onto
> assay's closed catalogue. `javascript` now registers at `{"R1", "R2"}`
> through the ingested path only — `generate_mutation_sites` is still
> unconditionally `"UNSUPPORTED"`, which is what MAKES it the ingested path.
> Decisions: A-375–A-383, plus A-360/A-361/A-362 from the schema cut.

**Filed 2026-08-30, alongside B036.** Same shape as B020: a scope boundary
and required decisions, not a dispatchable implementation brief.

> **RESOLVED 2026-08-30 (operator ruling; design review
> `reports/assay-3.1-js-adapter-design-review-2026-08-30.md` §5) — the three
> open decisions below are answered by B046, the dispatchable entry:** the
> LANE's own argv runs Stryker inside the private snapshot (neither a shell-out
> by assay nor a CIU-supplied report — R1's own shape), assay reads the
> mutation-testing-report-schema JSON through a FORMAT-keyed mutation registry
> (`judge.mutation.format = "mutation-report-json"` + `artifact`), commit
> binding is the snapshot exactly as for a coverage artifact, statuses map onto
> assay's buckets with `survived_uncovered` and `discarded` kept visible and
> `stryker:<mutator>` admitted as a namespace, `judgment.r2.producer =
> "ingested"` with the producer's identity copied from the report (not
> `helpers[]` — assay did not run it), and yes: this is the precedent for every
> R2 producer assay has no native engine for. The `decisions.md` rows land with
> B046's implementation.

### Scope boundary

Every existing R2 producer (Python, SQL) is **native** — assay parses and
mutates the source text itself, in-process, in Python. Doing the same for
JS/TS/JSX means either shelling out to a real JS/TS parser anyway (assay has
none of its own) or hand-writing a TS/JSX-aware AST layer in Python from
scratch — a categorically larger undertaking than Python's own native
operators (which mutate Python's own AST from within Python) or SQL's
(a purpose-built lexer for one narrow grammar, `sql_lex.py`). This is
plausibly the same class of overreach `ciu/docs/CIU-V8-TESTING-GATE-
PROPOSAL.md` §1.12 already warns against for R4/property-testing generally:
"specialized tools generate cases and produce structured evidence; Assay
validates thresholds, binds evidence to commit/input, emits verdicts and
fails loudly" — never reinventing mature tooling that already exists.

**RULING (operator-delegated product decision, 2026-08-30): evidence-ingestion,
via Stryker Mutator. The native-vs-ingestion fork is resolved; the three
decisions below it are NOT — this item stays design-first for those.**
Reasoning: (1) assay's own charter, restated explicitly in this project's
docs, is a judge that validates and binds evidence — it has never owned a
parser for any language it judges beyond what's needed to locate mutation
sites, and Python/SQL's native operators stay cheap only because Python's own
`ast` module and one narrow SQL grammar are small, stable surfaces assay
already has to touch anyway for other reasons. A TS/JSX-aware mutation engine
has neither property: it is a real, ongoing static-analysis project (grammar
evolution, JSX/TSX surface, type-directed mutants) that would make assay
responsible for tracking TypeScript's own language evolution, categorically
outside what B036 needed to add JS/TS coverage support. (2) This project
already committed to the evidence-ingestion shape for exactly this
complexity tier — CIU proposal §1.12's "specialized tools generate cases...
Assay validates, binds, fails loudly" was written for R4/fuzzing, but the
reasoning transfers without modification to R2 for a language assay has no
native tooling for; there is no principled reason to reinvent for JS what
the project already declined to reinvent for property-testing. (3) Stryker
is the correct specific choice, not merely "an" external tool: first-party
Vitest runner support (the test runner B036 already standardized on),
mature/widely-adopted (lower supply-chain risk than a niche alternative),
and a structured per-mutant JSON report that maps onto assay's own
Killed/Survived/NoCoverage/Timeout taxonomy without inventing new buckets.
**What this ruling does NOT settle, and an implementer must not treat as
answered:** whether assay shells out to Stryker directly or expects a
CIU-orchestrated caller to supply Stryker's report as input (a real design
question given CIU's own gate-preparation model); the exact non-repudiation
binding (commit, judge provenance, anti-"exit-status-as-proof") Stryker's
output needs before assay will trust it; and whether this sets a precedent
other future non-native R2/R4 producers should follow or is JS-specific.
Record the actual answers as decisions.md entries when this is implemented,
the same way every other design call in this backlog is recorded — this
ruling closes the architectural fork, not the implementation.

### Required design decisions before implementation

- ~~native (hand-rolled, matching every existing R2 producer) vs.
  evidence-ingestion (Stryker or equivalent, a new pattern for assay) —
  the central ruling this item exists to force~~ **RESOLVED above:
  evidence-ingestion via Stryker Mutator.**
- how a foreign tool's report is bound to a verdict
  with the same non-repudiation properties assay's own native producers
  have today (commit binding, judge provenance per B018, no "process
  returned zero" weakening — CIU proposal §1.10's own standing objection to
  trusting exit status as proof applies here with double force for a
  THIRD-PARTY process's exit status);
- operator vocabulary: does a foreign tool's mutator taxonomy map cleanly
  onto assay's own `mutation.*` bucket model (survived/killed/equivalent/
  budget_exceeded), or does it need its own schema shape;
- relationship to B018 (judge provenance) now that evidence-ingestion is
  the ruled direction — whose identity is recorded, assay's or Stryker's,
  or both.

### Acceptance

- [x] the central native-vs-evidence-ingestion fork is ruled (above:
      evidence-ingestion via Stryker Mutator) — this is a scope/direction
      decision, not an implementation, and does not by itself authorize
      code;
- [x] the three remaining design decisions above are written up and
      reviewed against every relevant standing constraint — written up in
      B046 (2026-08-30) against A-161/A-007/A-230a/the north-star's tier
      rule; B046's adversarial review is the review;
- [x] no implementation lands until those three are recorded — recorded in
      B046; the `decisions.md` rows are part of B046's own acceptance.

---

## B038 — `coverage-istanbul-json`: real branch arcs, and the type-only-module gap, once a producer can be declared

> **RESOLVED 2026-08-31 by B045** (Wave B, assay-4.0.0 / schema v9). Both
> halves shipped once the producer became declarable.
> **(a)** real branch arcs under `producer = "istanbul"` — decided as
> **A-356** (arcs key per-ARM with an entry-line fallback, departing from
> `istanbul-lib-coverage`'s entry-only reduction because real output writes
> lineless implicit-`else` arms) and **A-357** (an unrecognised `branchMap`
> entry type REFUSES the artifact rather than degrading silently).
> **(b)** the type-only-module gap — decided as **A-358** (the lexer's exact
> scope, its all-or-nothing rule, and its stated limitation; anything it does
> not recognise answers "has code", fail-closed).

**Filed 2026-08-30 by B036's own implementation, from measured evidence.**
Two consequences of one root cause: `coverage-final.json` is a format with
several producers that DISAGREE about the meaning of parts of it, and a lane
declares the format, never the producer.

### (a) branch arcs are reported `unavailable`, and should not have to be

`coverage_parsers/coverage_istanbul_json.py` returns `branches=None`
unconditionally (A-344). Measured on one source file
(`tests/fixtures/coverage/probe-js/src/branchy.ts`, two `if`s and a ternary,
one test):

- `@vitest/coverage-istanbul` — three `branchMap` entries typed
  `if`/`if`/`cond-expr`, one location per ARM, one count per arm:
  **6 arcs, 2 covered**;
- `@vitest/coverage-v8` — four entries all typed `"branch"`, each with
  exactly ONE location and ONE count, describing v8's own executed/unexecuted
  RANGES (one spans the whole function, another begins at a closing brace):
  **4 "arcs", 1 covered**.

Both artifacts are committed. A single translation cannot be honest for both,
so `None` was chosen over a number whose meaning depends on an undeclared
fact. The consequence for consumers: `require_branch = true` refuses on a
JavaScript lane today.

### (b) a type-only `.ts` module is a false failure under one provider

`JavaScriptAdapter.has_executable_code` answers `True` for a module holding
only `export type`/`interface` (A-343), because deciding otherwise needs real
TypeScript type-erasure semantics. Under `@vitest/coverage-v8` this never
surfaces — such a module IS reported, with an empty `statementMap`, so the
method is never consulted (measured: `probe-js/src/typesonly.ts`). Under
`@vitest/coverage-istanbul` the module is absent from the artifact entirely,
so a changed type-only module is reported as missing coverage.

### The decision this item exists to force

Should a lane be able to declare its PRODUCER (a `judge.coverage.provider`
key, a second registry format key such as `coverage-istanbul-json-v8`, or a
derivation from the artifact's own shape), and if so which mechanism? Both
halves above dissolve the moment the producer is a declared fact rather than
an inferred one; neither can be fixed honestly while it is not. Deriving the
producer by sniffing (e.g. "every `branchMap` entry is typed `"branch"` with
one location") is the obvious shortcut and is exactly the
declaration-versus-sniffing collapse `coverage.py`'s own module docstring
forbids (A-007) — it must be argued explicitly if it is chosen, not slid in.

> **RULED 2026-08-30 → B045** (operator ruling, design review
> `reports/assay-3.1-js-adapter-design-review-2026-08-30.md` §5): a
> `judge.coverage.producer` key, declared per lane from a closed PER-FORMAT
> vocabulary and recorded in the verdict as `judgment.r1.coverage_producer`
> (schema v9, one bundled cut). Sniffing rejected; a second format name
> rejected (it would bind a trust property to a format). (a) real arcs under
> `producer = "istanbul"` and (b) the narrow fail-closed type-only lexer are
> B045's acceptance; this item closes when B045 ships.

### Acceptance

- [ ] a recorded decision on how (or whether) a producer becomes a declared
      fact, argued against A-007;
- [ ] if declared: real `BranchCoverage` for the arc-bearing producer, with
      the `FileCoverage` cross-bucket invariants holding on both committed
      real artifacts, and `branch_capability` still `"unavailable"` for a
      producer whose `branchMap` is not arcs;
- [ ] if declared: the type-only-module case decided for the
      istanbul-provider path without a hand-written TypeScript parser;
- [ ] `README.md`/`CONSUMERS.md`'s current "leave `require_branch` unset on a
      JavaScript lane" guidance updated in step.

---

## B039 — `go_cover.parse` expands a block's line range with no fixed bound

**Filed 2026-08-30 by B036's own implementation, in passing.** Noticed while
writing the equivalent expansion for `coverage-istanbul-json`, which was
given a bound (`MAX_CLASSIFIED_LINES`, O4's "a fixed bound, never an ambient
guess") precisely because the shape is dangerous.

> **2026-08-30:** scheduled as B047 item 4 (Go wave prep) and pulled forward
> into Wave A (`WAVE-PROMPT-2026-08-30-js-consumer-producer.md` step 4).
> Acceptance box 2 is answered: ONE shared place, `coverage_parsers/model.py`,
> used by both expanding parsers.

`coverage_parsers/go_cover.py`'s `parse` runs
`for file_line in range(start, end + 1)` over every block, with `start`/`end`
read straight from the artifact and validated only for positivity and
ordering (`_parse_pos`, `_parse_block`). A single ~50-byte block line reading
`pkg/x.go:1.1,999999999.1 1 1` sits far inside the 16 MiB
`MAX_COVERAGE_ARTIFACT_BYTES` read bound and materializes a billion dict
entries.

This is a resource-exhaustion shape, not a correctness one, and it is behind
a lane that must already declare `go-cover` as its format — but assay's own
threat model treats a coverage artifact as potentially adversarial input in
the same breath as `FileCoverage`'s three independent arrays (P15/A-067
finding 4), so "the input is trusted" is not the answer this project gives
elsewhere.

### Acceptance

- [x] a fixed, documented ceiling on total classified lines per artifact in
      `go_cover.parse`, refusing `ERROR`/`UNREADABLE_ARTIFACT` past it, with
      a paired must-succeed control proving an ordinary profile still parses
      — `go_cover.py:230-232` (`budget.spend` before the range expansion),
      `tests/test_coverage_parsers_go_cover.py`
      `test_one_enormous_block_is_refused_rather_than_expanded` +
      `test_an_ordinary_real_shaped_profile_still_parses_under_the_shared_bound`
      (2026-08-30, Wave A);
- [x] check whether the bound belongs in ONE shared place rather than once
      per expanding parser (`coverage_istanbul_json` has its own today) —
      moved to `coverage_parsers/model.py:MAX_CLASSIFIED_LINES` +
      `ClassifiedLineBudget` (2026-08-30, Wave A); both parsers re-export the
      name into their own module namespace so each module's own
      `monkeypatch.setattr(<module>, "MAX_CLASSIFIED_LINES", ...)` test
      idiom keeps working —
      `test_coverage_parsers_go_cover.py::test_the_shipped_bound_is_the_one_shared_documented_value`
      pins object identity, not merely equal values. See A-348.

---

## B040 — `@vitest/coverage-v8` reports never-executed lines as executed, and assay cannot detect it

> **(b) RESOLVED 2026-08-31 by B045** (Wave B, assay-4.0.0 / schema v9),
> decided as **A-353**: the three v8-remapping producers are SPELLABLE so the
> refusal can name them, and are refused at LOAD by name, before any lane
> runs — with the three grounds kept deliberately distinct rather than
> blurred (`vitest-v8` and `c8` refused on MEASURED evidence, `jest-v8`
> refused as unmeasured-and-therefore-unproven). "That is not a known
> producer" is a much weaker message than "that producer is known, measured,
> and unsound; here is the fix", and the loader now emits the second.

**Filed 2026-08-30 by B036's round-1 adversarial review, reproduced and ruled
on as A-346 before filing.** The ruling (documentation names
`@vitest/coverage-istanbul` as the only judged-safe Vitest provider) is
shipped and is a mitigation, not a fix. This item owns the two things the
ruling deliberately did not do.

### The defect, measured

Ground truth needs no coverage tool here: five functions, each guarded by
`if (v === 0) return 0` on its second line, one test calling each with `0`.
Every line below a guard provably never runs.
`tests/fixtures/coverage/probe-js-provider-defect` is that project; four real
artifacts are committed (both providers × Vitest 3.2.4 and 4.1.11).

`@vitest/coverage-v8` reports lines below the guard as **executed** whenever a
conditional (ternary) expression appears earlier in the same block. Through
the shipped `evaluate_coverage`, with those lines as the diff and
`fail_under = 100.0`: **PASS at 100.0%** under v8, **FAIL at 0.0%** under
istanbul.

Four facts, each measured, that make this more than a caveat:

- reproduces on **both** currently-released Vitest majors — no version to pin
  past;
- a **one-line** ternary triggers it, not just a multi-line one — no
  formatting rule avoids it;
- `coverage.experimentalAstAwareRemapping = true` does **not** fix it;
- a multi-line binary expression, call and object literal are all correct in
  the same artifact from the same provider — so unsound records are not
  structurally distinguishable from sound ones.

`@vitest/coverage-istanbul` is correct on every case measured, as are
nyc/istanbul and Jest, which share its instrumenter.

### (a) report it upstream

Not yet done. The minimal reproduction is
`probe-js-provider-defect/src/shapes.ts` plus its one test; the two committed
v8 artifacts are the evidence. Worth checking first whether it is already
filed against `ast-v8-to-istanbul` (the remapping layer, which is where the
defect most likely lives — v8's own range data is not obviously wrong; the
mapping of ranges onto statement lines is).

### (b) the standing question this shares with B038

Should a lane be able to declare its coverage PRODUCER? B038 wants it for
branch arcs. This item wants it for a much sharper reason: with a declared
producer, assay could refuse a `coverage-istanbul-json` lane that names the v8
provider outright, instead of relying on a consumer having read a warning.
Without one, assay cannot tell the two apart — and **must not try**: nothing
in the document separates a true `s` count from a false one (`fnMap`/`f`
corroborate the lie, because the enclosing function really did run), so the
only mechanical route is inferring the producer from the artifact's shape,
which is the declaration-versus-sniffing collapse A-007 forbids and which
would already have broken between the two versions measured here (Vitest 4's
v8 provider emits multi-line extents where Vitest 3's emitted single-line
ones).

### Acceptance

- [ ] the defect reported upstream (or an existing report found and linked),
      with the committed reproduction;
- [ ] `test_coverage_istanbul_provider_accuracy.py`'s defect-witness tests
      re-checked against any newer provider release — they read four
      COMMITTED fixtures and cannot go red on their own, so this is a manual
      step: regenerate `probe-js-provider-defect`'s v8 fixtures against the
      new release and re-run; **if that makes them FAIL, the defect is
      fixed**, and that is this item's completion signal, not a test to
      relax;
- [ ] if a producer becomes declarable (B038): a `javascript` lane declaring
      the v8 provider is refused by name, and A-346's documentary mitigation
      is downgraded to a note about older configurations.

> **2026-08-30 — the producer question is RULED: B045.** Operator decision
> (design review, `reports/assay-3.1-js-adapter-design-review-2026-08-30.md`
> §5): a lane declares `judge.coverage.producer`, the verdict records it, and
> the v8 provider is refused by name at load. (a) above stays this item's own;
> (b) is discharged by B045's acceptance.

---

## B041 — a JavaScript lane's dependency closure (`node_modules`) is absent from the committed-object snapshot: the offline-install pattern, `isolation.link_paths`, and a real-`vitest` qualification

**Filed 2026-08-30 from the 3.1.0 design review**
(`reports/assay-3.1-js-adapter-design-review-2026-08-30.md` §3 G1).
**Operator ruling 2026-08-30: BOTH halves — (a) the documented offline-install
pattern with a real-`vitest` qualification, AND (b) a declared
`[lanes.<n>.isolation] link_paths` feature — with detailed consumer usage
documentation.** (a) and the docs ship on the current schema; (b) records
itself in the verdict and therefore rides the v9 cut (B045's wave).

### Mechanism, confirmed in source — not assumed

- `isolation.py:577` materialises every R1/R2/R3 snapshot with
  `git read-tree <commit>` into `tempfile.mkdtemp(dir=scratch_root,
  prefix="assay-p22-snap-")` (`isolation.py:492`). Only tracked blobs exist
  there; a gitignored `node_modules/` is absent by construction (A-161/A-184:
  committed objects, never the working tree). This is correct and must stay.
- The lane command runs with `cwd=snapshot.project_root` (`runner.py:1754`).
- `tests/test_cli_run_javascript.py:88,131`: the end-to-end lane's argv is
  `/bin/sh -c "cat > coverage-final.json <<EOF …"` — the producer is a test
  double (A-334's own definition). The real Vitest artifacts (`probe-js`,
  `probe-js-provider-defect`) were produced OUTSIDE assay (REPORT §2). **No
  real `vitest` has ever run inside an assay snapshot.**
- The first consumer's runner image ships Node
  (`dstdns/tools/test-runner/Dockerfile:17-43`) and `npm ci`s into the
  bind-mounted checkout at gate time; inside the snapshot there is no
  `node_modules`, so `npx vitest run --coverage` does not fail loudly — `npx`
  FETCHES a missing package from the registry (unpinned, network) unless
  `--no-install` is passed, and then fails with a resolution error. Either way
  no artifact lands at the declared path: `NO_MEASUREMENT` at best, an
  unpinned toolchain at worst.
- `environment_command` (B010) cannot vouch for the closure: it runs in the
  INVOKING environment before any snapshot work (DESIGN-GUIDE §4), so
  `node -e "require.resolve('vitest')"` succeeds in the checkout while the
  snapshot has nothing.
- Why this is new: Python's closure is a venv and Go's is `GOMODCACHE`, both
  out-of-tree, so neither adapter ever met an in-tree closure. R3 triples the
  cost (baseline + two canary runs, each a fresh snapshot).

### (a) The honest default: closures come from the image; the snapshot rebuilds the in-tree closure OFFLINE from the committed lockfile

Same doctrine as the image-baked judge (B009): the gate image carries an npm
cache populated from the committed `package-lock.json` (at image build:
`npm ci --cache /opt/npm-cache --prefix <app>` then discard the tree, or a
persistent `~/.npm/_cacache` provided by the environment — ciu v8
`[testing.environments.<e>] extra_mounts`, CIU-73; on the current gate,
`RUN_GATE_EXTRA_MOUNTS` for ephemeral lanes or the runner stack's own
image/volume for exec lanes — run-gate `CONSUMERS.md`, with RG-25/RG-26 as
the run-gate-side backports of CIU-72). The lane's argv starts
with the offline install, then the pinned runner:

```toml
[lanes.ui_unit]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["bash", "-c",
  "npm ci --offline --no-audit --no-fund --prefix applications/webapp-ui-react && npx --no-install --prefix applications/webapp-ui-react vitest run --coverage --root applications/webapp-ui-react"]
env = { npm_config_cache = "/opt/npm-cache", CI = "1" }
env_passthrough = ["PATH", "HOME"]
budget = "15m"
allow_argv_append = false

[lanes.ui_unit.isolation]
snapshot_selection = "repository"

[lanes.ui_unit.judge]
language = "javascript"
source_roots = ["applications/webapp-ui-react/src"]
fail_under = 100.0
allow_excluded = false
base_source = "request"

[lanes.ui_unit.judge.coverage]
format = "coverage-istanbul-json"
artifact = "applications/webapp-ui-react/.assay/coverage-final.json"
```

Properties: `--offline` fails loudly when the cache lacks anything (no silent
network); `--no-install` makes a missing runner a refusal, never a fetch; the
lockfile is committed, so the closure is reproducible modulo the image, which
`judge_provenance`/the ciu image pin already identify. The `bash -c` wrapper
goes away with B043 (`cwd`). Budget is the consumer's measurement (the docs
say to measure it, not a number).

### (b) The declared speed path: `[lanes.<n>.isolation] link_paths`

```toml
[lanes.ui_unit.isolation]
snapshot_selection = "repository"
link_paths = ["applications/webapp-ui-react/node_modules"]
```

Contract, each rule with its refusal:
1. every entry is a repo-relative forward-slash path with no `..`, no leading
   `/`, resolving to a DIRECTORY in the invoking checkout — absent →
   `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL` naming the path (the closest
   existing meaning: a declared prerequisite the environment did not
   provide; a dedicated code may ride v9 if the carve prefers one);
2. the entry must NOT be a tracked path at the resolved commit
   (`git ls-tree` lookup): linking a tracked path would silently replace
   committed content with working-tree content — `ERROR`/`BAD_LANE_CONFIG`;
3. materialised as a symlink `snapshot/<path> -> <checkout>/<path>`
   immediately after `read-tree` (`isolation.py:577`), before any command,
   for every snapshot the lane creates (R3 canary snapshots included);
4. the symlink is created only if `<path>`'s parent exists in the snapshot
   (tracked), else refused as (2) — never `mkdir -p` into the snapshot;
5. recorded in the verdict as `snapshot_policy.link_paths` (sorted), so a
   verdict states plainly that its snapshot was not purely committed
   objects — **schema v9**, registered in `verify.py` too (the third place,
   per the 2.4.0 lesson);
6. snapshot teardown must remove the LINK, never its target — the
   destructive failure to pin: a test plants a canary file in the target and
   proves it survives teardown; a wrong implementation using a following
   `rmtree` deletes the consumer's `node_modules`.

`excluded_dir_names` already excludes `node_modules` from judging, the link is
not a tracked path so the diff never sees it, and istanbul keys under it are
inert (A-341). `dirty_paths()` on the checkout is unaffected (snapshot-side).
Trade-off stated in the docs: faster, but the closure is whatever the checkout
holds — the honest default is (a).

### (c) Qualification: a real `vitest` inside a real snapshot

`tester-unified` deliberately has no Node (DESIGN-GUIDE §10), so this cannot
be a registered-gate test. It is a **qualification harness** in the P25 shape:
`tests/qualification/test_javascript_real_vitest.py`, skipped unless
`ASSAY_NODE_QUALIFICATION=1` and `node`/`npm` are on PATH, which builds an
npm cache from `tests/fixtures/coverage/probe-js/package-lock.json`, drives a
lane of shape (a) through the real CLI in a two-commit git fixture, and asserts
the PASS/FAIL verdict pair. Its transcript is pasted into the wave REPORT
(A-335: run the thing production runs, the way production runs it), and the
first dstdns lane's real verdict is the live qualification. The gate-runnable
tests own the `link_paths` mechanics with a fake directory (rules 1–6).

### Consumer-side actions (dstdns) — kept here so they are not lost

See the review report §7: provider + jsdom/testing-library install,
`vitest/config` import, `reportsDirectory: '.assay'` under the app, gitignore,
the baked npm cache in `tools/test-runner`, the `ui_unit` lane, and the one
type-only module (`src/auth/types.ts`, B038(b)).

### Acceptance

**Wave A discharges (a)+(c) only** (`WAVE-PROMPT-2026-08-30-js-consumer-
producer.md`'s own explicit split — this item's acceptance boxes were
written assuming one implementer might do (a)+(b) together; the wave prompt
supersedes that for THIS wave). (b) `link_paths` remains open for Wave B.

- [x] `docs/CONSUMERS.md`: a new section "JavaScript lanes and the dependency
      closure" carrying the mechanism, pattern (a) with the worked monorepo
      lane above, the image-side cache recipe, the `npx` fetch hazard and
      `--no-install`, the `environment_command` caveat, and the R3 cost —
      landed 2026-08-30. Pattern (b) is a ONE-PARAGRAPH PREVIEW marked
      "Wave B, schema v9" per the wave prompt's own instruction, not the
      full purity-trade-off write-up this box originally asked for (that
      lands with the real implementation in Wave B); README's JS section
      links to it (both directions, B042 item 5);
- [x] the qualification harness in (c) exists, is skipped in the registered
      gate with a named reason, and its transcript (PASS and FAIL runs) is in
      the wave REPORT — `tests/qualification/test_javascript_real_vitest.py`
      (2026-08-30); `pytestmark = pytest.mark.skipif(...)` names the reason
      (`ASSAY_NODE_QUALIFICATION=1` + node/npm on PATH; tester-unified has no
      Node, DESIGN-GUIDE §10); both transcripts are in
      `reports/assay-WAVE-A-js-consumer-REPORT.md`. Running it for real
      surfaced a genuine, previously-unknown defect (B049/A-347: Vitest's
      default `coverage.clean = true` orphans assay's own coverage-artifact
      reservation) — not anticipated by this item's own text, and the kind
      of finding only a real run (not a heredoc) can surface;
- [x] `link_paths` implemented per rules 1–6 with a gate-runnable test per
      rule, including the teardown-preserves-target canary — **Wave B,
      2026-08-31**. `IsolationConfig.link_paths` + grammar/bound/order checks;
      `SnapshotRepository._plant_link_paths`, called from `_build` AFTER
      `_verify` (A-370) so the committed-objects-only proof stays true of
      exactly the committed objects, and so BOTH materialisation entry points
      (`materialize` and `materialize_replacement`, an R2 mutant's own) carry
      the link structurally. `tests/test_isolation_link_paths.py`, 32 nodes.
      Rule 6 is proven EMPIRICALLY (A-373), never by "stdlib `rmtree` unlinks
      symlinks": a real symlink to a real directory outside the snapshot, a
      canary file inside it, and the canary's BYTES asserted after teardown on
      the success path, on `_materialize`'s exception path, and against
      `_remove_owned_tree` directly. **A FIFTH refusal the contract does not
      list** was needed (A-371): a link must be covered by a COMMITTED
      `.gitignore`, or the runner's own dirt check reports it as `DIRTY_TREE`
      after the lane's command — blaming the command for something the lane
      declared. And a MEASURED finding (A-372): a trailing-slash rule
      (`node_modules/`) does NOT cover the link, because git treats such a
      pattern as directory-only and does not count a symlink as a directory;
- [x] `snapshot_policy.link_paths` in the schema, the dataclass and
      `verify.py`; the frozen drift-guard asset updated at the v9 cut —
      landed with the cut (`af14021f`/`1577fa45`); `_verdict_snapshot_policy`
      populates it here (omitted, never empty — A-051);
- [x] `assay lanes --json` (B044) exposes `link_paths` — now emits the real
      declared list; `inventory_schema` stays `1`, since it changes when an
      EXISTING key's meaning changes, never because a key gained real values;
- [ ] R3 for `javascript` wired in `cli.py`'s registry ONLY after the
      qualification harness has run a real canary pair, never before — NOT
      done this wave (out of scope per the wave prompt's own "NOT IN SCOPE"
      list: "registering javascript at R2 or R3"); the qualification harness
      shipped this wave proves R1 only, so this box's own precondition is
      not yet met regardless.

---

## B042 — JavaScript consumer documentation: the worked lane is not a monorepo lane, "Jest is unaffected" is an overclaim, and support files are not test paths

**Filed 2026-08-30 from the 3.1.0 design review (§3 G2/G3, §6).** Docs only;
ships on the current schema. Every item below names its file:line and its
replacement so the change is mechanical.

1. **Worked lane (`docs/CONSUMERS.md:543-571`).** `argv = ["npm", "run",
   "test:coverage"]` runs at the repository root, where the first consumer has
   no `package.json`, while `source_roots = ["applications/webapp-ui/src"]`
   names a monorepo app; Vitest's `reportsDirectory` resolves against the
   app root, so `artifact = ".assay/coverage-final.json"` is the wrong path
   for that layout. Replace with B041's worked lane (offline install +
   `--no-install` runner + `applications/<app>/.assay/coverage-final.json`),
   and say in one sentence that a root-level app keeps the short form. Once
   B043 lands, show `cwd = "applications/webapp-ui-react"` instead of the
   `bash -c` wrapper.
2. **Jest scope (`README.md:191`, `docs/CONSUMERS.md:676-678`).** "nyc/istanbul
   and Jest are unaffected" is true only of Jest's default `coverageProvider:
   "babel"`. Jest `coverageProvider: "v8"` and `c8` write the same
   `coverage-final.json` through v8-to-istanbul remapping and were NOT
   measured. Replace with: "`nyc`/`istanbul` and Jest with its default
   `babel` coverage provider share `@vitest/coverage-istanbul`'s instrumenter
   and are unaffected. Jest's `coverageProvider: "v8"` and `c8` remap v8
   ranges the same way the defective provider does and have not been
   measured — treat them as unsafe until a committed witness says otherwise."
   If the implementer can reproduce the `probe-js-provider-defect` project
   under `c8` cheaply, commit the artifact and state the result instead.
3. **Support files (`docs/CONSUMERS.md:680-693`).** Add: `*.stories.tsx`,
   `src/test/setup.ts`, `vitest.setup.ts` and `*.config.*` are NOT test paths
   by the adapter's rule (Vitest's own `include` glob is the citation), and
   Vitest's default `coverage.exclude` drops config files from the artifact,
   so a changed one under a declared source root is reported as uncovered
   (fail-closed, visible). Keep them out of `source_roots`.
4. **`README.md:169-178` snippet.** Say `vitest/config` (not `vite`) is the
   `defineConfig` import that accepts a `test:` block; the CONSUMERS snippet
   already does.
5. **Cross-links.** README's JS section → B041's new CONSUMERS section;
   CONSUMERS "Practices" gains "Dependency closures come from the image,
   never the working tree" as a one-paragraph rule (B041 (a)).

### Acceptance

- [x] items 1–5 landed with the exact wording or better, each checked
      against the current files (line numbers above are 3.1.0's) — item 1
      discharged by B041(a)'s new CONSUMERS section (the old worked lane at
      the cited lines is replaced, monorepo-shaped, offline-pattern
      example); item 2 landed in `README.md`/`docs/CONSUMERS.md`'s v8-
      provider warnings, WITH a real, measured `c8` result (2026-08-30) —
      not left "untested": `tests/fixtures/coverage/probe-js-provider-
      defect-c8/`, `coverage-istanbul-json.provider-defect.c8.json`,
      `test_coverage_istanbul_provider_accuracy.py`'s `C8`/
      `C8_FALSE_GREENS` cases; item 3 landed as `docs/CONSUMERS.md`'s new
      "support files" paragraph, corrected past the backlog's own assumed
      mechanism (measured: it is `coverage.include`'s zero-coverage
      synthesis for a matched-but-unimported file, never a
      `*.config.*`/`*.stories.*` exclude glob — Vitest's own hardcoded
      excludes cover only its resolved config file, the test-name glob and
      declared setup files); item 4 landed in `README.md`'s snippet comment;
      item 5 landed both directions (README → CONSUMERS' new section;
      CONSUMERS "Practices" gained "Dependency closures come from the
      image, never the working tree");
- [x] no doc still says the two Vitest providers are interchangeable or that
      Jest is unconditionally unaffected (grep for "Jest" in README/CONSUMERS/
      DESIGN-GUIDE/the parser docstring) — re-grepped 2026-08-30 after every
      edit above; every remaining "Jest" mention is scoped to its default
      `babel` provider, and `src/assay/coverage_parsers/coverage_istanbul_json.py`'s
      own module docstring (the one hit outside README/CONSUMERS) corrected
      to match.

---

## B043 — a lane-level `cwd`: the command's working directory as a declared, recorded fact

**Filed 2026-08-30 from the 3.1.0 design review (§3 G2).** Schema v9 (the
verdict must witness it) — rides B045's wave.

### Problem

The lane key set is closed (`config.py:139-155`) and has no working-directory
key; every command runs at the snapshot root (`runner.py:1754`). A monorepo
app's package script (`npm run …`, `go test ./...` inside a module, `cargo`)
therefore needs `argv = ["bash", "-c", "cd applications/x && …"]` — dstdns's
`run-gate.toml` already carries eleven such wrappers. The wrapper (1) hides the
real `argv[0]` from the `MISSING_EXTERNAL_TOOL` preflight (it checks `bash`,
not `npm`), (2) makes `allow_argv_append` meaningless for the inner command,
(3) puts shell quoting between the lane file and what ran, which
`argv_effective` then records opaquely.

### Contract

- `cwd = "applications/webapp-ui-react"` — repo-relative, forward-slash, no
  `..`, no leading `/`, must resolve to a tracked DIRECTORY at the resolved
  commit (so it exists in every snapshot) — otherwise `ERROR`/`BAD_LANE_CONFIG`
  at load, naming the path and the commit. Symlink components are refused
  through the existing P22 containment gates.
- Applied as `cwd=snapshot.project_root / cwd` for the lane command and for
  every re-execution (R2 candidates, R3 canaries) — one place, so
  `resolve_command_plan` and the mutation executor cannot disagree.
- **Nothing else re-roots.** `judge.coverage.artifact`, `equivalence_artifact`,
  `infrastructure` facts, `source_roots`, `targets` stay project-root-relative
  (one path grammar, A-271). The docs say so in the key's own paragraph.
- Recorded in the verdict as `cwd_declared` beside `argv_declared` (absent
  when not declared — absent, never `"."`), registered in `verify.py`.
- `environment_command` keeps running in the invoking environment's cwd; it
  is not the lane command.

### Acceptance

**SHIPPED 2026-08-31 (Wave B, assay-4.0.0 / schema v9).**

- [x] loader accepts/refuses per the contract with a test per refusal —
      `config._load_lane_cwd`; `tests/test_config_lane_cwd.py` (19 nodes).
      One deviation from the contract's wording, recorded as A-368: the
      "tracked at the resolved commit" half CANNOT be checked at load (there
      is no resolved commit when `assay.toml` is read), so it is checked by
      `runner._execute_snapshot_unit` against the materialised snapshot,
      where the commit is known and is named in the message. The loader
      checks the grammar, containment after symlink collapse, and that the
      path is a directory in the invoking checkout;
- [x] the command, every R2 candidate execution and every R3 canary run use
      the same resolved cwd (one test each, proven by a command that writes
      `$PWD` to its artifact) — `tests/test_runner_lane_cwd.py`, where the
      oracle is a real `/bin/sh` command appending its own `$PWD` to a log
      outside the snapshot. Stronger than "one test each": there is ONE join,
      on the resolved `CommandPlan` inside `execute_plan` (A-367), so the
      four sites agree structurally rather than by four tests happening to
      pass. Two negative controls: a lane with no `cwd` runs at the project
      root, and the `environment_command` probe keeps the invoking cwd;
- [x] schema/dataclass/`verify.py` carry `cwd_declared`; drift-guard updated —
      landed with the cut (`af14021f`/`1577fa45`); `assemble_verdict` (the
      single verdict construction site) populates it here;
- [x] CONSUMERS' JS worked lane and the SQL lane example use `cwd` where the
      wrapper was; `assay lanes --json` (B044) exposes it — the JS worked
      monorepo lane now declares `cwd` instead of its `bash -c "cd … && …"`
      wrapper, and there is a new section for the key with a TABLE of what
      does not re-root. **The SQL example had no wrapper to retire** (its
      argv is `["scripts/schema-gate.sh"]`, already project-root-relative),
      so nothing there changed; `lanes --json` emits the real value.

---

## B044 — `assay lanes --json`: a machine-readable lane inventory for gate tools

**Filed 2026-08-30 from the 3.1.0 design review (§4).** No schema coupling;
ships on the current schema. Companion to ciu CIU-72 (v8 gate) AND run-gate
RG-25/RG-26 (the current gate — both consume this verb, so it is the
prerequisite for the v7-era backports as much as for v8).

### Problem

`assay lanes` prints text (`cli.py:125-140`, dispatch at `cli.py:255`). CIU v8
(SPEC S16, S15.3 stage 12) deliberately reads `assay.toml` for lane names
only, so the gate cannot know that a `javascript` lane needs Node in its
environment, that a future Go lane needs the statement-position helper on
PATH, or that a lane delegates its base — the consumer restates the last one
as `[testing.lanes.<l>] request_base = true`, a second spelling of one fact
(the proposal's own P1). Asking the judge is not reading the file.

### Contract

`assay lanes --json [--file PATH]` writes one JSON document to stdout:

```json
{"inventory_schema": 1, "assay_version": "3.2.0", "lanes": [
  {"name": "ui_unit", "scope": "S1", "rigor": ["R0","R1"], "enforcement": "gate",
   "language": "javascript", "rigor_reachable": ["R1"],
   "coverage": {"format": "coverage-istanbul-json", "artifact": "applications/webapp-ui-react/.assay/coverage-final.json", "producer": null},
   "mutation": null, "canary": null,
   "base_source": "request", "external_tools": [], "argv0": "bash",
   "env_required": [], "environment_command": false, "infrastructure_facts": [],
   "budget": "15m", "cwd": null, "link_paths": [], "snapshot_selection": "repository"}]}
```

- every field has ONE producer: the loaded `Lane`/`JudgeConfig` and the
  registry entry (`rigor_reachable` = the levels THIS build wires for the
  language, `registry.py`); nothing is re-derived from the TOML text;
- `external_tools` = the adapter's declared tuple, plus (after B047) the Go
  helper; `argv0` lets a gate preflight the real first token;
- a lane file that fails to load → exit 2 with the loader's message and NO
  partial document; `--json` never emits both text and JSON;
- `inventory_schema` is bumped only when a key changes meaning; adding keys
  (B043 `cwd`, B041 `link_paths`, B045 `producer`) is additive.

### Acceptance

- [x] golden JSON for every fixture lane file under `tests/fixtures/lanes/`
      (or the existing lane fixtures), including a `javascript`, a `sql` and
      a delegating lane — `tests/test_cli_lanes_json.py` (2026-08-30, Wave
      A): `test_an_r0_only_lane`, `test_a_python_r1_lane_with_a_declared_base`,
      `test_a_javascript_r1_lane_that_delegates_its_base`,
      `test_a_sql_r2_lane`, plus a standalone
      `test_a_lane_delegating_its_base_records_base_source_request` reusing
      B019's own `_delegating_r1_lane()` pattern;
- [x] a refusal test (bad lane file → exit 2, empty stdout) —
      `test_a_lane_file_that_fails_to_load_exits_two_with_no_json_on_stdout`,
      `test_a_missing_lane_file_exits_two_with_no_json_on_stdout`;
- [x] CONSUMERS "CMRU / tester-unified integration" shows a gate consuming
      it — `docs/CONSUMERS.md#preflighting-a-gate-environment-with-assay-lanes---json-b044`
      added (2026-08-30, Wave A), showing the exact document shape and how a
      gate reads `rigor`/`rigor_reachable`, `language`, `base_source`,
      `external_tools`/`argv0`, `environment_command`.
- [ ] (split out 2026-08-30, Wave A review round 1 — was incorrectly folded
      into the box above as done) the ciu handoff note itself: CIU-72,
      already filed in ciu's own backlog by the design review that filed
      this item, still needs to be WORKED, not just filed — out of scope
      here (Wave A touches `assay/**` only); flagging for the controller/ciu
      owner to cross-reference the exact key names in the box above when
      CIU-72 is worked.

---

## B045 — declare the coverage PRODUCER: `judge.coverage.producer`, recorded in the verdict (schema v9); closes B038(a)(b) and B040(b)

**Filed 2026-08-30 from the 3.1.0 design review (§4 D1). Operator ruling
2026-08-30: one bundled v9 cut with B046/B043/B041(b) — assay 4.0.0.** This
is the decision B038 and B040 exist to force, argued against A-007 as they
require: a producer becomes a **declared** fact, never a sniffed one.

### Why a declaration and not a per-producer format

`coverage-istanbul-json` is one format with several producers that disagree
about (i) what `branchMap` means (A-344) and (ii) whether a line ran (A-346).
Registering `coverage-istanbul-json-v8` as a second FORMAT would bind a
trust property to a format name — and the same document from nyc, Jest-babel
and `@vitest/coverage-istanbul` would then need three names for one shape.
The producer is a fact about the lane's toolchain, like `judge.language`; it
belongs beside the format, and the verdict must witness it (the B035 lesson:
a judgment that cannot state its own inputs cannot be verified).

### Contract

- `[lanes.<n>.judge.coverage] producer = "<name>"`, a closed vocabulary PER
  FORMAT, kept in `vocabulary.py` beside the operators:
  - `coverage-istanbul-json`: `istanbul` (babel-plugin-istanbul family:
    nyc/istanbul, Jest `babel`, `@vitest/coverage-istanbul`,
    `vite-plugin-istanbul`), `vitest-v8` (**refused at load by name, A-346**),
    `jest-v8`, `c8` (**refused until a committed witness clears them**, B042
    item 2). REQUIRED for this format — its producers disagree, so no
    implied value is correct in every context (DESIGN-GUIDE §5).
  - `coverage-py-json`: `coverage.py` — optional, the only producer; if
    present must equal it.
  - `lcov`, `cobertura`: optional; vocabulary opened by the first consumer
    that needs one (no speculative names — §5).
  - `go-cover`: `go-test` | `covdata` — declared by the Go wave (B047), same key.
- Verdict: `judgment.r1.coverage_producer` (string, present iff declared;
  absent never `null`), registered in `verify.py`; schema `const` 8 → 9.
- **B038(a) — real branch arcs under `producer = "istanbul"`.** The parser
  gains a producer-aware branch path: `branchMap` entries typed
  `if`/`cond-expr`/`switch`/`binary-expr`/`default-arg` with N locations and
  N counts become `BranchCoverage.by_line` arcs keyed by each arm's
  `start.line` (istanbul-lib-coverage's own reduction), with the
  `FileCoverage` cross-bucket invariants checked on BOTH committed real
  artifacts; any other producer keeps `branches=None` and
  `branch_capability = "unavailable"`. `require_branch = true` becomes legal
  on an istanbul-producer lane.
- **B038(b) — type-only modules under `istanbul`.** Absent-from-artifact
  changed `.ts`/`.tsx` files are offered to a NARROW, fail-closed lexer
  (Go's `has_executable_code` discipline, A-104): after comment masking, a
  file whose every top-level statement begins with `import type`,
  `export type`, `export interface`, `type `, `interface `, `declare ` and
  contains no other top-level token is NoCode; any construct the lexer does
  not recognise answers `True`. No TypeScript parser; measured against
  `probe-js/src/typesonly.ts` and a control with one runtime export.
- **B040(b)**: the `vitest-v8` refusal message names A-346 and the fix
  (`provider: 'istanbul'`); A-346's documentary warning is downgraded to a
  note about lanes written before this key existed.

### Migration (4.0.0 notes)

Every existing `coverage-istanbul-json` lane must add `producer = "istanbul"`
(there are none in the estate yet — dstdns has no JS lane); Python lanes
need nothing. v8 verdicts are refused by `assay verify` at v9 exactly as v7
were at v8.

### Acceptance

**SHIPPED 2026-08-31 (Wave B, assay-4.0.0 / schema v9).**

- [x] vocabulary, loader refusals (unknown name; name not in the format's
      set; `vitest-v8`/`jest-v8`/`c8` by name with their reasons; missing on
      istanbul-json), each with a test — `assay/vocabulary.py`
      (`COVERAGE_PRODUCERS_BY_FORMAT`, `COVERAGE_PRODUCER_REQUIRED_FORMATS`,
      `REFUSED_COVERAGE_PRODUCERS`), `config._load_coverage_producer`,
      `tests/test_config_coverage_producer.py`. Refusal-by-name is tested
      BEFORE catalogue membership (A-352), so a consumer who declared the
      unsound provider is told what is wrong with it and how to fix it;
- [x] `judgment.r1.coverage_producer` in schema/dataclass/`verify.py`;
      drift-guard v9 asset; migration notes in CHANGES.md — landed with the
      cut (`af14021f`/`1577fa45`), and wired at `runner.py`'s `JudgmentR1`
      construction in the same commit so the field is never
      declared-but-never-populated;
- [x] branch arcs under `istanbul` on both committed real artifacts, with
      the A-265 detail-over-metadata discipline and uniqueness/disjointness
      validation before aggregation; `unavailable` for every other producer —
      `coverage_parsers/coverage_istanbul_json.py` (commit `cc4e955f`);
      A-355 widened every parser's `parse` to take the producer keyword-only
      with NO default, so a caller that forgets it fails loudly rather than
      losing arcs silently;
- [x] the type-only lexer with its fail-closed controls —
      `adapters/javascript.py`; scoped exactly to B045's list and answering
      "has code" for anything it does not recognise (A-358);
- [x] README/CONSUMERS/DESIGN-GUIDE §11 updated: "leave `require_branch`
      unset" guidance replaced; B038 and B040 (b) marked resolved by this
      item — both marked above; B038(a) = A-356/A-357, B038(b) = A-358,
      B040(b) = A-353.

---

## B046 — R2 by evidence ingestion: `judge.mutation.format = "mutation-report-json"`, the lane's own argv runs the mutation tool inside the snapshot (resolves B037; schema v9)

**Filed 2026-08-30 from the 3.1.0 design review (§4 D2). Operator ruling
2026-08-30: RATIFIED — B037's three open decisions are resolved as below;
this entry is dispatchable in the v9 wave. Record the rulings as
`decisions.md` entries when implemented.**

### The shape: make ingested R2 look exactly like R1

R1 already ingests foreign evidence: the lane's argv runs a coverage tool
inside the snapshot, the tool writes an artifact at a declared path, assay
reads it through a FORMAT-keyed registry and computes the judgment. Ingested
R2 is the same sentence with "mutation" substituted:

```toml
[lanes.ui_mutation]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
cwd = "applications/webapp-ui-react"                      # B043
argv = ["bash", "-c", "npm ci --offline --no-audit --no-fund && npx --no-install stryker run --reporters json"]
env = { npm_config_cache = "/opt/npm-cache", CI = "1" }
env_passthrough = ["PATH", "HOME"]
budget = "45m"
allow_argv_append = false

[lanes.ui_mutation.isolation]
snapshot_selection = "repository"

[lanes.ui_mutation.judge]
language = "javascript"
source_roots = ["applications/webapp-ui-react/src"]
base_source = "request"

[lanes.ui_mutation.judge.mutation]
format = "mutation-report-json"                            # mutation-testing-report-schema (Stryker's json reporter)
artifact = "applications/webapp-ui-react/reports/mutation/mutation.json"
fail_under = 100.0                                         # killed / (killed + survived), over changed lines
```

This answers B037's open questions without a new trust boundary:

1. **"Shell out, or accept a CIU-supplied report?" — neither.** The lane's
   argv runs Stryker in the private snapshot (A-161), so the report is bound
   to the resolved commit by construction, exactly as `coverage-final.json`
   is today. CIU orchestrates WHERE (the environment, the npm cache); assay
   never invokes Stryker itself and never accepts a report it did not watch
   being produced.
2. **Non-repudiation.** (i) commit binding = snapshot; (ii) exit status is
   not proof: Stryker exits non-zero when the score is under ITS thresholds —
   the docs mandate `thresholds: { break: null }` so the exit status carries
   only crash information and assay judges the score; a non-zero exit is
   still R0's `COMMAND_FAILED`; (iii) the report's `projectRoot` must equal
   the snapshot root and every `files` key must resolve under a declared
   source root — otherwise `ERROR`/`UNREADABLE_ARTIFACT` ("an artifact from
   elsewhere"); (iv) `schemaVersion` is pinned to the major the committed
   real fixture carries; a `Pending` mutant anywhere refuses the whole
   report (incomplete evidence is not evidence); (v) the report is read once,
   bounded by `MAX_COVERAGE_ARTIFACT_BYTES`'s sibling and a fixed mutant
   ceiling.
3. **Vocabulary.** Status map, each direction chosen for the visible-failure
   side: `Killed → killed`; `Survived → survived`; `NoCoverage → survived`
   (recorded separately as `survived_uncovered` — the worst kind, never
   hidden inside the survived count); `Timeout → budget_exceeded`
   (Stryker's per-mutant timeout IS the per-candidate budget);
   `CompileError`/`RuntimeError → discarded` (an invalid mutant the native
   engine never emits; excluded from the denominator, counted);
   `Ignored → excluded` (the tool's own ignore, counted, never laundered as a
   kill); `Pending → refuse`. `equivalent` stays 0 and `kill_attribution =
   "unattributed"` (no equivalence detection; `equivalence_artifact` stays
   SQL-only). Operators: a foreign tool's mutator names are DATA, not assay's
   closed list — each recorded mutant carries `operator = "stryker:<mutatorName>"`,
   admitted by a namespace pattern branch in the schema's `mutation_operator`
   (`^stryker:[A-Za-z0-9]+$`), and `judge.mutation.operators` is REFUSED on an
   ingested lane (Stryker's config declares mutators; two declarations would
   be the P1 hazard).
4. **Provenance and the B018 relationship.** `judge_provenance` stays assay's.
   The producer's identity is copied from the report — `judgment.r2.producer
   = "ingested"`, `judgment.r2.producer_tool = {name, version,
   report_schema_version}` from `framework`/`schemaVersion` — declared-by-
   artifact, not verified, and said so in DESIGN-GUIDE. `helpers[]` is NOT
   used: it records tools assay itself invoked (A-230a), and assay did not
   run Stryker. Native lanes record `producer = "native"`. A verdict thereby
   distinguishes Tier-1-computed-natively from Tier-1-computed-over-ingested-
   evidence, which the north-star's "never conflate tiers" requires.
5. **Precedent.** Yes — this is the general shape for any R2 producer assay
   has no native engine for: a format-keyed MUTATION registry
   (`mutation_parsers/`, parallel to `coverage_parsers/`) keyed by the
   report format, not the tool; Stryker.NET/Stryker4s emit the same schema
   and would need no code. Go's or Python's tools join only when they emit
   a registered format.

### Judgment

Scope is assay's computation, not the tool's: under `changed_lines`, a mutant
counts iff its `location.start.line` (file key resolved to repo-relative) is
an added line of the diff; under `whole_target`, iff its file is a declared
target. A changed executable line with NO mutant is recorded
(`lines_without_candidates`) and, with zero candidates in scope overall,
renders `INCONCLUSIVE`/`NO_MUTANTS` exactly as native R2 does. `pct =
killed / (killed + survived)`; `fail_under` compares against it;
`survived_uncovered` is listed with file:line so a consumer sees the untested
mutant, not a number.

### Acceptance

**SHIPPED 2026-08-31 (Wave B, assay-4.0.0 / schema v9).** One deviation,
filed rather than fudged: `fail_under` is honoured at `100.0` only — see
**B050** and A-380.

- [x] a REAL Stryker report from `tests/fixtures/coverage/probe-js` (Stryker
      + `@stryker-mutator/vitest-runner`, versions and lockfile committed
      under `PROVENANCE.md`) is the primary fixture (A-334); synthetic
      reports cover every status, `Pending`, a foreign `projectRoot`, a key
      outside the source roots, and an over-ceiling mutant count —
      `tests/fixtures/mutation/mutation-report-json.probe-js-stryker.json`
      (StrykerJS 10.0.0, 109 mutants over 6 files) drives every happy-path
      node in `tests/test_runner_ingested_r2.py`, and each refusal node
      mutates ONE field of that same real document rather than hand-building
      a synthetic one;
- [x] `mutation_parsers/mutation_report_json.py` + registry + loader keys
      (`format`, `artifact`, `fail_under`; `operators`/`jobs`/`max_mutants`/
      `equivalence_artifact` refused on an ingested lane with reasons) —
      `src/assay/mutation_parsers/{__init__,model,mutation_report_json}.py`;
      the registry lives in the package rather than in `assay.mutation` to
      keep the `config -> mutation -> config` cycle closed (A-376);
      `config._load_ingested_mutation` carries the refusals, each naming WHY
      rather than merely that the key is not allowed;
- [x] `judgment.r2.producer`/`producer_tool`/`survived_uncovered`/
      `discarded`/`lines_without_candidates` in schema, dataclass, `verify.py`
      (re-derivation of `pct` and buckets from the payload), drift-guard v9 —
      schema/dataclass/drift-guard landed with the cut (`af14021f`/`1577fa45`);
      the `verify.py` half landed here as
      `_check_ingested_r2_agrees_with_its_payload`, which closes a real hole:
      every PRE-EXISTING raw R2 check is guarded on `judgment.r2.operators`
      and therefore SKIPPED on an ingested document rather than passing. The
      score itself is re-derived by the existing `_check_r2_rederivation`
      through `judge_mutation`, which an ingested claim goes through unchanged
      (A-379) — one owner, not two that could disagree;
- [x] `cli.py` registers `javascript` at `{"R1", "R2"}` ONLY through the
      ingested path (`generate_mutation_sites` stays `UNSUPPORTED`; the
      runner selects native vs ingested by `judge.mutation.format` presence) —
      and a NATIVE javascript R2 lane is still not constructible, because it
      would have to declare non-empty `operators` and no `javascript` operator
      catalogue exists; the 340-374 docstring is rewritten to say which of its
      two guards now carries that guarantee;
- [x] CONSUMERS: worked lane, the `thresholds.break: null` mandate, the
      status map table, what the verdict records; DESIGN-GUIDE §11 "Mutation
      is source-oriented" gains the ingested paragraph and the tier
      statement; B037 marked RESOLVED by this item; decisions recorded —
      CONSUMERS §"R2 for JavaScript, by ingesting Stryker's report (B046)"
      (its worked lane is a LOADABLE example, verified by the docs guard);
      DESIGN-GUIDE §11's new "Ingested R2" paragraphs; decisions A-375–A-383.

---

## B047 — Go wave preparation: helper distribution and identity, `helpers[]` in the gate envelope, the shared line-expansion bound (B039), the `covdata` producer

**Filed 2026-08-30 from the 3.1.0 design review (§4 D4/D8).** Not a package:
scope additions for the P27 re-carve (A-217/A-239), so the carve does not
discover them. Each item names what is already ruled and what is not.

1. **Helper distribution (NOT ruled by A-239, which settled the seam).** Two
   candidates, decide in the carve with a probe: (a) ship the
   statement-position oracle's Go source inside the wheel
   (`assay/helpers/go/stmtpos/`) and invoke `go run <path>` — the source is
   then covered by `judge_provenance`, and `helpers[].identity` is
   `go version …`; stdlib-only source needs no module download (`GOFLAGS=-mod=mod`,
   `GOPROXY=off` proven in the probe); compile latency measured against
   `tester-unified-go`'s warm `GOCACHE`. (b) a separately built binary in the
   environment image — a second artifact to pin, which ciu v8's
   `[testing.judge]` floor does not cover. Recommend (a).
2. **`external_tools = ("go",)`** on the Go adapter → the existing
   `MISSING_EXTERNAL_TOOL` preflight (A-253) covers it; `assay lanes --json`
   (B044) exposes it so CIU-72 can check the environment.
3. **`judge.coverage.producer` for `go-cover`** (B045's key): `go-test`
   (`go test -coverprofile`) | `covdata` (`go tool covdata textfmt` over the
   `GOCOVERDIR` binary data of `go build -cover` binaries — the integration-
   test path, an S3 lane). Same format; document both and their producer names.
4. **B039** — fold the per-artifact classified-line ceiling into ONE shared
   bound in `coverage_parsers/model.py`, used by `go_cover` and
   `coverage_istanbul_json`; B039's own acceptance is discharged there.
5. **Gate envelope** — CIU-72: the LaneResult copies `helpers[]` verbatim;
   nothing on assay's side beyond documenting that `helpers` is the
   reproducibility record for a Go verdict.
6. **Fixtures** — A-234's stale hand-authored profiles regenerate against
   `tester-unified-go` as already tracked.

### Acceptance

- [ ] the P27 re-carve cites items 1–6 explicitly (carve reviewer checks);
- [x] item 4 landed (may ride the v9 wave or the Go wave — whichever is
      first) — landed in Wave A instead, ahead of both: see B039's own
      acceptance boxes.
- [x] item 1 landed, option (a) as recommended — `assay/helpers/go/stmtpos/`
      ships inside the wheel AND the zipapp; `GOPROXY=off`/`GOFLAGS=-mod=mod`
      are forced on every invocation. One correction to the item's own text:
      "invoke `go run <path>`" is not enough on its own, because the zipapp
      has no path to give it — the source is staged out of the archive to a
      real directory first (A-403).
- [x] item 2 landed (Wave C generation 2) — `external_tools = ("go",)`.
- [x] item 3 landed (Wave C generation 3, A-398) — `go-test` | `covdata`,
      both documented, the key optional for this format.
- [x] item 5 landed (Wave C generation 4) — and it was MORE than the
      documentation item filed here. `Verdict.helpers` had no producer at
      all: P33 validated the array and nothing populated it. A Go R1 lane now
      emits a real entry, `assemble_verdict` refuses a helper with no
      correspondingly-judged claim, `_replace_highest_higher_rigor_claim_with_git_failed`
      drops one whose claim it voids, and the correspondence rule has one
      definition (`verdict.supported_helper_roles`) read by both sides.
      Proven end to end against the real toolchain by
      `tests/qualification/test_go_r1_real.py` (A-395: never a mock).
- [ ] item 6 (fixture regeneration, F008-A4) — still owed; blocked with
      F008-A5 behind **B059**.

---

## B048 — browser (Playwright) coverage of a React UI as an R1 lane: `vite-plugin-istanbul` inside the lane, and where the S3 binding stops

**Filed 2026-08-30 from the 3.1.0 design review (§4 D6).** Documents a path
that needs NO assay change today, and names the one thing that must not be
built before B004.

### The path

Inside one lane (the snapshot): `npm ci --offline`; `vite build --mode
coverage` with `vite-plugin-istanbul` (babel-plugin-istanbul — the same
instrumenter as `@vitest/coverage-istanbul`, `producer = "istanbul"` under
B045); serve the build (`vite preview` or the app's nginx) on the instance
network; run the Python-driven Playwright suite (`pytest -m browser
tests/e2e/ui/webapp-ui-react`) against it, with a fixture that dumps
`window.__coverage__` after every test and merges the maps
(`istanbul-lib-coverage`) into one `coverage-final.json`; the lane declares
`format = "coverage-istanbul-json"`, `source_roots = ["applications/webapp-ui-react/src"]`.
The implementer must measure that the artifact's keys are the ORIGINAL
`src/**/*.tsx` paths (the plugin instruments pre-transform sources), never
`dist/`.

WHERE is ciu's: the tester serving the preview must be reachable from the
browser service, and the UI needs the instance's backend
(`requires.services = ["webapp_server", "browser_service"]`); the backend
route reaches the lane as an `infrastructure` fact (B013 `derived:` → v8
`ciu.instance.resolved.routes…`). Scope `S3`, declared.

### The limit, stated once

The UI code judged is the snapshot's (fully bound). The API it talks to is the
deployed image's — an unverified declared fact, exactly A-O12/B004's subject.
Until B004 ships verified provenance, an S3 R1 verdict from this lane binds
the UI, not the system. A detached `assay judge <artifact>` verb (judging
evidence produced by a deployed image, outside any snapshot) is the larger
ask this pattern avoids; **do not build it before B004** — it would be the
first assay judgment with no commit binding of its own.

### Acceptance

- [x] a CONSUMERS section "Browser coverage of a UI as an R1 lane" with the
      recipe above and the limit paragraph verbatim in spirit — landed
      2026-08-30;
- [x] a small committed `vite-plugin-istanbul` artifact (produced outside
      assay like `probe-js`, `PROVENANCE.md` entry) proving the keys and the
      parser path, plus a parser test over it — real `vite build`
      (`forceBuildInstrument: true`) executed in real jsdom, node `v26.5.1`/
      `vite` `8.2.2`/`vite-plugin-istanbul` `9.0.1`:
      `tests/fixtures/coverage/probe-js-vite-plugin-istanbul/`,
      `tests/fixtures/coverage/coverage-istanbul-json.vite-plugin-istanbul.json`,
      `tests/fixtures/coverage/PROVENANCE.md`'s new section,
      `tests/test_coverage_parsers_vite_plugin_istanbul_artifact.py` (3
      tests: keys are `src/*.ts`, never `dist/`; the real bundle reports
      genuine partial coverage — `subtract`'s untaken branch, not a
      trivially-all-green fixture; the existing parser needed no change);
- [ ] the consumer-side `__coverage__` dump fixture is dstdns's package, listed
      in the review report §7 — not this repo's work, unchanged.

---

## B049 — a coverage/mutation tool that deletes-and-recreates its own output directory silently orphans assay's held reservation, reading `EMPTY_COVERAGE` over a genuinely complete artifact

**Filed 2026-08-30, Wave A (B041(c)'s real-`vitest` qualification harness) — the
first time a real external coverage tool has run inside an assay snapshot.**
Not JS-specific in mechanism (`safeio.reserve_output`/`consume` is
language-free core), but JS-specific in *discovery*: Python's `coverage.py`
writes its report file directly, with no directory-delete step, so nothing in
this project's existing test suite (all pre-B041 R1/R2 lanes, real or
heredoc'd) ever exercised this path. A-334's own lesson, one layer further:
even a REAL run through the REAL CLI does not prove a claim about behaviour a
test double never triggered.

### The mechanism, measured

`runner.py:1692` (`safeio.reserve_output(..., create_missing_parents=True)`,
threaded from B006(b)) opens (and, for a fresh snapshot, creates) the coverage
artifact's PARENT DIRECTORY once, before the lane's own command runs, and
holds that directory's own file descriptor (`OutputReservation._parent_fd`,
`safeio.py:212`) for the lane's whole execution. `runner.py:1771`
(`reservation.consume()`) reads the artifact AFTER the command exits by
opening the declared basename relative to that SAME held descriptor
(`safeio.py:319`, `os.open(basename, dir_fd=parent_fd)`).

If the lane's own tool deletes and recreates that directory (`rm -rf
reportsDirectory && mkdir reportsDirectory && write coverage-final.json` —
Vitest's own default `coverage.clean = true`, `coverageConfigDefaults` in
`vitest@4.1.11`'s own `chunks/defaults.*.js`) rather than writing into the ONE
directory assay already opened, the held `parent_fd` is left pointing at an
orphaned, now-empty directory inode. `consume()`'s lookup then raises
`FileNotFoundError` on the `os.open(basename, dir_fd=parent_fd)` at
`safeio.py:318-319`, which `consume()` returns as `None`
(`safeio.py:320-321`, `except FileNotFoundError: return None`), and the
caller reads that as "the command never produced an artifact" — reaching
`parse_coverage_artifact` (`coverage.py:161`), which raises
`NO_MEASUREMENT`/`EMPTY_COVERAGE` for a `None` read (`coverage.py:179-187`).
**The message that actually ships asserts a checkable falsehood**:
`coverage.py:181-184` says "the lane's command exited without writing the
declared artifact at all … there is nothing here to have failed to read" —
demonstrably false here, over a complete, correctly-keyed 678-byte artifact
that really existed on disk at the declared path the whole time (proved by
the parallel `cp` below). **A fix needs no new schema const or reason code**:
`coverage.py:171-173` already reserves `ERROR`/`UNREADABLE_ARTIFACT` for "an
object that exists but cannot be trusted (… or a race the caller's own
reservation already detected)" — this IS that race; the enum value this
class of failure belongs under already exists in the frozen v8 schema, it is
simply not the one raised here.

(Corrected 2026-08-30, Wave A review round 1: the mechanism paragraph above
originally miscited `safeio.py:339` — that line is `if not chunk: break`
inside the read loop, reachable only past a successful `os.open`, not the
`None` return — and `check_empty_coverage`/`coverage.py:313`, which is never
reached because that function takes an already-parsed `CoverageProfile` that
never exists on this path. Both corrected above against the code as it
stands.)

**Isolated by direct A/B measurement, real `assay run`, real Vitest, nothing
mocked** (a two-commit git fixture, `npm ci --offline` against a warm cache,
`npx --no-install vitest run --coverage`, `fail_under = 100.0`, a fully
covered diff):

| `vitest.config.ts` | `assay run` result |
|---|---|
| `coverage.clean` unset (Vitest's own default, `true`) | `NO_MEASUREMENT`/`EMPTY_COVERAGE`, exit 3 |
| `coverage.clean = false`, nothing else changed | `PASS`, `pct: 100.0`, `covered: 1`, `executable: 1` |

A parallel `cp .assay/coverage-final.json /tmp/…` appended to the SAME lane
command, executed immediately after the real `vitest run` step and BEFORE
assay's own post-command read, proves the artifact was genuinely complete and
correctly keyed on disk at the declared path the whole time — the reservation
mechanism failed to find something that was really there, not a case of the
tool truly writing nothing.

### Why this is filed, not fixed, in Wave A

`safeio.py`/`runner.py` are core, language-free evaluation machinery shared by
every adapter (Python R1/R2/R3, SQL R2, JavaScript R1) and every future one.
(Corrected 2026-08-30, Wave A review round 1: the wave prompt's own NOT-IN-
SCOPE list, `WAVE-PROMPT-2026-08-30-js-consumer-producer.md` lines 115-121,
does not in fact name "core-mechanism changes" as excluded — it forbids
verdict/schema/`verify.py`/drift-guard changes, R2/R3 registration,
`cwd`/`link_paths`/`producer`, and Go changes, and none of those is what a
B049 fix would touch. The prior wording overstated what the prompt actually
excludes; this fix is filed rather than implemented because it is a real
design call outside the wave's own ENUMERATED scope list, not because the
prompt forbids core-mechanism changes as a category.) The right fix is a
real design call this backlog entry is not the place to make unilaterally —
candidates:

1. Re-open the parent chain by NAME at `consume()` time instead of holding a
   directory descriptor across the whole command execution — loses the
   TOCTOU protection `arm()`'s pre-command unlink currently buys against a
   symlink swapped in mid-run, unless re-derived some other way.
2. Detect a recreated directory (compare the parent's own inode/device
   identity, not just the basename's) and raise a NAMED, distinguishable
   reason (`UNREADABLE_ARTIFACT`/something naming "your tool replaced its own
   output directory") instead of the current silent fold into
   `EMPTY_COVERAGE` — cheaper than (1), still leaves the underlying tools
   working around it via `clean = false`, but stops the failure from reading
   as "you produced no coverage" when the true cause is diagnosable.
3. Document only (today's mitigation, shipped this wave): every JS lane's
   `vitest.config.ts` declares `coverage.clean = false`. Costs nothing in
   code, but is silent about whether OTHER external tools (a future adapter,
   or a consumer's own coverage wrapper) share Vitest's "clean the output
   directory first" convention — a common enough pattern that it should not
   be assumed unique to Vitest.
4. **(Added 2026-08-30, Wave A review round 1)** A specific, name-free
   implementation of (2): `os.fstat(parent_fd).st_nlink == 0` on the ALREADY
   HELD descriptor, checked at `consume()` time, needs no by-name `stat` and
   so no new TOCTOU surface (unlike (1) and a naive reading of (2)); a
   deleted-and-recreated directory always leaves the OLD inode at `nlink=0`
   even though a NEW inode now answers to the same path (verified: held fd
   `nlink=0 ino=17860260` vs. the recreated directory `ino=17860261`). It
   raises the SAME `ERROR`/`UNREADABLE_ARTIFACT` `coverage.py:171-173`
   already reserves for this — no schema/enum change. Round-1 review
   prototyped and ran it (against a scratch copy, not this branch): a
   15-line pure-Python `rmtree`+`mkdir` fake reproduces the defect with no
   JS/Vitest involved (the regression test option (1)/(2)'s own acceptance
   box below asks for), and the full suite passed with zero regressions.
   **Blast radius wider than this entry's own title states**: the identical
   `None`-means-nothing-was-written fold also sits at `runner.py:1714` (SQL
   R2's `equivalence_artifact`) and `mutation.py:1167`, where an absent read
   is classified `crashed` (`mutation.py:1129`) and rolls up to
   `ERROR`/`EXEC_FAILED` (`mutation.py:1808`) — a directory-recreating dump
   step in a SQL lane's own tooling would report every mutant as CRASHED for
   a command that ran perfectly, not just `EMPTY_COVERAGE` for a JS lane.
   Not yet implemented on this branch; a maintainer ruling and a real
   implementer round (with its own review) are still needed before this
   lands, per this project's own standing practice for core-machinery
   changes — see decision ask in
   `nyxloom-trove/reports/assay-WAVE-A-js-consumer-REPORT.md` §14.

### Acceptance

- [ ] a product ruling among the four options above (or a fifth), recorded
      as a decision;
- [ ] if (1), (2) or (4): a regression test that plants a directory-recreating
      fake tool (no real Vitest needed to prove the CORE mechanism) and
      asserts the new, non-silent behaviour;
- [ ] CONSUMERS' `clean: false` note (added this wave) updated to match
      whatever ships — either it stays required with the same reason, or it
      becomes optional with the new diagnostic named instead.

---

## B050 — an ingested R2 lane cannot declare a mutation-score floor below 100: `judgment.r2` has no field recording WHICH floor was applied

**Filed 2026-08-31 from Wave B (B046's implementation), with evidence.
Refused loudly rather than half-implemented — the gap is a WIRE field, so the
fix belongs to the next schema cut, not to a patch release.**

### The problem

B046's contract says `judge.mutation.fail_under` "compares against" the
ingested mutation score `pct = killed / (killed + survived)`. The lane key
ships (required, validated, in the worked example) but **this build honours
only `100.0`**, and any other value is refused at load naming this entry.

The reason is a wire gap, not an implementation shortcut.
`JudgmentR2`'s own docstring states the property that makes R2 auditable:

> an independent consumer can already re-derive the R2 claim's *status* from
> `Mutation`'s own bucket fields alone (`judge_mutation`'s mapping needs no
> external policy input)

`judgment.r1` carries `fail_under`, so an R1 PASS is explicable from the
document. `judgment.r2` carries no such field — under v8 it needed none,
because native R2 fails on any survivor at all. A lane judging at 90 would
therefore emit `PASS` beside recorded survivors with **nothing in the document
explaining it**, and `verify.py`'s `_check_r2_rederivation` — which reuses
`judge_mutation` — would correctly report the verdict as inconsistent. The
verdict would be un-auditable by exactly the tool that exists to audit it.

Three options were weighed at implementation time (A-380). Dropping the key
leaves B046's own acceptance unmet and the documented worked lane unloadable.
Accepting the key and ignoring it is inert config that cannot fail loudly when
it is wrong (AGENTS.md 4.2a's own test). The third — ship the key, honour its
one currently-expressible value, refuse the rest with the reason — is what
landed.

### Evidence

- `src/assay/config.py` `_load_ingested_mutation`: the refusal, naming this
  entry.
- `tests/test_config_ingested_mutation.py::test_a_sub_hundred_fail_under_is_refused_naming_the_wire_gap`.
- `src/assay/mutation.py` `judge_mutation`: the `fail_under` parameter was
  written and then removed; the docstring records why.
- `src/assay/schemas/verdict.schema.json` `$defs.judgment_r2.properties`: no
  floor field, in v9 as in v8.

### The fix, when a schema cut next opens

Add `judgment.r2.fail_under` (number, 0..100), **required under
`producer = "ingested"` and forbidden under `"native"`** — the producer fork
A-360 already established is exactly the right shape for it, since native R2
genuinely has no floor to record. Then:

1. `judge_mutation` takes the floor (the parameter that was removed) and the
   `survived` branch consults `mutation_pct` — already implemented and tested
   in `assay.mutation`, so this is a re-wiring, not new arithmetic;
2. `verify._check_r2_rederivation` reads the floor FROM the document, which is
   what keeps the re-derivation total rather than partial;
3. the load-time refusal in `_load_ingested_mutation` is deleted, and the
   range check (`0.0..100.0`) is all that remains.

### Acceptance

- [ ] `judgment.r2.fail_under` in the schema, the dataclass and `verify.py`
      (the 2.4.0 lesson: three places), forked on `producer`, with the frozen
      drift-guard asset updated at that cut;
- [ ] `judge_mutation` honours it and `verify.py` re-derives the R2 status
      from the document alone — proven by a verdict that PASSes with recorded
      survivors and verifies clean, which is exactly the document this build
      cannot produce;
- [ ] the load-time refusal deleted, and
      `test_a_sub_hundred_fail_under_is_refused_naming_the_wire_gap` replaced
      by its positive counterpart;
- [ ] CONSUMERS' ingested-R2 section drops the "must be 100.0" paragraph.

---

## B051 — `judgment.r2.discarded` is accepted on the producer's word alone: never derived, never cross-checked, and a materially false value rides the wire uncontradicted

**Filed 2026-08-31 from Wave B fix round 1, with evidence. FILE, DO NOT BUILD
— what "derived" would even mean here is a real product question, not an
implementation shortcut.**

> **Numbering note.** The controller's fix-round brief asked for this to be
> filed as **B052**, citing "the same file-don't-build pattern as
> B049/B050/B051". There is no B051: `main` carries entries through B049 and
> this wave filed B050, so B051 is the genuinely next free identifier —
> verified against this file and against `git show main:.../4-backlog.md`
> before choosing. Filing it as B052 would have left a permanent phantom gap
> at B051 that a later reader would go looking for. Filed as **B051**, and
> called out here and in the wave REPORT so the controller can see the
> substitution rather than discover it.

### The problem

Every other fact an ingested `judgment.r2` carries is re-derived from the
payload the same document holds. `survived_uncovered` must name positions the
`survived` bucket actually records; `lines_without_candidates` must not name a
line a recorded mutant starts on; `survived_uncovered` must be a subset of
`survived`, never of the whole payload; the mutation SCORE is re-derived
through `judge_mutation` (A-379). That is what makes an ingested R2 claim
auditable from the artifact rather than believed.

`discarded` is the exception. It is checked for **presence and
non-negativity** and for nothing else:

```
src/assay/verify.py:964-974   # `_check_ingested_r2_agrees_with_its_payload`
    discarded = r2.get("discarded")
    if isinstance(discarded, bool) or not isinstance(discarded, int): ...
    elif discarded < 0: ...
```

The schema's own bounds (`integer`, `0..10000`) say the same thing one layer
up, so the raw layer adds no independent statement about this field at all.

**Reproduced:** an adversarial reviewer edited a real ingested verdict's
`judgment.r2.discarded` to `9999` — a count larger than the whole report's
109 mutants, and ~9999 more invalid mutants than the run actually had — and
`verify_document` accepted it with no failures. The document says "assay
measured much less than this score implies" in the strongest possible terms
and nothing contradicts it; equally, a run that really did discard most of its
mutants could report `0` and be believed.

This is not exploitable into a false GREEN — `discarded` is excluded from the
pct denominator, so it cannot move a claim's status. It is a **credibility**
defect: the field exists precisely so a consumer can see that a report which
could not compile most of its own mutants has measured far less than its score
suggests, and a value nothing checks cannot carry that.

### Why this is not fixable in Wave B

Because there is no derivation available, and inventing one would be worse
than the gap.

Assay sees the mutants the report LISTS. `discarded` counts mutants the report
describes as `CompileError`/`RuntimeError` — and in
`mutation-testing-report-schema` those mutants ARE listed, with those statuses,
so a count is derivable *for reports shaped like the committed fixture*. What
is NOT derivable is the thing the field is actually for: whether the foreign
tool discarded mutants it never listed at all. Stryker does not reveal why a
mutant was dropped before reporting, and a tool that dropped 900 candidates
silently emits a document indistinguishable from one that generated 109. So a
re-derivation would check the easy half, report agreement, and leave the half
that matters exactly as unchecked as it is now — while LOOKING like the field
had been audited. A green bar that says "checked" about the wrong half is
worse than one that says nothing.

The real question is a product call: does `discarded` mean "invalid mutants
this report listed" (derivable, and then it should be derived and the field
made redundant on the wire) or "invalid mutants the tool encountered"
(not derivable from any artifact assay receives, and then it should be
explicitly marked declared-not-verified, the way `producer_tool` already is
under A-230a/A-361)? Those are different fields with the same name, and the
v9 schema description — "how many mutants the ingested report marked
CompileError or RuntimeError" — reads as the first while the field's stated
PURPOSE reads as the second.

### Evidence

- `src/assay/verify.py:964-974` — the whole of the check.
- `src/assay/schemas/verdict.schema.json` `$defs.judgment_r2.properties.discarded`
  — `integer`, `minimum: 0`, `maximum: 10000`; the raw layer restates exactly
  this and adds nothing.
- `src/assay/verdict.py` `JudgmentR2.__post_init__` — `0..10_000`, same range,
  no payload cross-check.
- `tests/test_verify_ingested_r2.py::test_a_negative_discarded_count_is_caught`
  — the ONLY negative test the field has, and it tests the range.
- The committed real artifact
  (`tests/fixtures/mutation/mutation-report-json.probe-js-stryker.json`) has
  **zero** `CompileError`/`RuntimeError` mutants, so `discarded` is `0` in
  every document this project has ever produced — the field has no non-trivial
  witness anywhere.

### The fix, once the meaning is ruled

1. **Rule which field it is** — record the ruling as an A-row. "Listed" and
   "encountered" are different contracts and only one of them is checkable.
2. If **listed**: derive it in `assay.mutation.ingest_mutation_report` from the
   report's own statuses, re-derive it in
   `verify._check_ingested_r2_agrees_with_its_payload` against the payload the
   way the other three facts are, and refuse a document whose `discarded`
   disagrees.
3. If **encountered**: it is declared-not-verified evidence and must say so —
   the schema description gains that phrase in the words `producer_tool`'s
   already uses, DESIGN-GUIDE §11's tier paragraph names it beside
   `producer_tool`, and CONSUMERS says plainly that assay records this number
   and does not check it.
4. Either way, commit a real report carrying a non-zero `discarded` (a
   deliberately uncompilable mutant is easy to produce with Stryker) so the
   field finally has a witness.

### Acceptance

- [ ] the "listed vs encountered" ruling recorded as an A-row, naming which
      alternative was rejected and why;
- [ ] under **listed**: `discarded` re-derived from the payload in `verify.py`
      beside the other three re-derivations, with a test that mutates it on a
      REAL document and asserts a NAMED failure (the `9999` reproduction above
      is the test to write);
- [ ] under **encountered**: the declared-not-verified statement in the schema
      description, DESIGN-GUIDE §11 and CONSUMERS, and a test asserting the
      schema description says so — the same three-place discipline
      `producer_tool` already carries;
- [ ] a committed real report with a non-zero `discarded`, and a frozen
      W-generation document carrying it, so the field has a witness at all.

---

## B052 — an ingested report's embedded `source` is never compared against the snapshot's own committed bytes: assay derives every mutant position from text it takes entirely on the tool's word

**Filed 2026-08-31 during Wave B fix round 1, on the controller's request.
FILE, DO NOT BUILD — the check is easy and what a MISMATCH MEANS is not.**

> **Numbering note.** B051 is the `discarded` finding. This entry was filed
> second and takes the next free identifier. See B051's own numbering note for
> why the fix-round brief's "B052" ended up applied to neither item in the
> order it expected.

### The problem

`mutation-testing-report-schema` embeds, for every measured file, the full
`source` text the tool read. Assay **requires** it and leans on it hard:

- `mutation_parsers/mutation_report_json.py:170-186` refuses a file record
  with no `source` string, then builds `_line_byte_offsets(source)` and
  `len(source.encode("utf-8"))` from it — so **every mutant's byte span is
  derived from this text**;
- `mutation.py:2150-2160` (`lines_without_candidates`) walks the same text
  line by line, and its docstring says so plainly: *"Lines come from the
  report's OWN `source` text, not from the snapshot: that is the text the tool
  actually read, so a line number here means the same thing the tool's own
  line numbers mean."*

That reasoning is correct as far as it goes — and it is exactly why nothing
checks the text. Assay already holds the snapshot's own committed blobs for
those same paths (`isolation.SnapshotRepository.read_regular_file`), and never
compares the two. A report whose `source` differs from the commit's bytes —
in whitespace, in whole functions, in file identity — is ingested and judged
without a word.

B046's non-repudiation items already ask for `projectRoot` to match the
directory the command ran in, and for every `files` key to resolve under a
declared `source_root`. Those establish that the report is **about this
checkout**. They do not establish that it is about **this commit's content**,
which is the stronger property assay's own committed-object snapshot exists to
make checkable, and the only one that closes "an artifact from an earlier
state of the same tree".

### Why this is not implementable without a ruling first

The comparison is trivial. Its VERDICT is not, and shipping the check with the
wrong terminal would be worse than the gap.

A mismatch has at least four causes with genuinely different correct
responses:

1. **a stale report** — the tool ran before the last edit. `ERROR`, and the
   consumer should re-run. Almost certainly what a mismatch usually is;
2. **a tool that rewrites sources in flight** — transpilation, a formatter in
   the test command, Stryker's own instrumentation writing back. Here the
   report is *honest about what was mutated* and the snapshot is *honest about
   what was committed*, and refusing would break lanes that are working
   correctly. This is not hypothetical for a JS/TS toolchain;
3. **a genuinely foreign report** — the non-repudiation case, which should
   refuse loudly;
4. **line-ending or trailing-newline normalisation** — a mismatch in bytes
   that is not a mismatch in meaning, and a byte-equality check would refuse a
   correct lane on a `.gitattributes` setting.

(2) and (4) are why "compare and refuse" cannot simply be written. A check
that cannot distinguish them either refuses honest lanes or is downgraded to a
warning nobody reads — and `judgment` has no field for "the evidence text
differed from the commit", so today there is nowhere to *record* the fact
short of refusing.

This is the same shape as B051: the code is a morning's work and the contract
is the actual question.

### Evidence

- `src/assay/mutation_parsers/mutation_report_json.py:170-186` — `source`
  required; byte offsets and file length derived from it; no snapshot read.
- `src/assay/mutation.py:2150-2160` — `lines_without_candidates` walks the
  report's own text, with the docstring stating the choice explicitly.
- `src/assay/isolation.py` `SnapshotRepository.read_regular_file` — the
  committed bytes ARE available at ingestion time, from the same snapshot the
  lane's command ran in. Nothing calls it from the ingest path.
- `src/assay/runner.py` `_ingest_r2_report` — the whole ingest path; the only
  snapshot facts it uses are `project_root` (via `resolve_run_cwd`) and the
  commit identity.
- Wave B REPORT §13 ("What I did NOT do, and why") — the implementer flagged
  this at the end of the wave and deliberately did not file it, wanting a
  reviewer's opinion on whether it was worth filing. The reviewer's answer,
  relayed by the controller, is that it is.

### The fix, once the meaning is ruled

1. **Rule what a mismatch MEANS**, as an A-row, naming which of the four
   causes above the terminal is claiming. The likely shape is a third
   non-repudiation tier: identity (does the report describe this project),
   anchoring (do the paths resolve), and *content* (is the text the commit's).
2. Read the committed blob for each measured path through
   `read_regular_file`, inside the baseline snapshot's own `with` block where
   `_ingest_r2_report` already runs.
3. Compare under a **stated** normalisation — decide explicitly whether line
   endings and a trailing newline are in or out, and test both ways round.
4. Give the mismatch a terminal and a message naming the file, or a wire field
   if the ruling is "record, do not refuse" — in which case it is a schema
   field and belongs to the next cut, exactly as B050 does.

### Acceptance

- [ ] the mismatch ruling recorded as an A-row, naming the rejected
      alternatives (refuse-always / warn / record-on-the-wire) and why;
- [ ] the committed bytes read from the snapshot at ingest time and compared
      under a documented normalisation, with a test that mutates ONE file's
      `source` in the REAL committed Stryker fixture and asserts a NAMED
      failure — and a companion test proving a byte-identical report still
      passes, so the check is not vacuous;
- [ ] a test for the transpiled/rewritten-source case (2), asserting whatever
      the ruling says should happen to it — this is the case that decides
      whether the feature is safe to ship at all;
- [ ] CONSUMERS' ingested-R2 refusal list gains the new refusal, in the same
      "what assay checks about your report" paragraph as `projectRoot`;
- [ ] if the ruling is "record, do not refuse": the wire field in schema,
      dataclass and `verify.py` (three places), at the next schema cut.

---

## B055 — an uncovered Go statement sharing a physical LINE with a covered one is still laundered into `executed`; the statement-position oracle does not fix it, and cannot at line granularity

> **Renumbering note, 2026-09-02.** Every id this Wave C branch filed was
> shifted up by two: **B053→B055, B054→B056, B055→B057, B056→B058,
> B057→B059, B058→B060**. Main's `a050a467` (2026-09-02, from dstdns's first
> JavaScript lane adoption on assay 4.0.0) had already filed a *different*
> B053 and B054, and main's ids win — the estate's precedent is the ciu
> CIU-55 shift. The rewrite covered all 108 references in 25 tracked files,
> historical briefs and logs included, because an id that silently resolves
> to a different entry is worse than an edited record. Next free id after
> this wave: **B061**. `decisions.md` is unaffected; the A-393/A-396/A-401/
> A-404 rows that cite these entries were rewritten in place.

**Filed 2026-08-31, Wave C (the P27 re-carve), with a frozen witness and a
test that asserts the unfixed behaviour.** Recorded as decision **A-393**.

### What happens

`carve-assets/P27/witness/lit.go` line 4 is `f := func() int { return 7 }`.
The real profile (`coverage-lit.out`) carries two records over it:

```text
example.invalid/lit/lit.go:3.14,4.18 1 1    <- the assignment, executed
example.invalid/lit/lit.go:4.18,4.30 1 0    <- the func literal's body, NOT executed
```

Both counted statements genuinely begin on line 4. Executed-wins promotes the
line, so the uncovered statement is invisible: `executed` contains 4 and
`missing` is empty.

A-217's source-side oracle (B047 item 1) does **not** change this, and this
entry exists so that is on the record rather than discovered later as a
surprise. What the oracle *does* fix on this file is the fabrication — line 3,
the `func H() int {` signature, was reported executable by the shipped
`range(start, end + 1)` expansion and is not code at all.

### Why it is not simply a defect to fix

`carve-assets/P27/BLOCKED-grammar.md` §3 already names this precisely: it is
"line granularity's own limit — `coverage.py` shares it", unlike the comment
and closing-brace cases, which are specific to block extent and ARE fixed. A
verdict's wire schema speaks in line numbers; distinguishing two statements on
one line needs a column-granular claim, which is a schema cut. So this is a
**known and documented boundary of the R1 claim**, not an open bug — filed so
that a future reader who notices it does not re-derive the analysis, and so
that any future proposal to fix it is costed honestly as a schema cut.

### Exposure, stated plainly

The direction is toward false PASS: a genuinely untested func literal, or any
second statement sharing a line with a covered one, is counted as covered.
In real gofmt-clean Go the shape is uncommon (a func literal inline in an
assignment, a `switch` case body on the `case` line, `x := 1; y := 2`), and
`coverage.py` lanes have carried the identical limit since P06 without
incident — but "uncommon" is not "absent", and the honest statement is that a
Go R1 line claim is statement-granular **to the line**, not to the statement.

### The fix, if it is ever ruled worth it

1. Rule, as an A-row, whether a Go R1 claim should be able to express
   "this line contains an uncovered statement" at all — the alternatives are
   leaving it as documented (today's answer), a per-line partial marker, or
   full column granularity.
2. If ruled yes: a wire field, in schema + dataclass + `verify.py` (the three
   places), at the next schema cut — never a producer-side upgrade (A-138/A-170).
3. `assay.statement_attribution.attribute_statements` already has the data it
   would need: it holds each block's own `count` and `stmt_lines` before the
   executed-wins union collapses them, so the fix is a representation
   question, not a measurement one.

### Acceptance

- [ ] the ruling recorded as an A-row, naming the three alternatives above;
- [ ] if ruled to fix: the wire field in all three places, plus a test over
      the REAL committed `coverage-lit.out` asserting line 4 reports the
      uncovered statement — and a companion test proving an ordinary
      single-statement line does NOT, so the marker is not vacuous;
- [ ] CONSUMERS' Go section states the limit either way, in the same
      paragraph that describes what a Go R1 line claim means;
- [ ] `test_lit_go_drops_the_fabricated_signature_but_still_launders_line_four`
      updated (it asserts today's behaviour deliberately, so it MUST go red
      when this is fixed).

---

## B056 — `test_verdict_schema_is_packaged.py`'s docstring states a measurement that no longer holds: the `package-data` stanza it defends is inert, so its named negative is currently unreachable

**Filed 2026-08-31, Wave C, as a side finding while packaging the Go helper.**
Recorded as decision **A-396**. Not fixed here: the fix is a real call, not a
typo repair.

### What the test says

`tests/test_verdict_schema_is_packaged.py`'s module docstring names its
negative — *"the schema is not declared as package data, so it exists in the
source tree and vanishes on install"* — and then states a measurement:

> Measured in the gate image while writing this: with
> `[tool.setuptools.package-data]` the wheel carries
> `assay/schemas/verdict.schema.json`; without it the wheel carries only
> `assay/__init__.py`, `assay/cli.py`, `assay/config.py`, `assay/errors.py` and
> `assay/verdict.py`. **Both sides are real.**

### What is true now

Both sides are NOT real any more. Built from the current tree with the entire
`[tool.setuptools.package-data]` stanza deleted, the wheel still carries
`assay/schemas/verdict.schema.json` — and 47 members in total, not five.
`setuptools_scm` installs a git file finder, and setuptools'
`include_package_data` defaults to true under pyproject metadata, so every
GIT-TRACKED file under the package directory ships regardless of the stanza.

So the test's stated negative cannot currently be produced by the change it
names. The test still PASSES, and what it asserts (the schema is in the wheel,
and resolves from inside the venv) is still worth asserting — but it can no
longer fail for the reason it says it exists to catch. That is AGENTS.md's
"a check is only as strong as what it actually compares": the message states a
conclusion about package-data that the comparison no longer tests.

### Why it is not obvious which way to fix it

1. **Correct the docstring only.** Cheapest and honest, but leaves a test whose
   stated purpose no longer has a reachable failure mode.
2. **Make the negative reachable again** — assert the outcome under a build
   with the git finder disabled (a tree without `.git`, which is exactly the
   build `[tool.setuptools_scm]`'s own `fallback_version` anticipates). This
   restores a real two-sided check and would cover `A-029` properly, but adds a
   second wheel build to the suite.
3. **Drop the package-data declarations** as genuinely inert and rely on the
   git finder. Rejected on sight for the schema (A-029 is a consumer-facing
   guarantee and should not rest on git tracking), but naming it here so the
   next reader does not have to re-derive why.

Whichever is chosen applies identically to `tests/test_go_helper_is_packaged.py`,
whose docstring already states the corrected position and asserts the OUTCOME
rather than the mechanism — so it is unaffected either way, and is the shape
option 1 would move the sibling toward.

### Acceptance

- [ ] the ruling recorded as an A-row naming the three options above;
- [ ] `test_verdict_schema_is_packaged.py`'s docstring no longer states a
      measurement that a re-run would refute;
- [ ] if option 2: a build with the git file finder unavailable, asserting the
      schema is ABSENT without the stanza and PRESENT with it — the two-sided
      check the docstring currently claims;
- [ ] whatever is decided, the same treatment applied to the Go helper's own
      packaging test, so the two do not drift apart again.

---

## B057 — the Go canary and union tests now prove their subject against a DOWNGRADED adapter: `requires_statement_attribution=False`, because a real Go lane needs a toolchain the gate image does not have

**Filed 2026-08-31, Wave C, while wiring A-392's guard.** Not a defect in the
shipped code — the shipped `GoAdapter` declares `True` — but a real, tracked
gap between what those tests exercise and what a consumer runs.

**RESOLVED 2026-09-02 (Wave C generation 6), all three boxes.** F008-A4's
fixture regeneration removed both shortcuts rather than documenting either:
no Go test in the suite now judges a downgraded adapter or an uncorrected
profile. The narrative below is kept as filed — it is the reasoning that
selected the fix, and its "why it is filed rather than fixed here" paragraph
is exactly what F008-A4 went on to do.

### What happened

`GoAdapter.requires_statement_attribution = True` makes
`evaluate_coverage`/`evaluate_targets` refuse an uncorrected block profile
(A-392), and the correction is a real Go subprocess (A-217: a Python
re-implementation of `cmd/cover`'s segmentation is not an acceptable
substitute). Thirteen pre-existing tests then went red, all of them Go tests
that judge committed, pre-generated coverprofiles with **no toolchain**
(A-042/A-087/A-107 — this devcontainer has none, and `tester-unified:local`,
which runs the registered gate, has none either).

Two different shortcuts were taken, and they are not equally cheap:

1. **`tests/conftest.py::as_pre_oracle_attributed`** — used by
   `test_adapters_go_union_fidelity.py`, `test_adapters_go_python_equivalence.py`
   and `test_adapters_go_registration.py`. It sets the flag and leaves the line
   sets untouched. For the two files whose profiles are HAND-BUILT line sets
   this is exact: those sets are already statement-granular, and the flag
   merely says so. For `test_adapters_go_union_fidelity.py`, whose profiles are
   parsed from the committed `hello.out`, the sets are the naive expansion
   A-234 already records as stale.
2. **`tests/test_canary_go_pipeline.py::_PreOracleGoAdapter`** — a subclass of
   the real adapter with the declaration flipped to `False`. Everything the
   file tests (cause-sensitivity, the four INCONCLUSIVE causes, the real
   `inject_uncovered_line`, the real union) is unaffected; what is lost is that
   no Go canary is proven statement-granular anywhere.

### Why it is filed rather than fixed here

Fixing (1) properly IS F008-A4 (fixture regeneration, wave item 3): the correct
new expectations depend on running the oracle over `hello.go`/`greet.go`
through `tester-unified-go:local` and re-deriving every asserted set. A-234's
own warning applies exactly — swapping in a real profile before the
expectations are re-derived replaces a wrong profile with a real one still read
as statement truth, which is the conflation A-O19 exists to remove.

Fixing (2) needs a decision this entry does not pre-empt: either the canary Go
tests grow a canned oracle (the profile-derived blocks would have to be threaded
through `canary.run_go_canary`, which reads its artifacts itself), or the file
splits into a logic half on the double and a real half that runs only where a
Go toolchain exists — which the registered gate image does not provide.

### Acceptance

- [x] `test_adapters_go_union_fidelity.py`'s expectations re-derived from the
      real oracle, and `as_pre_oracle_attributed` dropped from that file
      (F008-A4). **Landed 2026-09-02 (Wave C generation 6).**
      `tests/fixtures/go/hello/hello.out` is now real `go test -coverprofile`
      output for the committed source bytes (`32.32,34.2 1 1` /
      `38.35,40.2 1 0`), and the module joins it against
      `carve-assets/P27-recarve/fixture-oracle.json` — the real oracle's
      output over the same bytes — with the production
      `attribute_statements`. The asserted sets went from `{29,30}`/`{36,37}`
      to `{33}`/`{39}`: 2 executable lines, not 4. A named control test
      asserts the naive expansion of the SAME real profile is
      `{32,33,34}`/`{38,39,40}`, which is the concrete form of A-234's
      warning — regenerating the bytes alone would have made the module
      assert three wrong lines instead of two. Provenance, the raw run and
      the per-fixture derivation table: `P27-recarve/PROVENANCE.md`.
      `as_pre_oracle_attributed` is gone; the remaining hand-built-line-set
      callers use `conftest.as_statement_attributed`, which now REFUSES a
      multi-line block extent instead of trusting the caller.
- [x] a decision recorded on the canary shortcut, and `_PreOracleGoAdapter`
      either removed or reduced to the half that genuinely needs it.
      **REMOVED 2026-09-02, same change — the shortcut fell out rather than
      needing the decision this entry anticipated.** The controller's DA-9
      (`vbpub@53eba55b`) said to close this box only if F008-A4's
      regeneration made it fall out, and it did: `greet_control.out` and
      `greet_transformed.out` are now real toolchain output, and
      `test_canary_go_pipeline.py` corrects each with the real oracle
      document before `run_go_canary` sees it. A genuinely
      statement-attributed profile satisfies A-392's guard, so the SHIPPED
      `GoAdapter` — `requires_statement_attribution=True`, no override of
      any declaration — judges these fixtures directly. Neither option this
      entry laid out was needed: no canned oracle threaded through
      `run_go_canary` (which still reads its own artifacts), no file split.
      The canary is now proven cause-sensitive at statement granularity
      (`{36,37}` missing, not `{35,36,37,38}`), which is precisely what the
      double could not prove.
- [x] whichever survives, a test that goes RED if the shipped adapter's
      `requires_statement_attribution` is ever flipped to `False` — so the
      double can never quietly become the product. **Landed 2026-09-01
      (Wave C generation 4):**
      `test_adapters_go_registration.py::test_the_adapter_the_REGISTRY_hands_a_lane_is_the_undowngraded_one`
      asserts it of the object a real lane RESOLVES, not merely of
      `GoAdapter()`, and uses `type(...) is GoAdapter` rather than
      `isinstance` — deliberately, because `_PreOracleGoAdapter` is a
      subclass and would satisfy `isinstance` while carrying the flipped
      declaration. The other two boxes stay open and both still depend on
      F008-A4, which is now blocked behind **B059**.

---

## B058 — srdm's `covergate` classifies a cover block's whole extent as executable, so its own coverage floor measures more lines than Go has statements

**Filed 2026-08-31, Wave C, while reading `covergate` for F008-A5's
qualification.** Not an assay defect and not a blocker for this wave — assay's
side is fixed. Filed because the qualification is about to compare the two,
and because the finding has a consequence for srdm's own gate that srdm
cannot see from inside itself.

### What was found

`shared-ramdisk-depot-manager/tools/covergate/profile.go`'s
`ParseCoverProfile` expands every profile record across its whole line range:

```go
for l := start; l <= end; l++ {
    if count > 0 { fc.Executed[l] = true; delete(fc.Missing, l); continue }
    if !fc.Executed[l] { fc.Missing[l] = true }
}
```

and `FileCoverage.Executable(line)` is `Executed[line] || Missing[line]`. So a
line is "code" iff it falls inside some block's extent. Function signature
lines, `case` labels, closing braces and statement-continuation lines are all
inside a block extent and are all counted. The doc comment states the premise
outright: "a block spans a range of lines and every line in that range is
executable."

**This is byte-for-byte the rule assay removed in this wave**, and A-217's
impossibility proof applies to it unchanged: `carve-assets/P27/witness/
collision-colA.go` and `collision-colB.go` are gofmt-clean, compile under the
pinned toolchain, emit **byte-identical** cover profiles, and have statements
beginning on different lines (`{4,6}` vs `{4,5}`). Any rule that is a
function of the profile alone must answer both identically; the two correct
answers differ; so covergate is wrong on at least one of them. A-217 already
recorded this in passing ("covergate shares the inclusive convention") — what
is new here is that it is now confirmed in covergate's shipped source rather
than inferred, and that the consequence for srdm's own gate is spelled out.

### Why it matters to srdm specifically

`tools/gate.sh` runs `covergate -fail-under ${SRDM_COVERAGE_FLOOR:-75}` over
`-source internal`. The denominator is changed lines the profile "deems
executable", which over-counts. The direction is not uniformly lenient, which
is the part worth care:

* A change that adds a **tested** function inflates both numerator and
  denominator (its signature and braces land in an executed block), which
  drifts the ratio TOWARD 100% and makes the floor easier to clear than it
  reads.
* A change that adds an **untested** function inflates the denominator only,
  which makes the floor HARDER to clear than it reads — and the lines it
  names as uncovered include braces and signatures a developer cannot write
  a test for, so the remedy the output implies does not exist.

Two mitigations exist and neither closes it: `HasExecutableCode`
(`hascode.go`) excludes a file declaring no function bodies, but that is
per-FILE and cannot demote a signature line inside a file that does have
functions; and `Evaluate` considers only ADDED lines, which bounds how often
the difference is reachable without changing its direction.

### The second, separate hazard the same reading surfaced

`Evaluate`'s `fc == nil` branch splits a changed source file absent from the
profile into `NoCode` (excluded from the ratio) and `Unmeasured` (counted
uncovered), distinguished only by `HasExecutableCode`. Project memory records
covergate "silently skipping a package (P14)" in a past run; this is where
such a skip lands. `Unmeasured` is surfaced in the report but is a listing,
not a refusal, so a package lost by a `-coverpkg` or build-tag problem is
reported in the same breath as a package that genuinely lacks tests. **Any
assay-vs-covergate disagreement must be classified as extent-expansion or as
file-absence before either side is called wrong** — they are different
questions and averaging them would produce a conclusion about neither.

### What is NOT claimed

No covergate run was performed. This is a reading of covergate's committed
source, which is legitimate evidence about its ALGORITHM but is not a
measurement of its OUTPUT on any particular commit. F008-A5's qualification is
what produces that, and it is still owed.

### Acceptance

- [ ] F008-A5's qualification run, with the disagreement classified per the
      split above rather than reported as a single number;
- [ ] the finding relayed to srdm against its own backlog (cross-repo
      convention: a finding about a TOOL is filed in that tool's backlog, not
      worked around locally), with the two directions of drift named — a
      "coverage floor" that is easier to clear on tested code and harder on
      untested code is not the policy `-fail-under 75` reads as;
- [ ] a decision on whether assay should ever CONSUME a covergate verdict
      (today it does not, and this finding is a reason to keep it that way
      unless the two are bound at statement granularity, which A-208 always
      intended and A-217 explains is the only binding that is not circular).

## B059 — `go` is registered at R1, but no Go lane reachable through the shipped CLI can resolve its own coverage keys

**Filed 2026-09-01, Wave C generation 4, MEASURED end to end inside
`tester-unified-go:local` (A-334) rather than reasoned about.** This is a
BLOCKER for F008-A3/A5 and it carries an open decision ask (REPORT §"Decision
asks", DA-8): the fix is a product/design fork with three defensible shapes,
so it is filed rather than improvised.

### What was found

A Go cover profile keys every record by the package's **import path**:

```text
mode: atomic
example.invalid/harness/internal/calc/calc.go:5.24,7.2 1 1
example.invalid/harness/internal/calc/calc.go:10.29,12.2 1 1
```

while `git diff` names the same file `internal/calc/calc.go`. Stripping that
module-path prefix is exactly what `GoAdapter.module_path` exists for
(`adapters/go.py`, mirroring `covergate/main.go`'s own `stripModulePrefix`) —
and **nothing can set it through the CLI.** `cli._built_in_registry`
constructs `GoAdapter()`, whose `module_path` defaults to `""`, meaning "no
strip"; `_KNOWN_JUDGE_FIELDS` (`config.py:243`) has no key for it and
`assay run` has no flag for it. The only callers that set it anywhere in the
tree are unit tests and `tests/test_standalone.py:1745`, a consumer building
its OWN registry through the library API.

Measured, in-image, on the real toolchain, with the shipped zipapp
(`assay-4.0.1.dev26+g8d7f8740`, `judge_provenance.artifact = "zipapp"`):

```text
$ python3 <pyz> run unit --file /work/fixture/assay.toml --verdict-json /work/verdict.json
unit: ERROR/UNREADABLE_ARTIFACT (exit 2)
  commit: b7d3bb56c0dbce5135e2fa81bea89774cb2ad98a
  argv: go test ./... -count=1 -coverpkg=./... -covermode=atomic -coverprofile=.assay/cover.out
ASSAY_EXIT=2
```

and the mechanism, probed directly through the same zipapp in the same image:

```text
adapter.module_path = ''
raw keys            = ['example.invalid/harness/internal/calc/calc.go']
resolved            = {'example.invalid/harness/internal/calc/calc.go': 'example.invalid/harness/internal/calc/calc.go'}
  exists(example.invalid/harness/internal/calc/calc.go) = False
REFUSAL outcome     = ERROR
REFUSAL reason_code = UNREADABLE_ARTIFACT
REFUSAL message     = the coverage artifact carries block extents for
  'example.invalid/harness/internal/calc/calc.go', but that file does not
  exist at /work/fixture/example.invalid/harness/internal/calc/calc.go --
  the profile and the working tree are not the same revision, so its blocks
  cannot be resolved to statement positions

with module_path set= {'example.invalid/harness/internal/calc/calc.go': 'internal/calc/calc.go'}
helper identity     = go version go1.25.14
helper tool/path    = go /usr/local/go/bin/go
  internal/calc/calc.go: extent 5.24,7.2 numStmts=1 stmt_lines=[6]
  internal/calc/calc.go: extent 10.29,12.2 numStmts=1 stmt_lines=[11]
```

The second half is the control, and it matters twice over: it shows the
defect is ONLY the missing declaration — with `module_path` supplied,
everything downstream works, the real `go1.25.14` oracle runs, and the
statement lines come back as `{6}` and `{11}`, the two `return` lines, rather
than the naive `{5,6,7}`/`{10,11,12}` that would include both signatures and
both closing braces.

### Why no fixture layout avoids it

A package's import path is `<module path>/<dir relative to module root>`, and
its profile key is that plus the file's basename. For the key to equal the
repo-relative path, the module path would have to be empty, which `go.mod`
does not permit. So this is not a property of an awkward fixture: **every**
real Go module hits it. srdm hits it too — DA-6's prescribed lane
(`cwd = "shared-ramdisk-depot-manager"`, `source_roots =
["shared-ramdisk-depot-manager/internal"]`) would resolve srdm's own
`srdm/internal/...` keys to
`shared-ramdisk-depot-manager/srdm/internal/...`, which is under no source
root and matches no file.

### The second defect, which is separable and smaller

The refusal names the **wrong cause**. "the profile and the working tree are
not the same revision" is a staleness finding; the actual condition is an
unstripped module prefix, and a consumer following that message would go
looking at their commits. `derive_statement_blocks`' own input validation
cannot tell the two apart today because by the time it runs, the key has
already been through a no-op `normalize_coverage_key`.

### What is NOT claimed

That any particular fix is right. Three shapes are defensible and they are
laid out in REPORT §"Decision asks" (DA-8): a declared lane key, derivation
from the repository's own `go.mod`, or a registry that builds adapters per
lane. §4.2a's DERIVE-then-READ preference and A-007's own precedent point at
derivation; the architectural seam for it does not exist, and inventing one
would be a protocol change on top of A-397's.

### Acceptance

- [x] DA-8 ruled, and the ruling recorded as a decision row. **Ruled by the
      controller at `vbpub@3a95459e` — derive from the snapshot's own
      `go.mod`, no declared key, the registry untouched — and recorded as
      **A-404** (Wave C generation 5), which carries the member's name, exact
      signature and the rejected alternatives.**
- [x] a Go R1 lane run through `assay run` (the shipped CLI, not a
      library-built registry) on a real Go module, producing a
      statement-granular PASS and a paired FAIL that names the uncovered
      line. **`tests/qualification/test_go_r1_real.py` now drives
      `python3 <pyz> run unit --file … --verdict-json …` inside
      `tester-unified-go:local`; every assertion survived the move from the
      library driver unchanged. REPORT §35 has the transcript.**
- [x] the misattributed refusal message either fixed or explicitly kept,
      with the reason recorded. **FIXED, per A-404 (e): a profile key not
      under the derived module path now refuses naming the key, the module
      path and the `go.mod` it came from. The staleness message survives for
      its actual subject — a key that IS under the module and still names no
      file — which is recorded in `go_stmtpos._derive`'s own comment.**
- [ ] F008-A5's srdm run, which cannot start until this is closed. **Now
      unblocked; see REPORT §37 for a lane-shape correction DA-6's
      prescription needs before it can run.**

---

## B060 — `build_release.py` leaves a `zipapp-staging/` directory beside `--outdir` and never removes it, which can turn the project's own gate red

**Filed 2026-09-02, Wave C generation 5, on the controller's ruling at
`vbpub@3a95459e`.** Observed by generation 4 while building the in-image
consumer harness (BRIEF-5 §3) and left unfiled as "a one-line property of a
builder this wave did not otherwise touch"; the controller's disagreement is
on cost, not substance — an unfiled hazard that can turn assay's own gate red
is what the backlog is for.

`gate/distribution/build_release.py` writes its staging tree to
`zipapp-staging/` NEXT TO the directory named by `--outdir`, and never
removes it. The natural invocation for building from inside the repository,
`--outdir assay/dist`, therefore leaves `assay/zipapp-staging/` behind. That
path is not gitignored, so the next run of the self-hosted gate lane sees an
untracked directory in the tree it is judging and refuses
`NO_MEASUREMENT`/`DIRTY_TREE` — correctly, and for a cause that has nothing
to do with the change under judgment. The workaround is to pass an `--outdir`
outside the worktree, which is what this wave's harness and its qualification
module both do; that is a workaround, not a fix, and it is invisible to
anyone who builds the release the obvious way.

Three shapes are defensible and this entry does not pick one: remove the
staging tree on success (a `finally`, or build it under a
`TemporaryDirectory`); place it INSIDE `--outdir` so a caller who directs
output outside the tree gets both; or gitignore the path and document that
the builder leaves it. The first is the only one that also cleans up after a
consumer who never reads this entry.

### Acceptance

- [ ] a build with `--outdir <repo>/assay/dist` leaves no untracked path in
      the worktree;
- [ ] a test that would go RED if a future edit reintroduced one, asserting
      the OUTCOME (the tree is clean after a build) rather than the mechanism.

---

## B061 — the statement-position join kept only the LAST record for a repeated block, so `-coverpkg=./...` profiles reported covered code as uncovered

**Filed 2026-09-02, Wave C generation 6, FOUND by F008-A5's srdm qualification
— which is what that criterion exists for.** Fixed in the same change; this
entry is the record, not a request.

### What was found

`go test -coverpkg=./...` instruments every package into **every** test binary,
and `go test` concatenates each binary's own profile section into one file. So
one block gets one record per test binary, and only the binary that actually
executed it carries a non-zero count. srdm's real profile is 68 761 lines and
carries **20 records for every block**:

```text
srdm/internal/power/wings.go:59.22,65.3 1 0
srdm/internal/power/wings.go:59.22,65.3 1 0
srdm/internal/power/wings.go:59.22,65.3 1 1      <- the binary that ran it
srdm/internal/power/wings.go:59.22,65.3 1 0
... (sixteen more zeros)
```

`coverage_parsers/go_cover.py::parse` folds these correctly at LINE
granularity — its `hits` map is explicitly executed-wins across every record in
the whole profile — and keeps every record, unmerged, in `FileCoverage.blocks`
(A-239, deliberately: the merge is what discards the column data the
correction needs).

`statement_attribution.attribute_statements` then did

```python
parsed_extents = {block.extent: block for block in file_cov.blocks}
```

which keeps whichever record came **last**, and read `parsed.count` off it. For
`wings.go:59.22,65.3` the last record is `0`, so a block the toolchain reports
as executed was attributed to `missing`. The function's own comment claimed
"applied AFTER the loop so block order cannot matter" — true of distinct
blocks, false of repeated records for one block, and that is exactly the case
it never saw.

### Why it was invisible until now

**Every frozen P27 witness has exactly one record per block.** They are
single-file, single-package probes; nothing in the corpus, in the regenerated
F008-A4 fixtures, or in the two-package qualification fixture produces a
repeated extent. The first profile in this project's history with repeated
records is srdm's, and it exposed the defect on the first run.

### The measurement

Same checkout, same commit range, and — for the control — the byte-identical
profile file:

| | changed executable lines | covered | verdict |
|---|---|---|---|
| covergate | 684 | 639 (93.4%) | PASS at its own 75% floor |
| assay, defective | 418 | 163 (39.0%) | FAIL/`UNCOVERED_LINES`, 255 lines named |
| assay, fixed | 418 | see REPORT | — |

The 255-vs-45 gap was not extent-expansion and not file-absence; it was this.
Classifying before naming a side is what caught it: the extent-expansion
hypothesis predicts assay's denominator is SMALLER (it is) and its covered
RATIO similar (it was not), and that second half is the discrepancy that did
not fit.

### The fix

Fold repeated records for one extent executed-wins **before** anything reads a
count — the same rule the parser already applies one layer down, so the
correction can no longer downgrade a line the uncorrected profile called
executed. That invariant is now asserted directly, in addition to the
srdm-shaped repetition being asserted in three orders (a fix handling only
"the non-zero record comes first" passes one of them).

### Acceptance

- [x] repeated records for one extent fold executed-wins, in any order
      (`test_statement_attribution_go_witnesses.py::test_repeated_records_for_one_block_fold_executed_wins_not_last_wins`);
- [x] the correction can never downgrade a line the parser called executed,
      asserted as the property
      (`…::test_the_correction_can_never_downgrade_a_line_the_parser_called_executed`);
- [x] an extent all of whose records are zero stays missing — the fold must
      not launder an uncovered block (same test, final stanza);
- [x] re-run F008-A5's qualification on the fixed build and record the
      classified table against covergate (REPORT).
