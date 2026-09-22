# Netcup SCP installation tools

These scripts provision disposable or fresh hosts through the Netcup Server
Control Panel API. They can optionally pass an operator-supplied cloud-init
`customScript` hook to the provider. The hook may come from
`debian-install-v2`, but this Netcup frontend does not build or interpret it.

## First-time setup

From the repository root, make an isolated environment for these scripts. The
remaining setup commands below assume the current directory is `scripts/netcup`.
`scp-api.py`, `install-host.py`, and `monitor-task.py` use the repository's
`cli-extended` package, installed in the same environment that runs them:

```bash
cd scripts/netcup
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --editable ../../libraries/cli-extended
```

All three commands use the family version in `VERSION` and generate their
grouped usage and verb help from their command registries. Discovery is
side-effect free and does not require a token or API settings:

```bash
./scp-api.py --help
./install-host.py help wizard
./monitor-task.py --version
```

Bare invocation prints top-level usage, whose common output options are the
union supported by the CLI. Use `help VERB` or `VERB --help` for detailed
command help; that selected-verb help shows which options such as `--json`
actually apply.

Then create the local secret file. A target is optional in an interactive
terminal: the installer can ask the authenticated API for a server list later.

```bash
cp .env.example .env
chmod 600 .env
vi .env                               # optional target/notification settings
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
and Debian image installation. Read-only queries, snapshot `dryrun`,
and installer `--dry-run` remain available. Account-level user-ISO upload and
firewall-policy create/PUT are not server-targeted; applying a policy with
`firewall SERVER set` is guarded. Task cancellation needs
`--server-id` while the denylist is configured so the task can be checked
against the protected target.

To change the selection, run login again and choose additional servers. For a
non-interactive login, the token is still saved but selection is skipped; set
the validated `v<digits>` names in `.env` or run login from a terminal.

### Quickstart: inspect and install a Debian VM

The installer has two separate workflows. `wizard` gathers server-specific
facts live and writes a complete local target config. `install` consumes that
reviewed file without gathering or silently filling values. `configure` is a
compatibility alias for `wizard`; it is no longer a separate server-dependent
recipe command.

```bash
./scp-api.py status
./scp-api.py servers
./scp-api.py imageflavours --filter debian

# Gather and save a complete target config, then ask before installing.
./install-host.py wizard

# Equivalent compatibility spelling:
./install-host.py configure

# Review the generated file, then install exactly that file and monitor it.
./install-host.py install

# Preview the gathered payload and account-key decision. Authentication,
# target lookup, and the protected-server check happen before the local
# controller key is reused/generated. No mutating API call is made and no
# target-host.jsonc is saved.
./install-host.py wizard --dry-run

# Build a Debian-v2 hook in its own project, then let the Netcup wizard consume
# the resulting JSON bundle (the wizard remains provider-agnostic):
../debian-install-v2/debian-install-v2.py --action build-customscript \
  --config debian-v2.json --controller-ssh-placeholder > debian-v2-customscript.json
./install-host.py wizard --custom-script-file debian-v2-customscript.json

# A different reviewed config can be selected explicitly.
./install-host.py install --config target-host-r1002.jsonc
```

The installer writes `target-host.jsonc` before its final install confirmation
so the exact request can be reviewed or reused. It is local and gitignored.
Existing Netcup account keys are persistent operator keys: choose `all`,
`none`, or a comma-separated selection, or repeat `--ssh-key-id`. The
installer never creates an account key. The separate local controller
identity is generated/reused for bootstrap monitoring and is never registered
in the Netcup account.

The generic controller-key policy defaults to local `retain`. The consumed
customScript owns any remote key cleanup. If its JSON bundle declares a
`completionMarker`, the Netcup monitor can safely wait for that marker before
applying `--local-controller-key remove`; without one, the local key is kept.

If the dry-run looks correct, the file-driven install can be made
non-interactive:

```bash
./install-host.py install --config target-host.jsonc --yes
```

`--yes` skips confirmation, so use it only after reviewing the dry-run and
the selected customScript source.

The wizard does not invent a customScript. Use `--custom-script-file` to
consume a plain command or a JSON bundle with `customScript` (and optionally
`completionMarker`). A file-driven install never loads a hidden recipe; it
sends the one in the selected target file, if present. A file with no
customScript also does not create a temporary controller key.

To preview or repeat a gathered request explicitly, use the generated file:

```bash
./install-host.py install --config target-host.jsonc --dry-run
./install-host.py install --config target-host.jsonc --ssh-key-id 123
```

The `install` command validates that the config is a JSON/JSONC object with a
positive `serverId` (or a resolvable hostname), positive `imageFlavourId`, and
non-empty `diskName` before authenticating or generating any key. `sshKeyIds`
and `customScript` are intentionally optional. The deprecated `--payload FILE`
spelling remains an alias for `--config FILE`.

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
PAT or REST client. Notification settings belong in the customScript
producer's v2 JSON; `install-host.py` does not read or rewrite Telegram or
Mattermost values.

For Debian v2, build the bundle in that project and review its generated
`config` object and `customScript` before handing it to this tool:

```bash
../debian-install-v2/debian-install-v2.py --action build-customscript \
  --config debian-v2.json --controller-ssh-placeholder \
  > debian-v2-customscript.json
```

## Install workflow

Preview the reviewed file without mutating the Netcup account:

```bash
./install-host.py install --config target-host.jsonc --dry-run
```

Run the gather-and-install wizard and monitor the provider task:

```bash
./install-host.py wizard --monitor
```

Use `install --config FILE` for a separately prepared complete payload. The
file-driven command monitors the task by default; `--no-monitor` returns after
task creation. To inspect or monitor a task after the installer has exited,
use the standalone task CLI. `show` fetches once; `watch` polls until the
provider reports a terminal state. Bare invocation prints usage and performs
no credential or API work.

```bash
./monitor-task.py show TASK_UUID
./monitor-task.py show TASK_UUID --json
./monitor-task.py watch TASK_UUID
./monitor-task.py watch TASK_UUID --poll 2
```

`install-host.py attach` is the SSH-only counterpart for reconnecting to an
existing install and following its provider customScript output. It requires
`NETCUP_SCP_API_SSH_HOST` in `.env` or `--ssh-host`, makes no Netcup API calls,
and never creates a local key. It uses normal SSH identity discovery unless
you select an existing private key with `NETCUP_SCP_API_SSH_IDENTITY_FILE` or
`--ssh-identity-file`:

```bash
./install-host.py attach
```

The `attach` verb appears in `./install-host.py --help`; it replaces the
former `--attach-only` mode.

JSON output redacts response fields such as generated root passwords. The
explicit `--debug-raw` opt-out prints secret-bearing fields and request/response
diagnostics; it warns on stderr. `watch` progress also goes to stderr, so it
does not corrupt machine-readable output. Pressing Ctrl-C cancels cleanly.

During the interactive key step, existing Netcup account keys are listed.
Enter selects all, `none` selects no persistent account key, and a
comma-separated list selects specific entries. Choosing keys uses them without
registering a new account key. To pin an existing key in a direct payload run,
pass its ID (and repeat the option for multiple IDs):

```bash
python3 install-host.py install --config target-host.jsonc --ssh-key-id 123
```

The local `--ssh-identity-file` is a separate controller key used for
customScript access and monitoring. If it is omitted, an existing valid key whose
filename contains the selected hostname/nickname is reused first, including
older dated installer filenames; only then is a new key generated. An
explicit missing or invalid path is an error. `--ssh-key-id` refers only to a
key already registered in the Netcup account.

`install-host.py` with no arguments prints usage and performs no API or SSH
work. Its `wizard`, `configure`, and `install` commands are documented by
`install-host.py --help`. `scp-api.py` provides read-only account/server
inspection and explicitly gated reversible actions. Read-only resource commands
enumerate every server when no ID is supplied, because the SCP API exposes
image flavours, ISO images, disks, rescue status, snapshots, and ISO attachment
status below each server. Results from an account-wide query include the source
server ID/name. Use a server ID to inspect only one server; mutating actions
such as `detach`, `deactivate`, `create`, and `dryrun` are positional and still
require it. `servers` lists the account inventory; use `server-details SERVER_ID`
for one server's full record. `status` is the compact live view: it queries
server details and prints vname, hostname (or nickname), run state,
architecture, CPU count, RAM/disk in GiB, and an `ssh-connect` result. The
final multiline reverse-DNS column contains only the addresses from the detail
response's `ipv4Addresses` and `ipv6Addresses`; nested live interface data is
not merged into that inventory. Status uses at most four workers, preserves
server-table order, loads the key list once, and performs reverse-DNS lookups
concurrently without caching duplicate addresses. `ssh-connect` first does one
SSH service probe per address until one responds, with a 2-second default timeout, then probes
every recognizable private key in `~/.ssh` plus the configured
`install-host.toml` identity (without creating a key). Server-name, hostname,
and nickname matches in key filenames are tried first. It reports the names of
keys that authenticate, `no keys match`, `rejected`, or `no answer`. `no keys
match` means the SSH service answered but every tested key failed public-key
authentication. `rejected` means the SSH endpoint explicitly refused the
session; `no answer` means no advertised address responded. `no keys found`,
`no server IP`, and configuration/client errors are reported separately when
those are the actual local condition. Use `--ssh-timeout SECONDS` to override
the 2-second probe timeout.

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

## CustomScript source

The customScript producer is responsible for its remote URL, repository
branch, JSON configuration, notification credentials, and remote controller-key
policy. For Debian v2, use its `build-customscript` action and pass the JSON
bundle to `wizard --custom-script-file`, or copy the bundle's `customScript`
string into a reviewed `target-host.jsonc`. Netcup only validates the generic
controller-key marker and submits the resulting opaque command.

If the bundle declares `completionMarker`, the wizard carries it into task
monitoring. For a file-driven target, provide the same generic contract
explicitly, for example:

```bash
./install-host.py install --config target-host.jsonc \
  --completion-marker /var/lib/example/install-done
```

The rationale for this producer/consumer boundary is in
[`DESIGN-GUIDE.md`](DESIGN-GUIDE.md).
