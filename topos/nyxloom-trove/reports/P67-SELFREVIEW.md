# P67 SELFREVIEW — Trust-boundary pass

2026-07-13 UTC

## 1. Gates and evidence

**FINDING: fixed.** The implementation report's focused-test count was stale
after the root-entity regression test landed. The self-review reran the focused
gate and updated the report with its real result:

```bash
PYTHONPATH=topos/src /workspaces/vbpub/.venv/bin/python -m pytest \
  topos/tests/test_daemon_http_gateway.py -q -W error -p no:schemathesis
# 47 passed in 23.86s
```

The timeout-wrapped clean package-venv full gate was rerun after the fixes and
completed successfully. `py_compile` and `git diff --check` were rerun before
the self-review commit. The report contains no invented total-suite count.

## 2. Scope and four contract groups

**FINDING: none after fixes.** Every changed file is under `topos/**`. The
handoff's four trust-boundary groups were checked mechanically:

1. **Safe bind:** literal-IP validation rejects wildcards/LAN addresses unless
   both `--allow-non-loopback` and non-empty auth configuration are supplied;
   refusal is `GatewayStartupError`. The default listener is `127.0.0.1`.
2. **Authentication/redaction:** every GET authenticates a single configured
   `X-Topos-Principal` from a loopback peer before routing. Metric values above
   the closed `Sensitivity` ceiling become server-side markers while keys and
   `metrics_meta` remain. The new configuration checks reject a non-mapping
   principal configuration and non-boolean opt-in instead of allowing Python
   truthiness across the bind boundary.
3. **Origin/CSRF:** no CORS, JSONP, or cookies exist; POST, PUT, PATCH, and
   DELETE are structurally `405` for every route.
4. **Read-only routing:** exactly four closed GET routes call one P63 typed
   method each. Query fields are closed, health remains gated off, and all P52
   codes have deterministic status mapping without exposing exception text.

The gateway remains outside `topos.daemon.__init__`; the new subprocess test
proves importing `topos.daemon` does not load the HTTP gateway.

## 3. Adversarial test observability

**FINDING: fixed.** The original error-map test listed representative codes
only. It now enumerates every closed P52 error code and asserts its HTTP status
mapping. Other numbered handoff oracles assert observable artifacts:

- socket address for the default bind;
- typed startup exceptions for unsafe binds;
- raw HTTP body absence for unauthenticated telemetry and a sensitive raw
  metric;
- typed redaction marker, retained metric key, and retained metadata;
- no principal from a non-loopback peer;
- HTTP `405` and `Allow: GET` across all mutation/route combinations;
- decoded route JSON, actual typed daemon errors, and a down-daemon response.

The real DaemonApi -> DaemonClient -> HTTP gateway fixture is used for route
and redaction tests. No test is mock-call bookkeeping.

## 4. Dates, paths, and artifacts

**FINDING: fixed.** Current date is 2026-07-13. LOG/REPORT paths resolve, the
focused count is now current, and the LOG validation section no longer says
"Pending implementation." The earlier implementation commit is intentionally
recorded as historical context; this self-review is a separate commit.

## 5. Hygiene

**FINDING: none after fixes.** LOG, REPORT, and this self-review are present;
the source and test additions are ASCII; no new heavy framework import,
dead route, legacy health fallback, or unused configuration path was found.

## Summary

Two real issues were fixed in this pass: runtime validation for trust-boundary
configuration and complete closed-code mapping coverage. No blocking contract
gap remains.
