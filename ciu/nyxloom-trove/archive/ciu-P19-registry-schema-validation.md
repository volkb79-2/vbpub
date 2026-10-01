---
schema_version: 1
id: ciu-P19-registry-schema-validation
project: ciu
component: deploy
title: "Pydantic-validated [registry.*] sub-tables CIU itself already consumes (postgresql.database, consul.token_vault_path), wired into ciu check's stage 7, plus an optional consumer-declared validate(config) callable for everything else"
tier: implement-2
input_revision: "370ea8141f7f69399a751f2d5731a8ccf5419921"
source: {kind: backlog, ref: "docs/BACKLOG-2026-08-24.md#CIU-QOL-12 stage 7 + #V8-PREP-8", design_ref: "docs/CIU-V8-TESTING-GATE-PROPOSAL.md §2.6"}
stack: none
depends_on: [P18]
session: fresh
scope:
  touch:
    - "src/ciu/deploy.py"
    - "src/ciu/provisioning.py"
    - "pyproject.toml"
    - "tests/tests/test_ciu_provisioning.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/handoffs/ciu-P19-registry-schema-validation.md"
    - "nyxloom-trove/reports/ciu-P19-registry-schema-validation-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/composefile.py"
    - "src/ciu/hooks_runner.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-scope-decision-recorded
    observable: "The LOG explicitly records this scoping decision (it is a deliberate narrowing of the backlog/proposal text, not an oversight): CONFIG.md today documents exactly TWO fields CIU itself reads from [registry.*] for its own provisioning probes -- [registry.postgresql].database (str, consumed by the pg:schema/<name> probe) and [registry.consul].token_vault_path (str template, consumed by the consul:token/<svc> probe). Grep docs/CONFIG.md and src/ciu/provisioning.py yourself to confirm this is still accurate at your commit -- do not assume it from this handoff alone. This package ships Pydantic models for ONLY these two CIU-consumed fields (the proposal's Option B, applied to what CIU is actually coupled to), NOT invented shapes for Redis/MinIO/Vault/PostgreSQL-users tables CIU has never read (no such shape exists anywhere in this repo to validate against, and inventing one would be an ungrounded guess the estate's own AGENTS.md 'defaults are hazards' rule warns against). For everything else under [registry.*], add ONE additive extension point: an optional module-level validate_registry(config) -> list[str] callable a CONSUMER may declare (mechanism: your call -- e.g. discovered via an existing hook file, or a new declared path in global config; document whichever you choose and why), matching the proposal's Option C for custom/consumer-owned shapes. If, after grepping, CIU actually reads MORE registry.* fields than these two, extend the two Pydantic models to cover what you find -- do not silently under-scope relative to what CIU ACTUALLY consumes today."
    negative: "shipping Pydantic models for PostgreSQL users/roles, Redis ACL entries, MinIO buckets, or Vault mounts when no such shape exists anywhere in this repo's own code or docs to validate against (an invented schema that happens to be wrong is worse than no schema -- it would reject a legitimate consumer table shape CIU never actually needed to constrain)"
    gate: "tester-unified"
  - id: O2-pydantic-dependency
    observable: "pydantic is added as a NEW optional extra in pyproject.toml (e.g. ciu[registry], following the EXACT precedent of ciu[schema]'s jsonschema optional-extra pattern for CIU-37/S5.7 -- grep pyproject.toml for how that extra is declared and mirror it). A declared [registry.postgresql] or [registry.consul] table is validated ONLY when pydantic is installed; if a consumer's config declares fields matching these tables but pydantic is ABSENT, ciu check fails LOUDLY naming the missing optional extra and how to install it -- it does NOT silently skip validation (same anti-pattern the S5.7 precedent already avoids: 'declared schemas fail loudly when jsonschema is absent, never silently skip')."
    negative: "making pydantic a hard (non-optional) dependency of ciu itself; silently skipping registry validation when pydantic is absent instead of a loud, actionable error"
    gate: "tester-unified"
  - id: O3-wiring
    observable: "provisioning.py (or a new small module it exposes) gains RegistryPostgresql/RegistryConsul Pydantic models (or a documented equivalent structure if you decide a single combined model reads better -- your call) and a validate_registries(config) -> list[str] function. ciu check's stage 7 (the insertion point P18 left explicitly marked) calls this function and aggregates findings into the SAME exit-code contract P18 established (stage failures -> exit 2, never 1)."
    negative: "a stage 7 that duplicates P18's stage-walking/aggregation machinery instead of plugging into the insertion point it left"
    gate: "tester-unified"
  - id: O4-docs
    observable: "docs/CONFIG.md's existing [registry.*] section documents the two validated fields' types/constraints explicitly (it already shows them as examples; add the validation behavior: what a malformed database (non-string) or token_vault_path (missing {svc} placeholder, if you choose to enforce that -- verify against provisioning.py's actual template substitution before adding a constraint that isn't really required) now produces). docs/SPEC.md documents this under S13 or S17 (your call, state which). CHANGES.md Unreleased entry names pydantic as a new optional extra. docs/BACKLOG-2026-08-24.md's CIU-QOL-12 stage-7 note and V8-PREP-8 row both -> reference this package, with the narrowing decision (O1) stated plainly so a future reader doesn't assume all five kinds got Pydantic models."
    negative: "documenting a token_vault_path constraint (e.g. 'must contain {svc}') without first verifying provisioning.py's actual substitution logic requires it -- an invented constraint that's stricter than the real code would reject legitimate configs"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "grepping CONFIG.md/provisioning.py at your commit shows CIU now reads MORE than the two documented registry.* fields (or fewer) -- this is not a blocker, it's new information: extend or narrow the two models to match what you actually find, and note the discrepancy from this handoff's O1 baseline in your LOG"
mutexes: [merge-lane]
review_focus:
  - "the O1 scope-narrowing rationale actually holds -- re-grep CONFIG.md/provisioning.py yourself at review time; if a real Redis/MinIO/Vault registry shape DOES exist somewhere in this repo the implementer missed, that's a real finding to raise, not a nitpick"
  - "pydantic absence produces a loud, actionable failure, never a silent skip"
---

# ciu-P19 — registry schema validation, CIU-consumed fields only (V8-PREP-8 + QOL-12 stage 7)

## Context to read first
1. `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §2.6 (~line 617-647) — the three
   options analysis and its recommendation ("Option B for well-known types,
   Option C for custom"). This package implements exactly that
   recommendation, but "well-known types" is scoped to what CIU itself is
   ACTUALLY coupled to today (see next item), not the proposal's aspirational
   list of five provisioning kinds — no Redis/MinIO/Vault/PostgreSQL-users
   registry shape exists anywhere in this repo's code or docs to validate
   against, and this handoff deliberately does not invent one.
2. `docs/CONFIG.md` — the `[registry.*]` section (~line 359-376): the ONLY
   two fields CIU documents itself reading — `[registry.postgresql].database`
   and `[registry.consul].token_vault_path`.
3. `src/ciu/provisioning.py` — grep for where these two fields are actually
   read (search `registry.get`, `"postgresql"`, `"consul"`, `token_vault_path`,
   `database`) to confirm current consumption and any implicit shape
   constraint (e.g. does the `{svc}` placeholder in `token_vault_path` get
   substituted via `.format()`, meaning a value without `{svc}` wouldn't
   crash but would silently produce a wrong path — worth validating, or not;
   your judgment call, grounded in what you actually find).
4. `pyproject.toml` — the existing `ciu[schema]` optional extra (for
   `jsonschema`, CIU-37/S5.7) — mirror its exact declaration shape for a new
   `ciu[registry]` (or your chosen name) extra wrapping `pydantic`.
5. `nyxloom-trove/handoffs/ciu-P18-config-check-hook-preflight.md` — P18's
   "Decision table" names stage 7 as an explicitly marked insertion point in
   `action_check`; find it (P18 lands before this package runs) and plug into
   it rather than re-deriving the stage-walking/aggregation structure.

## Work
1. `pyproject.toml`: new optional extra for pydantic (O2).
2. `provisioning.py`: `RegistryPostgresql`/`RegistryConsul` Pydantic models
   (or your documented equivalent) + `validate_registries(config) -> list[str]`,
   loud failure when pydantic is absent and a registry table needing it is
   declared (O1, O2).
3. `deploy.py`: wire `validate_registries` into `ciu check`'s stage-7
   insertion point from P18 (O3).
4. Tests: malformed `database`/`token_vault_path`, pydantic-absent loud
   failure (import-mocking or an actual uninstalled-extra test seam — check
   how CIU-37's jsonschema-absent test does this and mirror it), consumer
   `validate_registry` extension point exercised.
5. Docs per O4.
6. LOG at `nyxloom-trove/reports/ciu-P19-registry-schema-validation-LOG.md`,
   explicitly recording the O1 scoping decision and its grounding.

## Environment setup
Same worktree/venv as prior packages:
`cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu && .venv/bin/pip install -e '.[test,ssh,registry]' && .venv/bin/python run-ciu-tests.py`
(add whatever extra name you actually chose in step 1 above).

## BLOCKED rule
Per `escalate_if` above (not really a blocker — new information to act on).
Forbidden workaround: inventing a registry shape for a table CIU has never
read just to satisfy the proposal's "five kinds" language.
