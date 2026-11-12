# PWMCP design guide

The release gate uses source-backed Assay through `run-gate`. Run-gate owns
the tester-unified container, mounts, cgroup placement, and resumable progress
plumbing; Assay owns the test command, branch coverage, and mutation judgment.
Keeping those responsibilities separate lets the same lane run in a release or
as an operator-selected R2 investigation.

The R1 lane uses Hypothesis for the pure upstream-version intersection rule.
PWMCP has no OpenAPI surface, so Schemathesis would add no evidence and is not
part of the lane.

## Standalone CIU root and installer

PWMCP can run as a sub-stack in a consumer's CIU root or as an independent
`/srv/pwmcp-instance` root. The standalone release carries its own CIU defaults
under `standalone/`; the CMRU adapter exposes those files through stable links
to `current/tree`. This lets `get.py update` atomically switch the release
while CIU continues to find the root configuration. The adapter seeds the
standalone network override as a local file once and preserves it across
updates, avoiding a brief dangling link during the installer's current switch
and retaining site edits. The standalone global
defaults register the root directory as the one default deployment phase, so
plain `ciu up` selects PWMCP. Its sparse stack override marks the devcontainer's
generated internal network as external: the standalone root joins that network
and does not try to create or own it. Keeping these installed-root choices
separate preserves the existing sub-stack and maintainer-build policies, where
the consumer or maintainer owns network and resource governance.

The image tag is a prepared version coordinate, so Compose uses a matching
local image and pulls only when the host cache lacks it. This keeps cold
deployments self-provisioning while allowing a locally built, not-yet-published
image to serve the standalone acceptance run.

## Run-server host validation

The run-server gateway listens on the internal service port while Playwright's
actual server stays on loopback. It accepts the service name, CIU container
name, configured consumer aliases, and (when external routing is enabled) the
configured public hostname. Unknown `Host` values are rejected before an
upstream connection is opened. For accepted requests, the gateway rewrites
`Host` and `Origin` to the loopback upstream address because Playwright rejects
the container DNS name even though the gateway deliberately exposes that
service to its CIU network. Rewriting only after the allowlist check retains
hostname validation without changing the established unauthenticated,
internal-network access policy. See [security controls](SECURITY.md#internal-mode-access-control).

## Client version projection

The client wheel is versioned 1:1 with the server release: `client/src/pwmcp_client/VERSION` is generated
by `scripts/resolve-playwright-version.py` from the release (`1.64.1-r5` -> `1.64.1.post5`, the PEP 440
spelling of the same string) through the one helper in `pwmcp_client/release.py`, which the resolver loads
by path and the CLI imports. `--check` fails when the file differs from the mapped contract release, and the
file is listed in `[project.release] commit_generated` beside the other generated client projections.
`pwmcp doctor` compares client and server through the same helper (same Playwright major.minor = compatible, a different
patch or r-number = WARN, a different major.minor = FAIL).

## Client dependency projections

The client wheel declares its runtime dependencies directly in
`client/pyproject.toml`, so package metadata and CLI adoption checks see the
same requirements. `client/requirements-runtime.txt` mirrors that list for
offline wheel staging; the version resolver updates the exact Playwright pin
in both files and refuses release preparation if they drift.

CMRU's generated `get.py` installs the release-bundled wheels into a private
environment with hashes from the bundle manifest and no package-index access.
The bundled `pwmcp-client` and Playwright wheel are built from the same
Playwright version recorded in `pwmcp.contract.json`; the image and CLI
therefore share the contract's wire-protocol version. The installer launcher
exposes `pwmcp` from the installed release.

The MDT image keeps that entrypoint at `/srv/pwmcp-instance/bin/pwmcp`, a
stable link to the current release's virtual environment, and its devcontainer
configuration exposes that directory on the agent process's PATH. The packaged
skill uses the `pwmcp` command without assuming where a consumer installed it;
each environment owns CLI discovery and MCP endpoint configuration. This keeps
the skill portable while keeping command discovery separate from the service
network.

MDT releases depend on PWMCP and embed a commit-pinned installer plus contract
hash in the image build inputs. This supplies an immediately available
`/srv/pwmcp-instance` without restarting or rebuilding the container to start
the service. Inside the instance, no host ports or authentication are added;
the existing internal Docker network remains its access boundary. See the
[consumer install and update examples](CONSUMERS.md#install-and-update).

## Remote page inspection

`pwmcp inspect` is a thin command over `BrowserLease`, the same managed client
used by Python consumers. It validates the server contract and Playwright
version, opens the page in the service's remote browser, and closes the lease
on exit. This keeps browser binaries and browser process cleanup in PWMCP while
giving an operator text, accessibility snapshot, or screenshot output from one
command. `--wait-until` follows Playwright's navigation states, and
`--wait-for` waits for a visible selector before reading the rendered page.
Some single-page applications render useful content after the navigation event,
so consumers can select `networkidle` and wait for a page-specific content
selector. A selector can match repeated elements, as conversation message
selectors do; PWMCP waits for the first visible match instead of treating
multiple matches as an error. This keeps the wait tied to rendered content
without adding a fixed delay to every inspection. See the
[dynamic page example](CONSUMERS.md#inspect-a-webpage).

### Rendered content formats

`inspect --format text|markdown|links` reads the rendered DOM. A caller may
select one CSS element with `--selector`; `--main` chooses a semantic `main`,
then `[role=main]`, then `article`. When none exists, it reads the body after
removing `header`, `nav`, and `footer` elements. The two selection options are
exclusive so the selected region is clear.
Markdown uses a small deterministic HTML-to-Markdown renderer that keeps
headings, ordered and unordered lists, tables, inline and fenced code, and
links. Table cells use ` | ` and rows use newlines. Text collapses source
whitespace in normal paragraphs while preserving `<pre>` indentation and line
breaks. The links format emits one label and absolute href per line. Relative
hrefs are resolved against the page URL before output. Keeping this conversion
in the client makes fixture tests independent of a network or a live browser.

#### Known limits

The renderer keeps its traversal and URL rules small and predictable. As a
result, it resolves links against the page URL and ignores `<base href>`;
includes `javascript:` hrefs in link output; and can raise `RecursionError` on
very deeply nested DOMs (around 600 or more levels). `--selector` returns only
the first match. If `--main` finds no semantic candidate, its fallback removes
nested `header`, `nav`, and `footer` elements too. These cases are recorded in
`KNOWN_ISSUES_TODO_BACKLOG.md` (PWMCP-45 through PWMCP-50).

Hidden-content filtering (PWMCP-50) removes content a reader of the page
cannot see, because the caller is usually an agent that would treat hidden
text as page content. It drops elements with the `hidden` attribute (including
`hidden="until-found"`), `aria-hidden="true"`, inline `display:none` or
`visibility:hidden`, and `<input type="hidden">`, with their whole subtree.
Inline declarations are parsed after comments are removed; property names are
case-insensitive, `!important` takes priority, and the last declaration at the
winning priority applies. If a style is malformed, the parser recovers complete
declarations independently and applies that same cascade among them. It omits
the element if a recovered winner or a best-effort tokenized malformed fragment
contains either existing inline hiding marker, `display:none` or
`visibility:hidden`. A malformed style with neither marker remains visible.
An unescaped LF, CR, or FF inside a CSS string ends that malformed string so
later declarations can be recovered. CSS escapes in property names and hiding
keyword values are decoded, because escaped identifiers still name the same
CSS property or value (`d\69splay:none`, `display:\6e one`). Tokenization
ignores string and nested function contents, and an unclosed comment's
remainder is checked as a malformed fragment. This keeps an earlier valid hide
from disappearing behind a later parse error without hiding every element
that has a stray malformed declaration. Closed `<details>` keeps its first
`<summary>` and removes its body; open details keep their body. When
`--selector` selects a node, PWMCP serializes that node's `outerHTML` and checks
its ancestor chain for the same static hidden markers, so attributes outside
the selected fragment cannot bypass filtering.

The filter strips U+200B..U+200D, U+202A..U+202E, U+2060, U+2066..U+2069,
U+FEFF, and U+E0000..U+E007F. This includes bidi formatting controls and
Unicode tag characters as invisible-injection channels. The existing
`zero_width_stripped` count includes all of these code points. U+200D is also
removed inside emoji sequences, so a joined emoji such as `👩‍💻` can render as
separate glyphs; this is an intentional security choice. `hidden="until-found"`
is treated as hidden at extraction time because its content is concealed until
the user invokes find-in-page.

Pruning happens before `--main` selection, so a hidden `main` or `article` is
never chosen, and the counts are reported in the result (`hidden_removed`,
`zero_width_stripped`) and in a stderr note rather than silently.

The filter is deliberately static, and that is its recorded limit: hiding from
a CSS class or stylesheet rule is not visible in serialized inline markup. An
element hidden only by computed style can still reach the caller. Marking those
nodes would need to inspect computed style on the live page and annotate it
before serialization, which would put a browser-dependent step in a renderer
whose whole point is fixture-testable determinism. It is therefore documented
as a known limit (README "Known limits", PWMCP-50) instead of half-implemented.
Plain `text` reads Playwright's `innerText`, which the browser itself computes
from rendered visibility, so it already omits hidden elements; the filter runs
for `markdown`, `links`, and `text` with `--selector`/`--main`, which parse
serialized markup.

Every `inspect --json` result has the same envelope: `content`, `diagnostics`,
`format`, `http_status`, and `http_error`. This avoids making a no-diagnostics
response a JSON scalar while a warning changes it to an object. Formats that
parse markup add `hidden_removed` and `zero_width_stripped` (which also counts
bidi formatting controls and Unicode tag characters); formats that never run
the filter omit those keys rather than report a measured zero. When
`--output` writes a file, `content` is `null` and `output` identifies the file.
`eval --json` uses the same envelope, while eval without `--json` continues to
print the evaluated JSON value directly.

The native Playwright path takes the main-document status from the navigation
response and the latest main-frame response event. The MCP-backed HTML and eval
paths read Chromium's Navigation Timing `responseStatus`. A main-document 4xx
or 5xx keeps the response body available and exits 5 so scripts can distinguish
an error page from a successful page. `--allow-http-error` opts into exit 0
when the caller expects that body; the JSON still reports `http_error: true`.
Recognized bot protection in `inspect` remains exit 4 and takes precedence
over the HTTP error code. `eval` reports the HTTP status and does not classify
bot-protection pages.

### Client exit codes

The closed exit-code table for `pwmcp inspect` and `pwmcp eval` is:

| Code | Meaning |
|---:|---|
| 0 | Inspection or evaluation completed; includes an expected HTTP error when `--allow-http-error` is set. |
| 1 | Runtime failure, such as an unreachable service, browser error, or output failure. |
| 2 | Invalid command usage. |
| 3 | PWMCP client command is not installed. |
| 4 | `inspect` recognized bot protection; the page is treated as a challenge, not the requested target. |
| 5 | The main document returned HTTP 4xx or 5xx. |

`inspect` returns exit 4 for recognized bot protection, taking precedence over
exit 5. `eval` does not classify bot-protection pages; it uses the HTTP status.

### MCP-backed evaluation and bounded HTML

`pwmcp eval` and `inspect --format html` use the existing `browser_navigate`
and `browser_evaluate` tools from the pinned `@playwright/mcp` server. PWMCP
adds no new server tool or gateway permission: arbitrary page evaluation is
already part of the MCP tool surface. `eval` is an explicit caller-supplied
JavaScript capability for reading values from the page; HTML extraction sends
only fixed page-inspection code. Both speak the small Streamable HTTP subset
they need (`initialize`, `tools/list`, `tools/call`, and session close) with the
Python standard library. This avoids making the separately installed `mcp`
SDK a dependency of the client wheel, while the tests exercise JSON and SSE
responses against a local fake MCP endpoint.

HTML is encoded in the browser before its response leaves the page context and
is capped at 512 KiB of UTF-8 source bytes. That bounds the MCP response itself
and provides an exact included-byte count; a byte boundary is shortened to the
last valid UTF-8 sequence. The HTML format probes the title and bounded body
text with the same existing block detector before returning markup, so a
recognised challenge remains exit 4 and is never written as target HTML. The
probe reads the main-document status where Chromium exposes it through
Navigation Timing; browser JavaScript cannot read arbitrary response headers,
so HTML-mode detection has fewer bot-protection signals than native `inspect`.

`eval` measures the UTF-8 size of the compact JSON representation returned by
the page. The page function runs `JSON.stringify` and checks the encoded byte
length before MCP sends the value over HTTP. It allows 1 MiB by default;
`--max-bytes` sets a limit from 1 byte to 4 MiB. When the result exceeds the
limit, text mode prints only the JSON preview to stdout and writes an explicit
truncation marker to stderr. An HTTP 4xx/5xx still exits 5 unless
`--allow-http-error` is set. `--json` returns the common envelope with the
evaluated value or preview in `content`, status and diagnostics fields, and
`truncated` and `max_bytes` when capped. The HTTP reader has a
separate bounded body size of at most `12 * --max-bytes + 64 KiB`, capped at
64 MiB. A response that reaches this transport ceiling is a size error with a
hint to return a smaller or aggregated value. The result limit does not
interrupt JavaScript already running in the browser; a long or non-terminating
expression can keep the shared browser occupied until the MCP server stops that
tool call.

`--selector` returns `null` when there is no match, and `--all` returns an
empty array. A missing element and a matched element with an absent attribute
both produce `null`; the current response cannot distinguish those cases.
For `inspect --format html --wait-for`, the MCP HTTP timeout is two seconds
longer than the in-page selector timeout. This gives the page function time to
return its specific `selector not visible` error before the transport closes.

Expression wrapping follows one rule. A leading function expression is passed
through when `function` is followed by whitespace, `*`, or `(`; an arrow
function is passed through when its parameter head is an identifier or a
parenthesized list, optionally after `async`. A linear lexical scan recognizes
single- and double-quoted strings, escaped template literals, line comments,
and block comments before removing trailing semicolons and comments. Leading
comments are skipped when deciding whether an expression exists; a comment by
itself is a clear input error. If slash syntax cannot be classified safely, the
scanner leaves the source untouched for JavaScript to parse. Every other input
is wrapped as the following zero-argument function:

```javascript
() => (
EXPRESSION
)
```

Multiple statements must be supplied as a function, for example
`() => { const title = document.title; return title; }`. A JavaScript
`SyntaxError` from the MCP tool is surfaced with the error text and any
position the server provides.

`PWMCP_MCP_URL` must be an absolute HTTP(S) endpoint. It may be plain
`https://host/path` or include `username:password@` userinfo. The client strips
userinfo before constructing the urllib request and converts it to a Basic
Authorization header; percent-encode reserved credential characters. Use HTTPS
for credentials outside a trusted local network. The MCP opener disables
environment-configured HTTP proxies and refuses redirects, keeping credentials
and session headers on the configured endpoint. No `PWMCP_MCP_PROXY` override is
provided. URL parse, request-builder, transport, and timeout errors use fixed
messages and do not echo the configured URL.

Only a page-local `Page crashed` or `Execution context was destroyed` failure
receives the default native Playwright retry, in a new context on the same live
lease. `Target closed` and browser-closed failures are not retried on that lease
because it may already be dead. Explicit `--retries N` still retries ordinary
navigation or stability failures, but stops on browser-level closures. The
HTML path opens a new MCP session for each attempt and applies the same closure
stop rule because a new session cannot restore a browser that the server says
is closed; its tests pin both recognized crash retries and this guard. `eval`
never retries caller-supplied code. A Playwright tool error proves the MCP
endpoint replied. A local browser connection failure only says the browser
connection failed, so its hint also asks whether PWMCP is still running.

The pinned `@playwright/mcp` package is installed only while building the PWMCP
image; this repository has no local server launcher. The opt-in
`pwmcp_live` integration test checks `tools/list` and the actual
`browser_navigate`/`browser_evaluate` result shapes when
`PWMCP_LIVE_MCP_URL` is set. The operator smoke command is
`pwmcp eval https://example.com document.title`. See [consumer commands](CONSUMERS.md#inspect-a-webpage)
and [evaluation trust boundary](SECURITY.md#mcp-page-evaluation).

PWMCP's stack currently allocates 2 GiB for `/dev/shm`, has no service-specific
memory limit on the unified browser container, and does not set
`--disable-dev-shm-usage`. The OOM investigated in PWMCP-41 came from a test
fake, not the service envelope. Host memory limits remain host-owned.

### Certificate errors during inspection

The CLI keeps normal certificate validation enabled by default. For a known,
trusted device that uses a self-signed or otherwise invalid certificate,
`pwmcp inspect URL --ignore-https-errors` creates a temporary Playwright
browser context with `ignore_https_errors=True`. The context is closed with the
inspection, so this does not change the shared service's browser or TLS policy.

Playwright scopes this setting to a browser context rather than an individual
hostname. It therefore covers redirects and HTTPS subresources from any host
loaded by that page for this invocation. The CLI describes that scope directly
and leaves the choice opt-in; a hostname-specific certificate bypass is not
available through this Playwright context option. See the
[consumer example](CONSUMERS.md#inspect-a-webpage).

## GlyphGrid development tooling

GlyphGrid stays unavailable through the OCR service (HTTP 501). The reader,
font atlases, benchmark, and `imagegen` live under `tools/glyphgrid` as local
development tools; none is included in the service images or release bundle.

The fast reader remains paired with its slower reference implementation so
performance changes can be checked for identical results. The sequence reader
reuses a prior frame's geometry only while its invalidation checks hold, then
falls back to a full read. Additional font data is kept in separate atlases so
operators load fonts only for known targets; each font is indexed in the
[NOTICE](../tools/glyphgrid/NOTICE.md) with its source, version, checksum, and
licence attribution. Attribution uncertainty is recorded explicitly, and the
font data has not had legal review for redistribution in an image.

`imagegen` makes synthetic console frames with exact ground truth and controlled
transforms. The integration adds no screenshots and does not copy the operator's
larger local console corpus into these tooling changes. Generated PNGs, truth
files, atlases, and reports belong under the ignored `scratch/` directory. See the
[tool README](../tools/glyphgrid/README.md) for the command vocabulary and the
[consumer examples](CONSUMERS.md#glyphgrid-development-tooling) for a working
invocation.

Prefer these deterministic per-font alphabets for general reader tests; use
real console output only where behavior depends on an actual install screen.

## Local console corpus

The repository may keep curated screenshots from public, anonymous, agnostic
sources such as vanilla Debian and TrueNAS installs. The larger 242-frame
TrueNAS series stays in a local directory and is never published. This keeps
the public fixtures useful for review while preventing the full capture from
becoming a release or download dependency.

The committed series manifest pins each frame's name, SHA256, and dimensions.
`CORPUS-ASSETS.json` also pins a deterministic tar digest and size, so the local
copy can be checked without downloading it. `PWMCP_CONSOLE_CORPUS_DIR` is the
only source: it must name an absolute directory containing `frames/`. There is
no cache, tester-image copy, release URL, or fallback path. An unset variable
means the optional full-corpus checks are unavailable; a bad explicit path is
an error. The two real-corpus tests use the shared
`tests/_testdeps.py::optional_local_data` helper: an unselected corpus skips
with its reason unless `PWMCP_REQUIRE_CONSOLE_CORPUS=1` makes that absence a
failure, while invalid selected data stays an error. Registered gate lanes
remain corpus-free. See the [local verification example](CONSUMERS.md#local-console-corpus)
and the [OCR phase design](DESIGN-OCR-PHASE2-3.md#1-local-console-corpus).

## Upstream version age window

Until CMRU owns upstream selection, an explicit resolver refresh applies a
two-day default age window to npm and PyPI publication timestamps. This lets
the common 1.64.0 release pass once its latest required package upload is two
days old, while keeping newly published packages out of routine selections.
The threshold remains an explicit resolver option for deliberate exceptions.

## Competitors and alternatives

Each entry records what a comparable project does, where it differs from
PWMCP, and what PWMCP takes from it. An entry carries the date it was checked.
Figures and feature claims are as the project published them on that date
unless the entry says they were verified. Re-check an entry before relying on
it.

### Scrapling (checked 2026-11-08)

**What it is.** A Python scraping library (`scrapling` on PyPI, BSD-3-Clause,
0.5.1 released 2026-11-04, classified "4 - Beta", Python 3.10+). It is a
library, not a service. The parser core depends on `lxml`, `cssselect`,
`orjson`, `tld` and `w3lib`. The `fetchers` extra adds `curl_cffi` (HTTP with
browser TLS impersonation), `patchright` (a patched Playwright), and
`browserforge` and `apify-fingerprint-datapoints` (fingerprint data);
`scrapling install` then downloads browsers onto the caller's machine. The `ai`
extra adds an MCP server and `shell` an IPython shell.

**Features relevant to PWMCP.**

- Fetchers: `Fetcher` (HTTP with TLS and header impersonation, HTTP/3) and
  `StealthyFetcher` (browser). Since 0.5 the plain Playwright backend is
  removed, so every browser fetch uses the Patchright-based stealth backend.
  `RemoteSession` attaches to a running Chromium through a CDP URL.
- Adaptive element tracking: `css(sel, auto_save=True)` stores an element's
  tag, text, attributes, sibling and path tag names, and parent properties in
  SQLite, keyed by domain and identifier. When the selector later finds
  nothing, `css(sel, adaptive=True)` returns the most similar element. The
  parser also offers `find_similar()`, text and regex search, and selector
  generation.
- Spiders: Scrapy-style async spiders with per-domain concurrency,
  AutoThrottle, optional robots.txt obedience, pause and resume (`crawldir`), a
  development response cache, sitemap, feed and Shopify templates, and
  JSON/JSONL/CSV/XML export.
- Sessions and proxies: persistent HTTP and browser sessions, `save_state` and
  `load_state` in Playwright storage-state format, a `ProxyRotator`, and
  spider-level retry of blocked responses (401, 403, 407, 429, 444 and 5xx by
  default) that can switch session or proxy.
- AI: an MCP server with 17 tools (HTTP and browser fetch, sessions, actions,
  screenshots, frame sequences, network history, evaluation, state save and
  load) over stdio or Streamable HTTP, where HTTP requires a bearer token. It
  also ships an agent skill. `scrapling extract ... --ai-targeted` removes
  hidden elements, zero-width characters and comments that could carry prompt
  injection. Browser tools block about 3,500 ad and tracker domains by default.
- Network history: a browser session records completed responses with bounded
  text bodies, so a caller can read the JSON behind a page.

**Bot protection.** This is the main difference. Scrapling's documentation
states that `StealthyFetcher` bypasses Cloudflare Turnstile and interstitial
challenges (`solve_cloudflare=True`), patches headless and CDP detection, and
adds canvas noise; the README's sponsors include proxy and CAPTCHA-solving
services. PWMCP recognizes such pages and reports them (exit 4), and its only
route past a protection is a session a human exported (see README "Bot
protection"). Those Scrapling features fall under PWMCP's stated non-goals.
PWMCP does not adopt them or depend on the `fetchers` extra, which in 0.5
always installs the stealth backend. The bypass claims were not tested.

**Where PWMCP differs.** PWMCP is a managed, shared browser service. Consumers
hold no browser and reach it through leases. It ships a pinned, versioned
image (official Playwright base, pinned npm and Python packages, an upstream
age window). The CLI has a closed exit-code table and one JSON envelope. PWMCP
also reports bot protection, runs an OCR sidecar for canvas consoles, deploys
through ciu, and releases through an R1/R2 gate with branch coverage and
mutation judgment. Scrapling runs the browser inside each caller. Its
dependencies are `>=` floors with no lock file, and its Docker `latest` tag is
built from `main` with an unpinned `uv:latest` stage. CI collects coverage
without a threshold; the README's "92% test coverage" was not verified. PyPI
uploads use Trusted Publishing with provenance attestations (verified for
0.5.1). `RemoteSession` needs a CDP endpoint, which PWMCP keeps inside its
container ([CDP never leaves the container](SECURITY.md#cdp-never-leaves-the-container)),
so the two do not connect as designed.

**Maturity (GitHub API, 2026-11-08).** About 99.7k stars and 10.4k forks; 3
open and 180 closed issues; 55 PyPI releases, 15 of them between 2026-03-30
and 2026-11-04. The owner has 1,709 commits in GitHub's contributor count (the
next contributor has 17) and wrote 218 of the 263 commits since 2026-08-08, so
the project depends on one maintainer. The README parser benchmarks (for
example 1.99 ms against Parsel's 2.06 ms on 5,000 nested elements) are the
project's own and were not reproduced.

**What PWMCP takes.** Ideas only, with no dependency: hidden-content filtering
for agent-facing output (PWMCP-50) and a network-response format (PWMCP-51).
If a consumer needs them, a bounded crawl that obeys robots.txt (PWMCP-52)
and opt-in selector relocation (PWMCP-53). Re-check this entry after
Scrapling's next minor release, mainly for its MCP tool design and extraction
behavior.

**Sources.** <https://github.com/D4Vinci/Scrapling> (README, `pyproject.toml`,
`.github/workflows/` and `Dockerfile` at commit `30cadb56a`);
<https://github.com/D4Vinci/Scrapling/releases/tag/v0.5.1>;
<https://pypi.org/project/scrapling/0.5.1/>; and the documentation pages
<https://scrapling.readthedocs.io/en/latest/parsing/adaptive.html>,
<https://scrapling.readthedocs.io/en/latest/fetching/stealthy.html>,
<https://scrapling.readthedocs.io/en/latest/ai/mcp-server.html>,
<https://scrapling.readthedocs.io/en/latest/spiders/proxy-blocking.html> and
<https://scrapling.readthedocs.io/en/latest/migrating-to-v0.5.html>.
