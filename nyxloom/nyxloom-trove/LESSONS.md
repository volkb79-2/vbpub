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
`scope: product` · `upstream: proposed`

The statefile JSON schema exists as two hand-maintained copies
(`schemas/statefile.schema.json` + the packaged `src/nyxloom/schemas/statefile.schema.json`);
only the packaged copy is referenced (pyproject `nyxloom = ["schemas/*.json"]`)
and it diverged twice (D-CORRECT-2, F017). Per canonical **L1** the current
byte-identity guard test is a band-aid — the structural fix is one source of
truth. The top-level `schemas/` dir also holds `event.schema.json`,
`handoff-frontmatter.schema.json`, `routes.example.toml`; each must be classified
(true duplicate of `src/nyxloom/schemas/` → remove/generate; genuine example/
reference → keep + document why) before deleting anything. Tracked as item **A**
in `docs/plan-factory-hardening.md`. This is `scope: product` because the
one-source-of-truth-for-shipped-schemas principle helps every consumer.

<!-- Append new project-local lessons below. Product-scoped ones also get an
     upstream proposal; project-scoped ones stay here. -->
