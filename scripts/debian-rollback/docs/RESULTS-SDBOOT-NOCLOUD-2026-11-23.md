# NoCloud systemd-boot builder guest results — 2026-11-23

## Scope

This run validates the current systemd-boot target installer and rollback
runtime through the optional NoCloud builder path. A Debian cloud-image guest
used the seed ISO to install an 8 GiB target disk; every acceptance scenario
then booted that installed disk alone, without the seed or cloud image.

This is **not** the product M2 Live ISO install path. The M2 path remains a
bootable Debian Live ISO whose fixed build-time target choice selects GRUB or
systemd-boot. The NoCloud seed is only a builder input for this automated PoC
recipe. See [the installation architecture](INSTALLATION-ARCHITECTURE.md).

The builder and installed runtime came from source commit
`2e2f26e5ec03b1e87242e7a2714aca2b335162c2`. The guest reports also verify the
installed `dr-state` SHA-256 on every scenario. The one-shot scenario was
rerun after the host-side driver learned to create its own non-default test
snapshot; that driver and its offline contract test are part of this branch's
current change.

## Artifacts and setup

| Item | Evidence |
|---|---|
| NoCloud seed ISO | `debian-rollback-builder-systemd-boot-2e2f26e5.iso`; SHA-256 `47053dfcd9dceab2a05d6ea7093e1827a480a3016a704ac5662f9d2923b271dc` |
| Debian cloud image | SHA-256 `de9d63f5e5ec5927d7548bc05b6043b87457b8c1cb9bcb95365168e8d0293b4f` |
| Installer runtime | `poc/dr-state` SHA-256 `a1697c0dac638f60c9a7b7640f5f3615b9e73941e244d1c774dd76e8bc10c196`; `poc/install-btrfs.sh` SHA-256 `2195939eb14349bf19b1be291b43a0556d7b26452534a99cbbc6fee06835835c` |
| Installed target | 8 GiB qcow2; SHA-256 `173160471118574d539ddee8c992d8e5d4699288de5e789c7af5d851609eb14b`; mode `0444` after installation |
| Disk integrity | `qemu-img check` passed with no errors; 17.19% allocated |
| VM | UEFI, QEMU TCG, 2 vCPUs, 2048 MiB guest RAM; each scenario used a fresh overlay of the read-only target |
| Host placement | Host-owned `dev-gates.slice`, with admission checked before each launch; one guest at a time |

The installer completed with rc 0 and initialized
`@os-000001-20261123-012022Z-install`. It installed systemd-boot, diverted the
stock Debian kernel postinst/postrm hooks, disabled the stock BLS writer, and
generated the initial menu. The ESP capacity check passed. The target image's
hash remained unchanged after the guest matrix.

## Guest matrix

Each result below is from a separate disposable overlay. Totals count the
driver's `PASS` assertions; no accepted run had a failing assertion.

| Scenario | Result | What it established |
|---|---:|---|
| `boot-check` | **12/12 pass** | Installed state boots from its own subvolume; systemd, networking, ESP files, and the `/etc`/`/opt` versus persistent-data layout are present. |
| `sdboot-esp` | **16/16 pass** | BLS entries map one-to-one to states, include the exact kernel/initrd pair, and `loader.conf` selects an existing `.conf` entry id. No interrupted scratch files remain. |
| `s2-s3` | **23/23 pass** | APT creates and describes a timestamped pre-change state; one-shot boot, `/etc` rollback, persistent-data survival, one-shot consumption, and `set-default` all work. |
| `sdboot-oneshot-consumed` | **9/9 pass** | A fresh guest creates a manual fixture snapshot, boots it once, then confirms `LoaderEntryOneShot` is absent in userspace. |
| `recovery-dependency` | **23/23 pass** | Manual `dpkg --force-depends` removal breaks a real local package dependency and its service. Boot-menu selection restores dpkg consistency, the package, application, service, and post-snapshot `/home` data. The recovered state becomes default and the failed state is deleted. |
| `recovery-kernel` | **27/27 pass** | Invalid kernel and initrd payloads make the default unusable. Boot-menu selection reaches the intact snapshot, its kernel modules load, persistent data survives, and the failed state plus its damaged ESP kernel files are deleted after default recovery. |
| `recovery-library` | **25/25 pass** | Renaming the package-owned dynamic-loader target prevents init from starting. Boot-menu selection restores the exact loader object and persistent data; the recovered state becomes default and the failed state is deleted. |
| `kernel-upgrade` | **21/21 pass** | Trixie updates installed kernel `6.12.118` over `6.12.117`; the snapshot keeps the old kernel. Both states boot with matching modules, and one-shot rollback returns to the new default. |
| `backports` | **21/21 pass** | Trixie backports installed kernel `7.3.4` over `6.12.117`; both kernel states boot with matching modules and one-shot rollback returns to the new default. |

Total for the accepted matrix: **177 passing assertions, zero failures**.

The recovery scenarios deliberately used QMP virtual-keyboard input to select
the recovery entry from the systemd-boot menu. They test operator-selected
recovery; automatic fallback after a failed boot is not claimed. Each recovery
also verified the OS state can be deleted after the known-good state becomes
the default.

## Harness corrections during the run

- The first `boot-check` launch did not reach the driver because the host-side
  serial/QMP socket path exceeded the Unix-domain socket path limit. The run
  was recovered through a short symlink and passed; the runner was then fixed
  to create a short socket alias and `boot-check` was repeated normally at
  12/12. The product code was not involved.
- The first one-shot run correctly refused its missing precondition: a fresh
  overlay has only the initial state, while the scenario expected a second
  state. The scenario now creates a manual fixture snapshot when necessary;
  `poc/test-dr-serial.py` covers successful and failed fixture creation. The
  fresh-overlay retry passed 9/9.
- The guest did not act on QMP ACPI powerdown during teardown. After a brief
  grace period the runner used QMP `quit`, observed QEMU exit code 0, and
  removed the exact disposable container. Scenario results were collected
  before teardown.

These first attempts are retained in the run logs. They are harness setup
failures, not failed product assertions.

## Resource observation

The backports scenario was profiled for 436 seconds with 431 samples. The
container peaked at its 2.5 GiB memory cap, used no swap, and recorded no OOM,
`memory.high` breach, or memory-limit drift. Average CPU use was 1.14 cores.
Memory and I/O PSI stalls in the container were below one second over the
profile; host memory and I/O pressure stayed near zero. The profile JSON is
retained with the run evidence.

## Retained evidence and limits

Run id: `20261123-0108-2e2f26e5`. The local evidence directory is
`vm-lab/debian-rollback-systemd-boot/runs/20261123-0108-2e2f26e5/`; it contains
the manifest, input and runner hashes, scenario results, console transcripts,
serial logs, and cgroup profiles. `SHA256SUMS` binds the retained files.

This run does not certify Secure Boot, physical hardware, power-loss recovery,
live pruning, ESP exhaustion, migration from legacy `@os-0001` disks, or
automatic boot fallback. The earlier [2026-11-22 M2 ISO report](RESULTS-SDBOOT-M2-2026-11-22.md)
remains the evidence for that named product-media build; this report records
the separate NoCloud builder run.
