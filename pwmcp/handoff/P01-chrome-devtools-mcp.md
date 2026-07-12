# pwmcp P01 - chrome-devtools-mcp Sibling Server

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-max
> **Depends-on:** none
> **Base:** main
> **Session-hint:** fresh
> **Escalate-if:** a named contract cannot be met as specified; the stdio->HTTP proxy candidates both fail the pinned-version probe

## Goal

Extend the pwmcp unified container with a second MCP server: Google's
`chrome-devtools-mcp` (CDP-based performance tracing, DevTools insights,
CPU/network throttling emulation), served on its own port alongside the
existing `@playwright/mcp` (8931) and `playwright run-server` (3000). This
gives every network consumer (Claude Code, Codex, OpenCode, Reasonix,
VS Code) performance-profiling tools without installing Chrome/Node locally —
the same "no browser in the devcontainer" contract pwmcp already enforces.

## Why a sibling, not a shared browser

`@playwright/mcp` launches Chromium per MCP session (see
`containers/pwmcp/Dockerfile` ~L40 profile-dir comment); no persistent
browser or CDP remote-debugging port exists in the container today. v1
therefore runs `chrome-devtools-mcp` with its own Chromium launch (same
binary, via the existing `/etc/pwmcp-chromium-path.txt` mechanism) and its
own isolated profile dir. Sharing one live browser session between the two
MCP servers is explicitly out of scope (revisit only if a real "profile the
page Playwright just built" need appears).

## Workflow

- Branch: `feat/pwmcp-p01-chrome-devtools-mcp`
- Worktree: `git worktree add -b feat/pwmcp-p01-chrome-devtools-mcp
  .worktrees/-pwmcp-p01-chrome-devtools-mcp main` (repo-root `.worktrees/`,
  branch from local `main`).
- Touch only `pwmcp/**`; write `pwmcp/handoff/reports/P01-LOG.md` and
  `P01-REPORT.md` (log during, report after — observable actions, commands,
  evidence); commit the feature branch, do not merge.

## Context To Read First (bounded)

This handoff; `pwmcp/README.md`; `containers/pwmcp/{Dockerfile,
supervisord.conf, entrypoint.sh}`; `ciu.defaults.toml.j2`;
`ciu.compose.yml.j2`; `docker-bake.hcl`; `cmru.vars`; `bundle.toml`;
`docs/{USAGE,DEPLOYMENT,SECURITY,ARCHITECTURE}.md`. Ignore `ciu.toml.j2`'s
stale two-container schema — `ciu.compose.yml.j2` reading `pwmcp.unified.*`
is the live template.

## Required Contracts

### Transport (RESOLVED 2026-07-12 — controller verified upstream docs)

`chrome-devtools-mcp` v1.5.0 is **stdio-only**: no native HTTP/streamable
transport exists (no `--transport`/`--port`/`--http` flags). Therefore:
front it with a pinned stdio→streamable-HTTP MCP proxy npm package on 8932
(candidates: `supergateway`, `mcp-proxy` — evaluate, choose one, pin its
exact version in the Dockerfile like `@playwright/mcp@0.0.76` is pinned,
record choice + rationale in the LOG). A proxy that spawns one stdio child
per HTTP session is preferred — it maps cleanly onto chrome-devtools-mcp's
own-browser-per-instance model (use `--isolated` for auto-cleaned profiles).
The proxy must pass MCP protocol semantics through unchanged.
Confirmed upstream flags to build on: `--executablePath` (feed from
`/etc/pwmcp-chromium-path.txt`), `--headless`, `--isolated`, `--logFile`;
pin `chrome-devtools-mcp@1.5.x` exactly and note its Node "LTS" requirement
against the base image's Node version in the REPORT.
The consumer-facing contract is: `http://pwmcp:8932/mcp` (HTTP/streamable)
on the shared Docker network, internal mode, no auth — identical trust model
to 8931.

### Container integration

- Dockerfile: `npm install -g chrome-devtools-mcp@<exact-pin>` (and the proxy
  package if needed) in the same layer style as the existing pins. No
  browser download — it must use the image's Chromium via an executable-path
  flag fed from `/etc/pwmcp-chromium-path.txt` (same `%(ENV_PWMCP_...)s`
  pattern supervisord already uses for the mcp program). If the pinned
  version has no executable-path option, that is a blocker to report, not to
  work around with a second Chromium download.
- New `[program:devtools-mcp]` block in `supervisord.conf`: headless, no
  sandbox flags consistent with the existing `[program:mcp]` line, isolated
  profile under `/tmp` (the image already sets `HOME=/tmp`,
  `XDG_CACHE_HOME=/tmp/.cache`; do not write outside them — the container
  runs `USER 1000:1000`, `cap_drop: ALL`, `no-new-privileges`, and none of
  that hardening may be relaxed).
- Host-header protection: whatever serves HTTP on 8932 must accept
  `Host: pwmcp:8932` and `Host: <project>-<env>-pwmcp:8932` from sibling
  containers and reject others — mirror the existing
  `PWMCP_MCP_ALLOWED_HOSTS` mechanism (built in `ciu.compose.yml.j2` ~L34)
  with a parallel `PWMCP_DEVTOOLS_ALLOWED_HOSTS`. If the chosen
  server/proxy has no host allowlist, document that gap in SECURITY.md
  explicitly rather than silently shipping without it.

### ciu templating

- `ciu.defaults.toml.j2` `[pwmcp.unified]`: add `devtools_port = 8932`,
  `host_devtools_port`, and (if needed) a devtools extra-args var — same
  naming style as `ws_port`/`mcp_port`.
- `ciu.compose.yml.j2`: third port mapping under the existing
  `expose`-guarded block; extend the environment block for the new
  allowed-hosts var; external mode gets its own Traefik router (path or
  port distinct from the existing `-mcp` router) — copy the existing router
  block's shape.
- Existing consumers must be unaffected when they upgrade without setting
  the new vars: defaults must render a working config with the devtools
  server enabled on 8932 and nothing else changed. Ports 3000/8931 behavior
  must be byte-identical in rendered output except for the additive lines
  (verify by rendering before/after and diffing; include the diff in the
  REPORT).

### Version/release discipline

- Bump `PWMCP_VERSION_PYPI`/`PWMCP_VERSION_NPM` in `docker-bake.hcl` and
  `[pwmcp.unified.image].tag` in `ciu.defaults.toml.j2` (next `-rN`).
- **Templatize the `@playwright/mcp` pin while you are in these files**
  (folded in 2026-07-12; was a separate package candidate but touches the
  same Dockerfile/bake lines): promote the hardcoded `0.0.76` to a
  `PLAYWRIGHT_MCP_VERSION` Dockerfile ARG with a matching `docker-bake.hcl`
  arg and `cmru.vars` entry, defaulting to the current pin — same discipline
  `PLAYWRIGHT_VERSION` already follows. Pin your new packages
  (`chrome-devtools-mcp`, the proxy) the same templated way from the start.
  Rendered/built output with defaults must be behavior-identical to before.
- Add the chrome-devtools-mcp pin to wherever the README documents the
  `@playwright/mcp` pin, and note the Chrome-major ↔ chrome-devtools-mcp
  compatibility expectation.
- Do NOT push images or publish bundles from this package — build locally
  (`docker buildx bake all --load`), validate, and leave push/publish to the
  controller after review.

## Required Validation (mechanism-level, in-container)

No smoke harness exists yet — add one: `scripts/smoke-endpoints.sh` that,
against a locally built + `ciu`-started stack, performs for EACH of 8931 and
8932: (a) an MCP `initialize` POST with a correct `Host` header asserting a
successful JSON-RPC result naming the server, and (b) the same POST with a
forged `Host: evil.example:8932` asserting rejection (non-2xx). For 8932
additionally call one real tool end-to-end (e.g. start+stop a performance
trace against a `data:` URL page, or the pinned server's cheapest
tool) and assert a non-error MCP tool result — proving the server actually
drove Chromium, not just answered initialize. Capture the script's full
output in the REPORT. Also assert supervisord reports all three programs
RUNNING after 30 s and that killing the devtools program has no effect on
8931/3000 (fault isolation between siblings).

Wrap any long-running validation in `timeout`; a hung check is a finding to
record, never a pass. Do not claim any validation that was not actually run;
record environment limitations (e.g. no Docker access in the agent
environment) separately from implementation failures — if the agent cannot
run Docker, say so in the REPORT and leave the smoke run to the controller,
with the script committed and ready.

## Documentation

Update: `README.md` (endpoint table, pins), `docs/USAGE.md` (endpoint table
~L27, allowed-hosts ~L104, Multiple Consumers ~L175 — add a Claude Code
`.mcp.json` snippet showing both servers), `docs/DEPLOYMENT.md` (~L21-22,
L43), `docs/SECURITY.md` (new server's trust posture, host-allowlist status),
`docs/ARCHITECTURE.md` (three-program diagram ~L19-27, pin list ~L39,
chromium-path ~L49, allowed-hosts ~L57). Consumer repos (e.g. dstdns
`/.mcp.json` adding `http://pwmcp:8932/mcp`) are out of scope here — note the
snippet in USAGE.md instead.

## Patch Discipline

Additive edits only in supervisord/templates/docs; do not restructure the
existing programs, rename existing vars, or reformat whole files. If a
restructuring genuinely reads better, propose it in the REPORT.

## Out Of Scope

- Sharing one browser/session between `@playwright/mcp` and
  `chrome-devtools-mcp` (no persistent CDP browser exists; a future package
  may add one if a real need appears).
- Exposing a raw CDP remote-debugging port on the network.
- Image push, bundle publish, consumer-repo changes, external-mode TLS/auth
  changes beyond the added Traefik router block.
- Any change to the Playwright version pin or the existing two programs'
  behavior.
