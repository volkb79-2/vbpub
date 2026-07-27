---
schema_version: 1
id: topos-P172-cli-optional-frontends
project: topos
title: "Specify BPF, MCP, and gateway CLI frontend dispatch"
tier: luna-low
input_revision: "30425956"
depends_on: [topos-P171-cli-inspect-files-dispatch]
session: resume:cli
source: {kind: product-goal, ref: "nyxloom-trove/3-roadmap.md"}
scope:
  touch: ["tests/test_cli_optional_frontends.py", "nyxloom-trove/handoffs/topos-P172-cli-optional-frontends.md"]
  forbid: ["src/topos/cli.py", "nyxloom-trove/nyxloom.toml", "tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "`topos bpf gate` forwards roots, emits exactly one requested text/JSON representation, and unsupported post-parse commands fail nonzero without a BPF run."
    negative: "Roots or output mode are lost, both renderers run, or an unsupported command reaches the BPF boundary."
    gate: topos-suite
  - id: O2
    observable: "`topos mcp serve` imports and starts the optional MCP server only for serve, forwards its socket, and translates an optional sensitivity threshold faithfully."
    negative: "A non-serve command imports/starts MCP, a socket is lost, or the sensitivity threshold is omitted/mis-typed."
    gate: topos-suite
  - id: O3
    observable: "`topos gateway serve` validates principal declarations before startup, forwards one authenticated configuration, reports startup failure, and always closes its server after normal or interrupted serving."
    negative: "Malformed/duplicate principals start a gateway, auth/loopback config is lost, an error escapes, or a server leaks without close."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs a real BPF probe, live MCP server, bound network socket, production-code edit, or a file outside the two allowed paths"
---

# P172 — Specify BPF, MCP, and gateway CLI frontend dispatch

## Context to read first

1. `topos/src/topos/cli.py`, only `_main_bpf`, `_main_mcp`, and
   `_main_gateway` (roughly lines 1523–1596).
2. `topos/tests/test_cli_compare_dispatch.py` for direct CLI boundary style.
3. `topos/nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. In a fresh branch worktree based on `main`, add one focused direct test
   module. Production code is out of scope: do not modify `src/topos/cli.py`.
2. Monkeypatch the parser/boundaries needed to assert BPF argv forwarding,
   exactly-one text/JSON renderer, and its defensive unsupported-command path.
3. For MCP, patch or inject the narrow optional imports and server boundary;
   prove only `serve` imports/runs it and prove socket plus `Sensitivity` value
   forwarding for both a threshold and no threshold.
4. For gateway, use a fake server and startup boundary. Prove malformed and
   duplicate `NAME:CEILING` principals fail before startup; prove valid config,
   `GatewayStartupError`, normal return, and `KeyboardInterrupt`, including
   close-after-serve. No live socket may bind.
5. Run the focused module in `tester-unified` under
   `--cgroup-parent=nyxloom-gates.slice`, self-review each oracle and scope,
   commit only allowed files, and leave the branch unmerged.

## BLOCKED rule

If an oracle needs a real BPF probe, live MCP server, bound network socket,
production-code edit, or a forbidden file, write `BLOCKED: <named oracle and
reason>` and stop.
