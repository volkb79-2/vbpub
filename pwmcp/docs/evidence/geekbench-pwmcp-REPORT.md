<!-- Evidence scrub 2026-11-10: environment-specific host identities, addresses, controller paths, and key material were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/geekbench-pwmcp-REPORT.md -->
<!-- relation: PWMCP benchmark evidence; verbatim source text follows -->

# geekbench-pwmcp REPORT (2026-11-04)

## Verdict
PWMCP cannot pass the Cloudflare managed challenge for Geekbench. Task 1 stopped at the challenge, as instructed. Tasks 2 and 3 were not started because they depend on a page loading. Nothing was written to any repo, no worktree was created, and no commits were made. CP-59 stays open.

## What was tried
PWMCP 0.1.0, service host `pwmcp`. The `--user-agent` option offers the presets `edge-windows`, `chrome-windows`, `firefox-windows` and `default`. About 12 page loads in total, no crawling.

| URL | UA | wait | result |
|---|---|---|---|
| browser.geekbench.com/v7/cpu/1 | default | networkidle, 60 s | Page.goto timeout (network never idles on the challenge) |
| same | chrome-windows (Chrome/156 string) | networkidle, 60 s | same timeout |
| same | default, chrome-windows | domcontentloaded | empty body text |
| same | default, chrome-windows | load, selector `body *`, 45 s, snapshot | challenge page (below) |
| same | default, edge-windows | load, selector `table, .table, article`, 100 s | selector never became visible; timed out (the challenge did not auto-resolve in 100 s) |
| www.geekbench.com/legal/ | chrome-windows | load, `body *`, snapshot | same challenge, heading "www.geekbench.com" |
| browser.geekbench.com/processor-benchmarks | chrome-windows | load, `body *`, snapshot | same challenge |
| browser.geekbench.com/v7/cpu/1 via curl, UA "Mozilla/5.0" | n/a | n/a | HTTP 403 |

Challenge text seen (ARIA snapshot, identical for every UA; only the Ray ID varies, e.g. a553626f5ca61da0):
- heading level 1: `browser.geekbench.com` (or `www.geekbench.com`)
- heading level 2: `Performing security verification`
- paragraph: "This website uses a security service to protect against malicious bots. This page is displayed while the website verifies you are not a bot."
- footer: "Ray ID: ...", "Performance and Security by Cloudflare", links to Cloudflare privacy.

Not tried, by design: solver services, stealth plugins, header or fingerprint spoofing beyond the offered UA presets. The geekbench.com pages that were not loaded: the Geekbench 7 processor-benchmarks equivalent, the benchmark-internals PDF, and the Geekbench 6 and 7 EULAs (all on the same two Cloudflare-protected hosts, so no reason to expect a different outcome; I did not spend further loads on them).

## Consequences
- No published average scores: the cgprofile score table stays empty (CP-1 / CP-59 unchanged). No value was invented.
- No EULA text: the modification / reverse-engineering quote is still missing. The operator should read https://www.geekbench.com/legal/ in a normal browser.
- No real result-page HTML: no parser fixture. HB-1's `--resolve` fallback cannot work from this cockpit, the hosts, or PWMCP; the stdout path (verified in geekbench-explore-REPORT.md on real GB7 output) remains the only usable source. No HB-1 parser patch can be proposed from evidence.

## Options needing an operator decision (not attempted)
1. A human reads the processor-benchmarks pages and the EULA in a normal browser and pastes the numbers (with URL and date) so the table can be filled.
2. Ask Primate Labs for a data feed or permission (an API or allow-listing); this is the legitimate route to automation.
3. Fill the table from our own measurements (<host> GB7 single 1673 / multi 3521, <host> 1367 / 3431 from geekbench-explore-REPORT.md), labelled as `measured on our hosts`, not as published averages. This needs a decision on whether the `cgprofile-scores/1` schema should admit a source type other than the published table.

## Backlog
No new ids filed. CP-59 should record: "PWMCP (default, edge, chrome presets, up to 100 s wait) is stopped by the Cloudflare managed challenge on browser.geekbench.com and www.geekbench.com, 2026-11-04." That edit was not made, since the brief limited repo changes to the success path.
