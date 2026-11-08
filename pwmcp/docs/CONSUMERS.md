# PWMCP consumers

## Install and update

The install user needs CIU, Docker access, and Python 3.11 or newer with
`venv`/`ensurepip` available (`python3-venv` on Debian).

Install the current PWMCP release as an independent CIU root at
`/srv/pwmcp-instance`. The same user must own the root so `ciu up` can create
its generated workspace facts and rendered files:

```bash
sudo install -d -m 0755 -o "$USER" -g "$(id -gn)" /srv/pwmcp-instance
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/pwmcp/get.py \
  | XDG_DATA_HOME=/srv python3 - install --scope user
export PATH="/srv/pwmcp-instance/bin:$PATH"
cd /srv/pwmcp-instance
# First run only: record this devcontainer's CIU identity and network.
ciu env generate
ciu up
```

To update the install, run:

```bash
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/pwmcp/get.py \
  | XDG_DATA_HOME=/srv python3 - update --scope user
export PATH="/srv/pwmcp-instance/bin:$PATH"
cd /srv/pwmcp-instance
ciu up
```

An image that owns `/srv` can omit the first `sudo install` line. A root-owned
system-scope install is also supported; run CIU as the principal that can
write the installed root.

The installer seeds the instance's sparse `ciu.toml.j2` override once, marking
the existing internal network as external to this stack. It preserves that
instance file on updates, so operator additions survive release changes.

For an MDT devcontainer built with PWMCP included, `/srv/pwmcp-instance` and
the `pwmcp` launcher are already present. On the first start, run
`ciu env generate` in that directory to record the devcontainer's machine
identity and internal network, then start the instance with `ciu up`. Later
starts and updates use the existing generated facts. CIU joins the devcontainer
to the instance network so an agent running there can use
`http://pwmcp:8931/mcp`. The native Playwright endpoint is `ws://pwmcp:3000/`.
The `pwmcp inspect` CLI uses that native endpoint; for example,
`pwmcp inspect https://example.com` reads the rendered page from the service browser. An agent
can use the MCP endpoint at the same time through its MCP client configuration
below. The internal service remains unauthenticated for any process on the
shared CIU network, matching PWMCP's existing internal policy.

The stable CLI launcher for this install is
`/srv/pwmcp-instance/bin/pwmcp`; it follows the active release and does not
require activating the release virtual environment. The devcontainer exposes
it through a `~/.local/bin/pwmcp` symlink on the default user's existing PATH.
Check with `command -v pwmcp`; if a tool shell does not inherit that PATH, call
`/srv/pwmcp-instance/bin/pwmcp` directly. When including PWMCP in another MDT
devcontainer image, create the same symlink after installing the bundle. On a
separate `get.py` install, add that install root's `bin` directory to PATH or
use its `bin/pwmcp` launcher.

The internal deployment intentionally has no authentication or host-published
ports, matching PWMCP's existing internal-network policy. Keep consumers on
the CIU network created for this instance.

The Compose configuration uses its version-pinned image from the local Docker
cache when present and pulls that tag when it is absent. A locally built image
with the configured tag can therefore be used before that tag is available from
the registry.

## Install and use the agent skill

The client bundle includes the `pwmcp-fetch` skill. Install it for the supported
harnesses with:

```bash
pwmcp skills install --harness all
pwmcp skills check --harness all
```

The skill invokes `pwmcp` by command name. Each consumer environment must make
the installed CLI discoverable on the agent process's `PATH`; installing the
skill does not install the CLI or configure that path. An agent using MCP tools
also needs a configured PWMCP MCP connection whose endpoint is reachable from
the agent runtime. The default internal endpoint is `http://pwmcp:8931/mcp`
when the agent shares the PWMCP network. A CLI install can inspect pages
without configuring an MCP connection.

One service has one shared browser, so fetches must be serialised. A second
parallel MCP client fails with "Browser is already in use ... use --isolated";
do not follow that hint (the service cannot take `--isolated`), wait and retry.

## Wire protocol for a consumer without `pwmcp_client`

`BrowserLease.connect()` (and `pwmcp inspect`) is a thin wrapper over a plain Playwright `connect()`; a test
runner that cannot install the `pwmcp-client` wheel can speak the protocol directly. First read the contract
(`GET http://pwmcp:3000/contract`): `endpoints.playwright_ws`, `playwright.python`, `run_server.limits`.

```python
import json, urllib.request
from playwright.sync_api import sync_playwright

contract = json.load(urllib.request.urlopen("http://pwmcp:3000/contract", timeout=10))
with sync_playwright() as p:
    browser = p.chromium.connect(
        contract["endpoints"]["playwright_ws"],
        headers={"X-PWMCP-Lease-Seconds": "600", "X-PWMCP-Session-Label": "my-suite"},
        timeout=30_000,
    )
    page = browser.new_page()
    page.goto("http://my-app:8080/")
    browser.close()
```

* **Headers** (both optional at the gateway, `containers/pwmcp/run-server-gateway/index.js`):
  `X-PWMCP-Lease-Seconds` is a positive integer; default `run_server.limits.default_lease_seconds` (1800), capped at
  `max_lease_seconds` (7200); a non-integer is refused with HTTP 400 naming the header. The session is closed when
  the lease expires. `X-PWMCP-Session-Label` is free text (first 128 characters, default `unlabelled`) shown in the admin
  session list and the gateway log.
* **Limits:** at most `max_clients` (2) concurrent sessions; the next upgrade gets HTTP 503 ("all N browser slots
  are in use"). The browser is recycled after `idle_recycle_seconds` without sessions.
* **HTTP 403** (body names the problem): the connecting `Host` header is not in the gateway allowlist
  (`PWMCP_RUN_SERVER_ALLOWED_HOSTS`: the ciu-derived names `pwmcp:3000` and `<project>-<env>-pwmcp:3000`, the
  `pwmcp.consumer.aliases` entries, and `pwmcp.external.unified_host`). Connect by one of those names, or add an alias
  in the stack config. The gateway logs `[gateway] rejected status=403 ...` with the allowed list.
* **HTTP 428 Precondition Required** is not produced by this gateway (it never requires the lease headers: absent
  headers mean the default lease). If you see it, the request reached something else (an older release's gateway or the
  Playwright server directly): check `curl http://pwmcp:3000/contract` for the release and send both headers as above.
* **Version rule:** the connecting `playwright` package must have the same major.minor as the contract's
  `playwright.python` (the `playwright.protocol` field); a different minor fails at connect time. Install exactly
  `playwright==<contract playwright.python>` (the `pwmcp-client` wheel pins it for you).

### Reaching the instance from another ciu network

The browser runs inside the pwmcp container, so the pwmcp container must reach your app and your runner must
reach pwmcp. Put them on one network: either deploy pwmcp as a sub-stack of your project (pass your project's
network as `deploy.network_name`, see `docs/DEPLOYMENT.md`), or attach your runner and app containers to the
instance's network (for the devcontainer instance `vbpub-cmqemn-network`). netcup-api-filter does the latter:
its compose requires a `PWMCP_NETWORK` variable naming that network and joins both its runners and its app to it
as an external network, then uses `pwmcp:3000` and `http://<app-service>:<port>` as above.

## Inspect a webpage

Use the bundled CLI to read a rendered page through PWMCP's managed remote
browser:

```bash
pwmcp inspect https://example.com
pwmcp inspect https://example.com --format snapshot --output page.yml
pwmcp inspect https://example.com --format html --selector main --html-mode outer --output page.html
pwmcp inspect https://example.com --format html --selector 'script#__NEXT_DATA__' --html-mode inner --json
pwmcp inspect https://example.com --format markdown --main
pwmcp inspect https://example.com --format links --selector article
pwmcp inspect https://example.com --format text --selector '#article-summary'
pwmcp inspect https://example.com --format screenshot --output page.png --full-page
pwmcp inspect https://example.com --wait-for '#content' --wait-until domcontentloaded
pwmcp inspect https://example.com/app \
  --wait-until networkidle --wait-for '#content'
pwmcp inspect https://nas.example --ignore-https-errors
pwmcp inspect https://example.com --format snapshot --retries 2
pwmcp eval https://example.com 'document.title'
pwmcp eval https://example.com --selector 'meta[name=description]' --attr content --json
pwmcp eval https://example.com --selector '.record' --property textContent --all --timeout 60
pwmcp eval https://example.com --selector main --property textContent --max-bytes 262144
pwmcp inspect https://example.com/optional --allow-http-error
pwmcp eval https://example.com/optional document.title --allow-http-error
```

`--format` accepts `text`, `snapshot`, `html`, `markdown`, `links`, and
`screenshot`. For text, Markdown, and links, `--selector CSS` extracts the first
matching element. `--main` selects the first semantic `main`, `[role=main]`, or
`article`; if none exists, it reads the body after removing `header`, `nav`, and
`footer` elements. It cannot be combined with `--selector`.
Text collapses source whitespace inside paragraphs and preserves `<pre>`
indentation and line breaks. Markdown keeps headings, lists, tables, inline and
fenced code, and links; table cells are separated by ` | ` and rows by newlines.
The links format lists each anchor label with an absolute resolved href. HTML returns the
document's `outerHTML`, or one selector's `outerHTML`; pass `--html-mode inner`
to return `innerHTML`. It is capped at 512 KiB by default, reports the original
and included byte counts in JSON, and prints a truncation notice on stderr.
Use a narrower selector for a large embedded data block. Recognised bot
protection still exits 4 and does not write challenge HTML to `--output`.
The content renderer has additional known limits: it ignores `<base href>`,
includes `javascript:` hrefs in links, returns only the first `--selector`
match, and may raise `RecursionError` for very deep DOMs. The `--main` fallback
also removes nested `header`, `nav`, and `footer` elements. Agent-facing
formats (`markdown`, `links`, and `text` with `--selector` or `--main`) drop
content a reader cannot see — elements with `hidden`, `aria-hidden="true"` or
inline `display:none`/`visibility:hidden`, with their whole subtree — and strip
zero-width characters; the JSON result reports the counts as `hidden_removed`
and `zero_width_stripped`. Hiding applied by computed CSS (a class or
stylesheet rule) is not detectable in serialized HTML and is a known limit.
See the [content-format design](DESIGN-GUIDE.md#rendered-content-formats).

`inspect --json` always returns the same object envelope, including when there
are no warnings: `content`, `diagnostics`, `format`, `http_status`, and
`http_error`. `content` is `null` when `--output` writes the result to a file.
`http_status` is `null` if unavailable and `http_error` is true only when the
main document returned HTTP 4xx/5xx. `diagnostics` is an array of page warnings;
identity and storage-state metadata is included when active. `eval --json` uses
the same envelope with `format: "eval"`; its `content` is the evaluated JSON
value or a bounded preview when `truncated: true`.

The main document's 4xx/5xx status returns its body and exits 5. Pass
`--allow-http-error` to `inspect` or `eval` when that response is expected; the
body is still returned and the command exits 0. The JSON fields continue to
report the HTTP status. This option does not change `inspect`'s exit 4 for
recognized bot protection and does not treat failed subresources as
main-document errors.

### Client exit codes

This is the closed exit-code table for `pwmcp inspect` and `pwmcp eval`:

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

`pwmcp eval URL EXPRESSION` runs the expression through PWMCP's existing
`browser_evaluate` MCP tool and prints its result as JSON. Use `--selector` with
`--attr NAME` or `--property NAME` to read a matching element; `--all` returns
all matches as an array. No match returns `null` (or `[]` with `--all`); a
missing element and an absent attribute both return `null`. `--timeout` is a
per-MCP-request limit in seconds. Results are capped at 1 MiB by default;
`--max-bytes` accepts 1 through 4194304 bytes. An oversized result prints a
JSON preview to stdout and a `[truncated after N bytes]` marker to stderr; the
command exits 0 unless the main document returned HTTP 4xx/5xx. In `--json`
mode the common object contains `content` and `preview` with that bounded value,
`truncated: true`, `max_bytes`, `http_status`, `http_error`, and `diagnostics`.
The page checks compact JSON byte length
before returning it through MCP. If the transport response itself reaches its
separate ceiling, the command exits 1 with a size hint. The endpoint connection
ignores proxy environment variables and refuses redirects.
An `undefined` page result may be returned as the text `undefined`; several
MCP text blocks are returned as a JSON array, so prefer one JSON-serializable
value per expression. Serialize calls when using a shared browser.

Expressions are wrapped as a zero-argument function. A leading function
expression (`function` followed by whitespace, `*`, or `(`) and an arrow
function with an identifier or parenthesized parameter list are passed through;
`async` forms are accepted. For other input, PWMCP removes trailing
statement-terminating semicolons and comments outside literals, then sends:

```javascript
() => (
EXPRESSION
)
```

Leading comments can precede an expression; a comment alone is an error.
Semicolons and comment markers inside quoted strings and template literals are
preserved. When slash syntax is ambiguous, PWMCP leaves the input unchanged for
the JavaScript parser to diagnose. Supply an explicit function with `return`
for multiple statements. JavaScript syntax errors returned by the MCP server
are shown with any position included by that server. A timeout hint identifies
a timed-out MCP request and points to `--timeout` or `--timeout-ms`.

The client wheel speaks the small Streamable HTTP exchange it needs using the
Python standard library, so these CLI commands do not require the third-party
`mcp` package. `PWMCP_MCP_URL` defaults to `http://pwmcp:8931/mcp` and accepts
an absolute HTTP(S) endpoint with or without `username:password@` userinfo. The
client strips userinfo from the request URL and sends it as HTTP Basic Auth;
percent-encode reserved credential characters and use HTTPS outside a trusted
local network. For example, set `PWMCP_MCP_URL` to
`https://<user>:<percent-encoded-password>@<host>/mcp` for an externally guarded
endpoint. MCP requests go directly to this endpoint: proxy environment
variables are ignored, redirects are refused, and no `PWMCP_MCP_PROXY` setting
is available.

After upgrading the client or pinned server, an operator can check the live
`browser_navigate` and `browser_evaluate` response format with:

```bash
pwmcp eval https://example.com document.title
pwmcp eval https://example.com --selector title --property textContent
```

The first command returns a value from the expression route; the second checks
the selector/property route. No local server launcher exists in this checkout;
the pinned MCP server is installed into the PWMCP image.

`--wait-until` accepts
`commit`, `domcontentloaded`, `load`, and `networkidle`. The default output is
rendered body text. `snapshot` writes an ARIA snapshot; `screenshot` requires
`--output PATH` ending in `.png`, `.jpg`, or `.jpeg`. `--wait-for SELECTOR`
waits for a visible CSS selector; `--timeout-ms` sets the wait timeout.
For pages that hydrate after the initial navigation event, combine
`--wait-until networkidle` with a visible, page-specific selector. A selector
can match multiple elements; PWMCP waits for the first matching visible element.
PWMCP retries a page-local renderer crash on the first navigation once by
default and notes the retry on stderr. A new context uses the existing live
Playwright lease. `Target closed` and browser-closed errors are reported without
retry because that native lease may be dead. An explicit `--retries N` adds
attempts for ordinary navigation or unstable-page failures, while
`--retries 0` disables the automatic page-crash retry. The HTML path starts a
fresh MCP session for each attempt but also stops when the server reports a
browser closure; a new session cannot restore a closed browser. A browser-level
error does not prove that PWMCP is currently reachable; check the service if it
repeats. `eval` never retries caller-supplied code. Reduce page load and check
browser memory and `/dev/shm` for repeated page-local crashes.
For a trusted device with a known self-signed or otherwise invalid certificate,
`--ignore-https-errors` opts into certificate bypass for HTTPS requests in this
inspection's temporary browser context. This includes redirects and page
resources loaded from other hosts; the option does not change PWMCP's service
or browser defaults. See the [design rationale](DESIGN-GUIDE.md#certificate-errors-during-inspection).

An MCP client on the same network can use this server entry:

```json
{
  "mcp": {
    "servers": {
      "pwmcp": {
        "type": "http",
        "url": "http://pwmcp:8931/mcp"
      }
    }
  }
}
```

## GlyphGrid development tooling

The GlyphGrid reader and `imagegen` are local analysis tools in the PWMCP source
tree. They do not enable the OCR service engine, which still returns HTTP 501.
Run the examples from the source checkout with Python and Pillow available;
they use the committed bitmap font and synthetic screens, not the local console
corpus. Keep every generated image, truth file, atlas, and report under the
ignored `scratch/` directory.

```bash
cd pwmcp/tools/glyphgrid
python3 -m imagegen generate --font vga-8x16 --screen installer --seed 1 -o scratch/sample
python3 -m imagegen read --engine glyph-grid-oracle \
  --truth scratch/sample/vga-8x16__installer__clean__s1.json \
  scratch/sample/vga-8x16__installer__clean__s1.png
python3 -m imagegen suite --font vga-8x16 --screen installer --seed 1 -o scratch/suite
python3 -m imagegen eval --suite scratch/suite --engine glyph-grid --by screen --json scratch/eval.json
```

The generated files are reproducible within the recorded image-library
environment. The evaluation command reports exact, unknown, and wrong cells
separately. See the [tool README](../tools/glyphgrid/README.md) for all font,
screen, engine, and transform choices, the [design rationale](DESIGN-GUIDE.md#glyphgrid-development-tooling),
and [font attribution](../tools/glyphgrid/NOTICE.md).

## Local console corpus

The curated screenshots in the repository are public, anonymous fixtures. The
full 242-frame TrueNAS capture is kept locally and is never published. To check
a local copy, point PWMCP at an absolute directory containing a `frames/`
subdirectory with the indexed frames:

```bash
cd pwmcp
export PWMCP_CONSOLE_CORPUS_DIR="$PWD/artifacts/console-corpus"   # the conventional, gitignored place
python3 tools/corpus/console_corpus.py verify "$PWMCP_CONSOLE_CORPUS_DIR"
```

The verifier checks the exact frame set, each frame's SHA256 and dimensions,
and the SHA256 and size of the deterministic tar described by
`tests/corpus/console/CORPUS-ASSETS.json`. It reads only that local directory;
it has no download or cache path. If the variable is unset, the two tests that
need the complete capture skip with a reason. Set
`PWMCP_REQUIRE_CONSOLE_CORPUS=1` when running those tests in an environment
with the local directory available to make an absent corpus fail. The release
gate lanes remain corpus-free. See the [design rationale](DESIGN-GUIDE.md#local-console-corpus).

## Release gates

Run the release-equivalent suite with:

```bash
run-gate r1
```

This executes in `tester-unified` and installs Assay from the selected vbpub
worktree. The resumable mutation lane is:

```bash
run-gate r2
```

It writes progress and verdict state below the git-ignored `.assay/` directory.
Use the R1 lane for the normal CMRU release gate; run R2 separately when the
long mutation campaign is useful.

## Maintainer refresh of Playwright

To select the newest stable Playwright version available from npm, PyPI, and
the matching Microsoft image registry, run the version resolver from the
`pwmcp/` directory. Its temporary default age window is two days; pass
`--age-window-days DAYS` only when intentionally choosing a different cutoff:

```bash
python3 scripts/resolve-playwright-version.py --refresh
```

Review the generated version and digest changes, then use the supported CMRU
release workflow. The reason for the two-day window is in the
[design guide](DESIGN-GUIDE.md#upstream-version-age-window).
