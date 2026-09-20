# Consumers guide

## Debian fresh-install v2

Use `scripts/debian-install-v2/debian_install_v2/` only on a freshly installed,
supported Debian host. The current minimal release applies a fixed disk shape:
32 GiB of native GPT swap in eight partitions, with zswap configured before
`swap.target`.

### Paste-ready install config

Create `/root/install.json` on the target:

```json
{
  "schema_version": 1,
  "fresh_install": true,
  "swap_disk_total_gb": 32,
  "swap_file_count": 8,
  "zswap_compressor": "zstd",
  "telegram_bot_token": "",
  "telegram_chat_id": "",
  "auto_reboot_after_stage1": true,
  "never_reboot": false
}
```

The committed equivalent is
[`scripts/debian-install-v2/known-shape.json`](../scripts/debian-install-v2/known-shape.json).
Fill both Telegram values only if notifications are wanted; supply neither or both.

### Install and rehearse

```bash
# Rehearsal: prints/plans actions; executes no commands and writes no files.
./debian-install-v2.py --action install --config /root/install.json --dry-run

# Fresh-host install: stage1 runs, installs stage2, then reboots automatically.
sudo ./debian-install-v2.py --action install --config /root/install.json
```

Stage2 appends stdout/stderr to `/root/custom_script.output2`. Read state with:

```bash
sudo ./debian-install-v2.py --action status --config /root/install.json
```

### Compatibility boundary

v2 rejects v1 environment names such as `SWAP_ARCH`, `SWAP_TOTAL_GB`,
`SWAP_FILES`, and `USE_PARTITION`; it does not translate them. Benchmarking is
intentionally deferred. APT uses release, updates, security, backports,
testing, and unstable; backports are preferred at 600 and unstable remains
pinned at 50.

## Netcup SCP API provisioning

From `scripts/netcup/`, create the refresh token and local image recipe before
the first install:

```bash
python3 scp-api.py login
python3 scp-api-install-host.py configure
python3 scp-api-install-host.py --payload target-host.jsonc --dry-run
python3 scp-api-install-host.py --payload target-host.jsonc --monitor
```

`scp-api.py login` writes the refresh token to the local `.env` with mode `0600`.

The interactive install lists existing Netcup account SSH keys and uses the
first selected key by default; choosing one does not register a new account
key. For a direct payload run, pin an existing key explicitly:

```bash
python3 scp-api-install-host.py --payload target-host.jsonc --ssh-key-id 123 --monitor
```

The local controller identity used for monitoring is separate. A new account
key is registered only when no account key exists or the interactive create-new
choice is selected.

The generated `default-recipe.jsonc` is local and ignored. Its customScript
uses a controller-side bootstrap placeholder; configure a feature-branch
source before a live test:

```bash
export NETCUP_SCP_API_BOOTSTRAP_REPO_BRANCH=netcup-v2-integration
python3 scp-api-install-host.py --payload target-host.jsonc --dry-run
```

The dry run must show the intended branch before a real Netcup API install is
confirmed. See [`scripts/netcup/README.md`](../scripts/netcup/README.md) for
attach-only, exploration, and bootstrap-source details. To inspect the API
inventory before choosing a target, use `scp-api.py imageflavours --filter
debian` or `scp-api.py iso-bootable --filter rescue`; without a server ID these
enumerate all servers and label each result with its source. An image flavour is
a reinstallable OS/image variant, while an ISO image is bootable installer or
recovery media. State-changing explorer options require an explicit server ID
and use positional actions, for example `scp-api.py snapshots 799611 create`
or `scp-api.py power cycle 799611`.

For server operations and diagnostics:

```bash
python3 scp-api.py attach-iso 799611 --iso-id 1234
python3 scp-api.py tasks --state RUNNING --server-id 799611
python3 scp-api.py metrics 799611 cpu --hours 24
python3 scp-api.py guest-agent-status 799611
python3 scp-api.py firewall-policies
python3 scp-api.py firewall 799611 get
python3 scp-api.py firewall 799611 aa:bb:cc:dd:ee:ff set --user-policy-id 12 --active
python3 scp-api.py power off 799611
python3 scp-api.py power on 799611
python3 scp-api.py power cycle 799611
python3 scp-api.py power reset 799611
```

ISO attachment and firewall assignment are confirmed mutations. Firewall
`set` replaces the interface's existing copied/user policy assignment; it does
not create firewall policies or rules. Omit the firewall MAC only when the
server has exactly one interface; multiple interfaces require an explicit MAC.
`guest-agent-status` reports the provider's QEMU guest-agent state, not SSH or
installer state.

### Firewall policy example: public server, SSH allow-list

The CLI can read and assign policies, but policy/rule creation is intentionally
kept as an explicit API request. Read the current assignment first, and check
that `ingressImplicitRule` is `ACCEPT_ALL` if all non-SSH ports should remain
public:

```bash
python3 scp-api.py firewall 799611 get --consistency-check
```

Create a policy using the SCP API. `$ACCESS_TOKEN` is a short-lived bearer
token and `$SCP_USER_ID` is the SCP user ID, not the CCP customer number. If
needed, discover the latter from the OIDC userinfo endpoint:

```bash
set -o pipefail
SCP_USER_ID=$(curl --fail-with-body -sS \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/userinfo' | jq -r .id)
```

Replace the documentation-only TEST-NET addresses with real addresses before
use:

```bash
BASE_URL=https://www.servercontrolpanel.de/scp-core
POLICY_JSON=$(curl --fail-with-body -sS -X POST \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  "$BASE_URL/api/v1/users/$SCP_USER_ID/firewall-policies" \
  --data '{
    "name": "ssh-whitelist",
    "description": "Allow SSH only from approved administrator addresses",
    "rules": [
      {"direction":"INGRESS", "protocol":"TCP", "action":"ACCEPT",
       "sources":["198.51.100.10", "203.0.113.0/24"],
       "destinationPorts":"22"},
      {"direction":"INGRESS", "protocol":"TCP", "action":"DROP",
       "destinationPorts":"22"}
    ]
  }')
POLICY_ID=$(jq -r '.id // empty' <<<"$POLICY_JSON")
test -n "$POLICY_ID" || { echo "policy response had no id" >&2; exit 1; }
python3 scp-api.py firewall-policies
python3 scp-api.py firewall 799611 set --user-policy-id "$POLICY_ID" --active
python3 scp-api.py tasks --state RUNNING --server-id 799611
```

`firewall set` replaces the complete assignment, so include any existing
policy IDs that must remain. Keep an out-of-band console/rescue path while
testing: a wrong allow-list can immediately remove SSH access.

### User ISO upload and boot

The API supports account-level user ISO storage. `scp-api.py` accepts a user ISO
name for attachment but does not currently wrap the upload itself. A simple
single-part upload is:

```bash
BASE_URL=https://www.servercontrolpanel.de/scp-core
ISO_KEY=debian-custom-recovery.iso
UPLOAD_JSON=$(curl --fail-with-body -sS -X POST \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$BASE_URL/api/v1/users/$SCP_USER_ID/isos/$ISO_KEY?multipart=false")
UPLOAD_URL=$(jq -r '.presignedUrl // empty' <<<"$UPLOAD_JSON")
test -n "$UPLOAD_URL" || { echo "upload response had no presignedUrl" >&2; exit 1; }
curl --fail-with-body -sS --upload-file ./debian-custom-recovery.iso "$UPLOAD_URL"

# Keep the returned attach-task UUID and wait for it to be FINISHED.
python3 scp-api.py attach-iso 799611 --user-iso-name "$ISO_KEY" \
  --change-boot-device-to-cdrom --yes --json
python3 scp-api.py tasks TASK_UUID --json
python3 scp-api.py power cycle 799611
```

For large images use the API's multipart flow: prepare with `multipart=true`,
get one presigned part URL per part, upload each part and retain its `ETag`,
then complete the upload with the ordered `ETag`/`partNumber` list. The upload
task must finish before attaching; `--change-boot-device-to-cdrom` makes the
attached ISO the next boot medium.
