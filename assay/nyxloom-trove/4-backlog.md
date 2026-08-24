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
**Status:** **PARTIALLY IMPLEMENTED 2026-08-24.** Shipped: baseline/per-candidate progress NDJSON,
`mutation.progress_artifact` in the v6 payload, `assay plan`, deterministic plan IDs, per-file/operator
counts and runtime estimates, and optional `budget_per_candidate`. Still open: worker identity,
resume/checkpointing, native filtering/sharding with merge validation, and the remaining acceptance
items below.

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

- [x] progress events emitted and referenced from verdict;
- [x] `assay plan` reports deterministic totals/IDs/runtime estimate;
- [ ] interrupted lane resumes without rerunning completed candidates;
- [ ] shards are provably disjoint and exhaustive;
- [x] per-candidate timeouts do not abort unrelated candidates.

## B013 — repository-only snapshots cannot provide infrastructure facts required by SQL mutation lanes

**Filed 2026-08-23 (dstdns SQL mutation blocker; consumer evidence from `dstdns-SQL-MUTATION-LANE-BLOCKER.md`).**

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

- [ ] declared infrastructure inputs resolve before snapshot execution;
- [ ] missing/unresolvable inputs refuse loudly with named keys;
- [ ] isolated commands receive only injected values, no host paths;
- [ ] SQL lanes can complete full mutant scope without reading caller state.

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
**Status:** **IMPLEMENTED 2026-08-24** as two bounded families:
`python:uuid-equality-swap` and `python:enum-comparison-swap`. Eligibility is
syntactic and conservative: an in-place UUID construction or a
`Class.MEMBER` enum access must supply semantic evidence, and only the exact
`==`/`!=` token is spliced.

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
- [x] synthetic fixtures prove each eligible/ineligible AST boundary;
- [ ] a real R2 lane demonstrates kills attributable to each admitted family;
- [ ] P126's deferred debt is re-evaluated and its disposition recorded.
