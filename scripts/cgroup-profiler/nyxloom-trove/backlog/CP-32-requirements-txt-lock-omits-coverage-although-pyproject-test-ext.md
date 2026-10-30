---
kind: backlog-entry
schema_version: 1
id: CP-32
title: "requirements.txt lock omits coverage although pyproject [test] extra requires it; setup.sh --check cannot vouch for the coverage gate"
status: open
type: "bugfix"
severity: "low"
component: "gate"
context_estimate: "small"
provenance: "Pre-release deep review of the 1.1.0 wave branch, 2026-10-27 (fable-cgprofile-20261027)"
filed_by: "Claude (cgprofile 1.1.0 pre-release review)"
spec_owner: "cgroup-profiler gate"
priority: 3
filed_date: "2026-10-27"
---

## Observed

`pyproject.toml` declares `test = ["cgroup-profiler[report]", "pytest>=8", "coverage[toml]>=7.6"]` and says the
lock `requirements.txt` is generated from it by `./setup.sh --relock` (`pip freeze` of a venv built from `.[test]`).
The committed lock has no `coverage` line at all (`grep -c coverage requirements.txt` is 0): it was generated before
coverage joined the extra and never re-locked. Consequences:

- a venv built by `./setup.sh` cannot run `tools/gate.sh <wt> coverage`'s command locally (`python -m coverage` is
  missing), and `./setup.sh --check` reports "venv OK" for it;
- the "rebuild months from now reproduces this venv" claim in `setup.sh`'s header is false for the test closure.

The registered gate is unaffected: it runs inside tester-unified, whose closure is derived from `pyproject.toml`
directly, not from this lock. The review venv for 2026-10-27 had to `pip install coverage` by hand.

## Required contract

- Re-lock with `./setup.sh --relock` under the estate freshness rule (prefer the latest release at least 14 days old)
  and commit the diff; the lock must contain every distribution the `[test]` extra resolves to.
- Add a test (collector-tier, `tests/test_deployment_contract.py` is the natural home) that every top-level
  distribution named in `[project.optional-dependencies].test`/`.report` appears in `requirements.txt`, so the lock
  cannot drift silently again.

## Oracles

`setup.sh --check` on a fresh `setup.sh` venv passes AND `venv/bin/python -m coverage --version` works; the new test
fails when `coverage` is removed from the lock.
