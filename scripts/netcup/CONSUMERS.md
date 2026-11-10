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

### Keep wizard payloads out of the source tree

Wizard-generated `target-host.<hostname>.jsonc` files contain account and
target-specific facts. The default is the ignored `artifacts/install-host/`
directory. An explicit wizard `--config PATH` is an output path and is refused
if it resolves anywhere inside the source worktree. Use `--out-dir` to select
an external runtime directory, for example:

```bash
R=/home/vscode/vbpub-release-program-20261027
./install-host.py wizard --image 128 --out-dir "$R/v1001/install-review-20261107"
```

Review the generated file before passing its printed path to
`install --config FILE`. `target-host.*.jsonc` is ignored as an additional
guard against staging a per-host payload.

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

## Install-time disk plan (debian-install-v2 `partitioning`)

The producer's `partitioning` config rides in the customScript config that
`debian-install-v2.py build-customscript` embeds
(`VBPUB_CONFIG_EXTRA_JSON`); `install-host.py` passes that customScript
through untouched. Config loading and bundle construction refuse impossible fixed/percentage
size shapes and plans that exceed GPT's 128-entry limit. Before ordering a
reinstall, resolve disk fit and provide the target root geometry to the offline
resolver. Saved r1002 and v1001 evidence records a 512 GiB disk and vda3/root
starting at sector `2500608` (root number 3):

```bash
./debian-install-v2.py plan --config install.json --disk-size 512GiB \
  --root-start-sector 2500608 --root-number 3
```

Percentage sizes may share the pool with a numeric fixed reserve; the percent
pool is calculated after that reserve, raw iobench, and swap are removed.

The raw benchmark partition defaults to 8 GiB, has PARTLABEL `iobench-raw`,
and remains unformatted and unmounted. Set
`partitioning.iobench_raw_gb` to 2–10240 GiB, or `false` only when
`run_io_benchmark` is off. The runner finds the CoW volume by GPT PARTLABEL
`MDT_COW`; it does not use a fixed partition number or create a partition.
A missing/duplicate label, incompatible filesystem, or mount that disagrees
with `COW_MOUNT` is refused. The install plan must mount the CoW filesystem at
the configured `COW_MOUNT` (default `/srv/cow`).

Example portion of a 512 GiB host config with 160 GiB root, a CoW partition,
four 4 GiB swaps, and the default raw benchmark partition:

```json
{
  "partitioning": {"root": 160, "data": {"kind": "partitions", "volumes": [
    {"fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}
  ]}},
  "swap_policy": "on", "swap_disk_total_gb": 16, "swap_partition_count": 4
}
```

For the saved 512 GiB layout, the physical order is vda3 root at sector
`2500608`, vda4 `MDT_COW`, vda5 `iobench-raw`, then swaps vda6-vda9.
Partition numbers follow their disk position; the runner uses PARTLABEL so a
changed plan does not require an environment-file device-number edit.

## Guarded data-area-v2 reinstall

For the v1001/r1002 reinstall flow, stop the R2 campaign on v1001 through its
owning host-agent workflow before the reinstall sequence. Export its campaign
resume state, artifacts, and logs with a `SHA256SUMS` manifest; transfer the
export to r1002, verify it there, resume the campaign on r1002, and record the
handover on the campaign board. This is a separate host-agent action and must
be complete before the destructive sequence starts.

The read-only evidence exporter verifies API identity first, uses the pinned
SSH helper, checks the host name and server-ID file remotely, and writes an
allowlisted snapshot under each host's `pre-reinstall-<date>/` directory. It
redacts token, password, passphrase, pass/passwd/PWD suffixes, `adminpwd`,
`passkey`, `pin`, PresharedKey, API key, PAT, webhook URL fields and related
secret values. Covered examples include quoted JSON/YAML/INI pairs,
same-indent YAML secret sequences, multi-word plain values, JSONC comments,
`.netrc` passwords, `curl -u` and `--user` credentials, `redis-cli -a`,
`ldapsearch -w`, URL userinfo and `/hooks/<token>` paths, command-line options,
bearer headers, and private-key blocks.

The fixed input and redacted-output file budget is 1 MiB. The input line budget
is 4 KiB: allowlisted config and evidence lines are much shorter, and a longer
minified or encoded line is skipped and listed before scanning. Both redaction
passes scan tokens in linear time; the cap is defense in depth. Redaction
markers can expand a file, so the collector also skips and lists any output
that grows past 1 MiB. The `--max-file-bytes` option can lower the file budget.
Remote SSH collection has a 300-second wall-clock timeout and kills its whole
process group, including child `ssh` processes; a failure removes partial local
output. Ctrl-C on the controller during collection can leave the remote
`ssh`/collector running after the controller exits; it is read-only, but the
300 s limit is controller-side and no longer applies after interruption. The
iogovmon `scratch/` tree and Docker volume contents are excluded.

Known gaps include Slack/Discord webhook secrets in URL paths outside
`/hooks/<token>`, Kubernetes or Compose `name:`/`value:` secret pairs, prose
such as `the password is X`, secret-like INI section names, bare `key`/
`encryption_key`/`signing_key`/`tls_key`, bare `auth` or `cookie` fields, raw
JWTs, PGP private-key blocks, and multiline or encoded secret forms outside
the listed shapes. Round 8 added coverage for earlier misses `redis-cli -a`,
`ldapsearch -w`, `pin`, and `passkey`; `adminpwd` is now masked by the first
pass. `-p` followed by a path or a 1–5 digit number is kept for path/port uses.
The broad key matcher may hide harmless values such as `lock_passwd: true` and
`password_max_days`; that evidence loss is accepted. Review the exported file
names and contents, then verify every exported file against `SHA256SUMS` before
continuing. Run v1001 first, then r1002 only after v1001 passes post-install
checks.

From `scripts/netcup`, use the exact saved root geometry for the host and
reviewed config/bundle. The following uses the recorded 512 GiB disk, root
partition 3, and root start sector `2500608`:

```bash
R=/home/vscode/vbpub-release-program-20261027
./pre-reinstall-export.py v1001 --date 20261107
(cd "$R/v1001/pre-reinstall-20261107" && sha256sum -c SHA256SUMS)

./pre-reinstall.py v1001 \
  --config "$R/v1001/debian-install-v2-20261107.json" \
  --bundle "$R/v1001/debian-install-v2-customscript-20261107.json" \
  --disk-size 512GiB --root-start-sector 2500608 --root-number 3 \
  --repo-branch main --expect-commit "$REVIEWED_MAIN_COMMIT" \
  --ssh-key-id "$ACCOUNT_SSH_KEY_ID" \
  --run-dir "$R/v1001/reinstall-20261107" --dry-run
```

Use the exact full commit printed by `build-customscript` and approved by the
operator for `REVIEWED_MAIN_COMMIT`; the wrapper compares it with the current
remote commit for `main`. `ACCOUNT_SSH_KEY_ID` is the explicitly selected key
ID from the authenticated Netcup account. The dry-run prints `REMOTE_COMMIT=`
and `BUNDLE_SHA256=`. Inspect `reinstall.log`, confirm the API/SSH identity and
image ID `128`, and review the parsed wizard payload. Record the exact
`BUNDLE_SHA256` as `V1001_REVIEWED_BUNDLE_SHA256`; the dry-run records `EXIT=0` and
does not submit the install.

After the maintenance window is approved, repeat the same preflight with
`--yes`, the recorded hash, commit, and SSH-key ID. The wrapper repeats all
checks, including the wizard dry-run payload validation, before the submit
request. It writes the bundle checksum and a `reinstall.log.EXIT` record, and
streams wizard output to the terminal and log. If interrupted after submission,
use the task ID and `monitor-task.py watch <task-id>` printed by the wrapper or
stored in the run directory. Wizard output also goes to
`target-host.<hostname>.jsonc` under the requested run directory.

```bash
./pre-reinstall.py v1001 \
  --config "$R/v1001/debian-install-v2-20261107.json" \
  --bundle "$R/v1001/debian-install-v2-customscript-20261107.json" \
  --disk-size 512GiB --root-start-sector 2500608 --root-number 3 \
  --repo-branch main --expect-commit "$REVIEWED_MAIN_COMMIT" \
  --expect-bundle-sha256 "$V1001_REVIEWED_BUNDLE_SHA256" \
  --ssh-key-id "$ACCOUNT_SSH_KEY_ID" \
  --run-dir "$R/v1001/reinstall-20261107" --yes
```

Use the matching r1002 paths and image ID only after v1001 passes verification.
Before generating or selecting a bundle, move the exact stale payloads listed
in the operator runbook to that host's `stale-<date>/` directory. Keep those
files; never delete them or attach them. The preflight rejects removed Debian
v2 config keys before it can start the wizard.

After reinstall, obtain the new ED25519 host-key fingerprint through the
authenticated provider console or another verified provider channel. Do not
accept an unverified first-seen key. The updater verifies that fingerprint
with `ssh-keygen`, removes only the exact host records, and atomically installs
the candidate pin:

```bash
R=/home/vscode/vbpub-release-program-20261027
./update-host-pin.sh v1001 \
  --pin-file "$R/v1001/ssh_known_hosts-v1001-20261105" \
  --fingerprint "$V1001_PROVIDER_HOSTKEY_SHA256"
```

For r1002, use `r1002` and
`$R/r1002/ssh_known_hosts-after-reinstall-20261104` with the fingerprint
verified for that host. The updater refuses a fingerprint mismatch and leaves
the existing pin untouched.

## Remote gate (run a lane on a test host)

`runner/remote-gate.py` runs one registered Run-Gate lane on a dedicated test host instead of the shared controller
host (whose CPU PSI is high when many agents gate in parallel and which also serves production). stdlib Python, all
process execution behind an injectable `Runner` (tests: `tests/test_remote_gate.py`, fake runner, no network).

```bash
remote-gate run <v1001|r1002|auto> <worktree-path-of-any-git-repo> <lane> [--project-dir DIR] [--repo NAME] [--base REF]
            [--name ID] [--detach] [--wait-psi SECONDS] [--out-dir DIR] [-- extra run-gate args]
remote-gate status|logs [-f] [--lane-log]|collect|cancel <id>
```

### Assay self-qualification workspace

The cockpit launcher binds the runner's vbpub checkout at `/workspaces/vbpub`
in addition to the `/workspaces` parent bind. Assay's `self-qualification*`
lanes require that exact target when they inspect the selected worktree, so
use the checked-in `runner/start-cockpit-runner.sh` when recreating the
cockpit. The argv regression is in `tests/test_runner_scripts.py`.

- **Host table.** `--hosts-file` / `$REMOTE_GATE_HOSTS` / `~/.config/remote-gate/hosts.json` (gitignored; template
  `runner/remote-gate-hosts.example.json`): per host the pinned-known-hosts ssh argv (runner user, `StrictHostKeyChecking=yes`), the
  provider identity check command and the on-host guard. Only `r1002` and `v1001` are accepted anywhere (hard allowlist in code).
- **Host choice.** `auto` reads memory PSI `some avg10`, io PSI `full avg10` and CPU PSI `some avg10` over ssh on both hosts. Only memory some
  or io full above 25 refuses a host (CPU PSI is a stall measure, not utilisation, and is not a stop signal: hosts are meant to run at 350-400% CPU);
  among acceptable hosts the one with the lower CPU PSI is preferred. If both hosts exceed 25 on memory or io it refuses (exit 75) and prints both readings. The Netcup identity check runs before any ssh/scp write and the
  on-host guard (hostname, server id) prefixes every remote command.
- **Source.** The worktree must have a clean tracked tree (untracked files are ignored); commit first with
  `git commit --only -- <paths>` (agents do anyway). HEAD and, with `--base REF`, the base commit (named by a temporary local ref
  `refs/remote-gate/base` while the bundle is built, so a base that is not an ancestor of HEAD, e.g. `--base main` after main moved on,
  still reaches the host instead of ending the job with exit 72) are shipped as a thin git bundle against the closest commit the host
  already has under `~/checkouts` (closest by commits still missing for HEAD plus the base; no bundle if the host already has both),
  checksum-verified, then checked out detached as a throwaway
  checkout `~/checkouts/<repo>/.worktrees/rg-<id>-<sha12>` (visible as `/workspaces/<repo>/.worktrees/rg-<id>-<sha12>` in the cockpit,
  because the cockpit bind-mounts `~runner/checkouts` as `/workspaces`; lane scripts that require the worktree under
  `/workspaces/<repo>/.worktrees/` accept it; existing host checkouts are never touched, only the empty `<repo>/.worktrees` parents remain
  after cleanup). `<repo>` is the directory name of the worktree's main checkout (`git rev-parse --git-common-dir`, so
  `/workspaces/dstdns/.worktrees/x` ships as `dstdns`; `--repo NAME` overrides it; when the common git dir is not `<main checkout>/.git` (bare layout) or the main checkout's directory name is not a valid repository name, the command refuses with exit 2 and asks for `--repo`, never guessing from the worktree directory, because the name selects the prerequisite profile; names are letters, digits, `.` `_` `-`, never a commit id).
  A dirty tree is refused rather than patched in: one commit id is what the verdict is about.
  Seeds for the thin bundle are any `~/checkouts/<sha>` that already holds a commit of this repository (other repositories' checkouts are
  skipped by `git cat-file`); release tags of the repository (`*-v<digits>`, e.g. `dstdns-v0.1.0`, `run-gate-v25.0.2`) ride along as before.
- **Run.** A detached host job (`run-job rg-<id>`, `~/rg/<id>/` and `~/jobs/rg-<id>/{job.log,EXIT}`) waits until memory PSI some and io PSI full are <= 25 (CPU PSI is ignored)
  (`--wait-psi`, default 900 s, else exit 75, verdict `PSI_REFUSED`), then runs `nice -n 19 <run-gate> <lane> [--base SHA] [extra]` in the
  `mdt-cockpit-runner` container from the project dir (default: the worktree path's own subdirectory; pass `--project-dir .` for a repo
  whose `run-gate.toml` is at its root) with `RUN_GATE_ALLOW_MAIN_CHECKOUT=1` and `ASSAY_GATE_HOST_REPO_ROOT=~/checkouts/<repo>` (the host
  path behind the cockpit's `/workspaces/<repo>`, which assay's tester-unified lane cannot derive from `docker inspect` here) (the checkout is
  a throwaway clone) and `RUN_GATE_TESTER_UNIFIED_IMAGE` (the host's `image`, default `tester-unified:local`, or the repo profile's `tester_image`).
  `<run-gate>` is the first in-cockpit run-gate that meets the repo's version floor (see "Per-repo prerequisites"); its directory is put first on `PATH`.
  The caller follows it, or `--detach` returns at once; the job survives the caller (`status`, `logs -f`, `collect` later).
- **What runs.** A cockpit `run-gate` (default `/home/vscode/.local/bin/run-gate`, or the repo's newer venv) and the host's tester image; the
  repo name, the chosen run-gate version and the image are printed as `header:` lines in `job.log`. Refresh: v1001 `docker load` of a built
  tester-unified plus a tag `:local`, then `pip install` the wheel in the cockpit (`setup-cockpit-tools.sh`); do not change tools on a host
  while an R2 campaign there may resume (r1002's default cockpit run-gate is 24.3.0, see the host board).
- **Results**, copied to `<out-dir>/<id>/` (default `/home/vscode/vbpub-release-program-20261027/remote-gates`): `job.log`, `lane.log`,
  `lane-detail.log` (the lane's own log), `artifacts/` (`.assay/verdict*.json`, `progress*.jsonl`, `coverage*.json`, `.run-gate/**/*.log`),
  `meta.json`, `result.json`, `job.sh`. The last line printed is exactly `lane <lane> verdict <VERDICT>; exit_code <N>` for PASS and FAIL; any
  other verdict appends `; reason <text>` (and possibly ` [hint: ...]`). Parse it with `verdict (\w+); exit_code (\d+)`, never anchored at the end of the
  line (read it as a separate step); the command exits with the lane's exit code.
- **Lane declaration checks (before anything is shipped).** The committed `run-gate.toml` / `assay.toml` are read locally and a run that is
  certain to fail is refused with exit 2 and the reason: `--base` for an assay lane that declares its comparison base (`judge.base_source =
  "declared"`, or a `judge.base` without `base_source = "request"`; assay refuses `--request-base`, so drop `--base`); a lane in an exec-mode
  environment with no `container_name` (run-gate needs the worktree's own `ciu up` test-runner stack, which a throwaway host checkout never has
  and this helper does not start; `--allow-exec-env` overrides); a lane name that is declared nowhere only warns. The effective environment follows run-gate: the lane's own `environment`, else
  the `[assay] environment` default for assay lanes, `[assay.import].environment` for imported lanes (an `[assay.import]` lane is declared, never
  warned about; its `--base` check reads `assay.toml` under the same name), and every member of a `kind = "sequence"` lane. Only a provably exec-mode
  environment without `container_name` is refused. `--min-run-gate VER` raises or lowers the
  profile's run-gate floor for one run. The vbpub profile floors the host run-gate at 25.0.1 and falls back to the `gate-tools2502` venv, so r1002's
  default 24.3.0 is not used for estate lanes (add a newer venv with `runner/setup-cockpit-run-gate.sh`; a miss prints that command).
- **Local `main`.** The throwaway checkout is detached and has no branches, but lanes declare `base = "main"` (assay.toml of nyxloom,
  cowtree, worktree, ...). The controller's local `main` rides the same thin bundle (head `refs/heads/main`, its commits count towards the
  "closest host checkout" choice) and the job creates `refs/heads/main` in the checkout without checking it out, so `--base main` and
  `base = "main"` resolve like on the controller. Only a local branch `main` is shipped (no `origin/main`; no local `main` means none on the host).
- **Why a run failed.** When the verdict is neither PASS nor FAIL the last line carries the cause: `lane <lane> verdict ERROR; exit_code 2; reason
  <text>` (the verdict line's `reason`, the lane script's last own diagnostic line, else the job log's refusal line; `result.json` has the same `reason`).
  Known causes append a `[hint: ...]`. Known silent-looking causes: an exec-mode lane without a stack (above), a declared base given `--base`,
  a lane whose script requires `/workspaces/<repo>` to be a bind mount of its own (assay `self-qualification*`, backlog NC-14), a run-gate below the floor.
- **Cleanup.** On exit (pass, fail, cancel) the job removes, by exact name, only lane containers (label `run-gate.lane`, or a pre-RG-242 `run-gate-*` name) whose `docker inspect` references
  this run's unique checkout directory `rg-<id>-<sha12>` (and only once the lane started, so a PSI refusal removes nothing), then the
  throwaway checkout and its pid file; logs stay. Another run's containers are never matched. `cancel <id>` sends SIGTERM to the job
  only while the recorded pid's cmdline is this run's `job.sh`; the job then stops the lane via its in-cockpit pid.
- **Guard.** The on-host guard runs as `bash -c 'set -e; <guard>'` and the command runs only if it succeeded (fail-closed); write
  guard clauses with `&&` or `;`, both stop at the first failing test. A hosts file without a guard or api_check is refused.
  `status`/`logs -f`/a followed run give up with exit 3 after 5 consecutive failed polls (ssh down, guard failing).
- **Limits.** R2 campaigns keep using `run-r2-campaign.sh`. dstdns lanes that need the whole release gate (bake/up/clean of the dstdns
  stack, `release-gate`) are out of scope here: this helper runs one registered lane in the cockpit, exactly like vbpub lanes.

### Per-repo prerequisites (remote-gate, any repository)

Every repository has a profile (built-in `REPO_DEFAULTS` in `remote-gate.py`, overridden key by key by the host's `repos.<name>` in the hosts
file; unknown keys are refused at load time). A repository without a profile needs only the cockpit and its run-gate. The prerequisites are
checked in two places. Before anything is uploaded, over read-only ssh (a miss ends the command with exit 4, nothing created on the host): cockpit
up, required images, a run-gate meeting the floor (a proper PEP 440 comparison: release candidates and dev builds are below the floor, the version is the last
version-looking word of the first `--version` line), and the `uid_check` (a throwaway `docker run`, so only when the host's memory PSI some and io PSI full are at most 25, otherwise
deferred to the job). In the host job (a miss there ends with exit 76, verdict `PREREQ_MISSING`, `PREREQ MISSING: ...` lines and a pointer to this section in
`job.log`): all of those again, the `uid_check` after the job's own PSI wait, and the `admission_check`, which needs the repo's `run-gate.toml` and so can only
run once the checkout exists (it is therefore the one prerequisite that fails after the upload). The `admission_check` is fail-closed: an erroring
`admission show`, or output without an `enabled=true|false` statement, fails it.

| Profile key | Meaning | dstdns default |
| --- | --- | --- |
| `run_gate_min`, `run_gate_bins` | first in-cockpit run-gate path whose `--version` is >= the floor | `25.0.1` (RG-154: `--jobs` on a pyz-pinned consumer); `/home/vscode/.local/bin/run-gate`, then `/home/vscode/.venvs/gate-tools2502/bin/run-gate` |
| `required_images` | docker images that must exist on the host | `dstdns/test-runner:local` |
| `uid_check` | `getent passwd <uid>` inside the image (a throwaway `docker run --rm --network none`, only on a host at memory/io PSI <= 25: before the upload when quiet, always in the job after its PSI wait) | image `dstdns/test-runner:local`, uid `1003` (run-gate >= 25 refuses `user = "invoker"` without a passwd entry, DST-27) |
| `admission_check` | if `run-gate admission show` in the checkout says `enabled=true`, a `max_concurrent=N` cap must be published | on (dstdns `[admission] enabled = true`; without a cap every lane ends `NOT_RUN no-headroom`, DST-28) |
| `tester_image` | `RUN_GATE_TESTER_UNIFIED_IMAGE` for this repo on this host | host `image` |
| `env` | extra `NAME=value` environment for the lane's `docker exec`; the job's own safety variables (`RUN_GATE_ALLOW_*`, `ASSAY_GATE_HOST(S)_REPO_ROOT`, `RUN_GATE_PROFILE`, `RUN_GATE_TESTER_UNIFIED_IMAGE`, `PATH`, `HOME`, `DOCKER_HOST`, `DOCKER_CONFIG`, `LD_PRELOAD`, `LD_LIBRARY_PATH`) are reserved and refused at load time | none |

Bring-up steps for a (re)installed r1002/v1001 (none depends on earlier one-off host state; identity check before every write, one gate at a
time, memory PSI `some avg10` and io PSI `full avg10` <= 25 (CPU PSI is not a stop signal), never nano1):

1. Runner, cockpit and `tester-unified:local`: the generic order in `runner/README.md`. Host-setup is mandatory
   before the cockpit; there is no raw-socket degraded mode. The cockpit must be running before cockpit-only steps.
2. **A run-gate >= 25.0.1 for dstdns**: as the runner user, `run-job setup-rg-25.0.2 -- bash ~/runner-scripts/setup-cockpit-run-gate.sh 25.0.2 <wheel-sha256>`.
   `~/runner-scripts` is created by `provision-runner.sh` (it installs the runner scripts and `runner.env` there, from the directory the controller uploaded in
   step 1, so a reinstalled host has it after provisioning); the script also needs the synced vbpub checkout (`~/src/vbpub-current`, `sync-from-devcontainer`,
   `runner/README.md` step 3) to be recent enough to contain `tester-unified/fetch-run-gate-wheel.py` (it exits 4 with a message otherwise). It creates
   `~/.venvs/gate-tools2502` in the cockpit home and is idempotent: a rerun after success is a no-op, a failed install leaves no venv at that path (the partial
   one is moved to `<venv>.failed.<epoch>`; delete it by hand when you have looked at it) and the rerun rebuilds clean. The wheel is installed with
   `--no-deps --no-index`: Run-Gate is stdlib-only (`dependencies = []`), so nothing comes from PyPI and there is nothing to pin; should a Run-Gate release ever
   gain dependencies the install check fails and the script needs a hashed requirements file (`uv pip compile --generate-hashes`). Or put a newer run-gate in
   the cockpit default venv when no R2 campaign on that host can resume; then `run_gate_bins` needs no extra path. Verify:
   `docker exec -u vscode mdt-cockpit-runner /home/vscode/.venvs/gate-tools2502/bin/run-gate --version`.
3. **A dstdns checkout on the host** (needed for steps 4 and 5 only; the helper itself ships a git bundle per run and keeps no persistent dstdns checkout:
   each run clones into `~/checkouts/dstdns/.worktrees/rg-*` and deletes it at the end). Once per reinstall: on the controller, in the dstdns repository,
   `git bundle create /tmp/dstdns.bundle HEAD`, upload it to the runner's `~/incoming/`, then as the runner `git clone ~/incoming/dstdns.bundle ~/checkouts/dstdns-admin`.
   `~/checkouts` is the cockpit's `/workspaces`, so the clone is `/workspaces/dstdns-admin` in the cockpit; it must contain `run-gate.toml` with `[admission]`.
4. **The dstdns runner image**: `dstdns/test-runner:local`, built from that clone as the runner user (`cd ~/checkouts/dstdns-admin && docker buildx bake --builder default --load test-runner`;
   the Dockerfile bakes the `invoker` uid-1003 passwd entry since `4e2956a6`; an older image needs a `docker commit` layer adding that entry). On a second host
   `docker save | docker load` it. Verify `docker run --rm --entrypoint getent dstdns/test-runner:local passwd 1003`.
5. **The admission cap**: `docker exec -u vscode -w /workspaces/dstdns-admin mdt-cockpit-runner <run-gate >= 25.0.1> admission set --max-concurrent N --unreadable-policy refuse`
   (host-wide Docker state; `admission set|show` read the working directory's `run-gate.toml`, which is why step 3 exists; N = 1 is one dstdns gate at a time,
   raise it deliberately). Verify the same command with `show` prints `max_concurrent=N`.
6. **tester image tag** (only when the host keeps an older `tester-unified:local` for running campaigns): tag the newer image (r1002: `tester-unified:r25-<id>`)
   and set `repos.dstdns.tester_image` in the hosts file.
7. **Hosts file**: `repos.dstdns` is only needed to override a default (extra `run_gate_bins`, `tester_image`); see `runner/remote-gate-hosts.example.json`.

Typical dstdns call (the worktree must be committed and clean; the runner image is built from the host-side checkout, so a lane that depends on
a changed Dockerfile judges the new code with the host's existing image):

```bash
remote-gate run r1002 /workspaces/dstdns/.worktrees/<name> run-gate-docs --project-dir .
remote-gate run auto  /workspaces/dstdns/.worktrees/<name> release-r1 --project-dir . --detach
```

### Runner cockpit endpoints

After host-setup, cgprofile and topos have installed their services, check the
host-published endpoint record before starting the cockpit. Its values supply
the cockpit's socket paths and numeric GIDs; they do not belong in
`runner/runner.env`.

```bash
set -e
cat /etc/host-endpoints.env
cd "$HOME/src/vbpub-current/mdt-cockpit/host-setup"
sudo ./check.sh
cd "$HOME/runner-scripts"
./start-cockpit-runner.sh
```

The runner requires the Docker API and BuildKit facts from host-setup, the
cgprofile control socket, directory and GID, the cgprofile observer socket
and directory, and the topos observe socket, directory and GID. The loader
requires `/etc/host-endpoints.env` to be a root-owned regular file with no
group or other write permission; it rejects symlinks, duplicate keys and
values outside the published alphabet before any Docker call. If startup names
a missing variable, repair it through the publisher that owns it: host-setup,
`cgprofiled install`, or `toposd install`. Run that publisher's checker and inspect
the aggregate record again, then retry startup. Do not copy a value from
another host or add a local fallback. The cockpit binds each published socket
directory read-only at the same path and receives the socket values in its
environment. It gets numeric group access for Docker, cgprofile control,
topos observe and BuildKit. It does not receive the observer GID; Run-Gate
grants that to an observer lane when it starts the lane container.

`provision-runner.sh` runs before endpoint publication in the normal setup
order. During that bootstrap only, it adds the runner to the host group named
`docker`; host-setup later publishes the GID of that same group. A provisioning
rerun validates an existing record, and cockpit startup checks that the
published GID still matches the local group. See the [design rationale](DESIGN-GUIDE.md#runner-cockpit-uses-the-host-endpoint-record)
and [`runner/README.md`](runner/README.md).

### Install augeaswrap for host-setup

Copy the release wheels and their trusted SHA-256 sidecars into a host-local
directory before installation. The install command performs no downloads; it
checks both hashes, creates the versioned venv and publishes the command on
root's `PATH`.

```bash
wheel_dir=/var/cache/host-wheels
augeaswrap_sha256=$(awk 'NR == 1 { print $1 }' "$wheel_dir/augeaswrap-0.2.0-py3-none-any.whl.sha256")
cli_extended_sha256=$(awk 'NR == 1 { print $1 }' "$wheel_dir/cli_extended-0.6.0-py3-none-any.whl.sha256")
sudo bash "$HOME/runner-scripts/install-augeaswrap.sh" \
  --wheel-dir "$wheel_dir" \
  --augeaswrap-version 0.2.0 --augeaswrap-sha256 "$augeaswrap_sha256" \
  --cli-extended-version 0.6.0 --cli-extended-sha256 "$cli_extended_sha256" \
  --dry-run
sudo bash "$HOME/runner-scripts/install-augeaswrap.sh" \
  --wheel-dir "$wheel_dir" \
  --augeaswrap-version 0.2.0 --augeaswrap-sha256 "$augeaswrap_sha256" \
  --cli-extended-version 0.6.0 --cli-extended-sha256 "$cli_extended_sha256"
command -v augeaswrap
```

Use SHA-256 values checked against the release record; the adjacent sidecars
are input files, not an independent trust source. The installer uses
`pip install --no-index --no-deps`, so both required wheels must be present.
See the [design rationale](DESIGN-GUIDE.md#host-augeaswrap-is-installed-from-verified-offline-wheels)
and the full [runner host sequence](runner/README.md#install-augeaswrap-for-host-setup).

### Runner rollback commands

Remove the additive Run-Gate venv only after the host profile no longer names
it and the cockpit is running. Remove the pinned PostgreSQL image only after
all lanes that need it have finished:

```bash
docker exec -u vscode "$COCKPIT_NAME" bash -lc 'rm -rf -- "$HOME/.venvs/gate-tools2540"'
docker rmi 'postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
docker image inspect 'postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
```

The final inspect must return nonzero. Do not remove the shared endpoint
aggregate or profile loader on a host with multiple publishers. The sole-cgprofile
cleanup applies only to the observed host; see [NC-29](KNOWN_ISSUES_TODO_BACKLOG.md#nc-29--mixed-endpoint-publisher-rollback-can-delete-a-shared-aggregate)
and the [runner rollback sequence](runner/README.md#rollback).

### Run-Gate host profile default

Use Run-Gate 25.4.1 as the default for both test hosts only after collecting
the old campaign and resume state. Until then, preserve the current pins and
venvs; keep prior venvs available for rollback after the switch. HP-D10 is the
accepted target, not evidence of a completed host change. This repository
change does not update a host hosts-file.
