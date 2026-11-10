<!-- origin: 2026-11-05; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/div2-reinstall-RUNBOOK.md -->
<!-- relation: reinstall runbook; verbatim source text follows -->

# Reinstall runbook: v1001 then r1002 with data-area-v2

Status: **design only; no host or device action was taken for this revision.**
Updated 2026-11-07 for fix 5, from saved host exports and the fix4 review.
This runbook uses the current `partitioning` contract. The old keys and runner-side carve
are removed; the 2026-11-04 bundles cannot be reused.

The unmerged `reports/div2-runbook-swap-benchmark.patch` is retired as of
2026-11-09; do not apply it. Its swap-benchmark instructions describe an older
wrapper draft. Use the committed wrapper and current copyable procedure in
`scripts/debian-install-v2/docs/CONSUMERS.md`.

The current fix branch is not an install source. Before scheduling a reinstall,
confirm that the reviewed fix commit is available from the repository branch
embedded in the new customScript bundle, then rerun the offline plan against
both saved host configs. Do not interpret this runbook as permission to operate
the hosts during this code task.

## 0. Separate R2 campaign stop and handover (required before reinstall)

A separate host-agent action must stop dispatching the R2 campaign to v1001,
export its resume state, campaign artifacts, and job logs, and hand the campaign
off to r1002 before any reinstall step. Use the campaign's owning supervisor
workflow; do not stop or abandon a live campaign outside that workflow. Include
the campaign board `R2-BOARD-2026-11-05.md` and record the stop/handover there.
For each exported tree, create and verify a manifest before transfer:

```bash
(
  set -euo pipefail
  cd "$EXPORT_DIR"
  test ! -e SHA256SUMS
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 -r sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
```

Transfer the complete export to the approved r1002 campaign location, verify
its `SHA256SUMS` there, resume the campaign on r1002 through its owning
workflow, and record the resumed job/run ID and verification in the board.
Reinstall is blocked until this separate host-agent handover is complete and
reviewed. This runbook revision performs no campaign or host action.

## 1. Target layout and host evidence

Both real Netcup VPSs have 4 vCPUs and are reported with 512 GiB disks
(549755813888 bytes; 1073741824 sectors). The r1002 install log records
`vda3 start=2500608` at
`r1002/v2-reinstall-20261104.log:265-269` (also 322-328). The v1001 reinstall
log records the same start at `v1001/v2-reinstall-20261105.log:296`, and its
saved export records it in `v1001/export-20261105/meta/host-state-and-hashes.txt`.
Thus root partition 3 starts at sector `2500608` on both hosts in saved
evidence; this is recorded, not inferred. The `2625536` value from an earlier
review has no matching partition record. Re-read each host's root start
immediately before reinstall. If either live value differs, stop and resolve
a fresh offline plan for that host before generating its bundle.

The explicit plan for each host is:

```json
{
  "partitioning": {
    "root": 160,
    "reserve_gb": 0,
    "iobench_raw_gb": 8,
    "data": {
      "kind": "partitions",
      "volumes": [
        {"size": "*", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}
      ]
    }
  },
  "swap_policy": "on",
  "swap_disk_total_gb": 16,
  "swap_partition_count": 4,
  "run_io_benchmark": true
}
```

With disk size 512 GiB, root number 3, and recorded root start sector 2500608,
the resolved plan is the same for both example configs and matches the saved
partition records for both hosts. Re-resolve after the live pre-install read:

| Partition | PARTLABEL (GPT) | Filesystem LABEL / role | Start sector | Size (sectors) | Size (GiB) |
|---|---|---|---:|---:|---:|
| vda3 | — | root ext4 | 2500608 | 335544320 | 160.00 |
| vda4 | `MDT_COW` | `LABEL=MDT_COW`, btrfs at `/srv/cow` | 338044928 | 685363200 | 326.81 |
| vda5 | `iobench-raw` | unformatted | 1023408128 | 16777216 | 8.00 |
| vda6 | **empty** | swap `LABEL=vbpub-swap6` | 1040185344 | 8388608 | 4.00 |
| vda7 | **empty** | swap `LABEL=vbpub-swap7` | 1048573952 | 8388608 | 4.00 |
| vda8 | **empty** | swap `LABEL=vbpub-swap8` | 1056962560 | 8388608 | 4.00 |
| vda9 | **empty** | swap `LABEL=vbpub-swap9` | 1065351168 | 8388608 | 4.00 |

`PARTLABEL` is the GPT partition name; filesystem `LABEL` is metadata written
by the filesystem formatter. Managed swap partitions intentionally have an
empty GPT `PARTLABEL`; `vbpub-swapN` is the swap filesystem label. Each swap
fstab row identifies its partition as `PARTUUID=<partition-guid> none swap ...`.
Use the filesystem `LABEL` and the fstab `PARTUUID` for these swaps; do not
look for `PARTLABEL=vbpub-swapN`.

There is no unallocated reserve in this plan. `MDT_COW` takes the remainder
between root and raw iobench/swap. The final partition ends 2048 sectors before
the 512 GiB disk end; this is alignment slack before GPT backup metadata.

The installer runs `iobench` on vda5 in stage two after writing the final table
and before configuring io.cost, root growth, data formatting, or swap
activation. io.cost consumes the successful benchmark result. The raw
partition remains unformatted and reserved afterward. The runner finds the
CoW partition by GPT PARTLABEL `MDT_COW`; it does not create or carve a
partition. `runner.env` may set `COW_MOUNT=/srv/cow`; it must not set
`COW_DEVICE` or `COW_SIZE_GIB`.

## 2. Configs and exact offline regeneration commands

The shipped example configs (both v2; their offline resolution is covered
by the Netcup suite) are:

- `scripts/netcup/examples/debian-install-v2/r1002.json`
- `scripts/netcup/examples/debian-install-v2/v1001.json`

They are loaded by the Netcup test and resolved against the 512 GiB disk,
root start sector `2500608`, and root number `3`. Dated operator configs are
`r1002/debian-install-v2-20261107.json` and
`v1001/debian-install-v2-20261107.json` under
`<controller-program-dir>/`. Each removes the legacy
`root_max_size_gb` and `root_resize_policy` keys and carries the explicit plan
above. Removed keys are rejected: `root_max_size_gb`,
`root_resize_policy`, `disk_layout`, `data_area`, `data_partition`, and
`data_lvm`. Known old artifacts under the host folders include the 20261104
JSON configs/customScript bundles, v1001's 20261105 customScript bundle, and
the existing `debian-install-v2.json`/`debian-install-v2-customscript.json` names;
they must not be attached because they carry removed settings. The external
`prepare-install-20261104.py` scripts under both host folders orchestrated
`debian-install-v2.py build-customscript` and wrote the dated customScript
bundle plus a Netcup install payload; they read the old
`install-config-20261104.toml` values and are stale. The v1001
`reinstall-v2-v1001-20261105.sh` additionally asserts the removed
`disk_layout == "dev-host"` and `data_area == "partition"` keys. Do not run
those helpers or attach an old bundle. Use the reviewed project's
`build-customscript` command below and submit through the guarded
`scripts/netcup/pre-reinstall.py` flow in section 4; do not hand-edit a
prior bundle into a hybrid.

From the worktree containing the reviewed fix, check both configs without
opening a device:

```bash
cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/debian-install-v2
PYTHONPATH=/workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/libraries/cli-extended/src \
  /opt/mdt/venv/bin/python3 debian-install-v2.py plan \
  --config <controller-program-dir>/r1002/debian-install-v2-20261107.json \
  --disk-size 512GiB --root-start-sector 2500608 --root-number 3
PYTHONPATH=/workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/libraries/cli-extended/src \
  /opt/mdt/venv/bin/python3 debian-install-v2.py plan \
  --config <controller-program-dir>/v1001/debian-install-v2-20261107.json \
  --disk-size 512GiB --root-start-sector 2500608 --root-number 3
```

The r1002 and v1001 output tables must match section 1 exactly. `--root-number`
and `--root-start-sector` are required here because the offline resolver omits
pre-root partition geometry by default. The disk size and root start sector
must be checked against each host's latest saved or live facts; never substitute
a remembered default.

After the reviewed code is available from `main`, regenerate both provider
bundles with the exact config, branch and **commit** that the host must run.
`--repo-commit` pins the full reviewed SHA: the bundle exports `REPO_COMMIT`,
names that commit in the bootstrap URL, and `bootstrap-remote.py` fetches the
commit by SHA and refuses to continue unless `HEAD` equals it, so a branch that
moves between review and host boot cannot change the code the host runs.
`REVIEWED_MAIN_COMMIT` must be the full SHA of the merged, reviewed `main`
commit (the `set -u` makes an omission fail closed):

```bash
cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/debian-install-v2
umask 077
set -u
PYTHONPATH=/workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/libraries/cli-extended/src \
  /opt/mdt/venv/bin/python3 debian-install-v2.py build-customscript \
  --config <controller-program-dir>/r1002/debian-install-v2-20261107.json \
  --repo-branch main --repo-commit "$REVIEWED_MAIN_COMMIT" --controller-ssh-placeholder \
  > <controller-program-dir>/r1002/debian-install-v2-customscript-20261107.json
PYTHONPATH=/workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/libraries/cli-extended/src \
  /opt/mdt/venv/bin/python3 debian-install-v2.py build-customscript \
  --config <controller-program-dir>/v1001/debian-install-v2-20261107.json \
  --repo-branch main --repo-commit "$REVIEWED_MAIN_COMMIT" --controller-ssh-placeholder \
  > <controller-program-dir>/v1001/debian-install-v2-customscript-20261107.json
```

Before attaching either bundle, inspect its `config.partitioning`, confirm
`run_io_benchmark: true`, `swap_policy: on`, and the completion marker, and
confirm the embedded `REPO_BRANCH`/`REPO_COMMIT` point to the reviewed source.
`pre-reinstall.py` regenerates the bundle itself with `--expect-commit` as the
pin and requires byte equality, so a bundle built without (or with a different)
`--repo-commit` is refused. `build-customscript` rejects the old keys; do not
add compatibility fields. The generated config must agree with the offline
table above.

### Operator scripts outside vbpub

The release-program root is a symlink, so the discovery command must follow it:

```bash
find -L <controller-program-dir> \
  \( -name 'prepare-install*' -o -name 'runner.env*' \) -print
```

The stale bundle preparers discovered outside vbpub are exactly
`r1002/prepare-install.py`, `r1002/prepare-install-20261104.py`, and
`v1001/prepare-install-20261104.py`. The old reinstall wrappers are exactly
`r1002/reinstall-v2-r1002.sh`, `r1002/reinstall-v2-r1002-20261104.sh`,
`v1001/reinstall-v2-v1001-20261104.sh`, and
`v1001/reinstall-v2-v1001-20261105.sh`. The last v1001 wrapper asserts the
removed `disk_layout == "dev-host"` and `data_area == "partition"` keys. The
old runner provisioners are exactly `r1002/provision-runner-r1002.sh` and
`v1001/runner-scripts/lib.sh` plus
`v1001/runner-scripts/provision-runner.sh`; the two v1001 copies carve a
partition, while the r1002 copy assumes `/dev/vda8` and may create a
partition. The three live env files with `COW_DEVICE=/dev/vda8` are exactly
`r1002/runner.env`, `v1001/runner-scripts/runner.env`, and
`v1001/runner-scripts/runner.env.20261105`. The exported snapshots
`v1001/export-20261105/home/runner-scripts/runner.env` and
`v1001/export-20261105/root/home-root/runner-scripts/runner.env` are historical
evidence; do not deploy or edit them. The two exported `runner.env.example`
files are examples in archived checkouts, not live operator inputs.

The exact fail-closed retirement, current runner-copy replacements, and live
env edits are in
`<controller-program-dir>/reports/div2-operator-scripts-v2.patch`.
It is a unified diff against those 14 operator files and has not been applied.
Apply it only in the release-program tree after the reviewed commit is
available. Replace bundle generation by calling
`debian-install-v2.py build-customscript`, Netcup submission by calling the
guarded `scripts/netcup/pre-reinstall.py` workflow after its evidence export,
and runner setup by synchronizing and calling the current
`scripts/netcup/runner/provision-runner.sh`; these in-repo
workflows own the same functions as the old operator helpers.
## 3. Hard preconditions

1. The reviewed commit and required gate evidence are available from the exact
   repository branch named by the customScript. Run the registered
   `debian-install-v2` `gate` lane and Netcup `suite` lane before scheduling.
   **VM status: lane `r1-vm-real-commit` has not yet run for this revision.**
   Before the first reinstall, run it on a QEMU-capable machine with its
   declared cgroup/buildx environment:

   ```bash
   cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/debian-install-v2
   nice -n 10 run-gate r1-vm-real-commit
   ```
2. Re-read each host's disk bytes, root partition number/start, current table,
   running workloads, campaign board, and service health before its window.
   Resolve the plan again if any disk fact changed. The expected disk is vda,
   exactly 512 GiB, with vda3 as root; never assume those facts from this file.
3. The run window is confirmed. Drain one host at a time, v1001 first. Do not
   cancel a campaign without the operator's decision. Collect final-exit and
   campaign evidence before reinstalling; only start r1002 after v1001 passes
   the post-install checks and the operator approves continuing.
4. No Netcup snapshot is a rollback: the 2026-11-04 provider reinstall removed
   v1001 snapshots. The controller-side archive and collected configuration
   are the recovery record.
5. The pre-reinstall export
   `v1001/export-20261105/meta/host-state-and-hashes.txt` records evaluation
   pool `zp` on vda6/vda7. That export predates the 2026-11-05 reinstall. The
   later `v1001/v2-reinstall-20261105.log` and
   `reports/v1001-layout-verify-post-reboot.txt` show vda4-vda7 as swap and
   vda8 as `MDT_COW`; those saved post-reinstall records do not show ZFS
   members. Re-read the current host and any active pool before the window;
   preserve needed evaluation results, but do not assume the old ZFS member
   layout still exists. The new plan has no ZFS partition.
6. Keep `COW_MOUNT=/srv/cow` aligned with `partitioning.data` and remove any
   old `COW_DEVICE` or `COW_SIZE_GIB` setting from `runner.env`. Do not start
   runner provisioning unless the installer-created `MDT_COW` partition is
   present, mounted at `/srv/cow`, and verified as btrfs.

## 4. Drain, collect, and reinstall one host

Before any controller-side write, use the protected-server/API guards already
in `dstdns-r2/env.sh` and verify the exact server ID, hostname, and server ID
file. Never target <protected-host> or any other server. Re-inspect these values in the
maintenance window; this runbook does not authorize a host action.

Drain and collect, in order. Complete this sequence for v1001 first. Repeat it
for r1002 only after v1001 passes every post-install check and the operator
approves continuing.

1. Exclude the host from new remote gates and update the R2 board to DRAINING.
   Wait for each running campaign to finish or record the operator-approved
   abandonment. Collect campaign directories, referenced `.assay/` state, job
   logs, and checkouts with unpushed commits through their owning gate workflow.
2. Before any reinstall command, run the read-only, allowlisted system evidence
   exporter for the drained host. It calls that host's `*_api_check` before its
   pinned SSH helper, then checks `hostname -f`, `hostnamectl --static`,
   `/etc/vbpub/server-id`, and `/dev/vda` before it reads evidence. It writes
   to `$R/<host>/pre-reinstall-20261107/` under the release-program root, with
   mode `0700`. The allowlist includes `sfdisk -d`, `lsblk -b -J`, `blkid`,
   `/etc/fstab`, `/etc/hosts`, `/etc/vbpub/server-id` and `bootstrap.env`,
   `/etc/mdt`, `/etc/mdt-cow-root.sh`, cloud-init config, containerd and Docker
   config, systemd units, sysctl, udev rules, sudoers drop-ins,
   `/home/runner/runner-scripts/runner.env`, the cockpit config/home directories
   named by that file, `/var/lib/vbpub/bootstrap`, `/var/log/debian-install`,
   `/root/custom_script.output*`, `/var/lib/iobench`, `/var/lib/iogovmon`, and
   Docker volume names only. It excludes the iogovmon `scratch/` directory and
   never inspects or copies Docker volume contents. The fixed input and
   redacted-output file budget is 1 MiB; the input line budget is 4 KiB. The
   allowlisted config and evidence lines are far shorter than 4 KiB, so a longer
   minified or encoded line is skipped and listed before scanning. Redaction
   markers can expand a file; the collector also skips and lists output above
   1 MiB. Both scanner passes are linear in token length, making the cap defense
   in depth. `--max-file-bytes` may lower the file budget. The remote SSH
   collection has a 300-second wall-clock timeout and kills its process group,
   including child `ssh`; failed collection removes its partial local output.
   Ctrl-C on the controller during collection can leave the remote
   `ssh`/collector running after the controller exits; it is read-only, but the
   300 s limit is controller-side and no longer applies after interruption.
   Redaction is a best-effort filter for the **listed shapes only**, not a
   guarantee for arbitrary content: secret-looking names (`TOKEN`, `PASSWORD`,
   `PASSPHRASE`, `PASS`/`PASSWD`/`PWD` suffixes, `adminpwd`, `passkey`,
   `pin`, `PRESHAREDKEY`, `PRIVATE`, `API_KEY`, `PAT`, `SECRET`,
   `CREDENTIAL`, camelCase variants such as
   `apiToken`/`dbPassword`/`clientSecret`, and keys containing both
   `webhook` and `url`) in `key: value`/`key=value` pairs and JSON, systemd
   `Environment=NAME=value` (quoted or not), YAML children below a secret
   parent key or block scalar including a sequence at the parent's indent,
   multi-word plain values after secret keys, `chpasswd` input (YAML list,
   pipe, here-string, heredoc), `.netrc` passwords, `curl -u`/`--user`
   credentials, `redis-cli -a`, `ldapsearch -w`, URL userinfo for any scheme
   (`postgresql://`, `redis://`, ...),
   webhook credentials in `/hooks/<token>` paths, `--password X`/
   `--password=X`/`--db-password X`, `-p X`/`-pX`, `Authorization` and
   `bearer` values, raw GitHub/GitLab/Slack/AWS-style token formats, JSONC
   comments and PEM private-key blocks. A second, independently structured
   detector re-checks every file; a file it still flags is **skipped and
   listed** in `meta/collection.txt` ("skipped binary, sensitive or
   unredactable file"), never exported. Round 8 closed the earlier misses for
   `redis-cli -a`, `ldapsearch -w`, `pin`, and `passkey`; `adminpwd` is now
   masked by the first pass. Remaining known gaps include Slack/Discord webhook
   secrets in URL paths outside `/hooks/<token>`, Kubernetes or Compose
   `name:`/`value:` pairs (including JSON name/value objects), prose such as
   `the password is X`, INI `[section]` names, bare
   `key`/`encryption_key`/`signing_key`/`tls_key`, bare `auth` or `cookie`
   fields, raw JWTs, PGP private-key blocks, and multiline or encoded secret
   forms outside the listed shapes. `-p` followed by a path or a 1–5 digit
   number is kept for path/port uses. The broad key matcher may hide harmless
   values such as `lock_passwd: true` and `password_max_days`; that evidence
   loss is accepted. Review the export before it leaves the controller.
   Binary/NUL, FIFO/device/socket and symlinked files are skipped (a FIFO no
   longer stalls the collector).

   The 20261105 v1001 export contains configuration absent from the earlier
   exporter allowlist: `/etc/hosts`, `/etc/cloud/cloud.cfg.d`,
   `/etc/containerd/config.toml`, `/etc/docker/daemon.json`, all of `/etc/mdt`,
   `/etc/mdt-cow-root.sh`, `/etc/udev/rules.d`, `/etc/sudoers.d`,
   `/etc/sysctl.d`, `/etc/systemd/system`, `/etc/vbpub/bootstrap.env`, and the
   cockpit config/home paths referenced from `runner.env`. The exporter now
   includes these read-only paths. No r1002 export directory was present in the
   supplied release-program tree for comparison; do not infer r1002-specific
   files from the v1001 snapshot.
3. Verify the drained host's manifest and review its exported file names before any
   `pre-reinstall.py --yes` run. If export, identity verification, redaction or hashing
   fails, stop. There is no reinstall without a verified export:

   ```bash
   (
     set -euo pipefail
     R=<controller-program-dir>
     cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/netcup
     /opt/mdt/venv/bin/python3 pre-reinstall-export.py v1001 --date 20261107
     (cd "$R/v1001/pre-reinstall-20261107" && sha256sum -c SHA256SUMS)
     find "$R/v1001/pre-reinstall-20261107" -type f -printf '%P\n' | sort
   )
   ```

4. Quarantine the exact stale payloads before creating or selecting a new
   bundle. The destination must not already exist; `mkdir` intentionally fails
   on a repeat so the operator cannot silently replace prior quarantine data.
   These are moves, never deletions. The 20261107 config and bundle stay in
   place:

   ```bash
   (
     set -euo pipefail
     R=<controller-program-dir>
     mkdir -m 0700 -- "$R/r1002/stale-20261107"
     mv -- "$R/r1002/debian-install-v2.json" "$R/r1002/stale-20261107/"
     mv -- "$R/r1002/debian-install-v2-customscript.json" "$R/r1002/stale-20261107/"
     mv -- "$R/r1002/debian-install-v2-20261104.json" "$R/r1002/stale-20261107/"
     mv -- "$R/r1002/debian-install-v2-customscript-20261104.json" "$R/r1002/stale-20261107/"
     mv -- "$R/r1002/netcup-install.jsonc" "$R/r1002/stale-20261107/"
     mv -- "$R/r1002/netcup-install-20261104.jsonc" "$R/r1002/stale-20261107/"
     mv -- "$R/r1002/install-config.toml" "$R/r1002/stale-20261107/"
     mv -- "$R/r1002/install-config-20261104.toml" "$R/r1002/stale-20261107/"
     mv -- "$R/r1002/target-host.jsonc" "$R/r1002/stale-20261107/"
     mkdir -m 0700 -- "$R/v1001/stale-20261107"
     mv -- "$R/v1001/debian-install-v2-20261104.json" "$R/v1001/stale-20261107/"
     mv -- "$R/v1001/debian-install-v2-20261105.json" "$R/v1001/stale-20261107/"
     mv -- "$R/v1001/debian-install-v2-customscript-20261104.json" "$R/v1001/stale-20261107/"
     mv -- "$R/v1001/debian-install-v2-customscript-20261105.json" "$R/v1001/stale-20261107/"
     mv -- "$R/v1001/netcup-install-20261104.jsonc" "$R/v1001/stale-20261107/"
     mv -- "$R/v1001/netcup-install-20261105.jsonc" "$R/v1001/stale-20261107/"
     mv -- "$R/v1001/install-config-20261104.toml" "$R/v1001/stale-20261107/"
     mv -- "$R/v1001/target-host.jsonc" "$R/v1001/stale-20261107/"
   )
   ```

5. Confirm the current source config and generated bundle match the offline
   plan in sections 1-2. Run the wrapper's logged dry-run for the target host.
   It repeats strict Debian-v2 config validation, resolves the offline plan,
   records the bundle SHA-256, checks the live Netcup API identity and pinned
   SSH host facts, checks disk bytes/root start, then invokes the Netcup wizard
   with image ID `128` and `--dry-run`. The output includes the generated
   provider payload and per-host `target-host.<hostname>.jsonc` path; no install
   POST is sent. Review `reinstall.log`, including its `EXIT=0`, and confirm
   `imageFlavourId == 128`, `diskName == vda`, hostname, server identity,
   customScript, branch and completion marker before the approved submit.
   `build-customscript` prints the branch/commit that customScript will fetch;
   compare its full commit with the reviewed merged commit and set
   `REVIEWED_MAIN_COMMIT` to that exact value. The dry-run prints
   `REMOTE_COMMIT=` and `BUNDLE_SHA256=`; record both and use that exact digest
   as `V1001_REVIEWED_BUNDLE_SHA256` for v1001 `--yes`. Record the r1002
   dry-run hash separately as `R1002_REVIEWED_BUNDLE_SHA256` and pass that to
   r1002 `--yes`. Obtain `ACCOUNT_SSH_KEY_ID` from
   the authenticated Netcup account and pass it explicitly; the wrapper does
   not prompt for account-key selection. Before submit, it runs the wizard's
   own `--dry-run`, parses the payload summary the wizard really prints and
   validates server, hostname, image, disk and SSH key IDs. The wizard redacts
   `customScript` in that summary, so the wrapper binds the script through the
   `[payload] customScript sha256=... len=...` line the wizard prints next to
   it (the script text before `{{CONTROLLER_SSH_PUBKEY}}` expansion) and
   through the `[payload] sent customScript (placeholders expanded) ...` line
   (the exact text the POST sends). The wrapper computes the expected sent
   text itself from the reviewed script and the public key derived from the
   retained identity (`ssh-keygen -y`, the wizard's own code path); a missing,
   duplicated or different digest is a refusal. The wizard child is started
   **without** the operator's `CONTROLLER_SSH_PUBKEY` (that variable would
   otherwise silently replace the identity's key), so a stale value in the shell
   has no effect.

   The wrapper reads the bundle once into `bundle.snapshot.json` (0600) in the
   run directory and uses exactly those bytes for validation, the reviewed
   digest, the dry-run and the `--yes` run; it re-hashes the snapshot before
   `--yes`. The `--yes` run's own printed digest lines are checked live and the
   wizard is terminated on a mismatch. Honest limit: the provider POST follows
   the printed summary by moments, so a mismatch in the `--yes` run is
   detection plus best-effort abort, not a pre-flight gate; the gate is the
   identical dry-run, the snapshot and the commit pin. If the `--yes` run ends
   without both digest lines, the wrapper says the installation may already
   have been submitted and points at the task state file.

   The commit is pinned in the customScript (`--repo-commit`), so the host runs
   the reviewed commit even if `main` moves before it boots; `--expect-commit`
   must equal both the bundle's pin and the current head of the branch at
   preflight time.

   ```bash
   (
     set -euo pipefail
     R=<controller-program-dir>
     cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/netcup
     /opt/mdt/venv/bin/python3 pre-reinstall.py v1001 \
       --config "$R/v1001/debian-install-v2-20261107.json" \
       --bundle "$R/v1001/debian-install-v2-customscript-20261107.json" \
       --disk-size 512GiB --root-start-sector 2500608 --root-number 3 \
       --repo-branch main --expect-commit "$REVIEWED_MAIN_COMMIT" \
       --ssh-key-id "$ACCOUNT_SSH_KEY_ID" \
       --run-dir "$R/v1001/reinstall-20261107" --dry-run
   )
   ```

6. Only after v1001's dry-run and maintenance approval, submit it with the same
   arguments and `--yes` instead of `--dry-run`. The wrapper repeats every
   check before it starts the wizard; `--yes` does not bypass validation. It
   records `START` and `EXIT=` in `reinstall.log`, writes
   `reinstall.log.EXIT`, retains the bundle hash, and the wizard writes the
   target payload under the requested per-host run directory. For r1002, run
   the equivalent dry-run and then submit only after v1001 passes all
   post-install checks and the operator approves continuing:

   ```bash
   (
     set -euo pipefail
     R=<controller-program-dir>
     cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/netcup
     /opt/mdt/venv/bin/python3 pre-reinstall.py v1001 \
       --config "$R/v1001/debian-install-v2-20261107.json" \
       --bundle "$R/v1001/debian-install-v2-customscript-20261107.json" \
       --disk-size 512GiB --root-start-sector 2500608 --root-number 3 \
       --repo-branch main --expect-commit "$REVIEWED_MAIN_COMMIT" \
       --expect-bundle-sha256 "$V1001_REVIEWED_BUNDLE_SHA256" \
       --ssh-key-id "$ACCOUNT_SSH_KEY_ID" \
       --run-dir "$R/v1001/reinstall-20261107" --yes
   )
   ```

   Immediately after the v1001 reinstall completes, obtain its new ED25519
   host-key fingerprint from the authenticated provider console or another
   verified provider channel. Run the pin updater before SSH post-install
   checks or any r1002 work:

   ```bash
   R=<controller-program-dir>
   cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/netcup
   ./update-host-pin.sh v1001 \
     --pin-file "$R/v1001/ssh_known_hosts-v1001-20261105" \
     --fingerprint "$V1001_PROVIDER_HOSTKEY_SHA256"
   ```

   Before r1002's dry-run, after v1001 has passed the post-install checks, run
   its evidence export and verify that host's manifest:

   ```bash
   (
     set -euo pipefail
     R=<controller-program-dir>
     cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/netcup
     /opt/mdt/venv/bin/python3 pre-reinstall-export.py r1002 --date 20261107
     (cd "$R/r1002/pre-reinstall-20261107" && sha256sum -c SHA256SUMS)
     find "$R/r1002/pre-reinstall-20261107" -type f -printf '%P\n' | sort
   )
   ```

   Then run this r1002 dry-run and inspect its log and payload:

   ```bash
   (
     set -euo pipefail
     R=<controller-program-dir>
     cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/netcup
     /opt/mdt/venv/bin/python3 pre-reinstall.py r1002 \
       --config "$R/r1002/debian-install-v2-20261107.json" \
       --bundle "$R/r1002/debian-install-v2-customscript-20261107.json" \
       --disk-size 512GiB --root-start-sector 2500608 --root-number 3 \
       --repo-branch main --expect-commit "$REVIEWED_MAIN_COMMIT" \
       --ssh-key-id "$ACCOUNT_SSH_KEY_ID" \
       --run-dir "$R/r1002/reinstall-20261107" --dry-run
   )
   ```

   After review and maintenance approval, repeat the same r1002 command with
   `--yes` instead of `--dry-run`. Never bypass the export, quarantine,
   identity, image, payload, offline-plan, hash, or log checks.

   ```bash
   (
     set -euo pipefail
     R=<controller-program-dir>
     cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/netcup
     /opt/mdt/venv/bin/python3 pre-reinstall.py r1002 \
       --config "$R/r1002/debian-install-v2-20261107.json" \
       --bundle "$R/r1002/debian-install-v2-customscript-20261107.json" \
       --disk-size 512GiB --root-start-sector 2500608 --root-number 3 \
       --repo-branch main --expect-commit "$REVIEWED_MAIN_COMMIT" \
       --expect-bundle-sha256 "$R1002_REVIEWED_BUNDLE_SHA256" \
       --ssh-key-id "$ACCOUNT_SSH_KEY_ID" \
       --run-dir "$R/r1002/reinstall-20261107" --yes
   )
   ```
   Immediately after the r1002 reinstall completes, obtain its new ED25519
   host-key fingerprint through the authenticated provider console or another
   verified provider channel. Run the repository-owned updater before SSH
   post-install checks. It atomically replaces only the reviewed host records;
   do not accept a first-seen key or disable strict host-key checking for a write:

   ```bash
   R=<controller-program-dir>
   cd /workspaces/vbpub/.worktrees/div2-data-area-v2-20261105/scripts/netcup
   ./update-host-pin.sh r1002 \
     --pin-file "$R/r1002/ssh_known_hosts-after-reinstall-20261104" \
     --fingerprint "$R1002_PROVIDER_HOSTKEY_SHA256"
   ```

Do not run `wipefs -a` by hand and do not bypass the installer's label,
geometry, mounted-device, active-swap, or signature guards. On any refusal,
stop, preserve stage state and logs, and fix the config or installer source
before retrying.

## 5. Post-install verification

Record evidence in `reports/div2-reinstall-<host>-RESULT.md` before provisioning
runner services:

1. Compare `lsblk -b -o NAME,SIZE,START,FSTYPE,LABEL,PARTLABEL /dev/vda` and
   `sfdisk -d /dev/vda` with the resolved table. Expected: root vda3 at
   sector 2500608, 160 GiB; `MDT_COW` vda4, about 326.81 GiB; `iobench-raw`
   vda5, 8 GiB; four 4 GiB swap partitions vda6-vda9 at the listed sectors.
   The order and partition numbers must match exactly.
2. Confirm installer state marks the partition table, root growth, io benchmark,
   io.cost, data filesystem, and swap steps appropriately. With
   `run_io_benchmark: true`, the benchmark must precede io.cost, and io.cost
   must not say “no benchmark result” after a successful measurement.
3. Confirm `findmnt /srv/cow` reports btrfs on vda4, and the generated fstab
   entry identifies that filesystem by UUID. Confirm four swap partitions are
   active. Verify `/` is ext4 and its filesystem uses the planned 160 GiB root.
4. Verify the raw partition is still unformatted and unmounted after
   benchmarking. Do not rerun the benchmark manually.
5. Only after those checks pass, synchronize the complete
   `scripts/netcup/runner/` directory from the reviewed commit to the host's
   runner-scripts directory, replacing the old carve-based copies as a set.
   Confirm the copied `lib.sh`, `provision-runner.sh`, and `cow-select.sh`
   match that commit. Then run the copied `provision-runner.sh`. Its output must
   show it selected `MDT_COW` by PARTLABEL and did not partition the disk.
   Confirm `runner.env` has `COW_MOUNT=/srv/cow`, `COW_PARENT_DISK=/dev/vda`,
   and no `COW_DEVICE` or `COW_SIZE_GIB`. Restore host setup, runner tools,
   containers, and gate service from the collected records; verify the host
   against the remote-gate board before re-enabling it.
6. Run the accepted runner/cowtree smoke job and one registered remote lane
   when the campaign board allows. Re-check host pinning, service health, and
   resource pressure before draining the second host.

## 6. Recovery

Before the provider reinstall begins, aborting leaves the host unchanged.
After it begins, there is no in-place rollback and no legacy-layout bundle to
fall back to. If the new installer refuses or the resulting table differs from
the resolved plan, stop before runner provisioning, retain the install state
and logs, and fix forward with a reviewed v2 config/source. The installer keeps
a partition transaction backup when a table write fails; do not manually edit
the table or wipe a partition to force progress. Reinstalling from the provider
is destructive and requires the same drain, collection, and exact-host guards.

If a swap-benchmark `end` refuses during recovery, retain its state file and
verify the partition GUID with the wrapper's low-level probe,
`blkid -p -s PART_ENTRY_UUID -o value /dev/vdaN`, against the recorded PARTUUID.
Do not use the retired patch's cache-capable `blkid -s PARTUUID` probe or run
`swapon -a`; recovery must restore only the selected device with its fstab row's
options through the committed wrapper. If that `end` still refuses, stop and
preserve the state and logs for review.

## 7. Decisions for the maintenance window

1. Confirm campaign completion/abandonment policy and the window for each
   host; v1001 remains first.
2. Confirm the v1001 ZFS evaluation export and whether the results are needed
   after reinstall.
3. Confirm that root 160 GiB and a CoW area of about 326.81 GiB are the desired
   fixed plan for both hosts.
4. Reconcile the live root start sector with saved evidence. The offline plan
   in this document uses 2500608; if the actual value is different, regenerate
   both plans as needed and re-review before attaching a bundle.
