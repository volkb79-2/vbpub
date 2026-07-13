# P62-REPORT — Steady-State Window Auto-Detection

## What Was Built

`groop report --window auto` now selects the longest stable trailing suffix of
a P2 recording before passing exactly that frame subset to P54's unchanged
`compute_profile` math. The JSON output records `"window_mode":"auto"` and
`"window_detected":true|false`; successful detection also reports the selected
inclusive `window_start_ts` and `window_end_ts`. Detection failure falls back to
the all-frames profile without raising.

The detector is the independently testable `detect_steady_window()` helper.
For each candidate suffix of at least `--min-frames` (default 3), entities must
have a finite selected gauge value in every frame. The busiest eligible entity
is the one with the greatest gauge mean, with lexical EntityKey tie-breaking.
Its population CoV (`stddev / mean`) must be <= `--stability-cov` (default
0.05). An all-zero series has CoV 0; a non-constant zero-mean series is
rejected. Candidates are scanned shortest to longest, retaining the final
match, so the result is the longest valid trailing suffix.

New options:

- `--stability-gauge METRIC` — one of the fixed P54 report gauges; default `ram`.
- `--stability-cov FLOAT` — finite non-negative maximum CoV; default `0.05`.
- `--min-frames N` — positive candidate-frame floor; default `3`.

P61 assertions evaluate the detected profile because the CLI resolves the
window before running the existing assertion evaluator.

## Files Changed

- `src/groop/report.py`: pure detector, resolved-window/result types, safe
  option validation, auto metadata serialization, and additive load helper.
- `src/groop/cli.py`: auto-window and stability option parsing; resolved
  profiles feed unchanged into P61 assertion evaluation.
- `tests/test_report.py`: exact-boundary oracle, noisy fallback, override,
  busiest-entity, deterministic JSON, malformed CLI, and assertion-composition
  tests using production P2 recording writes.
- `README.md`, `docs/OPERATIONS.md`: operator example and pinned criterion.
- `handoff/reports/P62-LOG.md`: resumability log.

## Deviations from Handoff

None. The stability criterion's previously unspecified edge rules are pinned
in the log and detector docstring.

## Contract Changes

None. P2 recording and P54 profile formats are unchanged; auto metadata is
additive only to `groop report --window auto` output.

## Test Evidence

Environment: `/home/vscode/.venv/bin/python` (Python 3.14.6, pytest 8.4.2),
Linux amd64.

```text
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest \
  groop/tests/test_report.py -q -W error -p no:schemathesis
105 passed in 3.74s
```

The global Schemathesis pytest plugin is disabled only for warning-as-error
runs: it imports a deprecated jsonschema symbol during every test call, making
its own deprecation warning fail all tests before groop code executes. With the
plugin enabled, the same focused command produces 105 environment-level
failures; this is unrelated to P62.

```text
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m py_compile \
  groop/src/groop/report.py groop/src/groop/cli.py groop/tests/test_report.py
# OK

PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m groop.cli report \
  groop/tests/fixtures/frames/gstammtisch-once.jsonl --json --window auto
# exit 0; emits window_mode=auto, window_detected=false for the single-frame fixture

PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m groop.cli --once --json
# exit 0; emitted valid JSON

git diff --check
# no issues
```

The required full suite completed under `timeout 300` and `-W error`, with only
the unrelated Schemathesis plugin disabled. Its pytest `lastfailed` cache was
empty after completion. The managed terminal runner did not retain the normal
pytest summary line for this long command; the focused pass above is the
complete output-tail evidence.

## Known Gaps / Open Items

- Detection intentionally uses one global window, one primary gauge, and no
  change-point/composite scoring, as scoped by P62.
- Full-suite failure with the global Schemathesis plugin under `-W error` is an
  existing environment dependency warning, not a groop test failure.

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
