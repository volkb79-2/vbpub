# P12 Report

Release-hardening validation completed for the current `topos` prototype.

## What I did

- Verified the package metadata and console script entry point.
- Ran the full test suite in an isolated venv.
- Ran bytecode compilation across `topos/src/topos`.
- Exercised the `--once --json` fixture path on canned cgroup data.
- Exercised the replay UI smoke path on the golden recording.
- Built sdist and wheel artifacts.
- Installed the wheel into a fresh venv and confirmed `topos --version`.

## Evidence

- `pytest`: `79 passed`
- `py_compile`: passed
- fixture smoke: JSON output produced successfully from `topos/tests/fixtures/cgroupfs/gstammtisch`
- replay smoke: `ui smoke ok frames=1 view=tree profile=auto`
- CPU/RSS sample for once/json smoke:
  - wall: `0.189s`
  - child user: `0.134s`
  - child sys: `0.028s`
  - max RSS: `29984 KB`
- packaging:
  - built `topos-0.1.0.tar.gz`
  - built `topos-0.1.0-py3-none-any.whl`
  - fresh wheel install succeeded
  - `topos --version` returned `topos 0.1.0`

## Deviations and blockers

- No new deterministic systemd-data hook was added. The repo already has a focused test fixture runner in `topos/tests/conftest.py`, and the existing tests already exercise the canned-systemd path well enough for this release-hardening pass.
- `python`/`pytest` were not present on the base PATH, so all checks ran in isolated venvs.
- `time -v` was unavailable, so resource evidence came from Python `resource` output.

## Recommendation

- Treat this as a release-candidate readiness checkpoint for packaging and replay/fixture behavior.
- If the controller wants the evidence mirrored into the docs tree, fold this report and the log into the canonical handoff docs later.
