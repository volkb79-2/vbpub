---
kind: backlog-entry
schema_version: 1
id: NL-8
title: "pwmcp carries two tracked ciu.global.toml* layers (defaults + toml.j2), a standing shadowing hazard"
status: open
type: "bugfix"
severity: "low"
component: "ciu-config"
provenance: "nyxloom-P103, Work item 6, 2026-09-08"
filed_date: "2026-09-08"
---

## Observed mechanism and reproduction

`pwmcp/` (a ciu root, `vbpub/pwmcp`) carries TWO tracked global-layer files
that both merge into the same `[governance]` (and every other root-scoped)
namespace: `ciu.global.defaults.toml.j2` (the low-precedence defaults layer,
committed, meant to be the maintainer's build-test-only baseline) and
`ciu.global.toml.j2` (a tracked near-duplicate at HIGHER precedence,
`ciu/src/ciu/config_model.py:686-696`).

Measured 2026-09-08 (nyxloom-P103): `ciu.global.toml.j2` declares no
`[governance]` table today, so the higher-precedence file happens to be
silent on the one key nyxloom-P103 needed — but it is a tracked file that
CAN win per-key over anything the defaults layer declares (per-key
deep_merge, `config_model.py:558-571`), and it carries none of the
defaults file's own BUILD-TEST-ONLY warning header. Anyone editing
`pwmcp/`'s global config without reading both files first can add a key to
the wrong one, or add the SAME key to both and be surprised which one wins.

Reproduction: `grep -l governance pwmcp/ciu.global*.toml.j2` currently
matches only `ciu.global.defaults.toml.j2`; nothing prevents a future edit
from also touching `ciu.global.toml.j2` and creating a silent-shadowing
duplicate.

## Why nyxloom owns it

nyxloom-P103 (declaring `[governance]` on nyxloom's and pwmcp's ciu roots)
had to VERIFY this file stayed silent as an `escalate_if` precondition
(a table appearing there would have inverted the fix silently). That
package's own scope explicitly excludes fixing the duplication — it is a
pwmcp-root config hygiene question, not part of declaring governance. Filed
here per nyxloom-P103 Work item 6 so the hazard survives past that package.

## Proposed contract

Either (a) collapse to one tracked global file for `pwmcp/` (deleting the
now-redundant `ciu.global.toml.j2` once its purpose, if any, is understood),
or (b) if both files are intentional (e.g. `ciu.global.toml.j2` reserved for
a future non-build-test override), add the same BUILD-TEST-ONLY /
do-not-vendor header to it that `ciu.global.defaults.toml.j2` already
carries, and document the precedence relationship explicitly in both files'
headers so a future editor cannot add the same key to the wrong layer
without warning.

## Oracles

- `git grep -n governance pwmcp/ciu.global.defaults.toml.j2
  pwmcp/ciu.global.toml.j2` shows the key in exactly one file (or, if the
  duplication is kept deliberately, both files' headers state which one
  wins for which key).
- A fresh reader of `pwmcp/ciu.global.toml.j2` alone (without also reading
  `ciu.global.defaults.toml.j2`) can correctly answer "is this file safe to
  vendor to a consumer?" from its own header text.

## SPEC ownership

ciu v7 `docs/SPEC.md` S3.7 (global layer precedence);
`ciu/src/ciu/config_model.py:558-571,686-696` (deep_merge, layer order).

## Updates
