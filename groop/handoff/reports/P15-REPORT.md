# P15 Report

## What changed

- Added `snapshot/enrich.py` for bounded, injectable fresh metadata collection:
  selected-row `systemctl show` and Docker inspect summary.
- Wired the TUI snapshot hotkey to collect fresh metadata at snapshot time and
  record provider statuses without failing when providers are missing.
- Improved `groop snapshot inspect` output with redaction state, notable files,
  and explicit hash failure paths.
- Expanded snapshot tests for injected systemd/Docker metadata, Docker
  env/label redaction through the TUI path, notable inspect output, and hash
  mismatch reporting.
- Updated operations docs with snapshot location, contents, and redaction
  behavior.

## Deviations from handoff

- No progress spinner was added. Snapshot creation remains bounded and reports a
  success/failure status path in the TUI footer.
- Docker/systemd collection is best-effort and injectable; missing live providers
  are recorded in `providers-status.json` instead of failing bundle creation.

## Proposed contract changes

- None.

## Tests run

```bash
# /tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests -q
# 89 passed in 14.96s

# find groop/src -name '*.py' -print0 | xargs -0 /tmp/vbpub-groop-p13-venv/bin/python -m py_compile
# (no output)

# /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch
# schema_version=1 entities=8 host_metrics=20

# /tmp/vbpub-groop-p13-venv/bin/python -m groop.cli --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Known gaps / open items

- Snapshot creation is still synchronous in the current TUI action; the operation
  is bounded, but a future progress screen could improve feedback for slow
  providers.
- Redaction currently removes Docker environment variables and labels. Future
  fields should be added deliberately as privacy needs become concrete.
