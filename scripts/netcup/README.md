# Netcup SCP installation tools

These scripts provision disposable or fresh Debian hosts through the Netcup
Server Control Panel API and feed them the `debian-install-v2` bootstrap.

## First-time setup

From this directory, create the OAuth refresh token with the wizard:

```bash
python3 scp-api.py login
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
and wizard modes. `scp-api.py` provides read-only account/server
inspection and explicitly gated reversible actions. Read-only resource commands
enumerate every server when no ID is supplied, because the SCP API exposes
image flavours, ISO images, disks, rescue status, snapshots, and ISO attachment
status below each server. Results from an account-wide query include the source
server ID/name. Use a server ID to inspect only one server; mutating actions
such as `detach`, `deactivate`, `create`, and `dryrun` are positional and still
require it. `servers` lists the account inventory; use `server-details SERVER_ID`
for one server's full record.

An image flavour is a server-compatible reinstallable OS/image variant (for
example a Debian 13 UEFI amd64 image), not a VM template. ISO images are
bootable installer or recovery media. Useful first queries are:

```bash
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
./scp-api.py firewall 799611 get
./scp-api.py firewall 799611 aa:bb:cc:dd:ee:ff set --user-policy-id 12 --active
./scp-api.py power off 799611
./scp-api.py power on 799611
./scp-api.py power cycle 799611
./scp-api.py power reset 799611
```

`--filter` is case-insensitive and searches the returned fields, including an
image flavour's name and alias or an ISO image's name, description, and
architecture. All commands support `--help` and `--json`; no short `-h` alias
is used so the complete public spelling is visible in generated usage.

`attach-iso` changes the server's attached media and requires either an ISO ID
from `iso-bootable` or the name of an uploaded user ISO. `metrics` returns the
raw timestamped SCP data for CPU, disk, or network lookback windows.
`guest-agent-status` reports QEMU guest-agent availability; it is not an SSH
or bootstrap health check. Firewall `get`/`set` operates on one interface MAC:
if the server has exactly one interface, omit the MAC and the command resolves
it from live server details; multiple interfaces require an explicit MAC.
`set` replaces copied/user policy assignments and requires an explicit firewall
active state. It assigns existing policies; it does not create or edit policy
rules.

### Read, create, and assign a firewall policy

Read the current assignment before changing it. With one interface the MAC is
optional; with several interfaces, obtain the MAC from `server-details` and
provide it explicitly:

```bash
./scp-api.py firewall 799611 get --consistency-check
./scp-api.py firewall 799611 aa:bb:cc:dd:ee:ff get --consistency-check
```

The explorer lists and assigns policies but deliberately does not create rule
sets. The SCP API does create them. The following creates a policy that accepts
SSH from two example addresses and drops other SSH traffic, while leaving
other traffic to the existing implicit firewall rule. Replace the TEST-NET
addresses with the real administrator addresses. First obtain an access token
from the authenticated SCP session and the SCP user ID; never put the refresh
token in a curl command:

```bash
export SCP_BASE_URL=https://www.servercontrolpanel.de/scp-core
export ACCESS_TOKEN='access-token-from-the-authenticated-session'
export SCP_USER_ID=12345  # SCP user id, not the CCP customer number

# Or discover that SCP user id with the bearer token:
set -o pipefail
export SCP_USER_ID=$(curl --fail-with-body -sS \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/userinfo' | jq -r .id)

curl --fail-with-body -sS \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$SCP_BASE_URL/api/v1/users/$SCP_USER_ID/firewall-policies" | jq .

POLICY_JSON=$(curl --fail-with-body -sS -X POST \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  "$SCP_BASE_URL/api/v1/users/$SCP_USER_ID/firewall-policies" \
  --data '{
    "name": "ssh-whitelist",
    "description": "Allow SSH only from approved administrator addresses",
    "rules": [
      {
        "direction": "INGRESS",
        "protocol": "TCP",
        "action": "ACCEPT",
        "sources": ["198.51.100.10", "203.0.113.0/24"],
        "destinationPorts": "22"
      },
      {
        "direction": "INGRESS",
        "protocol": "TCP",
        "action": "DROP",
        "destinationPorts": "22"
      }
    ]
  }')
POLICY_ID=$(jq -r '.id // empty' <<<"$POLICY_JSON")
test -n "$POLICY_ID" || { echo "policy response had no id" >&2; exit 1; }
printf '%s\n' "$POLICY_JSON" | jq .
```

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
`scp-api.py` currently attaches an already-uploaded user ISO but does not wrap
the raw-object upload flow. For an ordinary-sized image, prepare a single-part
upload, send the file to the returned presigned URL without the bearer token,
then attach it:

```bash
export ISO_KEY=debian-custom-recovery.iso

UPLOAD_JSON=$(curl --fail-with-body -sS -X POST \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$SCP_BASE_URL/api/v1/users/$SCP_USER_ID/isos/$ISO_KEY?multipart=false")
UPLOAD_URL=$(jq -r '.presignedUrl // empty' <<<"$UPLOAD_JSON")
test -n "$UPLOAD_URL" || { echo "upload response had no presignedUrl" >&2; exit 1; }
curl --fail-with-body -sS --upload-file ./debian-custom-recovery.iso "$UPLOAD_URL"

# Use --json to retain the attach task UUID; wait for that task to report FINISHED.
./scp-api.py attach-iso 799611 --user-iso-name "$ISO_KEY" \
  --change-boot-device-to-cdrom --yes --json
# Then inspect the UUID returned above:
./scp-api.py tasks TASK_UUID --json
./scp-api.py power cycle 799611
```

For a large image use `multipart=true`: the prepare response supplies an
`uploadId`; fetch a presigned URL for every part at
`.../isos/$ISO_KEY/$UPLOAD_ID/parts/$PART_NUMBER`, upload each part and record
its returned `ETag`, then `PUT` the ordered list of `{"ETag": ..., "partNumber":
...}` objects to `.../isos/$ISO_KEY/$UPLOAD_ID`. The API documentation in
`netcup-scp-openapi.json` is authoritative for part sizing and the current
presigned-URL response. Do not power-cycle until the attach task has finished;
the boot-device option requests CD-ROM as the next boot device.

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
