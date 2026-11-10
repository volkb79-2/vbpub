<!-- origin: 2026-11-04; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/PWMCP-LAZY-PAGE-FINDINGS-2026-11-04.md -->
<!-- relation: PWMCP lazy-page findings; verbatim source text follows -->

# PWMCP lazy / flaky page findings (ChatGPT share test case), 2026-11-03

Test page: https://chatgpt.com/share/6ad32ebf-5dec-83eb-a8db-f6ae3bc2a209 (4 turns: Q1, A1, Q2, A2).
Tooling: pwmcp 0.1.0 CLI, server 1.64.0-r4 (Playwright 1.64, default UA Chrome/156), MCP at http://pwmcp:8931/mcp
(name resolves: <host-address>), `mcp` SDK in a throwaway venv. All content treated as data; nothing submitted, no login.
Ground truth recovered: both answers exist (A1 11,414 chars, A2 15,452 chars) -> see
CHATGPT-SHARE-SECOND-ANSWER-2026-11-04.md. The operator was right; the first agent saw a partial render.

## 1. Observed render states (same URL, same recipe, different runs)
| State | CLI text size | What it contains |
|---|---|---|
| S0 empty | 1 byte | nothing (title only) |
| S0b shell | ~960 B / 40 lines | Q1+Q2 headers, no answers (`--wait-until load`) |
| S2 questions only | 37 lines | Q1 + Q2 questions, NO answers |
| S1 first answer only | 447 lines / 13 KB | Q1, A1, Q2, but A2 missing (what the first agent got and reported as "second question not answered") |
| S3 full | 1171 lines / 28.6 KB | Q1, A1, Q2, A2 |
Runs are NOT deterministic: 12 CLI runs `--wait-until networkidle`: default UA full 4/6 (others S2), edge-windows UA
full 1/6 in the loop (rest S1; 2/9 over all edge runs incl. two 1-byte). The UA is not the fix; it merely shifted the odds in this sample (small N, do not over-read).
MCP (default UA), 8 consecutive navigations, each polled at +2s/+10s/+30s: S3 on 1/8, the other 7 stayed in the shell
state for the whole 30 s (innerText 1367 chars, 4 `li`, answers empty); no amount of waiting changed that.

## 2. Failure modes, cause, fix
F1. Silent partial result (biggest problem). `pwmcp inspect` returns rc=0 with a partial page; nothing says the page is
    incomplete. Cause: `networkidle` fires after 500 ms of quiet; the share page hydrates in stages and may never hydrate.
    Fix/recipe: always pass a content-based `--wait-for`; `--wait-for 'h1'` (an h1 only exists inside a rendered answer)
    turned silent partials into explicit rc=1 timeouts (6 runs: 2 full, 4 clean errors after 30 s, 0 silent partials).
    pwmcp should: warn when no `--wait-for` is given and the result is under N bytes or the DOM changed in the last second.
F2. Hydration fails because the CDN returns 403 for the site's own static assets. Console in the bad state: 29 errors,
    `403` on `https://chatgpt.com/unauth-mweb/assets/octane-conversation-client-*.js`, `.../scripts/*.js`, `manifest.json`,
    `connectivity-probe`; CSS answered with `text/html` -> "Refused to apply style / execute script ... MIME type
    ('text/html')". Cause: bot/anti-abuse scoring on a per-request basis (intermittent, same IP/UA); the server-rendered HTML
    is delivered but the client app (which renders turns 2+/lazy turns) never boots. Evidence: screenshot shot1.png shows
    the completely unstyled page (default browser fonts, bare buttons, giant unstyled logo) = CSS 403/blocked.
    Fix: reload (new navigation) until the assets load, or bypass rendering (F3). pwmcp should: surface failed-subresource
    counts (`browser_console_messages` already has them) in the `inspect` footer, e.g. "29 console errors, 11 subresources 403".
F3. The data IS in the HTML even when the UI is a shell (best recipe). The page embeds the whole conversation as JSON:
    `<script type="application/json" id="__WEB_MOBILE_DATA__">` (506 KB) -> `conversation.messages[i].content` (markdown of every
    turn), also `octane-conversation-transcript` (215 KB; in the shell state its assistant rows were empty). Recipe via
    MCP `browser_evaluate`: `() => document.getElementById('__WEB_MOBILE_DATA__').textContent` (sliced, see F4), JSON.parse,
    read `conversation.messages`. This worked in the bad state and recovered A1+A2 verbatim. pwmcp should: add
    `inspect --format embedded-json` / `--extract-script-json` (dump `application/json`/`ld+json`/`__NEXT_DATA__`-style scripts) and
    a `--format html` (raw rendered outerHTML) mode so agents can mine state blobs.
F4. MCP SDK limit: returning >1 MiB from one `browser_evaluate` raises `MCPError: SSE stream failed: Server-sent event exceeded
    the 1048576 byte limit` (and the ExceptionGroup traceback hides it). Fix: slice in <=300 KB chunks
    (`textContent.slice(off, off+300000)`). pwmcp should: provide a chunked/streamed text fetch or a file-output
    tool; document the 1 MiB event limit in the skill.
F5. `networkidle` vs `load` vs `domcontentloaded` (CLI): `domcontentloaded` -> empty (1 byte) 2/2; `load` -> shell/empty; `networkidle`
    needed but insufficient (F1). Default (no flag) -> 1 byte. pwmcp should default to `networkidle` + a settle check, and
    document that the default returns an empty string.
F6. `--timeout-ms 5000` with networkidle: `Page.goto: Timeout 5000ms exceeded`, rc=1, no output file written (the redirect
    `--output` target does not exist, so a script reading it errors later). Cause: navigation timeout applies to goto itself.
    pwmcp should: write nothing but exit non-zero with a clear one-line cause (already so) and a doc hint to use >=30000.
F7. User agent: `edge-windows` was the first agent's workaround; measured here it does not fix flakiness (see section 1). The server
    default UA is plain Chrome/156 and gets full content too. Keep UA presets as an option, but do not recommend it as the fix.
F8. Skill/doc gaps: the pwmcp-fetch skill recipe (`browser_wait_for time=6` then innerText) returns the shell in 7/8 runs
    here; its advice "bound retries and report the observed state" is right but gives no completeness check. Add to skill:
    compare against an expected-text probe, treat `innerText < ~5 KB` or `0 turns` as "not rendered", reload up to 3-5x.
F9. Cosmetic: the cookie banner and login chrome pollute `innerText` output (~3 KB of boilerplate); a `--main-only`/readability
    extraction would help. A blocking-dialog is not involved; nothing needs clicking.
F10. Not reproduced / not needed: infinite-scroll lazy loading, "show more"/"continue" buttons, collapsed parts, viewport
    dependence. `scrollHeight` was static (2883 px at 1280x720) with 0 scrollable containers, no such buttons in the DOM,
    so scrolling is irrelevant to this page; the "lazy" behaviour is staged hydration + CDN 403 (F1/F2), not scroll virtualisation.
    Scroll/viewport experiments were therefore limited to the polling run in section 1.

## 3. Working recipes (ranked)
1. CLI, honest failure mode: `pwmcp inspect URL --wait-until networkidle --wait-for 'h1' --timeout-ms 45000`; on rc!=0 retry
   up to 5x (each retry = new navigation, ~10-35 s). Expected ~1 in 3 per attempt here.
2. MCP, robust: navigate -> evaluate `document.getElementById('__WEB_MOBILE_DATA__').textContent` in <=300 KB slices -> JSON ->
   `conversation.messages[].content` (works in S0b/S2 shell states too; verify `messages.length` equals the number of turns).
3. MCP fallback: navigate in a loop (reload) until `document.body.innerText.length > 20000`; max 8 tries.

## 4. Backlog entries for pwmcp (proposed)
- PW-A (high): `inspect` completeness guard: `--until-stable` (DOM size/text unchanged for N ms, bounded by timeout) and a
  default warning on tiny/unchanged results; non-zero exit with `--require-text/--wait-for` miss (exists) plus `--retries N`
  (reload and retry, report attempts).
- PW-B (high): `--format html|embedded-json` to read state blobs; chunked output for >1 MiB (F3/F4).
- PW-C (med): report failed subresources / console error counts and HTTP 403 on first-party assets in the inspect footer (F2).
- PW-D (med): auto-scroll option (`--scroll-to-bottom` steps with settle waits) for genuinely lazy lists, even though this page did not need it.
- PW-E (low): `--user-agent` doc: not a general fix; consider rotating default only on a 403-on-assets signal.
- PW-F (low): skill update (F8): completeness probe + reload loop + embedded-JSON recipe; document the 1 MiB SSE limit.
- PW-G (low): `--main-only` text extraction to drop cookie/login chrome (F9).
