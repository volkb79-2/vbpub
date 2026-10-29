# Optional live v27 acceptance test

The `tests/live/test_live_workflows.py` probe exercises datasets and snapshots, a local user, a container, and a VM on
an operator-selected TrueNAS v27 host. It creates a unique `tnt-live-<suffix>` namespace and removes only the resources
it created there. It requires a disposable data pool with enough capacity for the container image, VM memory, and the
requested VM zvol. Do not point it at a production system or a pool that cannot safely hold temporary test data.

The default test command excludes the `live` marker through `pytest.ini`; `tests/test_live_lane_config.py` checks that
the exclusion remains configured. The live test is not skipped when selected: missing variables, an absent host,
or a host that cannot connect make the test fail.

Before running it, configure paired `tnt.toml` and `tnt.secrets.toml` files with a host allowed to create and delete
temporary resources on the chosen host. Copy the committed examples and keep both files mode `0600`; credentials
belong only in `tnt.secrets.toml`. The test uses the same host loader, credential checks, and TLS rules as the CLI.
The M2 probe also switches between the selected profile and a second alias for the same appliance. If
`TNT_LIVE_PROFILE=lab`, add `lab-alt` to `tnt.toml` with the same URL, username, auth mode, and TLS mode, then add
`[hosts.lab-alt]` to `tnt.secrets.toml` with a matching `host_url` and a credential for that endpoint. The alias may
use the same credential value as `lab`; each entry still needs its own endpoint binding. For another primary name,
use that exact name followed by `-alt`.
Supply all required environment values explicitly:

Run the registered lane from `truenas/tnt`; it starts `tester-unified` through Run-Gate and selects only the `live`
marker. Put both configuration files and any SSH identity material in a private directory outside the repository.
The left side of `RUN_GATE_EXTRA_MOUNTS` must be the physical path visible to the Docker host; the right side is the
tester-visible path and must match the paths in the environment and tnt configuration.

```sh
export TNT_CONFIG=/private/scratch/tnt/tnt.toml
export TNT_SECRETS=/private/scratch/tnt/tnt.secrets.toml
export RUN_GATE_EXTRA_MOUNTS=/private/scratch/tnt=/private/scratch/tnt
export TNT_LIVE=1
export TNT_LIVE_PROFILE=lab
export TNT_LIVE_POOL=tank
export TNT_LIVE_GROUP_ID=1000
export TNT_LIVE_CONTAINER_IMAGE_NAME=alpine:3.24:amd64:default
export TNT_LIVE_CONTAINER_IMAGE_VERSION=20261016_13:00
export TNT_LIVE_VM_MEMORY_MIB=2048
export TNT_LIVE_VM_DISK_BYTES=10737418240
run-gate live-v27
```

The image name and version must match a pair listed by the selected host's container image registry; the v27 RC.1
catalog returned this Alpine pair during the 2026-10-29 acceptance run. Replace the other sample values with values
that exist on the selected host. `TNT_LIVE_GROUP_ID` is the TrueNAS group entry ID, not the Unix GID.
`TNT_LIVE_VM_MEMORY_MIB` must be at least 20, and the zvol size must be positive. The selected
pool and group are checked before any resource is created, and all generated test resource names begin with
`tnt-live-`.

TLS verification stays enabled by default. For a deliberately disposable host with a self-signed certificate, set
`TNT_LIVE_INSECURE=1` and use a host with `tls = "insecure"`; both the host setting and explicit acknowledgement are
required. Never place credentials in this command line. Keep them in the mode-0600 secrets file described in the
[multi-host configuration guide](../README.md#configure-multiple-hosts).

Cleanup is attempted for every resource even if an earlier cleanup action fails. A cleanup failure fails the test and
names the resource class, so inspect the host for any remaining resource whose name starts with `tnt-live-` before
using that namespace again. The lane is a tester-unified gate; do not execute the pytest command directly from the
devcontainer cockpit. The out-of-repository mount keeps credentials out of the judged worktree and the gate forwards
only the declared live-test variables.

The live acceptance record below names the appliance class and software version only. The corresponding storage
import check follows the [ACTION-MATRIX B-06 workflow](ACTION-MATRIX.snapshot.md#actions).

## Live acceptance run: 2026-10-29

The live lane ran through registered Run-Gate in `tester-unified` against a disposable, vanilla TrueNAS 27.0.0-RC.1
appliance restored from the pristine provider snapshot. The final `live-v27` verdict was PASS (`2 passed` in
314.94 seconds). This report records the host class and version only; it contains no host name or address. The
appliance presented a self-signed TLS certificate, so the host profile used
`tls = "insecure"` together with the explicit `TNT_LIVE_INSECURE=1` acknowledgement. The six relaxation-patch markers
from the earlier acceptance session were absent after restore. A temporary pool was created on a free tail partition
and imported through tnt; final restore removes that test state.

| Area | Result | Evidence / limit |
|---|---|---|
| Registered live lane | PASS | `live-v27` completed with both live tests passing; namespaced resources were cleaned up. |
| Datasets, SMB/NFS shares, snapshots | PASS | Created and removed only temporary resources. Share deletion retained the backing dataset. A periodic snapshot task ran and produced a snapshot, then was disabled and removed. |
| Users and SSH credentials | PASS | Temporary user CRUD, public-key changes, and password workflow completed; cleanup succeeded. |
| Containers and job progress | PASS | Temporary container lifecycle completed; job state, progress events, and callbacks were observed. |
| Pool import, ACTION-MATRIX B-06 | PASS | Created a pool on a free partition with `zpool create`, exported it, then used `pool.import_find` and `pool.import_pool` on vanilla v27; the imported pool reported ONLINE and appeared in `pool.query`. See the [B-06 workflow](ACTION-MATRIX.snapshot.md#actions). |
| Dashboard, alerts, jobs, services, storage | PASS | CLI readers and Textual Pilot views loaded against the live host. |
| Network, Apps, audit, API keys | PASS | Read views loaded through CLI and TUI. A temporary API key was created, saved only to a private mode-0600 scratch file, renamed, revoked, and removed. No verifier fields appeared in listings. |
| Multi-host switch | PASS | Added a second alias for the same host and switched to it and back in the TUI. |
| Interactive SSH hand-off | PASS | The TUI suspend/restore path passed with Pilot. The real interactive PTY hand-off enforced the pinned host key and ignored a hostile user SSH config marker. No remote command was supplied. |
| Configuration export | PASS | Export created a valid tar archive with mode `0600`; the probe removed it without reading its contents. |
| VM lifecycle | DEFERRED | The appliance did not expose `/dev/kvm`, so virtualization capability was false and VM create/lifecycle was not attempted. |
| Apps lifecycle | DEFERRED | No disposable app candidate was available; only the read/list path was exercised. |
| Replication/cloud-sync execution | DEFERRED | Read/list paths passed. No disposable source/target or cloud endpoint was available for a safe task run. |
| Reboot, shutdown, update apply | DEFERRED | Not run. The live acceptance did not need a power or update action. |

The system configuration export is a harmless read/download action; reboot, shutdown, and update apply remain outside
this acceptance. A snapshot task run returned `null` on this RC.1 build even though the generated snapshot appeared;
the workflow and live probe now treat the call as an ordinary action and verify the resulting snapshot. The registered
lane cleanup succeeded. After testing, the provider snapshot revert task finished and the powered-off instance was
powered on. A separate read-only inspection named `live-inspect` was reported as passing from estate gate tooling in
another checkout; it confirmed TrueNAS 27.0.0-RC.1, an empty v27 patch install record, no Path B pool, and no guest
mutation calls. `live-inspect` is not declared in this directory's `run-gate.toml`, which registers `r0-r1` and
`live-v27`; tnt's estate registration remains open under TNT-32.

### Findings fixed during acceptance

- Container stop job completion could precede the instance's `STOPPED` state; the workflow now waits for the bounded
  state postcondition. Container and API query methods that reject exact-ID filters now use their instance lookup.
- VM names now follow the v27 ASCII-alphanumeric constraint, and VM acceptance is capability-gated when `/dev/kvm` is
  absent. The live environment had no virtualization support, so no VM mutation was attempted.
- The v27 dashboard returns fractional uptime and numeric arrays for reporting; readers now accept finite numeric
  values and arrays. Audit and API-key CLI JSON paths also serialize native datetimes safely.
- RC.1 rejected `guestok` in the SMB `DEFAULT_SHARE` fixture, so the fixture omits that unsupported field. The
  snapshot-task `run` response and API-key exact-ID query behavior are documented above and covered by regressions;
  the API-key rename collision check compares against the ID returned by `api_key.get_instance`.
- The configuration-export plan fingerprint previously included volatile uptime/load fields from `system.info`,
  causing false drift. Its stable host identity probe now fingerprints the hostname while retaining the separate
  version, state, and boot-ID probes. A FakeServer regression verifies both volatile-field tolerance and hostname
  drift refusal.
- Two Textual Pilot races were test synchronization defects: one checked before the refreshed view mounted, and one
  observed the new session before its dashboard remounted. The pilot now waits for a new loaded view and both the
  target session and dashboard before continuing.

### Remaining manual checks

Before running any reboot, shutdown, or update-apply acceptance on a disposable host, require an approved maintenance
window and set `TNT_LIVE_ALLOW_SYSTEM=1` in that shell; this variable gates the written procedure and is not a runtime
CLI option. Check job, VM, container, and SMB-session preconditions, then verify the relevant action and reconnect.
Never bypass host-key checking or add keys automatically. Generic middleware/syslog tailing and arbitrary remote SSH
command mode remain unsupported by the v27 contract.
