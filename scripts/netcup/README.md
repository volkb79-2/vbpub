# Netcup SCP installation tools

These scripts provision disposable or fresh Debian hosts through the Netcup
Server Control Panel API and feed them the `debian-install-v2` bootstrap.

## First-time setup

From this directory, create the local secret file and set the target server
name before logging in:

```bash
cp .env.example .env
chmod 600 .env
vi .env                               # set NETCUP_SCP_API_SERVER_NAME
./scp-api.py login
```

The browser device-code flow writes `NETCUP_SCP_API_REFRESH_TOKEN` to the
loaded `.env` file and enforces mode `0600` because it contains a long-lived
credential. After a successful login, an interactive terminal also lists
servers whose SCP internal name matches `v<digits>` (for example
`v2202503209318326780`) and offers a numbered selection for the local
protected-server denylist. Each candidate shows its server ID, IP addresses,
and configured or resolver-derived reverse-DNS entries. It does not create an
SSH key.

### Local protected-server denylist

The login selection is a local safety guard for this checkout, not a Netcup
account lock. It is saved in `.env` as
`NETCUP_SCP_API_PROTECTED_SERVERS`; the wizard also records the selected server
IDs in `NETCUP_SCP_API_PROTECTED_SERVER_IDS` so a later server rename cannot
silently remove protection. The `.env` writer enforces mode `0600`.

The guarded server mutations are ISO attach/detach, rescue deactivation,
snapshot creation, task cancellation, firewall assignment, power operations,
and Debian image installation/poweroff. Read-only queries, snapshot `dryrun`,
and installer `--dry-run` remain available. Account-level user-ISO upload and
firewall-policy create/PUT are not server-targeted; applying a policy with
`firewall SERVER set` is guarded. Task cancellation needs
`--server-id` while the denylist is configured so the task can be checked
against the protected target.

To change the selection, run login again and choose additional servers. For a
non-interactive login, the token is still saved but selection is skipped; set
the validated `v<digits>` names in `.env` or run login from a terminal.

### Quickstart: inspect and install a Debian VM

The normal installer gathers server-specific facts live. `configure` is
optional: it saves local locale/timezone/partition defaults, while the normal
interactive install still resolves the current Debian UEFI image every time.

```bash
./scp-api.py status
./scp-api.py servers
./scp-api.py imageflavours --filter debian

# Optional local defaults wizard; requires NETCUP_SCP_API_SERVER_NAME in .env.
./install-host.py configure

# Preview the gathered payload and account-key decision. This does not call a
# mutating Netcup API, but it may create the local controller key used for SSH
# monitoring. It does not save target-host.jsonc.
./install-host.py --dry-run

# Gather again, save target-host.jsonc, ask for final confirmation, install,
# and follow the task plus Debian bootstrap logs.
./install-host.py --monitor
```

The installer writes `target-host.jsonc` before its final install confirmation
so the exact request can be reviewed or reused. It is local and gitignored.
Selecting “create a new account key” registers that Netcup account key during
the gathering step, before the final confirmation; cancelling afterwards does
not undo that registration. Select an existing key, or use `--ssh-key-id`, to
avoid creating one. The separate local controller identity is generated when
needed for the bootstrap/monitoring path and is not the account key.

If the dry-run looks correct, the second command can be made non-interactive:

```bash
./install-host.py --yes --monitor
```

`--yes` skips confirmation, so use it only after reviewing the dry-run and
the selected bootstrap source.

To save or repeat a gathered request explicitly, use the generated file:

```bash
./install-host.py --payload target-host.jsonc --dry-run
./install-host.py --payload target-host.jsonc --ssh-key-id 123 --monitor
```

Direct payload mode does not load `default-recipe.jsonc`: for a Debian install,
the payload must contain `serverId` (or a resolvable `hostname`), `diskName`,
and the `customScript` that should be sent to Netcup. `imageFlavourId` and
`sshKeyIds` may be omitted and are resolved live. The normal interactive flow
is the easiest way to create a complete payload. `default-recipe.jsonc` is
deliberately local and gitignored; delete it or rerun `configure` to reset
those interactive defaults. Its secret and bootstrap placeholders are expanded
only in the API request, never by modifying the saved file.

### Notifications

The installer supports one selected notification backend: `telegram`,
`mattermost`, or `none`. Telegram remains the compatibility default. For the
public Mattermost deployment described by
[`nyxloom/mattermost/CONSUMER.md`](../../nyxloom/mattermost/CONSUMER.md), use
the producer's incoming-webhook secret in the local `.env`. The externally
reachable host is `mattermost.gstammtisch.dchive.de`:

```dotenv
NOTIFY_BACKEND=mattermost
MATTERMOST_WEBHOOK_URL=https://mattermost.example.test/hooks/REDACTED
```

From this checkout, the local secret file can provide the value without
committing it:

```bash
webhook_url=$(cat ../../nyxloom/mattermost/.ciu/secrets/installer_webhook_url)
sed -i 's/^NOTIFY_BACKEND=.*/NOTIFY_BACKEND=mattermost/' .env
sed -i "s#^MATTERMOST_WEBHOOK_URL=.*#MATTERMOST_WEBHOOK_URL=\"$webhook_url\"#" .env
```

Use the public Mattermost hostname in the webhook URL; an external Netcup VM
cannot use the Mattermost stack's internal Docker hostname. The webhook is
post-only and channel-bound, so this integration does not need a Mattermost
PAT or REST client. Notification failures are logged as warnings and do not
turn a successful Debian install into a failed one. The `build-customscript`
wizard asks which backend to use; normal API installs read `NOTIFY_BACKEND` and
the matching credentials from `.env`. To generate a fully expanded script for
a manual web-host UI install, run:

```bash
./install-host.py build-customscript
```

## Install workflow

Preview an installation without mutating the Netcup account:

```bash
./install-host.py --dry-run
```

Run an interactive installation and follow stage2:

```bash
./install-host.py --monitor
```

Use `--payload target-host.jsonc` only after the normal flow has generated the
file, or when supplying a separately prepared complete payload. To monitor a
task after the installer has exited, use the standalone task watcher:

```bash
./monitor-task.py TASK_UUID
./monitor-task.py TASK_UUID --json
```

Avoid `--raw` unless the response is being handled as a secret: task payloads
can contain values such as the generated root password.

During the interactive key step, existing Netcup account keys are listed and
the first one is the default. Choosing one uses it without registering a new
account key. To pin an existing key in a direct payload run, pass its ID (and
repeat the option for multiple IDs):

```bash
python3 install-host.py --payload target-host.jsonc --ssh-key-id 123 --monitor
```

The local `--ssh-identity-file` is a separate ephemeral controller key used
for bootstrap access and monitoring and is still generated when needed;
`--ssh-key-id` refers to a key already registered in the Netcup account. If no
account key exists, or the interactive create option is selected, the new
account key is registered during gathering, before the final confirmation.

`install-host.py --help` documents the payload, attach-only, poweroff,
and wizard modes. `scp-api.py` provides read-only account/server
inspection and explicitly gated reversible actions. Read-only resource commands
enumerate every server when no ID is supplied, because the SCP API exposes
image flavours, ISO images, disks, rescue status, snapshots, and ISO attachment
status below each server. Results from an account-wide query include the source
server ID/name. Use a server ID to inspect only one server; mutating actions
such as `detach`, `deactivate`, `create`, and `dryrun` are positional and still
require it. `servers` lists the account inventory; use `server-details SERVER_ID`
for one server's full record. `status` is the compact live view: it queries
server details and interfaces and prints vname, reverse DNS, run state,
architecture, CPU count, RAM/disk in GiB, and IPv4/IPv6 addresses.

An image flavour is a server-compatible reinstallable OS/image variant (for
example a Debian 13 UEFI amd64 image), not a VM template. ISO images are
bootable installer or recovery media. Useful first queries are:

```bash
./scp-api.py status
./scp-api.py servers
./scp-api.py server-details 799611
./scp-api.py imageflavours --filter debian
./scp-api.py iso-bootable --filter rescue
./scp-api.py iso-bootable 799611 --filter debian --json
./scp-api.py iso-attached
./scp-api.py disks
./scp-api.py snapshots
./scp-api.py tasks

# Explicit, confirmed actions:
./scp-api.py iso-attached 799611 detach
./scp-api.py attach-iso 799611 --iso-id 1234
./scp-api.py rescuesystem 799611 deactivate
./scp-api.py snapshots 799611 create --name before-upgrade
./scp-api.py tasks --state RUNNING --server-id 799611
./scp-api.py metrics 799611 cpu --hours 24
./scp-api.py guest-agent-status 799611
./scp-api.py firewall-policies
./scp-api.py user-iso
./scp-api.py firewall 799611 get
./scp-api.py firewall 799611 aa:bb:cc:dd:ee:ff set --user-policy-id 12 --active
./scp-api.py power off 799611
./scp-api.py power on 799611
./scp-api.py power cycle 799611
./scp-api.py power reset 799611
```

`--filter` is case-insensitive and searches the returned fields, including an
image flavour's name and alias or an ISO image's name, description, and
architecture. Every `scp-api.py` verb supports `--help`; resource reads and
API actions support `--json` for machine-readable output. No short `-h` alias
is used, so the complete public spelling is visible in generated usage.

`attach-iso` changes the server's attached media and requires either an ISO ID
from `iso-bootable` or the name of an uploaded user ISO. `metrics` returns the
raw timestamped SCP data for CPU, disk, or network lookback windows.
`guest-agent-status` reports QEMU guest-agent availability; it is not an SSH
or bootstrap health check. Firewall `get`/`set` operates on one interface MAC:
if the server has exactly one interface, omit the MAC and the command resolves
it from live server details; multiple interfaces require an explicit MAC.
`set` replaces copied/user policy assignments and requires an explicit firewall
active state. `firewall-policies create` and `firewall-policies put` accept a
strictly validated `FirewallPolicySave` JSON object from `--policy-json` or
`--policy-file`; the API still decides provider-side semantic constraints.

### Read, create, and assign a firewall policy

Read the current assignment before changing it. With one interface the MAC is
optional; with several interfaces, obtain the MAC from `server-details` and
provide it explicitly:

```bash
./scp-api.py firewall 799611 get --consistency-check
./scp-api.py firewall 799611 aa:bb:cc:dd:ee:ff get --consistency-check
```

The following creates a policy that accepts SSH from two example addresses and
drops other SSH traffic, while leaving other traffic to the existing implicit
firewall rule. Replace the TEST-NET addresses with the real administrator
addresses:

```bash
POLICY_JSON=$(./scp-api.py firewall-policies create \
  --policy-file firewall-policy-examples/public-ssh-whitelist.json \
  --yes --json)
POLICY_ID=$(jq -r '.id // empty' <<<"$POLICY_JSON")
test -n "$POLICY_ID" || { echo "policy response had no id" >&2; exit 1; }
```

For an inline snippet, use the same validation and confirmation path:

```bash
./scp-api.py firewall-policies create --policy-json \
  '{"name":"ssh-whitelist","rules":[{"direction":"INGRESS","protocol":"TCP","action":"ACCEPT","sources":["198.51.100.10"],"destinationPorts":"22"},{"direction":"INGRESS","protocol":"TCP","action":"DROP","destinationPorts":"22"}]}'
```

To replace an existing policy definition, use its ID and a complete request
document. The input is validated before the API PUT is attempted:

```bash
./scp-api.py firewall-policies put 12 \
  --policy-file firewall-policy-examples/public-ssh-whitelist.json
```

Unknown fields, missing required rule fields, invalid enums, malformed IP
addresses, duplicate addresses, and invalid port/range syntax are rejected.
The API remains authoritative for account-specific semantic validation.

Before assignment, confirm the current `ingressImplicitRule` is
`ACCEPT_ALL`; otherwise the policy above will not keep unrelated public ports
open. Assignment replaces the complete policy list, so preserve any existing
copied/user policy IDs if they are still required. The command is confirmed
unless `--yes` is supplied:

```bash
./scp-api.py firewall-policies
./scp-api.py firewall 799611 get --consistency-check
./scp-api.py firewall 799611 set --user-policy-id "$POLICY_ID" --active
./scp-api.py tasks --state RUNNING --server-id 799611
```

Treat this as a lockout-sensitive change: keep a provider console/rescue path
available and test SSH from an allowed and a deliberately disallowed address.

### Upload a user ISO and boot it

User ISO storage is an SCP account API, not an `iso-bootable` server inventory.
List existing uploads, then upload a local ISO with a streaming PUT to the
presigned object-storage URL. The CLI never sends the SCP bearer token to that
URL:

```bash
./scp-api.py user-iso
./scp-api.py user-iso upload ./debian-custom-recovery.iso --yes
```

For a large image, use multipart upload. The CLI obtains one presigned URL
per part, checks every returned ETag, and completes the upload only after all
parts were uploaded:

```bash
./scp-api.py user-iso upload ./debian-custom-recovery.iso \
  --name debian-custom-recovery.iso --multipart --part-size-mib 64 --yes
```

Then attach and boot it. Keep the returned attach task UUID and wait for it to
reach `FINISHED` before cycling power:

```bash
export ISO_KEY=debian-custom-recovery.iso

# Use --json to retain the attach task UUID; wait for that task to report FINISHED.
./scp-api.py attach-iso 799611 --user-iso-name "$ISO_KEY" \
  --change-boot-device-to-cdrom --yes --json
# Then inspect the UUID returned above:
./scp-api.py tasks TASK_UUID --json
./scp-api.py power cycle 799611 --yes
```

For a large image, the API prepare response supplies an
`uploadId`; `user-iso upload --multipart` performs that flow. The API
documentation in `netcup-scp-openapi.json` remains authoritative for the
presigned-URL response. The boot-device option requests CD-ROM as the next
boot device.

The sample policy files in
[`firewall-policy-examples/`](firewall-policy-examples/) cover SSH allow-lists,
public services with private admin ports, WireGuard management, and restricted
egress. The WireGuard example exposes only the outer UDP handshake and drops
other provider-level ingress; a guest `wg0` interface is not a separate SCP
NIC, so the guest firewall must enforce the actual `wg0`-only service rule.

## Bootstrap source

The generated customScript contains `{{BOOTSTRAP_URL}}`; the controller
resolves it from `[bootstrap]` in `install-host.toml`. The matching
`REPO_URL` and `REPO_BRANCH` are passed to `bootstrap-remote.py`, so a branch
test downloads the wrapper and the installer subtree from the same branch:

```bash
NETCUP_SCP_API_BOOTSTRAP_REPO_BRANCH=netcup-v2-integration \
  python3 install-host.py --payload target-host.jsonc --dry-run
```

For a nonstandard wrapper location, set
`NETCUP_SCP_API_BOOTSTRAP_URL` too. The URL must be HTTPS. Do not run a live
install until that branch is pushed and the dry-run payload shows the intended
source.

The rationale for the separate login/configure/build-customscript commands,
the local recipe, and the branch-pinned bootstrap is in
[`DESIGN-GUIDE.md`](DESIGN-GUIDE.md).
