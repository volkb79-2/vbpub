# P2 Report

## What was built

- Added `src/groop/record/writer.py`:
  - writes headered JSONL recordings using `model.frame_to_jsonable()`
  - append-safe on existing files
  - configurable flush cadence from shared config
  - optional `.zst` streaming when `zstandard` is importable
  - plain JSONL fallback when `zstandard` is unavailable
- Added `src/groop/record/reader.py`:
  - reads plain JSONL and sniffed zstd recordings
  - routes through `model.frame_from_jsonable()`
  - validates `schema_version == 1`
  - tolerates a truncated final line
  - accepts both headered P2 recordings and headerless P1 golden frame fixtures
- Added `src/groop/record/ring.py`:
  - fixed-capacity per-`(entity, metric)` history ring backed by `array('f')`
  - `append_frame()`, `last(n)`, `minmax(n)`
  - configurable capacity from `[history]`
  - configurable entity death grace via `[history].entity_grace_seconds`
- Added `src/groop/record/replay.py`:
  - replay loader and cursor API (`seek`, `step`, paced `play`)
  - frame summary formatter for non-Textual smoke paths
- Extended `src/groop/config.py`:
  - `[history]` parsing for retention/grace
  - `[record]` parsing for flush/fsync
  - config digest helper for recording headers
- Added the `zstandard` optional dependency extra in `pyproject.toml`.
- Extended `src/groop/cli.py`:
  - `--replay FILE`, `--speed N`, `--step`
  - minimal `--record FILE` live writer path
  - kept `--once --json` working
- Added focused tests in `tests/test_record.py`.

## Deviations

- The handoff asked for a memory budget test via RSS. I used a deterministic storage-budget test instead:
  - asserts the ring allocates the expected `float32` sample bytes
  - asserts every backing store is an `array`
  - bounds total `sys.getsizeof()` array storage under 50 MB
  - reason: RSS assertions are noisy across Python builds and allocators, while the storage constraint here is structural

## Proposed contract changes

- None required.

## Command output tails

### Tests

Command:

```sh
PYTHONPATH=/tmp/groop-pytest:/tmp/vbpub-groop-p2-record/groop/src python3 -m pytest /tmp/vbpub-groop-p2-record/groop/tests -q
```

Tail:

```text
...                                                       [100%]
18 passed in 1.73s
```

### Compile

Command:

```sh
PYTHONPATH=/tmp/vbpub-groop-p2-record/groop/src python3 -m py_compile $(find /tmp/vbpub-groop-p2-record/groop/src/groop -name '*.py' | sort)
```

Tail:

```text
(no output; exit 0)
```

### Replay smoke

Command:

```sh
PYTHONPATH=/tmp/vbpub-groop-p2-record/groop/src python3 -m groop.cli --replay /tmp/vbpub-groop-p2-record/groop/tests/fixtures/frames/gstammtisch-once.jsonl --step
```

Tail:

```text
frame 1/1 ts=100.000 interval=5.000 entities=8 host_metrics=20
```

## Known gaps / open items

- The replay CLI path is intentionally a smoke-mode summary printer. Textual transport controls and in-UI replay state still belong to P5/P7.
- The reader accepts headerless frame fixtures for compatibility with existing P1 goldens. Recorded files written by P2 always include the header line.
- When `zstandard` is unavailable, a `.zst` output path is written as plain JSONL and later read via magic-byte sniffing rather than suffix-based assumptions.
