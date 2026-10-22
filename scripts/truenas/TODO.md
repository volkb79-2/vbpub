# Shared TrueNAS tooling follow-up work

The boot-pool patch and golden-image workflows keep their own open work under
their project directories. This file tracks shared API, VM runner, and installer
infrastructure.

## 2026-10-21 v1001 public-host access check

### Paused live-test result

The fingerprint-pinned `live-ui-observed-v1001` workflow verified the exact
17-file source manifest, provisioned the guarded empty 2 GiB Path A root, and
authenticated through PWMCP. The first child dataset create failed in
middleware with `NameError: name 'mountpoint' is not defined` from
`pool_/dataset.py:117`; no child dataset was created and no adapter patch was
applied. The registered unit gate passed with 194 tests and 45 subtests. See
the [patch checkpoint](boot-pool-patch/TODO.md#current-checkpoint-2026-10-21-v1001-live-test-stop-point-v28-migration-remains-open)
and its restricted live log
[`live-ui-observed-v1001-child-create-nameerror-20261021.log`](boot-pool-patch/artifacts/live-ui-observed-v1001-child-create-nameerror-20261021.log).

The supported Netcup restore of snapshot
`truenas-v27-rc1-base-20261021`
(`b8e286a2-9248-4c35-b9ad-ca82fd2fde95`) finished under task
`fbad4f98-cfb0-4740-96dc-dff1e562113f`; the server-details response reported
`SHUTOFF`, uptime 0. This is the agreed stop point. Before the next live test,
rotate `truenas_admin` away from the publicly known `truenas` password on the
first boot, generate a random host-specific value, and save it in a protected,
gitignored host-secret TOML. Do not start another VM or power on v1001 until
testing resumes.

The operator confirmed v1001 still uses its install-time `truenas_admin`
credential. The earlier browser login failure used the golden-sidecar
password; it was a credential-selection mistake. The registered `live-ui-auth`
lane subsequently authenticated through PWMCP and loaded the dashboard. The
registered `live-ui-prepare-ssh` lane then installed the supplied root Ed25519
key while keeping root/global SSH password authentication disabled, enabled
SSH autostart, and started SSH. A post-snapshot SSH timeout was a boot-readiness
race: PWMCP login and SSH both succeeded after the server finished booting.

Before any storage UI test, a read-only `live-inspect` passed against v1001:
TrueNAS `27.0.0-RC.1`, Path A, `boot-pool` ONLINE, no user-data root, no Path B
pool, no VM/container records, and no patch-install marker. The supported
Netcup CLI created snapshot `truenas-v27-path-a-ssh-ready-20261021`
(`2cda033b-aec0-4f7f-8e6b-853c81bea5f8`) before storage mutations; the
provider completed its controlled shutdown/snapshot/restart. Recheck the
snapshot list through `scripts/netcup/scp-api.py` before relying on it for a
rollback. At that point v1001 was the only active TrueNAS VM, so no local QEMU
process was to run at the same time. The latest checkpoint below records the
completed restore and current shutoff state.

The guarded `live-v27-patch` installer refused before any write or service
stop: 14 of its 15 expected source fingerprints differ from the live files.
Read-only comparison found two custom modules that exactly match this
worktree, but `/root/v27-boot-pool-patch-install.json` is absent and the other
source changes have unknown provenance. The observed 17-file fingerprint is
[`observed-v1001-unrecorded-code-20261021.json`](boot-pool-patch/v27/observed-v1001-unrecorded-code-20261021.json).
It is for exploratory UI checks only; do not force the current adapter over
this state. The new guarded `live-ui-observed-v1001` lane verifies those hashes
before a bounded PWMCP UI workflow. It does not certify the current adapter.
The latest run result and snapshot restore are recorded above; do not repeat
the workflow until the middleware NameError is understood and host credentials
have been rotated.

The host presents a self-signed TLS certificate whose subject is
`CN=localhost`; the existing API config disables certificate verification.
The API-key authentication attempt used that unverified endpoint and returned
`Invalid API key` before any method call. Treat that key as exposed; do not
reuse it. The golden sidecar password was also captured in an earlier tool
snapshot and must be rotated on its source VM before another image capture.
The host's install-time password and the golden sidecar password are separate
credentials. See
[`golden-iso/TODO.md`](golden-iso/TODO.md#preserve-the-golden-image-credential-boundary).

This does not establish a defect in the API client. The rejected golden-image
API key is exposed and must not be reused; the golden sidecar password also
needs rotation on its source VM before another capture. Use the registered
live UI gates and do not start a local QEMU guest while v1001 is the active
test VM. The operator authorized provider snapshot/reinstall resets as needed.

## 2026-10-17 v28 VGA capture and serial console path

The resumed v28 installer guest reached UEFI, but the QMP screenshot and a
direct Unix-VNC capture were both reported as patterned/corrupt at 800x600
with a 32-bit RFB pixel format. The serial log stopped at GRUB; no install
input was sent. The operator stopped only that v28 guest cleanly and retained
its COW and OVMF state; the v27 guest remains untouched. There is not enough
capture evidence here to attribute the VGA symptom to QEMU, the guest, or the
RFB decoder.

The runner now has an opt-in `--serial-console` chardev backed by a bidirectional
Unix socket and an interactive `console serial` relay. It preserves a serial
log and adds no TCP listener. Unit coverage exercises QEMU argv construction,
socket aliasing, and terminal input/output. The registered unit lane passed
with 139 tests and 45 subtests. A serial-enabled v28 installer guest has since
been resumed, but its live serial console path still needs inspection. A serial
socket does not force UEFI or GRUB to mirror the menu to COM1; the boot entry
must route output there.

## 2026-10-17 installer-console resume and duplicate QEMU options

The operator resumed their stopped v28 installer state with the new VNC mode.
Admission passed for the second guest, but its runner exited and was cleaned
up; the COW overlay and OVMF variables remain in
`vanilla-iso-patcher/artifacts/qemu-test/v28-install`. Inspection found that
`host_command_script()` expanded its optional-argument list twice, duplicating
the installer CD-ROM and, in VNC mode, the same Unix VNC listener. The saved
serial log ends at the earlier clean poweroff, so it contains no new guest-side
failure detail. The runner now emits the optional list once, and its shell
argv test asserts one CD-ROM and one VNC listener. The registered unit lane
later passed with 139 tests and 45 subtests. A serial-enabled resume has since
started, but no successful installer console interaction is recorded here;
leave the running v27 guest untouched.

## 2026-10-16 isolated TCG runner status

The reusable [`tcg_vm.py`](tcg_vm.py) runner starts a disposable guest in its
own Docker container under `$CGROUP_PARENT_DEV_BACKGROUND` (resolved to
`dev-background.slice`). It requires a loaded finite background slice, checks
ancestor memory headroom, resolves bind mounts from Docker's live mount table,
and verifies the placed container and limits. The guest container is capped at
200% CPU and 3.5 GiB memory with swap disabled; the guest receives 3 GiB RAM.
Its COW disk and explicit OVMF state live in a private `.vm/` state directory;
the base image is mounted read-only. The registered unit lane most recently
passed with 139 tests and 45 subtests.

Patch VM, release adapter, and acceptance history now lives in
[`boot-pool-patch/TODO.md`](boot-pool-patch/TODO.md), with its test logs under
`boot-pool-patch/artifacts/`. Keep this file focused on the shared runner and
its resource boundary.


## Shared API compatibility

- Implement and test a separate legacy WebSocket, authentication, and job
  adapter for TrueNAS 24.10. The current shared client targets the v27+ API.
