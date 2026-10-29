# tnt (truenas-tui)

`tnt` is a keyboard driven TUI and scriptable CLI for the vanilla TrueNAS v27 JSON-RPC API. Workflows cover Datasets
and Snapshots, SMB/NFS Shares, snapshot/replication/cloud-sync tasks, Network status, Apps, Containers, Virtual
Machines, local Users, Audit, API Keys, guarded System actions, and the read-only Dashboard, Alerts, Jobs, Services,
and Storage health views. Containers and Virtual Machines are separate top-level menu entries. Pool creation and
boot-pool changes remain outside scope.

The CLI and TUI share one query and action layer. Every write is planned first, displays the exact API calls, and is
applied through the default deny guard. Destructive actions require typing the target name. See the
[design guide](docs/DESIGN.md) for the safety rules and v27 schema choices, and [API method evidence](docs/API-METHODS-V27.md)
for the verified method signatures. The original storage action matrix remains available in
[`docs/ACTION-MATRIX.snapshot.md`](docs/ACTION-MATRIX.snapshot.md).
Scope and safety choices are explained in the [design guide](docs/DESIGN.md#1-goals-and-non-goals); copyable setup and
workflow examples are in the [consumer guide](docs/CONSUMERS.md#configure-multiple-hosts).

M1 core workflows, M2 management workflows, It1 multi-host configuration and session control, It2 job tracking and
read-only core views, and It3 hardened interactive SSH hand-off are implemented. It4 vanilla-v27 acceptance is recorded
in the [live acceptance record](docs/LIVE-TESTING.md#live-acceptance-run-2026-10-29); release readiness remains. See the
[milestone plan](docs/DESIGN.md#10-milestones).

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
owned by you with exact mode `0600`. Each saved credential also carries the exact `host_url` it was entered for; tnt
checks it against the selected host before authentication. If an older secrets entry has no `host_url`, or the host URL
has changed, replace the credential with `tnt hosts edit NAME --set-secret` before connecting. This prevents a cwd
config and an independently resolved XDG secrets file from pairing a same-named credential with another appliance.

Config and secrets resolve independently. Each uses its explicit `--config` or `--secrets` path first, then its
`TNT_CONFIG` or `TNT_SECRETS` environment variable, then an existing file in the current directory, then
`$XDG_CONFIG_HOME/tnt/` (or `~/.config/tnt/`). The files may live in different directories; a matching host name is
accepted only when the secrets entry's `host_url` also matches the configured endpoint. `ws://` is refused. TLS
verification is on by default. A host with `tls = "insecure"` also requires `--insecure` for each connection and a
prominent warning acknowledgement when saving that mode.

Set `default_host` to connect automatically. If you leave it unset and configure several hosts, `tnt tui` opens the
host picker and CLI commands require `--host NAME`, showing the available names on error. A single configured host is
selected automatically. Use `tnt hosts list|show|add|edit|remove|test|set-default` to manage the pair. The TUI shows
host name, URL, description, and last connection status; `h` or the command palette opens the picker, and `w` changes
that host's read-only/write-enabled session. Reads, action construction, plan probes, and applies run in workers so
slow RPCs leave the UI responsive. Switching hosts invalidates old plans and cancels old work before closing the
session; if an RPC has not drained after five seconds, the TUI offers Keep waiting or Cancel switch. SSH connection
settings are used by the interactive hand-off described below; the configured SSH paths are never shown by host
listing or JSON output.

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
host_url = "wss://nas.example.invalid/api/current"
api_key = "REPLACE_WITH_API_KEY"
# Or use api_key_file = "/home/me/.config/tnt/lab.key".

[hosts.password-auth]
host_url = "wss://nas2.example.invalid/api/current"
password = "REPLACE_WITH_PASSWORD"
```

The first example is `tnt.toml`; the second is `tnt.secrets.toml`. `schema_version` is required and currently `1`.
The examples contain placeholders only. API keys use the pinned client's SCRAM flow. Password authentication uses its
supported `PASSWORD_PLAIN` login flow. Authentication failures are fatal and never retried. M1 user-key edits read an
explicit OpenSSH `.pub` file or validated public-key text; private key material is never accepted or printed.

## Interactive SSH hand-off

Run `tnt ssh [HOST]` to open an interactive shell on the selected host, or press `s` in the TUI (also available from
the command palette). If no host is named, tnt uses `default_host` or selects the only configured host. This command
uses host configuration only; it does not open a TrueNAS API session or load API credentials. See the
[copyable SSH setup and commands](docs/CONSUMERS.md#interactive-ssh-hand-off) and the
[security design](docs/DESIGN.md#82-interactive-ssh-hand-off).

The hand-off requires an SSH user, at least one user-owned identity file with mode `0600` or stricter, and an existing
user-owned known-hosts file that the current user can read but other users cannot modify. OpenSSH ignores user and
system SSH config, clears its built-in identity defaults, uses only those identity files and that known-hosts file,
disables agent and password authentication, and requires an already trusted host key. Populate `ssh_known_hosts` from
an independently verified host key; tnt never runs `ssh-keyscan` or adds keys automatically. Only an interactive
session is supported; tnt does not accept a remote command after the host name. The TUI restores its screen when SSH
exits. The CLI returns the SSH process status. Session content is passed directly between OpenSSH and the terminal,
not captured or logged by tnt.

`tnt apikey list` masks TrueNAS key verifier fields. CLI `create` shows the raw value once in non-JSON terminal output;
use `--write-key` to create a new mode-0600 file without overwriting an existing file. TUI `create` requires an output
path and writes the key directly to that private file without displaying it. With API-key auth, the TUI updates the
selected host's `api_key_file` entry for its next connection. The value is never written to `tnt.toml`.
v27 has no separate revoke RPC, so `apikey revoke` uses destructive `api_key.delete` and requires the current name as
typed confirmation. See the [API key design](docs/DESIGN.md#api-keys), [consumer workflow](docs/CONSUMERS.md#api-keys),
and [v27 method evidence](docs/API-METHODS-V27.md#api-keys).

## Use

### Read-only overview and jobs

The Dashboard shows v27 host facts plus one bounded hour of CPU and memory history; those graphs are historical samples,
not live utilization. Alerts, Jobs, Services, and Storage health are read-only. Jobs labels its TrueNAS visibility as
host-wide only when `auth.me` confirms `FULL_ADMIN`; other sessions see only jobs TrueNAS exposes to that session. The
TUI refreshes these screens in the background, keeps the last successful sample after a failed refresh, and provides
search, copy, help, and a sanitized error panel. Service lifecycle writes are not available from Services. See the
[job/view design](docs/DESIGN.md#non-blocking-jobs-and-core-read-views) and
[verified v27 contracts](docs/API-METHODS-V27.md#iteration-2-read-view-contract-implemented-against-the-verified-v27-reference).

```sh
tnt dashboard --host lab --json
tnt alerts list --host lab --json
tnt jobs list --host lab --limit 200 --offset 0 --json
tnt services --host lab --json
tnt storage health --host lab --json
tnt tui --host lab  # Dashboard; System > Alerts / Jobs / Services; Storage > Health
```

The `jobs list` JSON `rows` field contains only the requested remote page. On the first page, tnt also returns up to 200
recent process-local records in `local_jobs`; `local_jobs_possibly_more` reports when that ledger was truncated.
Dashboard JSON keeps v27 `system.info.uptime_seconds` as a finite, non-negative number; v27 may return fractional
seconds. It also preserves reporting samples in the shape returned by the host; v27 RC.1 returned ordered numeric rows.
See the [read-view contract](docs/DESIGN.md#non-blocking-jobs-and-core-read-views) and
[Dashboard adoption notes](docs/CONSUMERS.md#read-only-overview-and-jobs).

Applied jobs run through worker threads. Their remote IDs, progress, logs, errors, and generation are recorded; reconnect
reconciliation can leave an invisible or unqueryable job as UNKNOWN. If a multi-call plan stops partway, CLI JSON and
TUI review show each call's outcome and job ID, and the CLI exits nonzero. Stopping local waiting does not abort a remote
job. The Jobs view exposes no remote-abort action.

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
tnt container create web --host lab --image alpine:3.24:amd64:default --image-version 20261016_13:00 --pool tank --no-autostart --allow-write
tnt container restart web --host lab --allow-write --confirm web
tnt vm list --host lab --json
tnt vm create guest --host lab --memory 2048 --vcpus 2 --disk-zvol tank/vms/guest --disk-size 10737418240 --allow-write
tnt vm console guest --host lab --json
tnt user list --host lab --json
tnt user create deploy "Deploy user" --host lab --group 1001 --password-disabled --allow-write
tnt user update deploy --host lab --full-name "Deployment service" --allow-write
tnt user update operator --host lab --shell /bin/sh --allow-write --confirm-self --confirm operator
tnt user access deploy --host lab --groups 1001,1002 --allow-write --confirm deploy
tnt user ssh-key set deploy --host lab --file ~/.ssh/id_ed25519.pub --add --allow-write
tnt user password deploy --host lab --password-file /home/me/.config/tnt/new-password --allow-write
tnt user ssh-key remove deploy --host lab --allow-write
tnt user delete deploy --host lab --allow-write --confirm deploy
tnt app list --host lab --json
tnt app stop demo --host lab --allow-write --confirm demo
tnt app restart demo --host lab --allow-write --confirm demo
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
User updates accept only `full_name`, `shell`, and `home`. Changing the current login user's `shell` or `home` needs
both `--confirm-self` and `--confirm USER`, with `auth.me` checked when planning and again before apply. Group membership and `password_disabled`, `locked`, and
`ssh_password_enabled` changes use the separate `user access` workflow. Every access change is destructive and needs
`--confirm USER`; the current login account additionally needs `--confirm-self`, and tnt checks `auth.me` again at
apply. Built-in, immutable, root, and admin accounts are refused by this workflow. Current-login SSH-key changes also
require `--confirm-self` and typed confirmation. `user.update` refuses `random_password` and username rename. See the
[user update design](docs/DESIGN.md#user-account-updates).

VM names must contain ASCII letters and digits only; tnt refuses other names before contacting TrueNAS. VM creation
uses a `vm.create` plan followed by separately reviewed `vm.device.create` plans for its required disk and any optional
ISO or NIC. The v27 schema excludes devices from `vm.create`, so a later device-plan failure can leave the VM record in
place; inspect it before retrying. VM console output includes the v27 console command, display bind/port endpoints (or
`auto-assigned`), and web display URI when configured; tnt does not embed a VNC client. See the
[VM and container lifecycle design](docs/DESIGN.md#vm-and-container-lifecycle-contracts).

`container list` reports name, state, autostart, CPU affinity (`cpuset`), and current CPU/memory usage from the
`container.metrics` event source; `container update` changes autostart and CPU affinity. If a running container
produces no event within the bounded read window, output says so explicitly. The verified container CRUD record has
no current image field, CPU-count setting, or configured memory limit; list output and the TUI label those fields as unavailable. Dataset
properties include used/available space, quota/refquota, compression, readonly, mount state, and mountpoint. User list
output masks SSH keys and password hashes. The user SSH-key commands accept OpenSSH public keys only.

`tnt network show --json` and System > Network are read-only views of configured interfaces, live IP addresses, installed
routes, DNS nameservers, and default gateways. See the [network adoption example](docs/CONSUMERS.md#network-status) and
the [v27 read signatures](docs/API-METHODS-V27.md#network-status).

`tnt app list` reports installed app state and v27's `upgrade_available` flag. Stop and restart are destructive and
require `--confirm APP_ID`; restart runs the v27 stop and start jobs in sequence. Uninstall also requires typing the app ID and preserves TrueNAS-managed data volumes unless
`--delete-data-volumes` is explicitly supplied. See the [Apps guide](docs/CONSUMERS.md#apps) and
[v27 method evidence](docs/API-METHODS-V27.md#apps).

`tnt audit` reads filtered, paginated audit entries and scrubs known secret fields and inline secret patterns before
display. The v27 API does not expose a general middleware/syslog tail method. See the
[Audit guide](docs/CONSUMERS.md#audit) and [v27 audit signature](docs/API-METHODS-V27.md#audit).

v27 has no container restart RPC, so `container restart` plans a stop followed by a start and requires the container
name as destructive confirmation. Stop and restart request TrueNAS's `force_after_timeout` fallback, then confirm
`status.state == STOPPED` through `container.get_instance` before continuing; a successful middleware job alone does
not prove the container stopped. See the [lifecycle design](docs/DESIGN.md#vm-and-container-lifecycle-contracts).

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
retention, and naming schema. Snapshot-task run behavior on RC.1 and copyable task commands are in the
[consumer guide](docs/CONSUMERS.md#data-protection-tasks); the compatibility reason is in the
[design guide](docs/DESIGN.md#data-protection-tasks).

tnt scans every non-read API payload for boot-pool names and paths, including nested container, VM, sharing, app, and
user fields, and resolves pool references against the connected host before allowing a plan to apply. Dataset deletion
requires `--recursive` when the target is mounted, has children, or is attached to a running workload, and always
requires typing the full dataset name.

TUI keys: arrows/enter navigate the TrueNAS menu, `ctrl+p` opens the command palette, `r` refreshes, `?` shows keys,
and `q` quits after host-bound work drains. Slow reads and plan preparation show a busy message while the UI remains
responsive. Destructive action dialogs display the plan and ask for the exact confirmation target; a drain that exceeds
five seconds offers Keep waiting or Cancel quit.

## Tests

The default suite uses the in-memory `tnt.testing.FakeServer` over loopback with the real `truenas_api_client`; it does
not contact a TrueNAS host. The optional Textual, websockets, and test dependencies are required by the suite; missing
dependencies fail the suite. The separate `run-gate live-v27` lane is an explicit host integration run: it selects only
tests marked `live`, requires `TNT_LIVE=1`, and creates and cleans up resources under a `tnt-live-` prefix. It runs in
`tester-unified`; keep `tnt.toml` and `tnt.secrets.toml` in a private scratch directory outside the repository and pass
that directory through `RUN_GATE_EXTRA_MOUNTS`. The live probe also switches between two same-endpoint profile aliases;
see the [consumer setup example](docs/CONSUMERS.md#explicit-live-lane). The [live testing guide](docs/LIVE-TESTING.md)
covers the invocation, host restrictions, and cleanup behavior, and the [test strategy](docs/DESIGN.md#9-test-strategy)
explains why the live lane runs in `tester-unified`.

```bash
PYTHONPATH=src nice python -m pytest -q -p no:xdist
```
