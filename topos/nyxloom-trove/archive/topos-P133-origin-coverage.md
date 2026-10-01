---
schema_version: 1
id: topos-P133-origin-coverage
project: topos
title: "Complete governance origin policy coverage"
tier: haiku-high
input_revision: "fafd26bd"
depends_on: [topos-P132-hostmem-coverage]
session: "resume:topos-governance-coverage"
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/src/topos/drift/origin.py", "topos/tests/test_origin.py", "topos/nyxloom-trove/handoffs/topos-P133-origin-coverage.md"]
  forbid: ["topos/src/topos/drift/origin.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "Public annotate_frame_governance produces truthful typed governance metadata for unavailable live cgroup values, systemd runtime/unit/default records, malformed systemctl values, and finite/unlimited ancestor chains."
    negative: "A missing/unlimited/default or malformed systemctl value is silently mistaken for an owned finite limit or causes a false raw-write drift."
    gate: topos-suite
  - id: O2
    observable: "Protected-memory clamp and raw-write reasons name the responsible unit/value; each is observable from the emitted governance summary/limits."
    negative: "A protected floor is presented as un-clamped or an unmanaged live non-default value is not marked drift."
    gate: topos-suite
  - id: O3
    observable: "The full tester-unified xdist coverage JSON has no missing line/branch for drift/origin.py."
    negative: "Fixture happy paths pass while governance parser/refusal/default branches remain unexercised."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P133 — Complete governance origin policy coverage

## Context to read first

1. `topos/src/topos/drift/origin.py`, especially public
   `annotate_frame_governance`, `ShowResult`, and lines 146–390.
2. `topos/tests/test_origin.py` in full; extend it using small in-memory
   `Frame`/`Entity`/`EntityFrame` fixtures and an injected `ShowResult` runner.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Append behavioral tests through `annotate_frame_governance` only. Do not call
private helpers directly and do not mock the function under test.

1. Build an entity with unavailable live limits plus a successful systemd
   record (unit fragment/drop-in) and assert systemd origin + unavailable-live
   reason; then a nonzero systemctl result must not claim systemd ownership.
2. Use systemd `MemoryMin=infinity`/`max`, `[not set]`, `n/a`, and malformed
   numeric text through the runner. Assert emitted governance distinguishes
   unlimited from unset and does not manufacture a finite recorded value.
3. Build ancestor/leaf frames to demonstrate: a protected leaf request clamped
   by a smaller finite ancestor is red with ancestor key/value; a chain of only
   unlimited values has an unlimited effective value; an unavailable ancestor
   has unavailable effective source. Use public output only.
4. Demonstrate an unmanaged non-default live `mem_min` is raw-write warn and
   names the leaf; demonstrate a docker scope left at its known default is
   docker_default without drift.
5. Include at least one entity whose unit record has no fragment/dropin (no
   systemd recorded origin) and one runtime dropin path. Assert governance
   origins and reasons, not just numeric codes.

No coverage pragmas, subprocesses, or filesystem fixture copies. If public
tests prove a helper branch impossible from every caller, the only permitted
source change is removal/tightening of that dead branch with an invariant
comment; do not alter governance behavior.

## Oracle

Run the full declared tester-unified gate:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P133-origin-coverage; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

The gate must pass and coverage JSON must have empty `missing_lines` and
`missing_branches` for `topos/src/topos/drift/origin.py`.

## Scope / forbid

Touch only the named source, test, and handoff. Preserve governance semantics;
source changes may only remove public-call-site-unreachable helper branches.

## BLOCKED rule

If the named public annotation contracts cannot be reached without a forbidden
file, STOP. Write `BLOCKED: <specific reason>` to the handoff LOG, commit that
log-only change, and exit. Do not test private helpers or add suppression.
