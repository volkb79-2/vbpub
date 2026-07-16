# P67 REPORT — Versioned Read HTTP Gateway (trust-boundary hardened)

## What was built

- `topos.daemon.http_gateway`: a stdlib `ThreadingHTTPServer` gateway backed
  exclusively by P63's typed `DaemonClient.request_hello`, `request_current`,
  `request_history`, and `request_entity` methods.
- `topos gateway serve`: a separately-run operator entry point with a
  loopback-default bind, explicit `--allow-non-loopback`, and required
  `--principal NAME:CEILING` trusted-proxy authorization mapping.
- Trusted-local-reverse-proxy authentication: `X-Topos-Principal` is accepted
  exactly once and only from a loopback TCP peer. An unauthenticated or remote
  forwarded identity receives `401` and no telemetry.
- Server-side closed-enum redaction. Values above the principal's `public`,
  `operational`, or `sensitive` ceiling become
  `{"redacted":true,"sensitivity":"..."}` before JSON serialization;
  metric keys and `metrics_meta` stay present.
- Closed same-origin GET routes: `/v1/hello`, `/v1/current`,
  `/v1/history`, and `/v1/entity`. Unknown/duplicate query fields are refused,
  no CORS or JSONP is emitted, and every mutating method is `405 Allow: GET`.
- Deterministic safe error mapping with no exception text, socket paths, or
  stack traces in HTTP bodies.
- `docs/DAEMON.md` deployment guidance and a P67 real-stack adversarial test
  module (DaemonApi -> real DaemonClient -> ephemeral HTTP gateway).

## Deviations from handoff

None.

`health` is intentionally gated off: P66's typed versioned health client is
not on this base, and P67 forbids a legacy `request_health` fallback.

## Test evidence

Focused gate, package virtualenv (`/workspaces/vbpub/.venv`, Linux, Python
from that venv):

```bash
PYTHONPATH=topos/src /workspaces/vbpub/.venv/bin/python -m pytest \
  topos/tests/test_daemon_http_gateway.py -q -W error -p no:schemathesis
# 47 passed in 23.86s
```

Required full gate, clean package test virtualenv
(`/tmp/p43-clean-venv`, Linux; `textual` installed, optional `zstandard`
absent as required by the no-extra test):

```bash
timeout 900 env PYTHONPATH=topos/src /tmp/p43-clean-venv/bin/python -m pytest \
  topos/tests -q -W error -p no:schemathesis
# passed (exit 0; pytest lastfailed cache empty)
```

The workspace `.venv` has optional `zstandard` installed. Its full run reached
an existing test whose contract specifically assumes the extra is absent:
`test_zst_without_zstandard_exits_2` returns 1 for malformed compressed input
instead of that test's expected no-extra exit 2. This predates P67 and is not
caused by the gateway; the clean package gate above is the applicable green
environment. The isolated record/UI-style flaky failure observed in an earlier
concurrent clean run passed on its isolated rerun, and the final clean full run
completed with no `lastfailed` entries.

```bash
python3 -m py_compile topos/src/topos/daemon/http_gateway.py \
  topos/src/topos/cli.py topos/tests/test_daemon_http_gateway.py
# clean

git diff --check
# clean
```

## Adversarial coverage

1. Default listener binding is asserted loopback.
2. Non-loopback startup raises `GatewayStartupError` without the explicit
   opt-in and again without auth configuration; CLI flag wiring is covered.
3. Unauthenticated `current` has a `401` body with no telemetry bytes.
4. An operational principal's sensitive `pids_max` raw metric is absent while
   its typed marker, key, and `metrics_meta` remain.
5. A non-loopback peer cannot turn a forwarded identity header into a
   principal.
6. POST, PUT, PATCH, and DELETE are rejected across every documented route.
7. All routes return decoded JSON with intact `metrics_meta`; every closed P52
   error code has a deterministic status mapping, and representative typed
   `not_found`/invalid/out-of-range failures plus a down daemon map safely.

## Proposed contract changes

None.

## Known gaps / open items

- P66 must land before adding the versioned `health` route.
- The reverse proxy remains responsible for browser authentication, TLS, and
  overwriting untrusted identity headers on the documented private hop.

## Files changed

```text
topos/src/topos/daemon/http_gateway.py       hardened stdlib gateway
topos/src/topos/cli.py                       gateway serve command and explicit opt-in
topos/tests/test_daemon_http_gateway.py      real-stack adversarial coverage
topos/docs/DAEMON.md                         deployment and HTTP contract
topos/README.md                              P67 status
topos/handoff/reports/P67-LOG.md             resumability log
topos/handoff/reports/P67-REPORT.md          this report
```
