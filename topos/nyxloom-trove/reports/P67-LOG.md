# P67 Work Log

## Context

- Branch: `feat/topos-p67-versioned-read-http-gateway-v2`
- Worktree: `/workspaces/vbpub/.worktrees/topos-p67-versioned-read-http-gateway-v2`
- Base commit: `27e0a6ad05c3c3778731ae934ad2c67f8f1f9d3d`
- Package: P67 — Versioned Read HTTP Gateway
- Current objective: Implement the trust-boundary-hardened, stdlib HTTP read gateway.

## Timeline

```text
2026-07-13 UTC
- Action: Read the P67 handoff, standing workflow contracts, P52/P63 surfaces, P69 trust analysis, and daemon deployment documentation.
- Commands: rg/find/sed/git log inspection commands.
- Files changed: topos/handoff/reports/P67-LOG.md.
- Result: Confirmed P63 supplies typed hello/current/history/entity methods; P66 is not present on this base, so health must remain gated off.
- Follow-up: Add stdlib gateway, adversarial real-stack tests, deployment documentation, then run package-venv gates.

2026-07-13 UTC
- Action: Implemented the stdlib HTTP gateway, CLI entry point, P67 adversarial real-stack tests, and deployment documentation.
- Commands: `PYTHONPATH=topos/src /workspaces/vbpub/.venv/bin/python -m pytest topos/tests/test_daemon_http_gateway.py -q -W error -p no:schemathesis`; `python3 -m py_compile ...`; `git diff --check`.
- Files changed: `src/topos/daemon/http_gateway.py`, `src/topos/cli.py`, `tests/test_daemon_http_gateway.py`, `docs/DAEMON.md`, `README.md`, and this log.
- Result: Focused package-venv test gate passed (36 tests); compile and diff checks passed.
- Follow-up: Run the complete package-venv suite, write the final report, stage and commit.

2026-07-13 UTC
- Action: Ran full regressions in the clean package test venv and wrote the handoff report.
- Commands: `timeout 900 env PYTHONPATH=topos/src /tmp/p43-clean-venv/bin/python -m pytest topos/tests -q -W error -p no:schemathesis`; isolated rerun of the one transient record test.
- Files changed: `topos/handoff/reports/P67-REPORT.md` and this log.
- Result: Final clean full gate completed successfully with an empty pytest `lastfailed` cache. The workspace `.venv` optional-zstandard mismatch is documented in the report and is unrelated to P67.
- Follow-up: Stage all P67 files, run final focused/compile/diff checks, and commit the feature branch.

2026-07-13 UTC
- Action: Performed the standing self-review against P67's four trust-boundary groups and fixed configuration-input validation plus complete error-code mapping coverage.
- Commands: `PYTHONPATH=topos/src /workspaces/vbpub/.venv/bin/python -m pytest topos/tests/test_daemon_http_gateway.py -q -W error -p no:schemathesis`; `timeout 900 env PYTHONPATH=topos/src /tmp/p43-clean-venv/bin/python -m pytest topos/tests -q -W error -p no:schemathesis`; `py_compile`; `git diff --check`.
- Files changed: `src/topos/daemon/http_gateway.py`, `tests/test_daemon_http_gateway.py`, `handoff/reports/P67-REPORT.md`, this log, and `handoff/reports/P67-SELFREVIEW.md`.
- Result: Focused gate passed 47 tests. The clean full regression gate completed successfully with an empty pytest `lastfailed` cache.
- Follow-up: Commit this separate self-review fix and report.
```

## Decisions

- Decision: Use the handoff-permitted trusted-local-reverse-proxy identity header, `X-Topos-Principal`.
  Reason: It is the explicitly permitted v1 authentication shape; the gateway accepts it only from a loopback peer and maps configured principals to closed Sensitivity ceilings.
  Impact: No cookie, CORS, JSONP, or direct unauthenticated browser access is introduced.

- Decision: Omit the health route on this base.
  Reason: P66's typed versioned health client method is unavailable; P67 forbids using legacy `request_health` as a fallback.
  Impact: The exposed route set is hello/current/history/entity only.

## Blockers

- Blocker: None.

## Validation

```bash
PYTHONPATH=topos/src /workspaces/vbpub/.venv/bin/python -m pytest topos/tests/test_daemon_http_gateway.py -q -W error -p no:schemathesis
# 47 passed in 23.86s

timeout 900 env PYTHONPATH=topos/src /tmp/p43-clean-venv/bin/python -m pytest topos/tests -q -W error -p no:schemathesis
# passed (exit 0; pytest lastfailed cache empty)

/tmp/p43-clean-venv/bin/python -m py_compile topos/src/topos/daemon/http_gateway.py topos/src/topos/cli.py topos/tests/test_daemon_http_gateway.py
# clean

git diff --check
# clean
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed (`1bfb902`).
