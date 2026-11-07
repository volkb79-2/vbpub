# Fallback score table (`cgprofile-scores/1`)

The table is a fallback only. cgprofiled normally takes the CPU speed from the host benchmark that `mdt host-setup cpu-benchmark` (or the installer)
persisted in `/var/lib/mdt/host-facts/cpu-benchmark.json`. The table is read when that record is absent, invalid, incomplete or stale (the CPU
model or the CPU count changed). It is single-core only: a per-model multi-core score describes the whole chip, not the vCPUs a VM gets.

File: `src/cgprofile/data/cgprofile-scores.json`, shipped as package data (`data/*`) and versioned with the wheel.

```json
{"schema": "cgprofile-scores/1", "table_version": 1, "entries": [
  {"cpu_model": "AMD Ryzen 7 7700 8-Core Processor", "geekbench_major": 7, "single_core": 0,
   "source_url": "https://...", "retrieved": "2026-11-01"}]}
```

(The score `0` above is a placeholder in this document, not an entry; a real entry is a published positive integer.)

## Rules

- `cpu_model`: the string exactly as `/proc/cpuinfo` `model name` reports it (the lookup collapses runs of whitespace, nothing else).
- `geekbench_major`: 6 or 7. Majors are never mixed or converted. The lookup prefers the major of a stale host record, else the newest.
- `single_core`: the published single-core score for that model and major, a positive integer.
- `source_url`: an `https://` page you read; `retrieved`: the date (`YYYY-MM-DD`) you read it.
- One entry per (model, major). Entries failing these rules are skipped at load and fail `tests/test_machine.py`.
- Never invent or interpolate a score, never scrape or redistribute the Geekbench database: cite single values from the public chart. A model
  without an entry is reported as `speed: null` with a reason, which is the correct result.
- Baselines (`machine.BASELINE_SCORE`): 2500 for GB6 (Core i7-12700) and for GB7 (Ryzen 7 7700), the same constants as
  `scripts/debian-install-v2/tools/cpu_benchmark.py`. Change both together, with a source.

## Refresh procedure

1. Pick the CPU models to add (`grep -m1 "model name" /proc/cpuinfo` on each host; hosts with a valid host benchmark do not need one).
2. Read the published single-core score for the model and major from the public chart; note the URL and today's date.
3. Add or replace the entry with the Edit tool; bump `table_version` when any score changes.
4. Run `tools/gate.sh unit` and `tools/gate.sh coverage`. The table test validates every entry.
5. Add a line to `CHANGES.md`.
