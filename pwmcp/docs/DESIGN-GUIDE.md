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
while CIU continues to find the root configuration. Keeping the standalone
defaults separate preserves the existing sub-stack policy, where the consumer
owns network and resource governance.

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
