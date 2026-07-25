# `schemas/` — root-level reference examples (NOT the schema source of truth)

This directory holds **human-facing reference examples** that ship in the source
tree but are **not** part of the installed package and are **not** where any code
loads schemas from.

**The single source of truth for validated JSON schemas is
[`src/nyxloom/schemas/`](../src/nyxloom/schemas/).** Those files are packaged
(`pyproject.toml`: `nyxloom = ["schemas/*.json"]`) and loaded at runtime via
`importlib.resources.files("nyxloom.schemas")` (see `frontmatter.py`, `lint.py`,
`backlog_items.py`). If you are validating an event, a handoff frontmatter, a
statefile, a backlog item, a spine document, or the project config, that schema
lives under `src/nyxloom/`.

## What lives here

| File | Why it's here |
|---|---|
| `routes.example.toml` | An **example** routing config for operators wiring up `~/.local/state/nyxloom/routes.toml`. It is a reference, not a schema, and is not consumed by the package — so it belongs beside the docs that cite it, at repo root. |

## Why this dir no longer holds schema copies

It used to carry duplicate "published" copies of the packaged JSON schemas
(`event`, `handoff-frontmatter`, `statefile`). Those copies had **no readers**
(every loader uses the packaged `nyxloom.schemas` resource) and drifted out of
sync **unguarded** — `handoff-frontmatter` silently diverged while only
`statefile` had a byte-identity guard test. Two hand-maintained copies with one
alarm is the textbook band-aid; the structural fix (canonical
`reference/LESSONS.md` **L1**) is one source of truth. The duplicates and their
guard test (`tests/test_schema_sync.py`) were removed in **factory-hardening A**
(`docs/plan-factory-hardening.md`). Add new schemas under `src/nyxloom/schemas/`
only.
