# P12 Log

- Worktree: `/tmp/vbpub-topos-p12-release-hardening`
- Branch: `feat/topos-p12-release-hardening`
- Scope: touched only `topos/handoff/reports/P12-LOG.md` and `topos/handoff/reports/P12-REPORT.md` in this worktree.

## Actions

1. Created a separate worktree from local `main`.
2. Inspected `topos/pyproject.toml`, `topos/src/topos/cli.py`, `topos/src/topos/drift/origin.py`, and `topos/tests/conftest.py`.
3. Ran the full `topos/tests` suite in an isolated venv.
4. Ran `py_compile` over `topos/src/topos`.
5. Ran replay UI smoke: `topos --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke`.
6. Ran `--once --json` fixture smoke against `topos/tests/fixtures/cgroupfs/gstammtisch` and captured bounded CPU/RSS evidence with a Python wrapper.
7. Built sdist and wheel with `python -m build`.
8. Installed the built wheel into a fresh isolated venv and verified `topos --version`.

## Results

- `pytest`: `79 passed in 11.28s`
- `py_compile`: passed with no output
- replay UI smoke: `ui smoke ok frames=1 view=tree profile=auto`
- `--once --json` smoke: passed; wrapper captured `WALL_SEC=0.189`, `CHILD_USER_SEC=0.134`, `CHILD_SYS_SEC=0.028`, `CHILD_MAXRSS_KB=29984`
- packaging: `topos-0.1.0.tar.gz` and `topos-0.1.0-py3-none-any.whl` built successfully; wheel install in a fresh venv succeeded; `topos --version` printed `topos 0.1.0`

## Decisions

- No code change was needed for a deterministic canned systemd fixture hook. `topos/tests/conftest.py` already provides `systemctl_fixture_runner`, and the existing tests cover the replay and once/json acceptance paths that matter here.
- No long-running benchmark was attempted. The bounded once/json smoke was enough to provide safe CPU/RSS evidence without hanging or requiring root.

## Blockers

- `python` and `pytest` were absent from the base shell, so checks had to run from isolated venvs.
- `/usr/bin/time` is not installed here, so RSS evidence came from the Python `resource` module instead.

## Next

- Keep the reports as the handoff record.
- Controller can fold the evidence into `docs/STATUS.md`, `MEASUREMENTS.md`, and the P12 handoff material later.
