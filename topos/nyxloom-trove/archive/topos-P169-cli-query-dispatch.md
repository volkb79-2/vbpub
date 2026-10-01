---
schema_version: 1
id: topos-P169-cli-query-dispatch
project: topos
title: "Specify query CLI validation, execution, and rendering"
tier: luna-low
input_revision: "f64e4d50"
depends_on: [topos-P168-cli-damon-dispatch]
session: resume cli
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_query_dispatch.py", "topos/nyxloom-trove/handoffs/topos-P169-cli-query-dispatch.md"]
  forbid: ["topos/src/topos/cli.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "`topos query` rejects parser misuse and a missing --metric before opening a recording, then converts every declared option into the typed Query, Selector, Caps, and recording-source boundary contract."
    negative: "An invalid CLI reaches I/O, a missing metric becomes a runtime failure, or a limit/selector/sort/format option silently disappears or changes meaning."
    gate: topos-suite
  - id: O2
    observable: "The query boundary renders JSON versus the human table through their respective contracts and maps each documented query/file/runtime/read/unexpected failure to exit 2 with a useful diagnostic."
    negative: "A success renders through the wrong surface, pretty JSON is lost, a known failure exits zero, a file error loses its path, or an unexpected exception escapes."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs a real recording, product-code edit, or a file outside the two allowed paths"
---

# P169 — Specify query CLI validation, execution, and rendering

## Context to read first

1. `topos/src/topos/cli.py`, only `parse_query_args` and `_main_query`
   (roughly lines 1173–1312).
2. `topos/tests/test_cli_damon_dispatch.py` for direct command-boundary style.
3. `topos/tests/test_query.py`, only fixtures/types needed to understand Query
   contracts; do not copy its engine tests.
4. `topos/nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. Add exactly one direct test module. Do not modify production code.
2. Prove malformed parser input and a missing `--metric` return exit 2 before
   constructing `RecordingFrameSource` or calling `run_query`.
3. Patch only external/query boundaries, then call `_main_query` directly with
   a complete option set. Assert the constructed Query preserves shape,
   metric semantics, window, Selector keys/globs/slice, projection, visibility,
   sort, limits, on-exceed, selected recording path, and the JSON pretty flag.
   Use real Query/Selector/Caps values where practical; do not mock
   `_main_query` or parser helpers under test.
4. Prove both render contracts: JSON delegates to `format_result` and the
   default table delegates to `render_query(result.to_jsonable())`, with exact
   output and successful status.
5. Parameterize the documented error boundaries (`QueryError`, missing file,
   runtime/value errors, OSError with strerror, and an unexpected exception).
   Assert exit 2, stderr diagnostic, and no success renderer. Do not open a
   real recording.
6. Run the focused module in `tester-unified` under
   `--cgroup-parent=nyxloom-gates.slice`, self-review for hollow assertions and
   scope, commit only the allowed files, and leave the branch unmerged.

## BLOCKED rule

If an oracle needs a real recording, product-code edit, or a forbidden file,
write `BLOCKED: <named oracle and reason>` and stop.
