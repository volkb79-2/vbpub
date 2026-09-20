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

## Schema proposal

The alignment relationship should be structural rather than a repeated field.
The preferred CMRU shape is a named target table with source subtables:

```toml
[versions.targets."pwmcp.playwright"]
mode = "aligned"
constraint = ">=1.60,<2"

[versions.targets."pwmcp.playwright".sources.npm]
name = "playwright"
registry = "https://registry.npmjs.org"

[versions.targets."pwmcp.playwright".sources.pypi]
name = "playwright"
registry = "https://pypi.org"

[versions.targets."pwmcp.playwright".sources.oci]
image = "mcr.microsoft.com/playwright"
tag = "v{version}-{image_distro}"

[versions.targets."pwmcp.chrome-devtools-mcp"]
mode = "single"
constraint = ">=1.8,<2"

[versions.targets."pwmcp.chrome-devtools-mcp".sources.npm]
name = "chrome-devtools-mcp"
registry = "https://registry.npmjs.org"
```

The table path `pwmcp.playwright` is the alignment identity. CMRU does not
need a separate `alignment_group` value that can be misspelled or accidentally
left different on one source. `mode = "aligned"` means one version must be
available and old enough at every declared source; `mode = "single"` resolves
one source independently. The same structure handles Python, npm, Go, OCI,
and future ecosystems without hard-coding PWMCP names into CMRU.

The alternatives were considered as follows:

| Shape | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| Flat coordinates plus `alignment_group` | Small parser and easy programmatic input | Repeated group names, typo risk, weak human visibility | Reject |
| One table per target with source subtables | Relationship is structural, readable, supports aligned and single-source targets | Requires a small target/source schema | **Recommend** |
| One table per ecosystem only | Fits the original FEAT-03 sketch | Cross-ecosystem alignment becomes an extra side table or field | Reject for aligned targets |
| Consumer-owned resolver scripts | Minimal CMRU implementation | Every project reimplements age filtering, provenance, and alignment | Reject |

## Consumer-facing generation model

CMRU should minimize project-specific code. `cmru versions init` should offer
project-type templates and inspect known manifests where a fact can be derived:

- Python: read `pyproject.toml` or `requirements.in`, resolve through the
  Python engine, and create the committed dated constraints artifact plus the
  stable `constraints.txt` pointer described by FEAT-03;
- npm: read `package.json`, generate or update the lockfile, and use exact
  managed versions or native `overrides` where requested;
- Go: read `go.mod` and write the native module results;
- OCI/template consumers: render declared target values into supported TOML,
  JSON, HCL, and Jinja-backed output templates.

A Python project should not need to write its own resolver. It should be able
to run `cmru versions init`, review the generated inputs, and then use
`cmru versions refresh`. CMRU creates the version files the project consumes;
the project keeps its semantic dependency declarations and gates consume the
committed generated artifact.

For PWMCP, CMRU would generate the Playwright npm/PyPI/image values and the
independent MCP package values into the declared templates. PWMCP’s remaining
custom code would validate browser/protocol compatibility and live endpoints.

## Remaining decisions

The product decisions are settled: central default policy, explicit
project/image overrides, stable releases only, explicit pins/holds, synchronized
Playwright sources, `/mcp` as the only supported transport, and BasicAuth for
external access. The implementation questions are now bounded to:

1. whether the final CMRU spelling is `versions.targets` or another equivalent
   named-target table;
2. whether a per-image override may only make the age window stricter, with a
   newer version requiring an explicit pin/hold;
3. the exact native output templates for Python constraints, npm lockfiles, and
   Jinja/HCL projections.

For point 2, the recommended behavior is:

```toml
# Repository default
[versions]
age_window_days = 14

# A target may be stricter
[versions.targets."pwmcp.playwright".policy]
age_window_days = 21

# A known urgent fix may bypass the window, but must explain and expire
[versions.targets."pwmcp.playwright".pin]
version = "1.63.0"
reason = "Security fix required before the normal vetting window"
expires = "2026-10-01"
```

A target setting `age_window_days = 7` would be refused because it weakens the
repository policy. A temporary hold would keep an eligible version out of the
selection with the same required reason and expiry fields.

The version-policy backlog remains CMRU FEAT-03. The bearer-token feature is
owned by [tls-edge/KNOWN_ISSUES_TODO_BACKLOG.md](../../../tls-edge/KNOWN_ISSUES_TODO_BACKLOG.md)
FEAT-01; PWMCP only documents its future use.

## Previous review decision

The coverage work remains separate from the upstream modernization. Coverage
tests and resolver fail-closed hardening are already committed in this
worktree. Upstream compatibility still requires a fresh image build and live
endpoint acceptance after CMRU-generated pins are introduced.
