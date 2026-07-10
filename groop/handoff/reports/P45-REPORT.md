# P45 Report — Bounded Inspect-Files Content Reads (Corrected)

## What Was Built

- Added **`groop/src/groop/inspect_files/reader.py`** — bounded read API with:
  - `InspectFilesReadResult` — successful read result with content, truncation flags, JSON/text output
  - `InspectFilesReadError` — error result (no content echoed on error paths)
  - `ReadDenied` — gating result when `--inspect-files` or `--admin` is missing
  - `build_inspect_read()` — gated, confined, bounded read function with:
    - **Descriptor-relative path confinement**: opens `allow_root` with
      `O_DIRECTORY|O_NOFOLLOW`, walks each intermediate component via `dir_fd`
      with `O_NOFOLLOW` — not a lexical `is_relative_to` alone. Race-resistant
      against symlink swaps.
    - `os.open()` with `O_RDONLY | O_NONBLOCK | O_NOFOLLOW` for no-follow opens
    - `fstat` + `S_ISREG` verification (rejects symlinks, devices, FIFOs, sockets, dirs)
    - **Chunk-based bounded reads** (never line-by-line): single giant lines are
      bounded by `_READ_CHUNK_SIZE` (64 KiB).
    - **Aggregate limits**: `max_bytes`/`max_lines` apply across all files in a
      multi-file read (e.g. cgroup), not per-file.
    - **Conservative absolute maximums**: `_ABSOLUTE_MAX_BYTES` (1 MiB),
      `_ABSOLUTE_MAX_LINES` (100K) — negative/zero/huge values are rejected.
    - **Root enforcement**: production reads require EUID 0 (TUI-SPEC §4.8);
      injectable `fixture_root` seam bypasses in tests.
    - Replacement decoding plus terminal-control sanitization for hostile bytes
    - No `subprocess` import, no file writes, no host mutation
  - Docker JSON log reads: require full 64-char hex container ID (rejects short IDs/names)
  - Cgroup file reads: uses P29 catalog allowlist (20+ known files), per-file error handling
  - Fixture seam via `fixture_root=` parameter for testing

- Extended **`groop inspect-files read`** CLI subcommand:
  - `groop inspect-files read --kind --target --inspect-files --admin [--json] [--max-bytes N] [--max-lines N]`
  - **Removed `--fixture-root`** from production CLI — users cannot select arbitrary roots
  - Exit codes: 0=success, 1=read error, 2=denied/parse error

- Added **15 security/boundary tests** (92 total in test_inspect_files.py):
  - `TestReadSecurityCorrections` (15 tests): symlink escape, FIFO rejection,
    giant line (chunk-based), aggregate bytes/lines truncation, negative/zero/huge
    limits, CLI fixture-root absence, root requirement, hostile bytes safety,
    no subprocess/writes

- Replaced **20,000-line oversized fixture** (689 KB) with compact 10-line version.

## Worktree

- Branch: `feat/groop-p45-inspect-files-bounded-content`
- Worktree: `.worktrees/-groop-p45-inspect-files-bounded-content`
- Python: Python 3.14.6

## Security Corrections Applied

| Issue | Before | After |
|-------|--------|-------|
| Byte-bounded chunk reads | Line-by-line iteration (`for line in buf`) — single giant lines unbounded | Fixed-size chunk reads (64 KiB); `read_size` removed |
| Path confinement | Lexical `Path.is_relative_to()` — race-prone | Descriptor-relative fd walk with `O_NOFOLLOW` on every component |
| Limit validation | Any value accepted; limits per-file | Positive ints only; caps at 1 MiB / 100K lines; aggregate across cgroup files |
| `--fixture-root` in CLI | Hidden but present; users could select arbitrary root | Removed entirely from CLI parser; `fixture_root=` is Python-only seam |
| Root check | None | Literal `True` from `os.geteuid() == 0` or an injected test predicate; fixture roots do not bypass it |
| 20K-line fixture | 689 KB committed binary | 10-line compact fixture (532 bytes) |
| Test coverage | 77 tests | 113 tests after controller security/boundary corrections |

## Deviations from Handoff

- **Work bound**: reads cover a fixed catalog file set and stop at absolute
  byte/line caps using fixed-size chunks. No claim is made that synchronous
  local-filesystem `open`/`read` has a preemptive wall-clock deadline.

- **Systemd journal**: The handoff lists journal follow and volume/overlay traversal
  as separate work. Journal reads were left out of scope for reads since they
  require subprocess (`journalctl`), which violates the no-subprocess constraint.
  Added explicit error message: "does not support content reads".

- **Fixture root**: The handoff mentions "fixture seams may provide alternate roots".
  Implemented as a Python `fixture_root=` parameter only — NOT a CLI flag.
  Removed `--fixture-root` from production CLI per security review.

## Test Evidence

Merged to `main` as `b5ba9af` after controller security review.

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_inspect_files.py -v
# 113 passed in 0.64s (controller focused gate)

PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 623 passed, 1 skipped in 48.05s after current-main reconciliation

mapfile -d '' pyfiles < <(find groop/src/groop groop/tests -name '*.py' -print0)
python3 -m py_compile "${pyfiles[@]}"
# clean, exit 0
```

## Known Gaps

- Systemd journal content reads are not implemented (requires subprocess execution).
- No follow/stream mode or daemon integration for content reads.
- No TUI integration for file reads.
- No preemptive wall-clock deadline around local regular-file syscalls; work
  and returned content are strictly bounded.
- Docker log reads require full 64-hex container IDs; short IDs and names are rejected for reads.

## Contract-Change Proposals

The public CLI is additive. Its caller-selected byte/line limits cover the
content payload; the fixed typed metadata envelope is bounded separately.
