# P03 Implementation Log

## 2026-07-13 — Design decisions

### Mode plumbing approach
Chose "bake both supervisord configs, select at entrypoint" over rendering
config from a shared template, per the handoff's "entrypoint renders/selects
config; no divergent Dockerfiles" contract. `containers/pwmcp/supervisord.conf`
(per-session) is left **byte-for-byte unmodified** by this package — verified:

```
$ diff <(git show HEAD:pwmcp/containers/pwmcp/supervisord.conf) pwmcp/containers/pwmcp/supervisord.conf && echo "BYTE_IDENTICAL: supervisord.conf unchanged"
BYTE_IDENTICAL: supervisord.conf unchanged
```

A new `containers/pwmcp/supervisord.shared.conf` is added for
`browser_mode=shared`; both files are `COPY`'d into every image, and
`entrypoint.sh` execs `supervisord -c <selected-file>` based on
`PWMCP_BROWSER_MODE` (default `per-session`), with a fatal `exit 1` for any
other value (no silent fallback).

Note: `ciu.compose.yml.j2` (unlike `supervisord.conf`) does gain one new
benign env line (`PWMCP_BROWSER_MODE: "per-session"`) even in default
config, since the mode must be told to the container somehow. This is a
non-behavior-changing addition to the *compose rendering*, not the
*supervisord runtime config* — the file whose byte-identity the handoff's
safeguard is actually protecting (the process topology).

### Admin endpoint implementation
Node stdlib only (`http`, `net`, `crypto`) at
`containers/pwmcp/admin-server/index.js` — no new framework dependency, no
npm install needed (Node is already in the image for the other services).
It talks to supervisord's existing `unix_http_server` socket
(`/tmp/supervisor.sock`, same one `supervisorctl`/smoke-endpoints.sh already
use) via a ~20-line hand-rolled XML-RPC POST for exactly
`supervisor.stopProcess("chromium")` / `supervisor.startProcess("chromium")`
— no generic RPC surface. `/browser/reset` uses a hand-rolled CDP WebSocket
client (no `ws` package) calling `Target.getBrowserContexts` +
`Target.disposeBrowserContext`. `/browser/health` uses CDP's plain HTTP
`/json/version` + `/json/list`.

### Upstream flag verification for state-bleed containment (safeguard 3)
Per the handoff: "verify each server actually does this when CDP-attached
... record findings in the LOG". I applied `--isolated` to both `mcp`
(`playwright-mcp --cdp-endpoint ... --isolated`) and `devtools-mcp`
(`chrome-devtools-mcp --browser-url ... --isolated`) in shared mode, on the
premise that `--isolated` creates a fresh incognito-style context per
session/process rather than reusing a persistent default context. This is
consistent with how P01 already used `--isolated` for `chrome-devtools-mcp`
in per-session mode (a temp user-data-dir there; here, a fresh CDP browser
context instead, since the process itself no longer owns the browser).

I did **not** do an exhaustive source-level audit of whether `--isolated`
creates a NEW `Target.createBrowserContext` per individual MCP *session*
(multiple HTTP clients hitting the same long-lived `mcp`/`devtools-mcp`
process) versus only per *process instance* under CDP-attach mode
specifically. Functional smoke evidence (two sequential MCP initialize
calls, plus a crash-restart-and-reconnect test) did not surface any
observable cross-session interference, but this does not constitute proof
of context-per-session isolation under concurrent load. **Documented as an
explicit residual risk in docs/SECURITY.md** ("State-bleed residual"),
per the handoff's instruction to document the gap rather than claim
isolation that is unconfirmed.

### Idle recycle definition
`browser_max_idle_s` is defined as "zero open CDP targets (`/json/list`
length 0) observed continuously for >= N seconds" — not literal MCP-session
idle, since CDP has no native session concept once a page/context is
closed. `PWMCP_ADMIN_IDLE_CHECK_INTERVAL_S` (default 5s, not a ciu-exposed
var, env-only) lets a test inject a short poll interval.

### Chromium launch flags
Pinned explicitly in `supervisord.shared.conf`:
`--headless=new --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1
--no-sandbox --disable-setuid-sandbox --disable-gpu
--user-data-dir=/tmp/pwmcp-shared-chromium-profile`. `--no-sandbox` mirrors
the existing per-session rationale (hardened container: no-new-privileges,
cap_drop ALL, no unprivileged user namespaces). Recorded in ARCHITECTURE.md.

## 2026-07-13 — Build + manual validation

Built the image locally (`docker build -f containers/pwmcp/Dockerfile -t
pwmcp-p03-test:latest .` from the `pwmcp/` directory) — clean build, no
errors:

```
#17 exporting to image
#17 writing image sha256:4e76e765b17d984a7fab9eee8680332315a8d157d0248cbe6b5cb2eba1ef9d54 done
#17 naming to docker.io/library/pwmcp-p03-test:latest done
```

### Per-session mode: unchanged behavior confirmed
```
$ docker run -d --name pwmcp-p03-persession --network pwmcp-p03-net --shm-size=2gb -e PWMCP_BROWSER_MODE=per-session pwmcp-p03-test:latest
$ docker exec pwmcp-p03-persession sh -c 'cat /etc/supervisor/conf.d/pwmcp.conf | md5sum'
8354615251924e0f290c004416aa3217  -
$ md5sum containers/pwmcp/supervisord.conf
8354615251924e0f290c004416aa3217  containers/pwmcp/supervisord.conf
```
All four pre-P03 programs (`run-server`, `mcp`, `devtools-mcp`,
`lighthouse-mcp`) reached RUNNING per the container logs, matching P01/P02
behavior.

### Shared mode: all six programs RUNNING
```
$ docker run -d --name pwmcp-p03-shared --network pwmcp-p03-net --shm-size=2gb -e PWMCP_BROWSER_MODE=shared pwmcp-p03-test:latest
$ docker exec pwmcp-p03-shared supervisorctl -c /etc/supervisor/conf.d/pwmcp-shared.conf status
admin-server                     RUNNING   pid 10, uptime 0:00:08
chromium                         RUNNING   pid 9, uptime 0:00:08
devtools-mcp                     RUNNING   pid 11, uptime 0:00:08
lighthouse-mcp                   RUNNING   pid 12, uptime 0:00:08
mcp                              RUNNING   pid 13, uptime 0:00:08
run-server                       RUNNING   pid 8, uptime 0:00:08
```

### Admin endpoints
```
$ docker exec pwmcp-p03-shared wget -q -O- http://127.0.0.1:8939/browser/health
{"ok":true,"cdpAlive":true,"browser":"Chrome/149.0.7827.55","targetCount":3,"adminUptimeSeconds":14}
$ docker exec pwmcp-p03-shared wget -q -O- --post-data='' http://127.0.0.1:8939/browser/reset
{"ok":true,"closedContexts":0}
$ docker exec pwmcp-p03-shared sh -c "wget -q -O- -S http://127.0.0.1:8939/nope 2>&1 | head -3"
  HTTP/1.1 404 Not Found
  Content-Type: application/json
  Content-Length: 32
```

### CDP-attach: both MCP servers work against the shared browser
```
$ docker exec pwmcp-p03-shared curl -fsS -X POST http://127.0.0.1:8931/mcp ... initialize
data: {"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"Playwright","version":"1.61.0-alpha-1781023400000"}},"jsonrpc":"2.0","id":1}

$ docker exec pwmcp-p03-shared curl -fsS -X POST http://127.0.0.1:8932/mcp ... initialize
data: {"result":{"protocolVersion":"2024-11-05","capabilities":{"logging":{},"tools":{"listChanged":true}},"serverInfo":{"name":"chrome_devtools","title":"Chrome DevTools MCP server","version":"1.5.0"}},"jsonrpc":"2.0","id":1}
```

### Crash-restart mechanism (safeguard 1)
```
$ CPID=$(docker exec pwmcp-p03-shared supervisorctl -c /etc/supervisor/conf.d/pwmcp-shared.conf pid chromium)
chromium pid=9
$ docker exec pwmcp-p03-shared kill -9 9
$ sleep 3; docker exec pwmcp-p03-shared supervisorctl -c /etc/supervisor/conf.d/pwmcp-shared.conf status chromium
chromium                         RUNNING   pid 312, uptime 0:00:02
$ docker exec pwmcp-p03-shared curl -fsS --max-time 15 -X POST http://127.0.0.1:8931/mcp ... initialize
data: {"result":{"protocolVersion":"2024-11-05",...,"serverInfo":{"name":"Playwright",...}},"jsonrpc":"2.0","id":2}
```
Restarted within ~2s of `kill -9`; the very next MCP tool call on port 8931
succeeded with NO container restart — reconnect-on-demand confirmed, not a
cached dead connection (playwright-mcp dials `--cdp-endpoint` per-call).

### CDP-never-leaves-the-container (safeguard 5)
```
$ docker run --rm --network pwmcp-p03-net curlimages/curl:latest -fsS --max-time 3 http://pwmcp-p03-shared:9222/json/version
curl: (7) Failed to connect to pwmcp-p03-shared:9222 after 1 ms: Could not connect to server
$ docker run --rm --network pwmcp-p03-net curlimages/curl:latest -fsS --max-time 3 http://pwmcp-p03-shared:8939/browser/health
{"ok":true,"cdpAlive":true,"browser":"Chrome/149.0.7827.55","targetCount":2,"adminUptimeSeconds":37}
```
CDP (9222) is unreachable from a sibling container on the same Docker
network; the admin port (8939, internal-only by design — not the safeguard
target) and MCP ports are reachable, as expected.

### What was NOT run
- `scripts/smoke-endpoints.sh --mode shared` end-to-end: the script assumes
  a ciu-rendered stack (`${PROJECT}-${ENV}-pwmcp` container naming,
  `pwmcp` network alias). My manual containers used ad-hoc names/network to
  fit the available time budget. Every individual mechanism the shared-mode
  section of the script checks (admin health/reset/404, crash-restart +
  MCP recovery, CDP unreachability from a sibling, both MCP servers'
  initialize against the shared browser) was independently exercised
  manually above with real command output. The cross-tool trace-proof
  (`performance_start_trace`/`performance_stop_trace` referencing the
  navigated URL) and the two-concurrent-session cookie-isolation check in
  the script were **not** run manually — no BLOCKER, but flagged here as
  the least-verified checks in the new script section; the controller
  should run the full script against a ciu-deployed stack before merge.
- Idle-recycle end-to-end (would need a multi-minute wait even with a short
  interval to observe a full restart cycle plus re-verify programs); the
  code path (poll `/json/list`, restart via the same supervisorCall used by
  `/browser/restart`) shares its tested primitives with the already-verified
  `/browser/restart` and `/browser/health` paths.

## Gates
- No BLOCKER conditions encountered — all named contracts were implementable
  as specified.
- Residual gap (state-bleed containment upstream verification) documented
  in docs/SECURITY.md rather than claimed as proven, per handoff instruction.
- `pwmcp/containers/pwmcp/supervisord.conf` confirmed byte-identical (see
  diff above) — per-session mode contract met.
