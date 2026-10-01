---
schema_version: 1
id: topos-P179-cli-record-live
project: topos
title: "Cover CLI record and live collection mode contracts"
tier: luna-low
input_revision: "d7af8a75"
depends_on: []
session: "resume:cli"
source: {kind: product-goal, ref: "nyxloom-trove/3-roadmap.md"}
scope:
  touch: ["tests/test_cli_record_live_boundary.py", "nyxloom-trove/handoffs/topos-P179-cli-record-live.md"]
  forbid: ["src/topos/cli.py", "src/topos/record", "src/topos/collect", "nyxloom-trove/nyxloom.toml"]
oracles:
  - id: O1
    observable: "record/live incompatible options and controlled collector resolution errors return their documented public errors before unsafe boundaries run."
    negative: "An invalid combination reaching collector/writer/UI code or reporting success is red."
    gate: topos-suite
  - id: O2
    observable: "record/headless/once/live routes forward exact public options, write/print their observable frame result, and map UI/headless/interrupt outcomes correctly."
    negative: "A dropped option, omitted writer context, wrong source label, fallback change, or wrong exit code is red."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["a named contract cannot be met as specified", "scope requires a forbidden file"]
---

# P179 — CLI record/live boundary

## Context to read first

1. `src/topos/cli.py`, `main` lines 625–695 only.
2. `tests/test_record.py`, `tests/test_headless_record.py`, and `tests/test_cli_live_guards.py`: existing public behaviour and fixture terminology.
3. `tests/test_cli_attach_replay_boundary.py`: direct CLI boundary seams and exact behavioural oracles.
4. `nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]`.

## Work

1. Add only `tests/test_cli_record_live_boundary.py`. Test `main` directly with injected module-owned collector, writer, stream, UI, and headless runner seams; do not write real recordings, collect host state, sleep, or use clock-based assertions.
2. Cover record JSON rejection; headless once and bounded headless forwarding; record once; record UI success/fallback; writer/collector `ContainerResolveError`; interrupt mapping; ordinary live UI success/fallback; live once/json; and incomplete live invocation rejection.
3. Use unique sentinel frames/results and exact output/argument assertions. Assert writer receives the collector config and UI receives the documented LIVE source label; a call count alone is inadequate.
4. Self-review, focus-test in tester-unified, commit only allowed files.

## Oracles

- O1: Invalid paths must return 2 before live boundaries; controlled resolve error returns 2 and interrupt returns 0. A boundary call on invalid input makes tests red.
- O2: Fakes record exact forwarded options and yield distinct frames. Wrong writer config, live source label, frame JSON, fallback text, or exit code makes tests red.
- Gate: tester-unified focus then declared `topos-suite`; never cockpit Python.

## Test constraints

- No sleep, wall-clock verdict, real collector/writer, network, or global-state leak.
- Patch owning namespaces with `monkeypatch`; assert public output/exit behaviour.
- No coverage exclusions or `no cover` text.

## Scope / forbid

Only the named test and handoff may change. No product/gate/existing-test edits. Work in assigned worktree and branch.

## BLOCKED rule

If a named contract cannot be proven through stated seams, or a forbidden file is required, STOP; write `BLOCKED: <reason>` to the LOG, commit it, and exit. Do not improvise.
