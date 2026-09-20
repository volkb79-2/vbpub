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

The Chrome DevTools MCP 1.9.0 release also changes security-relevant CLI
defaults, including enabling unrestricted paths by default and adding a switch
to disable JavaScript evaluation. PWMCP currently exposes the server through a
proxy and does not declare an explicit path policy for that service, so a pin
bump must review those defaults against `docs/SECURITY.md`, rather than being
treated as a dependency-only update.

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

### 3. Transport policy is now modern Streamable HTTP only

PWMCP documentation previously called the port 8931 service “HTTP/SSE” and
advertised `/sse`, while the runtime smoke script already exercised the
streamable-HTTP `/mcp` route. The operator selected `/mcp` as the only
supported contract. The two mcp-proxy services now pass `--server stream`; the
old commands remain commented with a deprecation note, and the smoke lane
checks that each legacy `/sse` route is unavailable.

The coverage work will test the resolver and Python behavior, while the
modernization gate will verify the transport choice against the selected image
and package versions.

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
success and refusal behavior, and the implementation now refuses these
ambiguous inputs instead of silently proceeding.

The vendored Lighthouse MCP package has a related reproducibility issue:
`@modelcontextprotocol/sdk` and `chrome-launcher` use caret ranges and the
Dockerfile runs `npm install --production` without a committed lockfile. A
modernization pass should either commit and install the lockfile or make the
resolved versions explicit, then include the resulting MCP protocol behavior
in the container acceptance lane.

## Revised modernization plan

The 14-day rule is not a PWMCP-local policy. CMRU already owns the decided
FEAT-03 contract for central version selection in
`cmru/KNOWN_ISSUES_TODO_BACKLOG.md`: a repository-wide age window, language
resolvers, explicit pins and holds with reasons, resolved state, and explicit
refresh/check operations. PWMCP will consume that mechanism.

CMRU should perform generic version selection and alignment. A project declares
version coordinates rather than embedding package-specific selection logic in a
`steps.prepare` command. A coordinate has an ecosystem/source, package or image
name, version constraint, and an optional alignment group. An alignment group
expresses facts such as “the npm package, PyPI package, and Playwright image tag
must use one common version.” CMRU resolves the newest stable candidate whose
publication timestamp clears the central age window, then projects the resolved
state into the declared native files. CMRU remains generic because the package
names, sources, groups, and output mappings are configuration data.

PWMCP therefore keeps a project-specific compatibility check, but its resolver
does not independently decide which versions are current. The Playwright
coordinate remains one alignment group spanning npm, PyPI, and the Microsoft
image. `@playwright/mcp`, `chrome-devtools-mcp`, `mcp-proxy`, Lighthouse, and
the Lighthouse MCP SDK dependencies are separate coordinates unless a declared
compatibility relation couples them.

Transitive dependencies may also be declared as managed coordinates when the
project deliberately owns them. For npm this means the project declares the
package explicitly or through `overrides`, and commits the resulting
`package-lock.json`; the image build then uses `npm ci --omit=dev`. Unmanaged
transitives remain governed by the lockfile but are not independently selected
by CMRU.

The runtime transport policy is now Streamable HTTP at `/mcp` only. The
`mcp-proxy` commands use `--server stream`, which disables its legacy `/sse`
endpoint. The previous commands remain commented in the supervisor files with
a deprecated note. The old active `/sse` documentation line is retained only
as a disabled documentation comment. The container acceptance lane must assert
that `/mcp` works and `/sse` is unavailable for every exposed MCP service.

External authentication remains the existing TLS plus Traefik BasicAuth
configuration. Bearer-token authentication is a separate future feature owned
by `tls-edge`: its current documentation describes a `forwardAuth` verifier
pattern, but no such middleware is shipped yet. PWMCP will document the
dependency without embedding a second authentication mechanism.

The implementation sequence is:

1. carve and implement CMRU FEAT-03 as a generic coordinate/source resolver;
2. add PWMCP coordinate declarations and Playwright alignment metadata;
3. move PWMCP’s generated version writes under CMRU’s resolved-state projection;
4. add the Lighthouse lockfile and managed direct SDK coordinates;
5. keep the PWMCP compatibility validator and live endpoint acceptance lane;
6. run the container build and smoke lane through `tester-unified`.

## Remaining decisions

The product decisions are now settled: central default policy, explicit
project/image overrides, stable releases only, explicit pins/holds, synchronized
Playwright sources, `/mcp` as the only supported transport, and BasicAuth for
external access. The remaining design work is limited to the generic CMRU
schema: the exact coordinate/output mapping shape, whether a per-image policy
override means a stricter age window or only an explicit pin/hold, and how a
managed transitive npm coordinate is represented (`dependencies` versus
`overrides`).

The existing CMRU FEAT-03 backlog is the version-policy backlog. PWMCP has no
separate backlog file; the bearer-token feature belongs in the existing
`tls-edge/KNOWN_ISSUES.md` roadmap when it is carved, with PWMCP retaining only
the consumer-side security note.

## Previous review decision

The coverage work remains separate from the upstream modernization. Coverage
tests and resolver fail-closed hardening are already committed in this
worktree. Upstream compatibility still requires a fresh image build and live
endpoint acceptance after CMRU-generated pins are introduced.
