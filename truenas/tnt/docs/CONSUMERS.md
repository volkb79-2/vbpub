# Using tnt

This guide shows pasteable multi-host config and commands for a vanilla TrueNAS v27 host. For the feature scope and its safety
choices, see the [design guide](DESIGN.md#1-goals-and-non-goals). The quick overview is in the [README](../README.md).
The v27 request shapes and the source revision they were checked against are in [API method evidence](API-METHODS-V27.md).

## Configure multiple hosts

Copy both committed examples and protect each private file before entering credentials:

```sh
cp tnt.toml.example tnt.toml
cp tnt.secrets.toml.example tnt.secrets.toml
chmod 600 tnt.toml
chmod 600 tnt.secrets.toml
```

`tnt.toml` contains connection facts only. `tnt.secrets.toml` contains credentials with the same host names. Both must
be regular files owned by your user with exact mode `0600`; tnt refuses symlinks and checks the descriptors it reads.
Credentials in `tnt.toml` are rejected. Each secret entry records the exact `host_url` where that credential was
entered; tnt refuses to authenticate when it differs from the selected config host. This check matters when an
existing cwd `tnt.toml` falls back to an XDG secrets file with the same alias for another appliance. An older secret
entry without `host_url` is also refused until you replace the credential with `tnt hosts edit NAME --set-secret`.
The paired files use this schema:

```toml
schema_version = 1
default_host = "lab"

[hosts.lab]
url = "wss://truenas.example.invalid/api/current"
username = "admin"
auth = "api_key"
tls = "verify"
ssh_user = "admin"
ssh_key_files = ["/home/me/.ssh/id_ed25519"]
ssh_known_hosts = "/home/me/.ssh/known_hosts"
description = "Primary TrueNAS host"
tags = ["primary", "storage"]

[hosts.backup]
url = "wss://backup.example.invalid/api/current"
username = "operator"
auth = "password"
tls = "verify"
description = "Backup host"
```

Save credentials in the separate file:

```toml
schema_version = 1

[hosts.lab]
host_url = "wss://truenas.example.invalid/api/current"
api_key = "REPLACE_WITH_API_KEY"
# Or set api_key_file to an absolute path to a separate mode-0600 key file.

[hosts.backup]
host_url = "wss://backup.example.invalid/api/current"
password = "REPLACE_WITH_PASSWORD"
```

`auth` values are `api_key` and `password`; `tls` values are `verify` and `insecure`. Password auth uses the pinned
client's v27 `PASSWORD_PLAIN` login. API keys use its SCRAM flow. `api_key_file`, when used, is stored only in
`tnt.secrets.toml`; the referenced file must also be owned by you with exact mode `0600`. `ws://` is refused and TLS
verification is on by default. `tls = "insecure"` also requires `--insecure` per connection and explicit warning
acknowledgement when saved.

Config and secrets resolve independently: `--config PATH` or `--secrets PATH`, the matching `$TNT_CONFIG` or
`$TNT_SECRETS`, an existing file in the current directory, then `$XDG_CONFIG_HOME/tnt/` (or `~/.config/tnt/`). They
may live in different directories and pair by matching host name plus the exact endpoint binding in each secret entry.
Supply an explicit path or matching environment variable for whichever file is outside its default search locations.
If you change a host URL, rotate its credential in the same edit so the saved binding is updated:

```sh
tnt hosts edit lab --url wss://new-nas.example.invalid/api/current --set-secret
```

Set `default_host` to connect automatically. If it is unset
with multiple hosts, `tnt tui` opens the picker and CLI commands list available host names and ask for `--host NAME`.
A single host is selected automatically.

Manage the paired files through the CLI. Passwords and API keys are prompted with input hidden:

```sh
tnt hosts list
tnt hosts show lab
tnt hosts add staging --url wss://staging.example.invalid/api/current --username operator --auth api_key --make-default
tnt hosts edit staging --description "Staging system"
tnt hosts test staging --insecure
tnt hosts set-default lab
tnt hosts remove staging --confirm staging
```

The TUI picker shows host name, URL, description, and last status. Press `h` or use the command palette to switch
hosts; `w` reopens the selected host in the other write mode. Reads, action construction, plan probes, and applies run
in workers with a visible busy message. A switch invalidates its plans and cancels host work before closing the old
session; after five seconds without a drain, choose Keep waiting or Cancel switch. SSH user, key-file, and known-host
settings are private local paths and are not shown.

## Interactive SSH hand-off

Configure an SSH user, one or more private identity files, and a known-hosts file in the selected `[hosts.NAME]` table.
Use absolute local paths. Identity files must be regular files owned by your user with no group/other permissions
(normally mode `0600`, or `0400`). The known-hosts file must be non-empty, regular, owned by your user, readable by you,
and not writable by group or others. Its path must use ordinary absolute path characters without spaces or SSH option
syntax characters.

Add the target's host key to that file from an independently trusted source before connecting. Do not use an
unverified key or rely on automatic key discovery. tnt never runs `ssh-keyscan` and never disables host-key checking.
For example, after verifying the fingerprint through your approved channel, configure:

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
```

Then start an interactive session from the CLI or TUI:

```sh
tnt ssh lab
tnt ssh --host lab
tnt tui --host lab  # press s, or choose “Open interactive SSH session” in the command palette
```

The CLI reads only `tnt.toml`; it does not need the paired API credential file. OpenSSH runs with `/dev/null` as its
configuration file, clears the built-in identity defaults, uses only the configured identity files and known-hosts
file, and disables global known-hosts, DNS host-key checks, agent use, and password authentication. The target hostname
comes from the host URL. The TUI is suspended around SSH and returns to its previous screen when the child process
exits; CLI exit status follows the SSH process. Only an interactive session is supported: `tnt ssh lab -- whoami` is
rejected and no remote-command mode is available. Paths are passed as argv values and are never displayed by tnt.

See the [SSH hand-off design](DESIGN.md#82-interactive-ssh-hand-off) for the pinned OpenSSH options and refusal rules.

## Check a host's v27 methods

After configuring the hosts, make one authenticated connection to compare the selected host's WebSocket method list with
tnt's exact v27 adapter manifest:

```sh
tnt doctor --online --host lab
```

The doctor result includes missing manifest methods grouped by feature. A listed gap means workflows that need one of
those methods are unavailable on that host; check the role requirements separately because method presence does not
grant the authenticated user permission to call it. See the [capability-check design](DESIGN.md#23-version-adapters-and-the-method-manifest)
and the [verified v27 contract](API-METHODS-V27.md#v27-adapter-contract).

## Read-only overview and jobs

Use these commands to collect the current v27 overview as JSON. Jobs pages default to 200 rows; `--limit` accepts
1–10000 and `--offset` is zero-based. A full page is marked possibly-more because the API pages are volatile and do not
form an atomic snapshot. In JSON, `rows` and `possibly_more` cover the remote page only. The first page also includes up
to 200 recent process-local job records in `local_jobs`; `local_jobs_possibly_more` marks a truncated local ledger. Later
pages return an empty `local_jobs` list. A remote row with a matching local record includes it in `local`.

```sh
tnt dashboard --host lab --json
tnt alerts list --host lab --json
tnt jobs list --host lab --limit 200 --offset 0 --json
tnt jobs list --host lab --limit 200 --offset 200 --json
tnt services --host lab --json
tnt storage health --host lab --json
tnt tui --host lab
```

Dashboard CPU and memory are one bounded hour of historical graph data, not live percentages. A missing reporting role
or graph is represented as unavailable; missing pool/disk values stay `null` or Unknown. Jobs shows host-wide scope
only after `auth.me` confirms `FULL_ADMIN`; otherwise TrueNAS limits the list to jobs visible to the authenticated
session. The TUI Jobs screen has Previous/Next page controls. Use `/` to focus search, `r` to refresh, `c` to copy
visible rows, and `?` for view help. Dashboard polls every 30 seconds, Jobs every 5 seconds, and Alerts, Services, and
Storage every 60 seconds. Refreshes do not overlap; on failure the TUI retains the last successful sample and shows a
credential-scrubbed error. Services has no start/stop controls. The full v27 methods, role requirements, schemas, and
unknown states are in [API method evidence](API-METHODS-V27.md#iteration-2-read-view-contract-implemented-against-the-verified-v27-reference);
the job and view design is in [DESIGN.md](DESIGN.md#non-blocking-jobs-and-core-read-views).

## Run the core workflows

Every command accepts `--host NAME`, `--config PATH`, and `--json` where it returns data. Add `--allow-write` to
apply a planned mutation. Destructive operations also need `--confirm` with the exact target shown in the plan.

```sh
tnt dataset list --host lab --json
tnt dataset show tank/home --host lab --json
tnt dataset create tank/home --host lab --type filesystem --compression LZ4 --allow-write
tnt dataset create tank/archive --host lab --type zvol --volsize 10737418240 --allow-write
tnt dataset update tank/home --host lab --readonly ON --allow-write
tnt dataset delete tank/archive --host lab --recursive --allow-write --confirm tank/archive
tnt snapshot list tank/home --host lab --json
tnt snapshot create tank/home before-maintenance --host lab --allow-write
tnt snapshot hold tank/home@before-maintenance --host lab --allow-write
tnt snapshot release tank/home@before-maintenance --host lab --allow-write
tnt snapshot rollback tank/home@before-maintenance --host lab --allow-write --confirm tank/home@before-maintenance
tnt snapshot clone tank/home@before-maintenance tank/recovered --host lab --allow-write
tnt snapshot delete tank/home@before-maintenance --host lab --allow-write --confirm tank/home@before-maintenance
tnt container list --host lab --json
tnt container show web --host lab --json
tnt container create web --host lab --image nginx --image-version latest --pool tank --no-autostart --allow-write
tnt container update web --host lab --cpuset 0-1 --autostart --allow-write
tnt container start web --host lab --allow-write
tnt container stop web --host lab --allow-write --confirm web
tnt container restart web --host lab --allow-write --confirm web
tnt container delete web --host lab --allow-write --confirm web
tnt vm list --host lab --json
tnt vm create guest --host lab --memory 2048 --vcpus 2 --disk-zvol tank/guests/guest --disk-size 10737418240 --allow-write
tnt vm device add guest --host lab --type iso --path /mnt/tank/install.iso --allow-write
tnt vm device add guest --host lab --type nic --attach br0 --allow-write
tnt vm update guest --host lab --memory 4096 --vcpus 4 --no-autostart --allow-write
tnt vm start guest --host lab --allow-write
tnt vm stop guest --host lab --allow-write --confirm guest
tnt vm poweroff guest --host lab --allow-write --confirm guest
tnt vm suspend guest --host lab --allow-write
tnt vm resume guest --host lab --allow-write
tnt vm console guest --host lab --json
tnt vm delete guest --host lab --allow-write --confirm guest
tnt user list --host lab --json
tnt user create deploy "Deployment account" --host lab --group 1001 --password-disabled --allow-write
tnt user update deploy --host lab --full-name "Deployment service" --allow-write
tnt user update operator --host lab --shell /bin/sh --allow-write --confirm-self --confirm operator
tnt user access deploy --host lab --groups 1001,1002 --allow-write --confirm deploy
tnt user ssh-key set deploy --host lab --file ~/.ssh/id_ed25519.pub --add --allow-write
tnt user ssh-key set deploy --host lab --file ~/.ssh/id_ed25519.pub --allow-write
tnt user ssh-key remove deploy --host lab --allow-write
tnt user password deploy --host lab --password-file ~/.config/tnt/deploy-password --allow-write
tnt user delete deploy --host lab --allow-write --confirm deploy
tnt tui --host lab
tnt tui --host lab --allow-write
```

### Shares

List both protocols independently. Create payloads use v27 field names; the host path must already exist. SMB's
`purpose` accepts `DEFAULT_SHARE`, `LEGACY_SHARE`, `TIMEMACHINE_SHARE`, `MULTIPROTOCOL_SHARE`, `TIME_LOCKED_SHARE`,
`PRIVATE_DATASETS_SHARE`, `VEEAM_REPOSITORY_SHARE`, or `FCP_SHARE`. Guest access is available only with
`LEGACY_SHARE`; the default share options accept `hostsallow` and `hostsdeny`.

```sh
tnt share smb list --host lab --json
tnt share smb show team --host lab --json
cat > smb-share.json <<'JSON'
{"name":"team","path":"/mnt/tank/team","purpose":"DEFAULT_SHARE","enabled":true,"readonly":false,
 "comment":"Team files","options":{"hostsallow":["10.0.0.0/8"],"hostsdeny":[]}}
JSON
tnt share smb create --host lab --file smb-share.json --allow-write --dry-run
tnt share nfs list --host lab --json
cat > nfs-share.json <<'JSON'
{"path":"/mnt/tank/export","networks":["10.0.0.0/8"],"hosts":[],"maproot_user":"root",
 "security":["SYS"],"ro":true}
JSON
tnt share nfs create --host lab --file nfs-share.json --allow-write
tnt share smb delete team --host lab --allow-write --confirm team
```

Share deletion removes only the share record and leaves files intact. Creation uses remote `filesystem.stat`, refuses
paths that do not exist, and rejects `/mnt/boot-pool` paths. The share form follows the same plan/apply and drift-check
flow as other writes. See [share safety decisions](DESIGN.md#shares) and the [v27 method signatures](API-METHODS-V27.md#workflows).

### Data Protection tasks

Periodic snapshot tasks require a dataset, cron schedule, retention, and a v27 naming schema. Snapshot runs add
snapshots; deletion removes only the scheduled task. Replication run plans identify source and target before apply.
Cloud-sync credential records and encryption values are masked in both clients. The task name is the replication
`name` or cloud-sync `description`; snapshot task actions use its numeric ID.

```sh
tnt task snapshot list --host lab --json
cat > snapshot-task.json <<'JSON'
{"dataset":"tank/home","schedule":{"minute":"00","hour":"02","dom":"*","month":"*","dow":"*"},
 "lifetime_value":14,"lifetime_unit":"DAY","naming_schema":"daily-%Y-%m-%d_%H-%M","enabled":true}
JSON
tnt task snapshot create --host lab --file snapshot-task.json --allow-write
tnt task snapshot run 1 --host lab --allow-write
tnt task snapshot disable 1 --host lab --allow-write
tnt task snapshot delete 1 --host lab --allow-write --confirm 'tank/home [1]'
tnt task replication list --host lab --json
tnt task replication show nightly --host lab --json
tnt task replication run nightly --host lab --allow-write --confirm nightly
tnt task replication disable nightly --host lab --allow-write
tnt task cloudsync list --host lab --json
tnt task cloudsync run offsite-copy --host lab --allow-write --confirm offsite-copy
```

For snapshot task retention, `lifetime_unit` is one of `HOUR`, `DAY`, `WEEK`, `MONTH`, or `YEAR`. Replication task
run-now is typed-confirmed because a configured `allow_from_scratch` task may remove destination snapshots. Cloud sync
`transfer_mode` accepts `SYNC`, `COPY`, or `MOVE`; `MOVE` removes the local source after transfer and is called out in
the confirmation plan. Replication `direction` is `PUSH` or `PULL`, and `transport` is `SSH`, `SSH+NETCAT`, or `LOCAL`.
See the [task safety decisions](DESIGN.md#data-protection-tasks) and [v27 API schemas](API-METHODS-V27.md#workflows).

### Network status

Inspect active addresses, interface configuration, installed routes, DNS nameservers, and default gateways with the
read-only network view:

```sh
tnt network show --host lab
tnt network show --host lab --json > network-status.json
tnt tui --host lab  # choose System > Network
```

This screen does not edit interface or resolver configuration. The JSON includes `interfaces`, `addresses`, `routes`,
`dns`, and `default_gateways`; a response that omits required summary fields is an error, not an empty status. See the
[network view design](DESIGN.md#network-status) and [v27 read signatures](API-METHODS-V27.md#network-status).

### Apps

List installed apps and their running state, installed version, and upgrade availability. App IDs are the values passed
to v27 app lifecycle methods:

```sh
tnt app list --host lab --json
tnt app show demo --host lab --json
tnt app start demo --host lab --allow-write --dry-run
tnt app stop demo --host lab --allow-write --confirm demo
tnt app restart demo --host lab --allow-write --confirm demo
tnt app uninstall demo --host lab --allow-write --confirm demo
tnt app uninstall demo --host lab --allow-write --delete-data-volumes --confirm demo
```

Stop and restart are destructive and require typing the exact app ID. Restart is an ordered `app.stop` then `app.start`
pair. Uninstall also requires typing the exact app ID; it preserves data volumes by default. The final example
explicitly opts into deleting TrueNAS-managed app volumes.
See the [Apps design decisions](DESIGN.md#apps) and [v27 app schemas](API-METHODS-V27.md#apps).

### Audit

Audit results are read-only and paginated. `--since` is inclusive, `--until` is exclusive, and both accept
ISO-8601 timestamps with an explicit UTC offset. Use `--offset` to request the next page:

```sh
tnt audit --host lab --service MIDDLEWARE --since 2026-10-27T00:00:00Z \
  --until 2026-10-29T00:00:00Z --user admin --event USER_LOGIN --limit 100 --offset 0 --json
tnt audit --host lab --service MIDDLEWARE --limit 100 --offset 100 --json
tnt tui --host lab  # choose System > Audit, set filters, then Refresh/Older/Newer
```

`--service` is one of `MIDDLEWARE`, `S3`, `SMB`, `SUDO`, or `SYSTEM`; one service is queried per request. The viewer
scrubs known secret keys and inline secret patterns from event data before output. v27 does not expose a generic
middleware or syslog tail method. See the [Audit design](DESIGN.md#audit) and [v27 audit schema](API-METHODS-V27.md#audit).

### API Keys

List and rename keys without exposing the stored SCRAM verifier fields. The CLI can display the one-time key only in
non-JSON terminal output. TUI creation requires an absolute output path and writes directly to a new mode-0600 file;
the raw value never appears on screen. Pass `--write-key` for CLI file output. The TUI updates the paired secrets path
for the selected host when it uses API-key auth. A password-authenticated host must be changed to `api_key` auth in
the host editor before tnt can use the new key:

```sh
tnt apikey list --host lab --json
tnt apikey create deploy-key deploy --host lab --expires-at 2027-01-01T00:00:00Z \
  --allow-write --write-key /home/me/.config/tnt/deploy.key
tnt apikey rename 4 automation --host lab --allow-write
tnt apikey revoke 4 --host lab --allow-write --confirm deploy-key
tnt hosts edit lab --api-key-file /home/me/.config/tnt/deploy.key
```

Do not pass the raw key as a command argument or paste it into shell history. v27 exposes no separate revoke method;
`apikey revoke` uses `api_key.delete`, which removes and invalidates the key record, and requires typing its current name.
The web UI places “My API Keys” in the account menu; tnt puts it under Credentials to make it available in the navigation
tree. See the [API key design](DESIGN.md#api-keys) and [v27 signatures](API-METHODS-V27.md#api-keys).

### System actions

Check updates first. The output shows the current train and version, the exact available target train and version, and
the first release-notes paragraph. Apply the exact version shown; the CLI follows the update job, and reboot remains a
separate action:

```sh
tnt system update check --host lab --json
tnt system update apply TrueNAS-27.0.0-RC.2 --host lab --allow-write \
  --confirm TrueNAS-27.0.0-RC.2
```

Replace the example target with the version returned by the check. Before apply, reboot, or shutdown, tnt requires
`FULL_ADMIN` so it can inspect all active jobs. The plan shows those jobs, running VM/container names, and active SMB
session count. Running jobs block the action unless `--force-with-jobs` is supplied; the host name or target version
must still be typed exactly. Reboot and shutdown accept an optional 0–86400 second delay:

```sh
tnt system reboot --host lab --delay 60 --allow-write --confirm HOST_NAME_FROM_PLAN
tnt system shutdown --host lab --allow-write --confirm HOST_NAME_FROM_PLAN
tnt system reboot --host lab --force-with-jobs --allow-write --confirm HOST_NAME_FROM_PLAN
tnt tui --host lab  # System > Update, Reboot, or Shutdown; reboot reconnects with bounded retries
```

The TUI checks `system.state` after reboot and stops retrying after bounded attempts; an authentication failure stops
retries immediately. If the connection closes before the reboot request returns, tnt checks the host without resending
the action. It reports the outcome as uncertain unless a boot-state transition is observed; inspect host state before
retrying. A lost shutdown response is also uncertain and is never resubmitted. Do not start a system action outside an
approved maintenance window.

Configuration export writes a new local `.tar` file with mode `0600`. It includes the TrueNAS secret seed. The
destination directory must already exist and an existing file is never overwritten. The optional root authorized-keys
file is sensitive too:

```sh
tnt system config-export /home/me/truenas-config.tar --host lab --allow-write
tnt system config-export /home/me/truenas-config-with-ssh.tar --host lab \
  --include-root-authorized-keys --allow-write
tnt tui --host lab  # System > General Settings
```

Store the export as a credential. See [system action decisions](DESIGN.md#system-actions) and the
[verified v27 methods](API-METHODS-V27.md#system-actions).

Use `--file spec.json` for fields beyond a workflow's minimal create options. Passwords are entered through a no-echo
prompt or `--password-file`, never a command-line argument. User SSH keys are loaded from a public-key file or
validated public-key text; private keys are not accepted or printed.

The dataset CLI kind is `filesystem` or `zvol`, which map to v27 API `type` values `FILESYSTEM` and `VOLUME` when
you use a JSON `--file`; readonly values are `ON`, `OFF`, or `INHERIT`. The compact VM
device option accepts `disk`, `iso`, or `nic`; a schema-complete device JSON file uses v27 device types `DISK`,
`CDROM`, `NIC`, `DISPLAY`, `PCI`, `RAW`, or `USB`. Destructive actions show their exact call plan and ask for the
full target via `--confirm`. Password changes to another account are planned writes; changing the current login
account also needs `--confirm-self` and typed confirmation. Built-in users cannot be deleted. Add `--builtin` only
when intentionally editing a built-in user; it never enables deletion.

User `update` accepts only basic `full_name`, `shell`, and `home` fields. Changing the current login user's `shell` or
`home` requires both `--confirm-self` and typed `--confirm USER`; `auth.me` is bound into the plan and checked again
before apply. Use `user access` for primary or supplementary
groups and for `--password-disabled` / `--password-enabled`, `--locked` / `--unlocked`, or `--ssh-password-enabled` /
`--ssh-password-disabled`. It always asks you to type the target username. Changing the current login account also
requires `--confirm-self`; tnt binds `auth.me` into the plan and checks it again before apply. Built-in, immutable,
root, and admin accounts are refused. `random_password` and username rename are refused by both user update paths.
Current-user password and SSH-key changes also require `--confirm-self` and typed username confirmation.

For VM creation, v27 requires two stages: tnt first reviews `vm.create`, then resolves the new VM ID and displays a
separate exact `vm.device.create` plan for each disk, ISO, or NIC before applying that call. A later device failure
may leave the VM record and any earlier device in place; inspect it before retrying. In the TUI, enter the `disk_zvol`
and `disk_size` values in the VM create action data; `iso` and `nic_attach` are optional. Each generated device call
gets its own plan review.

Each non-read plan is checked recursively for boot-pool names and paths, wherever they occur in its payload. Pool
references in mapping fields such as `pool`, `poolId`, `pool_ids`, and `pools` must resolve to a listed pool; unknown,
null, numeric-string, list, and nested mapping references are refused. VM/container stop, dataset and snapshot removal,
snapshot rollback, app stop/restart, user deletion, current-login shell/home changes, and a password reset of the current login account are destructive operations and
show their typed confirmation target before apply. Other user password changes are planned writes. v27 has no direct
container restart RPC; tnt's restart plan calls stop then start and needs the same typed target confirmation as stop.
Container list adds current
CPU/memory usage from v27's `container.metrics` event source, with a bounded 2.5 second wait and an explicit status
when no sample arrives; update changes autostart and CPU affinity. The v27 CRUD record exposes CPU affinity as
`cpuset`, but not the current image, CPU-count setting, or configured memory limit; JSON and TUI output identify these
unavailable fields. VM console info includes configured display bind/port endpoints and the v27 web display URI when
available, without embedding a VNC client. Use the TUI command palette (`ctrl+p`) to open each implemented workflow
and plan actions.

## Explicit live lane

The default pytest config deselects tests marked `live`; `tests/test_live_lane_config.py` asserts that registration and
default exclusion remain in place. The live test never skips. If selected, it fails unless `TNT_LIVE=1`, the host named
by `TNT_LIVE_PROFILE` exists in the selected protected config file with a matching credential in the paired secrets
file, and all pool/group/image/VM sizing variables below are present. Run it only against an operator-designated v27
test host with a disposable data pool and image available. Config and secrets resolve independently; set `TNT_CONFIG`
and/or `TNT_SECRETS` when the corresponding file is outside its explicit, environment, current-directory, and XDG search paths.
The test creates a dataset, snapshot/clone, user, container, and VM with names prefixed `tnt-live-`, then removes each
through the same plan/apply guard. It is reserved for It4 acceptance and is intentionally not run during M1, M2, or It1–It3.

```sh
export TNT_CONFIG="$PWD/tnt.toml"
export TNT_LIVE=1
export TNT_LIVE_PROFILE=lab
export TNT_LIVE_POOL=tank
export TNT_LIVE_GROUP_ID=1001
export TNT_LIVE_CONTAINER_IMAGE_NAME=nginx
export TNT_LIVE_CONTAINER_IMAGE_VERSION=latest
export TNT_LIVE_VM_MEMORY_MIB=2048
export TNT_LIVE_VM_DISK_BYTES=10737418240
PYTHONPATH=src nice python -m pytest -q -p no:xdist -m live tests/live
```

Set `TNT_LIVE_INSECURE=1` only when the selected host has `tls = "insecure"`; TLS verification stays enabled by
default. The live test is not a replacement for the default FakeServer suite and it must not be pointed at a host with
production data or workloads.

