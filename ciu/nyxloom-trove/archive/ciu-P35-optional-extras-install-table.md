---
schema_version: 1
id: ciu-P35-optional-extras-install-table
project: ciu
component: docs
title: "README.md gains one consolidated 'Optional extras' table (ssh/schema/registry -- the three real runtime extras in pyproject.toml's [project.optional-dependencies], excluding dev-only `test`) naming what each unlocks and its exact pip install command, so a consumer setting ciu up for the first time can decide proactively instead of discovering each extra reactively, one at a time, only after hitting that feature's own runtime refusal -- each extra is ALREADY individually documented at its own feature's point of use (CONFIG.md:485/765/1129, CONSUMERS.md:604-605) and that per-feature documentation is NOT being duplicated or replaced, only supplemented with one upfront index"
tier: implement-1
input_revision: "2c842ba0"
source: {kind: operator-report, ref: "operator question 2026-08-25: 'ciu consumers.md should [state] which sibling packages are suggested to be installed next to the wheel itself... or do we bundle this with the wheel?' -- controller investigated pyproject.toml (3 real extras: ssh/schema/registry, all already documented per-feature but nowhere consolidated) and _load_pydantic's own docstring (module-scope pydantic import would hard-couple every ciu command to it) before recommending against bundling and carving this docs-only consolidation instead; operator has not yet confirmed this specific carve, dispatch only after operator confirmation is visible in the conversation"
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "README.md"
    - "CHANGES.md"
    - "nyxloom-trove/reports/ciu-P35-optional-extras-install-table-LOG.md"
  forbid:
    - "src/ciu/*.py"
    - "tests/tests/*.py"
    - "pyproject.toml"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "docs/CONSUMERS.md"
    - "docs/DESIGN-GUIDE.md"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-table-matches-pyproject-exactly
    observable: "README.md gains one table (near the existing install instructions, e.g. after the `pip install` block around README.md:148-160 -- find the right spot by reading the file's existing structure, don't guess a line number) with exactly three rows -- ssh, schema, registry -- each naming: the exact `pip install 'ciu[<name>]'` command, the underlying package(s) pinned (read straight from `pyproject.toml`'s `[project.optional-dependencies]`, do not hardcode a version that could drift -- either omit versions from the table or reference pyproject.toml as the source of truth explicitly), and ONE sentence naming which ciu feature/verb needs it (S14 remote transport for ssh; S5.7 configfile schema validation for schema; S13.4b `[registry.*]` validation in `ciu check` for registry). The dev-only `test` extra is explicitly NOT a row in this table (it's for ciu's own contributors, not consumers)."
    negative: "a table that includes `test`, hardcodes a version number that duplicates (and can drift from) pyproject.toml, or omits which verb/feature actually needs the extra (a bare package name with no usage context doesn't help a consumer decide anything)"
    gate: "tester-unified"
  - id: O2-does-not-duplicate-or-contradict-existing-per-feature-docs
    observable: "The existing per-feature extra mentions (CONFIG.md:485-488 registry, CONFIG.md:765 schema, CONFIG.md:1129 ssh, CONSUMERS.md:604-605 registry) are UNCHANGED -- this package is additive documentation only, one new consolidated table, not a rewrite or relocation of the existing contextual mentions. Verify by diff: only README.md and CHANGES.md change."
    negative: "removing or rewording any existing per-feature mention as part of 'consolidating' -- the point is an upfront INDEX in addition to the contextual documentation, not a replacement of it"
    gate: "tester-unified"
  - id: O3-gate-stays-green-with-zero-code-changes
    observable: "`.venv/bin/python run-ciu-tests.py` is 100% line+branch coverage and fully green with ZERO source or test file changes -- this is pure documentation."
    negative: "touching any file under src/ or tests/ to make this land -- if that seems necessary, the premise is wrong and this should escalate, not expand into forbidden territory"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "pyproject.toml's optional-dependencies has changed shape since input_revision (a fourth extra added, or one of the three removed) in a way that makes this handoff's table shape stale -- BLOCKED naming the actual current shape, re-verify against pyproject.toml directly rather than trusting this handoff's list"
mutexes: [merge-lane]
review_focus:
  - "confirm the table's package list and version references are read from the actual current pyproject.toml, not copied from this handoff's prose (which could itself be stale by review time)"
  - "confirm nothing in CONFIG.md/CONSUMERS.md's existing per-feature mentions was touched"
---

# ciu-P35 — a consolidated "Optional extras" table in README.md

## Context to read first

1. `pyproject.toml:20-41` (`[project.optional-dependencies]`) — the actual
   source of truth: `test` (dev-only, exclude), `ssh` (paramiko>=5.0),
   `schema` (jsonschema>=4.18), `registry` (pydantic>=2). Re-read it live,
   don't trust the versions quoted in this handoff by the time you implement.
2. `src/ciu/provisioning.py:678-697` (`_load_pydantic`) — the existing design
   rationale for keeping these optional rather than bundled: a module-scope
   import would hard-couple every `ciu` command to a dependency most
   installs never need. This package does not change that decision, only
   makes the three extras discoverable up front instead of only reactively
   (each one already fails loud with its own exact `pip install` remedy at
   the point of use — this table is an additional index, not a substitute).
3. `docs/CONFIG.md:485-488`, `:765`, `:1129` and `docs/CONSUMERS.md:604-605`
   — the existing per-feature mentions this package supplements, never
   duplicates or rewords.
4. `README.md` — read the existing install section in full to find the
   right, natural placement for one new table.

## Definition of done

- One new README.md table, three rows (ssh/schema/registry), each with the
  exact install command and a one-sentence "why."
- `CHANGES.md` gets a short docs-only entry, no `!` marker.
- Zero changes outside `README.md`/`CHANGES.md`/the LOG.
