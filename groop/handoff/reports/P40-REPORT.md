# P40 Textual 8 Test Compatibility Report

## Outcome

P40 restores the green full suite under the managed devcontainer environment
(Python 3.14.6, Textual 8.2.8) by replacing direct dependence on the removed
`Static.renderable` widget attribute with a version-compatible
`_static_text()` helper. The 15-test failure cascade reported in P39 is fully
resolved: **382/382 tests pass**, all 23 UI tests pass, all 40 acceptance
tests pass, and the P38 TUI smoke evidence harness exits zero.

## Summary

| Measure | Before (P39) | After (P40) |
|---|---|---|
| UI tests (`test_ui_app.py`) | 8/23 passed, 15 failed | 23/23 passed |
| Full suite | 367/382 passed, 15 failed | 382/382 passed |
| Acceptance tests | 40 passed (subprocess unaffected) | 40 passed |
| P38 tui-smoke | ALL CHECKS PASSED | ALL CHECKS PASSED |

## Diagnosis

The root cause is the removal of `Static.renderable` in Textual >=8.x. The
replacement property is `Static.content`, which returns the raw string or
VisualType content set via the constructor or `.update()`. No production code
uses `.renderable` — the entire impact is confined to the test file.

Three call sites were affected:

1. `_status_text()` helper (line 64) — used by 12 replay/status/snapshot tests.
2. `#damon-confirm-body` query (line 526) — used by the vaddr DAMON modal test.
3. `#hostmem-body` query (line 567) — used by the paddr DAMON modal test.

## Fix

A single reusable test helper was added to `groop/tests/test_ui_app.py`:

```python
def _static_text(w: Static) -> str:
    """Get the displayed text content of a ``Static`` widget."""
    if hasattr(w, "content"):
        return str(w.content)
    return str(w.renderable)  # pragma: no cover -- Textual <1
```

The helper:
- Tries `.content` first (available since Textual >=1, present in Textual 8.x).
- Falls back to `.renderable` (Textual <1 / `<0.58` target range).
- Always returns a plain string for substring assertions.
- Is fully backward-compatible — no version checks, skips, xfails, or pins.

## Validation

All required gates pass in the managed environment:

```bash
# Focused UI tests
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests/test_ui_app.py -v
# 23 passed in 11.40s

# Full suite
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests -q
# 382 passed in 49.63s

# Acceptance tests
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests/test_acceptance.py -v
# 40 passed in 8.70s

# P38 TUI smoke
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m groop.acceptance tui-smoke
# ALL CHECKS PASSED (exit code 0)

# Python compile
/home/vscode/.venv/bin/python -m py_compile groop/tests/test_ui_app.py
# PASS
```

## Files Changed

- `groop/tests/test_ui_app.py` — added `_static_text()` helper, replaced 3
  `.renderable` references with `_static_text()` calls, added `typing.Any`
  import for type annotations.
- `groop/README.md` — P40 status changed from "Planned" to "Done".
- `groop/docs/STATUS.md` — removed the P40 blocker note from v1 confidence
  description; updated "Current Quality Gate" with P40 evidence.
- `groop/docs/ROADMAP.md` — added P40 to the flowchart; updated remaining
  estimate; updated P40 description from "planned" to "done".
- `groop/MEASUREMENTS.md` — replaced the "Current Release Blocker" section
  with the P40 green suite evidence.
- `groop/handoff/reports/P40-LOG.md` — this work log.
- `groop/handoff/reports/P40-REPORT.md` — this report.

## Deviations from the Handoff Doc

None. The handoff requirements are fully met:
- Test helper is small and reusable (`_static_text()`).
- Semantic UI assertions are preserved identically (same substring checks).
- No skips, xfails, version excludes, or private-API assertions added.
- No production code changes.
- Focused coverage for the helper itself is minimal (it is a one-liner with
  two branches; a future version-parameterized test matrix could be added
  but is not blocking).

## Remaining Gates (unchanged from P39)

Before a production-certified v1/v1.5 tag:

- Record five-minute live TUI CPU/RSS.
- Record controlled live drift/reversion and formatted replay fidelity.
- Record local-artifact pipx/no-config behavior.
- Record the exact live docker-group non-root smoke.
- Record DAMON and daemon live evidence only when those capabilities are in
  the release claim.

P40 removes the automated suite blocker; these manual live-host gates remain.
