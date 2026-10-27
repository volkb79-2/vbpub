# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

### Added
- pwmcp: OCR sidecar, phase 1 of PWMCP-11 (opt-in, off by default). A second stateless container (`containers/ocr`, Tesseract 5 behind a small stdlib HTTP service; `GET /health`, `GET /v1/engines`, `POST /v1/ocr`) joins the stack when `pwmcp.ocr.enabled = true`, reachable only on the stack network as `pwmcp-ocr:8941`, with an 8 MiB / 4096x4096 / 20 s / one-at-a-time limit set, a pluggable engine interface (`tesseract` now; `glyphgrid` registered but 501 until phase 2), read-only rootfs and a 512m cap. The gateway `/contract` gains an `ocr` block (`url`, `engines`; absent or empty on servers without the feature, and `PwmcpContract` gains `ocr_url` / `ocr_engines` with empty defaults). New `pwmcp ocr` verb (`--image PATH|-`, or URL plus `--selector` / `--region` / `--wait-ms`; `--engine --lang --psm --scale --whitelist --min-conf --sidecar-url --format text|json|tsv --output`) and `pwmcp_client.ocr.recognize()`. Returns text and confidence, never decisions; no watching and no key sending. The same pwmcp release now builds, pushes and records both images: a `pwmcp-ocr` bake target in the `all` group publishes `ghcr.io/volkb79-2/pwmcp-ocr:<same release tag>` (and `latest`), `[pwmcp.ocr.image] tag` is kept equal to the pwmcp tag by `scripts/resolve-playwright-version.py`, GHCR visibility is synced for both packages, and the OCR image's `debian:trixie-slim` base is pinned by digest (`DEBIAN_RELEASE` / `DEBIAN_IMAGE_DIGEST`, refreshed and checked by the same resolver; `cmru.vars` now requires them). `pwmcp.contract.json` and `get.py` are unchanged. Confidence is NOT calibrated (a garbled TrueNAS dialog read at 91% mean confidence; `10.0.2.15` came back as `16.0.2.15`): without `min_conf` nothing is assessed and `low_conf`, `low_conf_words` and `min_conf_applied` are null, never false/0; an unscored word is kept with `conf: null` and counted in `unscored_words`; parameters are validated before the recognition slot is taken; a missing optional test dependency fails per test, not at collection. The sidecar result is complete: `min_conf` flags words below it (`low_conf`, per-line `conf_mean`/`conf_min`/`low_conf_words`, top-level `low_conf_words`/`min_conf_applied`) and never deletes them (`drop_low_conf=1` is the explicit opt-in); `conf` stays as an alias of `conf_mean`. The Debian base has no age window by design (decided 2026-10-27). The OCR image exists from the NEXT release after `1.64.0-r3`; until then enabling `pwmcp.ocr` needs a local build (README). The pwmcp gate lanes set `PWMCP_REQUIRE_TEST_DEPS=1` and judge the sidecar, so the tester image must carry the pwmcp-client `test` extra (Pillow, websockets, jinja2, PyYAML) plus node and sh: tests needing an optional dependency now FAIL when it is missing (skipping is the opt-in, `PWMCP_ALLOW_MISSING_TEST_DEPS=1`), through one shared helper (`client/src/pwmcp_client/user_agent.py` is now a judged 100% target too); the real-Tesseract test skips explicitly unless `PWMCP_REQUIRE_TESSERACT=1`. No release was cut and nothing was built or run against real Tesseract.
- pwmcp: overridable user agent (PWMCP-09, opt-in, off by default). Config key `user_agent` (`PWMCP_USER_AGENT`): presets `edge-windows`, `chrome-windows`, `firefox-windows`, or a custom string; overrides the UA string and its matching client-hint brands (no other fingerprint surface). `pwmcp inspect` reports `user_agent` / `user_agent_source` whenever an override is active (nothing is added in default mode) and gains `--user-agent` for one call; `/contract` and the shared-mode admin health report the active value. With no override `pwmcp inspect` output is unchanged (stdout, bare `--json` string, empty stderr); only when an override is ACTIVE does `--json` print an object with the identity fields and a stderr line appear (not silenced by `--quiet`). Typo'd presets are rejected rather than taken as a custom UA. Not verified against a live Anubis deployment.

<!-- cmru: release history -->

## [1.64.0-r3] - 2026-10-21
<!-- cmru: generated -->
<!-- cmru: source-end=ff75374eeed6972871a879a4672fb8f6534888d2 -->

### Added
- feat(run-gate): make wheel SemVer the only supported interface (9a0e192ab)

### Fixed
- fix(pwmcp): re-render get.py without the legacy-name fallback; record the PWMCP-08 decision (releases <= v1.64.0-r2 stay uninstallable by get.py) (0475fcd75)
- fix(pwmcp): re-render get.py with the bounded legacy-name fallback; fix stale bundle names in DEPLOYMENT.md and build-bundle (PWMCP-07) (e7a3b51be)
- fix(pwmcp): publish the bundle under the contract name pwmcp-v<version>.tar.xz (PWMCP-07) (98a545c1a)

### Changed
- backlog: PWMCP-07 / KI-89 status after merge (fixed on main, not released) (716ceb514)
- backlog: file PWMCP-07 / cmru KI-89 (pwmcp get.py cannot download its own release asset) (42251319c)
- pwmcp: annotate the stale cmru KI-70 references as KI-83 (review A-05) (12bd44516)
- INT-01 amendment review of steps 1-3: fold duplicate KI-85 into KI-83 (3365d8de2)
- Merge main (b3277b36e) (INT-01 step 3) (4d7400c15)
- Merge cgprofile-v1.0.0 (a27fd282f) into estate-integration-20261019 (INT-01 step 1) (12d774477)
- Merge main into CGProfile release candidate (f4933a2d5)
- pwmcp: file PWMCP-03 (pwmcp-fetch skill examples hard-code a stale dstdns ciu instance id) (63a58e002)
- chore(gates): declare mutation resource ceilings (da06e52de)
- chore: integrate contract-2 release wave (336336d81)

### Documentation
- docs(pwmcp): curated [Unreleased] note, get.py downloads only contract-named assets; drop 'fallback trust' from PWMCP-07 (PWMCP-08) (80b9ef760)
- docs(pwmcp): record websocket recycle failure (b3277b36e)

<!-- cmru: pending release notes begin -->
### Changed
- The release bundle is published as `pwmcp-v<version>.tar.xz` (PWMCP-07), and `get.py` downloads only contract-named assets (`<tag><asset_suffix>`), with no legacy-name fallback; a release published under another name, i.e. pwmcp releases <= v1.64.0-r2, is not installable by `get.py` (PWMCP-08).
<!-- cmru: pending release notes end -->

## [1.64.0-r2] - 2026-10-17
<!-- cmru: generated -->
<!-- cmru: source-end=0859edf9b96489ef032f6f6b4d9cb1cd1147246f -->

### Fixed
- fix(pwmcp): keep fetch skill install agnostic (948080489)

## [1.64.0-r1] - 2026-10-17
<!-- cmru: generated -->
<!-- cmru: source-end=0f9b1cc6a3fb7d74018d3f5f7cf8c5fddaf5c69d -->

### Added
- feat(pwmcp): add opt-in inspect TLS bypass (849129bfc)

### Fixed
- fix(pwmcp): use cached pinned images during deployment (df42e0602)
- fix(pwmcp): preserve instance override across installer updates (29280b8a2)
- fix(pwmcp): make standalone instance start with ciu up (9833e8386)
- fix(pwmcp): include all offline installer wheels (504c0e7ef)

### Changed
- pwmcp: guide agents through late page hydration (6b5b57382)
- pwmcp: update CLI review browser mock (20246a50c)
- pwmcp: wait for repeated visible content selectors (0c879d420)
- PWMCP select Playwright 1.64.0 with two-day age window (fb34492cc)
- chore(pwmcp): select release version r4 (8e88d50bf)
- pwmcp: clear idle timer during gateway shutdown (f335b26b1)
- pwmcp: allow remote CLI through run-server gateway (94fbb9e13)

### Documentation
- docs(pwmcp): document MDT CLI launcher path (ceb57e542)
- docs(pwmcp): record inspect TLS acceptance (81c5fcd36)
- docs(pwmcp): record local inspection acceptance (b4610ee4e)

### Testing
- test(pwmcp): parse annotated wheel projection (93d3c016a)

## [1.63.0-r3] - 2026-10-16
<!-- cmru: generated -->
<!-- cmru: source-end=8d372647e02c88e0213c55daa191a7a9896e2f86 -->

### Added
- feat(pwmcp): reviewed CLI surface, pytest plugin, docs and orchestration edge (PWMCP-02) (7609d7078)
- feat(pwmcp): release-step scripts on the installed cli-extended and cmru (PWMCP-02) (d51d44482)
- feat(pwmcp): pwmcp client CLI on cli-extended (PWMCP-02) (eb2505586)
- feat(pwmcp): modernize pinned MCP stack and projection checks (553f00d8a)
- feat: add estate cli version compatibility (05f373a40)

### Fixed
- fix(cmru): W2-INTEG review round 1 (C1-C7, run_step removal) (508bc83d6)
- fix(pwmcp): enforce stream-only MCP gateway (c488571a8)
- fix(pwmcp): use managed remote BuildKit service (0d2fb1326)
- fix(pwmcp): enforce Playwright base image digest pin (0198a5237)
- fix(pwmcp): make prepared vars loader report missing files (adff180b0)
- fix(pwmcp): fail closed on resolver template drift (f020f8040)
- fix(gates): correct release and VM lane contexts (a4bba60db)

### Changed
- pwmcp: record R1 gate evidence (bc33901ba)
- pwmcp: cover missing Dockerfile version arg (d7caee83c)
- pwmcp: provide archive test temp path (4c284a9d9)
- pwmcp: isolate bundle archive test (75191985b)
- pwmcp: verify page inspection and version projections (cd0e34041)
- Add standalone PWMCP instance and inspect CLI (dc4072cf2)
- chore(pwmcp): clear smoke script lint warning (6cb99c810)
- cli: universalize vbpub parser diagnostics (aa0e69fad)
- chore: land run-gate root and dev-gates migration (41c1cafb3)

### Documentation
- docs(pwmcp): record second r1 gate run at the report tip (e76070db3)
- docs(pwmcp): PWMCP-02 report, backlog FIXED, program row 5; file cmru KI-70 [now KI-83] and CLI-EXT-32 (194f01e1d)
- docs(cli-extended): file planned adoptions in each tool's backlog (W10) (c8a56ad8f)
- docs(pwmcp): add PWMCP-01 (665246456)
- docs(pwmcp): record final external gate evidence (79e2efd7e)
- docs(pwmcp): document stream-only MCP gateway (12334e05d)
- docs(pwmcp): record managed builder review (9915628bb)
- docs(pwmcp): align release commands with cmru (de6fbddef)
- docs(pwmcp): update modernization completion criteria (159074769)
- docs(pwmcp): record digest and external gate status (b45992a5f)
- docs(pwmcp): adopt project-scoped version targets (9bd64ff0a)
- docs: formalize version target schema and backlog name (99d460ec1)
- docs(pwmcp): centralize modernization and disable legacy SSE (afe9f7a9b)
- docs(pwmcp): record devtools MCP security changes (ffb41179d)
- docs(pwmcp): note npm dependency reproducibility gap (2d9283b03)
- docs(pwmcp): record resolver hardening findings (204803505)
- docs(pwmcp): review coverage and upstream compatibility (10e08b8c8)

### Testing
- test(pwmcp): assert builder diagnostics under mutation (d770556cc)
- test(pwmcp): cover ignored builder inspection lines (8131dbabd)
- test(pwmcp): match managed builder endpoint in entrypoint fixture (aefa29917)
- test(pwmcp): close remaining mutation gaps (0f3f04067)
- test(pwmcp): correct external mutation oracles (784457397)
- test(pwmcp): harden mutation oracles (87c79ea90)
- test(pwmcp): restore projection after refusal probe (7baef4502)
- test(pwmcp): cover digest projection safeguards (fd5ff337b)
- test(pwmcp): close resolver defensive branch (da7b517f9)
- test(pwmcp): cover resolver age and projection refusals (f68061e45)
- test(pwmcp): cover script entrypoint and refusal paths (43a45714b)
- test(pwmcp): repair resolver refusal fixtures (a6480ccf1)
- test(pwmcp): cover remaining release branches (b5f4472fb)
- test(pwmcp): cover release and client branches (c257109ce)
- test(pwmcp): adopt assay R1 and R2 gates (f23a42237)

## [1.62.0-r4] - 2026-09-21
<!-- cmru: generated -->

### Changed
- Update eligible Playwright/MCP/Lighthouse dependencies and lock the vendored Lighthouse dependency tree.
- Make release preparation validate the committed version projection; upstream refresh is explicit.
- Require Streamable HTTP `/mcp` endpoints and keep legacy `/sse` disabled.

## [1.63.0-r2] - 2026-09-18
<!-- cmru: generated -->
<!-- cmru: source-end=3565200ecf3ce69ba8845254704b2e86ee75549e -->

### Changed
- Release metadata prepared by CMRU.

## [1.62.0-r3] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=c92b522d30b98cad4813f0c1c119e3e7d18498fd -->

### Fixed
- fix(nyxloom+pwmcp): nyxloom-P103 -- declare [governance] on both ciu roots, retire fictional nyxloom.slice (c703cd04)

### Changed
- pwmcp,topos: adopt $INSTANCE_ID/$DOCKER_NETWORK_INTERNAL naming (CIU-104 mitigation) (28a70b24)

### Documentation
- docs(run-gate): RG-13 adoption hygiene + estate budget↔timeout sweep (df5c9c10)

## [1.62.0-r2] - 2026-08-22
<!-- cmru: generated -->
<!-- cmru: source-end=9b6cd337bf199799fd147a8cc8acad52981018e1 -->

### Added
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)
- feat(cmru)!: enforce strict release framework (8dd0e416)

### Changed
- run-gate: estate-wide adoption as SSOT test definition (CIU-40 adoption half) (4c6eb2b6)
- cmru: centralize estate release policy (2281181a)
