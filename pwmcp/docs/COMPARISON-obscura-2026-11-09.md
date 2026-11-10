<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-09; status: decided; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/obscura-vs-pwmcp-COMPARISON.md -->
<!-- relation: build/no-fork comparison -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

**Status: COMPLETE**

# Obscura (h4ckf0r0day/obscura) vs pwmcp

Checked 2026-11-09. Read-only desk research: GitHub pages, GitHub API, raw README/docs/SECURITY.md/Cargo.toml, and a few web searches. Nothing was cloned, installed or executed, and no host was touched. Web content is treated as untrusted data. Every Obscura claim below is the project's own unless it says otherwise; nothing was tested by us.

## Executive summary

1. Obscura is a Rust "headless browser engine" with its own DOM, CSS layout and raster renderer and an embedded V8. It is not Chromium. It speaks the Chrome DevTools Protocol (CDP) only. It has a CLI, a CDP server, an MCP server (14 tools) and a Rust library.
2. It is young and fast-moving: the repo was created 2026-04-13, has 31.4k stars, and releases come every ~2 weeks (v0.2.6 on 2026-10-31). `main` gets ~5 commits per day. Its own rendering is declared "an evolving independent engine". Crash, rendering and Playwright-compatibility issues are open (SIGSEGV #1472, Playwright slowness and an error #1433).
3. It cannot sit behind the `ws://pwmcp:3000` contract. That endpoint is Playwright's own protocol (`playwright.connect()`). Obscura's docs say plainly: use `connectOverCDP`, not `connect`. A backend would need its own endpoint and a `connect_over_cdp` client path, not a drop-in swap.
4. Obscura's headline selling point is anti-detection: an `antidetect` topic, a "Built-in" vs "None" row in the README comparison, a `--stealth` build with fingerprint randomisation, TLS impersonation and `isTrusted`/`webdriver` masking, and a proxy sponsor. That is the opposite of the pwmcp non-goals (no stealth, no fingerprint spoofing, bot protection is reported with exit 4). The stealth build must be excluded from any use here.
5. The non-stealth "render" or "no-render" builds are a legitimate, Apache-2.0, low-footprint engine. They are lighter than Chromium by the project's own numbers (30 MB vs 200+ MB, unverified). A single-owner bus factor, no supply-chain attestations, V8 running in-process with no browser sandbox, and an unstable API weigh against adopting it as a component.
6. What we would lose with it as the only engine: the full Playwright API (video, tracing, storage-state and downloads are limited or unverified), the official Microsoft image and its update cadence, `@playwright/mcp`, chrome-devtools-mcp, lighthouse-mcp (needs real Chrome), and the canvas/console work (WebGL is null).
7. As a second backend it would be a new product surface. It would need its own image, gateway work (CDP relay, token, Host allowlist, lease), client engine selection, a capability contract, tests and a release gate. A rough effort of 2-3 weeks of agent work plus review.
8. The benefit is narrow: cheap text/Markdown extraction for pages with light JavaScript. Our `pwmcp-fetch` skill exists for pages where raw fetch fails, and those are the pages where Chromium fidelity matters most. A cheap engine is not the bottleneck on 4 core / 8 GB hosts at `max_clients = 2`.
9. Recommendation: **(C) reject adoption now, plus (B) borrow ideas**, with re-evaluation triggers (below). No backlog item for a backend. File a few small idea items.
10. Smallest safe next step: no action is required. If the operator wants evidence, approve the disposable-container evaluation checklist below, run on a test host only, never <host>/<host>/<protected-host>, non-stealth build only.

## What Obscura is

- Rust workspace of 10 crates (dom, ssrf, net, browser, cdp, js, mcp, render, cli, obscura). Dependencies include html5ever, tokio, reqwest with rustls, and tiny-skia, taffy and ab_glyph for the render layer. V8 comes via deno_core according to the release notes; `Cargo.toml` at the root does not list it. Vendored crates under `vendor/` (taffy; cosmic-text and wreq-util excluded from the workspace). Apache-2.0. [1][2][3]
- CLI: `serve` (CDP WebSocket, optional multi-worker with recycling by connections, RSS or age), `fetch` (render one page to HTML/text/links/Markdown), `scrape` (parallel), `mcp` (stdio or HTTP). [1][4]
- Distribution: tar.gz or zip archives for Linux, macOS and Windows, in four variants (render, stealth, no-render, no-render-stealth), 39-75 MB each. A Docker image on `distroless/cc:nonroot`, uid 65532, ~57 MB compressed. Source build ~5 minutes; the stealth build needs CMake, Clang and libclang for BoringSSL. [1][5]
- A hosted "Obscura Cloud" is announced as a waitlist product; the README promises the open-source engine stays fully featured. [1]

## Maturity and ownership (GitHub API / pages, 2026-11-09)

| Item | Value | Source |
|---|---|---|
| Created | 2026-04-13 (about 7 months) | [6] |
| Stars / forks | 31,385 / 2,379 | [6] |
| Open issues + PRs | 214 (114 issues, 100 PRs) | [6][7] |
| Last push | 2026-11-09 | [6] |
| Releases | v0.1.11 (26 Jul) to v0.2.6 (31 Oct), roughly every 13-15 days. Release notes cite breaking Rust API changes in v0.2.5 and v0.2.6 | [8][5] |
| Contributors | SGavrl 1,243 commits, mnaza 95, owner account h4ckf0r0day 34, xrip 26, then 10-22 each | [9] |
| Recent activity | 35 commits in 7 days (19 on 2026-11-07), ~32 of 35 by SGavrl | [10] |
| Owner account | self-described 16-year-old, "reversing antibot and fingerprinting systems", OSINT and pentest tools; 6 repos | [11] |
| Security policy | private reporting, 3-business-day response target; no published advisories | [12] |
| Release integrity | tag commits show "verified"; no checksum, signature, SBOM or attestation files among the 20 assets (GitHub's per-asset SHA-256 digest only) | [5][13] |
| CI / tests | `.github/` and `scripts/ci/` exist; test scale, coverage and CI results UNVERIFIED | [1] |
| Star growth | ~5k in April 2026 (secondary source) to 31k now, UNVERIFIED independently | [14] |

Reading: a very active, fast-evolving project where one account (SGavrl) writes ~95% of commits and the repo owner is a different account. The relationship between them, the sponsors and the "Obscura Cloud" company is not documented (UNVERIFIED). Bus factor is effectively 1. The API is unstable, and the volume of commits per day makes review of what we would run hard.

## Security posture (their own statements)

- SECURITY.md: V8 runs untrusted page JavaScript in-process. The protections are a termination watchdog, panic guards and a default SSRF block on loopback/private/link-local. They "don't contain a hostile page that achieves native code execution through a V8 exploit"; run it inside OS-level isolation (container or VM, restricted network). [12]
- Out of scope for security reports: stealth and anti-fingerprinting behaviour ("intended design"). [12]
- Auth: `OBSCURA_CDP_TOKEN` (>=32 bytes, bearer in `Authorization`) is required for non-loopback CDP binds. MCP over HTTP needs `OBSCURA_MCP_TOKEN` on non-loopback binds and denies browser Origins unless allowed. Host-header checks and DNS-rebinding protection for CDP are not documented (UNVERIFIED). [15]
- Posture differences: Chromium has a multi-process sandbox and site isolation. Obscura has neither. Inside pwmcp's container hardening (non-root, `cap_drop ALL`, `no-new-privileges`) the blast radius of a V8 exploit is the container, the same as for Chromium without its sandbox. Its default-deny SSRF is stronger than pwmcp's documented posture (pwmcp relies on the network boundary; I found no egress filtering in its docs).
- Runtime network calls: none known beyond the page's own requests and an optional proxy. UNVERIFIED (not traced). The stealth build adds a tracker blocklist and uses bundled webpki roots instead of the system store. [16]
- Supply chain: the Rust dependency set is conventional and the release notes mention security bumps (tokio, rustls-webpki, zstd). Prebuilt binaries are unsigned (digest only); we would have to build from a pinned commit ourselves or pin a digest. A stealth build pulls BoringSSL via wreq.

## Stealth and ethics

- What `--stealth` does: browser-matching TLS ClientHello via wreq, a tracker blocklist, per-session fingerprint randomisation (GPU, screen, canvas, audio, battery), a Chrome 145 `userAgentData` profile, `navigator.webdriver = false`, `isTrusted = true`, native-function masking. Optional profile rotation, timezone and geolocation settings, and proxy support (HTTP and SOCKS5). [1][16]
- Its own limits: no help against Cloudflare interactive challenges, Datadome or Akamai active challenges, or CAPTCHAs; IP rate limits need proxies; passive TCP/IP fingerprints are out of scope. [16]
- Neither the README nor the docs say anything about ethics, terms of service, or authorised use. `--obey-robots` exists but is off by default. [1]
- Implication for us: the value proposition of the project is largely evasion of bot detection. pwmcp's stated position is the opposite: "No challenge solver, no CAPTCHA-solving service, no stealth or anti-detection patch, no fingerprint spoofing, and no dependency that does any of that", and recognised protection pages return exit 4. Adding Obscura as a dependency, even with the stealth build unused, would add a dependency whose maintainers treat evasion as the product, and it needs a security and policy review of every release. The non-stealth builds avoid the feature code at runtime but not the project direction. The 2026-11-08 Scrapling comparison reached the same conclusion for the same reason (docs/DESIGN-GUIDE.md "Competitors and alternatives").

## Capability comparison

| Capability | pwmcp | Obscura | Notes |
|---|---|---|---|
| Browser engine | Real Chromium from the official Microsoft Playwright image, pinned by digest | Own Rust engine (html5ever, taffy, tiny-skia) with V8 | Obscura README: "evolving independent engine", may differ on long-tail CSS, Web APIs, media, font rasterisation [1] |
| Native Playwright `connect()` (`ws://pwmcp:3000`) | Yes, behind a lease gateway (default 1800 s, max 7200 s, max 2 clients, idle recycle 30 s) | **No.** CDP only: "Use `connectOverCDP`, not `connect`" [17] | Biggest contract mismatch |
| Playwright API coverage | Full (real Playwright server) | Partial. Works: goto, evaluate, click, fill, locators, cookies, route continue/abort, screenshots, PDF. Not implemented or limited: video, tracing needing desktop capture, storage-state save/restore; downloads unverified; service workers, native media incomplete [17] | Open issues: #1433 (utility-world queries ~15 s and ~900 MB, `getByRole` throws), #1406 (`setInputFiles`) [7] |
| Puppeteer | Via Chromium CDP if exposed (it is not: CDP stays inside the container) | Yes, via CDP [1] | |
| MCP: Playwright tools | Official `@playwright/mcp` 0.0.80 on :8931 | Own MCP, 14 tools (navigate, snapshot, screenshot, pdf, click, fill, type, press_key, select_option, evaluate, wait_for, network_requests, console_messages, close); snapshots carry element refs since v0.2.5 [1][5] | Similar shape, not the same tool names or semantics as `@playwright/mcp` (tool list partly overlaps); no `browser_run_code`, tabs etc. listed |
| DevTools tracing MCP | chrome-devtools-mcp on :8932 | None | Needs Chromium |
| Lighthouse | lighthouse-mcp on :8933 | None | Needs Chrome |
| OCR / canvas consoles | OCR sidecar (opt-in), canvas change detection planned | Canvas partial (issue #1432: Chart.js renders black); WebGL returns null [16][7] | VNC/noVNC canvas consoles need a real browser |
| Rendered-page reading (`inspect`) | text, snapshot, bounded HTML, Markdown, links, screenshot; hidden-content filtering; closed exit codes | `fetch` dumps HTML/text/links/Markdown; native `LP` CDP domain for DOM-to-Markdown [1] | Obscura is a good fit for this narrow job on simple pages |
| Admin / lifecycle | Admin API (:8940), supervisord, idle recycle | Multi-worker balancer, 503 when none ready, recycle by connections/RSS/age, drain timeout [15] | Worth borrowing the RSS/age recycle idea |
| Hardening | non-root 1000, cap_drop ALL, no-new-privileges, shm 2 GB, internal network only | Distroless nonroot (65532) image; bearer token for non-loopback; SSRF default-deny; no process sandbox [12][15] | Both fit a hardened container; Obscura's image is far smaller |
| Bot protection stance | Detects and reports (exit 4); no evasion; UA override is opt-in identity string only | Evasion is the feature (stealth build) | See Stealth and ethics |
| Memory footprint | Chromium (~hundreds of MB per context; shm 2 GB reserved) | Claimed ~30 MB vs Chrome 200+ MB, ~70 MB binary; UNVERIFIED; Playwright issue reports ~900 MB on a large page [1][7] | Our hosts: 4 cores / 8 GB; devcontainer cap 8 GiB |
| Startup | Seconds | Claimed near instant; UNVERIFIED | |
| Release/update model | Follows Microsoft image; our `-rN` train, R1/R2 gates, 14-day age buffer | Releases every ~2 weeks, breaking changes still happening, no signed artefacts | Our freshness rule needs an age window; 0.x breaks often |
| Licence | Playwright Apache-2.0, our code | Apache-2.0 [6] | Compatible |
| Crash behaviour | Chromium crash recovered per session / `autorestart` in shared mode | Issues #1472, #1487: SIGSEGV on heavy-JS pages (medium.com), CDP connections killed [7] | Worker restart exists but sessions die |
| Persistence | Persistent profile in shared mode | `--storage-dir` (Web Storage, IndexedDB; IndexedDB correctness a known limitation in v0.2.4) [5] | |
| Observability / audit | Contract endpoint `/contract`, `/health`, logs | `--verbose`, stderr logs; no health or metrics endpoint documented [15] | UNVERIFIED if present |

## Options

### (A) Adopt as an additional backend/profile behind the pwmcp contract

- Shape: a second container (or supervisord program) `pwmcp-lite` running the non-stealth `obscura serve`, with its own alias and port, a CDP-relay gateway (we already own a Node gateway for WS bytes, Host allowlist and leases), a token, and a contract entry `engines.obscura` with a capability list. The client gets `connect_over_cdp` plus an `--engine` flag; `inspect` text/markdown/links on this engine only.
- Pros: low memory per session; fast start; useful for many simple extractions; distroless image; default SSRF block.
- Cons: not the same protocol, so the "full Playwright API via connect()" promise cannot hold for it and consumers must handle a capability matrix; two engines to test, gate and document; unsigned 0.x binaries from a one-owner project with an unstable API (we would build from a pinned commit); evasion-focused upstream; no sandbox; known crashes on heavy pages; wrong fidelity for the pages `pwmcp-fetch` exists for; cannot serve MCP, devtools, lighthouse, OCR.
- Effort (my estimate): image build from pinned source or digest-verified binary, supervisord program, gateway changes, contract/client/skill/docs changes, smoke tests, R1 coverage and R2 mutation for new client code, review: about 2-3 weeks of agent time, plus ongoing tracking of a 2-week release cadence.

### (B) Keep pwmcp, borrow ideas (recommended, small)

Candidate backlog items (each optional, none needs Obscura code or a dependency):
1. Egress default-deny / SSRF guard for the managed browser (loopback, link-local, RFC1918 and metadata addresses, checked at DNS-resolution time, explicit opt-in per instance). Obscura's `obscura-ssrf` crate is a design reference. First check what the compose network already blocks.
2. Worker/browser recycle by RSS and age in addition to idle recycle (`--worker-max-rss`, `--worker-max-age` pattern) and a drain timeout.
3. Bearer-token option on the :3000 gateway and :8931 gateway. This aligns with tls-edge FEAT-01 (Traefik forwardAuth); a token for non-loopback binds is the Obscura default.
4. Explicit per-operation deadline knobs (navigation, script, CDP command) surfaced in the contract. Obscura documents `OBSCURA_NAV_TIMEOUT_MS`, `OBSCURA_SCRIPT_DEADLINE_MS`, `OBSCURA_CDP_COMMAND_TIMEOUT_MS`.
5. Network response capture (already PWMCP-51; Obscura's MCP `browser_network_requests` and 2 MiB body buffer are another reference).
6. Documentation ideas: a "what the engine can and cannot do" capability table in `/contract` (useful regardless of backend), and the `fetch --wait`-style single-call patterns already covered by `inspect`.
Do not copy: fingerprint randomisation, TLS impersonation, `webdriver`/`isTrusted` masking, profile rotation, proxy rotation.
- Effort: each item S to M; items 1 and 3 are the most valuable.

### (C) Do nothing / reject

- Pros: no new maintenance or policy surface; keeps one engine and one contract; consistent with the Scrapling decision.
- Cons: forgoes a lighter engine for simple pages; no cost today, because Chromium footprint is acceptable at `max_clients = 2`.
- Effort: none.

## Recommendation

Reject Obscura as a component or second backend for now (C) and file the small idea items from (B) when convenient. Reasons in order of weight: protocol mismatch (CDP only, no Playwright `connect()`), policy mismatch (evasion is the project's purpose, and we deliberately have none), fidelity (own engine, open crash and Playwright issues), supply chain and bus factor (one prolific committer, unsigned binaries, breaking 0.x releases, in-process V8 without a sandbox), and low benefit on our hosts.

Re-evaluate only if all hold: a consumer need for a very cheap extraction engine is documented (for example many parallel reads on a constrained host); Obscura reaches a stable API or 1.0 with checksums/signatures or attestations; at least two active maintainers or an organisation owner; SIGSEGV and Playwright-compatibility issues in the evaluation corpus are closed; and the operator is comfortable with the stealth-feature direction. Even then, treat it as an isolated optional profile with its own contract name, never as a replacement for the official Playwright image.

## Smallest safe next step (operator approval needed; I have not run any of this)

Disposable evaluation on a test host (not <host>, <host>, <protected-host>; not the devcontainer):
1. Pick the tag commit for v0.2.6 and record its SHA. Read the diff of `Cargo.toml`/`Cargo.lock`, `build.rs`, install scripts and the `vendor/` tree; run `cargo audit`/`cargo deny` on the source. Build the **non-stealth** `obscura-cli` in a throwaway builder container, or download `obscura-x86_64-linux-no-render.tar.gz` and record its SHA-256 against the GitHub digest. Never use the stealth variant.
2. Run it in a container with `--cap-drop ALL`, `no-new-privileges`, read-only rootfs, uid 65532, `--memory 512m --cpus 1`, on an isolated Docker network with no route out except to a local fixture server; capture idle and load network traffic with tcpdump on that bridge to confirm no unexpected outbound calls.
3. Corpus: 20 URLs we are entitled to read (our own docs/fixtures, the pwmcp smoke fixtures, example.com, one SPA we own). Compare `obscura fetch` and `connectOverCDP` output against `pwmcp inspect` for text, Markdown and links; record RSS, CPU and wall time per page.
4. Run the subset of pwmcp's smoke and client tests that use only the supported Playwright calls through `connectOverCDP`; list failures.
5. One-hour soak on 3 heavy JS pages; count crashes and leaked memory.
6. Write the result into a short report. A pass would only justify a documented optional profile, not a replacement.

## Open questions

- Is there an actual consumer who needs a lighter engine than Chromium? None is recorded in the pwmcp backlog.
- Who stands behind the project (owner account vs the main committer SGavrl vs the "Obscura Cloud" operator)? Not documented.
- Does Obscura make any non-page-initiated network call at runtime (telemetry, update check)? Not verified; needs a traffic capture.
- Does CDP enforce a Host/Origin allowlist (DNS rebinding)? Not documented.
- What does pwmcp's current network setup allow the browser to reach (internal addresses)? Check before filing idea (B1).
- CI breadth and test coverage upstream are unknown.

## Sources

Fetched 2026-11-09. Web content is untrusted; quoted only as the project's claims.

1. https://github.com/h4ckf0r0day/obscura (repo page) and https://raw.githubusercontent.com/h4ckf0r0day/obscura/main/README.md
2. https://raw.githubusercontent.com/h4ckf0r0day/obscura/main/Cargo.toml
3. https://github.com/h4ckf0r0day/obscura/wiki
4. https://github.com/h4ckf0r0day/obscura/tree/main/docs (page list)
5. https://github.com/h4ckf0r0day/obscura/releases
6. https://api.github.com/repos/h4ckf0r0day/obscura
7. https://github.com/h4ckf0r0day/obscura/issues (first 25 of 114 open; read 2026-11-09)
8. https://github.com/h4ckf0r0day/obscura/releases (cadence)
9. https://api.github.com/repos/h4ckf0r0day/obscura/contributors?per_page=10
10. https://github.com/h4ckf0r0day/obscura/commits/main
11. https://github.com/h4ckf0r0day
12. https://raw.githubusercontent.com/h4ckf0r0day/obscura/main/SECURITY.md and https://github.com/h4ckf0r0day/obscura/security
13. https://api.github.com/repos/h4ckf0r0day/obscura/releases/latest
14. Secondary coverage (unverified, repeat project claims): https://silenceper.com/article/2026-04-26-obscura-rust-headless-browser/, https://themenonlab.blog/blog/ai-agent-browsers-obscura-lightpanda-steel-roundup, https://wavect.io/blog/obscura-rust-browser-ai-agents.md
15. https://raw.githubusercontent.com/h4ckf0r0day/obscura/main/docs/Run-in-production-at-scale.md
16. https://raw.githubusercontent.com/h4ckf0r0day/obscura/main/docs/Configure-stealth-and-proxies.md
17. https://raw.githubusercontent.com/h4ckf0r0day/obscura/main/docs/Use-with-Playwright.md

Our side (read): `<checkout>/pwmcp/README.md` (bot protection, overridable UA, endpoints), `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/DESIGN-GUIDE.md` (Scrapling comparison), `pwmcp.contract.json` (release 1.64.1-r7), `KNOWN_ISSUES_TODO_BACKLOG.md` (PWMCP-50..53), `CHANGES.md`, `client/src/pwmcp_client/skills/pwmcp-fetch/SKILL.md`, and the memory note `pwmcp-ocr-console-design-2026-10.md`.
