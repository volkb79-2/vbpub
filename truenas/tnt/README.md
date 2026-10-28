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
workflow examples are in the [consumer guide](docs/CONSUMERS.md#configure-a-profile).

## Install

```bash
pip install 'tnt[tui]'        # the CLI alone: pip install tnt
```

`truenas_api_client` (LGPL-3.0, official) is installed from a pinned git commit; the pin rationale is in `pyproject.toml`.

## Configure a host

Copy [`tnt.toml.example`](tnt.toml.example) to `tnt.toml`, fill in one or more profiles, then set mode `0600`:

```bash
cp tnt.toml.example tnt.toml
chmod 600 tnt.toml
```

The selected file must be a regular file owned by the current user with exactly mode `0600`; tnt refuses to start if
that check fails. Config lookup order is `--config PATH`, `$TNT_CONFIG`, `./tnt.toml`, then
`$XDG_CONFIG_HOME/tnt/tnt.toml` (or `~/.config/tnt/tnt.toml` when `XDG_CONFIG_HOME` is unset). `ws://` is refused.
TLS verification is on by default. A profile with `tls = "insecure"` also requires `--insecure` on each command.

Credentials may be inline by operator choice; see the [secret-handling rationale](docs/DESIGN.md#7-secrets). They never
appear in command arguments, logs, exceptions, `profile show`, read JSON, or ordinary TUI screens. The one-time API key
creation result is shown only in a dedicated warning view/output and is never added to the local config automatically.
`profile show` masks configured secrets as `***`.

```toml
schema_version = 1
default = "lab"

[profiles.lab]
url = "wss://nas.example.invalid/api/current"
username = "admin"
auth = "api_key"
api_key = "REPLACE_WITH_API_KEY" # Or use api_key_file = "/home/me/.config/tnt/lab.key".
tls = "verify"
ssh_user = "admin"
ssh_key_files = ["~/.ssh/id_ed25519"]
ssh_known_hosts = "~/.ssh/known_hosts"

[profiles.password-auth]
url = "wss://nas2.example.invalid/api/current"
username = "operator"
auth = "password"
password = "REPLACE_WITH_PASSWORD"
tls = "verify"
```

`schema_version` is required and currently `1`. The example contains placeholders only. API key authentication uses the pinned client's SCRAM flow. Password
authentication uses its supported `PASSWORD_PLAIN` login flow. Authentication failures are fatal and never retried.
SSH private-key and known-host paths remain profile facts for optional SSH workflows. M1 user-key edits read an explicit
OpenSSH `.pub` file or validated public-key text; private key material is never accepted or printed.

`tnt apikey list` masks TrueNAS key verifier fields. `create` shows the raw value once in a bold-warning result; use
`--write-key` to create a new mode-0600 file without overwriting an existing file. tnt never writes the returned value
to `tnt.secrets.toml`; add only the private file's `api_key_file` path to a profile when you choose to use the key.
v27 has no separate revoke RPC, so `apikey revoke` uses destructive `api_key.delete` and requires the current name as
typed confirmation. See the [API key design](docs/DESIGN.md#api-keys), [consumer workflow](docs/CONSUMERS.md#api-keys),
and [v27 method evidence](docs/API-METHODS-V27.md#api-keys).

## Use

```bash
tnt --version
tnt profile list
tnt profile show lab
tnt doctor --online --profile lab       # one login, no retry
tnt dataset list --profile lab --json
tnt dataset create tank/home --profile lab --type filesystem --compression LZ4 --allow-write
tnt snapshot create tank/home nightly --profile lab --allow-write
tnt snapshot rollback tank/home@nightly --profile lab --allow-write --confirm tank/home@nightly
tnt container list --profile lab --json
tnt container create web --profile lab --image nginx --image-version latest --pool tank --no-autostart --allow-write
tnt container restart web --profile lab --allow-write --confirm web
tnt vm list --profile lab --json
tnt vm create guest --profile lab --memory 2048 --vcpus 2 --disk-zvol tank/vms/guest --disk-size 10737418240 --allow-write
tnt vm console guest --profile lab --json
tnt user list --profile lab --json
tnt user create deploy "Deploy user" --profile lab --group 1001 --password-disabled --allow-write
tnt user ssh-key set deploy --profile lab --file ~/.ssh/id_ed25519.pub --add --allow-write
tnt user password deploy --profile lab --password-file /home/me/.config/tnt/new-password --allow-write
tnt user ssh-key remove deploy --profile lab --allow-write
tnt user delete deploy --profile lab --allow-write --confirm deploy
tnt app list --profile lab --json
tnt app restart demo --profile lab --allow-write
tnt app uninstall demo --profile lab --allow-write --confirm demo
tnt network show --profile lab --json
tnt audit --profile lab --user admin --event USER_LOGIN --limit 100 --json
tnt apikey list --profile lab --json
tnt apikey create deploy-key deploy --profile lab --expires-at 2027-01-01T00:00:00Z --allow-write --write-key /home/me/.config/tnt/deploy.key
tnt apikey rename 4 automation --profile lab --allow-write
tnt apikey revoke 4 --profile lab --allow-write --confirm automation
tnt tui --profile lab
tnt tui --profile lab --allow-write
```

Each write command requires `--allow-write`; destructive commands also require `--confirm TARGET`. Use `--dry-run` to
render the plan without applying it. Changing the current login user's password additionally requires
`--confirm-self`; it remains destructive and needs `--confirm USER`. Other users' password changes are planned writes.
Password entry uses a no-echo prompt or `--password-file`; tnt does not accept a password argument. API payloads not
covered by a minimal create wizard can be supplied as a JSON `--file`.

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
