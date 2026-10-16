# PWMCP design guide

The release gate uses source-backed Assay through `run-gate.py`. Run-gate owns
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
