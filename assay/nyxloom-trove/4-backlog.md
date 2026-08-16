---
kind: backlog
schema_version: 1
items:
  - {id: B001, title: "SQL/DDL source-mutation adapter — design/probe checkpoint after P28 and before P29. Preferred path recorded; not yet carved.", type: feature, component: adapters, context_estimate: medium, folds_into: F013}
  - {id: B002, title: "Adopt cmru for assay's release process — design checkpoint. STOPPED SHORT of landing: two named blockers, and it edits release keys seven other products share.", type: feature, component: distribution, context_estimate: medium, folds_into: F014}
  - {id: B003, title: "Ship a zipapp (.pyz) beside the wheel as a second release artifact. Mechanically proven end to end; blocked only on B002's release path.", type: feature, component: distribution, context_estimate: small, folds_into: F014}
  - {id: B004, title: "Provenance as VERIFIED evidence, not merely recorded: ciu provenance --json as assay's first Tier-2 adjudicated integration. Hard-blocked on ciu CIU-20; the recorded half already ships via A-254.", type: feature, component: evidence, context_estimate: medium}
  - {id: B005, title: "A whole-module / per-callable coverage judge — an R1 mode that asserts a coverage FLOOR over a declared owned module (or callable span) independent of the base..HEAD diff. Consumers running method-reconciliation programs need whole-method rigor the changed-line judge cannot express; today they bolt it on with --cov-fail-under in the argv, invisible to the verdict.", type: feature, component: evaluate, context_estimate: medium}
  - {id: B006, title: "Snapshot substrate ergonomics: (a) an absolute-target symlink ANYWHERE in the tree fails every R1+ lane even when outside all source_roots — offer a source-root-scoped snapshot walk or a symlink allow/skip knob; (b) reserve_output refuses a missing coverage-artifact parent and never mkdirs, so a gitignored artifact dir is absent from the tracked-only snapshot — create the parent inside the snapshot, or document the tracked-dir requirement.", type: bug, component: isolation, context_estimate: small}
---

# assay — backlog

Items proposed but not carved. One line each in the frontmatter; rationale
below, so a proposal cannot be adopted without the argument that produced it.

---

## B001 — a SQL/DDL adapter, and why PostgreSQL projects are the cheapest place to prove R2/R3

**Proposed by:** dstdns, 2026-08-10, out of the CW2a corpus-schema wave.
**Status:** accepted for a Sol design/probe checkpoint after P28 and before
P29; not accepted for implementation yet. A-215 records the disposition.

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
**Status:** **IMPLEMENTED 2026-08-11 (A-249/A-250)**, except the first real
release (step 6), which needs `main` pushed plus explicit authorisation — see
A-250. A-247 recorded the original stop-short and A-248 lifted it. The two
blockers below became rulings: adopt cmru's orchestration, decline its build;
the release manifest is authoritative over a `.sha256` sidecar. **The plan
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
**Status:** **IMPLEMENTED 2026-08-11 (A-249)** as part of
`gate/distribution/build_release.py`; publication of the `.pyz` asset itself
waits on A-250's release step. Two things this scoping did not find, both caught
by building for real: the WHEEL is also non-reproducible without
`SOURCE_DATE_EPOCH`, and `zipapp -m` would have discarded every non-zero exit
code.

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
half is **hard-blocked on ciu CIU-20**, which does not exist. A-256 rules the
adjacent same-instance question as discipline, not schema.

### What already ships — do not re-carve this part

A lane declaring `env_required` for a provenance variable puts it in the
artifact's `env_effective` verbatim, on every outcome including refusals, and
the artifact verifies clean. Measured, not designed: a real `S3` lane produced
`env_effective` carrying `CIU_IMAGE_REVISION=1b369e23` and
`CIU_INSTANCE_ID=dstdns-pkgP96`. **No verdict-schema change was needed and none
should be added** (A-255).

### What is blocked, and on exactly what

| step | needs | status |
|---|---|---|
| Recorded, caller-asserted | `env_required` (A-254) | **SHIPPED** |
| Recorded, ciu-**attested** | **ciu CIU-21** — inject the image's own baked `org.opencontainers.image.revision` as an env var | blocked; zero assay work when it lands |
| **Verified** (adjudicated Tier-2 evidence) | **ciu CIU-20** — `ciu provenance --json`, a closed machine-readable verdict | blocked; this backlog item |
| **Enforced** (refuse on mismatch) | a new `ReasonCode` → closed-enum widening → v6-class | not proposed |

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

## B005 — a whole-module / per-callable coverage judge (R1 without a diff)

**Proposed by:** dstdns's DESIGN-AUTHORITY reconciliation program, 2026-08-16, on
the first package that adopted an R1 coverage lane (`redirect_chain`, a
docstring+dead-code reconcile of `libs/common/src/common/redirect_chain.py`).
**Status:** proposed. dstdns has a working stopgap in production use; this is the
first-class replacement.

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

## B006 — snapshot substrate ergonomics: two papercuts that fail a lane for reasons outside it

**Proposed by:** dstdns's reconciliation program, 2026-08-16, both hit on the very
first R1 lane run against a real multi-service repo. Both are already worked around
in dstdns `main`, so this is a usability/robustness item, not a blocker for us — but
each cost a debugging round and each fails a lane for a reason that has nothing to
do with the change under test.

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

**Proposed:** either scope the snapshot walk / symlink check to the lane's declared
`source_roots` (a symlink assay will never read cannot break isolation), or offer
an explicit allow/skip knob (`isolation.allow_symlinks = [...]` or a snapshot
`exclude`) so a consumer can attest "this path is outside what I test" once, rather
than being forced to mutate unrelated tracked files. The security property is
preserved: a symlink outside every source root is never materialized into a lane's
working tree, so it cannot exfiltrate.

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

**Proposed:** assay owns the artifact path (it reserves it), so it can safely
create the artifact's parent directory inside its own ephemeral snapshot before
running the argv — a mkdir of a path it is about to write is not a hermeticity
violation. Failing that, detect the missing-parent case and emit a diagnostic that
names the fix ("declared coverage artifact `.assay/x.json` — its parent `.assay/`
is not present in the snapshot; track the directory or choose a tracked path"),
rather than the generic `UNREADABLE_ARTIFACT`.

### Evidence available on request

dstdns `main`: commit `c359a6b1` (symlink removal, with the assay error verbatim in
its message), `9f42acdc` (`.assay/.gitkeep` + gitignore), and the two intermediate
`assay run redirect_chain` verdicts showing `GIT_FAILED` then `UNREADABLE_ARTIFACT`
then `PASS`.
