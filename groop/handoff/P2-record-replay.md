# P2 — Record / replay / history ring

**Cut:** v1. **Depends:** P1 merged. Branch: `feat/groop-p2-record`.
Follow `groop/README.md` workflow protocol.

## Goal

JSONL recording, deterministic replay through the same model objects, and the
in-memory history ring that powers sparklines/charts.

## Spec references

§3.5 (history: ring buffer, budget math — 4h @ 5s default, 20–40 MB using
numeric arrays), §3.8 (record & replay), §7 ([history] config keys).

## Scope — in

1. `record/writer.py`: header line + frame-per-line JSONL per CONTRACTS §5,
   using `model.frame_to_jsonable()`; append-safe; flush policy configurable;
   optional `.zst` streaming output if
   `zstandard` is importable — MUST degrade to plain JSONL without it (stdlib-
   only remains true by default; zstd is an optional extra in pyproject).
2. `record/reader.py`: iterate `Frame`s from plain or `.zst` files using
   `model.frame_from_jsonable()`; validate schema_version; tolerate truncated
   final line (crash-time recording).
3. `record/ring.py`: per-(entity, metric) fixed-capacity numeric ring using
   `array('f')`; capacity from config (retention/interval); handles entity
   birth/death (new entities join late, dead entities age out after
   configurable grace); O(1) append; windowed reads for sparklines
   (`last(n)`, `minmax(n)`).
4. Replay driver: `groop --replay FILE` yields frames at recorded pace or
   `--speed N` / `--step` (the actual UI hookup is P5/P7 — you provide the
   iterator + time cursor API and a CLI smoke path that prints frame
   summaries).
5. Memory budget test: synthesize 40 entities × 24 metrics × 2880 samples,
   assert ring RSS overhead stays under ~50 MB (spec §3.5 math).

## Scope — out

UI rendering of charts (P5), incident bundles (P10 builds on your reader).

## Acceptance

- Round-trip: record 100 synthetic frames → read back → frames equal
  (a test does this via golden fixtures from P1).
- Truncated-file tolerance test passes.
- Ring budget test passes; no Python-float-list storage anywhere.
- pytest green; report per README protocol.
