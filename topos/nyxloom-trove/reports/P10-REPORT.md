# P10 Report

## What was built

- Added `topos.snapshot.bundle` for incident snapshot bundles:
  - bounded `frames.jsonl` capture using the existing record writer format;
  - raw cgroup copies for the selected entity plus ancestor memory protection
    files;
  - `systemctl-show.txt`, `docker-inspect.json`, `providers-status.json`, and
    `manifest.json`;
  - SHA-256 manifest entries for every bundled file;
  - optional `.tar.zst` output when `zstandard` is available, with `.tar`
    fallback;
  - safe archive extraction and manifest verification for inspection.
- Added `[snapshots]` config:
  - `dir`
  - `frames`
  - `redact`
- Added `topos snapshot inspect FILE`.
- Added TUI `x` hotkey support to save a snapshot for the selected row.
- Added tests for bounded frame capture, manifest hashes, cgroup copies,
  privacy redaction, empty history, same-second collision avoidance, CLI
  inspect, and the TUI hotkey path.

## Safety evidence

- Snapshot creation reads cgroup files and writes only under the configured
  snapshot directory, or the XDG state fallback.
- Archive inspection rejects unsafe tar member paths before extraction and uses
  Python's data filter.
- Redaction removes Docker `Config.Env` and `Config.Labels` and excludes fields
  outside the small Docker summary allowlist.
- Same-second snapshots for the same entity allocate unique bundle names instead
  of overwriting.

## Deviations / gaps

- The public `create(entity_key, ring, frame, config)` signature is present, but
  full-frame history is supplied by the TUI through an internal bounded deque.
  `HistoryRing` intentionally stores numeric series only and does not retain
  complete `Frame` objects.
- The TUI path currently records provider metadata already present on the frame.
  Live `systemctl show` and Docker inspect collection can be added later at the
  collector boundary where those providers already exist.

## Validation

```bash
PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p10-snapshots/topos/src python3 -m pytest topos/tests -q
# 74 passed in 10.59s
```

```bash
PYTHONPATH=/tmp/vbpub-topos-p10-snapshots/topos/src python3 -m py_compile $(find topos/src/topos -name '*.py' | sort)
# clean
```

```bash
PYTHONPATH=/tmp/vbpub-topos-p10-snapshots/topos/src python3 -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# once 1 8
```

```bash
PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p10-snapshots/topos/src python3 -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Known open items

- Add live provider fetches for systemctl and Docker metadata when snapshotting
  from the TUI.
- Add a richer Textual confirmation/status surface if snapshots become large
  enough to merit background task progress.
