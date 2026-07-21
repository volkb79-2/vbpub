# nyxloom pwmcp-instance — nyxloom's own browser/UI-testing service

**pwmcp** (Playwright-as-a-Service) from `ghcr.io/volkb79-2/pwmcp`, owned by **nyxloom**
and scoped to **nyxloom's own network**. This is how we browser-test the nyxloom
dashboard without nyxloom reaching into dstdns's shared pwmcp (or vice-versa). It is a
**proper ciu-managed sub-stack** of the nyxloom ciu root — not a hand-written compose.

## Why nyxloom owns its own instance
The upstream `vbpub/pwmcp` is the **image source** (build/publish tooling → the ghcr
image), not a runnable per-project service. Each consumer runs the published image on
**its own** network. The nyxloom dashboard lives at `nyxloomd:8942` on
`nyxloom-prod-nyxloomd-net` (owned by the `nyxloomd` stack), so this container attaches
to exactly that network:

| leg | path |
|---|---|
| browser (inside pwmcp) → dashboard | `http://nyxloomd:8942/` |
| devcontainer → MCP | `http://pwmcp:8931/mcp` (via `nyxloom/.mcp.json`) |
| test suites → Playwright run-server | `ws://pwmcp:3000/` |

The devcontainer joins **only** `nyxloom-prod-nyxloomd-net` to reach it — the agreed
security boundary. No cross-project network bridging.

## A ciu sub-stack in the non-default `tools` profile
This ships as `ciu.compose.yml.j2` + `ciu.defaults.toml.j2` (the rendered `ciu.compose.yml`
/ `ciu.toml` are gitignored) and is registered in the nyxloom ciu root
(`ciu.global.defaults.toml.j2`) under `[deploy.profiles.tools]` — **not** the default
profile. So it inherits nyxloom's project/env/network/naming from the root global
(container `nyxloom-prod-pwmcp`, ciu-scoped compose project `nyxloom-prod-pwmcp-instance`) yet a plain `ciu up` /
`ciu down` of the daemon **never touches it** — the same "shared tool, opt-in" property
a skywalking instance would have.

```bash
# from the nyxloom ciu root (/workspaces/vbpub/nyxloom):
ciu up   --dir pwmcp-instance      # bring this stack up (or: ciu up --profile tools)
ciu down --profile tools           # tear it down (down takes --profile, not --dir)
ciu up   --dir pwmcp-instance --dry-run   # render ciu.compose.yml without deploying
```
The network `nyxloom-prod-nyxloomd-net` must already exist (the `nyxloomd` stack owns
it — it comes up with the default profile). Container name is `nyxloom-prod-pwmcp` —
clearly owned, unlike the old ambiguous `pwmcp-local-pwmcp`.

> Do **not** run a bare `docker compose up` against a rendered file here: ciu scopes the
> compose project to `deploy.project_name` (`nyxloom`), not the directory basename, which
> is what keeps a stray `pwmcp` compose project from colliding with dstdns's shared one.

## Claude Code usage
Launch Claude Code from the nyxloom root (`/workspaces/vbpub/nyxloom`) so it picks up
`nyxloom/.mcp.json` (registers the `playwright` MCP at `http://pwmcp:8931/mcp`). Then
drive the Playwright browser tools against `http://nyxloomd:8942/www/*.html`. See
`nyxloom-dashboard-ui-testing` in the operator's memory for the full recipe (incl. the
isolated serve-only instance for testing pause/resume writes as a no-op on live).
