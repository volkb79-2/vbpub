# Next steps

State 2026-11-12: planning only. Order matters; each step names what it
unblocks. Decisions and reasons are in [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md).

1. **Operator review of these documents.** Confirm or correct the decisions
   table, the OS-versus-data line (design guide section 3) and the experiment
   plans. Open: project name and final location (Q8).
2. **Desk checks** that need no VM (design guide Q4, Q5): signed systemd-boot
   in Debian 13; `compatibility=grub2` against `zpool upgrade`; GRUB 2.14
   environment block on btrfs in Debian 14; snapper 0.10.6 against the
   features needed.
3. **General VM tool in `scripts/vm-lab/`**, lifted from
   `scripts/truenas/tcg_vm.py` with TrueNAS behaviour unchanged. Add selectable
   RAM, a serial boot menu, reset, boot-loop detection. Own change, own review.
   Unblocks experiment E1.
4. **Unattended installer ISO**, modelled on `scripts/truenas/golden-iso`, that
   partitions and installs instead of restoring a fixed image. Two layouts:
   btrfs with snapper, ZFS with boot environments. Unblocks E1 scenario S1 and
   the real-host installs.
5. **Experiment E1** in the VM lab for both candidates
   ([docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)).
6. **Experiment E2** on r1002, only after the controller session sends "lease
   start". Prepare the install and workload scripts before the lease.
7. **Decide** filesystem and bootloader (D14, Q1, Q2) from E1 and E2, and
   record the decision in the design guide.
8. **Phase 1 product**: installer, snapshot hook, boot-menu entries, the
   "make permanent" and prune commands, README and consumer guide.
9. **Phase 2**: automatic fallback, tied into `vbpub-reboot-check.timer`.
10. **Cloud image** built from the installer's output.
