# tnt (truenas-tui)

`tnt` is a keyboard driven TUI and scriptable CLI for the vanilla TrueNAS v27 JSON-RPC API. Workflows cover Datasets
and Snapshots, SMB/NFS Shares, snapshot/replication/cloud-sync tasks, Network status, Apps, Containers, Virtual
Machines, local Users, Audit, API Keys, and guarded System actions. Containers and Virtual Machines are separate top
level menu entries. Pool creation and boot-pool changes remain outside scope.

The CLI and TUI share one query and action layer. Every write is planned first, displays the exact API calls, and is
applied through the default deny guard. Destructive actions require typing the target name. See the
[design guide](docs/DESIGN.md) for the safety rules and v27 schema choices, and [API method evidence](docs/API-METHODS-V27.md)
for the verified method signatures. The original storage action matrix remains available in
[`docs/ACTION-MATRIX.snapshot.md`](docs/ACTION-MATRIX.snapshot.md).
Scope and safety choices are explained in the [design guide](docs/DESIGN.md#1-goals-and-non-goals); copyable setup and
workflow examples are in the [consumer guide](docs/CONSUMERS.md#configure-multiple-hosts).

M1 core workflows, M2 management workflows, and It1 multi-host configuration and session control are implemented.
The planned work after them is It2 the non-blocking job engine and read-only overview views, It3 hardened SSH hand-off,
and It4 vanilla-v27 acceptance. See the [milestone plan](docs/DESIGN.md#10-milestones).

At connection time tnt checks the v27 WebSocket method list. Methods missing from a host are reported by
`doctor --online` and refused if a workflow needs them; a method-name match does not grant an RBAC role. See the
[capability-check design](docs/DESIGN.md#23-version-adapters-and-the-method-manifest) and the
[verified v27 contract](docs/API-METHODS-V27.md#v27-adapter-contract).

## Install

```bash
pip install 'tnt[tui]'        # the CLI alone: pip install tnt
```

`truenas_api_client` (LGPL-3.0, official) is installed from a pinned git commit; the pin rationale is in `pyproject.toml`.

## Configure multiple hosts

Copy both committed examples and set each file to mode `0600`:

```bash
cp tnt.toml.example tnt.toml
cp tnt.secrets.toml.example tnt.secrets.toml
chmod 600 tnt.toml
chmod 600 tnt.secrets.toml
```

The non-secret `tnt.toml` stores `default_host` and one `[hosts.NAME]` table per host. Credentials belong only in the
paired `tnt.secrets.toml` under matching `[hosts.NAME]` tables. Both files must be regular files owned by you with
exact mode `0600`; symlinks are refused. tnt validates the opened descriptors before reading. Credentials in
`tnt.toml` are refused. An `api_key_file` path belongs in the secrets file, and the referenced key file must also be
owned by you with exact mode `0600`.

Config and secrets resolve independently. Each uses its explicit `--config` or `--secrets` path first, then its
`TNT_CONFIG` or `TNT_SECRETS` environment variable, then an existing file in the current directory, then
`$XDG_CONFIG_HOME/tnt/` (or `~/.config/tnt/`). The secrets file is paired with config by matching host name and may
live in a different directory. `ws://` is refused. TLS verification is on by default. A host with `tls = "insecure"`
also requires `--insecure` for each connection and a prominent warning acknowledgement when saving that mode.

Set `default_host` to connect automatically. If you leave it unset and configure several hosts, `tnt tui` opens the
host picker and CLI commands require `--host NAME`, showing the available names on error. A single configured host is
selected automatically. Use `tnt hosts list|show|add|edit|remove|test|set-default` to manage the pair. The TUI shows
host name, URL, description, and last connection status; `h` or the command palette opens the picker, and `w` changes
that host's read-only/write-enabled session. Switching hosts invalidates old plans, drains host-bound work, closes
the old session, then connects the new one. SSH connection settings may be recorded now; the SSH hand-off is It3.

Credentials never appear in argv, logs, exceptions, `hosts show`, JSON, or TUI output. Password and API-key fields in
the TUI are hidden while editing. API-key creation writes the one-time key directly to a new mode-0600 file in the
TUI; the CLI can display its one-time result only in non-JSON terminal output. See the [secret-handling rationale](docs/DESIGN.md#7-secrets).

```toml
schema_version = 1
default_host = "lab"

[hosts.lab]
url = "wss://nas.example.invalid/api/current"
username = "admin"
auth = "api_key"
tls = "verify"
ssh_user = "admin"
ssh_key_files = ["/home/me/.ssh/id_ed25519"]
ssh_known_hosts = "/home/me/.ssh/known_hosts"
description = "Primary TrueNAS host"
tags = ["primary", "storage"]

[hosts.password-auth]
url = "wss://nas2.example.invalid/api/current"
username = "operator"
auth = "password"
tls = "verify"
```

```toml
schema_version = 1

[hosts.lab]
api_key = "REPLACE_WITH_API_KEY"
# Or use api_key_file = "/home/me/.config/tnt/lab.key".

[hosts.password-auth]
password = "REPLACE_WITH_PASSWORD"
```

The first example is `tnt.toml`; the second is `tnt.secrets.toml`. `schema_version` is required and currently `1`.
The examples contain placeholders only. API keys use the pinned client's SCRAM flow. Password authentication uses its
supported `PASSWORD_PLAIN` login flow. Authentication failures are fatal and never retried. SSH private-key and
known-host paths are recorded for the later SSH hand-off. M1 user-key edits read an explicit OpenSSH `.pub` file or
validated public-key text; private key material is never accepted or printed.

`tnt apikey list` masks TrueNAS key verifier fields. CLI `create` shows the raw value once in non-JSON terminal output;
use `--write-key` to create a new mode-0600 file without overwriting an existing file. TUI `create` requires an output
path and writes the key directly to that private file without displaying it. With API-key auth, the TUI updates the
selected host's `api_key_file` entry for its next connection. The value is never written to `tnt.toml`.
v27 has no separate revoke RPC, so `apikey revoke` uses destructive `api_key.delete` and requires the current name as
typed confirmation. See the [API key design](docs/DESIGN.md#api-keys), [consumer workflow](docs/CONSUMERS.md#api-keys),
and [v27 method evidence](docs/API-METHODS-V27.md#api-keys).

## Use

```bash
tnt --version
tnt hosts list
tnt hosts show lab
tnt doctor --online --host lab       # one login, no retry
tnt dataset list --host lab --json
tnt dataset create tank/home --host lab --type filesystem --compression LZ4 --allow-write
tnt snapshot create tank/home nightly --host lab --allow-write
tnt snapshot rollback tank/home@nightly --host lab --allow-write --confirm tank/home@nightly
tnt container list --host lab --json
tnt container create web --host lab --image nginx --image-version latest --pool tank --no-autostart --allow-write
tnt container restart web --host lab --allow-write --confirm web
tnt vm list --host lab --json
tnt vm create guest --host lab --memory 2048 --vcpus 2 --disk-zvol tank/vms/guest --disk-size 10737418240 --allow-write
tnt vm console guest --host lab --json
tnt user list --host lab --json
tnt user create deploy "Deploy user" --host lab --group 1001 --password-disabled --allow-write
tnt user update deploy --host lab --full-name "Deployment service" --allow-write
tnt user access deploy --host lab --groups 1001,1002 --allow-write --confirm deploy
tnt user ssh-key set deploy --host lab --file ~/.ssh/id_ed25519.pub --add --allow-write
tnt user password deploy --host lab --password-file /home/me/.config/tnt/new-password --allow-write
tnt user ssh-key remove deploy --host lab --allow-write
tnt user delete deploy --host lab --allow-write --confirm deploy
tnt app list --host lab --json
tnt app restart demo --host lab --allow-write
tnt app uninstall demo --host lab --allow-write --confirm demo
tnt network show --host lab --json
tnt audit --host lab --user admin --event USER_LOGIN --limit 100 --json
tnt apikey list --host lab --json
tnt apikey create deploy-key deploy --host lab --expires-at 2027-01-01T00:00:00Z --allow-write --write-key /home/me/.config/tnt/deploy.key
tnt apikey rename 4 automation --host lab --allow-write
tnt apikey revoke 4 --host lab --allow-write --confirm automation
tnt tui --host lab
tnt tui --host lab --allow-write
```

Each write command requires `--allow-write`; destructive commands also require `--confirm TARGET`. Use `--dry-run` to
render the plan without applying it. Changing the current login user's password additionally requires `--confirm-self`;
other users' password changes are planned writes. Password entry uses a no-echo prompt or `--password-file`; tnt does
not accept a password argument. API payloads not covered by a minimal create wizard can be supplied as a JSON `--file`.
User updates accept only `full_name`, `shell`, and `home`. Group membership and `password_disabled`, `locked`, and
`ssh_password_enabled` changes use the separate `user access` workflow. Every access change is destructive and needs
`--confirm USER`; the current login account additionally needs `--confirm-self`, and tnt checks `auth.me` again at
apply. Built-in, immutable, root, and admin accounts are refused by this workflow. Current-login SSH-key changes also
require `--confirm-self` and typed confirmation. `user.update` refuses `random_password` and username rename. See the
[user update design](docs/DESIGN.md#user-account-updates).

VM creation uses a `vm.create` plan followed by separately reviewed `vm.device.create` plans for its required disk
and any optional ISO or NIC. The v27 schema excludes devices from `vm.create`, so a later device-plan failure can
leave the VM record in place; inspect it before retrying. VM console output includes the v27 console command, display
bind/port endpoints (or `auto-assigned`), and web display URI when configured; tnt does not embed a VNC client.

`container list` reports name, state, autostart, CPU affinity (`cpuset`), and current CPU/memory usage from the
`container.metrics` event source; `container update` changes autostart and CPU affinity. If a running container
produces no event within the bounded read window, output says so explicitly. The verified container CRUD record has
no current image field, CPU-count setting, or configured memory limit; list output and the TUI label those fields as unavailable. Dataset
properties include used/available space, quota/refquota, compression, readonly, mount state, and mountpoint. User list
output masks SSH keys and password hashes. The user SSH-key commands accept OpenSSH public keys only.

`tnt network show --json` and System > Network are read-only views of configured interfaces, live IP addresses, installed
routes, DNS nameservers, and default gateways. See the [network adoption example](docs/CONSUMERS.md#network-status) and
the [v27 read signatures](docs/API-METHODS-V27.md#network-status).

`tnt app list` reports installed app state and v27's `upgrade_available` flag. Restart runs the v27 stop and start jobs
in sequence; uninstall requires typing the app ID and preserves TrueNAS-managed data volumes unless
`--delete-data-volumes` is explicitly supplied. See the [Apps guide](docs/CONSUMERS.md#apps) and
[v27 method evidence](docs/API-METHODS-V27.md#apps).

`tnt audit` reads filtered, paginated audit entries and scrubs known secret fields and inline secret patterns before
display. The v27 API does not expose a general middleware/syslog tail method. See the
[Audit guide](docs/CONSUMERS.md#audit) and [v27 audit signature](docs/API-METHODS-V27.md#audit).

v27 has no container restart RPC, so `container restart` plans a stop followed by a start and requires the container
name as destructive confirmation.

SMB/NFS shares can be listed, inspected, created from a v27 JSON payload, and deleted through the CLI and TUI. Share
creation checks the path on the TrueNAS host and refuses missing paths and boot-pool paths; deleting a share removes
only its share configuration, never the data. SMB output includes the share ACL. See the [share adoption examples](docs/CONSUMERS.md#shares).

System actions are under `System > Update`, `Reboot`, `Shutdown`, and `General Settings` (configuration export).
Update check shows the current train/version, the exact available target train/version, and a changelog summary. Apply
uses the shown version as typed confirmation and leaves reboot separate. Reboot and shutdown show active jobs, running
VMs and containers, and the active SMB-session count; any running job blocks apply unless `--force-with-jobs` is
explicit. Every power/update action still requires typing the host name or target version shown in its plan.
If the connection drops after reboot or shutdown submission, the TUI treats the result as uncertain, never resubmits the
power action, and asks you to verify host state before retrying.

```sh
tnt system update check --host lab --json
tnt system update apply TrueNAS-27.0.0-RC.2 --host lab --allow-write --confirm TrueNAS-27.0.0-RC.2
tnt system reboot --host lab --delay 60 --allow-write --confirm HOST_NAME_FROM_PLAN
tnt system shutdown --host lab --allow-write --confirm HOST_NAME_FROM_PLAN
tnt system config-export /home/me/truenas-config.tar --host lab --allow-write
```

Configuration export writes a new mode-0600 tar file, includes the TrueNAS secret seed, and refuses overwrite. Store
the file as a credential. See the [system action decisions](docs/DESIGN.md#system-actions),
[copyable system commands](docs/CONSUMERS.md#system-actions), and [verified v27 methods](docs/API-METHODS-V27.md#system-actions).

Data Protection includes periodic snapshot task create/list/show/run/enable/disable/delete, plus replication and
cloud-sync task list/show/run/enable/disable. Run plans show replication source and target and require the typed task
name; cloud-sync credential fields are always masked. Snapshot task creation validates its dataset, cron schedule,
retention, and naming schema. See [task commands](docs/CONSUMERS.md#data-protection-tasks).

tnt scans every non-read API payload for boot-pool names and paths, including nested container, VM, sharing, app, and
user fields, and resolves pool references against the connected host before allowing a plan to apply. Dataset deletion
requires `--recursive` when the target is mounted, has children, or is attached to a running workload, and always
requires typing the full dataset name.

TUI keys: arrows/enter navigate the TrueNAS menu, `ctrl+p` opens the command palette, `r` refreshes, `?` shows keys,
and `q` quits. Destructive action dialogs display the plan and ask for the exact confirmation target.

## Tests

Tests use the in-memory `tnt.testing.FakeServer` over loopback with the real `truenas_api_client`. No test contacts a
TrueNAS host. The optional Textual, websockets, and test dependencies are required by the test suite; missing
dependencies fail the suite. A host integration lane is available only with the explicit `live` marker and the
`TNT_LIVE=1` opt-in; it creates and cleans up resources under a `tnt-live-` prefix. It is not part of the normal suite.
See the [live testing guide](docs/LIVE-TESTING.md) for required variables, host restrictions, and cleanup behavior.

```bash
PYTHONPATH=src nice python -m pytest -q -p no:xdist
```
