# PWMCP coverage and upstream compatibility review

Date: 2026-09-20  
Reviewed commit: `a4bba60d`  
Reviewer: Codex

## Scope

This review precedes the coverage expansion in the dedicated
`test/pwmcp-coverage-20260920` worktree. It covers the Python release helper,
the installable client, the Playwright version resolver, and the runtime
contracts that the tests need to witness. It also checks whether the pinned
Playwright and MCP components still describe the current upstream interfaces.

## Findings

### 1. The Playwright coordinate is currently split across sources

`docker-bake.hcl`, `ciu.defaults.toml.j2`, and `pwmcp.contract.json` carry
Playwright `1.63.0`, while the Dockerfile default, its image digest comment,
the README, deployment examples, and usage documentation still describe
`1.61.0`. The resolver updates the bake file, templates, and contract, but it
does not update the Dockerfile defaults or the human-facing documentation.

This is a release correctness issue, independent of test coverage. A direct
Dockerfile build can therefore use a different base image from the generated
release coordinate. The coverage work will add regression checks around the
resolver and release inputs; the pin cleanup should be a separately reviewed
compatibility change because it changes the image and consumer contract.

### 2. The MCP pins are behind current upstream releases

The checked-in pins are:

| Component | PWMCP pin | Current upstream observed 2026-09-20 | Review consequence |
| --- | ---: | ---: | --- |
| Playwright npm/PyPI coordinate | 1.63.0 in generated inputs; stale 1.61.0 fallback/docs remain | 1.63.0 | Keep the common-version resolver, but eliminate split defaults. |
| `@playwright/mcp` | 0.0.76 | 0.0.82 | Review CLI flags, HTTP transport, browser revision, and host checks before bumping. |
| `chrome-devtools-mcp` | 1.5.0 | 1.9.0 | Review `--browser-url`, executable-path, and Chrome argument behavior before bumping. |
| `mcp-proxy` | 6.5.2 | 6.7.18 | Review proxy flags and transport behavior before bumping. |

The current Playwright release notes and PyPI page identify 1.63.0 as the
current Python release at this review date. The official MCP package pages
identify `@playwright/mcp` 0.0.82 and `mcp-proxy` 6.7.18, while the Chrome
DevTools MCP release page identifies 1.9.0 as latest.

Sources:

- [Playwright Python release notes](https://playwright.dev/python/docs/release-notes)
- [Playwright 1.63.0 on PyPI](https://pypi.org/project/playwright/1.63.0/)
- [Playwright MCP installation](https://playwright.dev/mcp/installation)
- [`@playwright/mcp` on npm](https://www.npmjs.com/package/%40playwright/mcp?activeTab=versions)
- [`mcp-proxy` on npm](https://www.npmjs.com/package/mcp-proxy)
- [Chrome DevTools MCP releases](https://github.com/ChromeDevTools/chrome-devtools-mcp/releases)

The observed versions are not, by themselves, proof that the newer packages
work with PWMCP's supervisor commands. Do not upgrade all four pins as a side
effect of raising Python coverage. The compatibility change needs a build and
live endpoint acceptance run.

### 3. Transport documentation needs an explicit modern policy

PWMCP documentation calls the port 8931 service “HTTP/SSE” and describes
`/sse`, while the runtime smoke script already exercises the streamable HTTP
`/mcp` route. Current MCP proxy documentation describes streamable HTTP as the
modern transport and SSE as legacy for the current protocol line; some MCP
features also do not cross the proxy boundary.

The follow-up compatibility change should choose and document one of these
policies:

1. make streamable HTTP at `/mcp` the supported contract and retain `/sse`
   only as a tested compatibility route; or
2. remove the legacy route and update consumers and deployment docs together.

Until that decision is tested against the selected package versions, the
existing routes remain part of the acceptance surface. The coverage work will
test the resolver and Python behavior, and will not claim that an upstream
pin bump is safe merely because unit tests pass.

### 4. Coverage is weak in behaviorally important boundaries

The previous PWMCP R1 run measured 198 of 425 statements and 12 of 64
branches. The missing behavior is concentrated in:

- builder discovery, limit mismatch recreation, and build/push command
  construction in `build-push.py`;
- local and HTTP contract loading plus malformed payloads;
- CLI command dispatch and error/help output;
- installed Playwright absence and major/minor mismatch;
- TCP preflight, lease header construction, connection failure cleanup, and
  context-manager lifecycle in `session.py`;
- retry, malformed upstream payload, tag selection, release-number, file
  rewrite, and `main()` paths in the resolver.

These are real release and runtime decisions, so the coverage expansion will
add behavioral tests for both the successful and refusing directions. The
Assay whole-target contract remains in force; narrowing the target list would
hide gaps rather than fix them.

### 5. The resolver has two fail-open hardening candidates

The text rewrites in `update_toml_j2()` and `update_bake_hcl()` do not verify
that the expected keys were found. A template rename can therefore produce a
successful-looking prepare step with stale release inputs. Similarly,
`read_current_distro()` returns `noble` when the template does not contain
`image_distro`, even though the template is the authoritative source for the
selected MCR tag family.

These should become explicit refusal paths in the compatibility hardening
change, with tests that construct a missing-key template and assert that the
resolver names the missing input. The coverage tests exercise the current
success and fallback behavior so the present implementation remains measured;
the follow-up can then change the contract deliberately rather than silently.

The vendored Lighthouse MCP package has a related reproducibility issue:
`@modelcontextprotocol/sdk` and `chrome-launcher` use caret ranges and the
Dockerfile runs `npm install --production` without a committed lockfile. A
modernization pass should either commit and install the lockfile or make the
resolved versions explicit, then include the resulting MCP protocol behavior
in the container acceptance lane.

## Review decision

Proceed with coverage improvements in this worktree. Keep upstream package
bumps out of the first coverage commit unless a compatibility test requires a
small source change. After R1 is green, prepare a separate modernization
commit with:

1. one authoritative Playwright version coordinate and generated consistency
   checks;
2. explicit runtime compatibility checks for the selected MCP CLI flags and
   browser revision;
3. streamable HTTP and legacy SSE acceptance coverage, if both are retained;
4. a fresh image build and endpoint smoke run through the PWMCP container
   lane;
5. updated README, architecture, deployment, usage, and consumer examples.

This separation makes a failed upstream compatibility experiment diagnosable
without confusing it with ordinary Python branch coverage.
