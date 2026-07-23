# P7 — v1 integration, acceptance, packaging

**Cut:** v1 (final). **Depends:** P2–P6 merged. Branch: `feat/topos-p7-integration`.
Follow `topos/README.md` workflow protocol.

## Goal

Wire everything into the shippable v1: live loop → diagnostics → ring →
UI, recording on demand, replay indistinguishable from live, non-root mode
verified on the real host, spec §9 acceptance criteria executed and reported.

## Spec references

§9 (acceptance criteria — this package's checklist), §6.2 (sampling loop),
§3.8 (record/replay), §0.1 (v1 read-only guarantee), §7 (full config surface).

## Scope — in

1. `cli.py` finalization: `topos` (live TUI), `--once --json`, `--record FILE`
   (live TUI + recording), `--replay FILE [--speed N|--step]`, `--profile`,
   `--config PATH`, `--cgroup-root` (fixtures/debug). Lazy textual import
   preserved.
2. Live loop: collector → providers → drift → diag.annotate → ring.append →
   UI frame push, on the configured interval, collection off the UI thread;
   slow-sample overrun policy per §6.2.
3. End-to-end tests: fixture-root live loop for N frames; record→replay
   equality; UI pilot smoke over replay.
4. Real-host verification (root and non-root, read-only): startup time,
   sample cost (CPU% of topos itself — spec §9 budget), degradation banner
   correct; capture evidence in the report. NO writes to any cgroup file —
   verify the process opened nothing for writing (strace spot-check or code
   audit statement).
5. Packaging: `pyproject.toml` (console script `topos`, textual dependency,
   optional `zstandard` extra), README quickstart section for the tool itself,
   `topos --version`.
6. §9 acceptance run: execute every v1-applicable criterion, tick them off in
   the report with evidence (criteria that spec marks v1.5/v2 are listed as
   deferred).

## Scope — out

Any v1.5/v2 feature; fixing other packages' bugs beyond integration glue (file
issues in your report; small obvious fixes allowed with a note).

## Acceptance

- All §9 v1 criteria pass on gstammtisch (root + non-root).
- `pip install -e topos/ && topos --replay golden.jsonl` works from a clean
  venv.
- pytest green across the whole tree; report per README protocol with the
  acceptance checklist.
