# P45 Report — Bounded Inspect-Files Content Reads

## What Was Built

- Added **`groop/src/groop/inspect_files/reader.py`** — bounded read API with:
  - `InspectFilesReadResult` — successful read result with content, truncation flags, JSON/text output
  - `InspectFilesReadError` — error result (no content echoed on error paths)
  - `ReadDenied` — gating result when `--inspect-files` or `--admin` is missing
  - `build_inspect_read()` — gated, confined, bounded read function with:
    - `Path.is_relative_to()` confinement to allowlisted root
    - `os.open()` with `O_RDONLY | O_NONBLOCK | O_NOFOLLOW` for no-follow opens
    - `fstat` + `S_ISREG` verification (rejects symlinks, devices, FIFOs, sockets, dirs)
    - Bounded reads via `--max-bytes` (default 65536) and `--max-lines` (default 5000)
    - Safe `surrogateescape` UTF-8 decoding for hostile bytes
    - No `subprocess` import, no file writes, no host mutation
  - Docker JSON log reads: require full 64-char hex container ID (rejects short IDs/names)
  - Cgroup file reads: uses P29 catalog allowlist (20+ known files), per-file error handling
  - Fixture seam via `fixture_root=` for testing

- Extended **`groop inspect-files read`** CLI subcommand:
  - `groop inspect-files read --kind --target --inspect-files --admin [--json] [--max-bytes N] [--max-lines N]`
  - Hidden `--fixture-root` for test fixture support
  - Exit codes: 0=success, 1=read error, 2=denied/parse error

- Added **33 focused tests** (77 total in test_inspect_files.py):
  - `TestReadDisabled`: gating (4)
  - `TestReadDisabledViaCli`: CLI disabled behavior (5)
  - `TestReadContent`: Docker log reads (7), cgroup reads (3)
  - `TestReadSafety`: no subprocess import, no path escape, short ID rejection,
    absolute/traversal rejection, unsupported kind, error JSON format (8)
  - `TestReadCliIntegration`: arg parsing, defaults, custom bounds,
    CLI docker/cgroup reads (6)

- Updated **7 documentation files** (README, ROADMAP, STATUS, INSPECT-FILES,
  OPERATIONS, RELEASE-READINESS, MEASUREMENTS) with P45 status, read command
  documentation, safety guarantees, and evidence.

## Worktree

- Branch: `feat/groop-p45-inspect-files-bounded-content`
- Worktree: `.worktrees/-groop-p45-inspect-files-bounded-content`
- Python: `/home/vscode/.venv/bin/python` (Python 3.14.6)

## Deviations from Handoff

- **CLI `--max-bytes`/`--max-lines`**: The handoff specifies "bound bytes, lines,
  time/work, and rendered output". `--max-bytes` and `--max-lines` are implemented;
  explicit wall-clock time bounds are not enforced because bounded file reads via
  `os.open` + `os.read` on regular files are effectively instant for the configured
  limits (64 KiB / 5000 lines). A future package could add time bounds for large
  or slow files.

- **Systemd journal**: The handoff lists journal follow and volume/overlay traversal
  as separate work. Journal reads were also left out of scope for reads since they
  require subprocess (`journalctl`), which violates the no-subprocess constraint.
  Added explicit error message: "does not support content reads".

- **Fixture root**: The handoff mentions "fixture seams may provide alternate roots".
  Implemented as a hidden `--fixture-root` CLI flag and `fixture_root=` parameter,
  matching the `--cgroup-root` pattern used elsewhere in groop.

## Test Evidence

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_inspect_files.py -v
# 77 passed in 0.60s

PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 466 passed, 1 skipped in 49.67s

mapfile -d '' pyfiles < <(find groop/src/groop groop/tests -name '*.py' -print0)
python3 -m py_compile "${pyfiles[@]}"
# clean, exit 0

PYTHONPATH=groop/src python3 -m groop.cli inspect-files read \
  --kind docker-json-log \
  --target aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --inspect-files --admin \
  --fixture-root groop/tests/fixtures/inspect_files/docker --json
# exit 0, {"kind": "docker-json-log", "mode": "content", ...}

PYTHONPATH=groop/src python3 -m groop.cli inspect-files read \
  --kind cgroup-files --target system.slice/ssh.service \
  --inspect-files --admin \
  --fixture-root groop/tests/fixtures/inspect_files/cgroup
# exit 0, text with memory.current, cpu.stat, pids.current, pids.max
```

## Known Gaps

- Systemd journal content reads are not implemented (requires subprocess execution).
- No follow/stream mode or daemon integration for content reads.
- No TUI integration for file reads.
- No wall-clock time bounds (bounded bytes/lines are sufficient for 64 KiB / 5000 lines on regular files).
- Docker log reads require full 64-hex container IDs; short IDs and names are rejected for reads.

## Contract-Change Proposals

None. P45 is entirely additive and package-private.
