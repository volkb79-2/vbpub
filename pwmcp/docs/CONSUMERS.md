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

The internal deployment intentionally has no authentication or host-published
ports, matching PWMCP's existing internal-network policy. Keep consumers on
the CIU network created for this instance.

The Compose configuration uses its version-pinned image from the local Docker
cache when present and pulls that tag when it is absent. A locally built image
with the configured tag can therefore be used before that tag is available from
the registry.

## Inspect a webpage

Use the bundled CLI to read a rendered page through PWMCP's managed remote
browser:

```bash
pwmcp inspect https://example.com
pwmcp inspect https://example.com --format snapshot --output page.yml
pwmcp inspect https://example.com --format screenshot --output page.png --full-page
pwmcp inspect https://example.com --wait-for '#content' --wait-until domcontentloaded
```

`--format` accepts `text`, `snapshot`, and `screenshot`. `--wait-until` accepts
`commit`, `domcontentloaded`, `load`, and `networkidle`. The default output is
rendered body text. `snapshot` writes an ARIA snapshot; `screenshot` requires
`--output PATH` ending in `.png`, `.jpg`, or `.jpeg`. `--wait-for SELECTOR`
waits for a visible CSS selector; `--timeout-ms` sets the wait timeout.

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
