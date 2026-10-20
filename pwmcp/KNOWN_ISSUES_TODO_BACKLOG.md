# PWMCP — known issues, TODO, and backlog

This file records consumer-facing product gaps for later triage. Normative
behavior remains in the product specification and consumer documentation.

Last updated: 2026-10-17 (PWMCP-04 delayed page hydration and multi-match selector wait).

## PWMCP-01 — Provide a rendered-page inspection command

**Status: IMPLEMENTED 2026-10-15; R1 PASS 2026-10-16; live CLI/MCP acceptance PASS 2026-10-16; public bundle publication pending.**

Consumers needed one page's JavaScript-rendered text or screenshot without
writing a browser client. Before this change, the CLI exposed only `doctor` and
`contract`; consumers had to call MCP tools directly or install the
release-bundled Python client plus matching Playwright package and write a
`BrowserLease` script. Raw MCP SDK snippets also coupled consumers to transport
and SDK API details.

Implemented as a registered `pwmcp inspect URL` verb using `BrowserLease` and
the matching Playwright package from the release bundle. It returns rendered
body text, an ARIA snapshot, or a screenshot; supports navigation and visible
selector waits; and closes the managed lease on exit. It does not require local
browser binaries. The standalone CMRU install also ships this CLI and makes
the service available to agents over the existing internal network.

The consumer examples, output formats, wait states, standalone install/update
flow, and internal MCP endpoint are documented in the README and consumer
guide. The registered `./run-gate.py r1` passed in `tester-unified` on commit
`d7caee83c479db157995c90d6730d76b18290ba9`: 247 tests passed, with 719/719
statements and 206/206 branches covered across the seven judged targets. The
updated R1 lane also passed on `f335b26b15cafd9a0292e037c52af951b0889fa5`.

Live acceptance passed on 2026-10-16: after `ciu up` from
`/srv/pwmcp-instance`, `pwmcp inspect https://example.com` returned rendered
page text, and an MCP client initialized over Streamable HTTP, listed tools,
navigated to the page, and retrieved its browser snapshot. The public bundle
remains unpublished because the configured GHCR credential lacks
`write:packages`; the local installer bundle and service image were deployed
for this acceptance run.

## PWMCP-02 — Adopt cli-extended (unified adoption, order 5 of 8)

**Status: FIXED 2026-10-07 (branch `pwmcp-clix`; controller merges and releases).**

**Observed / fix / oracle (evidence in [`docs/PWMCP-02-REPORT.md`](docs/PWMCP-02-REPORT.md)).**

- **Observed:** the mechanism section below (hand-rolled argparse with a `choices` list, a literal `"0.1.0"` fallback, tracebacks from `contract`/`doctor`, `dependencies = []`, four scripts with their own parsers and a `sys.path.insert` toward `cmru/src`).
- **Fix:** the client CLI is registered on `CliRegistry` (`contract`, shared `doctor` with `contract`/`playwright`/`skills` checks, `skills` via `register_skills_verbs`), identity through `CliIdentity.resolve` (`client/src/pwmcp_client/VERSION` is the wheel's dynamic version, metadata and file must agree), `unexpected_exceptions="report"`, domain failures as `CliFailure`; `cli-extended>=0.3.0` is declared in `client/pyproject.toml` with its floor reason; the skill moved to package data; surface lifecycle configured in `client/pyproject.toml` (`docs/CLI-SPEC.md`, `docs/cli-review.toml` with 59 cases, `docs/cli-surface.json`); `build-push.py` and the three `scripts/` files register a single-command CLI through `scripts/_vars.py` `script_cli` (`--dry-run`, no `--yes`: non-interactive CMRU steps) and import the installed `cli_extended` and `cmru` (the `sys.path.insert` toward `cmru/src` is gone: cmru's own wheel requires `cli-extended`, so any interpreter that runs these scripts under CMRU has both); `cli-extended` added to pwmcp's `depends_on` in `cmru.orchestration.toml`.
- **Oracle:** `./run-gate.py r1` PASS at 100% line and branch coverage of the judged targets; `cli-extended audit` 15 pass / 0 fail / 0 manual; `cli-extended surface check` passes; `assert_cli_contract` against the real `python -m pwmcp_client.cli`; plants (exit code, vendoring, `sys.path`, literal version fallback, stale surface) each fail a named test. Intentional behavior changes are listed in the report.
- **Not covered / follow-ups:** deploy of a released pwmcp (client wheel plus bundle) is the controller's; cmru KI-83 (filed from this item as KI-70; preflight does not read the nested `client/pyproject.toml`, so the `depends_on` edge is declared by hand); CLI-EXT-32 (audit AC-25 piecewise-path gap). PWMCP-01 is implemented separately in the 2026-10-15 instance work; its release-gate verification is pending.

Original plan (filed 2026-10-05 by the cli-extended unified-adoption program, W10):

**Source documents.** [`libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md`](../libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md) (decisions CX-D1..CX-D12, section "W10 - planned adoptions") and [`libraries/cli-extended/docs/ADOPTION-CHECKLIST.md`](../libraries/cli-extended/docs/ADOPTION-CHECKLIST.md) (AC-01..AC-25). **Dependency:** cli-extended 0.2.0 released first (W8, the controller). Not executed in the program's session. Land this before PWMCP-01 so the proposed `pwmcp inspect` verb is registered with the library from the start instead of added to a hand-rolled parser.

**Observed mechanism (verified in source).** The consumer-facing CLI is the 25-line `pwmcp` console script of the `pwmcp-client` wheel (`client/pyproject.toml`: `dependencies = []`, `[project.scripts] pwmcp = "pwmcp_client.cli:main"`). No file under `client/`, `scripts/` or `build-push.py` references `cli_extended`.

- Parser and help (AC-03, AC-04): `client/src/pwmcp_client/cli.py:1-25` builds a `PwmcpArgumentParser` with a positional `command` restricted to `choices=["doctor", "contract"]`, `--version` and `--contract` (default `http://pwmcp:3000/contract`). `client/src/pwmcp_client/diagnostics.py:15-26` defines `PwmcpArgumentParser(argparse.ArgumentParser)` overriding `format_help`, `format_usage` and `error`, plus `cli_headline()` (`:11`, "PWMCP <version> — client utility"). The two verbs are a choice list, not registered verbs; `doctor` is hand-written (`cli.py:21-23`) and prints a single `ok release=... playwright=... ws=...` line.
- Version (AC-01): `client/src/pwmcp_client/__init__.py:3-10` reads `importlib.metadata.version("pwmcp-client")` and falls back to the literal `"0.1.0"` (a hard-coded fallback, exactly the AC-01 pattern); `cli.py:6,15` and `diagnostics.py:8,12` consume `__version__`. The client's `session.py:33` also reads the installed Playwright version through `importlib.metadata`; that is a compatibility check against the server contract, not the tool's own version, and stays.
- Exception boundary (AC-10, AC-11): no top-level wrapper and no `--traceback`; `cli.py` lets `load_contract` and `verify_installed_playwright` errors propagate as tracebacks (`PwmcpError`, `PwmcpUnavailable`, `VersionMismatch` at `session.py:12,16,20` are the domain errors to map to `CliFailure`). The one `except Exception` is `session.py:87`, a cleanup-and-re-raise around `connect` that stays.
- `--dry-run` / `--yes` (AC-05, AC-12): none found in the client CLI. `build-push.py:202-206` has a stand-alone `argparse.ArgumentParser` with a required exclusive `--build`/`--push` group (push is mutating and has no confirmation or dry-run; decide `dry_run=True` or a recorded reason).
- Scripts (AC-03, AC-25): `build-push.py:33,35`, `scripts/build-bundle.py:21,35` and `scripts/publish-bundle.py:27,32` use `sys.path.insert` to reach `pwmcp/scripts` and `cmru/src` (cmru's own source), none toward `libraries/cli-extended`; `scripts/resolve-playwright-version.py:658` has its own `argparse` parser. Per CX-D3 these scripts import the installed `cli_extended` and their gate lanes keep `PYTHONPATH` at the worktree source; the cmru-source path hacks are a separate hazard to track with cmru's own library packaging.
- Gate PYTHONPATH (AC-25): `assay.toml:8` and `:47` set `PYTHONPATH = "client/src"` for the test lanes; the lanes must also see the library (installed in the tester image from the released wheel, or per-lane CX-D3 exemption).
- `run-gate.py` (the table's "also `run-gate.py`"): `pwmcp/run-gate.py` is a symlink to `../run-gate-project/run-gate.py` (verified with `ls -l`), so it is not a pwmcp-owned script; it is adopted once, under the run-gate entry (order 7).
- Skills (AC-19): `.claude/skills/pwmcp-fetch/SKILL.md` is the only source tree; move to package data in `pwmcp-client` and call `register_skills_verbs`.
- Doctor (AC-20): `pwmcp doctor` exists as a hand-written choice; it becomes the shared `doctor` verb with named checks (contract reachable, release matches, Playwright version matches), keeping the `ok release=...` output in `--json`.
- Tests (AC-21, AC-23): `client/tests/test_client.py:35,43` call `main()` and `main(["--version"])` in-process; no subprocess helper found.

**Common shape (tick each, cite the AC row).**

- [ ] `pwmcp-client` declares `cli-extended>=0.2.0` in `[project].dependencies` (currently `[]`); no vendoring (AC-24).
- [ ] `CliIdentity.resolve(...)` replaces the `"0.1.0"` fallback (AC-01, AC-02).
- [ ] Register `doctor` and `contract` as verbs; delete `PwmcpArgumentParser` (AC-03, AC-04, AC-07).
- [ ] `unexpected_exceptions="report"`; domain errors as `CliFailure` (AC-10, AC-11).
- [ ] Surface lifecycle: review/manifest/spec, `surface check` (AC-16, AC-17, AC-18).
- [ ] Skills via `register_skills_verbs` (AC-19); `doctor` as the shared verb (AC-20).
- [ ] `build-push.py` and `scripts/resolve-playwright-version.py` registered (or recorded as internal scripts with a reason); installed-library import, CX-D3 (AC-24, AC-25).
- [ ] Tests: `assert_cli_contract`, `invoke_script` where subprocess is needed (AC-21, AC-23).

**Acceptance.** `cli-extended audit` reports no `fail`; `cli-extended surface check` passes; pwmcp's own registered gate passes (`run-gate.toml`, `assay.toml`); a released pwmcp (client wheel plus server bundle) is deployed. The release bundle's client install instructions work offline with `--no-index --find-links` and the cli-extended wheel (CX-D2).

**Oracles.** `pwmcp --version`, `pwmcp doctor` and `pwmcp contract` keep their observable output on the golden fixtures in `client/tests/`; installing the built `pwmcp-client` wheel without the library fails with the dependency named; a controlled wrong implementation that keeps the `"0.1.0"` literal fails a test that removes the metadata and expects `VersionLookupError`.

## PWMCP-03 — Opt-in invalid-certificate support for `pwmcp inspect`

**Status: IMPLEMENTED 2026-10-16; `cli-extended` audit and surface check pass; R1 PASS; live-host acceptance PASS.**

**Observed.** `pwmcp inspect` failed with `ERR_CERT_AUTHORITY_INVALID` on the supplied TrueNAS host, while consumers could connect with Playwright by setting `ignore_https_errors=True` on their browser context.

**Fix.** Add `pwmcp inspect URL --ignore-https-errors`. It creates a temporary Playwright context with certificate errors ignored only for that invocation, then closes the context and the managed browser lease. This setting is context-wide: redirects and HTTPS resources loaded from other hosts in the page are included. Certificate verification remains on by default, and PWMCP's shared service policy is unchanged.

**Oracle.** Unit coverage proves the default passes `ignore_https_errors=False`, the option passes `True`, and the context closes on success and navigation failure. On 2026-10-16, the CLI reproduced the certificate failure without the flag, then returned the live sign-in form snapshot with the flag and a visible username-field wait. The initial root URL redirects to `/ui/signin`; waiting for `input[autocomplete="username"]` allows the JavaScript UI to hydrate before inspection. `cli-extended audit` passed 15/15 checks, `surface check` passed, and `./run-gate.py r1` passed on implementation commit `849129bfc8df05c35d31fad0a99349bbea51a49a`.

## PWMCP-04 — Inspect pages that hydrate after navigation

**Status: IMPLEMENTED 2026-10-17; live shared-page reproduction confirmed.**

**Observed.** The default `load` navigation wait returned ChatGPT's initial
shell before its shared conversation was rendered. `networkidle` returned the
later page text. A visible selector for message roles matched several message
elements, but `Locator.wait_for()` raised Playwright's strict-mode error.

**Fix.** Keep the efficient `load` default for ordinary pages. For delayed
single-page applications, document `--wait-until networkidle` together with a
page-specific `--wait-for` selector. Filter to visible matches and wait for the
first, so repeated selectors work without a fixed delay on all inspections.
Update the bundled `pwmcp-fetch` skill to prefer this readiness check and treat
its MCP fixed wait as an initial poll only.

**Oracle.** Unit coverage asserts multi-match selectors use the visible first
match. On the supplied shared page, the default inspection returned 993
characters, while `networkidle` returned about 11 KB. The message-role selector
resolved four elements and exposed the strict-mode defect. The registered R1
gate validates the implementation and documentation change.

## PWMCP-05 — the canonical `pwmcp-fetch` skill hard-codes a consumer's literal ciu instance id in its examples

**Status:** OPEN. Filed 2026-10-15 from dstdns (docs-sync W05, dstdns@16fc5d7e). Severity Low. Type: bugfix (docs).

**Observed.** `.claude/skills/pwmcp-fetch/SKILL.md:30` and `:34` give `dstdns-98535c-pwmcp` and `dstdns-98535c-network` as examples. `98535c` is a ciu instance id that dstdns's checkout path once derived. Instance ids are dynamic: dstdns's current main instance has another id, and every worktree has its own. A reader who copies the example targets a container that does not exist. A consumer that vendors the skill also trips its own literal-instance-id check (dstdns `tests/config/test_instance_identity_contract.py`).

**Why pwmcp owns it.** The skill text is pwmcp's canonical; consumers sync from it (NL-23).

**Required contract.** Examples use a placeholder (`<project>-<instance_id>-pwmcp`) and say how to resolve the real name, with `ciu resolve --json` or `docker ps --filter label=com.docker.compose.service=pwmcp`.

**Oracle.** `grep -nE '[a-z0-9]+-[a-z0-9]{6}-(pwmcp|network)' .claude/skills/pwmcp-fetch/SKILL.md` finds no literal id.

**Numbering note (INT-01, 2026-10-19):** filed on local main as `PWMCP-03`; renumbered because origin/main already used `PWMCP-03` (invalid-certificate inspect) for a different item.

## PWMCP-06 — Recover transient browser WebSocket failures during worker recycle

**Status: OPEN — candidate.**

**Observed 2026-10-16.** With PWMCP `1.64.0-r1` and Playwright `1.64.0`, a direct
`chromium.connect("ws://pwmcp:3000/")` call once failed with `socket hang up`
and WebSocket close code `1006`. At the same time, `pwmcp doctor` passed the
release and Playwright checks and Docker reported the PWMCP container healthy.
The container log showed a `run-server` recycle and a gateway session closed
with `reason=upstream_error`; the next `pwmcp inspect` call succeeded. This is
evidence of a transient connection failure near worker recycling, not a
confirmed root cause.

**Required investigation.** Reproduce a client connection during the idle
`run-server` recycle and determine whether the gateway should hold the request
until a worker is ready or return a typed, retryable availability error. Make
the supported Python `connect()` path resilient to that lifecycle edge, with a
bounded retry only before a browser session is established. Page navigation
and interaction failures must remain visible to the caller.

**Oracle.** A repeatable test forces the worker recycle while clients connect.
Each connection either succeeds or returns a documented retryable error, no
orphan browser lease remains, and a page/action error is never retried as a
connection startup failure. `pwmcp doctor` alone does not count as evidence for
this lifecycle case.

**Numbering note (INT-01, 2026-10-19):** filed on local main (`b3277b36e`) as `PWMCP-04`; renumbered because origin/main already used `PWMCP-04` (delayed page hydration) for a different item.

## PWMCP-07 — every published pwmcp release ships a bundle its own `get.py` cannot download (asset name lacks the `v`)

**Status: OPEN — blocks the mdt-cockpit image build.** Found 2026-10-20 by `BUILD_NO_CACHE=1 cmru build mdt-cockpit`.

**Evidence.** Stage-2 step 39/55 (`python3 /tmp/install_pwmcp_instance.py`) fails:
`==> Pinned version: pwmcp-v1.64.0-r2` / `==> Downloading pwmcp-v1.64.0-r2.tar.xz ...` /
`err Download of pwmcp-v1.64.0-r2.tar.xz failed (HTTP 404).` (log: the build worktree's
`mdt-cockpit/logs/cmru/build.log`, search `#51 `). Release `pwmcp-v1.64.0-r2` (2026-10-17T19:16:20Z)
carries `get.py get.py.sha256 pwmcp-1.64.0-r2.tar.xz pwmcp-1.64.0-r2.tar.xz.sha256`; r1
(`pwmcp-1.64.0-r1.tar.xz`), v1.63.0-r2 and v1.62.0-r3 are named the same way (no `v`).
For comparison `tls-edge-v2.0.0` carries `tls-edge-v2.0.0.tar.xz`, and its get.py works.

**Mechanism (two sides, two sources, no shared function).**
- Producer: `pwmcp/bundle.toml:14` `name_template = "pwmcp-{version}.tar.xz"` (since `82c29e2db`,
  2026-06-16) -> `pwmcp/scripts/publish-bundle.py:55` (`find_bundle` expects `pwmcp-{version}.tar.xz`)
  -> `publish_versioned(...)` (`cmru/src/cmru/release.py:745`) uploads `asset_path` under the
  file's own name; it never renames the primary asset. (Only the variants path,
  `release.py:940 variant_asset_name`, derives a name, and it derives the tag-based one.)
- Consumer: the rendered `get.py` (`cmru/src/cmru/templates/get.py.tmpl:614`) hard-computes
  `f"{tag}{ASSET_SUFFIX}"` = `pwmcp-v1.64.0-r2.tar.xz`; unchanged since the get.py rewrite
  (`10476a47e`, 2026-06-20). `hosts/github.py:101` has the same tag-based expectation, but only on
  the ambiguity branch; with exactly one match `resolve_latest` returns whatever single asset is
  there, so cmru's own resolution masked the mismatch while get.py could not.
- First exercised only now: pwmcp's get.py began shipping as a release asset with r1 (2026-10-17,
  `dc4072cf2`) and mdt-cockpit is its first real consumer. The defect is pwmcp-specific in the
  producer (ciu/tls-edge produce tag-named assets); the missing producer/consumer check is generic
  (cmru KI-89).

**Blast radius.** `get.py install` from any pwmcp release, so every release r1..r2 and all earlier
ones; the mdt-cockpit image (and anything pinning `pwmcp.contract.json`'s release). Not affected:
the offline `--local-dev-dir` path (it replaces `_download_asset`, and so also hides this bug).

**Why no test caught it.** `cmru/tests/installer_fakes.py:138` builds its fake release with the
template's own name (`<tag>.tar.xz`), so the test compares the template with itself.
`pwmcp/tests/test_script_cli.py:342` mocks `publish_versioned`. No test compares the name the real
producer publishes with the name the rendered get.py requests.

**Fix proposal (third version; do not pick a side).** Derive the primary asset name from ONE
function, `cmru.release.bundle_asset_name(prefix, version, suffix, variant=None)` (generalising
`variant_asset_name`), and call it from `publish_versioned` (rename via `_place_named`, as the
variants path already does), from `get.py.tmpl` (rendered into the script, or `tag`-parse), and from
`hosts/github.py`. Then pwmcp's `name_template`/`find_bundle` become a build-local intermediate
only. Do not rename already-published assets (immutable, signed-adjacent sidecars); for the
installed base add a bounded get.py fallback that, on a 404 for the derived name, picks the single
`*{ASSET_SUFFIX}` asset of the release whose sidecar matches (fail on 0 or >1; never guess).
Oracle: an integration test renders get.py for a project, publishes through the REAL
`publish_versioned` against a fake GitHubReleases, then runs get.py's download step against that
fake; for pwmcp also assert `bundle.toml`'s name equals the function's output. Then release
pwmcp (r3) so the shipped get.py asset and bundle agree.

**Unblock options for mdt-cockpit (not done here).** (a) release a fixed pwmcp then re-pin
`pwmcp.contract.json`/`PWMCP_VERSION` and the get.py URL+sha256 in mdt-cockpit; (b) interim, pin a
commit-pinned get.py that carries the fallback and point `PWMCP_GETPY_URL`/`PWMCP_GETPY_SHA256` at
it while the contract keeps `pwmcp-v1.64.0-r2` (no republish of the bundle needed); (c) build with
`MDT_BUILD_MODE=local-dev` (pwmcp-bundle supplied locally) as a stopgap, accepting that it bypasses
the very download being broken. No existing release works with the current get.py.
