# PWMCP coverage and upstream compatibility review

Date: 2026-09-20; implementation update: 2026-09-21
Reviewed baseline: `a4bba60d`; implementation worktree: `test/pwmcp-coverage-20260920`
Reviewer: Codex

## Scope

This review precedes the coverage expansion in the dedicated
`test/pwmcp-coverage-20260920` worktree. It covers the Python release helper,
the installable client, the Playwright version resolver, and the runtime
contracts that the tests need to witness. It also checks whether the pinned
Playwright and MCP components still describe the current upstream interfaces.

## Implementation update — 2026-09-21

The modernization is implemented in the dedicated worktrees. The selected
coordinates are the newest
eligible values at the 2026-09-07 UTC cutoff imposed by the temporary
14-day policy:

| Coordinate | Selected value | Selection evidence |
| --- | ---: | --- |
| Playwright npm/PyPI/official MCR image | `1.62.0` | npm publication 2026-07-24; PyPI publication 2026-07-31; matching `noble` MCR tag |
| `@playwright/mcp` | `0.0.80` | npm publication 2026-09-01; its bundled prerelease Playwright core is a separate target |
| `chrome-devtools-mcp` | `1.8.0` | npm publication 2026-08-25 |
| `mcp-proxy` | `6.7.14` | npm publication 2026-09-06 |
| `lighthouse` | `13.4.1` | npm publication 2026-07-20 |
| `@modelcontextprotocol/sdk` | `1.30.0` | npm publication 2026-07-27 |
| `chrome-launcher` | `1.2.1` | npm publication 2025-09-25 |

The committed projection is `pwmcp-v1.62.0-r4`. The selected Playwright base
manifest is pinned as
`sha256:baed2032d533817f3dbe6425de795788430ba345e819a1201337009ba17c9d07`
in the Dockerfile and bake projection; refresh resolves that digest from the
exact MCR tag and check mode verifies all copies. Release preparation now runs
the resolver in `--check` mode, which validates the Dockerfile, bake file,
CIU templates, contract, and Lighthouse lockfile without contacting upstreams.
An explicit `--refresh` performs the temporary age-filtered upstream selection;
CMRU FEAT-03 is intended to replace that project-local selection later. The
Dockerfile uses the matching Playwright `1.62.0-noble` manifest digest.

The vendored Lighthouse server now has exact direct dependencies and a
committed npm lockfile, installed with `npm ci --omit=dev`. The active transport
contract is Streamable HTTP at `/mcp`; the legacy `/sse` commands remain only as
commented deprecated references, and the acceptance smoke checks both sides of
that contract. Assay R1 and R2 are configured for whole-target 100% branch
coverage. External `tester-unified` R1 has now passed at commit `7baef450`
with 100% whole-target branch coverage; R2 mutation evidence and the image/live
smoke remain pending.

## Findings

### 1. The reviewed-baseline Playwright coordinate was split across sources

`docker-bake.hcl`, `ciu.defaults.toml.j2`, and `pwmcp.contract.json` carry
Playwright `1.63.0`, while the Dockerfile default, its image digest comment,
the README, deployment examples, and usage documentation still described
`1.61.0`. The implementation makes the Dockerfile and documentation part of
the checked projection; the current committed coordinate is `1.62.0`.

This is a release correctness issue, independent of test coverage. A direct
Dockerfile build can therefore use a different base image from the generated
release coordinate. The coverage work will add regression checks around the
resolver and release inputs. The resulting image and consumer contract are
covered by the same compatibility review and external acceptance gate.

### 2. The reviewed-baseline MCP pins were behind current upstream releases

The checked-in pins are:

| Component | PWMCP pin | Current upstream observed 2026-09-20 | Review consequence |
| --- | ---: | ---: | --- |
| Playwright npm/PyPI coordinate | 1.63.0 in generated inputs; stale 1.61.0 fallback/docs remain | 1.63.0 | Keep the common-version resolver, but eliminate split defaults. |
| `@playwright/mcp` | 0.0.76 | 0.0.82 | Review CLI flags, HTTP transport, browser revision, and host checks before bumping. |
| `chrome-devtools-mcp` | 1.5.0 | 1.9.0 | Review `--browser-url`, executable-path, and Chrome argument behavior before bumping. |
| `mcp-proxy` | 6.5.2 | 6.7.18 | Review proxy flags and transport behavior before bumping. |

Those latest values were the upstream values observed during the baseline
review. The implementation applies the 14-day cutoff, which excludes the
newer PyPI 1.63.0 and the newest MCP package releases, and selects the values
recorded in the implementation update above. `@playwright/mcp@0.0.80` remains
independent because it bundles its own prerelease Playwright core; PWMCP passes
the baked browser executable explicitly and leaves compatibility to the live
acceptance check.

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

The selected versions are not, by themselves, proof that the packages work
with PWMCP's supervisor commands. The compatibility change therefore needs a
build and live endpoint acceptance run.

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

These are now explicit refusal paths in the compatibility hardening change,
with tests that construct a missing-key template and assert that the resolver
names the missing input. The external R1 gate confirmed the success and refusal
branches, including the digest projection checks.

The vendored Lighthouse MCP package had a related reproducibility issue:
`@modelcontextprotocol/sdk` and `chrome-launcher` use caret ranges and the
Dockerfile runs `npm install --production` without a committed lockfile. A
modernization pass should either commit and install the lockfile or make the
resolved versions explicit, then include the resulting MCP protocol behavior
in the container acceptance lane.

## Revised modernization plan

The 14-day rule is not a PWMCP-local policy. CMRU already owns the decided
FEAT-03 contract for central version selection in
`cmru/KNOWN_ISSUES_TODO_BACKLOG.md`: a repository-wide age window, language
resolvers, explicit exact-version overrides with reasons and expiry where they
bypass the normal window, resolved state, and explicit refresh/check operations.
PWMCP will consume that mechanism.

CMRU should perform generic version selection and alignment. A project declares
version targets rather than embedding package-specific selection logic in a
`steps.prepare` command. The target table name is the identity, and its direct
source children (`.npm`, `.pypi`, `.oci`, and so on) carry source-specific
coordinates. A target with `mode = "aligned"` expresses facts such as “the npm
package, PyPI package, and Playwright image tag must use one common version.”
CMRU resolves the newest stable candidate whose publication timestamp clears the
effective age window, then projects the resolved state into the declared native
files. CMRU remains generic because the package names, sources, targets, and
output mappings are configuration data.

The repository-wide `versions.age_window_days` belongs in
`cmru.orchestration.toml`. PWMCP-specific targets such as
`versions.targets."pwmcp.playwright"` belong in `pwmcp/cmru.toml`, together with
project-local overrides and generated resolution state. CMRU reconstructs the
effective document from the root and project file for every invocation; an
estate-wide run repeats that merge independently for each project, so one
project's values cannot leak into the next project's environment or resolver.

PWMCP therefore keeps a project-specific compatibility check, but its resolver
does not independently decide which versions are current. The Playwright
target remains one alignment target spanning npm, PyPI, and the Microsoft image.
`@playwright/mcp`, `chrome-devtools-mcp`, `mcp-proxy`, Lighthouse, and the
Lighthouse MCP SDK dependencies are separate targets unless a declared
compatibility relation couples them.

Transitive dependencies may also be declared as managed targets when the project
deliberately owns them. For npm this means the project declares the package
explicitly or through `overrides`, and commits the resulting `package-lock.json`;
the image build then uses `npm ci --omit=dev`. Unmanaged transitives remain
governed by the lockfile but are not independently selected by CMRU.

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
2. add PWMCP project-local coordinate declarations and Playwright alignment metadata;
3. move PWMCP’s generated version writes under CMRU’s resolved-state projection;
4. add the Lighthouse lockfile and managed direct SDK coordinates;
5. keep the PWMCP compatibility validator and live endpoint acceptance lane;
6. run the container build and smoke lane through `tester-unified`.

## Schema proposal

The alignment relationship should be structural rather than a repeated field.
The preferred CMRU shape is a named target table with direct source subtables:

```toml
# pwmcp/cmru.toml
[versions]
age_window_days = 21

[versions.targets."pwmcp.playwright"]
mode = "aligned"
constraint = ">=1.60,<2"

[versions.targets."pwmcp.playwright".npm]
name = "playwright"
registry = "https://registry.npmjs.org"

[versions.targets."pwmcp.playwright".pypi]
name = "playwright"
registry = "https://pypi.org"

[versions.targets."pwmcp.playwright".oci]
image = "mcr.microsoft.com/playwright"
tag = "v{version}-{image_distro}"

[versions.targets."pwmcp.chrome-devtools-mcp"]
mode = "single"
constraint = ">=1.8,<2"

[versions.targets."pwmcp.chrome-devtools-mcp".npm]
name = "chrome-devtools-mcp"
registry = "https://registry.npmjs.org"
```

The table path `pwmcp.playwright` is the alignment identity. CMRU does not
need a separate `alignment_group` value that can be misspelled or accidentally
left different on one source. `mode = "aligned"` means one version must be
available and old enough at every declared source; `mode = "single"` resolves
one source independently. The same structure handles Python, npm, Go, OCI,
and future ecosystems without hard-coding PWMCP names into CMRU.

The root and project documents are merged recursively for one invocation. Tables
merge by key, scalars replace inherited values, and arrays replace inherited
arrays. A project override is authoritative for that project, including a
different `age_window_days`; CMRU does not impose a separate “only stricter”
rule. Project-specific target declarations and their generated state remain in
the project document, so the root does not acquire PWMCP package knowledge.

An exact override is written directly on the target:

```toml
[versions.targets."pwmcp.playwright"]
mode = "aligned"
version = "1.64.0"
reason = "Required for security fix"
expires = "2026-10-01"
```

`version` means “use this exact operator-selected value”; `reason` is required,
and `expires` is required when the value bypasses the normal age policy. The
override can intentionally select an older or newer version.

Resolved state uses a separate child of the same target, so the input override
and generated result cannot be confused:

```toml
[versions.targets."pwmcp.playwright".resolved]
version = "1.63.0"
resolved_at = "2026-09-20T14:00:00Z"
age_cutoff = "2026-09-06"

[versions.targets."pwmcp.playwright".resolved.npm]
version = "1.63.0"

[versions.targets."pwmcp.playwright".resolved.pypi]
version = "1.63.0"

[versions.targets."pwmcp.playwright".resolved.oci]
version = "1.63.0"
```

The alternatives were considered as follows:

| Shape | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| Flat coordinates plus `alignment_group` | Small parser and easy programmatic input | Repeated group names, typo risk, weak human visibility | Reject |
| One table per target with direct source subtables | Relationship is structural, readable, supports aligned and single-source targets | Requires a small target/source schema | **Recommend** |
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
`cmru versions resolve`. CMRU creates the version files the project consumes;
the project keeps its semantic dependency declarations and gates consume the
committed generated artifact.

For PWMCP, CMRU would generate the Playwright npm/PyPI/image values and the
independent MCP package values into the project-owned declarations and native
artifacts. PWMCP’s remaining custom code would validate browser/protocol
compatibility and live endpoints.

## Remaining decisions

The product decisions are settled: central default policy, project-scoped
targets and overrides, recursive root-plus-project reconstruction per
invocation, stable releases only, direct exact-version overrides with reasons
and expiry where they bypass the normal window, synchronized Playwright sources,
`/mcp` as the only supported transport, and BasicAuth for external access. The
remaining implementation questions are the exact native output templates for
Python constraints, npm lockfiles, and Jinja/HCL projections.

The version-policy backlog remains CMRU FEAT-03. The bearer-token feature is
owned by [tls-edge/KNOWN_ISSUES_TODO_BACKLOG.md](../../../tls-edge/KNOWN_ISSUES_TODO_BACKLOG.md)
FEAT-01; PWMCP only documents its future use.

## Previous review decision

The coverage work and upstream modernization are committed in the dedicated
worktrees. Coverage tests, resolver fail-closed hardening, the enforced base
image digest, and the CMRU gate invocation are covered by the external R1
pass. R2 mutation evidence, a fresh image build, and live endpoint acceptance
remain the release checks after the long-running lane completes.
