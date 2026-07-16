# P33 - Release Smoke Harness

## Goal

Add a small, rootless acceptance smoke harness that produces repeatable release
evidence for the current safe paths without requiring operators to remember a
sequence of manual commands.

This is release-confidence work, not privileged daemon/DAMON/BPF work. Keep it
independent from `topos/src/topos/cli.py` if possible so it can run in parallel
with daemon CLI slices.

## Workflow

Follow `topos/README.md` "Workflow protocol" exactly.

- Branch: `feat/topos-p33-release-smoke`
- Worktree: `.worktrees/-topos-p33-release-smoke`
- Branch from local `main`
- Touch only `topos/**`
- Keep `topos/handoff/reports/P33-LOG.md` updated while working
- Finish with `topos/handoff/reports/P33-REPORT.md` and a focused commit

## Required Context

Read before coding:

- `topos/README.md`
- `topos/CONTRACTS.md`
- `topos/MEASUREMENTS.md`
- `topos/docs/OPERATIONS.md`
- `topos/docs/STATUS.md`
- `topos/src/topos/collect/collector.py`
- `topos/src/topos/model.py`
- `topos/src/topos/record/replay.py`
- `topos/tests/conftest.py`
- `topos/tests/test_collector.py`
- `topos/tests/test_record.py`

## Functional Requirements

Create a module runnable as:

```bash
python -m topos.acceptance smoke [--cgroup-root PATH] [--replay PATH] [--json] [--pretty-json]
```

The harness should be read-only and rootless:

- no subprocess execution;
- no systemd/docker commands;
- no file mutation except normal stdout/stderr;
- no DAMON/BPF/sysfs writes;
- no Textual import requirement.

Checks:

- collect one frame using `Collector(cgroup_root=...)`;
- serialize it through `frame_to_jsonable`;
- report schema version, timestamp, entity count, and source labels/availability summary useful for evidence;
- if `--replay PATH` is provided, load it with `ReplayDriver.from_path()` and report frame count/first/last timestamps without needing Textual;
- measure wall time, user CPU, sys CPU, and max RSS for the smoke run using standard-library APIs.

Output:

- JSON should be deterministic and include:
  - `ok`;
  - `version`;
  - Python/platform metadata;
  - `checks` list with names, ok booleans, messages, and details;
  - `measurements` for wall/user/sys/rss;
  - collected frame summary.
- Text should be concise and suitable for pasting into `MEASUREMENTS.md`.
- Return `0` when all requested checks pass, `1` for smoke-check failures, `2`
  for usage/validation errors.

Implementation shape:

- Prefer `topos/src/topos/acceptance.py` with small dataclasses/helpers.
- Use a minimal `if __name__ == "__main__"` entry point for `python -m`.
- Do not add a `topos acceptance` subcommand in `cli.py` in this package; avoid
  conflicts with daemon CLI work.

## Tests

Add focused tests covering:

- JSON smoke with the existing fixture cgroup root;
- text smoke with the fixture root;
- replay summary with `topos/tests/fixtures/frames/gstammtisch-once.jsonl`;
- parseable pretty JSON;
- no Textual import requirement;
- non-existent replay path returns a controlled non-zero result.

Keep tests deterministic. Use `PYTHONPATH=topos/src` style invocations where a
subprocess is the cleanest way to verify `python -m topos.acceptance`.

## Documentation

Update:

- `topos/MEASUREMENTS.md` with the new command as the preferred rootless smoke
  evidence path.
- `topos/docs/OPERATIONS.md` with a short release-smoke command example.

Do not update merge evidence in `docs/STATUS.md`; the controller does that after
review and merge.

## Out Of Scope

- No live 5-minute TUI CPU/RSS benchmark.
- No privileged DAMON or BPF acceptance.
- No packaging build command execution inside the harness.
- No daemon status checks; P32 covers that.
