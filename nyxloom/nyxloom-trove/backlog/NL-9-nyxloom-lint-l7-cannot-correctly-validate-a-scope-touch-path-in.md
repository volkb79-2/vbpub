---
kind: backlog-entry
schema_version: 1
id: NL-9
title: "nyxloom lint L7 cannot correctly validate a scope.touch path in a sibling project of the same monorepo checkout"
status: open
type: "bugfix"
severity: "medium"
component: "nyxloom-lint"
provenance: "nyxloom-P103, Work item 7, 2026-09-08"
filed_date: "2026-09-08"
---

## Observed mechanism and reproduction

`nyxloom lint`'s L7 rule cannot correctly express a `scope.touch` path that
lives in a SIBLING project of the same monorepo checkout (a real,
non-hypothetical case: nyxloom-P103 had to edit `pwmcp/ciu.global.defaults.toml.j2`,
a file no nyxloom project owns). L7 has two related defects, both observed
2026-09-08 against `src/nyxloom/lint.py`:

1. **False negative.** A bare sibling-relative path such as
   `pwmcp/ciu.global.defaults.toml.j2` is silently ACCEPTED, but for the
   WRONG reason: the create-exemption at `lint.py:1044-1045` treats any
   `scope.touch` entry that does not already exist under `cfg.root` as a
   file-to-be-created, so it resolves `cfg.root / "pwmcp/ciu.global.defaults.toml.j2"`
   — a path that does not exist — and never looks at the REAL file at the
   monorepo-sibling location. Lint reports success while validating nothing.
2. **False positive.** The correct, explicit way to reference an out-of-project
   path — a `../`-prefixed or absolute reference — is a HARD ERROR at
   `lint.py:1017-1024` ("non-resolving reference"), with no exemption path
   for a legitimate cross-project reference inside the same monorepo.

Net effect: there is no way to write a `scope.touch` entry for a sibling
project that is BOTH accepted by lint AND validated against the real file.
nyxloom-P103's handoff worked around this by using the bare (false-negative)
form and documenting the caveat in prose (`scope.touch`'s own comment), which
is exactly the kind of thing L7 exists to make unnecessary.

## Why nyxloom owns it

L7 is nyxloom's own lint rule (`src/nyxloom/lint.py`); this is a genuine gap
in nyxloom's carve-validation tooling, not a ciu or estate-config defect.

## Proposed contract

Give L7 a third path class, alongside "in-project" and "to-be-created":
a monorepo-sibling reference, syntactically distinguished from a
`../`-prefixed traversal (e.g. an explicit `sibling:<project>/<path>` form,
or a config-declared list of sibling project roots the lint pass may resolve
against). The false-negative half matters more to fix first: a bare sibling
path should either resolve against the REAL sibling root and validate the
file exists, or should be rejected as unresolvable — never silently
misapplied to `cfg.root`.

## Oracles

- A `scope.touch` entry naming a real file in a declared sibling project
  (e.g. `pwmcp/ciu.global.defaults.toml.j2` from a nyxloom handoff) passes
  L7 AND the pass demonstrably checked the real file's existence (e.g. lint
  fails when that file is deleted, which it does not today — the bare form
  resolves to a nonexistent `cfg.root`-relative path regardless).
- A `scope.touch` entry naming a nonexistent path in a declared sibling
  project fails L7 with a message identifying it as a missing sibling file,
  not a "will be created" pass.
- The existing `../`-prefixed hard-error stays intact for genuine
  out-of-repo escapes; only the declared-sibling case gets a new path.

## SPEC ownership

nyxloom's own lint contract: `src/nyxloom/lint.py:1010-1050` (L7),
`reference/AUTHORING.md` (`scope.touch` semantics). Originating handoff:
`nyxloom-trove/handoffs/nyxloom-P103-ciu-governance-standalone-roots.md`
(`scope.touch`'s `pwmcp/ciu.global.defaults.toml.j2` entry and Work item 7).

## Updates
