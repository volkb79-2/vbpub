---
schema_version: 1
id: topos-P128-banner-residual
project: topos
title: "Close the two residual banner coverage arcs"
tier: haiku-high
input_revision: "8683e9ba"
depends_on: [topos-P127-banner-rendering]
session: "resume:topos-ui-coverage"
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch:
    - "topos/src/topos/ui/banner.py"
    - "topos/tests/test_ui_banner.py"
    - "topos/nyxloom-trove/handoffs/topos-P128-banner-residual.md"
  forbid:
    - "topos/nyxloom-trove/nyxloom.toml"
    - "topos/tools/coverage_gate.py"
    - "topos/pyproject.toml"
oracles:
  - id: O1
    observable: "The real render_banner output for complete DAMON byte telemetry whose class percentages total below 100 contains the remaining-dot heat bar; the full tester-unified xdist coverage JSON records no missing line/branch for banner.py."
    negative: "A renderer that omits unused heat-bar width produces no dots and leaves banner.py:139 / branch 138->139 uncovered."
    gate: topos-suite
  - id: O2
    observable: "banner.py documents the structurally unreachable for-loop fall-through arc in _fmt_bytes with coverage.py's no-branch directive; the full tester-unified coverage JSON has no remaining missing branches for banner.py."
    negative: "A bare loop leaves the impossible loop-exhaustion arc (400->404) counted as an untestable branch, preventing exact coverage."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires a forbidden file"
---

# P128 — Close the two residual banner coverage arcs

Work in this branch only. P127 already made every executable banner behavior
except the heat-bar underfill observable. The remaining `_fmt_bytes` loop
fall-through is not a runtime behavior: `units` is a non-empty literal and the
last tuple element always executes `break`, so normal, huge, infinity, and NaN
inputs cannot exhaust the loop.

## Context to read first

1. `topos/src/topos/ui/banner.py`: `_heat_bar` (lines 128–140) and `_fmt_bytes`
   (lines 396–406).
2. `topos/tests/test_ui_banner.py`: `_make_base_frame` and the P127 DAMON tests
   near the end of the file. `Frame` host metrics live in `frame.host`, not a
   `frame.metrics` attribute.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add one focused, public-API test to `test_ui_banner.py`. Build a fresh
   `_make_base_frame()` with all four DAMON byte metrics non-null and the DAMON
   mode present. Set only `host_damon_hot_pct` to `10.0` (leave the other class
   percentages absent/None). Assert the real `render_banner` `DRAM HEAT` line
   contains exactly `[HH..................]`. Do not import or call `_heat_bar`.
2. On the `_fmt_bytes` `for unit in units:` line, add exactly coverage.py's
   `# pragma: no branch` directive and a concise adjacent explanation that the
   loop cannot fall through: the non-empty fixed tuple's final unit breaks.
   Do not use `no cover`, and do not change formatting behavior.
3. Do not modify any other production behavior, gate configuration, tooling,
   or dependencies.

## Oracles

Run the project gate only in its declared `tester-unified` container:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P128-banner-residual; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

The full xdist gate must pass, and the generated coverage JSON must report
empty `missing_lines` and `missing_branches` for
`topos/src/topos/ui/banner.py`. A focused green test alone is insufficient.

## Scope / forbid

Touch only the three declared files. Assertions must inspect the public
`render_banner` snapshot; no private-helper tests or mocks of the code under
test. Keep the existing P127 tests unchanged unless a factually incorrect
assertion blocks this exact contract.

## BLOCKED rule

If the specified public render behavior cannot be observed, coverage.py does
not honour the documented `no branch` directive, or meeting either oracle
requires any forbidden file, STOP. Write `BLOCKED: <specific reason>` to the
handoff LOG, commit that log-only change, and exit. Do not improvise another
coverage suppression or rewrite the formatter.
