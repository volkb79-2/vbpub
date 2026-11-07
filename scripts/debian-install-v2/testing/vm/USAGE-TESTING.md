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
`artifacts/vm-lab/` folder (the project's gitignored VM state folder) with mode
`0600`; inside the runner it is `/vm-lab/`. Confirm the ignore rule before
putting a private artifact there:

```bash
git check-ignore -v ../../artifacts/vm-lab/private-seed.iso
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
cat ../../artifacts/vm-lab/ssh/id_ed25519.pub
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
  --mem 2048 --smp 2 --disk-gb 20 --seed-iso /vm-lab/private-seed.iso
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
SEED_ISO=/vm-lab/private-seed.iso

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
wrapper. The seed path is in the runner's namespace (`/vm-lab/` for files kept
in `artifacts/vm-lab/`). Do not pass a
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

## Geekbench 6: TCG guest versus native host

The repository's [`geekbench_runner.py`](../../../../scripts/debian-install/geekbench_runner.py)
downloads and runs Geekbench 6. It does not currently support Geekbench 7. The
free Geekbench 6 executable attempts to upload its result automatically. This
procedure downloads/extracts first, then installs a default-drop guest output
policy before benchmarking. By default, only loopback and host-forwarded SSH
replies are allowed; if a public result is wanted, separately allowlist only
the resolved Geekbench Browser HTTPS endpoint. Do not use
`geekbench_runner.py --run` for this sequence, because that combines download
and execution before the firewall is installed.

### Image choice

Debian describes its [NoCloud image](https://cloud.debian.org/images/cloud/)
as a plain-VM image for local QEMU that omits cloud-init. That is a reasonable
future base for a CPU-only benchmark: it avoids cloud-init, though it is not
expected to materially change Geekbench's CPU workload once the VM is idle.
There is a Trixie first-boot wrinkle: Debian's cloud team says the serial
console asks for a root password before completing first boot. The current VM
harness is built around cloud-init: it creates the SSH `tester` account from
cloud-init data and `vmctl wait` waits for cloud-init status. Therefore this
procedure uses the existing `case-b` generic-cloud base. A NoCloud benchmark
would first need a deliberate account/SSH provisioning path and a wait mode
that does not require cloud-init.

Geekbench's system report identifies this VM as `QEMU TCG CPU version 2.5+`,
with model `QEMU Standard PC (Q35 + ICH9, 2009)` and four guest cores. That is
already explicit to score consumers; do not spoof the host EPYC CPU identity
in the guest. Publish the native comparison only when acceptable; describe it
as an EPYC host run pinned to four CPUs, not as a four-core EPYC. Geekbench
may still report the host's full CPU topology.

This is a manual performance experiment, not a registered gate. Run the
commands from a **root host shell in the physical checkout**, not from inside
the devcontainer. The standard `dev-gates.slice` is deliberately capped; for this
comparison, explicitly select the built-in `system.slice`, which the harness
verifies is loaded, has no CPU quota or memory ceiling, and exposes the host's
full CPU set. This verifies the slice itself: a host-wide Docker transient-
scope backstop can still limit the runner. Inspect the live runner cgroup;
`docker inspect HostConfig.Memory` alone does not show systemd-added scope
limits. The exception is host-only and does not change placement for normal VM
tests. A TCG workload can contend with production workloads, so use a quiet
maintenance window. Keep the VM at four vCPUs/4 GiB and compare with a native
run restricted to four host CPUs; record host load and CPU model because other
host activity still affects results.

First stage the Debian base and start the benchmark guest:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT/scripts/debian-install-v2/testing/vm"
export CGROUP_PARENT_DEV_GATES=system.slice
export BUILDX_BUILDER=mdt-managed  # use the builder configured on this host
export MDT_VM_SOURCE_DIR="$REPO_ROOT/scripts/debian-install"

./run-vm-harness.sh prepare-base case-b
./run-vm-harness.sh start geekbench-tcg --case case-b \
  --ssh-port 2230 --mem 4096 --smp 4 --disk-gb 20 --no-apt-cache
./run-vm-harness.sh wait geekbench-tcg --timeout-s 900
```

Run this from a root host shell in the physical checkout. If the checkout is
in a devcontainer, enter the host with `host-escape` and use the physical
checkout path reported by Docker's mount table; do not pass `/workspaces/...`
as a daemon-side bind source. The commands below do not depend on stdin; the
`host-escape` helper does not forward heredoc input.

After starting the runner, inspect its actual cgroup on the host. This host
uses a Docker-scope memory backstop even when `HostConfig.Memory=0`:

```bash
VM_RUNNER=debian-install-vm-harness-REPLACE_WITH_PRINTED_FINGERPRINT
RUNNER_PID="$(docker inspect --format '{{.State.Pid}}' "$VM_RUNNER")"
RUNNER_CGROUP="$(awk -F: '$1 == 0 {print $3}' "/proc/$RUNNER_PID/cgroup")"
printf 'runner cgroup: %s\n' "$RUNNER_CGROUP"
for knob in cpu.max memory.high memory.max cpuset.cpus.effective; do
  printf '%s=' "$knob"
  cat "/sys/fs/cgroup$RUNNER_CGROUP/$knob"
done
```

The observed Docker-scope `memory.max` on the 2026-10-14 host was 12 GiB;
the 4 GiB guest fit beneath it. This cap is a host safety backstop, not a
`system.slice` limit. See the [MDT host-setup backstop description](../../../../mdt-cockpit/host-setup/README.md#what-gets-installed).
Do not remove or override host governance to make a benchmark appear
unbounded.

Let any first-boot `apt`/`unattended-upgrade` activity finish before measuring,
then disable its timers in the disposable guest and confirm no package manager
is still running. Install the guest firewall tooling while networking remains
available, copy the repository runner into the guest, and download/extract
Geekbench without running it:

```bash
./run-vm-harness.sh ssh geekbench-tcg -- \
  'sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer; sudo systemctl stop apt-daily.service apt-daily-upgrade.service unattended-upgrades.service || true; sudo systemctl is-active apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service unattended-upgrades.service || true'
./run-vm-harness.sh ssh geekbench-tcg -- \
  'sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nftables rsync'
./run-vm-harness.sh copy geekbench-tcg \
  /source/geekbench_runner.py \
  /home/tester/geekbench_runner.py
./run-vm-harness.sh ssh geekbench-tcg -- \
  'cd /home/tester && python3 -c '\''import os; from geekbench_runner import GeekbenchRunner; d="/home/tester/geekbench6"; os.makedirs(d, exist_ok=True); r=GeekbenchRunner(version=6, work_dir=d); ok=r.download_and_extract(); print("Downloaded Geekbench", r.release_version, r.geekbench_path); raise SystemExit(0 if ok else 1)'\'''
```

Now install an output-chain policy that drops guest-originated traffic while
allowing loopback and replies from the guest SSH server to the runner's
host-forwarded SSH connections. Its final counter makes attempted egress
visible, including a blocked Geekbench Browser upload:

```bash
./run-vm-harness.sh ssh geekbench-tcg -- \
  'sudo nft add table inet geekbench_lockdown && sudo nft add chain inet geekbench_lockdown output "{ type filter hook output priority 0; policy drop; }" && sudo nft add rule inet geekbench_lockdown output oifname lo accept && sudo nft add rule inet geekbench_lockdown output ct direction reply tcp sport 22 accept && sudo nft add rule inet geekbench_lockdown output counter drop'

timeout 20s ./run-vm-harness.sh ssh geekbench-tcg -- \
  'sudo nft reset counters table inet geekbench_lockdown && python3 -c '\''import socket; s=socket.socket(); s.settimeout(3); rc=s.connect_ex(("1.1.1.1",443)); print("egress probe connect_ex=",rc); assert rc != 0'\'' && sudo nft list chain inet geekbench_lockdown output'
```

For this run, publication was approved. Before Geekbench finished, the guest
was given a static `/etc/hosts` mapping for `browser.geekbench.com`, and only
TCP/443 to its current resolved IPv4 addresses was inserted ahead of the
terminal drop rule. This lets the result upload without opening general
egress; DNS and all other destinations remain blocked. For a later run, resolve
the Browser addresses again on the host and use those values, not this
historical pair:

```bash
# Example addresses observed on 2026-10-14; resolve afresh before reuse.
./run-vm-harness.sh ssh geekbench-tcg -- \
  'sudo sh -c '\''printf "\n172.66.150.166 browser.geekbench.com\n104.20.34.162 browser.geekbench.com\n" >> /etc/hosts'\'' && sudo nft insert rule inet geekbench_lockdown output position 4 ip daddr 172.66.150.166 tcp dport 443 accept && sudo nft insert rule inet geekbench_lockdown output position 4 ip daddr 104.20.34.162 tcp dport 443 accept'
```

The repository runner's `run_benchmark()` has a fixed 15-minute subprocess
timeout; that is not a Geekbench runtime limit. Geekbench runs its
single-thread and multi-thread tests sequentially, and either a TCG or native
run could exceed 15 minutes. Execute the already-downloaded binary directly
with a longer outer timeout instead. A 45-minute attempt on 2026-10-14 ended
before final scores (outer `timeout` exit 124), so the procedure now allows
three hours. Once started, leave at least 15 minutes between progress checks;
the individual tests run sequentially and there is little value in repeatedly
querying an unchanged current stage. With the default drop-only firewall, the
free binary warns that it needs internet; our native offline attempt completed
its workloads but printed no score summary and exited 255 when upload/DNS
failed. If a score is needed, add the narrow Browser allowlist above and record
the public result URL, or use a Geekbench license with offline support. Keep
any claim URL in a private, git-ignored artifact
under `artifacts/vm-lab/`; it is not a repository document or a public link:

```bash
VM_LAB="$REPO_ROOT/scripts/debian-install-v2/artifacts/vm-lab"
mkdir -p "$VM_LAB"
timestamp_lines() {
  while IFS= read -r line || [[ -n "$line" ]]; do
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%S.%3NZ')" "$line"
  done
}
set -o pipefail
if ./run-vm-harness.sh ssh geekbench-tcg -- \
  'cd /home/tester/geekbench6 && GB_BIN="$(find . -type f -name geekbench6 -print -quit)" && test -n "$GB_BIN" && timeout 3h "$GB_BIN"' \
  2>&1 | timestamp_lines | tee "$VM_LAB/geekbench-tcg-console.log"; then
  BENCHMARK_EXIT=0
else
  PIPE_STATUS=("${PIPESTATUS[@]}")
  BENCHMARK_EXIT="${PIPE_STATUS[0]}"
fi
printf 'Geekbench/SSH exit: %s\n' "$BENCHMARK_EXIT"
test "$BENCHMARK_EXIT" -eq 0

./run-vm-harness.sh ssh geekbench-tcg -- \
  'sudo nft list chain inet geekbench_lockdown output'
```

For the native comparison, use the same repository runner API to download and
extract Geekbench on the host. The free Geekbench 6 binary automatically
uploads results and does not print a score summary when it cannot reach the
Browser; our offline attempt completed the workloads but exited 255 with no
scores. Run with ordinary host network access only when publication is
acceptable, or use a Geekbench license that supports offline operation. The
native result is public, so record its URL; keep any claim URL private. Capture
the version printed by each download and only compare the runs if they match.
Run the executable directly here too, with a generous timeout rather than the
runner API's fixed 15-minute limit:

```bash
NATIVE_DIR="$(mktemp -d /var/tmp/geekbench6-native.XXXXXX)"
python3 -c 'import os,sys; sys.path.insert(0,sys.argv[1]); from geekbench_runner import GeekbenchRunner; r=GeekbenchRunner(version=6, work_dir=sys.argv[2]); ok=r.download_and_extract(); print("Downloaded Geekbench", r.release_version, r.geekbench_path); raise SystemExit(0 if ok else 1)' \
  "$REPO_ROOT/scripts/debian-install" "$NATIVE_DIR"

NATIVE_CPUS="$(lscpu -p=CPU | awk -F, '!/^#/ && NF { if (n++) printf ","; printf "%s", $1; if (n == 4) exit }')"
test "$(printf '%s' "$NATIVE_CPUS" | tr -cd ',' | wc -c)" -eq 3 || {
  echo "Could not select four online host CPUs: $NATIVE_CPUS" >&2
  exit 1
}
echo "Native CPU affinity: $NATIVE_CPUS"
uptime
lscpu | grep -E 'Model name|^CPU\(s\):'
NATIVE_BIN="$(find "$NATIVE_DIR" -type f -name geekbench6 -print -quit)"
test -n "$NATIVE_BIN"

systemd-run --scope --slice=system.slice \
  --unit="geekbench-native-$$" \
  taskset -c "$NATIVE_CPUS" timeout 3h "$NATIVE_BIN"
```

`taskset` restricts execution to four host CPUs, matching the guest's four
vCPUs; it may not change the hardware topology Geekbench reports. In this run
the host result still reported eight processors, while TBB warned that its
worker pool was limited to three workers plus the caller. Record both facts.
The temporary `system.slice` scope avoids developer-gate CPU quotas. Geekbench
prints its individual tests and final score blocks after upload. Compare the
printed Geekbench versions and disclose background host load and any mismatch
between reported topology and CPU affinity. This procedure downloads the
newest Geekbench 6 version independently on each machine; if the CDN version
changes between downloads, repeat with a shared artifact before comparing
scores.

Record the Geekbench version, host CPU model, single-/multi-core scores,
guest and native CPU count/affinity, host load, and whether Geekbench uploaded
the result or reported it blocked. Treat a published result URL as a public
artifact. Do not describe this score ratio as a precise virtualization
overhead measurement: CPU model/topology reporting and background host work
also differ.

### Host-side resource profile and per-test limits

The host cgprofile daemon can sample the runner's Docker cgroup while the VM
is running. Start it against the idle, booted guest before launching Geekbench,
so the profile includes an idle baseline. Use the runner's full Docker ID (not
its truncated display prefix), `--scope container`, five-second sampling, and
DAMON off:

```bash
VM_RUNNER=debian-install-vm-harness-REPLACE_WITH_PRINTED_FINGERPRINT
RUNNER_ID="$(docker inspect --format '{{.Id}}' "$VM_RUNNER")"
PROFILE_META='{"lane":"manual-geekbench-tcg","project":"debian-install-v2","worktree":"manual","commit":null,"run_gate_revision":0,"kind":"command","expected":null}'
PROFILE_START="$(cgprofilectl start \
  --target "containerid:$RUNNER_ID" --scope container --damon off \
  --interval 5.0 --meta "$PROFILE_META" --json)"
PROFILE_SESSION="$(printf '%s' "$PROFILE_START" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session"])')"
printf 'cgprofile session: %s\n' "$PROFILE_SESSION"
```

After Geekbench and the guest have stopped, finish the session and render the
profile on the client (the report extra must be installed):

```bash
cgprofilectl stop "$PROFILE_SESSION" --json
PROFILE_ARTIFACTS="$REPO_ROOT/scripts/debian-install-v2/artifacts/vm-lab/cgprofile-$PROFILE_SESSION"
mkdir -p "$PROFILE_ARTIFACTS"
cgprofile report "$PROFILE_SESSION" --out "$PROFILE_ARTIFACTS" --md-only
```

`cgprofile report` exports the stored session and renders it locally, so the
daemon never renders. Rendering HTML can need more memory than a small
container offers; `--md-only` is the light form. Keep `report.md` beside its
`charts/` directory so the relative chart links work.

This profile describes the whole host-side runner cgroup—QEMU and its
supporting processes—not individual processes inside Debian. It can still give
useful phase-level estimates without guest instrumentation: timestamp the
Geekbench console output on the host, correlate each workload boundary with
the five-second cgroup samples, and compare that phase with the idle baseline.
This yields elapsed time and approximate host-charged memory deltas/peaks for
each named phase. It is not guest process RSS or an exact per-workload working
set: QEMU overhead is included, and guest pages can remain resident or be
reused after a workload finishes. Label the measurements as approximate.

The 2026-10-14 run began with a one-second profile before Geekbench, preserving
the idle-start baseline; leave that stream running rather than restarting it.
Its console output was not captured with per-line host timestamps, so the
profile is strongest for whole-run resource use and can only support
approximate phase alignment if workload boundaries are visible in saved
output. The benchmark command above timestamps each output line on the host
and stores the transcript in the ignored `artifacts/vm-lab/` folder for future runs.

As a reference point, Geekbench's [May 2024 benchmark-internals document](https://www.geekbench.com/doc/geekbench6-benchmark-internals.pdf)
publishes page-granular working-set sizes for each workload: Horizon Detection
is 803.5 MB single-core / 816.2 MB multi-core, while Object Remover is 207.5 /
208.6 MB. Geekbench defines working set as memory touched by a workload; these
figures are not peak RSS, a minimum VM RAM requirement, or measurements from
this 6.7.2 TCG run. The paper gives no per-workload wall-clock durations; the
host-side timestamped-console/cgroup correlation above can estimate those for
our VM. Geekbench says the characterization data were collected on a Dell
Precision 3460 with an Intel Core i5-12500 running Ubuntu 22.04, so treat them
as reference values rather than measurements of this host or guest. Geekbench
6.1 and later also inserts a [five-second gap between
workloads](https://www.geekbench.com/blog/2023/06/geekbench-61/) to reduce
thermal effects. The CPU suite has 16 workloads in each
of its single- and multi-core sections, so those gaps account for at most
31 × 5 seconds (about 2 minutes 35 seconds) of a full run. A multi-hour TCG
runtime is therefore overwhelmingly the workloads executing slowly, not
repeated sampling or cooldown; removing the pauses would save little and could
make later workload scores less consistent.

Geekbench 6's full CPU benchmark is the path to an official, comparable score.
The requested future fast mode is Geekbench-only: consider it only if Geekbench
officially documents a way to configure each workload's runtime. The reviewed
Geekbench 6 materials document no such control. Do not invent undocumented
flags, modify the benchmark binary, or substitute another benchmark. If a
vendor-supported shortened run becomes available, label it diagnostic rather
than official/comparable unless Geekbench explicitly says otherwise: changing
workload duration may change the measured metric, and the published composite
aggregates the full suite's workload scores.

At the 2026-10-14 08:01 UTC checkpoint, Docker attributed about 104% CPU to
the Geekbench runner and `docker top` showed QEMU at about 98%; the queried
`04a8eb8edde5` run-gate container did not exist. A separate Pterodactyl
`steamcmd:debian` container was also using about one CPU, so record host load
before the native comparison and treat it as a potential source of variance.
At 08:17 UTC, the Geekbench runner was at 105% CPU and 1.27 GiB cgroup memory;
the separate Pterodactyl container was at 104% CPU and 4.34 GiB. These are
host-side container totals, not guest process RSS.
At 08:33 UTC, Geekbench was still in its single-core sequence (HDR and Photo
Filter progress); QEMU was at 98.5% CPU, the runner cgroup at 1.65 GiB, and
host load average was 4.66 on eight available CPUs. The Pterodactyl container
remained a separate ~103% CPU consumer.
At 08:48 UTC, the single-core section had completed and the multi-core section
had begun; the first five multi-core workloads were running. No score had yet
been printed.
At 09:03 UTC, multi-core progress had advanced through Clang, Text Processing,
Asset Compression, Object Detection, Background Blur, and Horizon Detection;
scores were still pending.
At 09:18 UTC, Geekbench 6.7.2 completed and uploaded the TCG result: 65
single-core and 215 multi-core. The public result is
[Geekbench Browser result 19341722](https://browser.geekbench.com/v6/cpu/19341722);
the separate claim URL is kept only in the ignored private artifact
`artifacts/vm-lab/geekbench-tcg-result.txt`.

The host cgprofile session `s-20261014T074505Z-069f` ran from 07:45:05 to
09:19:21 at one-second sampling. Across the whole runner cgroup it recorded
2.35 average CPU cores, 3.98 peak cores, and no throttling or OOM. The memory
peak was 3.09 GB (about 2.88 GiB), up about 1.60 GiB from its 1.37 GB idle
baseline; peak cgroup swap was 4.84 GB (about 4.51 GiB). This includes QEMU
and host-side effects, not guest process RSS or a guest RAM requirement. The
report also recorded repeated zswap-refault events, so host memory pressure
may have affected this score. Its 56 automatically detected changepoints are
not labeled Geekbench workloads. Because this run's console lines were not
timestamped individually, do not assign per-workload elapsed time or memory
peaks from those changepoints. The ignored `artifacts/vm-lab/` folder
preserves the profiler summary, raw one-second samples, charts, and Markdown
report for later analysis.

The native comparison ran Geekbench 6.7.2 on the host with CPUs 0–3 affinity
from 09:43:09 to 09:49:12 UTC. It scored 1,667 single-core and 4,596
multi-core; the public result is
[Geekbench Browser result 19341831](https://browser.geekbench.com/v6/cpu/19341831),
and its claim URL is only in the ignored
`artifacts/vm-lab/geekbench-native-result.txt`. The host had load averages
2.35/3.53/4.91 at launch, and a separate Pterodactyl container used about one
CPU. Geekbench reported eight processors despite the four-CPU affinity; TBB
reported the worker pool limited to three workers plus the caller. Treat the
raw score ratio as a directional comparison, not isolated TCG overhead. An
offline attempt completed the workloads but failed at upload with exit 255
and printed no scores; the free edition needs connectivity to show its result.
The raw native/TCG score ratios are about 25.6× single-core and 21.4×
multi-core (TCG scored 3.9% and 4.7% of native, respectively); the different
CPU reporting, host background load, and memory pressure make these directional
figures rather than a controlled pure-overhead measurement.

Is a 20–30× TCG slowdown “common”? These runs establish that it is plausible
for this CPU-heavy Geekbench workload, but they do not establish a usual or
expected multiplier. QEMU documents TCG as dynamic guest-to-host instruction
translation and notes that system emulation uses a software MMU
([TCG internals](https://www.qemu.org/docs/master/devel/tcg.html)). As another
workload-specific point of reference, a [Linaro QEMU performance
investigation](https://www.linaro.org/blog/qemu-a-tale-of-performance-analysis/)
found Android boot under TCG about 8× slower for an x86-64 guest and 12× slower
for an AArch64 guest than its hardware-assisted reference. That is boot-time
measurement, not a Geekbench CPU score, and should not be generalized either.
The meaningful conclusion is that TCG can be dramatically slower than native
or hardware-accelerated execution, with the ratio depending on workload, guest
and host architectures, emulated CPU model, QEMU configuration, and competing
host load. Our measured 21–26× is an observed result for this setup, not a
portable TCG performance expectation.

A second, profile-only TCG run used Geekbench 6.7.2 with four guest vCPUs and
4 GiB guest RAM. Its timestamped console was correlated with the runner
cgroup's five-second `memory.current` samples. The table gives approximate
active duration (launch-to-next-launch time less Geekbench's five-second
inter-workload pause) and the highest sampled host-cgroup memory during that
workload. Durations are rounded to 0.1 minute. The final workload in each
section ends at the observed section boundary. The cgroup includes QEMU and
runner overhead; its memory is not guest process RSS or Geekbench's workload
working set. Resident pages can persist across phases, so treat the memory
figures as phase-correlated host usage, not isolated per-test requirements.

| Workload | Single: time / peak cgroup memory | Multi: time / peak cgroup memory |
|---|---:|---:|
| File Compression | 2.6 min / 1.69 GiB | 1.1 min / 1.30 GiB |
| Navigation | 1.2 min / 1.68 GiB | 0.4 min / 1.38 GiB |
| HTML5 Browser | 0.3 min / 1.54 GiB | 0.4 min / 1.90 GiB |
| PDF Renderer | 0.8 min / 1.52 GiB | 0.9 min / 2.06 GiB |
| Photo Library | 2.2 min / 1.52 GiB | 2.5 min / 1.98 GiB |
| Clang | 1.1 min / 1.50 GiB | 3.8 min / 1.47 GiB |
| Text Processing | 1.9 min / 1.57 GiB | 1.6 min / 1.20 GiB |
| Asset Compression | 7.6 min / 1.56 GiB | 1.9 min / 1.19 GiB |
| Object Detection | 2.5 min / 1.40 GiB | 3.3 min / 1.75 GiB |
| Background Blur | 9.4 min / 1.34 GiB | 2.7 min / 1.57 GiB |
| Horizon Detection | 1.3 min / 1.43 GiB | 0.4 min / 1.51 GiB |
| Object Remover | 11.4 min / 1.39 GiB | 3.2 min / 1.52 GiB |
| HDR | 3.1 min / 1.51 GiB | 0.9 min / 1.56 GiB |
| Photo Filter | 4.9 min / 1.73 GiB | 1.7 min / 2.24 GiB |
| Ray Tracer | 2.7 min / 1.65 GiB | 1.2 min / 2.45 GiB |
| Structure from Motion | 5.0 min / 1.29 GiB | 1.5 min / 2.45 GiB |

The profile session `s-20261014T101335Z-d097` ran 10:13:35–11:48:55 UTC;
its idle baseline was 1.21 GiB. Kernel cgroup accounting recorded an exact
whole-run peak of 2.52 GiB (the five-second samples peaked at 2.45 GiB),
including 2.12 GiB anonymous memory and 4.67 GiB peak cgroup swap. Swap is not
RAM. There was no throttling or OOM, but the host had memory pressure and
4,051,309 major faults / 4,738,217 anonymous workingset refaults during the
session; this is a noisy, host-pressure-affected measurement. The guest's
drop-by-default firewall blocked the result upload (4,060 packets / 278,988
bytes dropped), so this run produced no score and exited 255 at the expected
upload step. The complete private bundle—timestamped console, raw five-second
samples, charts, and report—is preserved under the ignored
`artifacts/vm-lab/cgprofile-s-20261014T101335Z-d097/` directory (the original
2026-10-14 run kept it under the since-removed `testing/vm/.vm/`).

One earlier egress-check `vmctl ssh` client remained waiting for over an hour
while using essentially no CPU. Put a host-side `timeout` around short
diagnostic SSH probes; a stuck control command is not Geekbench load.
At cleanup, the older idle runner container `c1a4bc9d9238` contained only
`docker-init`/`sleep infinity` and `apt-cacher-ng`, not QEMU; it was stopped
but not removed, preserving its writable layer. The separate Pterodactyl
container was left running as requested and still used about 109% CPU at the
final check.

After collecting results, remove only the disposable guest and its overlay.
The persistent harness container keeps its own writable layer (including the
downloaded base image and run logs); `--stop-daemon` removes that container,
so use it only when that state is disposable. To stop benchmark CPU use while
retaining the state, stop the exact runner container after destroying the VM:

```bash
./run-vm-harness.sh destroy geekbench-tcg
docker stop "$VM_RUNNER"
```

The previous 2026-10-14 test guests were removed in the same way with
`vmctl destroy` for each exact run name (`v1001-cloudinit-20261014`,
`v1001-cloudinit-retry-20261014`, and `v1001-cloudinit-20g-20261014`). Their
run overlays and logs were removed; the older runner and its immutable
`case-b-base.qcow2` were retained.

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
- [Host cgroup mount-flag rationale](../../../../mdt-cockpit/host-setup/CGROUP-NOTES.md#5-cgroup2-mount-options--not-a-unit-setting-at-all)
