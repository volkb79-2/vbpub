# Milestones from 2026-11-18

Agreed with the operator (interview, 2026-11-18) and acknowledged by the
controller session `vbpub-main-controller`. Decisions D18 and D19 in the
[design guide](DESIGN-GUIDE.md). This plan narrows the order of
[WORK-PACKAGES.md](WORK-PACKAGES.md); package ids there stay valid.

## M1. Proof of concept by hand (start now)

Goal: for each method, show on a Debian 13 UEFI guest that a package upgrade
and a kernel change can be undone by booting the earlier state, with the
outside paths of D18 untouched.

| Method | Order |
|---|---|
| btrfs + snapper + GRUB (menu and `grubenv` on the ESP, F5) | 1 |
| btrfs + systemd-boot (kernels copied to the ESP per state) | 2 |
| ZFS boot environments with ZFSBootMenu, then ZFS with GRUB | 3 |

Scenarios: S1 to S6 and S10 of [EXPERIMENTS.md](EXPERIMENTS.md) (install,
`apt install`, upgrade then boot the earlier state, make it permanent, broken
initramfs, kernel removal, `/etc` edit). Marker files in `/home` and in a
service directory (`/srv`, `/var/lib/docker`) after the snapshot must survive
every rollback.

How the guest gets its system (no host-side loop devices, no host changes):
boot the shared raw Debian 13 cloud base from `downloads/vm-images`, attach a
second blank raw disk, and run `poc/install-btrfs.sh` inside the guest. The
script partitions the disk (ESP, swap-less, one btrfs), creates the layout with
the D18 subvolumes, runs `debootstrap`, installs kernel, GRUB and snapper, and
writes the ESP menu. The same script becomes the core of the M2 installer, so
the PoC is also its first test. Then the guest is restarted from the new disk
alone for the scenarios.

Host rules (controller, 2026-11-18): v1001 is a shared queue host. Before the
first long guest run, message the controller and get a window. Then at most 2
vCPUs and 2 to 4 GiB RAM for guests in total, `nice`d, one guest at a time,
never during the hostobs image build or the mutation campaign, guests only (no
edits of host configuration or docker state). The Netcup API identity check
comes before any writing SSH. Never nano1, never the controller host, never
r1002 (reserved for iogovmon until about 2026-11-19T04:00Z, then only on the
controller's "lease start"). ISO and base image paths come from the cowtree
resolver (`libraries/cowtree/repo-path iso|vm-images`), run disks are raw
reflinks with `cache=unsafe`, VMs are driven through `vm-lab/` where it fits;
what is found there is reported to the controller (no guest has booted from
`vm-lab` on a real run before this).

Output: a dated report per method in this directory (what existing packages
worked unchanged, what was written, manual steps, failures), and the decision
inputs for D14.

## M2. Unattended btrfs ISO flavor (start about 5 h after M1 starts)

One flavor, timebox 6 to 8 hours, remastered from the official Debian 13
netinst ISO promoted to `downloads/iso` (with `.sha256`), using the overlay
pattern of `scripts/truenas/vanilla-iso-patcher` (copy the pattern; those files
belong to the controller's track and are not edited).

| Aspect | Decision |
|---|---|
| Target disk | first SSD (`rotational=0`), else the first rotational disk; refuses a disk holding the booted ISO or USB stick |
| Safety | wipes without a prompt only when a config says so (USB stick config, or a kernel command line reference); with none, it stops at a confirmation prompt |
| Config sources | USB stick first (label `CIDATA`, cloud-init NoCloud style files, plus the layout file), then a reference to the vbpub repo (branch or `main`, fetched over the network) |
| Result | root on btrfs with the D18 layout, snapper, GRUB menu on the ESP, first boot ready for the rollback scenarios |
| Layout owner | our installer; `debian-install-v2` is not changed and not run in M1 or M2 (only a documented handoff point: a hook that runs a given command after the first boot) |

Later flavors (not planned yet): interactive minimal installer allowing root on
btrfs; a flavor that hands over to `debian-install-v2` after an adaptation
that lets it consume only the regions our layout file gives it (F9).

## Process

Own ciu worktree (`debian-rollback-20261118_053547-poc-20261118`), rebased when
`main` advances. Sonnet agents for implementation and review, Opus for
escalation; one fresh independent reviewer per package, docs-only changes
included. The controller merges from one queue; this session never pushes
`main`. Hand-off per package: branch, head, gate logs for every project
directory the branch touches, the reviewer's MERGE bound to that head.
