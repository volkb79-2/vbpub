# Netcup SCP installation tools

These scripts provision disposable or fresh Debian hosts through the Netcup
Server Control Panel API and feed them the `debian-install-v2` bootstrap.

## First-time setup

From this directory, create the OAuth refresh token with the wizard:

```bash
python3 scp-api-install-host.py login
```

The browser device-code flow writes `NETCUP_SCP_API_REFRESH_TOKEN` to the
loaded `.env` file and enforces mode `0600` because it contains a long-lived
credential. Set `NETCUP_SCP_API_SERVER_NAME` there as well, then let
the API-backed wizard resolve the current Debian UEFI image and save a local
recipe:

```bash
python3 scp-api-install-host.py configure
```

`default-recipe.jsonc` is deliberately local and gitignored. Delete it or
rerun `configure` to regenerate it. It contains placeholders for secrets and
for the bootstrap source; the controller expands those only in the API
request, never by modifying the saved recipe.

## Install workflow

Preview an installation without mutating the Netcup account:

```bash
python3 scp-api-install-host.py --payload target-host.jsonc --dry-run
```

Run an interactive installation and follow stage2:

```bash
python3 scp-api-install-host.py --payload target-host.jsonc --monitor
```

During the interactive key step, existing Netcup account keys are listed and
the first one is the default. Choosing one uses it without registering a new
account key. To pin an existing key in a direct payload run, pass its ID (and
repeat the option for multiple IDs):

```bash
python3 scp-api-install-host.py --payload target-host.jsonc --ssh-key-id 123 --monitor
```

The local `--ssh-identity-file` is a separate ephemeral controller key used
for monitoring and is still generated when needed; `--ssh-key-id` refers to a
key already registered in the Netcup account. If no account key exists, or
the interactive create option is selected, the controller key is registered
before the final install request.

`scp-api-install-host.py --help` documents the payload, attach-only, poweroff,
and wizard modes. `scp-api-explore.py` provides read-only account/server
inspection and explicitly gated reversible actions. Read-only resource commands
enumerate every server when no ID is supplied, because the SCP API exposes
image flavours, ISO images, disks, rescue status, snapshots, and ISO attachment
status below each server. Results from an account-wide query include the source
server ID/name. Use a server ID to inspect only one server; mutating options
such as `--detach`, `--deactivate`, `--create`, and `--dryrun` still require it.

An image flavour is a server-compatible reinstallable OS/image variant (for
example a Debian 13 UEFI amd64 image), not a VM template. ISO images are
bootable installer or recovery media. Useful first queries are:

```bash
./scp-api-explore.py servers
./scp-api-explore.py imageflavours --filter debian
./scp-api-explore.py isoimages --filter rescue
./scp-api-explore.py isoimages 799611 --filter debian --json
./scp-api-explore.py disks
./scp-api-explore.py snapshots
./scp-api-explore.py tasks
```

`--filter` is case-insensitive and searches the returned fields, including an
image flavour's name and alias or an ISO image's name, description, and
architecture. All commands support `--help` and `--json`; no short `-h` alias
is used so the complete public spelling is visible in generated usage.

## Bootstrap source

The generated customScript contains `{{BOOTSTRAP_URL}}`; the controller
resolves it from `[bootstrap]` in `scp-api-install-host.toml`. The matching
`REPO_URL` and `REPO_BRANCH` are passed to `bootstrap-remote.py`, so a branch
test downloads the wrapper and the installer subtree from the same branch:

```bash
NETCUP_SCP_API_BOOTSTRAP_REPO_BRANCH=netcup-v2-integration \
  python3 scp-api-install-host.py --payload target-host.jsonc --dry-run
```

For a nonstandard wrapper location, set
`NETCUP_SCP_API_BOOTSTRAP_URL` too. The URL must be HTTPS. Do not run a live
install until that branch is pushed and the dry-run payload shows the intended
source.

The rationale for the separate login/configure/build-customscript commands,
the local recipe, and the branch-pinned bootstrap is in
[`DESIGN-GUIDE.md`](DESIGN-GUIDE.md).
