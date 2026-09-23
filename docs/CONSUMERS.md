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
Fill both Telegram values only if notifications are wanted; supply neither or
both. The minimal Debian v2 configuration does not accept the older
`notify_backend` or Mattermost keys.

### Install and rehearse

```bash
# Rehearsal: prints/plans actions; executes no commands and writes no files.
./debian-install-v2.py install --config /root/install.json --dry-run

# Fresh-host install: stage1 runs, installs stage2, then reboots automatically.
sudo ./debian-install-v2.py install --config /root/install.json
```

Stage2 appends stdout/stderr to `/root/custom_script.output2`. Read state with:

```bash
sudo ./debian-install-v2.py status --config /root/install.json
```

### Compatibility boundary

v2 rejects v1 environment names such as `SWAP_ARCH`, `SWAP_TOTAL_GB`,
`SWAP_FILES`, and `USE_PARTITION`; it does not translate them. Benchmarking is
intentionally deferred. APT uses release, updates, security, backports,
testing, and unstable; backports are preferred at 600 and unstable remains
pinned at 50.

## Netcup SCP API provisioning

Use a dedicated Python environment for the scripts. `scp-api.py`,
`install-host.py`, and `monitor-task.py` all consume the shared `cli-extended`
package; install it into the same environment used to invoke the scripts:

```bash
cd scripts/netcup
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --editable ../../libraries/cli-extended
```

Create the local secret file and authenticate with the browser device-code
flow:

```bash
cp .env.example .env
chmod 600 .env
vi .env                               # optional target/notification settings
./scp-api.py login
```

Login writes the refresh token to `.env` and enforces mode `0600`. In an
interactive terminal it offers SCP internal names matching `v<digits>` for a
local protected-server denylist. Each candidate includes its server ID,
addresses, and reverse-DNS entries. This denylist is a checkout-local guard,
not a Netcup account lock.

Inspect account inventory and available Debian images, then gather the target
configuration with the wizard. It writes `target-host.jsonc` before the final
confirmation so the exact request can be reviewed:

```bash
./scp-api.py status
./scp-api.py imageflavours --filter debian
./install-host.py wizard --dry-run
./install-host.py wizard
```

The file-driven operation validates and installs the reviewed config. To use a
different file, pass `--config`:

```bash
./install-host.py install --config target-host.jsonc --dry-run
./install-host.py install --config target-host.jsonc
```

To reattach to an existing host and follow the provider customScript output,
set `NETCUP_SCP_API_SSH_HOST` in `.env` (optionally set an exact existing key
in `NETCUP_SCP_API_SSH_IDENTITY_FILE`) and run the SSH-only verb:

```bash
./install-host.py attach
```

This makes no Netcup API calls and never creates a key. It replaces the former
`--attach-only` mode. All three Netcup commands generate grouped usage and
verb-specific help from their registries; bare invocation, `help VERB`, and
version discovery do not need API credentials. Top-level help lists the union
of common output options; command-specific help shows which switches, such as
`--json`, apply to that verb.

`wizard` gathers server-specific values; `install` consumes a complete file.
The frontend does not generate an OS-specific `customScript`; for Debian v2,
generate its JSON bundle in that project and pass it to the wizard with
`--custom-script-file`. Existing Netcup account SSH keys are selected for
operator access. A separate local controller key may be generated only when
the supplied hook needs it; the remote hook controls host-side cleanup, while
the local key defaults to retention for reuse.

To configure Mattermost notifications, use the public incoming-webhook URL
from the Mattermost consumer deployment. Keep it only in `.env`; remote hosts
must use the public hostname, not an internal Docker service name. The complete
setup and firewall/user-ISO workflows are in the
[`Netcup quickstart`](../scripts/netcup/README.md).

When protection is configured, cancel a task with its server ID so the CLI can
verify the task target before issuing the cancel request:

```bash
python3 scp-api.py tasks TASK_UUID cancel --server-id 799611 --yes
```

`status` reports compact server facts and SSH reachability. SSH states include
`no answer` (no SSH endpoint responded) and `rejected` (the endpoint explicitly
refused the session); they are distinct from `no keys match` (SSH responded,
but no tested key authenticated). Key-name matches are tried first. The table's
reverse-DNS column uses only the server detail response's `ipv4Addresses` and
`ipv6Addresses`.

The Netcup installer can report to Mattermost instead of Telegram. After the
first-time setup above, the
consumer deployment's public host is
`mattermost.gstammtisch.dchive.de`; copy only its incoming-webhook URL (not a
Mattermost password or PAT) into `.env`:

```bash
sed -i 's/^NOTIFY_BACKEND=.*/NOTIFY_BACKEND=mattermost/' .env
webhook_url=$(cat ../../nyxloom/mattermost/.ciu/secrets/installer_webhook_url)
sed -i "s#^MATTERMOST_WEBHOOK_URL=.*#MATTERMOST_WEBHOOK_URL=\"$webhook_url\"#" .env
```

The webhook is bound to the producer's configured channel and is post-only;
the remote Debian host must reach the public Mattermost URL, not an internal
Docker service name. Keep `.env` at mode `0600` and never commit it.

To inspect a task once or resume monitoring from another terminal, use the
verb-oriented task CLI:

```bash
./monitor-task.py show TASK_UUID
./monitor-task.py show TASK_UUID --json
./monitor-task.py watch TASK_UUID
./monitor-task.py watch TASK_UUID --poll 2
```

`show` performs one fetch; `watch` polls until `FINISHED`, `ERROR`, `CANCELED`,
or `ROLLBACK`. Missing task UUIDs and malformed UUIDs print command help. Bare
invocation, `help`, and version requests do not read `.env`, load API settings,
or make requests. JSON redacts sensitive response fields; `--debug-raw` is an
explicit, warning-emitting opt-out for troubleshooting and may reveal root
passwords or tokens. Progress is sent to stderr, and Ctrl-C exits cleanly.

For server operations and diagnostics:

```bash
python3 scp-api.py attach-iso 799611 --iso-id 1234
python3 scp-api.py tasks --state RUNNING --server-id 799611
python3 scp-api.py metrics 799611 cpu --hours 24
python3 scp-api.py guest-agent-status 799611
python3 scp-api.py firewall-policies
python3 scp-api.py user-iso
python3 scp-api.py firewall 799611 get
python3 scp-api.py firewall 799611 aa:bb:cc:dd:ee:ff set --user-policy-id 12 --active
python3 scp-api.py power off 799611
python3 scp-api.py power on 799611
python3 scp-api.py power cycle 799611
python3 scp-api.py power reset 799611
```

After an install has returned a task UUID, resume polling from another
terminal with `./monitor-task.py watch TASK_UUID`.

ISO attachment, ISO upload, and firewall changes are confirmed mutations.
Firewall `set` replaces the interface's existing copied/user policy assignment.
Omit the firewall MAC only when the server has exactly one interface; multiple
interfaces require an explicit MAC. `guest-agent-status` reports the
provider's QEMU guest-agent state, not SSH or installer state.

### Firewall policy example: public server, SSH allow-list

The CLI validates and creates/updates policy definitions from inline JSON or a
JSON file, then separately assigns policy IDs to interfaces. Read the current
assignment first, and check that `ingressImplicitRule` is `ACCEPT_ALL` if all
non-SSH ports should remain public:

```bash
python3 scp-api.py firewall 799611 get --consistency-check
```

Create a policy from one of the shipped examples:

```bash
POLICY_JSON=$(python3 scp-api.py firewall-policies create \
  --policy-file firewall-policy-examples/public-ssh-whitelist.json \
  --yes --json)
POLICY_ID=$(jq -r '.id // empty' <<<"$POLICY_JSON")
test -n "$POLICY_ID" || { echo "policy response had no id" >&2; exit 1; }
```

Inline JSON and PUT/update use the same validation:

```bash
python3 scp-api.py firewall-policies create --policy-json \
  '{"name":"ssh-whitelist","rules":[{"direction":"INGRESS","protocol":"TCP","action":"ACCEPT","sources":["198.51.100.10"],"destinationPorts":"22"},{"direction":"INGRESS","protocol":"TCP","action":"DROP","destinationPorts":"22"}]}'
python3 scp-api.py firewall-policies put "$POLICY_ID" \
  --policy-file firewall-policy-examples/public-ssh-whitelist.json
python3 scp-api.py firewall-policies
python3 scp-api.py firewall 799611 set --user-policy-id "$POLICY_ID" --active
python3 scp-api.py tasks --state RUNNING --server-id 799611
```

`firewall set` replaces the complete assignment, so include any existing
policy IDs that must remain. Keep an out-of-band console/rescue path while
testing: a wrong allow-list can immediately remove SSH access.

### User ISO upload and boot

The API supports account-level user ISO storage. The CLI lists and uploads
account user ISOs, then `attach-iso` selects one for a server. A simple
single-part upload is:

```bash
ISO_KEY=debian-custom-recovery.iso
python3 scp-api.py user-iso upload ./debian-custom-recovery.iso --yes

# Keep the returned attach-task UUID and wait for it to be FINISHED.
python3 scp-api.py attach-iso 799611 --user-iso-name "$ISO_KEY" \
  --change-boot-device-to-cdrom --yes --json
python3 scp-api.py tasks TASK_UUID --json
python3 scp-api.py power cycle 799611
```

For large images use the API's multipart flow: prepare with `multipart=true`,
get one presigned part URL per part, upload each part and retain its `ETag`,
then complete the upload with the ordered `ETag`/`partNumber` list. The CLI
wraps this as `python3 scp-api.py user-iso upload FILE --multipart`. The
upload must finish before attaching; `--change-boot-device-to-cdrom` makes the
attached ISO the next boot medium.

See [`scripts/netcup/firewall-policy-examples/`](../scripts/netcup/firewall-policy-examples/)
for SSH allow-lists, public services with restricted admin ports, WireGuard
management, and limited egress. The WireGuard example exposes only the outer
UDP handshake and drops other provider-level ingress. A guest `wg0` is not a
separate SCP NIC, so the guest firewall must enforce SSH/services only on
`wg0`.

## Shared CLI contract

For a new or migrating user-facing Python CLI, install the shared contract
layer from the checkout:

```bash
python3 -m pip install --editable ./libraries/cli-extended
PYTHONPATH=libraries/cli-extended/src pytest -q libraries/cli-extended/tests
```

Declare commands once with `cli_extended.CliRegistry`, `VerbSpec`,
`ArgumentSpec`, and `OptionSpec`. The registry generates parser registration,
grouped help, per-command help, handler dispatch, common options, and Markdown
reference output. It also provides clean default-no confirmation and redacted
progress/logging output. See the package's
[`SPEC`](../libraries/cli-extended/SPEC.md) for the behavioral contract,
[`README`](../libraries/cli-extended/README.md) for a complete example and its
[`consumer guide`](../libraries/cli-extended/docs/CONSUMERS.md) for the
adoption boundary.

The adopting CLI still owns authoritative version data, public verb names and
workflow examples, API/configuration validation, expected exception
classification, secret registration, result schemas, and whether a proposed
mutation is safe. `--yes` only accepts a validated prompt; it is not a domain
safety bypass. Test the real executable with `assert_cli_contract()` and
use `known_verb_errors` for failures that must include the full selected-verb
help. The helper rejects `-h` unless a documented compatibility exception is
enabled. Separately prove that help/version paths perform no credential, API,
file, or mutation side effects.
