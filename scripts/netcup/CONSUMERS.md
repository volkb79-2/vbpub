# Netcup SCP consumer guide

## Prepare the CLI

From `scripts/netcup`, install the shared CLI package and complete the Netcup
device-code login once:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --editable ../../libraries/cli-extended
./scp-api.py login
```

The login flow stores the SCP refresh token in the local mode-0600 `.env`.
Review the shared protected-server policy before any automated operation on a
server. The API commands use this policy, and any automated SCP UI/PWMCP flow
must call the same guard before operating on a server. Direct manual use of the
SCP website can bypass local settings. Existing `.env` files must use
`NETCUP_PROTECTED_SERVERS` and `NETCUP_PROTECTED_SERVER_IDS`; non-empty legacy
`NETCUP_SCP_API_PROTECTED_*` settings are rejected instead of ignored.
Read-only inspection works without choosing a target:

```bash
./scp-api.py servers
./scp-api.py server-details 804027
```

### Refresh the SCP API reference

Netcup's human documentation is the
[canonical SCP REST API reference](https://www.netcup.com/en/helpcenter/documentation/servercontrolpanel/api).
The live OpenAPI JSON is available at
[`https://www.servercontrolpanel.de/scp-core/api/v1/openapi`](https://www.servercontrolpanel.de/scp-core/api/v1/openapi).
Refresh the tracked local snapshot from `scripts/netcup` with:

```bash
./update-scp-openapi.py
```

The updater validates the API title, OpenAPI major version, API version, and
non-empty paths before atomically replacing `netcup-scp-openapi.json`.

### Inspect rescue status or the installer screen

Netcup's provider rescue system is a separate Grml Linux boot environment for
repair, backup, and network diagnosis. The API resource supports status,
activation, and deactivation. Its status response includes an `active` flag
and optional temporary rescue password; the current CLI redacts that password
in normal output. The current CLI reads status and supports deactivation:

```bash
./scp-api.py rescuesystem 804027 --json
./scp-api.py rescuesystem 804027 deactivate
```

The SCP Screen is browser VNC, separate from the rescue API resource. For a
manual PWMCP check during an ISO run, store the SCP URL, username, and password
in the local mode-0600 `.env` using the `NETCUP_SCP_UI_*` names from
`.env.example`, then open the server's Screen and sign in. The popup URL has
the form `https://www.servercontrolpanel.de/scp-ui/servers/SERVER_ID/screen?popup=1`;
the SCP page also has an “Open (new window)” button. Click the console canvas
before sending any keyboard input. Do not send credentials in chat. A
configured second factor must be completed interactively. These environment
values remain placeholders for the repository: no current Netcup command
reads them or launches PWMCP.

A live check on 804027 showed the installed TrueNAS `Console setup` menu and
the guest's HTTP and HTTPS management URLs. That is the normal ready-to-use
TrueNAS console. Clicking the canvas and sending `0` without Enter, then
Backspace, verified that input reaches the console without selecting a menu
option. The console pixels are a canvas and are not available as page text, so
automatic screen recognition still needs OCR or a screen-specific image
recognizer. The generic installer continues to detect handoff from provider
power state; it does not yet use PWMCP to classify reboot or finish screens.
Any future managed browser flow that opens or sends input to a server's Screen
must resolve that server and pass its ID/name to the shared protected-server
guard.

## Create and restore a Netcup snapshot

Snapshots are server-specific. **A provider reinstall (image task) removes the
server's snapshots**: after reinstalling v1001 the API listing was empty although the
server record still reported a snapshot count of 1. A snapshot is therefore not a
rollback path across a reinstall. Before reinstalling a host whose current state must
stay recoverable, keep the state in an installable form instead (for TrueNAS: the
golden restore ISO built by `install-restore-iso.py`) and save the snapshot listing
(`./scp-api.py snapshots <id>`) next to the install log.

Check feasibility first, create a named rollback point, and list it before restoring:

```bash
./scp-api.py snapshots 804027 dryrun
./scp-api.py snapshots 804027 create \
  --name before-live-test \
  --description 'TrueNAS live-test rollback point' \
  --disk-name vda \
  --yes
./scp-api.py snapshots 804027
```

Preview a restore, then submit it only after confirming the selected snapshot
and server:

```bash
./scp-api.py snapshots 804027 revert before-live-test --dry-run
./scp-api.py snapshots 804027 revert before-live-test --yes
./monitor-task.py watch TASK_UUID
./scp-api.py server-details 804027
```

Restore replaces the server's current disk contents. The CLI refuses a missing
or duplicate snapshot name, checks the protected-server policy and active
provider tasks, and requires `--yes` for a non-interactive run. The restore is
asynchronous; wait for its task to finish, then verify the server boots from
the restored disk before treating it as a usable rollback point. To revert a
TrueNAS UI test, use a snapshot taken before that test and check the installed
version and boot-pool health after provider recovery.

## Restore a TrueNAS golden disk

`install-restore-iso.py` uploads a completed restore ISO to user ISO storage,
attaches it to the selected server, records the existing provider boot order,
and power-cycles into the unattended runtime. It writes a mode-0600 workflow
checkpoint under `.state/`; `status` shows the saved phase if the command is
interrupted.

Start the restore with the confirmed server ID and ISO path:

```bash
./install-restore-iso.py start 804027 \
  --iso ../truenas/golden-iso/artifacts/truenas-27-unattended-restore.iso \
  --yes
./install-restore-iso.py status 804027
```

The command reports local file size and SHA-256, the unique uploaded ISO name,
each multipart transfer part, SCP task state/progress, and attachment checks.
The TrueNAS restore environment separately reports disk discovery, selected
device, write/read-back progress, GPT repair, and completion to Mattermost.
Wait for its completion message and confirm that it reports the full disk
read-back check succeeded before finalizing. `--yes` only accepts the CLI's
provider confirmations; it does not bypass the separate completion assertion.

Then detach provider media, restore the original saved boot order, cold-cycle,
and verify SSH plus the boot pool:

```bash
./install-restore-iso.py finish 804027 \
  --restore-complete \
  --ssh-host v1001.vxxu.de \
  --ssh-host-key-alias '[127.0.0.1]:2223' \
  --ssh-identity-file ../truenas/artifacts/vm-lab/golden-source-27-20GiB/root_ed25519 \
  --ssh-known-hosts ../truenas/artifacts/vm-lab/golden-source-27-20GiB/restore-known_hosts \
  --yes
```

`--restore-complete` is a required operator assertion. The tool does not infer
completion from a reboot, a running server, or guest-side `CDROMEJECT`; that
ioctl does not detach Netcup's provider ISO. The finish stage detaches through
SCP, restores the saved provider boot order, completes a cold provider cycle,
waits for `RUNNING` with `configChanged=false`, and checks that root SSH
returns UID 0 and `boot-pool` is `ONLINE` without known data errors. It never
grows the restored ZFS partition or pool.

The restore is unattended and rotates nothing: the host keeps the golden image's
baked-in secrets (admin password hash, API key, root key, SSH host keys,
identity, TLS certificate). Name the ISO `...-<8-hex capture ID>.iso`; `start`
and `finish` then print the ID of the matching `...-<id>.secrets.toml` sidecar,
and `finish` ends with a pointer to the rotation checklist in
[golden-iso/README.md](../truenas/golden-iso/README.md#secrets-baked-into-a-golden-iso--what-to-change-after-a-restore).
Rotate before the host is exposed.

If the ISO did not send its completion notification, do not use `finish` to
assert that it did. After inspecting the checkpoint and provider state, use
`recover` to detach the attached ISO and try booting the restored disk. This
checks whether TrueNAS and its boot pool come up, but leaves restore completion
uncertified:

```bash
./install-restore-iso.py recover 804027 \
  --state-file .state/restore-804027-v5.json \
  --try-disk-boot \
  --ssh-host v1001.vxxu.de \
  --ssh-identity-file /workspaces/vbpub/scripts/truenas/artifacts/vm-lab/golden-source-27-20GiB/root_ed25519 \
  --ssh-known-hosts /workspaces/vbpub/scripts/truenas/artifacts/vm-lab/golden-source-27-20GiB/restore-known_hosts \
  --yes
```

The recovery command uses the boot order saved by `start`, waits for provider
tasks, then requires Netcup `RUNNING` with `configChanged=false` and root SSH
plus boot-pool health. Its terminal phase is `recovered`, not `complete`.
`status` reports that restore completion was not certified.

## Install from a generic ISO

The generic `install` verb works with an installer that shuts down the guest
after successful installation. It waits for the guest to run and then power
off, detaches the provider ISO while the server is off, restores the provider
boot order captured before attachment, and boots the installed disk. This
removes the boot media before the next boot so it cannot loop back into the
installer.

For the TrueNAS unattended relaxed ISO, generate the image with
`--post-install-action poweroff`, then run:

```bash
./install-restore-iso.py install 804027 \
  --iso ../truenas/vanilla-iso-patcher/artifacts/TrueNAS-27-unattended-relaxed.iso \
  --state-file /tmp/v1001-install.json \
  --yes
```

The command hashes the selected local ISO, uploads it, records a private
checkpoint, attaches it and waits for the installer poweroff. It then removes
the provider media, restores the saved boot order and powers on the installed
disk. Netcup's `RUNNING` plus `configChanged=false` confirms provider-level
disk boot. An optional readiness probe can be added for systems that serve
HTTP:

```bash
./install-restore-iso.py install 804027 \
  --iso ./installer.iso \
  --ready-url https://host.example.test/health \
  --ready-text ready \
  --state-file /tmp/example-install.json \
  --yes
```

`--ready-text` requires `--ready-url`; both are optional. A reboot-only
installer does not provide a reliable completion event to Netcup, so configure
the guest to power off after success. A normal guest reboot with provider media
still attached can start the installer again. `status` shows the recorded
phase; resume the same command with the same ISO and readiness settings to
continue an interrupted handoff. See the [generic install design](DESIGN-GUIDE.md#generic-installer-iso-handoff)
for why the successful-install poweroff is required.

The poweroff is a configured handoff signal, not guest-side proof that the
installer succeeded: the generic workflow does not parse console output or
installer logs. If the installer blocks waiting for a key, the provider still
reports the guest running and the wait times out until the prompt is handled.
If the installer reboots and remains running, this same wait cannot distinguish
that reboot from an installer that is still working. If the reboot appears as
an `OFF` sample, the generic check has no reason code and could mistake it for
the post-install handoff. Use SCP Screen/PWMCP for visual diagnosis in those
cases; see
[Inspect rescue status or the installer screen](CONSUMERS.md#inspect-rescue-status-or-the-installer-screen).

For a preview, run `start ... --dry-run` or `finish ... --dry-run`. An unfinished
checkpoint blocks a second start. Resume the recorded workflow with `finish`
after checking `status`; replace a completed checkpoint only after reviewing
the prior deployment and supplying `--replace-state` to the next `start`.
See [README](README.md#unattended-restore-iso-workflow) for the feature
overview and [DESIGN-GUIDE](DESIGN-GUIDE.md#restore-iso-lifecycle) for the
two-stage lifecycle rationale.

## Remote gate (run a lane on a test host)

`runner/remote-gate.py` runs one registered Run-Gate lane on a dedicated test host instead of the shared controller
host (whose CPU PSI is high when many agents gate in parallel and which also serves production). stdlib Python, all
process execution behind an injectable `Runner` (tests: `tests/test_remote_gate.py`, fake runner, no network).

```bash
remote-gate run <v1001|r1002|auto> <worktree-path> <lane> [--project-dir DIR] [--base REF] [--name ID] [--detach]
            [--wait-psi SECONDS] [--out-dir DIR] [-- extra run-gate args]
remote-gate status|logs [-f] [--lane-log]|collect|cancel <id>
```

- **Host table.** `--hosts-file` / `$REMOTE_GATE_HOSTS` / `~/.config/remote-gate/hosts.json` (gitignored; template
  `runner/remote-gate-hosts.example.json`): per host the pinned-known-hosts ssh argv (runner user, `StrictHostKeyChecking=yes`), the
  provider identity check command and the on-host guard. Only `r1002` and `v1001` are accepted anywhere (hard allowlist in code).
- **Host choice.** `auto` reads CPU and memory PSI `some avg10` over ssh on both hosts and takes the one with the lower max;
  if both exceed 25 it refuses (exit 75) and prints both readings. The Netcup identity check runs before any ssh/scp write and the
  on-host guard (hostname, server id) prefixes every remote command.
- **Source.** The worktree must have a clean tracked tree (untracked files are ignored); commit first with
  `git commit --only -- <paths>` (agents do anyway). HEAD is shipped as a thin git bundle against the closest commit the host already
  has under `~/checkouts` (no bundle if the host already has HEAD), checksum-verified, then checked out detached as a throwaway
  checkout `~/checkouts/vbpub/.worktrees/rg-<id>-<sha12>` (visible as `/workspaces/vbpub/.worktrees/rg-<id>-<sha12>` in the cockpit, so
  lane scripts that require the worktree under `/workspaces/vbpub/.worktrees/` accept it; existing host checkouts are never touched, only
  the empty `vbpub/.worktrees` parents remain after cleanup). A dirty tree is refused rather than
  patched in: one commit id is what the verdict is about.
- **Run.** A detached host job (`run-job rg-<id>`, `~/rg/<id>/` and `~/jobs/rg-<id>/{job.log,EXIT}`) waits until PSI is <= 25
  (`--wait-psi`, default 900 s, else exit 75, verdict `PSI_REFUSED`), then runs `nice -n 19 run-gate <lane> [--base SHA] [extra]` in the
  `mdt-cockpit-runner` container from the project dir (default: the worktree path's own subdirectory) with
  `RUN_GATE_ALLOW_MAIN_CHECKOUT=1` (the checkout is a throwaway clone) and `RUN_GATE_TESTER_UNIFIED_IMAGE=tester-unified:local`.
  The caller follows it, or `--detach` returns at once; the job survives the caller (`status`, `logs -f`, `collect` later).
- **What runs.** The cockpit's `run-gate` wheel and the host's `tester-unified:local` image; both are printed as `header:` lines in
  `job.log`. Refresh: v1001 `docker load` of a built tester-unified plus a tag `:local`, then `pip install` the wheel in the cockpit
  (`setup-cockpit-tools.sh`); do not change tools on a host while an R2 campaign there may resume (r1002 currently carries run-gate 24.3.0,
  see the host board).
- **Results**, copied to `<out-dir>/<id>/` (default `/home/vscode/vbpub-release-program-20261027/remote-gates`): `job.log`, `lane.log`,
  `lane-detail.log` (the lane's own log), `artifacts/` (`.assay/verdict*.json`, `progress*.jsonl`, `coverage*.json`, `.run-gate/**/*.log`),
  `meta.json`, `result.json`, `job.sh`. The last line printed is exactly `lane <lane> verdict <VERDICT>; exit_code <N>` (read it as a separate
  step); the command exits with the lane's exit code.
- **Cleanup.** On exit (pass, fail, cancel) the job removes the lane's containers by exact name (`run-gate-*` containing the commit prefix
  and the lane) and the throwaway checkout; logs stay. `cancel <id>` sends SIGTERM to the job, which stops the lane via its in-cockpit pid.
- **Limits.** Concurrent runs of the same commit and lane on one host share container name prefixes; run one at a time. R2 campaigns
  keep using `run-r2-campaign.sh`.
