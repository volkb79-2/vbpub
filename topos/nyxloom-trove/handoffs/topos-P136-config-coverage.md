---
schema_version: 1
id: topos-P136-config-coverage
project: topos
title: "Complete public configuration parsing and normalization coverage"
tier: luna-low
input_revision: "f53bf723"
depends_on: [topos-P135-tree-coverage]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_config.py", "topos/nyxloom-trove/handoffs/topos-P136-config-coverage.md"]
  forbid: ["topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml", "topos/src/topos/config.py"]
oracles:
  - id: O1
    observable: "Public ThresholdBand normalization handles absent, non-positive, zero/identical bounds, below-warn, and above-warn inputs with its documented bounded score semantics."
    negative: "Malformed threshold ordering, zero bounds, or a value at the warn boundary produces an unbounded or discontinuous score."
    gate: topos-suite
  - id: O2
    observable: "Loading a real temporary TOML configuration applies tier/default threshold precedence, accepts valid integer/string/range port definitions, and safely ignores malformed/out-of-range port and score-weight data."
    negative: "A malformed configuration crashes loading, swaps a reversed port range, applies invalid score weights, or chooses default thresholds over a valid tier override."
    gate: topos-suite
  - id: O3
    observable: "The full tester-unified coverage JSON has no missing line or branch for config.py."
    negative: "Untested configuration degradation or normalization branches remain a global-coverage hole."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P136 — Complete public configuration parsing and normalization coverage

## Context to read first

1. `topos/src/topos/config.py` in full, concentrating on `ThresholdBand`,
   `ToposConfig.threshold_band`, and public `load`.
2. `topos/tests/test_ui_table.py` for small real-config test style.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Create `topos/tests/test_config.py`. Exercise only public `ThresholdBand`,
`ToposConfig`, and `load(path)` behavior with real temporary TOML files. Do
not import or call underscored helpers, monkeypatch the module, inspect source,
or add coverage suppressions.

1. Assert the public normalized score contract for `None`, non-positive input,
   zero/negative/reversed bounds, equal bounds, lower segment, warn boundary,
   and upper segment. Use exact or close numeric results rather than helper
   calls.
2. Through `load(tmp_path / "config.toml")`, assert tier threshold selection
   takes precedence over default only when a tier band is structurally valid;
   malformed/non-dict sections safely fall through to the default band.
3. Through real TOML, assert net classes accept valid integer ports, numeric
   strings, and reversed inclusive ranges, deduplicate/sort them, and ignore
   invalid strings, invalid ranges, out-of-range integers, and non-list values.
   Also assert score weights preserve defaults when absent/malformed and safely
   coerce valid custom values while falling back for non-numeric values.
4. Reach 100% statements and branches for `config.py` through those public
   contracts. Keep the test data compact; do not change production code merely
   to close coverage.

## Oracle

Run the declared tester-unified gate:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P136-config-coverage; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

The gate must pass; `topos/src/topos/config.py` must have empty
`missing_lines` and `missing_branches` in the coverage JSON.

## Scope / forbid

Touch only the named test and handoff. Tests must observe public configuration
objects/load results; private-helper imports/calls, coverage suppressions,
source audits, and production cleanup are forbidden.

## BLOCKED rule

If the named public contracts cannot be reached without a forbidden file,
STOP. Write `BLOCKED: <specific reason>` to the handoff LOG, commit that
log-only change, and exit. Do not fabricate coverage with private-helper tests
or a suppression.

## LOG

- Implemented public configuration coverage in `topos/tests/test_config.py`.
- Focused result: `13 passed`.
- Declared tester-unified gate could not run because Docker access is denied in
  this environment (`permission denied while trying to connect to the Docker API`).
