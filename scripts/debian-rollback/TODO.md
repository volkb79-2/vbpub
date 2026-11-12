# Next steps

State 2026-11-12: planning only. Order matters; each step names what it
unblocks. Decisions and reasons are in [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md).

1. **Operator review of these documents.** Confirm or correct the decisions
   table, the OS-versus-data line (design guide section 3) and the experiment
   plans. Open: project name and final location (Q8).
2. **Desk checks** that need no VM (design guide Q4, Q5). Done 2026-11-12 for
   signed systemd-boot in Debian 13 (exists) and `compatibility=grub2` against
   `zpool upgrade` (safe); results are in the design guide's question table.
   Left for the VM (E1): Secure Boot through shim to systemd-boot, and whether
   Debian 14's `grub-install` sets up the btrfs-header environment block.
   Still a desk check: snapper 0.10.6 against the features needed.
3. **General VM tool in `scripts/vm-lab/`**, lifted from
   `scripts/truenas/tcg_vm.py` with TrueNAS behaviour unchanged. Add selectable
   RAM, a serial boot menu, reset, boot-loop detection. Own change, own review.
   Unblocks experiment E1.
   Packages (operator go-ahead 2026-11-12 for all synergy items Y1-Y6 of the
   design guide, section 9.1). Implementation is outsourced through
   `scripts/controller/codex-impl.sh` with `CODEX_ROUTE=deepseek` (or
   `opencode-impl.sh`). The luna route is usage-limited until 2026-11-17, so
   reviews go to a Sonnet agent or a fresh deepseek session that never edits.
   Each package runs in its own `ciu worktree` and is merged to `main` with
   `--no-ff`. Serial, because they touch the same files. Gate lanes are queued
   through the controller's two gate slots
   (`/home/vscode/vbpub-release-program-20261027/codex/gate-run.sh`; the
   in-repo `scripts/controller/gate-run.sh` needs lock paths that are not
   configured in this environment). Briefs, review reports and gate logs are
   under `artifacts/debian-rollback/` (ignored, local).

   | Package | Content | Route | State |
   |---|---|---|---|
   | VML-01 | Extract `scripts/vm-lab/vm_lab.py`; TrueNAS front unchanged; `console reset` | Sonnet; deepseek review, 2 rounds | merged 2026-11-12 |
   | VML-02 | Y2 + Y1: console recorder and typing in the VM tool; pwmcp's `console_capture.py` and `launch-truenas-vm.sh` replaced by it | deepseek | next |
   | VML-03 | Y3: `console wait-for-text` through `pwmcp ocr --image`; boot-loop detection from repeated screen text | deepseek | after VML-02 |
   | VML-04 | Y5: capture console screens (Debian installer, Grml, GRUB, systemd-boot, ZFSBootMenu) into pwmcp's corpus | deepseek | after VML-03 |
   | VML-05 | Y4: one console interface, QMP backend and pwmcp browser backend | deepseek | after VML-03 |
   | VML-06 | Y6: pwmcp test fixture serving a guest's VNC socket to its browser | deepseek | after VML-05 |

   The TrueNAS `unit` and `unit-boot-pool-patch` lanes are red on `main` for
   reasons outside this work (TN-15, TN-16 in the TrueNAS backlog).

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
