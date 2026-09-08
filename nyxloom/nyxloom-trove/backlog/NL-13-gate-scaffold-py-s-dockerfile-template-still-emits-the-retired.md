---
kind: backlog-entry
schema_version: 1
id: NL-13
title: "gate_scaffold.py's Dockerfile template still emits the retired 'nyxloom gate verify' (GA1) into every scaffolded project"
status: open
type: "bugfix"
severity: "medium"
component: "onboarding"
provenance: "nyxloom-P102 retroactive adversarial review, 2026-09-08, F6"
filed_date: "2026-09-08"
---

## Observed mechanism and reproduction

`src/nyxloom/gate_scaffold.py:88-90`, inside the Dockerfile template string
returned to `nyxloom onboard --scaffold-gate`:

```
# below before building or trusting it. Once adjusted, verify it actually
# rejects broken code with `nyxloom gate verify <project>` (GA1) -- this
# scaffold does not prove itself correct.
```

`nyxloom gate verify` no longer exists (retired by nyxloom-P98,
2026-09-03; `gate` is now a reserved top-level verb with zero
subcommands, `cli.py:1918-1919`, `cli.py:2147-2149` prints help and
returns 2). Every project scaffolded via `nyxloom onboard --scaffold-gate`
today gets a checked-in Dockerfile whose own comment instructs the
operator to run a command that no longer works.

The same module's docstring at `gate_scaffold.py:19-21` already knows
better -- it explicitly states "nyxloom-P98 retired GA1's `nyxloom gate
verify` cross-check -- Assay's own R2/R3 mechanisms supersede it." P98's
carve updated the module's own prose but missed the Dockerfile template
it emits, which is generated output handed to end users, not just
internal documentation.

Found during the retroactive adversarial review of nyxloom-P102
(`nyxloom-trove/archive/nyxloom-P102-CODE-REVIEW-RETROACTIVE.md`, F6),
which was itself prompted by the operator asking whether every package
this session had a real adversarial review.

## Why nyxloom owns it

`gate_scaffold.py` is nyxloom's own onboarding machinery; the stale
reference is a leftover from nyxloom's own P98 retirement, not an
external tool's defect.

## Proposed contract

Fix `gate_scaffold.py:89`'s comment to point at adopting `run-gate`+Assay
instead of the retired `nyxloom gate verify`, matching the module
docstring's own framing at `:19-21`. As part of the same package, run a
repo-wide `grep -rn "gate verify"` to catch any other emitters this sweep
might miss (P98's own sweep was for PROSE, not generated templates --
this is exactly the class of miss a template/generated-output pass would
catch that a docs-only sweep wouldn't).

## Oracles

- `git grep -n "nyxloom gate verify"` returns zero hits inside any string
  literal `gate_scaffold.py` (or any other module) emits as generated
  output -- as opposed to zero hits overall, which would also delete
  legitimate historical/prose mentions like the module's own docstring.
- A fresh `nyxloom onboard --scaffold-gate` run's emitted Dockerfile
  contains no reference to a nonexistent command.

## SPEC ownership

`src/nyxloom/gate_scaffold.py`; `docs/plan-gate-adoption.md` §GA3 (the
scaffold's own design doc).

## Provenance

Found during the retroactive adversarial review of nyxloom-P102, 2026-09-08
(F6) -- pre-existing since nyxloom-P98 (2026-09-03), not introduced by
P102 itself.
