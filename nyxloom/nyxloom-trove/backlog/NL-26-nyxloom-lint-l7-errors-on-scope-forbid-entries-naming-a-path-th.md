---
kind: backlog-entry
schema_version: 1
id: NL-26
title: "nyxloom lint L7 errors on scope.forbid entries naming a path that does not exist yet"
status: open
type: "bugfix"
severity: "medium"
component: "nyxloom-lint"
provenance: "dstdns 2026-09-30 P224 carve"
filed_date: "2026-09-30"
---

## Observed mechanism and reproduction

`_check_path_resolution` (`src/nyxloom/lint.py`, l.~1005-1056) applies the "file to be created" exemption to `scope.touch` only (`if not is_touch and not full_path.exists()` -> L7 **error** "path '...' does not exist"). `_check_l7` calls it with `is_touch=False` for every `scope.forbid` entry and for `source.ref`. A handoff that forbids creating a file that is absent on main, for example `run-gate.footprint.json` (a controller-only artefact written after merge), therefore gets a hard L7 error, although forbidding a not-yet-existing path is exactly the intent: the forbid entry exists to stop an implementer creating it.

Reproduce: a handoff with `scope.forbid: ["run-gate.footprint.json"]` in a project whose root has no such file -> `nyxloom lint <handoff>` reports `L7 error path 'run-gate.footprint.json' does not exist`. (Source-grounded, not re-run live.) The NL-9 defects are in the same function but are distinct (sibling-project paths).

## Why nyxloom owns it

L7 is nyxloom's own carve-validation rule. The consumer's only workarounds are to drop the forbid entry (losing the guard) or to bury the prohibition in prose.

## Proposed contract

For `scope.forbid` entries, a non-existent path is valid (warning at most, or info); keep the error for absolute / `../` references and archived docs. A forbid entry that is a glob matching nothing is likewise valid. `source.ref` keeps the existence requirement. Optionally warn when a forbid path is BOTH absent and outside every scope.touch glob (a pure no-op), but never error.

## Oracles

- `scope.forbid: [<absent path>]` lints without an L7 error.
- `scope.touch: [<absent path>]` still passes (unchanged); `source.ref` naming an absent path still errors.
- A forbid entry `../x` or `/abs` still errors.
- A controlled wrong implementation that exempts every `is_touch=False` call (including `source.ref`) must fail the `source.ref` oracle.

**Found in:** dstdns 2026-09-30, P224 carve (`dstdns/nyxloom-trove/handoffs/`, forbid of `run-gate.footprint.json`).
