# P40 Textual 8 Test Compatibility Report

## Outcome

P40 restores semantic UI tests under the managed Textual 8.2.8 environment
without weakening assertions or changing production code. It also verifies the
same helper at the package's declared Textual 0.58 endpoint.

## Root Cause

`groop/tests/test_ui_app.py` read `Static.renderable`, which exists in Textual
0.58.1 but not Textual 8.2.8. A shared status helper and two direct DAMON body
checks accounted for all 15 failures.

## Fix

The tests now inspect displayed content through the public widget render path:

```python
def _static_text(w: Static) -> str:
    return str(w.render())
```

The existing replay, action-gating, snapshot, and DAMON assertions are
unchanged. No version detection, private state, skip, xfail, or dependency pin
was introduced.

The published dependency remains `textual>=0.58,<1`. Passing under Textual
8.2.8 documents compatibility with the managed development environment; it
does not claim that all Textual 1-8 releases are package-supported.

## Evidence

- Textual 0.58.1 isolated UI tests: 23 passed in 8.35s.
- Textual 8.2.8 managed UI suite: 23 passed in 11.24s.
- Textual 8.2.8 managed full suite: 382 passed in 48.04s.
- Focused acceptance: 40 passed in 8.12s.
- P38 TUI smoke: exit 0, `ok: true`, `frames: 1`, `view: tree`, `profile: auto`.
- Changed-file Python compilation and diff whitespace: passed.

## Documentation

- README marks P40 done.
- STATUS and MEASUREMENTS replace the P39 automated-suite blocker with verified
  green evidence.
- ROADMAP records P40 in the dependency chain and returns automated release
  packages to zero remaining.

## Remaining Release Gates

P40 closes only the automated Textual suite blocker. Five-minute live TUI
CPU/RSS, controlled drift/reversion, rendered replay fidelity, exact
docker-group non-root smoke, and applicable DAMON/daemon live evidence remain
governed by `docs/RELEASE-READINESS.md`. Local-artifact pipx/no-config
acceptance passed after merge and is recorded in `MEASUREMENTS.md`.

P40 merged on main as `970953a` after P39 merge `bfdf3db`. Post-merge
controller validation recorded 382 passed in 47.73s, 40 focused acceptance
tests in 7.54s, passing P38 TUI smoke, and clean full-source compilation.
