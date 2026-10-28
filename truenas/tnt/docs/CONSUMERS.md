# Using tnt

This guide shows a pasteable profile and commands for a vanilla TrueNAS v27 host. For the feature scope and its safety
choices, see the [design guide](DESIGN.md#1-goals-and-non-goals). The quick overview is in the [README](../README.md).
The v27 request shapes and the source revision they were checked against are in [API method evidence](API-METHODS-V27.md).

## Configure a profile

Start from the committed example and protect the copy before entering credentials:

```sh
cp tnt.toml.example tnt.toml
chmod 600 tnt.toml
```

The selected config file must be owned by your user and have exact mode `0600`. Edit the copy to match this shape:

```toml
schema_version = 1
default = "lab"

[profiles.lab]
url = "wss://truenas.example.invalid/api/current"
username = "admin"
auth = "api_key"
api_key = "REPLACE_WITH_API_KEY"
tls = "verify"
ssh_user = "admin"
ssh_key_files = ["~/.ssh/id_ed25519"]
ssh_known_hosts = "~/.ssh/known_hosts"
```

The closed `auth` values are `"api_key"` and `"password"`; for password auth include `password = "..."` in the
protected profile file to use the pinned client's v27 `PASSWORD_PLAIN` login. An API key may instead come from an
`api_key_file`. The closed `tls` values are `"verify"` and `"insecure"`; the latter also requires `--insecure` on
every command. `ws://` URLs are refused.

## Run the core workflows

Every command accepts `--profile NAME`, `--config PATH`, and `--json` where it returns data. Add `--allow-write` to
apply a planned mutation. Destructive operations also need `--confirm` with the exact target shown in the plan.

```sh
tnt dataset list --profile lab --json
tnt dataset show tank/home --profile lab --json
tnt dataset create tank/home --profile lab --type filesystem --compression LZ4 --allow-write
tnt dataset create tank/archive --profile lab --type zvol --volsize 10737418240 --allow-write
tnt dataset update tank/home --profile lab --readonly ON --allow-write
tnt dataset delete tank/archive --profile lab --recursive --allow-write --confirm tank/archive
tnt snapshot list tank/home --profile lab --json
tnt snapshot create tank/home before-maintenance --profile lab --allow-write
tnt snapshot hold tank/home@before-maintenance --profile lab --allow-write
tnt snapshot release tank/home@before-maintenance --profile lab --allow-write
tnt snapshot rollback tank/home@before-maintenance --profile lab --allow-write --confirm tank/home@before-maintenance
tnt snapshot clone tank/home@before-maintenance tank/recovered --profile lab --allow-write
tnt snapshot delete tank/home@before-maintenance --profile lab --allow-write --confirm tank/home@before-maintenance
tnt container list --profile lab --json
tnt container show web --profile lab --json
tnt container create web --profile lab --image nginx --image-version latest --pool tank --no-autostart --allow-write
tnt container update web --profile lab --cpuset 0-1 --autostart --allow-write
tnt container start web --profile lab --allow-write
tnt container stop web --profile lab --allow-write --confirm web
tnt container restart web --profile lab --allow-write --confirm web
tnt container delete web --profile lab --allow-write --confirm web
tnt vm list --profile lab --json
tnt vm create guest --profile lab --memory 2048 --vcpus 2 --disk-zvol tank/guests/guest --disk-size 10737418240 --allow-write
tnt vm device add guest --profile lab --type iso --path /mnt/tank/install.iso --allow-write
tnt vm device add guest --profile lab --type nic --attach br0 --allow-write
tnt vm update guest --profile lab --memory 4096 --vcpus 4 --no-autostart --allow-write
tnt vm start guest --profile lab --allow-write
tnt vm stop guest --profile lab --allow-write --confirm guest
tnt vm poweroff guest --profile lab --allow-write --confirm guest
tnt vm suspend guest --profile lab --allow-write
tnt vm resume guest --profile lab --allow-write
tnt vm console guest --profile lab --json
tnt vm delete guest --profile lab --allow-write --confirm guest
tnt user list --profile lab --json
tnt user create deploy "Deployment account" --profile lab --group 1001 --password-disabled --allow-write
tnt user update deploy --profile lab --full-name "Deployment service" --groups 1001,1002 --allow-write
tnt user ssh-key set deploy --profile lab --file ~/.ssh/id_ed25519.pub --add --allow-write
tnt user ssh-key set deploy --profile lab --file ~/.ssh/id_ed25519.pub --allow-write
tnt user ssh-key remove deploy --profile lab --allow-write
tnt user password deploy --profile lab --password-file ~/.config/tnt/deploy-password --allow-write
tnt user delete deploy --profile lab --allow-write --confirm deploy
tnt tui --profile lab
tnt tui --profile lab --allow-write
```

### Shares

List both protocols independently. Create payloads use v27 field names; the host path must already exist. SMB's
`purpose` accepts `DEFAULT_SHARE`, `LEGACY_SHARE`, `TIMEMACHINE_SHARE`, `MULTIPROTOCOL_SHARE`, `TIME_LOCKED_SHARE`,
`PRIVATE_DATASETS_SHARE`, `VEEAM_REPOSITORY_SHARE`, or `FCP_SHARE`. Guest access is available only with
`LEGACY_SHARE`; the default share options accept `hostsallow` and `hostsdeny`.

```sh
tnt share smb list --profile lab --json
tnt share smb show team --profile lab --json
cat > smb-share.json <<'JSON'
{"name":"team","path":"/mnt/tank/team","purpose":"DEFAULT_SHARE","enabled":true,"readonly":false,
 "comment":"Team files","options":{"hostsallow":["10.0.0.0/8"],"hostsdeny":[]}}
JSON
tnt share smb create --profile lab --file smb-share.json --allow-write --dry-run
tnt share nfs list --profile lab --json
cat > nfs-share.json <<'JSON'
{"path":"/mnt/tank/export","networks":["10.0.0.0/8"],"hosts":[],"maproot_user":"root",
 "security":["SYS"],"ro":true}
JSON
tnt share nfs create --profile lab --file nfs-share.json --allow-write
tnt share smb delete team --profile lab --allow-write --confirm team
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
tnt task snapshot list --profile lab --json
cat > snapshot-task.json <<'JSON'
{"dataset":"tank/home","schedule":{"minute":"00","hour":"02","dom":"*","month":"*","dow":"*"},
 "lifetime_value":14,"lifetime_unit":"DAY","naming_schema":"daily-%Y-%m-%d_%H-%M","enabled":true}
JSON
tnt task snapshot create --profile lab --file snapshot-task.json --allow-write
tnt task snapshot run 1 --profile lab --allow-write
tnt task snapshot disable 1 --profile lab --allow-write
tnt task snapshot delete 1 --profile lab --allow-write --confirm 'tank/home [1]'
tnt task replication list --profile lab --json
tnt task replication show nightly --profile lab --json
tnt task replication run nightly --profile lab --allow-write --confirm nightly
tnt task replication disable nightly --profile lab --allow-write
tnt task cloudsync list --profile lab --json
tnt task cloudsync run offsite-copy --profile lab --allow-write --confirm offsite-copy
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
tnt network show --profile lab
tnt network show --profile lab --json > network-status.json
tnt tui --profile lab  # choose System > Network
```

This screen does not edit interface or resolver configuration. The JSON includes `interfaces`, `addresses`, `routes`,
`dns`, and `default_gateways`; a response that omits required summary fields is an error, not an empty status. See the
[network view design](DESIGN.md#network-status) and [v27 read signatures](API-METHODS-V27.md#network-status).

### Apps

List installed apps and their running state, installed version, and upgrade availability. App IDs are the values passed
to v27 app lifecycle methods:

```sh
tnt app list --profile lab --json
tnt app show demo --profile lab --json
tnt app start demo --profile lab --allow-write --dry-run
tnt app stop demo --profile lab --allow-write
tnt app restart demo --profile lab --allow-write
tnt app uninstall demo --profile lab --allow-write --confirm demo
tnt app uninstall demo --profile lab --allow-write --delete-data-volumes --confirm demo
```

Restart is an ordered `app.stop` then `app.start` pair. Uninstall is destructive and requires typing the exact app ID;
it preserves data volumes by default. The final example explicitly opts into deleting TrueNAS-managed app volumes.
See the [Apps design decisions](DESIGN.md#apps) and [v27 app schemas](API-METHODS-V27.md#apps).

### Audit

Audit results are read-only and paginated. `--since` is inclusive, `--until` is exclusive, and both accept
ISO-8601 timestamps with an explicit UTC offset. Use `--offset` to request the next page:

```sh
tnt audit --profile lab --service MIDDLEWARE --since 2026-10-27T00:00:00Z \
  --until 2026-10-29T00:00:00Z --user admin --event USER_LOGIN --limit 100 --offset 0 --json
tnt audit --profile lab --service MIDDLEWARE --limit 100 --offset 100 --json
tnt tui --profile lab  # choose System > Audit, set filters, then Refresh/Older/Newer
```

`--service` is one of `MIDDLEWARE`, `S3`, `SMB`, `SUDO`, or `SYSTEM`; one service is queried per request. The viewer
scrubs known secret keys and inline secret patterns from event data before output. v27 does not expose a generic
middleware or syslog tail method. See the [Audit design](DESIGN.md#audit) and [v27 audit schema](API-METHODS-V27.md#audit).

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

For VM creation, v27 requires two stages: tnt first reviews `vm.create`, then resolves the new VM ID and displays a
separate exact `vm.device.create` plan for each disk, ISO, or NIC before applying that call. A later device failure
may leave the VM record and any earlier device in place; inspect it before retrying. In the TUI, enter the `disk_zvol`
and `disk_size` values in the VM create action data; `iso` and `nic_attach` are optional. Each generated device call
gets its own plan review.

Each non-read plan is checked recursively for boot-pool names and paths, wherever they occur in its payload. Pool
references in mapping fields such as `pool`, `poolId`, `pool_ids`, and `pools` must resolve to a listed pool; unknown,
null, numeric-string, list, and nested mapping references are refused. VM/container stop, dataset and snapshot removal,
snapshot rollback, user deletion, and a password reset of the current login account are destructive operations and
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
default exclusion remain in place. The live test never skips. If selected, it fails unless `TNT_LIVE=1`, the
`TNT_LIVE_PROFILE` exists in the selected protected config file, and all pool/group/image/VM sizing variables below
are present. Run it only against an operator-designated v27 test host with a disposable data pool and image available.
The test creates a dataset, snapshot/clone, user, container, and VM with names prefixed `tnt-live-`, then removes each
through the same plan/apply guard. It was written for M1 but is intentionally not run as part of this work.

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

Set `TNT_LIVE_INSECURE=1` only when the selected profile has `tls = "insecure"`; TLS verification stays enabled by
default. The live test is not a replacement for the default FakeServer suite and it must not be pointed at a host with
production data or workloads.

