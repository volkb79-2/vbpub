# QEMU VM usage, acceptance checks, and diagnosis

This is the operational companion to the [VM harness reference](README.md)
and its [design rationale](DESIGN.md). It covers the real-device test lane,
manual Debian cloud-image/seed-ISO acceptance tests, and investigation of guest
or host symptoms.

## What runs where

The VM runner is a persistent, unprivileged Docker container. QEMU runs inside
it with TCG software emulation and user-mode networking; the Debian guest has
its own kernel, block devices, swap, cloud-init state, and systemd. The guest
cannot remount the host's `/sys/fs/cgroup`. QEMU does consume host CPU and
qcow2 I/O, so the runner must stay in its configured cgroup tier.

`run-vm-harness.sh` is the supported entry point. It validates the caller's
governed cgroup configuration and runner setup. The lower-level
`host-escape -- docker exec ...` commands below are for operating an already
running runner or diagnosing a live VM. `host-escape` runs host-root commands
through a privileged helper; review each command as a host operation.

## Run the real-device test lane

From the repository root (or use the equivalent paths from a checkout):

```bash
cd scripts/debian-install-v2/testing/vm
./run-vm-tests.sh
```

The lane creates a disposable Case-B guest, runs real loop-device and swap
commit tests there, verifies that both required tests passed rather than
skipped, then removes that run's overlay. It needs the host-setup environment,
including `CGROUP_PARENT_DEV_GATES`, `CGROUP_PARENT_DEV_INTERACTIVE`, and
`BUILDX_BUILDER`. See [`../README.md`](../README.md) for the isolation contract
and gate details.

## Manually boot a cloud image or private seed ISO

Use the raw `case-b` Debian generic-cloud image for first-boot/cloud-init
tests. The image is checksum-verified when prepared; each run uses a disposable
copy-on-write overlay, so the base is not changed.

The wrapper can prepare a base, boot a guest, wait for SSH/cloud-init, and run
checks:

```bash
cd scripts/debian-install-v2/testing/vm
./run-vm-harness.sh prepare-base case-b
./run-vm-harness.sh start smoke --case case-b --disk-gb 20 --mem 2048 --smp 2
./run-vm-harness.sh wait smoke --timeout-s 900
./run-vm-harness.sh ssh smoke -- 'sudo cloud-init status --long'
./run-vm-harness.sh ssh smoke -- 'lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS; sudo swapon --show; df -h /'
./run-vm-harness.sh console smoke
```

For a NoCloud seed ISO, keep the credential-bearing artifact in the ignored
`testing/vm/.vm/` directory with mode `0600`. Confirm the ignore rule before
putting a private artifact there:

```bash
git check-ignore -v .vm/private-seed.iso
```

The ISO must provision the runner's `tester` account with the runner's SSH
public key and passwordless sudo; `vmctl wait` and `vmctl ssh` use that account.
The runner copies the ISO into the run directory and does not alter the source
ISO. A seed ISO is a cloud-init data disk, not a Debian installer image.

For a custom ISO, the runner-specific test key must be available before the
ISO is built. If this is a new runner, one disposable standard start creates
the key; destroy that throwaway run but keep the runner alive:

```bash
./run-vm-harness.sh prepare-base case-b # if this runner has no staged base yet
./run-vm-harness.sh start key-init --case case-b --no-apt-cache
./run-vm-harness.sh destroy key-init
VM_RUNNER=debian-install-vm-harness-REPLACE_WITH_PRINTED_FINGERPRINT
VM_FINGERPRINT=${VM_RUNNER##*-}
host-escape -- docker exec "$VM_RUNNER" cat \
  "/var/lib/mdt-debian-install-vm/$VM_FINGERPRINT/ssh/id_ed25519.pub"
```

Copy that public-key line into the temporary test seed as the `tester` user's
`ssh_authorized_keys` entry (not as a consumer's persistent root key):

```yaml
#cloud-config
users:
  - name: tester
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      # Replace this with the complete public-key line printed above.
      - "<runner public key>"
ssh_pwauth: false
```

```bash
./run-vm-harness.sh start iso-smoke --case case-b --ssh-port 2223 \
  --mem 2048 --smp 2 --disk-gb 20 --seed-iso ./.vm/private-seed.iso
./run-vm-harness.sh wait iso-smoke --timeout-s 900
./run-vm-harness.sh ssh iso-smoke -- 'sudo cloud-init status --long'
```

When a persistent runner already exists, its name is printed by
`./run-vm-harness.sh --daemon`. Direct `vmctl` operation through that runner is
useful when testing with `host-escape` or when wrapper setup is already
complete:

```bash
VM_RUNNER=debian-install-vm-harness-REPLACE_WITH_PRINTED_FINGERPRINT
VM_RUN=iso-smoke
SSH_PORT=2223
SEED_ISO=/work/vm/.vm/private-seed.iso

host-escape -- docker exec "$VM_RUNNER" /work/vm/vmctl status "$VM_RUN"
host-escape -- docker exec "$VM_RUNNER" /work/vm/vmctl start "$VM_RUN" \
  --case case-b --ssh-port "$SSH_PORT" --mem 2048 --smp 2 --disk-gb 20 \
  --seed-iso "$SEED_ISO"
host-escape -- docker exec "$VM_RUNNER" /work/vm/vmctl wait "$VM_RUN" --timeout-s 900
host-escape -- docker exec "$VM_RUNNER" /work/vm/vmctl ssh "$VM_RUN" -- \
  'sudo cloud-init status --long'
host-escape -- docker exec "$VM_RUNNER" /work/vm/vmctl console "$VM_RUN"
```

Replace the fingerprint placeholder with the actual name printed by the
wrapper. The seed path is in the runner's `/work/vm` namespace. Do not pass a
devcontainer path that is not mounted into the runner.

`vmctl wait --timeout-s` limits only the wait for SSH to become reachable.
After SSH responds, `vmctl wait` waits for `cloud-init status --wait`; that
part has no timeout. The command is not a proof of successful cloud-init:
`vmctl` tolerates a nonzero cloud-init status, so inspect it explicitly with
`cloud-init status --long`. Nor does `wait` prove that the installer's
stage-two service finished. Stage one may reboot the guest; if SSH drops,
check `status`/`console`, then run `wait` again after the VM is back.

For an installer acceptance check, collect non-secret summaries:

```bash
host-escape -- docker exec "$VM_RUNNER" /work/vm/vmctl ssh "$VM_RUN" -- \
  'sudo cloud-init status --long; sudo systemctl show vbpub-bootstrap-stage2.service -p Result -p ExecMainStatus -p ActiveState'
host-escape -- docker exec "$VM_RUNNER" /work/vm/vmctl ssh "$VM_RUN" -- \
  'sudo journalctl -b -u cloud-final.service -u vbpub-bootstrap-stage2.service --no-pager'
host-escape -- docker exec "$VM_RUNNER" /work/vm/vmctl ssh "$VM_RUN" -- \
  'lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS; sudo swapon --show; df -h /; free -h'
host-escape -- docker exec "$VM_RUNNER" /work/vm/vmctl ssh "$VM_RUN" -- \
  'sudo systemctl is-active docker; cat /sys/module/zswap/parameters/enabled'
```

Do not print the seed user-data, `/var/lib/cloud/instance/user-data.txt`,
installer config, or state JSON into a shared terminal or report: they can
contain notification credentials and SSH material. Prefer service status,
redacted installer logs, partition summaries, and the notification result.

`vmctl console` follows the serial log; Ctrl-C stops following, not the VM.
Use `vmctl stop` to stop QEMU while preserving that run's files. `vmctl destroy`
removes the run's overlay and run-local diagnostic files, so capture needed
evidence first. It does not delete the source seed ISO or immutable base image.

## Host checks when cgroup2 flags disappear

The host requires `nsdelegate`, `memory_recursiveprot`, and, on supported
kernels, `memory_hugetlb_accounting` on `/sys/fs/cgroup`. A runtime remount can
drop flags without changing systemd slice properties. The host's
`mdt-dev-governance-reconcile` service periodically reports and repairs this;
`host-escape` also runs `mdt doctor` after each host command and repairs missing
flags immediately.

To confirm which governed cgroup parent Docker configured for the persistent
runner:

```bash
host-escape -- docker inspect --format \
  'status={{.State.Status}} cgroup-parent={{.HostConfig.CgroupParent}}' \
  "$VM_RUNNER"
```

Inspect the mount and recent reconcile log together, preferably in one
`host-escape` call to avoid unnecessary helper-container launches:

```bash
host-escape -- bash -c 'findmnt -no SOURCE,FSTYPE,OPTIONS /sys/fs/cgroup; journalctl --utc -b -u mdt-dev-governance-reconcile.service --since "15 minutes ago" --no-pager'
```

The `findmnt` output is the command's observation before `host-escape`'s
trailing doctor runs. If flags are missing, the later `[mdt doctor] restored`
line is the repair, not evidence about which process removed them. Likewise,
`[mdt doctor]` output around a VM run is from the host helper, not the Debian
guest.

To correlate with Docker/containerd activity, query the host journal promptly:

```bash
host-escape -- bash -c 'journalctl --utc -b --since "15 minutes ago" --no-pager | rg "mdt doctor|mdt-dev-governance-reconcile|docker-|containerd|TaskDelete|qemu-system|vmctl"'
```

Docker's event stream is short-lived and does not reliably provide historical
command lines for removed `--rm` containers. A live event capture can help
establish timing, but it does not identify a process that remounted cgroup2;
also remember that `host-escape` itself starts a privileged Docker helper.
For exact attribution, tracing must already be active when the flags disappear
(for example, approved host audit/eBPF instrumentation). During the
2026-10-14 incident, audit tooling and retained audit records were unavailable,
so the exact remounting PID/command could not be recovered retrospectively.

### 2026-10-14 incident finding

For the VM boot on SSH port 2223, the runner logged QEMU start at 03:58:40 UTC.
At 03:58:40 the post-command host doctor found the cgroup2 flags missing and
restored them. The host reconcile service observed the flags missing again at
03:58:46 and restored them. Docker/containerd logs show short-lived container
creation/deletion around both windows, including activity at 03:58:44–45.

This rules out the Debian guest as the source: it has a separate kernel. The
strongest lead is the host-side privileged helper/container path used by
`host-escape` or another concurrent host operation. The `host-escape` source
itself documents privileged namespace escapes as the likely trigger. The
retained logs do not map the second set of transient container IDs to a caller,
so they do not prove whether this was our helper or the other concurrent Luna
agent. Treat that as a likely mechanism, not a named-process attribution.

The reconcile timer and doctor are self-healers, not the cause established by
these logs. To catch the next occurrence, record the exact UTC time and run
the compact mount/journal query above immediately; do not use guest cloud-init
status as a proxy for host cgroup state.

## Links

- [Harness commands and base-image details](README.md)
- [Why this uses QEMU and a separate guest kernel](DESIGN.md)
- [Test isolation contract](../README.md)
- [Host cgroup mount-flag rationale](../../../../modern-debian-tools-python-debug/host-setup/CGROUP-NOTES.md#5-cgroup2-mount-options--not-a-unit-setting-at-all)
