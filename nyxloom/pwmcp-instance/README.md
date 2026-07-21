# nyxloom pwmcp-instance — nyxloom's own browser/UI-testing service

**pwmcp** (Playwright-as-a-Service) driven from `ghcr.io/volkb79-2/pwmcp`, owned by
**nyxloom** and scoped to **nyxloom's own network**. This is how we browser-test the
nyxloom dashboard without nyxloom reaching into dstdns's shared pwmcp (or vice-versa).

## Why nyxloom owns its own instance
The upstream `vbpub/pwmcp` is the **image source** (build/publish tooling → the ghcr
image) — not a runnable per-project service. Each consumer runs the published image on
**its own** network. The nyxloom dashboard lives at `nyxloomd:8942` on
`nyxloom-prod-nyxloomd-net`, so this container attaches to exactly that network:

| leg | path |
|---|---|
| browser (inside pwmcp) → dashboard | `http://nyxloomd:8942/` |
| devcontainer → MCP | `http://pwmcp:8931/mcp` (via `nyxloom/.mcp.json`) |
| test suites → Playwright run-server | `ws://pwmcp:3000/` |

The devcontainer joins **only** `nyxloom-prod-nyxloomd-net` to reach it — the agreed
security boundary. No cross-project network bridging.

## Bring up / tear down
```bash
docker compose -f nyxloom/pwmcp-instance/docker-compose.yml up -d
docker compose -f nyxloom/pwmcp-instance/docker-compose.yml down
```
The network `nyxloom-prod-nyxloomd-net` must already exist (the `nyxloomd` stack owns
it). Container name is `nyxloom-prod-pwmcp` — clearly owned, unlike the old ambiguous
`pwmcp-local-pwmcp`.

## Claude Code usage
Launch Claude Code from the nyxloom root (`/workspaces/vbpub/nyxloom`) so it picks up
`nyxloom/.mcp.json` (registers the `playwright` MCP at `http://pwmcp:8931/mcp`). Then
drive the Playwright browser tools against `http://nyxloomd:8942/www/*.html`. See
`nyxloom-dashboard-ui-testing` in the operator's memory for the full recipe (incl. the
isolated serve-only instance for testing pause/resume writes as a no-op on live).

## Not (yet) a ciu-managed sub-stack
This ships as a plain `docker-compose.yml` (like the pre-rendered copies the `ntfy` /
`nyxloomd` stacks carry) rather than a `ciu.compose.yml.j2` registered in the nyxloom
ciu root. That's deliberate — it keeps bring-up a one-liner and avoids coupling this
optional test dependency to the live `ciu up` of the daemon. Promoting it to a
registered ciu sub-stack (add `ciu.compose.yml.j2` + `ciu.toml` + list it in
`nyxloom/ciu.global.toml` `[deploy.profiles.default].stacks`) is a small follow-up if
we want `ciu up` to manage it alongside `ntfy`/`nyxloomd`.
