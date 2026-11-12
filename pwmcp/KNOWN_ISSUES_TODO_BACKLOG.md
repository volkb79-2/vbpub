<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
# PWMCP — known issues, TODO, and backlog

This file records consumer-facing product gaps for later triage. Normative
behavior remains in the product specification and consumer documentation.

Last updated: 2026-11-12 (PWMCP-61: the R2 lane lets memory-runaway mutants swap because `memory_swap` is unset).

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

**Status:** STALE-FIXED 2026-11-04 (already fixed by 948080489 "keep fetch skill install agnostic"; the skill at `client/src/pwmcp_client/skills/pwmcp-fetch/SKILL.md` has no literal instance id, oracle grep finds none; the skill moved from `.claude/skills/`). Filed 2026-10-15 from dstdns (docs-sync W05, dstdns@16fc5d7e). Severity Low. Type: bugfix (docs).

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

**Status: FIXED on main (merged 2026-10-21, gate-provisional PASS at b22b747a6), not released; fix C removed by PWMCP-08 (2026-10-21).**

Found 2026-10-20 by `BUILD_NO_CACHE=1 cmru build mdt-cockpit`; independently reviewed, and the
review's conclusions (contract, keystone scope, unblock preconditions) are inlined below.

**Evidence.** Stage-2 step 39/55 (`python3 <temp-path>`) fails:
`==> Pinned version: pwmcp-v1.64.0-r2` / `==> Downloading pwmcp-v1.64.0-r2.tar.xz ...` /
`err Download of pwmcp-v1.64.0-r2.tar.xz failed (HTTP 404).` (log: the build worktree's
`mdt-cockpit/logs/cmru/build.log`, search `#51 `). Release `pwmcp-v1.64.0-r2` (2026-10-17T19:16:20Z)
carries `get.py get.py.sha256 pwmcp-1.64.0-r2.tar.xz pwmcp-1.64.0-r2.tar.xz.sha256`; r1
(`pwmcp-1.64.0-r1.tar.xz`), v1.63.0-r2 and v1.62.0-r3 are named the same way (no `v`).
For comparison `tls-edge-v2.0.0` carries `tls-edge-v2.0.0.tar.xz` (+ sidecar; it ships no `get.py`
asset): its asset name matches what a get.py requests.

**The contract.** The tag-based name is the documented one: `cmru/docs/CONSUMERS.md:319` ("The release
assets are `<tag>.tar.xz` ... `<tag>.tar.xz.sha256` ... and `get.py`") and SPEC S6.3 step 3; tls-edge
(`scripts/build-artifact.sh:45`) and the variants publisher follow it. pwmcp is the deviating
producer; the decision is "producers conform to the documented contract", not a free choice.

**Mechanism (two sources, no shared function).**
- Producer: `pwmcp/bundle.toml:14` `name_template = "pwmcp-{version}.tar.xz"` (since `82c29e2db`,
  2026-06-16) -> `pwmcp/scripts/publish-bundle.py:55` (`find_bundle`) ->
  `publish_versioned(...)` (`cmru/src/cmru/release.py:745`), which uploads `asset_path` under the file's
  own name (`:865`, `:869`, `:890` all use `asset_path.name`); it never renames. Only the variants path
  (`release.py:940 variant_asset_name`) derives a name, the tag-based one.
- Consumer: the rendered `get.py` (`cmru/src/cmru/templates/get.py.tmpl:614`) computes
  `f"{tag}{ASSET_SUFFIX}"` = `pwmcp-v1.64.0-r2.tar.xz` (tag-based in the rendered get.py, spelled
  `ASSET_SUFFIX` since `c54e90581`). `hosts/github.py:101` has the same expectation, but only on the
  ambiguity branch; with exactly one match `resolve_latest` returns whatever single asset exists, so
  cmru's own resolution masked the mismatch while get.py could not.
- First exercised only now: pwmcp's get.py began shipping as a release asset with r1 (2026-10-17,
  `dc4072cf2`) and mdt-cockpit is its first real consumer. Same class as CIU-99 (get.py's
  `<tag><asset_suffix>` resolves nothing because ciu publishes only a bare wheel, no bundle at all).
  Generic part: nothing compares any producer with the contract (cmru KI-89).

**Blast radius.** `get.py install` from any pwmcp release (r1..r2 and all earlier), hence the mdt-cockpit
image and anything pinning `pwmcp.contract.json`'s release. Not affected: the offline
`--local-dev-dir` path (it replaces `_download_asset`, so it also hides this bug).

**Why no test caught it.** `cmru/tests/installer_fakes.py:138` types the template's formula out in the
fake release, so the template is compared with itself. `pwmcp/tests/test_script_cli.py:340-345` mocks
`publish_versioned`. Nothing compares the name the real producer publishes with the name get.py requests.

**Fix, in this order (sequencing A, then B, then C).**
- **A. pwmcp-local (primary; independent of any cmru release):** `bundle.toml` `name_template` becomes
  `pwmcp-v{version}.tar.xz`, `find_bundle` expects the same, plus the tests and the docs that name the
  file. Fixes the next pwmcp release (r3).
- **B. cmru KI-89, so it cannot recur:** one name function (`asset_name_for_tag` / `bundle_asset_name`)
  used by the variants publisher, `hosts/github.py` and (equality-tested) by the rendered get.py, and an
  oracle that reads EVERY project with `[project.installer]` + `asset_suffix` and compares the name its
  producer (`bundle.toml` template or `tarball-publish --glob`) would publish with the contract name.
  `publish_versioned` is NOT changed to rename or refuse: it is the keystone behind `wheel-publish` and
  `tarball-publish`, and a wheel's file name is pip's. A runtime refusal, if wanted later, must be scoped
  to installer projects and make `latest.json`, the release body and the download URL follow the placed
  name. Cross-reference CIU-99 (ciu is the listed no-producer exemption).
- **C. get.py fallback for the already-published releases: REMOVED (PWMCP-08, operator decision
  2026-10-21: "just leave it be, we clean up eventually").** It had been implemented (listing the release's
  assets on a 404 and taking the single `*{ASSET_SUFFIX}` asset whose sha256 sidecar verified). It is
  gone from `get.py.tmpl` and its tests; `get.py` requests exactly `expected_asset_name` and a 404 is a
  loud refusal naming the requested asset. **Consequence:** the already-published pwmcp releases
  <= `v1.64.0-r2` (r1, r2, 1.63.0-r2, 1.62.0-r3 and earlier) stay undownloadable by `get.py`; consumers
  must pin a release >= the next one published after A. The checked-in installers (`ciu/get.py`,
  `tls-edge/get.py`, `pwmcp/get.py`) are re-rendered from the changed template
  (`cmru get-py <project> --output <project>/get.py`, in-process from the worktree's cmru); pwmcp's
  `get.py` sha256 must be re-pinned in mdt-cockpit after the release (not part of this change).

**Unblock options for mdt-cockpit (not done here).**
- (a) Release pwmcp r3 with fix A (a new tag: mdt-cockpit's `pwmcp.contract.json` version and the
  `PWMCP_VERSION`/get.py URL+sha256 pins change with it). Sound, and needs only A.
- (b) WITHDRAWN: it depended on fix C, which was removed (PWMCP-08).
- (c) A local-dev build is not set by hand: `MDT_BUILD_MODE=local-dev` is selected only by
  `build-push.py --build --local-dev <manifest.toml>`, which supplies artifacts through the
  `mdt-local-dev` context. Its image is provenance-marked as local-dev and not a releasable image, so (c)
  unblocks a developer build only.
No existing release works with `get.py`; only a release published after A does.

## PWMCP-09 — pages protected by Anubis answer "Access Denied" to the managed browser; a human sees them fine

**Status: IN PROGRESS, FIXED-ON-BRANCH for the configurable user agent (option 3); acceptance part 2 (`blocked-by-site` detection) still OPEN.**
**Decision (operator, 2026-10-27): option 3, with selectable presets**, as an explicit, recorded, opt-in
feature (off by default): config key `user_agent` / `PWMCP_USER_AGENT` with presets `edge-windows`,
`chrome-windows`, `firefox-windows`, or a custom string; the UA string and its matching client-hint
brands only (no navigator.webdriver, plugin or canvas changes); every `pwmcp inspect` result carries
`user_agent` and `user_agent_source`; `pwmcp inspect --user-agent` for one call. Details: README
"Overridable User Agent". **STILL UNVERIFIED: whether Anubis actually lifts the block** under any
preset (nothing was run against a live deployment from the implementing worktree), whether Chromium
omits `Sec-CH-UA` for the Firefox preset's empty brand list, and the container wiring (supervisord
wrapper, `ua-override`, entrypoint) until an image is built and run. Open: the `blocked-by-site`
classification of recognised Anubis/Cloudflare block pages and the skill wording for that outcome.

*Original filing:* (filed 2026-10-27, operator request). Found while reading <https://git.azlux.fr/Azlux/sss>
through the `pwmcp-fetch` recipe.

**Evidence.** `browser_navigate` + `browser_evaluate document.body.innerText` returned "Oh noes! Access
Denied: error code 4d1dbaddfcc0f385 ... Protected by Anubis ... version v1.28.2" (WebFetch got the same
page, code 9e4edb5b6b850c41). The browser's identity as measured through the same endpoint:
`navigator.userAgent` = `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)
HeadlessChrome/156.0.0.0 Safari/537.36`, `navigator.webdriver` = false, 5 plugins. The operator opens the
same URL in their own browser without a block. The word "Access Denied" (not a proof-of-work challenge page)
is Anubis answering a deny rule, which is consistent with the `HeadlessChrome` token, but that cause is NOT
yet verified: nothing was changed to test it.

**Why this is not simply "spoof a normal browser".** Anubis is deployed to refuse automated clients. Making the
managed browser present as a non-headless user agent to defeat an explicit deny rule is circumvention of the
site owner's access control, so it is a decision to take deliberately, not a silent default in the shared
service. What the managed browser may legitimately do, in increasing order of intrusiveness, for the operator's
own reading of a public page:
1. Report what it is: document that these hosts are unreachable by design and have the consumer use another
   source (the GitHub mirror of the same project worked on 2026-10-27).
2. Operator-solved session handoff: the operator opens the page in their own browser, and the cookie Anubis
   sets is imported into a named, expiring PWMCP browser context for that host (explicit, per host, logged).
3. A configurable `--user-agent` (off by default, recorded in every result) for hosts the operator owns or
   has permission to read automatically.

**Acceptance.** A decision recorded among 1-3, then: `pwmcp inspect` reports a recognised Anubis/Cloudflare-style
block as `blocked-by-site` (distinct from empty content and from timeout) with the block's error code, so an agent
does not mistake it for page content; the `pwmcp-fetch` skill documents the outcome.

## PWMCP-10 — two concurrent clients collide on the single shared browser ("Browser is already in use")

**Status: MESSAGES FIXED 2026-11-04 (6be337905): the `pwmcp-fetch` skill and `docs/CONSUMERS.md` say fetches must be serialised and the `--isolated` hint must not be followed. The error text itself is emitted by the upstream Playwright MCP and cannot be changed here. Concurrent sessions remain OPEN (per-session contexts) (filed 2026-10-27).** Running four `browser_navigate` clients in parallel against
`http://pwmcp:8931/mcp` made all but one fail with: `Browser is already in use for
<temp-path>/ms-playwright-mcp/mcp-chrome-for-testing-8a5edab, use --isolated to run multiple instances of the
same browser`. Sequential use works. `containers/pwmcp/supervisord.shared.conf:27,114` records that `--isolated`
broke the shared-browser mode on 2026-07-13, so the fix is not "add the flag". **Acceptance.** Either concurrent
sessions are served (per-session contexts on the one browser) or the `pwmcp-fetch` skill and the error text say
plainly that fetches must be serialised; today the error names a flag the service cannot take.

## PWMCP-11 — OCR sidecar and console watching (program)

**Status: phase 1 FIXED-ON-BRANCH (2026-10-27), release wiring for the second image included (FIXED-ON-BRANCH, unverified until a real release is cut); phases 2 and 3 OPEN.**

**Need.** Automating a host console through a web console. The Netcup SCP "Screen" is ONE 1280x800 `<canvas>`
fed by a VNC WebSocket, so Playwright snapshots carry no text and an agent was interpreting screenshots
visually. Wanted: MECHANICAL recognition of what a text console (Debian installer, TrueNAS installer, shells)
shows. Origin: a Codex session driving a TrueNAS relaxed unattended install on a Netcup host (rollout
`01a14d1b-bec6-7671-80b8-a5c53ff976ee`, 2026-10-18; read it with
`nyxloom-harness extract --format codex --preset watch <rollout.jsonl>`).

**Operator decisions (2026-10-27).**
- OCR runs in a STATELESS SIDECAR container: pwmcp posts a PNG and gets text + word boxes + confidence (or a
  glyph-grid text screen); pwmcp keeps the browser and the watch loop. The sidecar lives under
  `containers/` in the same ciu/compose stack with its own service alias and is released WITH pwmcp (one
  contract, one set of gates).
- Engines: Tesseract (native, pluggable interface) first, then exact glyph-grid matching for fixed-font text
  consoles (Debian installer, TrueNAS installer).
- Watching: in-browser canvas change detection (hash on animation frames, debounce), then OCR on change,
  exposed as `wait_for_text(pattern|regex, timeout, stable)`. The WebSocket frame event is only an optional
  wake-up hint (66 frames in 15 s with unchanged text), never the decision. Unrecognised screens are
  INDETERMINATE.
- A guarded expect-and-send primitive in pwmcp is OFF unless a config flag enables it ("flag only": no
  allowlist, no rules file). Consumers may still own policy (for example the Netcup protected-server check).
- Test corpus: the local QEMU VM harness (`testing/vm/vmctl`) captures first; live Netcup captures verify
  afterwards. Finish/reboot screens of the TrueNAS install were never observed; only the ready signature
  ("Console setup" plus management URLs) is known.

**Phases.**
1. (FIXED-ON-BRANCH) The sidecar (`containers/ocr/`: Dockerfile, `ocr_service.py`; Tesseract via the CLI,
   pluggable engine registry with `glyphgrid` registered-but-501), compose/ciu wiring (`pwmcp.ocr.*`, default
   off, alias `pwmcp-ocr`, port 8941 because 8940 is the run-server admin port), the `/contract` `ocr` block,
   `pwmcp_client.ocr.recognize`, `pwmcp ocr`, docs and offline tests. Returns text and confidence only.
2. (OPEN) `wait_for_text` watcher (in-browser canvas change detection on the shared browser's already-open
   page; the shared-browser mode from P03 is the natural home) and the glyph-grid engine fed by VM-harness
   captures. Needs the PWMCP-10 concurrency answer first, since a watcher holds the one shared browser.
3. (OPEN) Guarded expect-and-send, OFF unless a config flag enables it.

**GlyphGrid tooling update (2026-11-03).** The tooling now includes SHA-pinned console font atlases with per-font attribution, a reference-checked
reader fast path and frame-sequence reader, and deterministic synthetic image generation with exact ground truth. These support offline
measurement and regression testing only. Phase 2 remains OPEN: the service engine is still registered as unavailable (HTTP 501), the watcher is
not implemented, and the larger 242-frame console series used for performance measurements remains local.

**Local corpus verification (2026-11-03).** The 242-frame console corpus is local-only and is never published. Its copy is verified from an
absolute `PWMCP_CONSOLE_CORPUS_DIR` against the committed frame index and deterministic tar SHA256. The full-corpus tests skip when the variable is
unset; an explicitly invalid path fails. Registered gate lanes remain corpus-free.

**Release wiring: FIXED-ON-BRANCH (operator decision 2026-10-27: extend the existing release so ONE pwmcp
release builds, publishes and records BOTH images under one contract).** The image is first published in `1.64.0-r4`; this stays
UNVERIFIED until the post-release smoke has run.
- Image: `ghcr.io/volkb79-2/pwmcp-ocr` (sibling of `ghcr.io/volkb79-2/pwmcp`, the repository naming
  convention), tagged `<PWMCP_VERSION>` (the SAME coordinate, e.g. `1.64.0-r3`) and `latest`.
- Build/push: a `pwmcp-ocr` target in `docker-bake.hcl` (same context `.`, no extra platform/cache policy),
  added to the `all` group, so the existing `build-push.py --build/--push` bake builds and pushes both.
  `GHCR_PACKAGE_NAMES` is `pwmcp,pwmcp-ocr`, so the existing visibility sync runs for both packages. The main
  image has no signing/provenance step, so none is added.
- Base pin: `debian:trixie-slim` pinned by multi-arch manifest digest in `containers/ocr/Dockerfile`
  (`DEBIAN_RELEASE`, `DEBIAN_IMAGE_DIGEST`), `docker-bake.hcl` and `cmru.vars`. The mechanism is the same as
  the Playwright base: `scripts/resolve-playwright-version.py --refresh` resolves the digest (Docker Hub,
  anonymous HEAD) and rewrites all projections; `--check` (CMRU's prepare phase) fails if they disagree, are
  not a sha256, the FROM lost its digest, or `[pwmcp.ocr.image] tag` differs from the pwmcp release.
  `containers/ocr/Dockerfile` is in cmru's `commit_generated`, so a refreshed pin is committed. The loader
  (`scripts/_vars.py`) now requires the two keys, so an old `cmru.vars` fails closed. The digest was read
  from Docker Hub on 2026-10-27 (`sha256:62bd423a...`).
- Coordinate/contract: `pwmcp.ocr.image.tag` defaults to the pwmcp tag and the resolver keeps them equal.
  `pwmcp.contract.json` carries no image coordinates (not for the main image either) and is UNCHANGED, as is
  `get.py`; consumers pin images through the bundle's `ciu.defaults.toml.j2`, which now has both. Neither
  mdt-cockpit (it reads only the contract `release` and `get.py`) nor `topos/pwmcp-instance/UPSTREAM.toml`
  (vendored copy at 1.61.0-r6) needs a change; topos picks the OCR defaults up on its next re-vendor.
- STILL OPEN / decisions not made: (a) DECIDED BY DESIGN, 2026-10-27: no age window for the rolling Debian base
  digest (a rolling OS tag whose newer digests are mostly security rebuilds, which a window would only delay);
  the digest is whatever the tag points to at `--refresh`. (b) The second GHCR package is created on first push; the existing
  mirror-repo-visibility sync is applied to it, which assumes the release PAT may create and administer it.
  (c) Whether to also register `debian:trixie-slim` as a cmru `[versions.targets."oci.*"]` target (mdt-cockpit
  has advisory ones); NOT added, because it could not be validated without running cmru and a failed load
  would break the release. (d) cmru's own artifact accounting still lists `artifacts = ["oci-image",
  "bundle"]`; whether it needs to enumerate the second image is not checkable without running cmru. (e) The
  main image's unpinned `debian:bookworm-slim` KSM builder stage is untouched.

**Result contract (reviewed 2026-10-27).** The sidecar result is COMPLETE: `min_conf` flags words below it
(`low_conf`, per-line `conf_mean` / `conf_min` / `low_conf_words`, top-level `low_conf_words` and
`min_conf_applied`) and never deletes them; removal is the explicit `drop_low_conf=1` opt-in. Phases 2-3 match
on this text and send keystrokes, so doubt must stay visible.

**Phase 2-3 hazards / required inputs** (from the Opus review and the TrueNAS capture; that corpus is on another
branch, to be merged after this one):
- (a) `text` joins a line's words with single spaces, so the console's column layout is lost. Phase 2 needs a
  grid/rows output for console frames (a fixed 160x50 character grid at 1280x800), not free text.
- (b) `_capture_element` captures at CSS size, which RESAMPLES the exact 8x16 px cell grid. Phase 2 must capture
  the canvas's intrinsic bitmap (and verify the size), not a displayed-size screenshot.
- (c) Tesseract reads light-on-black console text at 92-100% but FAILS black-on-grey installer dialogs (needs
  colour-aware preprocessing: invert/threshold) and mis-reads digits (`<host-address>` -> `192.0.2.15`, `9)` -> `3)`).
  Exact values need glyph matching: the console is an exact 8x16 px cell grid.
- (d) Confidence is NOT calibrated: a garbled dialog read at 91% mean confidence. `low_conf` is a hint; an
  unflagged word is not verified; without `min_conf` the flags are null. Keystrokes need an EXACT match of a
  known screen signature or the glyph-grid engine, never "unflagged".
- (e) UNMEASURED: whether the 512m limit holds for 24 MP scaled images, and the R2 mutation runtime on the newly
  judged files (about 380 mutation sites); expect the r2 lane budget of 4h to matter.
- The container `mem_limit`/`cpus`/`pids_limit` keys are author-set and replace (are not clamped by) the ciu
  governance overlay's per-service value (author-set keys win): their interaction with a live overlay is UNVERIFIED.

**First release of the OCR image: checklist** (also in README "Release Model"): confirm the base digest with
`docker buildx imagetools inspect debian:trixie-slim` (and `--refresh` if it differs); a transaction prepared
before the new `cmru.vars` keys fails closed; confirm the token has `write:packages` for a new package
(PWMCP-01); expect `pwmcp-ocr` to be created PRIVATE and unlinked and make it public once in the GHCR UI
(the `org.opencontainers.image.source` label links it); if the visibility sync hard-fails after both images
were pushed (cmru KI-97) re-run ONLY the sync step, never `cmru release --resume` without `--set-version`
(the 2026-09-08 v1.0.0 incident). The sidecar image is first published in `1.64.0-r4`. The pwmcp gate lanes depend on the tester
image carrying the pwmcp-client `test` extra (pytest, pillow, websockets, jinja2, pyyaml) plus node and sh.
Operator policy 2026-10-27: a test needing an optional module or binary FAILS when it is missing (clear message
naming it and the extra); skipping is the opt-in, `PWMCP_ALLOW_MISSING_TEST_DEPS=1` on a developer machine.
One helper (`tests/_testdeps.py`) serves every site; the lanes set nothing. The real-Tesseract test keeps an
explicit skip (the binary exists only in the sidecar image) and fails when `PWMCP_REQUIRE_TESSERACT=1` (set only
in a lane that runs inside the OCR image; none exists today). `client/src/pwmcp_client/user_agent.py` (PWMCP-09) and the sidecar are judged targets at 100% line and branch
(user_agent.py was 73% before: its `_main` and platform/hint branches had no in-process tests). The Node code
(`ua-override/index.js`, the two gateways, the admin server) is outside the Python judge and is exercised only by
the subprocess tests. Not judged: `get.py` (generated by `cmru get-py`, 1093 statements), `installer/adapter.py`,
`scripts/build-bundle.py`, `scripts/publish-bundle.py`, `scripts/prepare-client-wheels.py`, `scripts/_vars.py`
(partly covered, see the report). The controller owns the tester-unified change and
image rebuild and the `test` extra in `client/pyproject.toml` (not touched on this branch: keep it verbatim on merge).

**UNVERIFIED until an image is built and run.**
- Real Tesseract behaviour: the TSV column layout assumption (12 columns, level 5 = word, `conf` -1 for
  layout rows) matches the documented format and the fake used by the tests, but was not checked against the
  packaged binary; `tesseract stdin stdout ... tsv` reading from stdin and `--list-langs` / `--version`
  output shapes are likewise assumed.
- Recognition accuracy on real console frames (fonts, colours, `--psm 6` vs `11`, `--scale`), and whether
  the Debian `tesseract-ocr-eng` data (fast or best) is good enough. The ONE real-engine test
  (`tests/test_ocr_service.py::test_real_tesseract_recognises_rendered_console_lines`) skips unless
  `tesseract`, Pillow and DejaVu Sans Mono are present; it did NOT run in the implementing worktree.
- The release wiring end to end: `docker buildx bake all` against the managed BuildKit builder building both
  targets (the `bake --print` plan was NOT run: no docker here), the push, the GHCR visibility sync for the
  new `pwmcp-ocr` package, cmru's handling of the extra `commit_generated` path, and `cmru release pwmcp`
  itself. Offline tests check the committed inputs against each other and the push loop's package list only.
- The image build (`containers/ocr/Dockerfile`: package names on the pinned Debian release, uid 1000,
  read-only rootfs with only `/tmp` writable, `nice` entrypoint) and the healthcheck.
- The compose wiring under a real `ciu up` (that the narrowing `mem_limit`/`memswap_limit`/`cpus` coexist
  with the governance overlay, that `read_only` plus tmpfs is accepted, the `$INSTANCE_ID` container name,
  alias resolution from the pwmcp container). The offline tests render the template with plain Jinja
  (`StrictUndefined`, as ciu does), not through ciu.
- Element capture of a real canvas through Playwright's compositor screenshot (chosen over `toDataURL`
  because it does not fail on tainted or WebGL canvases): the image is the DISPLAYED size, not the canvas's
  intrinsic 1280x800 bitmap unless the page is shown at that size. The fake browser in the tests cannot
  show this.
- Gate coverage: `client/src/pwmcp_client/ocr.py` was added to the `assay.toml` judged targets (100% line
  and branch measured locally); the sidecar `containers/ocr/ocr_service.py` is tested but deliberately NOT a
  judged target yet, and the registered gate was not run.

## PWMCP-12 — the session cleanliness guard makes Git a prerequisite for the whole test suite

**Status: FIXED 2026-11-04 (48838ede3).** Guard in `pwmcp/conftest.py`: missing git / non-worktree / `safe.directory` each give one `UsageError` before the suite, `--no-optional-locks`, 60 s timeout, explicit warned opt-out `PWMCP_ALLOW_NO_GIT_GUARD=1`; oracle `tests/test_git_guard.py`. (Original: Severity P2.) Filed 2026-10-30 from `reviews/pwmcp2.out`.

**Observed.** The autouse session fixture in `pwmcp/conftest.py:17-30,33-38`
invokes `git status` before and after every test session. If Git is absent, the
snapshot is not a Git worktree, or Git rejects the checkout for dubious
ownership, setup or teardown errors before ordinary tests run. The last case
also depends on the gate applying `safe.directory` to the mounted checkout.
The subprocess has no timeout.

**Proposed fix.** Make the guard's repository-backed execution requirement
explicit and bounded: either opt it in only for lanes that guarantee a Git
checkout, or validate that prerequisite with one named, actionable diagnostic
before the suite. Distinguish missing Git, non-worktree snapshots, and
`safe.directory` refusal; add a subprocess timeout and keep the gate's
identity setup in sync.

## PWMCP-13 — the Git-visible cleanliness guard misses ignored and exit-time writes

**Status: FIXED 2026-11-04 (48838ede3), narrower contract chosen.** The module docstring of `pwmcp/conftest.py` now states that ignored paths and exit-time writers (coverage) are out of scope; the guard certifies no new Git-visible entries under `pwmcp/`. (Original: Severity P2.) Filed 2026-10-30 from `reviews/pwmcp2.out`.

**Observed.** `pwmcp/conftest.py:23-29,35-42` compares `git status
--untracked-files=all`, which omits ignored paths such as `.pytest_cache/`,
`__pycache__/`, `.assay/`, and `coverage.json`. Coverage also writes
`coverage.json` from its atexit handler after this fixture takes its final
snapshot. The guard therefore cannot certify that tests left the repository
tree unchanged.

**Proposed fix.** State the narrower contract in the fixture message and docs
if Git-visible pre-exit changes are the intended scope. If the contract is
whole-tree cleanliness, add an ignored-path check and a post-process check that
runs after coverage and other interpreter-exit writers complete.

## PWMCP-14 — repository-wide status can blame PWMCP for another subtree's writes

**Status: FIXED 2026-11-04 (48838ede3).** `git status ... -- .` from `pwmcp/`; oracle `test_write_outside_pwmcp_is_not_blamed`. (Original: Severity P2.) Filed 2026-10-30 from `reviews/pwmcp2.out`.

**Observed.** `pwmcp/conftest.py:23-29` runs `git status` from the PWMCP
directory but supplies no pathspec, so Git reports changes anywhere in the
vbpub worktree. A concurrent write outside `pwmcp/` is then reported by the
PWMCP-specific assertion at `:39-43` as a PWMCP test change.

**Proposed fix.** Add the `pwmcp/` subtree pathspec to both snapshots, keeping
the assertion's scope and wording aligned.

## PWMCP-15 — the session guard can report xdist workers' temporary writes as failures

**Status: FIXED 2026-11-04 (48838ede3).** The comparison runs in `pytest_sessionstart`/`pytest_sessionfinish` of the xdist controller only (workers return early); oracle `test_xdist_workers_do_not_trip_the_guard` runs `-n 2` with transient writes. (Original: Severity P3.) Filed 2026-10-30 from `reviews/pwmcp2.out`.

**Observed.** The current suite does not enable pytest-xdist, but the session
fixture in `pwmcp/conftest.py:33-43` takes independent before/after snapshots
in each worker. If xdist is enabled later, one worker's temporary repository
write can fall inside another worker's snapshot window and be attributed to
the wrong worker.

**Proposed fix.** Run the comparison only in the xdist controller (or one
designated worker) and add an oracle that proves worker-local snapshots cannot
overlap and misattribute a write.

## PWMCP-16 — the session guard walks and prints more repository state than needed

**Status: FIXED 2026-11-04 (48838ede3).** Pathspec-limited status with `--no-optional-locks`; failure report lists at most 20 added and 20 removed entries plus "... and N more" (oracle `test_new_file_under_pwmcp_fails_with_bounded_report`). (Original: Severity P3.) Filed 2026-10-30 from `reviews/pwmcp2.out`.

**Observed.** `pwmcp/conftest.py:23-29,39-43` walks the whole monorepo twice
per session, including nested pytest sessions, and prints complete before and
after status listings when they differ. On a large or already-dirty worktree
this adds avoidable cost, index-refresh contention, and noisy failure output.

**Proposed fix.** Use `git --no-optional-locks status` for the scoped status
query and report only a bounded set of added/removed entries with a count of
omitted paths.

## PWMCP-17 — the new top-level test is separated from the next statement by one blank line

**Status: STALE 2026-11-04 (48838ede3).** `flake8` 7.3.0 no longer reports E305 for `tests/test_ocr_service.py` (two blank lines precede the `try:` block); it did report an unused `import shutil` (F401), removed in that commit. (Original: Severity P3.) Filed 2026-10-30 from `reviews/pwmcp2.out`.

**Observed.** `pwmcp/tests/test_ocr_service.py:51-58` has one blank line
between the top-level test function and the following top-level `try:` block,
which triggers the E305 style finding and makes the import/setup section harder
to scan.

**Proposed fix.** Add the second top-level blank line or move the test below
the module's import-time setup.

## PWMCP-18 — RGB PNG color-key transparency is not composited before OCR

**Status:** FIXED 2026-11-04 (6be337905; `_flatten` now composites P/L/RGB with `tRNS`; test `test_rgb_and_gray_colour_key_transparency_is_composited_onto_white`). Severity: P2. Filed 2026-11-02 from the PWMCP R2 survivor
triage (`ocr_service.py:387`, `And->Or`).

**Observed.** The OCR service accepts PNG images and the README says transparent
PNGs are composited onto white. `_flatten` handles alpha-channel modes and
palette PNGs with transparency, but an RGB PNG with a `tRNS` color key has
`mode == "RGB"` and transparency metadata; it falls through unchanged. Its
transparent pixels can therefore reach the engine with their stored color
instead of white.

**Proposed fix.** Add an offline Pillow fixture for an RGB PNG with a
transparency color, composite it onto white before encoding the engine input,
and document the supported transparency behavior in the README, design guide,
and consumer guide. Do not add an oracle that preserves the current narrower
behavior.

## PWMCP-19 — screenshot fixtures: keep only public, agnostic ones, and prefer generated per-font alphabets

**Status:** OPEN (filed 2026-11-03; operator rule 2026-11-03). Screenshots from public, agnostic, anonymous sources (vanilla Debian or TrueNAS
installs) may be committed; the 242-frame local series stays out of git. The 24 vanilla-TrueNAS PNGs under
`tests/corpus/console/truenas-27-vanilla/` stay as the published curated set. Test inputs should prefer the imagegen generator (alphabet
screenshots per font, deterministic, no capture) over captured frames: move `tests/test_glyphgrid_perf.py` (~154-155, 20 captured frames) to
generated per-font alphabets where the measurement allows, and keep the captured frames only for what only real console output can show.

## PWMCP-20 — complete the R2 campaign waived for the r4 release

**Status: OPEN (R2 was waived for 1.64.0-r4; follow-up is pending on the btrfs runner).** The registered r2 lane covers 384 mutation candidates
on the changed targets. The latest campaign judged 56 candidates and retained resumable state; five hardening rows remain marked Pending R2, and
PWMCP-18 (`containers/ocr/ocr_service.py:387`) remains an open survivor. The r4 release already shipped after its R1 gate, so this item tracks the
separate mutation-evidence follow-up.

**Runner note (2026-11-04).** `pwmcp/assay.toml` `[lanes.r2.judge.mutation]` declares `jobs = 1`, so `run-gate r2 --jobs auto` selects N=1
(`jobs_max=1` in Run-Gate's INFO line) however many CPUs the runner has: the 384-candidate campaign runs serially. Parallel judging needs a
declared `jobs` above 1 (or `"auto"`) in `assay.toml`, which is a pwmcp decision (candidate isolation and the OCR service tests' memory).

**Close by:** resume the r2 campaign on the btrfs runner with the supported lane and parallelism, account for all candidates, triage the five pending
rows and remaining survivors, then record the final verdict and any accepted survivors here or in the linked R2 report.

## PWMCP-21 — corpus tooling should consume the per-project VM layout

**Status:** PARTLY FIXED 2026-11-04 (6be337905): `eval_glyphgrid.py` defaults derive from the script location and the docs name `pwmcp/artifacts/console-corpus` as the place to point `PWMCP_CONSOLE_CORPUS_DIR` at (unset still raises `CorpusMissing`). Still open: `console_corpus.py run --out`/verifier defaults (explicit paths) (filed 2026-11-03). Console recording moved to the VM tool: its `console record --out` and `--state-dir` both take explicit paths, and `--readonly-media` takes an explicit ISO path. The corpus side
does not yet follow: capture output and the verifier still take explicit paths, and `tools/glyphgrid/eval_glyphgrid.py` hardcodes `DEFAULT_SERIES` under a home directory.

**Proposed fix.** Keep captured frames in `pwmcp/artifacts/console-corpus/` (`frames/`, `manifest.jsonl`), derived from the script
location, and make that the documented place to point `PWMCP_CONSOLE_CORPUS_DIR` at. Do not turn it into an implicit default:
the corpus design (`docs/DESIGN-OCR-PHASE2-3.md`) deliberately has no fallback, so an unset variable must keep raising
`CorpusMissing`; any change there is a design decision, not a path derivation. Replace the `eval_glyphgrid.py` home-directory default with a
location derived from the same folder, and update the corpus README, `docs/CONSUMERS.md#local-console-corpus` and this tool's README.

## PWMCP-22 — unported groundwork from the 2026-10-27 canvas-capture session: xterm.js buffer reader, password-only login helper, intrinsic-bitmap canvas capture

**Status:** OPEN (filed 2026-11-04 while closing out the `.claude/worktrees` session worktrees). The shipped `pwmcp ocr --selector` path
(compositor screenshot of the element) and the console-capture tools on main superseded that session's `pwmcp canvas-capture` verb and its
TrueNAS/Netcup scripts; three ideas from it are not on main.

- **xterm.js text without OCR.** Web terminals that expose `window.term` (Proxmox serial consoles and node shells) hold exact text in
  `term.buffer.active.getLine(i).translateToString(true)`; reading it beats OCR and a WebGL canvas's `toDataURL` is blank. Measured on one setup only.
- **Password-only login helper.** Fill from named environment variables (values never logged or returned), submit with a real `press('Enter')`
  (spice-html5 listens for the Enter key event, not a button click). Never run against a live prompt.
- **Intrinsic canvas bitmap.** `canvas.toDataURL` (with `getImageData` fallback) returns the page-painted bitmap, which a glyph-grid reader needs;
  the screenshot path returns the element as displayed (CSS pixels). The metadata records whether CSS scaling applied.

**Source.** Commits `c827746d9`, `7c5b35beb`, `19a78ceb2` (modules `pwmcp_client/{canvas,login,terminal}.py`, tests, CLI-SPEC/USAGE/surface
entries). The branch was deleted; the commits are preserved as a git bundle at
`<checkout>/artifacts/worktree-archive-20261104/worktree-agent-ae5983017bc3b1d7e.bundle` (gitignored, local; `git fetch <bundle> <ref>`).

**Close by:** decide against the PWMCP-11 phase 2 watcher design whether the in-page bitmap read and the xterm reader belong in the watcher or as
standalone verbs, then port the wanted pieces from the bundle onto current main (the CLI surface files conflict) or drop them with a reason here.
<!-- Id note (2026-11-04): PWMCP-22 exists on branch finish-pwmcp-1-20261104 (not on main). The id below is PWMCP-23. -->

## PWMCP-23 — find the local base image behind the release's `BUILDX_BUILDER=default` bypass, or drop the bypass

**Status:** OPEN (investigation). Filed 2026-11-04.

**Problem.** The pwmcp release is documented to need `BUILDX_BUILDER=default BUILDKIT_HOST=` (environment only; `build-push.py:127` requires both variables
when building through the configured endpoint, and `docs/ARCHITECTURE.md:130` says `BUILDX_BUILDER = "mdt-managed"`). `build-push.py:115-195` supports both
paths and no committed text says why the default builder is needed. A bypass loses governance (`dev-buildkitd.slice`), the btrfs cache and the
fail-closed shim (mdt-cockpit MDT-056); on the host the shim may even remap `BUILDX_BUILDER=default` back to `mdt-managed` (MDT-057).

**Decision record (operator, 2026-11-04).** `mdt-managed` is the only builder; local bases come in as OCI-layout build contexts pinned by digest
(`--build-context <ref>=oci-layout://...@sha256:...`); a bypass is allowed only for the fresh-host bootstrap and is never selected by environment.

**Design / steps.**

1. Identify what the build needs from the local store: a `FROM <local-tag>` (a base image built by another project), a `--load` consumer, or a
   registry-auth reason. Record the finding in this entry.
2. If a local base exists: build it as an OCI layout and consume it with `--build-context`, following the tester-chain pilot (TB-14).
3. If none exists: drop the bypass from the release instructions and from the script, and run the release through `mdt-managed`.

**Acceptance.** The release procedure names no environment-selected bypass; a test (or the release dry-run) shows the build runs on
`mdt-managed`; docs updated. **Non-mdt path:** the build still works with any buildx builder (document the plain-host form).

**Dependencies and order.** Package 5 of MDT-056, after TB-14 and the cmru `build_contexts` option (KI-113).

<!-- Id note (2026-11-04): PWMCP-24..30 file the lazy/flaky-page findings ([PWMCP-LAZY-PAGE-FINDINGS-2026-11-04.md](docs/evidence/PWMCP-LAZY-PAGE-FINDINGS-2026-11-04.md), ChatGPT share test case: staged hydration, first-party assets answered 403, silent partial results). PWMCP-22 exists only on branch finish-pwmcp-1-20261104 and PWMCP-23 above is on main; check both before taking the next id. -->

## PWMCP-24 — `inspect --retries N` with a stable-DOM check (finding PW-A, F1/F8)

**Status: FIXED 2026-11-04 (004fbbe29).** `--retries N` (0..10, default 0): each attempt is a fresh context and navigation inside the same lease; a failed attempt, or text/snapshot that still changes over a 0.5 s settle window, starts the next one; every retry is reported on stderr and the last attempt is returned as it is. Tests: `client/tests/test_inspect.py` (`test_retries_*`, `test_unstable_page_is_retried_and_stable_one_is_not`). Not done here: a separate `--until-stable` flag (the settle check is part of `--retries`).

## PWMCP-25 — surface failed subresources and console errors in the `inspect` footer (PW-C, F2)

**Status: FIXED 2026-11-04 (004fbbe29).** One stderr line `page diagnostics: ...` (main document HTTP status when >=400, console error count, failed subresources counted per status, outright failed requests) whenever any is non-zero; stdout and JSON shape unchanged, `--quiet` cannot hide it. Test: `test_page_diagnostics_are_reported_on_stderr_only`.

## PWMCP-26 — warn when an `inspect` result is much smaller than the previous attempt or looks like a shell (PW-A, F1)

**Status: FIXED 2026-11-04 (004fbbe29).** Text/snapshot output under 1 KiB without `--wait-for` warns on stderr (advice, a legitimately tiny page also triggers it); with `--retries`, a result under half the size of an earlier attempt warns. Tests: `test_small_result_without_wait_for_warns`, `test_result_much_smaller_than_an_earlier_attempt_warns`.

## PWMCP-27 — `inspect --format html|embedded-json` and chunked output for results over 1 MiB (PW-B, F3/F4)

**Status: PARTIALLY IMPLEMENTED 2026-11-07 (PWMCP-42).** `inspect --format html` now returns bounded document/selector `outerHTML` or `innerHTML`, supports `--output`, and reports truncation. `pwmcp eval --selector ... --property textContent` reads embedded JSON script data without installing the Python MCP SDK. Residual: HTML is capped at 512 KiB; output above that is identified as truncated, not chunked. There is no separate embedded-JSON format, because the evaluator already reads a selected script's `textContent`.

## PWMCP-28 — `inspect --scroll-to-bottom` auto-scroll for genuinely lazy lists (PW-D, F10)

**Status:** OPEN. Severity: P3 (feature). Scroll in steps with settle waits until height stops growing, bounded by `--timeout-ms`. The reference page did not need it (no virtualisation), so write the test against a synthetic infinite-scroll fixture.

## PWMCP-29 — update the `pwmcp-fetch` skill for the completeness probe, reload loop, embedded-JSON recipe and the 1 MiB event limit (PW-F, F8)

**Status: FIXED 2026-11-07 (PWMCP-42).** The packaged skill now gives a content-based wait, explains the current `<1 KiB` shell warning and page-diagnostic stderr, reads `__NEXT_DATA__` with `pwmcp eval`, documents the bounded HTML/truncation and crash retry, and records `mcp` SDK 2.3.0's unchanged two-tuple API plus the required `browser_click.target`. `client/tests/test_skill_contract.py` checks every `browser_*` name against the version-pinned Playwright MCP tool contract and every CLI flag named in the skill against the registered parser. The previous ~5 KiB heuristic is intentionally not copied: the maintained CLI warning threshold is 1 KiB and a short page alone is not proof of missing content.

## PWMCP-30 — `inspect --main-only` readability extraction and `--user-agent` documentation stance (PW-G/PW-E, F7/F9)

**Status:** OPEN. Severity: P3. `--main-only` drops cookie banners and login chrome (about 3 KB of boilerplate in `innerText`). Document that a user agent preset is not a general fix for flaky renders (measured: it only shifted the odds); consider rotating the default only on a 403-on-assets signal.

## PWMCP-31 — the released bundle's client cannot be installed by a consumer without a cli-extended source (dstdns test-runner rebuild fails)

**Status:** OPEN. Filed 2026-11-04 from dstdns (P220 image build; dstdns@ed07ca19 base). Severity: P2 (blocks every consumer image rebuild that tracks `pwmcp-latest`).

**Observed.** dstdns `tools/test-runner/Dockerfile` installs the client with `tools/test-runner/install-pwmcp-client.py`, which runs `pip install playwright==<contract pin> <bundle>/client`. With `pwmcp-v1.64.0-r4` (the current `pwmcp-latest`) that fails: `ERROR: Could not find a version that satisfies the requirement cli-extended>=0.3.0 (from pwmcp-client) (from versions: none)`. `cli-extended` is published on GitHub Releases only (`cli-extended-latest` -> `cli_extended-0.5.1-py3-none-any.whl`), the bundle ships no wheelhouse, and `bundle/client/requirements-runtime.txt` only repeats the floor. The previous release (1.63.0-r2, still inside dstdns's running runner) had no such dependency, so the break appeared with PWMCP-02 and is invisible until a consumer image is rebuilt.

**Fix direction.** Make the consumer install recipe part of the bundle: either ship the cli-extended wheel (and its checksum) inside the bundle and have the documented install command use `--find-links <bundle>/wheels`, or document the exact `--find-links`/release URL a consumer must add, with a consumer-side test in the release gate that installs the bundle into a clean image with no index access beyond PyPI. The dstdns side (`install-pwmcp-client.py` passes no find-links) is the consumer half and is reported separately in the dstdns P220 report.

**Acceptance.** A clean `python:3.14-slim` image can install the released bundle's client with the command the bundle README documents, and the release gate fails when it cannot.

## PWMCP-32 — `inspect` returned a Cloudflare challenge page as if it were the target

**Status:** FIXED in branch `pwmcp-block-20261105` (not released). Filed and fixed 2026-11-05 from geekbench-pwmcp-REPORT.md (CP-59): `pwmcp inspect https://browser.geekbench.com/...` loaded the managed-challenge page ("Just a moment...", "Performing security verification") and printed it like content, so callers had to guess.

**Fix.** `client/src/pwmcp_client/blocked.py` signature tables (definitive vs hint signals, see README "Bot protection"); `inspect` exits 4 with an honest message, `--json` carries `blocked`/`provider`/`signals`/`status`/`final_url`/`waited_ms`, never writes `--output`, never retries, and a wait that ends on a challenge is reported blocked (with the time waited) instead of a generic timeout. `--storage-state FILE` (`storage_state.py`) reuses a human-exported Playwright session: mode 0600, scoped to the URL's host, counts only in output. pwmcp-fetch skill updated: report BLOCKED, offer the human-in-the-loop route. Non-goals held: no solver, no stealth, no fingerprint spoofing.

**Open follow-ups (P3).** (a) `pwmcp ocr URL --selector` does not check for bot protection. (b) The signature table is heuristic and unverified against live providers other than the Cloudflare page in the geekbench report; extend it with a row and a fixture when a new interstitial is seen. (c) Optional: a short grace period that lets a self-resolving interstitial clear before reporting blocked when no `--wait-for` is given.

## PWMCP-33 — the repository-cleanliness guard leaked a process tree on `git status` timeout (R2 baseline `ERROR/EXEC_FAILED leaked-process-tree`)

**Status:** FIXED 2026-11-05 on branch `pwmcp-leak-20261105` (not released). Severity: P1 (blocked every R2 campaign for 1.64.1-r2 at its R0 baseline although the suite passed: 1204 passed, 4 skipped).

**Root cause.** `pwmcp/conftest.py` `snapshot()` ran `git status` through `subprocess.run(..., timeout=)`. On a timeout `subprocess.run` kills only the direct child, so descendants (git helpers, or the `sleep` child of the wrapper script in `test_snapshot_timeout_is_reported`) were orphaned, reparented to assay's subreaper and counted as `outliving_descendants` by the process-tree check. Reproduced locally in `tester-unified:r25-e92d43b28b00` under assay's `ProcessTreeWindow` with the lane argv: `outliving_descendants=1`, comm `sleep`, ppid 7 (the wrapper `sh`). A plain `docker run` is blind to it because `--init` reaps the orphan silently.

**Fix.** `snapshot()` starts git in its own session and kills the whole process group on timeout or interrupt (`_kill_group`). Regression tests: `test_snapshot_timeout_kills_the_descendants_of_git` (fails on the old code), `test_snapshot_interrupt_kills_the_process_group`. Assay's detector is correct and unchanged.

## PWMCP-37 — the client version was a static `0.1.0` unrelated to the server release (two different wheels, one version)

**Status:** FIXED 2026-11-05 on branch `pwmcp-client-version-20261105` (not released). Severity: P2.

**Defect.** `client/src/pwmcp_client/VERSION` was a hand-set `0.1.0` that never changed, so `pwmcp --version` said `PWMCP 0.1.0` while the server release was `1.64.1-r2`, and wheels with different content (the exact `playwright==` pin changes per release) shared one version: pip could neither tell them apart nor upgrade. Nothing compared client and server.

**Fix.** Operator decision 2026-11-05: the client is versioned 1:1 with the server release and rolled out together. `VERSION` is generated by `scripts/resolve-playwright-version.py` from the release (`1.64.1-r2` -> `1.64.1.post2`, PEP 440 equal to `-r2`) via the single helper `pwmcp_client/release.py` (both directions, tested), checked by `--check` against `pwmcp.contract.json` and listed in `[project.release] commit_generated`. `pwmcp versions` prints client and server (`(server unreachable)` when not reachable); `pwmcp doctor` gained the `skew` check (different Playwright = FAIL, only `-rN` differs = WARN). Compatibility policy documented in README "Client install and upgrade" and `docs/CLI-SPEC.md`.

**Deviation / follow-up.** `pwmcp --version` and `pwmcp version` remain the shared cli-extended CLI's exact, network-free `pwmcp <version>` line (its contract test `assert_cli_contract` requires exactly that, and a network probe in `--version` would break it), so the server release is shown by the new `pwmcp versions` verb. A cli-extended hook to append a caller-provided suffix to the version line would let `--version` carry the server too: file in the cli-extended backlog if wanted.

## PWMCP-38 — a consumer that only needs the client had no supported install path (the full stack installer or a bundle by hand)

**Status:** FIXED 2026-11-05 on branch `pwmcp-client-version-20261105` (not released; the new assets appear with the next release). Severity: P2.

**Defect.** `get.py` installs the whole stack release (tree, CIU root links, wheels); a consumer that talks to a server running elsewhere had to unpack the bundle by hand. The client wheel was not a release asset, and a bare `pip install` of it fails because `cli-extended` is GitHub-Releases-only. The `/srv/pwmcp-instance` upgrade path and the follow-up `pwmcp skills install` ("N skill(s) not current") were undocumented.

**Fix.** New stdlib-only `pwmcp-client-install.py` (SHA-256 of the bundle, manifest check of every wheel, `pip install --no-index --require-hashes` into a private venv, atomic `current` and launcher links, no docker/ciu/compose), published with the client wheel (+ `.sha256` sidecars) by `scripts/publish-bundle.py`; README "Client install and upgrade" documents the asset names, the consumer path and the instance upgrade. `get.py --client-only` was NOT added: `get.py` is generated by `cmru get-py`, so a flag belongs in the cmru generator (vbpub cmru backlog), not in a hand edit of the generated file.

## PWMCP-39 — the release wheels (and so the client-only install) work on CPython 3.14 / Linux x86_64 only

**Status:** OPEN 2026-11-05. Severity: P3. The bundle's wheels are staged for one interpreter (`greenlet` cp314, `playwright` manylinux x86_64), so `pwmcp-client-install.py` and the `pwmcp-client-requirements.txt` path fail on any other Python or architecture, although the client wheel itself is pure Python (`requires-python >=3.11`). The installer now refuses early with a clear message. Fix: per-interpreter client wheels / variants (relates to cmru KI-126, variant-aware bundle build).
## PWMCP-34 - a consumer that cannot install `pwmcp_client` has no documented wire protocol; the gateway answers 403 then 428 with no hint

**Status:** FIXED 2026-11-05 on branch `pwmcp-client-version-20261105` (not released): `docs/CONSUMERS.md` "Wire protocol for a consumer without `pwmcp_client`" (headers, ceilings, 403/503/400, version rule, raw `connect()` example, reaching the instance from another ciu network); the gateway's 403/400/503 bodies and log lines name the fix (test `test_rejections_say_what_to_fix_in_the_body_and_the_log`). Finding: this gateway never answers 428 (lease headers are optional there), so the 428 in the report came from an older release or the Playwright server behind it; documented as such, no 428 added. Original report: Found migrating netcup-api-filter's e2e suite to the provided instance (`netcup-api-filter@3786024`, pwmcp 1.64.0-r4). The client is "intentionally not on PyPI", and a test runner image should not need it. The raw `chromium.connect("ws://pwmcp:3000/")` that older consumers use now fails in two steps: HTTP 403 when the connecting `Host` is not in `PWMCP_RUN_SERVER_ALLOWED_HOSTS` (an older consumer-owned template never set it), then HTTP 428 Precondition Required when the lease headers are absent. The 428 body names neither header, and `docs/CONSUMERS.md` shows only the `BrowserLease` path. The working call is `connect(ws, headers={"X-PWMCP-Lease-Seconds": "<s>", "X-PWMCP-Session-Label": "<label>"})` with a client whose major.minor equals `/contract` `playwright.python`.

**Wanted.** (1) Put the wire protocol (the two headers, their ceilings, 403/428 meanings, the Playwright version rule) in `docs/CONSUMERS.md` next to the `BrowserLease` example. (2) Make the 428 body (and a 403 log line) say what is missing. (3) Say how a consumer on another ciu network reaches the instance (join `network_name`, since the browser must also reach the consumer's app): naf joins its runners and app to `vbpub-cmqemn-network` via a required `PWMCP_NETWORK`, which works but is undocumented.

## PWMCP-40 — PWMCP-33 was incomplete: the killed `git status` wrapper's children were still adopted (as zombies) by the gate's subreaper (R2 baseline `leaked-process-tree`, round 2)

**Status:** FIXED 2026-11-05 on branch `pwmcp-leak2-20261105` (not released). Severity: P1 (same R0 baseline failure as PWMCP-33, reproduced at `pwmcp-v1.64.1-r3` on <host> twice: 1258 passed, 4 skipped, then `ERROR/EXEC_FAILED`).

**Root cause.** PWMCP-33 killed the whole process group, but a SIGKILLed wrapper and its `sleep` die together, so the `sleep` is re-parented to the nearest child subreaper: assay's process-tree window, one level above pytest. Nobody reaps it before the window closes, and assay counts every adopted process, dead or alive, in `outliving_descendants`. Evidence (assay B233 diagnostics, lane `r2` in `tester-unified:r25-d38b3324fba5` on <host>): `command_finished.leaked_descendants` = four `comm=sleep`, `ppid` = assay, empty cmdline (zombies), one per sleeping wrapper in `tests/test_git_guard.py`. The same four appear when only that file runs under a `ProcessTreeProcessRunner` (`sleep`, `sleep`, `slow-git`, `sleep`). A plain `docker run --init` hides it because init reaps silently.

**Fix.** `snapshot()` makes the pytest process a child subreaper for the duration of the query (restored afterwards; no-op off Linux), so orphans are adopted here, and reaps the adopted members of the git process group (`reap_process_group`). The setsid escapee test now reaps its escapee. The check in assay is unchanged. Regression: `test_snapshot_timeout_leaves_no_orphan_for_the_gates_subreaper` (fails on the PWMCP-33 conftest with a zombie `sleep`, passes now), which plays the gate's subreaper itself.

## PWMCP-41 — close the R2 survivor and OOM-crash findings from pwmcp-v1.64.1-r4

**Status: FIXED 2026-11-07 on branch `pwmcp-surv3-20261106` (not released).** R2 reported 29 survivors and three OOM crashes across 493 candidates. The closeout adds mutation-sensitive boundary and logging tests, and corrects the rejected-body reader fake so it honors `read1(size)` including `read1(0)`; an eight-read cap makes a non-progressing drain fail clearly. The OOM was caused by that fake returning a byte for a zero-byte read while the `or`→`and` mutant repeatedly retried it; it was not evidence that the service's configured memory envelope was too small. No lane or host memory limit changed. The hostless invalid-port origin `https://:99999` is now covered: it must be ignored without raising. Four survivors are equivalent under supported inputs and remain explicitly waived; 25 now have test evidence, including the `cli.py:369` mutant killed by the exact-`None` assertions. The three formerly crashed candidates are covered by clean test failures. The first final R1 run also exposed a load-sensitive timeout test that gave both the forced timeout and the follow-up success the same 0.4-second subprocess deadline; it now injects timeout then success deterministically, with no wall-clock dependency. A full R2 rerun was not performed; expected closeout classification is 489 killed, 4 equivalent, and 0 crashed. Per-candidate decisions and hand-mutant results: [pwmcp-surv3-REPORT.md](docs/evidence/pwmcp-surv3-REPORT.md).

## PWMCP-42 — browser crashes were misclassified and the CLI lacked page evaluation and HTML output

**Status: IMPLEMENTED; R1 PASS 2026-11-07 on `55747e192db772cd7535691c7f4c74dc8bd86210`; independent review pending; not released.** The client distinguishes browser/page lifecycle failures from endpoint connectivity, retries one recognized first-navigation crash with a stderr note, and provides `pwmcp eval` plus capped `inspect --format html` through the already exposed Playwright MCP tools using only the Python standard library. The `pwmcp-fetch` skill documents the new CLI path, MCP SDK 2.3.0 / `browser_click.target`, and has an inventory/parser drift test. Stack review found `shm_size = "2gb"`, no `--disable-dev-shm-usage`, and no PWMCP-owned memory cap on the unified browser container; the r4 R2 OOM was caused by a test fake (PWMCP-41), not the service envelope. No stack limit changed. R1 verified 2,507/2,507 lines and 802/802 branches; exact test and gate details are in `pwmcp-fold-REPORT.md`.

## PWMCP-43 — preserve main-document HTTP exit status for file and truncated output

**Status: FIXED in this branch.** Inspect and eval return the requested body or preview while reporting exit 5 for an unallowed main-document 4xx/5xx, including when inspect writes a file or eval truncates its value. Failed subresources do not become the main-document status. Regression tests cover each output path.

## PWMCP-44 — separate rendered content blocks and judge the extraction module

**Status: FIXED in this branch.** Compact list items, table cells and rows, and select options now have deterministic separators; Markdown renders table rows; text collapses paragraph source whitespace and preserves `<pre>` indentation and newlines. Markdown also separates adjacent blocks nested inside list items, table cells and links with a single space, and `<details>`/`<summary>` are blocks in both formats. `page_content.py` is included in R1/R2 line and branch coverage targets, with tests for the reviewed renderer and CLI guards.

## PWMCP-45 — link resolution ignores HTML `<base href>`

**Status: KNOWN LIMIT.** Relative links resolve against the page URL, not the document base URL. Revisit if a consumer needs page-base resolution.

## PWMCP-46 — the links format includes `javascript:` hrefs

**Status: KNOWN LIMIT.** Link extraction lists anchor hrefs without filtering schemes. Revisit if a consumer needs executable schemes excluded.

## PWMCP-47 — very deep DOMs can exceed Python recursion depth

**Status: KNOWN LIMIT.** Content extraction can raise `RecursionError` around 600 or more nested levels. No iterative traversal change is planned without a concrete consumer case.

## PWMCP-48 — `--selector` returns only the first matching element

**Status: BY DESIGN.** `--selector` selects one element; use `pwmcp eval --all` when a multi-match value is needed.

## PWMCP-49 — the `--main` fallback removes nested page-chrome tags

**Status: KNOWN LIMIT.** If no semantic main candidate exists, the fallback removes every nested `header`, `nav`, and `footer`, including tags inside article content. Revisit if a consumer needs more selective chrome removal.

<!-- PWMCP-50..53 were filed 2026-11-08 from the Scrapling comparison (docs/DESIGN-GUIDE.md "Competitors and alternatives"). They borrow ideas only; no Scrapling dependency is proposed. -->

## PWMCP-50 — agent-facing extraction keeps hidden page content (prompt-injection surface)

**Status:** IMPLEMENTED 2026-11-08 (review blockers closed; computed-style hiding remains a known limit). Severity: P2. Default `inspect --format text` reads `innerText`, which leaves out browser-hidden elements. `--format markdown|links`, and `text` with `--selector` or `--main`, parse serialized markup in `page_content.py`, which previously skipped only `script`, `style`, `noscript` and `template`. Hidden content and invisible Unicode characters could therefore reach the agent that reads the output. Scrapling's `--ai-targeted` removes these.

**What was done (2026-11-08, branch `pwmcp-50-20261108`, review-fix round).**
`page_content.extract_page` prunes before `--main` selection every element
with `hidden` (including `hidden="until-found"`), `aria-hidden="true"`, inline
`display:none`/`visibility:hidden`, or `<input type="hidden">`. Inline CSS is
parsed declaration by declaration: comments are removed, property names are
case-insensitive, `!important` takes priority, and the last declaration at
the winning priority applies. Malformed inline styles fail open as visible.
Closed `<details>` retains its first `<summary>` and prunes its body (counted as
one removed body region); `<details open>` retains its body. For a selected node,
the CLI serializes its `outerHTML` and checks its ancestor chain for those static
hidden markers before extraction, so a hidden selected node or hidden ancestor
cannot bypass filtering. The filter strips U+200B..U+200D, U+202A..U+202E,
U+2060, U+2066..U+2069, U+FEFF, and U+E0000..U+E007F. Counts remain in
`hidden_removed` and `zero_width_stripped`; the latter includes bidi controls
and tag characters for compatibility. JSON keys are additive, and stderr
prints a `content filtering:` note. Plain `text` keeps `innerText`.

Fixture `client/tests/fixtures/pages/hidden-content.html` covers the static
hidden forms, CSS declarations and false positives, closed/open details,
hidden inputs, nested hidden-in-visible and visible-in-hidden, encoded and
case/space variants, and zero-width characters. Tests are in
`client/tests/test_page_content.py` and the CLI path in
`client/tests/test_inspect.py`. The new tests also cover bidi controls and tag
characters. No opt-out flag: the content is exactly what a reader cannot see,
so exposing it by flag would reopen the prompt-injection surface.

Decisions from the review: `hidden="until-found"` counts as hidden at
extraction time because the content stays concealed until find-in-page reveals
it. U+200D remains stripped even within emoji sequences, so `👩‍💻` can display
as separate glyphs; resistance to invisible prompt injection takes priority
over preserving that joiner. Bidi controls U+202A..U+202E/U+2066..U+2069 and
tag characters U+E0000..U+E007F are stripped as additional invisible-injection
channels. These behaviors and the U+200D display effect are documented in
README, DESIGN-GUIDE, and CONSUMERS.

**What is left (known limits).** Computed-style hiding — a CSS class or
stylesheet rule that renders to `display:none`/`visibility:hidden` — cannot be
seen in serialized HTML. Marking such nodes needs a live-page evaluation
(`getComputedStyle` over the subtree, then a data attribute) before
serialization, which would add a browser-dependent step to a
fixture-testable renderer. This remains open in README/DESIGN-GUIDE/CONSUMERS
until there is a consumer case. Malformed inline style is also treated as
visible by design.

Original report: the affected formats omitted nothing the browser hid, so a
prompt-injection payload rendered invisible to a human still reached the agent
that read the output. Acceptance: a fixture page with each hidden form; the
affected formats omit them, visible content is unchanged, and the result is
covered by a CLI review case.

## PWMCP-51 — `inspect` cannot return the JSON responses behind a page

**Status:** OPEN. Severity: P3 (feature). A page's data often arrives by XHR or `fetch`; today a caller needs `eval` or the embedded-JSON route (PWMCP-27). Scrapling 0.5 records completed responses per browser session, with text bodies capped at 1 MiB each and 20 MiB in total. Proposal: `inspect --format network` that lists the main page's completed XHR/fetch responses (method, URL, status, content type, size) and, with a filter, returns bounded text bodies in the JSON envelope. First check whether the pinned `@playwright/mcp` network-request tool already covers this. Acceptance: fixture page that loads JSON by `fetch`; body caps and truncation reported as for `--format html`.

## PWMCP-52 — bounded crawl mode (deferred until a consumer needs it)

**Status:** OPEN, deferred. Severity: P3 (feature). Scrapling has a full spider framework; PWMCP reads one page per call. If a consumer needs a site section as Markdown, the acceptable shape is: same-origin only, a required `--max-pages`, serial within one lease and within the lease ceilings, robots.txt always obeyed (not optional), a per-host delay, and a stop at the first recognized bot protection (exit 4) with no retry, proxy rotation, or session switching. Without a concrete consumer case, document "one page per call" as by design.

## PWMCP-53 — opt-in selector relocation for `--selector` (deferred)

**Status:** OPEN, deferred. Severity: P3. Scrapling's adaptive tracking stores an element fingerprint (tag, text, attributes, sibling and path tags, parent) per domain and relocates the most similar element when a saved selector stops matching. PWMCP's client is stateless and its agent consumers can re-derive a selector from `--format snapshot`, so the benefit is limited to scripted consumers. If one needs it: an explicit `--selector-memory FILE` (never a hidden store), with any relocation reported on stderr and in `diagnostics` and never applied silently. Revisit only with a consumer case.

<!-- PWMCP-54..58 were filed 2026-11-10 from the Obscura comparison (checked 2026-11-09; reject adoption, borrow ideas only). No Obscura dependency or backend is proposed. -->

## PWMCP-54 — the managed browser has no destination-egress policy beyond the Docker network boundary

**Status:** OPEN. Severity: P2 (security hardening). Filed from the Obscura comparison idea "egress default-deny / SSRF guard ... checked at DNS-resolution time".

**Problem.** The browser must reach public pages and can also reach anything on the project Docker network, but PWMCP applies no destination policy. A page can attempt requests to loopback services, sibling application containers, RFC1918 addresses, link-local/metadata-style endpoints, or a hostname whose DNS answer resolves to one of those. TLS and same-origin/CORS do not prevent server-side request forgery: an attacker may infer reachability or send state-changing requests even when it cannot read the response. `docs/SECURITY.md` currently states that the Docker network is the access-control boundary; that controls who may call PWMCP, not where Chromium may connect. The compose template joins the normal project network and does not set Docker `internal: true`, because the browser needs internet egress. The comparison correctly found no egress filtering in the documented posture.

**Proposed minimal solution.** Record the threat-model decision first. The minimal accepted shape is an opt-in per-instance policy such as `egress_policy = "network-boundary" | "public-only"`; `public-only` refuses loopback, link-local, RFC1918/private, and metadata destinations at DNS-resolution/connection time, with explicit exact-host exceptions for a consumer that intentionally reads its own internal app. Do not treat the existing gateway `Host` allowlist or a URL-string denylist as a destination guard; both miss DNS rebinding and redirects. Prefer one small enforcement path at the browser launch/proxy boundary over a general policy engine. If the operator decides the project network is the intended trust zone for every deployment, close this as BY DESIGN instead of adding partial filtering.

**Where.** `ciu.compose.yml.j2` and `ciu.defaults.toml.j2` (`[pwmcp.network]`, `[pwmcp.unified]`, `[pwmcp.hardening]`), the browser launch in `containers/pwmcp/supervisord.conf` or `supervisord.shared.conf`, and `docs/SECURITY.md`/`docs/DEPLOYMENT.md` for the enforcement point and exceptions. A helper under `containers/pwmcp/` is only justified if the browser cannot enforce the policy directly.

**Acceptance test idea.** With `public-only` enabled, a fixture page that fetches a public local fixture succeeds; requests to loopback, a sibling private-network fixture, link-local, and a hostname whose DNS answer is private are refused before connection, including after a redirect; the refusal is observable in a page diagnostic and never reads as a successful HTTP response. With `network-boundary`, current behavior remains unchanged. Use local fixture DNS and fake private addresses so the test does not depend on host routing.

## PWMCP-55 — the managed browser recycles after idle/crashes, but not by memory use or age

**Status:** OPEN, deferred until a consumer or incident demonstrates a need. Severity: P3 (operations).

**Problem.** Existing recycling is intentionally idle-only: `run-server-gateway/index.js` restarts `run-server` after `idle_recycle_s` with zero leases, and shared-mode `admin-server/index.js` restarts `chromium` after `browser_max_idle_s` with zero consumer pages. Supervisord restarts crashed programs. There is no RSS/age policy for a process that remains below the crash threshold but grows across long-lived use; the container does not set a PWMCP-owned browser memory cap (PWMCP-42). The Obscura comparison's "worker/browser recycle by RSS and age" is therefore genuinely absent. Killing active clients is not an acceptable default, and the opaque Playwright gateway has no drain protocol.

**Proposed minimal solution.** Keep the single-worker design. If a concrete leak is observed, add two optional zero-disabled shared-mode settings, `browser_max_rss_mb` and `browser_max_age_s`, checked by the existing `admin-server` loop; restart only when no consumer page is open, log the measured reason, and keep the existing idle semantics once a page closes. Apply the same idle-only check at `run-server-gateway` before admitting a new lease rather than interrupting an active lease. No worker pool, connection-count recycling, or new drain machinery unless a real multi-worker requirement appears.

**Where.** `containers/pwmcp/admin-server/index.js` (shared-mode process/RSS check and existing restart path), `containers/pwmcp/run-server-gateway/index.js` (idle admission check), `supervisord.shared.conf`/`supervisord.conf` for the process name, `ciu.defaults.toml.j2`, `pwmcp.contract.json` `/contract` projections, and deployment/security docs.

**Acceptance test idea.** With fake process metrics and a fake supervisord restart callback, assert no restart below either threshold, no restart while a consumer page/lease is active, one restart when either "over RSS/age" is true and there are zero consumers, a logged threshold/reason, and unchanged behavior when either setting is `0`. Tests must inject readings, not sleep or depend on host RSS/load.

## PWMCP-56 — per-operation deadlines are client flags, not explicit contract/server limits

**Status:** BY DESIGN (closed 2026-11-10). Severity: P3 (documentation/compatibility).

**Problem.** `/contract` publishes the absolute lease ceiling but not per-operation deadlines. `inspect --timeout-ms`, `eval --timeout`, and `ocr --timeout-ms` expose operation-scoped client deadlines, while raw `connect()` callers rely on Playwright's own per-call timeouts. An `eval` HTTP timeout also does not cancel JavaScript already running in the page. The comparison lists Obscura's explicit navigation/script/CDP-command knobs as a borrowable idea, but duplicating them as claimed server limits would be misleading because the WS gateway relays opaque frames.

**Decision and minimal solution.** Do not add a policy engine or an unenforced contract limit. `max_lease_seconds` is the server-owned outer deadline for every session; navigation/selector waits and MCP requests are bounded by the existing CLI options; raw Playwright clients own their per-call timeouts and must use Playwright's APIs. `docs/SECURITY.md`, `docs/CLI-SPEC.md`, and `docs/CONSUMERS.md` already expose these controls and the non-cancelling `eval` limitation. If a future consumer has a runaway-expression case, file a focused cancellation proposal with evidence; do not infer it from the generic comparison.

**Where.** Existing behavior is in `client/src/pwmcp_client/session.py` (`timeout_ms` for contract/preflight/connect and lease headers), `client/src/pwmcp_client/cli.py` (`inspect`/`eval`/`ocr` timeouts), and `containers/pwmcp/run-server-gateway/index.js` (`lease` timer). The authoritative consumer wording is `docs/SECURITY.md`, `docs/CLI-SPEC.md`, and `docs/CONSUMERS.md`.

**Acceptance test idea.** Docs-sync should keep `default_lease_seconds`/`max_lease_seconds`, the CLI timeout flags, and the warning that an MCP HTTP timeout does not cancel in-browser JavaScript consistent with the parser and contract. No new runtime test is needed for a closed by-design item.

## PWMCP-57 — `/contract` has no machine-readable capability table

**Status:** OPEN. Severity: P3 (consumer discoverability).

**Problem.** `/contract` reports release, Playwright protocol, endpoints, lease limits, the active UA, and optionally OCR engines, but a consumer cannot discover from that endpoint which high-level capabilities and output formats this instance exposes. Today the README/CLI-SPEC/DESIGN-GUIDE tables are prose, and MCP tool discovery can describe the MCP surface only after connecting. A backend/profile or opt-in feature change therefore requires out-of-band knowledge. The Obscura comparison's "what the engine can and cannot do" table is useful independently of adopting that engine.

**Proposed minimal solution.** Add a small, additive, closed `capabilities` object to `/contract` (and the committed contract) with stable boolean/enum values for native Playwright `connect()`, the Playwright/DevTools/Lighthouse MCP endpoints, `inspect` formats, `eval`, bot-protection detection, and enabled OCR engines. It is an inventory, not a second tool schema and not a promise of behavior outside the documented contract. Keep old clients working by ignoring unknown keys, and derive dynamic values (OCR/UA/mode) from the same gateway fields already used in `/contract`; do not hand-copy them into multiple consumers.

**Where.** `pwmcp.contract.json`, `containers/pwmcp/run-server-gateway/index.js` `/contract` merge, `client/src/pwmcp_client/contract.py` optional parsing, and README/DESIGN-GUIDE/CONSUMERS plus contract-generation tests.

**Acceptance test idea.** A `/contract` fixture with OCR disabled and one with OCR enabled parses in old and current clients, advertises the actual reachable endpoints/formats, changes only the dynamic OCR fields between the fixtures, and has no capability value that cannot be exercised by an existing test or documented command. A docs-sync test compares the closed vocabulary with the CLI/MCP surface.

## PWMCP-58 — record Obscura re-evaluation triggers and the anti-detection non-goal

**Status:** OPEN, watch item only; do not adopt or spike without evidence. Severity: P3.

**Problem.** The 2026-11-09 comparison recommends rejecting Obscura as a backend now and borrowing a few operational ideas. Without an explicit watch entry, a later "lightweight browser" request could reopen adoption without rechecking protocol fit, maintenance, supply-chain, crash, and policy risks.

**Re-evaluate only if all of these hold.** (1) a consumer need for a very cheap extraction engine is documented, for example many parallel reads on a constrained host; (2) Obscura reaches a stable API or 1.0 with checksums/signatures or attestations; (3) it has at least two active maintainers or an organisation owner; (4) the SIGSEGV and Playwright-compatibility issues in our evaluation corpus are closed; and (5) the operator is comfortable with the project's stealth-feature direction. Even then, evaluate only the non-stealth build as an isolated optional profile with its own contract name; never replace the official Playwright image.

**Explicit non-goal.** Do not add stealth or anti-detection features: no fingerprint randomisation, TLS impersonation/passive-fingerprint evasion, `navigator.webdriver` or `isTrusted` masking, profile/proxy rotation for evasion, or a challenge solver. The existing overridable UA remains the limited opt-in identity-string design documented in README "Overridable user agent"; it does not solve interactive challenges, and recognised bot protection remains exit 4.

**Why.** This carries the recommendation and all five triggers from the comparison's "Re-evaluate only if all hold" paragraph, and keeps the stealth boundary aligned with `docs/SECURITY.md`, README "Bot protection", and PWMCP-32.

**Where.** This backlog entry and the existing "Competitors and alternatives" section in `docs/DESIGN-GUIDE.md`; a future evaluation report must cite each trigger rather than treating popularity or lighter claimed memory as sufficient.

**Acceptance test idea.** A docs-sync check can assert the comparison trigger list and the stealth non-goal are present as a closed set; a future adoption proposal is incomplete unless it cites evidence for every trigger and names an isolated, non-stealth evaluation path.

## PWMCP-59 — `r2` has `max_mutants = 1000` while the current selected candidate inventory is 1165

**Status:** OPEN (filed 2026-11-11); priority major (the exact release tag cannot complete an unmodified R2 campaign); source: host-campaign controller session record 2026-11-11.

**Problem.** `pwmcp/assay.toml` declares `max_mutants = 1000`, but the selected mutation inventory for `pwmcp-v1.64.1-r7` is 1165. Assay correctly refuses over-cap discovery before executing a partial sample, so every unmodified R2 campaign ends `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED` with exit 4 after paying for the baseline. The cap therefore makes the release lane unusable rather than bounding an unexpectedly large run, and there is no documented rule for choosing the next cap from candidate inventory and expected runtime.

**Evidence.** The host-campaign controller session record 2026-11-11 tested tag commit `520e9ae660dbd0462959fa5151a55d30af078339`; the exact tag reported `max_mutants = 1000`, real selected candidates 1165, `over_candidate_cap`, and verdict `BUDGET_EXCEEDED/MUTANT_LIMIT_EXCEEDED`, exit 4 after the 400 s baseline. The two temporary host-checkout commits `3d1249019` and `1a11be5bde6d2110c1336ad31e9cf4ed77346b87` changed only this cap. Repository `main` still has `pwmcp/assay.toml:106 max_mutants = 1000`. This is distinct from PWMCP-20, which tracks completion of the waived campaign; this item is the configuration defect that prevents it from starting.

**Proposed minimal solution.** Raise `pwmcp/assay.toml`'s `max_mutants` above the measured inventory with explicit headroom: at least 1200, and choose the concrete value as `ceil(expected_selected_candidates * headroom_factor)` with a documented minimum 10% headroom and a declared upper bound checked against expected lane runtime. For the current 1165 candidate set, that rule requires a final value above 1281; do not lower the cap back to the former value unless a new measured inventory and budget justify it. Document the rule beside the lane: refresh the expected inventory from a bounded plan/selection result before raising or lowering the cap, preserve a full inventory rather than truncate, and review the cap alongside `budget`, `jobs`, and per-candidate cost. No host-local edit of the judged checkout is a supported remedy.

**Where.** `pwmcp/assay.toml` (`[lanes.r2]` / `judge.mutation.max_mutants`), with the cap-choice rule in PWMCP's lane-authoring or consumer documentation and a focused config/docs contract test.

**Acceptance test idea.** A recorded 1165-candidate inventory and the chosen cap satisfy the headroom formula without an over-cap refusal; a test consumes a candidate inventory fixture and fails if the cap is at or below the inventory or below the declared 10% headroom. A control fixture with fewer candidates still passes, and a hand mutation restoring `1000` trips the oracle.

**Close when.** The repository cap is raised to at least 1200 with at least 10% measured headroom, the cap-choice rule and inventory-refresh obligation are documented, and an R2 plan/selection preflight (not a host-local `assay.toml` commit) proves the current campaign fits the cap on a clean final commit.

## PWMCP-61 — the R2 lane lets memory-runaway mutants swap instead of dying

**Status:** OPEN (filed 2026-11-12); priority should-fix; resource-only change
under Assay B529. Source: 2026-11-12 host R2 restart record.

**Problem.** `pwmcp/run-gate.toml:30-34` declares a 3 GiB lane memory limit but
no `memory_swap`, so Docker leaves swap unbounded for the lane. Re-executing
five previously crashed `client/src/pwmcp_client/cli.py` candidates around
lines 853-876 drove the container to its 3 GiB anonymous-memory cap while
continuing to use host swap. Three concurrent executions raised host memory
PSI above the operator's 25% stop threshold for roughly ten minutes. One of
the five candidates was killed, two crashed and two exceeded their per-candidate
budget under the new judge; the campaign itself did not prove a product defect.

**Minimal solution.** Add `memory_swap = "3g"` to `[lanes.r2.resources]` so the
combined memory-plus-swap ceiling equals the existing memory ceiling and a
runaway mutant is OOM-killed instead of swapping. This is an execution-resource
change only; finished records remain reusable under B529.

**Where.** `pwmcp/run-gate.toml` (`[lanes.r2.resources]`) and the lane
configuration/documentation test that pins its resource contract.

**Acceptance test idea.** The parsed lane contract requires `memory_swap` equal
to `memory`; a fake Docker argv shows the exact `--memory` and `--memory-swap`
arguments; removing or weakening the swap ceiling makes the oracle fail. A
judge-identity fixture proves the resource change does not invalidate killed or
survived records.

**Close when.** The R2 lane's combined memory-plus-swap cap is 3 GiB, the
resource-only identity behavior is pinned, and a campaign containing the five
known runaway candidates completes without sustained host memory PSI above the
operator limit.
