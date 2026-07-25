# nyxloom (self-hosting project) — LESSONS (project-local)

> **Sibling of `reference/LESSONS.md`.** This is the **writable** lessons surface
> for the nyxloom-building-nyxloom project. The factory and any working agent
> append discovered lessons HERE — never directly to the shipped
> `reference/LESSONS.md`. See that file for the placement & promotion model.
>
> **Each entry:** `scope: project | product`. A `product`-scoped lesson also emits
> an upstream proposal (`upstream: proposed`) for a maintainer to integrate into
> `reference/LESSONS.md`; mark it `upstream: integrated (ref)` once accepted. A
> `project`-scoped lesson stays here.

---

## PL1 — Dual statefile schema is structural debt; de-duplicate it
`scope: product` · `upstream: integrated (ref: reference/LESSONS.md L1)` · **RESOLVED (factory-hardening A)**

The statefile JSON schema existed as two hand-maintained copies
(`schemas/statefile.schema.json` + the packaged `src/nyxloom/schemas/statefile.schema.json`);
only the packaged copy was referenced (pyproject `nyxloom = ["schemas/*.json"]`)
and it diverged twice (D-CORRECT-2, F017). Per canonical **L1** the byte-identity
guard test was a band-aid — the structural fix is one source of truth.

**Resolution (factory-hardening A).** Classification confirmed the top-level
`schemas/*.json` copies (`event`, `handoff-frontmatter`, `statefile`) had **no
readers** — every loader uses `importlib.resources.files("nyxloom.schemas")` (the
packaged dir) and `handoff-frontmatter` had already drifted stale *unguarded*. All
three top-level copies and the guard test (`tests/test_schema_sync.py`) were
removed; `src/nyxloom/schemas/` is the single source of truth; `routes.example.toml`
(a genuine example, no packaged twin) stays under `schemas/` with a README
documenting the dir's reference-only purpose. The general principle
(one-source-of-truth for shipped schemas) is already integrated upstream as
canonical **L1**, which uses this very incident as its worked example.

## PL2 — The gate's value is a *composition*; nyxloom requires an interface, offers a toolkit, mandates no infra
`scope: product` · `upstream: proposed`

Factory-hardening A/F validated that "the gate catches real bugs" — but the value
is not any single component (not docker, not `tester-unified`). It is a **stack**,
and only one layer is infra:

1. **INFRA** — a runtime-faithful *isolated* environment, never the dev cockpit
   (project-owned, expressed entirely inside the gate's `argv`).
2. **TOOLKIT** — a changed-line completeness floor (`coverage_gate.py`) and mutation
   (`mutation_gate.py`): nyxloom-shipped but **opt-in** and ecosystem-specific.
3. **CONTENT** — the project's own invariant/behavioral tests. (F's two real catches
   were a config-schema invariant test + the coverage floor — neither is infra.)
4. **DISCIPLINE** — GATE→VERDICT→MERGE read as *separate* steps (canonical **L4**)
   + SOLO serialization across gates.

**The generalization for "all kinds of projects."** nyxloom must **REQUIRE only the
interface**: a `[gates.*]` command that runs isolated at a commit and exits non-zero
on failure with nothing masking it (the `{worktree}` placeholder is the sole
integration seam; `gate_runner.py` + the daemon revert path is the universal
orchestration, hardened by F). It should **OFFER the toolkit** as an opt-in menu
(`coverage_gate`/`mutation_gate` for Python; the *interface* generalizes to
`cargo llvm-cov`/`nyc`). It must **NOT mandate specific infra** (docker, pytest,
`tester-unified`) — that breaks config-driven onboarding and locks the daemon to one
ecosystem. Proof it already generalizes: dstdns (docker `test-runner`, **no** coverage
floor) and nyxloom (docker `tester-unified`, **with** floor) run under one daemon
today with wholly unrelated gate commands.

**Open gap (→ folds into factory-hardening D).** nyxloom trusts but cannot *verify* a
project's gate quality — `argv=["true"]` would merge everything. Close it with a
declared per-project rigor contract (`asserts=[...]`) that feeds review-depth
selection, optionally probe-verified by an adversarial meta-gate (must reject a
canary). See `docs/plan-factory-hardening.md` §D.

<!-- Append new project-local lessons below. Product-scoped ones also get an
     upstream proposal; project-scoped ones stay here. -->
