---
schema_version: 1
id: topos-P132-hostmem-coverage
project: topos
title: "Complete host-memory rendering and control coverage"
tier: haiku-high
input_revision: "9e6f6ccc"
depends_on: [topos-P131-redaction-malformed-coverage]
session: "resume:topos-ui-coverage"
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/src/topos/ui/hostmem.py", "topos/tests/test_p23_zram_drilldown.py", "topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P132-hostmem-coverage.md"]
  forbid: ["topos/src/topos/ui/hostmem.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "Host-memory text safely renders malformed replay metadata, zero/unknown DAMON classes, no paddr session, and both topos/foreign ownership messages while preserving valid sibling details."
    negative: "Malformed metadata crashes or hides valid zram/paddr data, and a zero total draws a misleading non-empty heat bar."
    gate: topos-suite
  - id: O2
    observable: "HostMemoryScreen shows explicit stop failure/success and start cancelled/result notices after its public actions; no action silently reports the wrong outcome."
    negative: "A DAMON control error is swallowed or a cancelled confirmation is displayed as a success."
    gate: topos-suite
  - id: O3
    observable: "Full tester-unified xdist coverage has no missing line or branch for ui/hostmem.py."
    negative: "Focused rendering tests leave UI action or malformed-data branches unexecuted."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P132 — Complete host-memory rendering and control coverage

## Context to read first

1. `topos/src/topos/ui/hostmem.py`, lines 81–100 and 143–271.
2. `topos/tests/test_p23_zram_drilldown.py` for frame/render builders.
3. `topos/tests/test_ui_app.py`, the existing host-memory pilot tests near
   `test_pilot_damon_paddr_modal_starts_and_duplicate_is_reported`.
4. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Append behavioral tests only. Do not import private formatting helpers.

1. In `test_p23_zram_drilldown.py`, drive `render_host_memory_text` with
   frames proving: malformed `host_meta` (non-dict, non-list zram devices, and
   mixed non-dict/dict devices) safely retains a valid device; a root entity
   with non-list DAMON sessions renders no session; a paddr session lacking
   `class_bytes`/regions renders ownership; a topos-owned session with all
   class bytes zero renders four empty dot bars and the topos stop instruction;
   integer/missing host DAMON metrics render as integer / `-`; boolean zram
   counters render 0/1 and boolean ratio renders `-`.
2. In `test_ui_app.py`, use the existing Textual pilot pattern to verify the
   host-memory `s` action shows its exact successful stopped-count notice.
   Add a narrow screen/action-level test with the module boundary
   `stop_owned_sessions` patched to raise a concrete exception; the rendered
   notice must say `stop unavailable: <message>`. Do not mock
   `HostMemoryScreen.action_stop_topos_damon` itself.
3. Exercise `_on_control_result` via the real `HostMemoryScreen` result
   callback: `None` renders `start cancelled`; a nonempty result renders that
   exact result. Use the mounted screen/Static text, not direct `_notice`
   inspection.

No source behavior changes, sleeps, or private-helper tests. The sole allowed
source annotation is coverage.py's `# pragma: no branch` on `_fmt_bytes`'s
fixed non-empty unit loop: its final tuple element unconditionally breaks, so
the loop-exhaustion arc is structurally unreachable.

## Oracle

Run the full declared gate in `tester-unified`:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P132-hostmem-coverage; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

The gate must pass and `ui/hostmem.py` must have empty `missing_lines` and
`missing_branches` in its coverage JSON.

## Scope / forbid

Touch only the named source, tests, and handoff. Preserve all DAMON safety
semantics and do not alter production UI or control behavior.

## BLOCKED rule

If a named public/rendered behavior cannot be reached without a forbidden
file, STOP. Write `BLOCKED: <specific reason>` to the handoff LOG, commit that
log-only change, and exit. Do not substitute a private-helper test.
