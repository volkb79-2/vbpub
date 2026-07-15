# Standing package contracts — handoffctl2 implementation waves

Inherited by EVERY handoff in this directory. Read once, follow exactly.
Today's date: 2026-07-15 (use it; wrong dates are review-rejected).

## Environment

- Work dir: `/workspaces/vbpub/handoffctl2`. Never leave it.
- Python: `/workspaces/vbpub/.venv/bin/python` (3.13; PyYAML, jsonschema,
  hypothesis, pytest installed — install NOTHING).
- Gate (the only accepted evidence):
  `cd /workspaces/vbpub/handoffctl2 && /workspaces/vbpub/.venv/bin/python -m pytest tests/<your test files> -q`
  Run it; paste the tail of its real output into your REPORT.

## Frozen files — read, NEVER modify

`pyproject.toml`, `tests/conftest.py`, `schemas/`, `docs/`, and
`src/handoffctl/{__init__,types,paths,storage,config,leases}.py`, plus every
file owned by another package. Your module's stub DOCSTRING is the normative
interface: implement beneath it, keep the docstring and all public
signatures EXACTLY as written. If a frozen file or the contract seems wrong,
insufficient, or impossible: STOP — do not improvise, do not work around —
write `BLOCKED: <reason>` in your REPORT and final message, and exit.

## Cross-package dependencies

Other packages are being implemented in parallel; their modules may still
raise NotImplementedError. Code against their frozen interfaces; in YOUR
tests, monkeypatch those functions with canned returns where your handoff
says so. Never import-and-hope; never reimplement another package's logic.

## Code and test rules

- stdlib + PyYAML + jsonschema (+ hypothesis in tests) only. Type hints on
  public functions. No dead code, no scaffolding, ASCII only.
- Use conftest fixtures (`tmp_state`, `sample_project`, `make_handoff`).
  Local fixtures go in YOUR test file, never conftest.
- No hollow tests: assert observable artifacts (files written, events
  appended, exit codes, rendered content), not call bookkeeping. Every
  bound/negative case in your handoff's oracles gets a test that VIOLATES
  it and asserts the outcome.
- Determinism: no sleeps>2s, no network (except handoff-specified loopback
  servers), no reliance on wall-clock beyond monotonic ordering.

## Deliverables (all four, or the package is incomplete)

1. Implementation in your owned files only.
2. Tests green under the gate command.
3. `handoff/reports/P<NN>-REPORT.md`: result (done|BLOCKED), per-oracle
   pass/fail table, files touched, gate output tail (verbatim), deviations
   or assumptions, suggestions for the reviewer (do NOT act on them).
4. Final message = short receipt: `result / oracles: n pass m fail /
   files: ... / notes`.

## Never

Commit or run any git write command (worktree creation inside tests via the
fixtures is fine); touch files you don't own; start long-lived daemons that
outlive your tests; call external networks or AI services; edit this file
or any handoff.
