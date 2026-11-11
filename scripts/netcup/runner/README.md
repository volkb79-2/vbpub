# Runner host scripts

Generic, reviewed scripts that turn a freshly installed Debian host (see `install-host.py`) into a parallel test
runner: a `runner` account, a Btrfs CoW area, detached-job and checkout-sync helpers, the gate tools, the
`tester-base`/`tester-unified` images and a cockpit container that drives Run-Gate lanes. Runner-specific host settings
(identity, uid, disk layout and pinned versions) come from `runner.env`, which is gitignored. Copy
`runner.env.example` to `runner.env`, fill it in, and upload the whole
directory (scripts, `lib.sh`, `run-job`, `sync-from-devcontainer`, `runner.env`) to the host together.

The cockpit's service endpoints and access GIDs come from the host-published `/etc/host-endpoints.env` record, not
`runner.env`. The loader accepts only a regular, non-symlink file owned by root with no group or other write bit. It
parses comments, blank lines and the published `KEY=VALUE` alphabet as data; malformed and duplicate entries fail with
the file and line named. It exports only the runner's keys. `start-cockpit-runner.sh` refuses with the first missing
variable's name. Repair the record through its owner (host-setup for Docker API and BuildKit, `cgprofiled install` for
cgprofile, or `toposd install` for topos), then run
the corresponding host checker and retry; do not add socket paths or endpoint GIDs to `runner.env`. The cockpit
receives the record's endpoint values, including both `CGPROFILE_OBSERVER_SOCKET` and its directory, gets read-only
identity binds of the published socket directories, and joins the Docker, cgprofile-control, topos-observe and
mdt-buildkit groups by canonical numeric GID. It never joins the observer group; Run-Gate grants that GID to observer
lanes when it starts them. GIDs must be positive decimal values without leading zeros, and cockpit startup verifies
that `MDT_HOST_DOCKER_GID` matches the local `docker` group. The example pins Run-Gate 25.4.0 for the host endpoint
cutover. See the [runner endpoint rationale](../DESIGN-GUIDE.md#runner-cockpit-uses-the-host-endpoint-record)
and the [runner host setup steps](../CONSUMERS.md#runner-cockpit-endpoints).
Host-setup is mandatory: `start-cockpit-runner.sh` has no degraded mode. Without its
`/etc/host-endpoints.env`, Docker API and BuildKit facts, and cgprofile observer, the cockpit cannot be started
consistently; a raw-socket variant is unsupported because observer lanes and governed builds depend on those facts.

Provisioning normally precedes endpoint publication. As that explicit bootstrap exception, `provision-runner.sh`
adds the runner to the host's group named `docker`; it does not choose or hard-code a GID. Host-setup publishes the
GID of that same group, and provisioning checks the record if it already exists. Later cockpit startup checks the
published GID against the local group before using it.

Every script sources `lib.sh`, which loads `runner.env` (or `$RUNNER_ENV`) and defines `identity_check`: the host must
answer to the configured FQDN, static hostname and `/etc/vbpub/server-id` before anything is changed. Verify the
provider-side identity (API id, name, hostname, address, state) from the controller before each remote write; the
scripts can only check what the host itself reports.

## Cockpit restart policy

`start-cockpit-runner.sh` starts the cockpit with Docker restart policy `unless-stopped`. After a host reboot Docker
brings `mdt-cockpit-runner` back automatically, so the host-sat queue can launch a lane without an operator running
`docker start`. If the cockpit was deliberately stopped before the reboot, it stays stopped; `unless-stopped` does not
override an explicit `docker stop`.

Inspect the live policy with:

```bash
docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$COCKPIT_NAME"
```

The operator applied the live workaround on both test hosts on 2026-11-09 by updating the existing container. The
launcher now carries the same durable setting, so a replaced cockpit inherits it:

```bash
docker update --restart unless-stopped "$COCKPIT_NAME"
```

To revert the live workaround, restore Docker's default policy:

```bash
docker update --restart no "$COCKPIT_NAME"
```

The full reboot acceptance still requires a test-host reboot and a queue launch without a manual cockpit start.

| Script | Runs as | What it does |
| --- | --- | --- |
| `provision-runner.sh` | root, detached | writes `/etc/vbpub/server-id`, creates the runner account (uid `RUNNER_UID`, default 1003), formats and mounts the Btrfs CoW partition, installs `run-job` and `sync-from-devcontainer` and the runner scripts with `runner.env` into `~/runner-scripts` (the path the `run-job ... -- bash ~/runner-scripts/<script>` invocations use), creates the tools venv and `uv` |
| `install-augeaswrap.sh` | root on host | verifies the supplied Augeaswrap and cli-extended wheel hashes, installs them offline into `/opt/augeaswrap/<version>/venv`, and links `/usr/local/bin/augeaswrap` |
| `sync-from-devcontainer` | runner | imports a controller-made Git bundle into `~/checkouts/<commit>`; default calls update `~/src/vbpub-current`, while `--no-current-link` preserves it for T3 imports |
| `run-job` | runner | runs a named command detached with `~/jobs/<name>/job.log` and an `EXIT` marker |
| `setup-assay-venvs.sh`, `setup-gate-tools.sh` | runner, via `run-job` | Assay venvs (3.13, 3.14) and the tools venv (cmru, cli-extended, the published Run-Gate wheel) from the synced checkout |
| `build-tester-base.sh`, `build-tester-unified.sh` | runner, via `run-job` | build the two images inside the gates slice |
| `start-cockpit-runner.sh` | runner | creates the cockpit container (docker-run form of the host-folder devcontainer) and remaps its `vscode` user to the runner uid |
| `run-lane.sh` | runner, via `run-job` | runs one registered Run-Gate lane from a runner checkout inside the cockpit using `RUN_GATE_BIN` or the established cockpit default |
| `remote-gate.py` | controller | `remote-gate run <host\|auto> <worktree-of-any-git-repo> <lane>`: ships a committed worktree (vbpub, dstdns, any repo) to a runner host and runs one lane there (see CONSUMERS.md, "Remote gate"); host table in a gitignored JSON (`remote-gate-hosts.example.json`) |
| `setup-cockpit-run-gate.sh` | runner, via `run-job` | installs one pinned Run-Gate wheel into its own cockpit venv `~/.venvs/gate-tools<version>` (for repos that need a newer Run-Gate than the cockpit default, e.g. dstdns >= 25.0.1 through `remote-gate.py`) |
| `run-r2-campaign.sh` | runner, via `run-job` | supervises native Assay R2 in a clean persistent checkout; gives each `-s<N>` start its own campaign directory, counts completed candidates, supports `stop <campaign>`, clamps `--jobs` to the lane maximum, copies a fresh Assay verdict byte for byte, and writes `final-exit` plus a PASS/NOT_PASS summary |
| `run-cowtree-assay-smokes.sh` | runner, via `run-job` | cowtree and Assay workspace tests against the real CoW root |
| `setup-cockpit-tools.sh` | runner | installs the pinned Run-Gate wheel and the checkout's Assay into the cockpit user's `~/.venvs/gate-tools` (run once after `start-cockpit-runner.sh`; `run-lane.sh` needs it) |
| `run-jobs-window.sh`, `run-jobs-compare.sh` | runner, via `run-job` | native-R2 `--jobs 1/3/4/6/auto` comparison on fresh state: time-boxed throughput, or full wall time |

## Persistent T3 R2 campaigns

The runner can resume a native Assay R2 campaign from the persistent
checkouts/<commit> tree after a Run-Gate budget exit. Each supervision start
uses a unique -s<N> suffix for both its detached job and campaign directory;
the stable campaign base still owns one lock. Logs and terminal records live
under campaigns/<base>-s<N>/, so a re-supervision never overwrites the
previous start. The checkout's .assay state remains in place.

Import a T3 bundle without changing the runner's shared source symlink:

~~~bash
sync-from-devcontainer --no-current-link \
  ~/incoming/t3-<tool>-<tag>-<lane>.bundle t3/<tool>-<tag>
~~~

Run the campaign with the same start suffix on the job and campaign names.
run-job requires the separator before the command:

~~~bash
run-job t3-<tool>-<tag>-<lane>-s1 -- \
  bash ~/runner-scripts/run-r2-campaign.sh \
  t3-<tool>-<tag>-<lane>-s1 <commit> <project-dir> <lane>
~~~

Re-supervision increments s<N> and keeps the same unsuffixed campaign base.
The current terminal result is campaigns/<base>-s<N>/final-exit; exit 4
means the supervisor stopped after the configured attempt cap or two attempts
without a completed candidate. campaign-summary.json records NOT_PASS,
reason=budget, and stop_reason=max-attempts or stalled. Completed candidate
events are counted from Assay event=candidate records; replayed baseline events do
not reset the stall counter.

Stop an active or orphaned campaign with the supported command:

~~~bash
bash ~/runner-scripts/run-r2-campaign.sh stop t3-<tool>-<tag>-<lane>
~~~

The stop command signals the supervisor and tracked lane client and sampler,
removes lane containers carrying that start's exact Run-Gate job and campaign
labels, and uses each attempt's recorded exact container name when labels are
unavailable. It waits until the campaign lock is free. Run-Gate 25.2.0 or newer
is required for label-based stop. Older versions use the legacy exact name
pattern `run-gate-<repo>-<lane>-<pid>-<epoch>`; if neither labels nor a
recorded exact name identifies a container, stop fails with a manual
exact-name instruction. A persistent
checkout must be clean before a campaign starts. Commit any lane-config change
first; Run-Gate has no budget override, so a shorter test budget requires a
committed run-gate.toml change in the imported checkout.

run-lane.sh accepts RUN_GATE_BIN as an absolute path inside the cockpit.
When omitted, it keeps the established cockpit default. Hosts with cgprofile
3.0.0 should point RUN_GATE_BIN to Run-Gate 25.4.x; set it in the runner's
private runner.env for observer lanes. RUNNER_ENV can point to the runner
environment file when a copied script is kept beside lib.sh but outside the
installed runner-scripts directory. A copied supervisor needs both lib.sh and
run-lane.sh beside it.

## One uid for everything

The `tester-unified` lane user is uid 1003. The runner account (which owns the checkouts and the CoW root) and the
cockpit user (which invokes `run-gate`) must be the same uid, otherwise the `worktree` lane fails as `dubious
ownership`, `Permission denied` under `.run-gate`, or `MDT_COW_ROOT must be owned by the non-root caller uid`
(Run-Gate RG-150, backlog NC-04, mdt-cockpit MDT-052). `provision-runner.sh` creates the account with `RUNNER_UID` and
refuses a different existing uid unless `RUNNER_UID_ALLOW_CHANGE=1`; `start-cockpit-runner.sh` aligns the cockpit user.

## Host profile defaults

HP-D10, accepted 2026-11-10: both test hosts target Run-Gate 25.4.1 as the
default after old campaign and resume state has been collected. Keep the
existing pins and venvs until that collection is complete; then retain the
prior venvs for rollback. This records the accepted target, not a claim that
either host has switched. No host hosts-file was changed for this note.

## Order

1. **P1 — reinstall the provider host** from the reviewed Debian-install-v2 bundle. The controller checks the target
   identity before submitting the install.
2. **P2 — pin the new host key and check the installed layout.** Verify the provider identity and disk layout before
   making further host changes.
3. **P3 — provision the runner.** Run `provision-runner.sh` as a detached root job. After provisioning alone, the host
   can run the remote `selftest` lane; the cockpit, CoW smokes and cgprofile-observed lanes are not ready.
4. **P4 — sync the reviewed source.** Bundle the reviewed commit and required `assay-v*` and `ciu-v*` tags, upload it
   to `~/incoming`, then run `sync-from-devcontainer`. Host-setup and runner venv setup use this checkout.
5. **P5 then P6 — install Augeaswrap, then run host-setup.** Use `install-augeaswrap.sh` with the verified local
   wheels before running `mdt-cockpit/host-setup/install.sh` and its wizard. Set `PYTHONDONTWRITEBYTECODE=1` for
   host-setup (NC-10). Host-setup publishes the endpoint record and loads the slices and Docker facts needed by later
   steps.
6. **P7–P11 — install services and gate prerequisites.** Install cgprofile and topos, run endpoint and host checks,
   create the runner venvs from the synced checkout, then build `tester-base` and `tester-unified`. Start each
   detached job only after the previous `EXIT` is 0. Host-setup must precede the tester images; the cockpit itself
   does not require the `tester-unified` image.
7. **P12–P15 — start, register and accept.** Start the cockpit and install cockpit Run-Gate, register controller
   pins and repository profiles, run the selftest/observer/CoW/cgroup/workspace/reboot acceptance checks, then enable
   the queue and require its first PASS. Run one gate at a time unless the host is deliberately overcommitted.

`build-tester-unified.sh` sets `BUILDX_BUILDER=default MDT_BUILDX_PASSTHROUGH=1`: the managed rootless BuildKit cannot see the locally
loaded `tester-base:local` (mdt-cockpit MDT-049).

## Install augeaswrap for host-setup

`host-setup` requires `augeaswrap` on root's `PATH`. Install the release
wheels from a directory already copied to the host; this script does not
download packages. The Augeaswrap wheel depends on `cli-extended`, so provide
and verify both wheels and their release SHA-256 sidecars before running the
installer. The expected hashes are explicit inputs to the installer.

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

The dry run verifies both wheel files and shows the versioned venv, offline
pip command and root-PATH link without writing. The apply run uses
`python3 -m venv`, then `pip install --no-index --no-deps`; repeat runs with
the same wheel hashes reuse the venv and repair the symlink if needed. Review
the hashes against the release record before passing them in; a sidecar copied
beside a wheel is only useful when its provenance is trusted.

## Host without mdt host-setup

This section records the cut-over path observed on the reinstalled `v1001` runner host on 2026-11-08. It is documentation
for the degraded, manual path, not a claim that the checked-in launcher already implements it. The reviewed
`start-cockpit-runner.sh` still requires the complete host endpoint record, and the cgprofile-aware and no-host-setup
cockpit launchers used below remain outside this repository under `v1001/runner-scripts-local/` (backlog NC-21).
Moving that path into supported scripts and re-proving it on a fresh host are still open.

The observed path used the existing runner provisioning and image build sequence, then added these host prerequisites:

1. installed systemd slice units accepted by assay's `cgroup-parent.sh`;
2. the exact pinned PostgreSQL image needed by the SQL qualification lane;
3. cgprofile 3.0.0, its groups, sockets and six-key endpoint record;
4. Run-Gate 25.4.0 in an additive cockpit venv;
5. a recreated cockpit with cgprofile endpoint forwarding, the control GID, the Docker GID, and the explicit
   `/workspaces/vbpub` workspace bind.

Pause the host-sat queue and let its current lane finish before any cockpit recreation or host change. The pause flag
and drain procedure are in [Host-sat queue pause flags](#host-sat-queue-pause-flags). Keep the pre-change container
inspection, generated scripts and wheel directory until the cut-over is accepted or rolled back.

### 1. Install the two slice marker units

`assay/tools/cgroup-parent.sh` requires both `dev-interactive.slice` and `dev-gates.slice` to be loaded from installed
unit fragments. It also requires positive CPU and I/O weights and at least one non-default resource control on the
gates slice, and, for the B105 self-qualification path, finite RAM headroom of at least `2147483648` bytes. The
weights are read as effective unit properties; the checker does not test whether they were explicitly assigned in
the unit file. A host reinstalled without mdt host-setup had only runtime cgroups, no `FragmentPath`, so the lane
failed with the excerpt preserved in the handoff:

```text
cgroup-parent: Docker-host unit "dev-interactive.slice" is not a loaded installed slice (FragmentPath=)
```

The checker's current diagnostic also formats `LoadState`, but the handoff excerpt does not preserve its runtime
value.

The source records the required directives but does not preserve the exact `Description=` text, comments, staging
commands, or process-count checks. The following are minimal proposed unit bodies that preserve the recorded
directives; the exact observed file wording and installation transcript are `to be confirmed`.

`/etc/systemd/system/dev-interactive.slice`

```ini
[Unit]
Description=Interactive devcontainer tier

[Slice]
```

`/etc/systemd/system/dev-gates.slice`

```ini
[Unit]
Description=Gate and lane container tier

[Slice]
# assay cgroup-parent.sh requires positive effective weights and a non-default control;
# IOWeight=101 is the non-default control in this observed unit.
CPUWeight=100
IOWeight=101
# Recorded value for the cut-over host. Derive the value for another host from MemTotal.
MemoryMax=6G
```

The first file is a marker: it deliberately carries no resource controls. The second supplies positive effective
`CPUWeight` and `IOWeight` properties, one non-default value (`IOWeight=101`), and finite memory headroom, satisfying
the controls assay checks. The source does not record an explanation of the observed host's I/O scheduler behavior.

`MemoryMax=6G` is the value recorded on the cut-over host, not a portable default. Do **not** copy it blindly to a
different host. Backlog NC-25 requires deriving the finite value from `MemTotal` with at least a 2-GiB reserve, but
the repository does not yet provide an implementation. The portable derivation is `to be confirmed`; read `MemTotal`
from the target host and size it for that host before installing the unit.

The following validation and installation sequence is proposed; the source does not preserve the exact observed
argv or file mode. Run it as root:

```bash
install -d -m 0755 /root/slicefix-20261108
# Write the two exact [Unit]/[Slice] bodies shown above to:
#   /root/slicefix-20261108/dev-interactive.slice
#   /root/slicefix-20261108/dev-gates.slice
systemd-analyze verify \
  /root/slicefix-20261108/dev-interactive.slice \
  /root/slicefix-20261108/dev-gates.slice
install -m 0644 -o root -g root \
  /root/slicefix-20261108/dev-interactive.slice \
  /etc/systemd/system/dev-interactive.slice
install -m 0644 -o root -g root \
  /root/slicefix-20261108/dev-gates.slice \
  /etc/systemd/system/dev-gates.slice
systemctl daemon-reload
```

The source records that `dev-gates.slice` was restarted while empty so the newly configured memory limit was applied;
it does not record the exact emptiness checks. Before restarting, verify that no processes or child cgroups exist and
stop if either is present. This emptiness check is proposed rather than an observed transcript:

```bash
gates=/sys/fs/cgroup/dev.slice/dev-gates.slice
processes="$(<"$gates/cgroup.procs")"
child_cgroup="$(find "$gates" -mindepth 1 -maxdepth 1 -type d -print -quit)"
if [[ -n "$processes" || -n "$child_cgroup" ]]; then
  echo "dev-gates.slice is not empty; do not restart it yet" >&2
else
  systemctl restart dev-gates.slice
fi
```

Verify the installed identity and controls:

```bash
for unit in dev-interactive.slice dev-gates.slice; do
  systemctl show "$unit" \
    -p Id,LoadState,ActiveState,FragmentPath,DropInPaths,Transient,MemoryMax,CPUWeight,IOWeight
done
```

Expected evidence is `LoadState=loaded`, a real `FragmentPath` under `/etc/systemd/system/`, and
`dev-gates.slice` showing `CPUWeight=100`, `IOWeight=101`, and a finite `MemoryMax`. Verify the actual cgroup value and
the assay contract as used by the lane:

```bash
cat /sys/fs/cgroup/dev.slice/dev-gates.slice/memory.max
assay/tools/cgroup-parent.sh --memory-reserve-bytes 2147483648
```

The source records that a later full `tester-unified` attempt ran after the units were installed and failed tests; it
does not record the results of these two standalone invocations, which are `to be confirmed`. The rollback below
removes only the two files added by this workaround. Retain the units while any cockpit that uses
`dev-interactive.slice` is running:

```bash
# Stop and remove that cockpit first, and confirm the slices have no processes or child cgroups.
rm -f /etc/systemd/system/dev-interactive.slice \
      /etc/systemd/system/dev-gates.slice
systemctl daemon-reload
```

The source preserved the gates file that existed before the controller added `MemoryMax=6G` at
`/root/slicefix-20261108/dev-gates.slice.before-memmax` on the observed host. It is an evidence backup, not an
automatic restore command.

### 2. Make the pinned PostgreSQL image locally present

Assay's SQL qualification checks with `docker image inspect "$IMAGE"` and, when absent, reports the exact immutable
reference to pull. It never pulls by itself. The exact qualification image is:

```text
postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2
```

Prepare and verify it:

```bash
image='postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
docker image inspect "$image" >/dev/null
```

The observed host initially had no PostgreSQL image. Pull the pinned reference when it is absent:

```bash
docker pull \
  postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2
```

The same string is the constant in `assay/gate/python/qualify_sql.py`; `docker run` then uses `--pull=never`, so a
missing or differently resolved image fails loudly rather than silently fetching another version.

After every lane that needs this image has finished, remove the exact immutable reference and verify that it is absent:

```bash
docker rmi 'postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
docker image inspect 'postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
```

The inspect command must return nonzero after removal. The pull and inspect paths above use this same reference.

### 3. Install cgprofile 3.0.0 and publish the endpoint record

The observed cut-over acquired and hash-checked these release wheels from their GitHub releases:

| Wheel | Version | SHA-256 |
| --- | --- | --- |
| `cgprofile-3.0.0-py3-none-any.whl` | 3.0.0 | `4dd5f1855be5a3265a994c187d4046d2e6e7cebd7b335b303112d524eee45711` |
| `cli_extended-0.5.3-py3-none-any.whl` | 0.5.3 | `5a88979592ec5d3c7bebae57787d1674d88d72413c764f2dcde82f672fd267d4` |
| `augeaswrap-0.1.0-py3-none-any.whl` | 0.1.0 | `461d45bdc0480d58a55dc952baec5e2c029ffa2bfab0c28ef332d74cf20d8714` |

Put all three in one root-owned directory, verify their sidecars or recorded hashes, and run the installer through
zipimport from the wheels themselves. Use the same command first with `--dry-run --json`, inspect the plan, then apply
it. The observed non-interactive inputs were:

```bash
wheel_dir=/root/cgp-cutover-20261108/wheels
cgprofile_wheel="$wheel_dir/cgprofile-3.0.0-py3-none-any.whl"
cli_extended_wheel="$wheel_dir/cli_extended-0.5.3-py3-none-any.whl"
augeaswrap_wheel="$wheel_dir/augeaswrap-0.1.0-py3-none-any.whl"

PYTHONPATH="$cgprofile_wheel:$cli_extended_wheel:$augeaswrap_wheel" \
python3 -m cgprofile.cli.cgprofiled install \
  --wheel '3.0.0@sha256:4dd5f1855be5a3265a994c187d4046d2e6e7cebd7b335b303112d524eee45711' \
  --cli-extended-wheel '0.5.3@sha256:5a88979592ec5d3c7bebae57787d1674d88d72413c764f2dcde82f672fd267d4' \
  --augeaswrap-wheel '0.1.0@sha256:461d45bdc0480d58a55dc952baec5e2c029ffa2bfab0c28ef332d74cf20d8714' \
  --wheel-source "$wheel_dir" \
  --control-group cgprofile-control \
  --observer-group cgprofile-observer \
  --add-user control=runner \
  --enable --yes \
  --dry-run --json
```

After reviewing the plan, repeat that exact command without `--dry-run --json` to apply it. It creates the control and
observer groups, socket units and daemon, installs the immutable versioned venv, and publishes
`/etc/host-endpoints.d/cgprofile.env`, aggregated into `/etc/host-endpoints.env`. On the observed host this produced:

```text
CGPROFILE_CONTROL_SOCKET
CGPROFILE_CONTROL_SOCKET_DIR
CGPROFILE_CONTROL_GID
CGPROFILE_OBSERVER_SOCKET
CGPROFILE_OBSERVER_SOCKET_DIR
CGPROFILE_OBSERVER_GID
```

The observed values were control GID 986 (runner is a control member) and observer GID 985 (no users; Run-Gate grants
that GID only to observer lane containers). The runner must not be added to the observer group.

Verify the service, record and group membership:

```bash
systemctl is-active \
  cgprofile-control.socket cgprofile-observer.socket cgprofiled.service
/opt/cgprofile/current/venv/bin/cgprofiled doctor --json
grep -E '^CGPROFILE_[A-Z_]+=' /etc/host-endpoints.env
getent group cgprofile-control cgprofile-observer
id -nG runner
```

The observed result was all three units active and enabled, six endpoint keys, and
`cgprofiled doctor --json`: 12 OK, 0 warn, 0 fail. `cgprofilectl doctor` additionally had one non-blocking
`report-extra` warning because matplotlib was absent; the daemon and gates did not need it.

Rollback is below, after the cockpit has been returned to a launcher and shape that do not use cgprofile.

### 4. Install Run-Gate 25.4.0 beside the cockpit default

The cgprofile endpoint layout is implemented by Run-Gate 25.4.0. The observed cockpit default was 25.0.2, which
hard-coded the old socket layout and ignored the `CGPROFILE_*` environment. The supported installer adds the new
version in its own venv and leaves the default in place:

```bash
run-job setup-rg-25.4.0 -- \
  bash ~/runner-scripts/setup-cockpit-run-gate.sh \
    25.4.0 '<wheel-sha256-from-run-gate-v25.4.0-release>'
```

The source report preserved only the abbreviated wheel hash `d417e38b...f383`; the full 64-character value is
`to be confirmed` from the `run-gate-v25.4.0` release `.sha256` asset. Do not substitute a guessed hash.

Verify the additive venv without changing the default:

```bash
docker exec -u vscode "$COCKPIT_NAME" bash -lc \
  '"$HOME/.venvs/gate-tools2540/bin/run-gate" --version'
docker exec -u vscode "$COCKPIT_NAME" bash -lc \
  'command -v run-gate; run-gate --version'
```

Expected results are 25.4.0 from `gate-tools2540` and the prior default still resolvable. A lane selects the new venv
through its private hosts-file `repos.<repo>.run_gate_bins` entry. The observed cut-over used a private release hosts
file and did not change the queue's hosts file (NC-23). Rollback is to re-pin that hosts-file entry to the prior
Run-Gate, or use the default. After every host profile has stopped referencing this venv, remove only that additive
cockpit environment with the command in rollback step 4 below; it does not touch the cockpit's default Run-Gate.

### 5. Recreate the cockpit with cgprofile and the workspace bind

The cut-over used three operator-run local variants copied to the host's `~runner/runner-scripts/`:

| Script | Purpose |
| --- | --- |
| `start-cockpit-runner-nohostsetup.sh` | Pre-cgprofile cockpit shape with the raw Docker socket bind and no endpoint mounts. |
| `start-cockpit-runner-cgprofile.sh` | Adds the six cgprofile environment keys, control/observer directory binds and control GID. |
| `start-cockpit-runner-cgprofile-ws.sh` | Adds all of the above plus the required nested `/workspaces/vbpub` bind. |

These files are not under `scripts/netcup/runner/` yet. Copy the reviewed local variants before this step; the exact
controller copies are under `v1001/runner-scripts-local/`.

The cgprofile-and-workspace variant must make these exact cockpit additions:

| Addition | Exact observed form |
| --- | --- |
| Workspace bind | `--mount type=bind,source="$RUNNER_HOME/checkouts/vbpub",target=/workspaces/vbpub,consistency=cached` |
| Control directory bind | `--mount type=bind,source="${EP[CGPROFILE_CONTROL_SOCKET_DIR]}",target="${EP[CGPROFILE_CONTROL_SOCKET_DIR]}",readonly` |
| Observer directory bind | `--mount type=bind,source="${EP[CGPROFILE_OBSERVER_SOCKET_DIR]}",target="${EP[CGPROFILE_OBSERVER_SOCKET_DIR]}",readonly` |
| Control group | `--group-add "${EP[CGPROFILE_CONTROL_GID]}"` |
| Docker group | `--group-add "$docker_gid"` |
| Docker GID env | `-e "MDT_HOST_DOCKER_GID=$docker_gid"` |
| Endpoint env | One `-e "$key=${EP[$key]}"` for each of the six required `CGPROFILE_*` keys |

The script sets `docker_gid` and parses the record as data with this exact command:

```bash
docker_gid=$(getent group docker | cut -d: -f3)
```

It refuses `/etc/host-endpoints.env` unless it is a regular non-symlink root-owned file with no group/other write
bits. It accepts only lines matching
`^(CGPROFILE_[A-Z_]+)=([A-Za-z0-9_./:@+-]+)$`, requires all six keys, requires both GIDs to be positive decimal
values, and passes the values with `-e`. It does not source the record and does not type values into the launcher.

The extra workspace bind is needed because `assay/tools/self-qualification-container.sh` requires `findmnt` for the
selected worktree to report target exactly `/workspaces/vbpub` with an absolute FSROOT other than `/`. The outer
`checkouts -> /workspaces` bind alone made the target `/workspaces` and the lane refused:

```text
self-qualification-container: the selected worktree is not inside the declared /workspaces/vbpub bind
```

With the queue paused and its in-flight lane finished, recreate the cockpit by running the reviewed local variant as
the runner/root path used by the source:

```bash
bash /home/runner/runner-scripts/start-cockpit-runner-cgprofile-ws.sh
```

Do not run the reviewed repository `start-cockpit-runner.sh` for this degraded path: it requires the additional
host-setup Docker API, BuildKit and topos record keys that this host does not publish (NC-18/NC-21).

Verify the six endpoint values and the Docker GID:

```bash
docker_gid=$(getent group docker | cut -d: -f3)
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$COCKPIT_NAME" |
  grep -E '^(CGPROFILE_[A-Z_]+=|MDT_HOST_DOCKER_GID=)'
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$COCKPIT_NAME" |
  grep -F "MDT_HOST_DOCKER_GID=$docker_gid"
```

Verify the control GID was added and the observer GID was not:

```bash
docker inspect -f '{{json .HostConfig.GroupAdd}}' "$COCKPIT_NAME"
docker exec -u vscode "$COCKPIT_NAME" id
```

The observed process groups contained the Docker GID and cgprofile-control GID but not cgprofile-observer. Run-Gate
stats the observer socket and gives only the observer lane `--group-add <observer-gid>`.

Verify the endpoint-directory binds are read-only identity mounts:

```bash
docker inspect -f \
  '{{range .Mounts}}{{println .Source "->" .Destination "rw=" .RW}}{{end}}' \
  "$COCKPIT_NAME"
```

Verify the workspace target and host FSROOT:

```bash
docker exec -u vscode "$COCKPIT_NAME" \
  findmnt --target /workspaces/vbpub --noheadings --output TARGET,FSROOT
```

The observed output reported target `/workspaces/vbpub` and FSROOT `/home/runner/checkouts/vbpub`.

Run-Gate 25.3 and later require `RUN_GATE_HOST_DOCKER_GID` or `MDT_HOST_DOCKER_GID` as a strict decimal value before
cockpit observer lanes can pass. Run-Gate intentionally does not infer it from the Docker socket's owning group. The
cockpit launcher resolves the host `docker` group on the host and exports:

```bash
docker_gid=$(getent group docker | cut -d: -f3)
# In the cockpit docker-run argv:
--group-add "$docker_gid"
-e "MDT_HOST_DOCKER_GID=$docker_gid"
```

The observed values were Docker GID 990, cgprofile-control GID 986, and cgprofile-observer GID 985. These are examples
from that host, not constants for another host.

### 6. Run acceptance checks

Use the project's registered lanes for test execution. The observed cut-over accepted the path with these checks:

| Check | Expected result |
| --- | --- |
| `assay/tools/cgroup-parent.sh --memory-reserve-bytes 2147483648` inside the cockpit | Emits `dev-gates.slice` and reports RAM admission OK. |
| `findmnt --target /workspaces/vbpub --noheadings --output TARGET,FSROOT` inside the cockpit | Target `/workspaces/vbpub`; absolute host FSROOT other than `/`. |
| `docker inspect` cockpit environment | Six `CGPROFILE_*` keys and `MDT_HOST_DOCKER_GID=<host docker gid>`. |
| `docker inspect` cockpit groups | Docker and control GIDs present; observer GID absent. |
| `docker image inspect postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2` | Exit 0. |
| Run-Gate 25.4.0 preflight through the private hosts-file pin | Host Docker GID, control group, observer directory/socket checks pass. |
| Observer lane smoke | Lane receives the observer directory mount and its numeric GID; assay reports observed cgroup measurements. |

The observed observer smoke produced the lane arguments
`--mount type=bind,source=/run/cgprofile/observer,target=/run/cgprofile/observer,readonly`,
`--group-add 985`, and
`-e ASSAY_RESOURCE_OBSERVER_SOCKET=/run/cgprofile/observer/cgprofile.sock`; Assay baseline passed and candidate
progress carried cgroup measurements from cgprofile. A later self-qualification attempt exercised the actual release
gate. A complete fresh-host replay using only checked-in files has not been performed and is `to be confirmed`.

After the host is idle and accepted, remove the per-host pause flag so the queue loop can relaunch.

### Rollback

Rollback in dependency order so an active cockpit does not retain the new mounts after their publisher is removed.
Pause the queue and drain before each cockpit recreation:

```bash
CONTROLLER_HOME=${CONTROLLER_HOME:-"$HOME/controller"}
[[ ! -f $CONTROLLER_HOME/controller.local.env ]] || . "$CONTROLLER_HOME/controller.local.env"
queue=${CONTROLLER_QUEUE:?set CONTROLLER_QUEUE in controller.local.env}
host=v1001
touch "$CONTROLLER_HOME/host-sat-queue.disabled.$host"
bash "$queue" status
# Wait until status reports no in-flight lane for "$host".
```

1. Replace the cgprofile-and-workspace cockpit with the pre-cgprofile no-host-setup shape:

   ```bash
   bash /home/runner/runner-scripts/start-cockpit-runner-nohostsetup.sh
   ```

2. Remove cgprofile and its groups. The recorded uninstall removes the installer's endpoint fragment, units, sysusers
   file, links, versioned tree, `/etc/cgprofile`, `/run/cgprofile`, and the two groups. It is data-preserving when
   `--purge-data` is omitted: it intentionally keeps `/var/lib/cgprofile`, and it does not remove the
   aggregate/profile files or the uploaded wheel directory. Do not use `--purge-data` during normal rollback.

   ```bash
   /opt/cgprofile/current/venv/bin/cgprofiled uninstall --yes --remove-groups
   ```

3. On the observed host, where cgprofile was the only publisher of the endpoint aggregate, remove the remaining
   installer artifacts by exact path:

   Warning: the first command deletes the `/root/cgp-cutover-20261108` upload directory, including the wheels and
   install evidence kept there. It does not delete `/var/lib/cgprofile`.

   ```bash
   rm -rf /root/cgp-cutover-20261108
   rm -f /etc/host-endpoints.env /etc/profile.d/host-endpoints.sh
   rmdir /etc/host-endpoints.d
   ```

   `/var/lib/cgprofile` is deliberately preserved. These aggregate cleanup commands apply only when cgprofile was the
   sole endpoint publisher. On a host with another publisher, do not remove the shared aggregate or profile loader;
   collect the live fragment and publisher inventory first. That host-specific cleanup remains open under NC-35.

4. Re-pin lanes to the prior Run-Gate. Remove the additive venv only after no host profile references it and the
   cockpit is running:

   ```bash
   docker exec -u vscode "$COCKPIT_NAME" bash -lc 'rm -rf -- "$HOME/.venvs/gate-tools2540"'
   ```

5. The rollback cockpit still uses `$INTERACTIVE_SLICE` for `--cgroup-parent`. Retain the two slice units while that
   cockpit is running. To remove them, stop and remove the cockpit first, confirm the slices have no processes or
   child cgroups, then:

   ```bash
   rm -f /etc/systemd/system/dev-interactive.slice \
         /etc/systemd/system/dev-gates.slice
   systemctl daemon-reload
   ```

6. Remove the pinned PostgreSQL image after all lanes that use it have finished:

   ```bash
   docker rmi 'postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
   docker image inspect 'postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
   ```

   The inspect command must return nonzero.

7. After rollback verification, remove this operation's per-host pause flag and confirm the queue can launch again:

   ```bash
   rm -f "$CONTROLLER_HOME/host-sat-queue.disabled.$host"
   bash "$queue" status
   ```

## Host-sat queue pause flags

The host-satellite queue is checked in at `scripts/netcup/runner/host-sat-queue.sh`.
It is controller-side orchestration for serial Run-Gate lanes and uses the same
private host table as `remote-gate.py`. Copy
`scripts/controller/hosts.local.json.example` to
`$CONTROLLER_HOME/hosts.local.json`, then add `queue_lanes` arrays to the hosts
that should receive queue work. Each entry must be exactly one non-empty
`project|lane` pair. The `ssh`,
`api_check`, and `guard` values remain local because they contain addresses,
key paths, provider identifiers, or provider-specific commands. Every lane
launch and standalone `probe` command checks provider identity; the periodic
resource probe does not. Remote commands also run the configured on-host
guard. Host aliases must be accepted by the installed
`remote-gate.py`.

Pause flags live under `$CONTROLLER_HOME`, alongside controller logs:

| Flag | Scope | Effect |
| --- | --- | --- |
| `$CONTROLLER_HOME/host-sat-queue.disabled` | Global | Every queue loop idles and launches no new lane. |
| `$CONTROLLER_HOME/host-sat-queue.disabled.<host>` | One host | Only that host's queue loop idles and launches no new lane. |

When either flag exists, the loop logs
`disabled flag present: not launching` and rechecks every 60 seconds. The flag does not kill the loop and does not
cancel an in-flight remote run: the current lane finishes normally. `stop [host]` terminates only the queue loop;
an in-flight remote-gate job is left to finish.

The commands accept configured host aliases, or discover all configured queue
hosts for `start`, `stop`, and `status`:

```bash
CONTROLLER_HOME=${CONTROLLER_HOME:-"$HOME/controller"}
[[ ! -f $CONTROLLER_HOME/controller.local.env ]] || . "$CONTROLLER_HOME/controller.local.env"
queue=${CONTROLLER_QUEUE:-$CONTROLLER_HOME/bin/host-sat-queue.sh}
bash "$queue" status
bash "$queue" start
bash "$queue" pause runner-a
bash "$queue" resume runner-a
bash "$queue" stop runner-a
```

With no host argument, `pause` and `resume` set or clear the global flag. A
host-specific `resume` clears only that host's flag. A global resume clears
only the global flag, so it cannot accidentally remove another host's pause.

`status` reports queue pids, the current lane from `<host>.current`, pause
metadata, recent results when the TSV exists, and the last three log lines.
Queue state and results are in `$CONTROLLER_HOME/logs/host-sat-queue/` and
`$CONTROLLER_HOME/logs/host-sat-results.tsv`; remote-gate results are under
`$CONTROLLER_HOME/remote-gates/`.

After five consecutive `INFRA` verdicts the queue automatically pauses that
host and writes the reason, last lane, result, run, and log path into the flag.
If the metadata write fails, it leaves an empty pause flag and logs the write
failure. Every `INFRA` result also causes a 600-second back-off. Remove the
per-host flag only after diagnosing the failures.

Any release, cockpit recreation, or other host-exclusive operation must create
the per-host flag before it starts and let the current lane drain. For a queue
update, keep the flag in place until both `<host>.current` is absent and the
last queue log line contains `disabled flag present: not launching`; that line
proves the loop observed the pause. Then stop the loop and wait for its PID
file to disappear before replacing files. Never send TERM while a lane is in
flight: the TERM trap exits immediately and can leave a stale `.current`
marker. The full direct-predicate drain, atomic replacement, and bounded
startup-verification procedure is documented in
[`scripts/controller/README.md`](../../controller/README.md#installing-a-queue-update).

## Source trail

Reusable operating contracts are linked to their repository sources:

- Queue flags, drain behavior, back-off, and auto-pause: this guide's
  [Host-sat queue pause flags](#host-sat-queue-pause-flags) section and
  `host-sat-queue.sh`.
- Queue installation ordering and rollback-safe replacement:
  [`scripts/controller/README.md`](../../controller/README.md#installing-a-queue-update).
- Slice validation: `assay/tools/cgroup-parent.sh` and
  `scripts/netcup/runner/provision-runner.sh`.
- Workspace bind requirements: `assay/tools/self-qualification-container.sh`
  and `run-gate-project/CONSUMERS.md`.
- Run-Gate environment installation:
  `scripts/netcup/runner/setup-cockpit-run-gate.sh`.

Host execution logs, handoff notes, and machine-specific command captures are
scratch evidence. The reusable facts from them are stated here with links to
the maintained code and operating procedures.
