# Systemd-boot M2 ISO guest results — 2026-11-22

This is the current-candidate guest evidence for both build-time target
variants of the M2 Debian Live installer. The candidate uses the timestamped
state-ID schema. The systemd-boot M2 ISO received the full rollback and
recovery matrix; the GRUB-target comparison received a fresh install, first
boot, and APT rollback/default-selection run. The separate NoCloud
cloud-image builder route also installed a systemd-boot target disk, which
then booted without its seed or cloud image. This report is not a release
claim; the final registered offline gate and independent exact-commit review
remain before merge.

## Artifacts and provenance

| Artifact or source | Revision / SHA-256 | Use |
|---|---|---|
| M2 systemd-boot Live ISO | `debian-rollback-m2-systemd-boot-fe2a9afe4.iso`; `a7f51e97580900d0d60b984d499f9bf54c05d15f74b32b5f4449bd24d32addee` | Installed to a blank 12 GiB virtual disk in UEFI mode |
| M2 GRUB Live ISO | `debian-rollback-m2-grub-fe2a9afe4.iso`; `ea5bea60bb51f3b7c10e0bd829b862aae7f29283401c8e3dff36314f8906b12c` | Fresh comparison install and rollback/default-selection guest passed |
| Optional M2 `CIDATA` config ISO | `debian-rollback-seed-fe2a9afe4.iso`; `9336c540335bab78944a86d62e38db0139d4683ab4606020dd0d1c171514348f` | Built, but not attached; install used the ISO-embedded config |
| NoCloud systemd-boot builder seed ISO | `debian-rollback-builder-systemd-boot-f9f55e1aa.iso`; `5565430138555d676a1e6c3985316e66b14e3c6fd5310770f0b64e119cd5201d` | Attached only to the automated cloud-image builder guest |
| Debian generic cloud image | `debian-13-genericcloud-amd64.qcow2`; `de9d63f5e5ec5927d7548bc05b6043b87457b8c1cb9bcb95365168e8d0293b4f` | Builder guest boot disk; not installed onto the target |
| NoCloud builder installed target disk | `data-disk-1.qcow2`; `fe6939fb0226a5dcbd3bb22873f9bd5bf032c142b74abc4bcfe8a6494bef871d` | Read-only base for standalone systemd-boot guest checks |
| ISO payload source | `fe2a9afe4` | Includes the M2 variants and installed runtime tested here |
| NoCloud builder source | `f9f55e1aa` | Seed and shared installer/runtime payload used for the builder guest |
| Guest scenario driver | `f9f55e1aa`; `dr-serial.py` SHA-256 `3e74e54a2f47f8f6d85ac05d4ab6528a4b400ab8d9173124a11b210bde0c2671` | Drives UEFI, QMP keyboard selection, serial prompts and acceptance scenarios |
| `poc/dr-state` | SHA-256 `e4b0ee22a1d23cff212ad1ea69f1ebe7a535aded9060ed2313509d4aa92e3579` | Runtime installed into the guest |
| `poc/install-btrfs.sh` | SHA-256 `9785218bf0bd1b344fb02353dec8853204ae9f5eeb9c812a78cf55b1c35d0888` | Shared target installer installed into the guest |
| Installed APT hook config `80dr-state` | SHA-256 `8252ee29d24c6bcf3ed1a1dfc1c500e66d2ff89638302c50c1e9e55f494cf1cb` | Exact hook captured from `install-btrfs.sh` and verified in both backend guests |
| `iso/dr-autoinstall` | SHA-256 `e10e5b5f366cea7275a7d67b7676f7e6d9e9fc6794d300440b08d61e9c6b1145` | M2 Live installer entry point |
| `iso/build-iso.sh` | SHA-256 `0a7c92cc99814b3ef45b3e505a256b9a18d8d6eff0d5d1699f6d60286351e056` | M2 ISO builder |

The ISO was built from the Debian Live 13.7.0 amd64 standard image. The guest
ran under OVMF on v1001 in the `debian-install-vm:cb76f8b5350b` QEMU
environment. The systemd-boot M2 install used `source=iso-embedded`, target
`/dev/vda`, and completed with rc 0. The installed initial state was
`@os-000001-20261122-024158Z-install`. No M2 `CIDATA` config ISO or NoCloud
builder seed was attached to this installation.

## Guest results

Each result below came from the current systemd-boot M2 installed target. A
scenario starts from a fresh copy of the installed base unless it explicitly
depends on a state created by an earlier scenario in the same guest.

| Scenario | Result | Evidence established |
|---|---:|---|
| `boot-check` | 12 pass, 0 fail | First UEFI boot, running systemd, network, selected state, ESP mounts and kernel files |
| `sdboot-esp` | 16 pass, 0 fail | Canonical BLS ownership and paths, `.conf` selectors, loader default, ESP scratch cleanup and free space |
| `s2-s3` | 23 pass, 0 fail | Apt pre-change snapshot and plan metadata, `/etc` rollback, persistent-data survival, boot-once returning to default, and `set-default` persistence |
| `sdboot-oneshot-consumed` | 8 pass, 0 fail | systemd-boot consumes `LoaderEntryOneShot` after the selected state boots |
| `recovery-dependency` | 23 pass, 0 fail | Manual `dpkg --force-depends` removal made `apt-get check`, the dependent app and service fail; boot-menu recovery restored consistency and service, preserved a post-snapshot `/home` witness, set the recovered state default, rebooted it, and deleted the failed state |
| `recovery-kernel` | 27 pass, 0 fail | Invalid kernel/initrd in the default state prevented boot; selecting the older menu entry restored boot and modules, preserved `/home`, set the recovered state default, proved a plain reboot, and deleted the failed state |
| `recovery-library` | 25 pass, 0 fail | Removing the package-owned dynamic-loader target prevented init; selecting the older entry restored the loader and userspace, preserved `/home`, set the recovered state default, proved a plain reboot, and deleted the failed state |
| `kernel-upgrade` from Trixie/security | 6 pass, 1 expected availability failure | `apt-get update` succeeded, but no newer kernel was offered than installed `6.12.117-1`; the scenario stopped before installing a package or creating a snapshot |
| `kernel-backports` | 21 pass, 0 fail | With Trixie backports enabled, upgraded to `7.3.4+deb13-amd64`, booted the new kernel, selected the old state/kernel once, then returned to the new default on a normal boot |

The separate GRUB-target ISO was also installed on a blank v1001 UEFI guest
with its embedded config (`target=grub`, `/dev/vda`, rc 0). Its initial state
was `@os-000001-20261122-174829Z-install`.

| GRUB scenario | Result | Evidence established |
|---|---:|---|
| `boot-check` | 9 pass, 0 fail | First boot uses the GRUB backend and timestamped root; systemd/network, split persistent mounts, and GRUB ESP files are present |
| `s2-s3` | 21 pass, 0 fail | Apt install created `@os-000002-20261122-182228Z-apt`; exact package plan/metadata was recorded; one-shot boot restored the earlier state while preserving `/home` and service data; one-shot returned to the original default; `set-default` persisted the APT state across reboot |

## NoCloud cloud-image builder path

This is a separate automated installation route. The M2 product install does
not need a NoCloud seed: its Debian Live ISO carries the installer and can use
embedded configuration or the optional M2 `CIDATA` configuration ISO. The
cloud-image builder route does use its NoCloud seed ISO to give cloud-init the
installer payload and target-bootloader choice.

On v1001, the Debian generic cloud image booted as the builder guest. The
NoCloud seed selected `systemd-boot`; the guest attached a separate blank 8
GiB target as `/dev/vdb` and ran
`install-btrfs.sh --device /dev/vdb --yes --bootloader systemd-boot`. The
installer completed with `DR-BUILD-DONE rc=0` at 2026-11-22 18:57Z. It created
the initial state `@os-000001-20261122-184000Z-install`.

After the builder shut down, a fresh UEFI guest booted from that installed
target disk alone. Neither the cloud image nor the seed ISO was attached. The
target disk was a read-only base, with QEMU writing to a separate overlay.
Both read-only checks returned driver exit 0:

| Scenario | Result | Evidence established |
|---|---:|---|
| `boot-check` | 12 pass, 0 fail | Installed state booted under systemd-boot; systemd, network, ESP, kernel/initrd, and split data paths were present |
| `sdboot-esp` | 16 pass, 0 fail | Canonical BLS entry, suffixed default, valid kernel/initrd paths, no scratch state, and readable free space |

The installed target checksum remained unchanged after these guest checks.
The retained `ncl-builder-boot-check-results.txt` and
`ncl-builder-sdboot-esp-results.txt` summaries, their serial transcripts,
filtered builder markers, and installed-disk checksum are in the evidence
directory below; `SHA256SUMS` covers those records.

The ordinary Trixie/security kernel scenario's single failure is an input
availability result, not a product assertion failure. The positive upgrade
acceptance used Debian Trixie backports and exercised both kernel/state boot
paths.

## Harness correction during testing

The first `recovery-library` attempt stopped before issuing its guest damage
command. The serial driver treated a kernel build-banner line containing a
prompt-like substring as an already-open shell prompt. The transcript and
scenario output are retained as `recovery-library-fe2a9afe4.log`. Commit
`f9f55e1aa` fixed prompt recognition to require a whole normalized console
line and added a kernel-banner regression case; the fresh rerun passed 25/25.
The first GRUB boot-check invocation also used the driver's default login
credential, while the M2 install uses the private configured credential; it
stopped before product assertions. The rerun read that credential privately
inside the helper and passed 9/9. The first GRUB `s2-s3` setup omitted the
installer script needed to extract the APT hook, so it stopped before package
changes. The complete source bundle rerun passed 21/21. These were test
orchestration corrections, not product failures.

## Retained evidence

Logs are in the resolved, git-ignored project `vm-lab` directory:

```text
scripts/debian-rollback/vm-lab/runs/fe2a9afe4-systemd-first/logs/
```

The directory contains filtered installer marker excerpts, the per-scenario
outputs in the tables, the kernel availability diagnostics, and the initial
test orchestration attempts. `SHA256SUMS` covers the retained evidence files. Full
VM disks and OVMF state remain on v1001 under
`/home/runner/jobs/dr-poc/vm-lab/dr823dep/runs/fe2a9afe4-builder-systemd-f9f55e1aa/`;
the installed target disk is read-only after capture and is not tracked in
Git.

## Limits and remaining work

- Live pruning, ESP-full behavior, kernel removal, Secure Boot, physical
  hardware, and GRUB-to-systemd-boot conversion are not established here.
- Automatic failed-boot fallback remains out of scope. Recovery requires an
  operator to select a known-good state from the systemd-boot menu.
- Run the final registered `offline` gate on the documented commit and obtain
  independent review of that exact commit before merge.
