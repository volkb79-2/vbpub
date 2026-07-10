# P38 Report — TUI Smoke Evidence Harness

## What Was Built

Extended `python -m groop.acceptance` with a `tui-smoke` subcommand that
exercises the existing Textual `--ui-smoke` path from a child subprocess,
collecting wall time, child user/sys CPU, and child max RSS measurements.

### Key Components

1. **Data structures** (`TuiSmokeResult` dataclass) — carries `ok`, `exit_code`,
   parsed `smoke_line` fields (`frames`, `view`, `profile`), stdout/stderr
   snippets, and resource measurements.

2. **Argument parsing** — `tui-smoke` subcommand with `--replay PATH` (default:
   the deterministic fixture `.../gstammtisch-once.jsonl`), `--config PATH`,
   `--profile NAME`, `--timeout-s FLOAT` (default 30.0), `--json`, and
   `--pretty-json`.

3. **Core logic** (`run_tui_smoke()`) — uses `subprocess.run()` to invoke
   `python -m groop.cli --replay PATH --step --ui-smoke` with optional
   `--config`/`--profile` pass-through. Captures child resource usage via
   `resource.getrusage(RUSAGE_CHILDREN)` diff. Parses the `"ui smoke ok ..."`
   line from stdout. Handles `TimeoutExpired` gracefully.

4. **Output formatters** (`format_tui_smoke_text`, `format_tui_smoke_json`) —
   deterministic, sort-keys JSON and concise human-readable text following the
   existing smoke/steady patterns.

5. **Exit codes** — `0` on successful UI smoke (child exit 0 + "ui smoke ok"
   line found), `1` on failed smoke, `2` on argument validation errors.

6. **Import contract preserved** — no `textual` or `groop.ui.*` imports in the
   acceptance module. Confirmed by subprocess-level test.

## Deviations from Handoff

None. All functional requirements are met.

## Proposed Contract Changes

None. The `tui-smoke` subcommand is additive and package-private within
`acceptance.py`.

## Test Evidence

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_acceptance.py -q --tb=short
# 40 passed in 7.21s
```

Full suite:

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests -q --tb=short
# 382 passed in 41.88s
```

py_compile:

```bash
python3 -m py_compile groop/src/groop/acceptance.py groop/tests/test_acceptance.py
# (no output — clean)
```

### 14 New Tests

| Test | What It Covers |
|------|----------------|
| `test_parse_ui_smoke_line_parses_correctly` | Parsing `"ui smoke ok frames=1 view=tree profile=auto"` |
| `test_parse_ui_smoke_line_handles_garbage` | Empty/unparseable input returns `{}` |
| `test_run_tui_smoke_fixture_replay` | Full fixture replay exit 0, smoke line parsed |
| `test_run_tui_smoke_bad_replay_path` | Bad path exit 1, no smoke line |
| `test_run_tui_smoke_timeout` | Timeout exits through bounded result with `(timeout)` stderr snippet |
| `test_run_tui_smoke_with_profile` | `--profile minimal` pass-through |
| `test_format_tui_smoke_text_contains_expected_markers` | Text output format |
| `test_format_tui_smoke_json_parseable` | JSON output parseable with all fields |
| `test_subprocess_tui_smoke_json` | `python -m groop.acceptance tui-smoke --json` exit 0 |
| `test_subprocess_tui_smoke_text` | Text output exit 0, expected markers |
| `test_subprocess_tui_smoke_bad_replay` | Bad replay subprocess exit 1 |
| `test_subprocess_tui_smoke_pretty_json_parseable` | Pretty-json parseable |
| `test_subprocess_tui_smoke_profile_minimal` | Profile pass-through via subprocess |
| `test_subprocess_tui_smoke_invalid_timeout` | Invalid `--timeout-s` exit 2 |

### Fixture Evidence

Command:
```
PYTHONPATH=groop/src python3 -m groop.acceptance tui-smoke \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
```

Result:
- exit `0`, `ok: true`
- smoke line: `ui smoke ok frames=1 view=tree profile=auto`
- wall: `0.3465s`
- child user CPU: `0.2780s`
- child sys CPU: `0.0238s`
- child max RSS: `41688 KB`

### Import Contract

```
$ python3 -c "import groop.acceptance; import sys; print('textual' in sys.modules)"
False
```

## Known Gaps / Open Items

- Live 5-minute Textual TUI CPU/RSS evidence remains manual operator work
  (spec §9 items 1–2, as noted in `MEASUREMENTS.md`).
- The `tui-smoke` command requires Textual to be installed in the Python
  environment where the child process runs, but `groop.acceptance` itself does
  not. When Textual is absent the child prints an error and exits 2. This is
  correct behavior.
- The default `--timeout-s` of 30 seconds is test-friendly and avoids hangs;
  extremely slow CI environments may need a higher value.
