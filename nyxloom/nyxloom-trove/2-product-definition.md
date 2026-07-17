---
kind: product-definition
schema_version: 1
product_version: 1
features:
  - id: F001
    title: "Handoff carve -> dispatch -> gate -> review -> merge loop"
    acceptance:
      - "a schema-valid handoff in nyxloom-trove/handoffs/ dispatches, its declared gate runs, and a passing review merges it with --no-ff"
    status: shipped
  - id: F002
    title: "Direction spine (north-star / product-definition / roadmap / backlog)"
    acceptance:
      - "nyxloom lint validates spine doc frontmatter against schemas/spine-*.schema.json (S1-S4)"
    status: building
non_goals:
  - "Onboarding flow that interviews a user to author the real north-star/product-definition content (F2)"
  - "The gap-engine that diffs product-definition features against code (F5)"
  - "Any UI editor for these documents"
---

# nyxloom — product definition

> Placeholder (PACKAGE F1). This is a MINIMAL-VALID spine doc seeded with
> two illustrative features (the handoff loop that already ships, and this
> very spine-documents feature under construction) — it is NOT a complete,
> hand-authored product definition. Do not treat `features` above as
> exhaustive; onboarding (F2) is what will bring this document up to date
> with nyxloom's real feature set.

See `docs/spine-documents-spec.md` for the frontmatter contract, and
`docs/SPEC.md` / `docs/ARCHITECTURE.md` for the authoritative, detailed
design this placeholder does not attempt to restate.
