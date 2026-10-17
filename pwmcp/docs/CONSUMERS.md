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

## Inspect a webpage

Use the bundled CLI to read a rendered page through PWMCP's managed remote
browser:

```bash
pwmcp inspect https://example.com
pwmcp inspect https://example.com --format snapshot --output page.yml
pwmcp inspect https://example.com --format screenshot --output page.png --full-page
pwmcp inspect https://example.com --wait-for '#content' --wait-until domcontentloaded
pwmcp inspect https://example.com/app \
  --wait-until networkidle --wait-for '#content'
pwmcp inspect https://nas.example --ignore-https-errors
```

`--format` accepts `text`, `snapshot`, and `screenshot`. `--wait-until` accepts
`commit`, `domcontentloaded`, `load`, and `networkidle`. The default output is
rendered body text. `snapshot` writes an ARIA snapshot; `screenshot` requires
`--output PATH` ending in `.png`, `.jpg`, or `.jpeg`. `--wait-for SELECTOR`
waits for a visible CSS selector; `--timeout-ms` sets the wait timeout.
For pages that hydrate after the initial navigation event, combine
`--wait-until networkidle` with a visible, page-specific selector. A selector
can match multiple elements; PWMCP waits for the first matching visible element.
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

## Release gates

Run the release-equivalent suite with:

```bash
./run-gate.py r1
```

This executes in `tester-unified` and installs Assay from the selected vbpub
worktree. The resumable mutation lane is:

```bash
./run-gate.py r2
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
