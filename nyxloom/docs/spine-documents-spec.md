# nyxloom spine documents — spec (F1 contract)

> Status: spec · 2026-07-17 · the managed "direction spine" documents — format,
> schema, naming, validation. This is the CONTRACT the F1 carve implements.
> Companion to `nyxloom-operating-model.md` (the spine is §1 there).

## Format decision (resolved)

**Markdown + schema-validated YAML frontmatter** — nyxloom's existing handoff
pattern. The machine trusts **only the frontmatter** (structured, JSON-schema
validated, `nyxloom lint`-checked, diffable by the gap-engine). The markdown
**body is human narrative** the machine never parses for correctness. Result:
readable in the trove for devs, machine-safe where it matters, and it REUSES the
frontmatter+lint infrastructure — no new format, no generated-file drift. A UI
edits the frontmatter via table/form and the body via guided chat.

## The documents (numeric-prefixed by the waterfall; all in the trove, managed)

| File | Purpose | Frontmatter-heavy? |
|---|---|---|
| `1-north-star.md` | the invariant vision / WHY | no — mostly prose, minimal fm |
| `2-product-definition.md` | versioned; features + acceptance (the machine-diffed target) | YES |
| `3-roadmap.md` | ordered milestones → the next product-def version | yes |
| `4-backlog.md` | features + bugfixes ledger (table-editable items) | yes |

`decisions.md` (the `D-NNN` inbox) stays as-is, unnumbered — it's an inbox, not a
spine level.

## Frontmatter schemas (the F1 carve writes JSON schemas for each)

**1-north-star.md**
```yaml
kind: north-star
schema_version: 1
# body = the vision narrative (prose). No diffed fields: the north-star is the
# invariant WHY, rarely changes, is not diffed against code.
```
**2-product-definition.md**
```yaml
kind: product-definition
schema_version: 1
product_version: 2                    # the version this document defines
features:
  - id: F001                          # stable, unique, referenced by roadmap/backlog
    title: "..."
    acceptance: ["<checkable criterion>", "..."]   # >=1 required (EARS-style)
    status: planned|building|shipped
    milestone: M1                     # optional; which roadmap milestone delivers it
non_goals: ["<explicit out-of-scope>"]
# body = prose elaboration.
```
**3-roadmap.md**
```yaml
kind: roadmap
schema_version: 1
milestones:
  - id: M1
    title: "..."
    target_product_version: 2
    features: [F001, F002]            # each MUST exist in 2-product-definition
    status: planned|active|done
```
**4-backlog.md**
```yaml
kind: backlog
schema_version: 1
items:
  - id: B001
    title: "..."
    type: feature|bugfix
    component: "<slug>"               # optional; cheap proxy for wave-grouping (P42)
    context_estimate: small|medium|large   # carver's read-context estimate (scheduler input, task #29)
    folds_into: F001                  # optional; the product-def feature / milestone it belongs to
```

## Placement + config

- All spine docs live in the trove (managed — nyxloom may write them).
- `nyxloom.toml` gains keys: `north_star`, `product_definition`; `roadmap` and
  `backlog` are repointed to the numeric-prefixed filenames (`3-roadmap.md`,
  `4-backlog.md`). `decisions_inbox` unchanged.
- `[refs]` stays for read-only PROJECT docs (SPEC/ARCHITECTURE) the machine reads
  but never manages.

## The non-AI structural validator (extend `nyxloom lint`; token-free)

Like L1–L12 for handoffs, but for the spine — so 3rd-party edits or corruption
produce a clear "violates the standard" signal, never a silent mystery:
- **S1 (schema):** each spine doc's frontmatter validates against its JSON schema.
- **S2 (cross-doc consistency):** every `3-roadmap` milestone `features` id exists
  in `2-product-definition.features`; every product-def feature has ≥1
  `acceptance`; every `4-backlog` item `folds_into` (if set) resolves to a real
  feature/milestone.
- **S3 (naming/placement/config):** numeric-prefix filenames, trove placement, and
  the `nyxloom.toml` spine keys resolve.
- **S4 (tamper/corruption):** a spine doc present but frontmatter-invalid or of an
  unknown `schema_version` is a hard ERROR (fail-closed — never a silent skip).

Surfaced in both `nyxloom lint` and `doctor`.

## Scope boundary (what F1 is NOT)

F1 is the doc **structure + schema + validator + config keys** only. It does NOT
build: the onboarding that populates them (F2), the gap-engine that diffs
`2-product-definition` vs code (F5), or the UI editors. Ship empty/templated spine
docs + green validation; the rest builds on this foundation.
