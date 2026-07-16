# P67 - Frontier review (pass #2)

Reviewer: Opus 4.8, fresh session, wave P67/P75/P76 · 2026-07-13
Verdict: **MERGED** (182d862), one review-fix.

## Adversarial check: are the trust-boundary gates real, or inert like P72's?

P72's review this same day found three "passing" safety gates that were inert in
production because the real caller never invoked them. I checked P67's auth, TLS
posture, and bind checks the same way -- by tracing the production entrypoint,
not the tests.

**They are real.** `topos gateway serve` (`cli.py:_main_gateway`) constructs a
`GatewayConfig`, and `VersionedReadHttpGateway.__post_init__` runs
`_validate_startup` *before* the socket is created. `--principal` is argparse
`required=True`, so no unauthenticated listener can be started from the CLI at
all. The peer identity comes from `request.client_address[0]` -- the kernel's
`accept()` result -- not from a spoofable header. `DaemonClient` opens a fresh
socket per request and holds no mutable state, so the `ThreadingHTTPServer`
sharing one client is sound (I checked this specifically: a shared, stateful
socket would have interleaved responses across principals).

All four contract groups verified against the production path:

| Group | Verdict | How |
| --- | --- | --- |
| 1. Safe bind by default | met | Default `127.0.0.1`; non-loopback refused unless opt-in **and** auth config; refusal is a typed `GatewayStartupError` raised in the constructor, exit 2 from the CLI. |
| 2. Auth + redaction ceiling | met | 401 before any client dispatch; redaction runs server-side before `json.dumps`. Verified **live** against a real `DaemonApi` -> `DaemonClient` -> gateway stack on all three telemetry routes. |
| 3. Origin / CSRF | met | No CORS headers at all; `OPTIONS` -> 405 so preflight fails; every mutating verb -> 405 with `Allow: GET`. Header auth, no cookies. |
| 4. Read-only routing | met | Each route maps to exactly one typed `client.request_*`; closed query allowlists; error codes from a closed set; no socket paths or tracebacks in bodies (asserted). |

## Findings

### 1. Redaction was oracled on only one of three routes (should-fix, FIXED)

`flagged-by-pass-1: no`

`_redact_frame` walks a shape (`host` map + `entities` map) that `_redact_metrics`
never sees, but only `/v1/entity` had a redaction oracle. `/v1/current` and
`/v1/history` -- the two routes carrying the most telemetry -- had no test
asserting a sensitive value was actually redacted.

The behaviour is **correct today**; I verified it live before writing anything.
The defect is the oracle, and it is the P72 shape exactly: I disarmed
`_redact_frame` and **all 47 existing tests stayed green**. A shape drift in
`frame_to_jsonable` (entities becoming a list, say) would silently disarm the
redaction ceiling on both routes with the suite still passing.

Fixed in `2e9c3da`: a parametrized oracle over both frame routes that greps the
response bytes for the raw value and requires a typed marker in its place. Both
new tests fail against a disarmed walker.

### 2. `findings[]` is an unredacted channel (nit -> carved as P81)

`flagged-by-pass-1: no`

The gateway redacts `metrics` but passes `findings[]` through untouched, and a
`Finding` carries a free-text `message`. A rule that interpolates a `sensitive`
metric value into its message would ship that value past the ceiling. I checked:
no current rule in `diag/` does. That is luck and a small rule set, not a
boundary. Carved as **P81**.

### 3. Two redaction dialects (nit -> carved as P81)

`flagged-by-pass-1: no`

The gateway emits `{"redacted": true, "sensitivity": ...}`; the already-merged MCP
frontend emits the bare string `"__redacted__"` (`mcp/server.py:52`). Same closed
enum, two wire shapes; P73's UI would have to handle both. Carved as **P81**.

### 4. `--allow-non-loopback` with a specific LAN IP is silently useless (nit)

`flagged-by-pass-1: no`

Forwarded identities are trusted only from loopback peers, so binding a *specific*
non-loopback address yields a listener that 401s everything. It fails **closed**,
which is the right direction, so this is documentation, not a defect. (`0.0.0.0`
does work, since it includes the loopback the local proxy connects to.)

### 5. `do_HEAD = do_POST` returns 405 with a body (nit)

`flagged-by-pass-1: no`. HEAD responses must not carry a body. Harmless; safe.

## Pass-1 overlap

**0 of 5** (0%). The self-review (ebc0940) did real work -- it fixed genuine
auth/bind runtime validation gaps and expanded the closed error-code coverage
before I saw the diff -- but it found none of the five findings above. Consistent
with the standing §6 result: pass #1 catches mechanical misses, not the
hollow-oracle class.

## Gates (re-run from `main`, package venv `/workspaces/vbpub/.venv`)

- Focused: 49 passed (47 original + 2 review-fix).
- Full suite from `main` after merge: **1254 passed, 1 failed**.
- The single failure is `test_report.py::TestReportCLI::test_zst_without_zstandard_exits_2`,
  which **also fails on unmodified `main`** (verified before any merge). Not a P67
  regression. Carved as **P82**.
- ASCII clean in all P67-touched source; `git diff --check` clean.

## Merge

Conflict in `cli.py` (P67's branch predates the MCP merge): both branches added a
parser and a dispatch arm at the same location. Resolved by keeping both surfaces
-- `topos mcp` and `topos gateway` are independent commands. Verified both are
present and the module compiles.
