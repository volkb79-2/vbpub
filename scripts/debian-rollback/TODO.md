# Next steps

State 2026-11-12: the VM tool packages VML-01 to VML-03b are merged; no real
guest has been started yet. Decisions and reasons are in
[docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md).

**The plan for everything that remains is
[docs/WORK-PACKAGES.md](docs/WORK-PACKAGES.md)** (written 2026-11-12): package
ids VML-04 to VML-10 and DR-01 to DR-81, model routing, frontier decisions F1
to F18, operator prerequisites OP1 to OP8. It supersedes the VML-04 to VML-06
rows of the table below (the old Y5, Y4, Y6 packages are now VML-08, VML-09,
VML-10) and details steps 4 to 10 of this list. Next: operator items OP1 to
OP3, then VML-04; DR-01 needs no guest and can start at once.

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
   (`$CONTROLLER_HOME/codex/gate-run.sh`; the
   in-repo `scripts/controller/gate-run.sh` needs lock paths that are not
   configured in this environment). Briefs, review reports and gate logs are
   under `artifacts/debian-rollback/` (ignored, local).

   | Package | Content | Route | State |
   |---|---|---|---|
   | VML-01 | Extract `scripts/vm-lab/vm_lab.py`; TrueNAS front unchanged; `console reset` | Sonnet; deepseek review, 2 rounds | merged 2026-11-12 |
   | VML-02 | Y2 + Y1: console recorder and typing in the VM tool; pwmcp's `console_capture.py` and `launch-truenas-vm.sh` replaced by it | deepseek, finished by Sonnet; Sonnet reviews, 3 rounds | merged 2026-11-12 (vbpub@c46b0dfe8) |
   | VML-03a | vmscreen `--qemu-dump-dir`: works when QEMU sees the dump directory under another path (the VM tool's container) | Sonnet; Sonnet review, 2 rounds | merged 2026-11-12 (vbpub@77343737e) |
   | VML-03b | Y3: `console text` and `console wait-for-text` in the VM tool: vmscreen (exact text) first, `pwmcp ocr --image` for graphical screens; fail patterns and boot-loop detection | Sonnet; Sonnet review, 2 rounds | merged 2026-11-12 (vbpub@d55c3132e); not yet run against a real guest |
   | VML-06a | Raw base images cloned by reflink (`start --base-format`, `--allow-full-copy`), `disk promote` into a shared base folder; qcow2 path unchanged (D17, WORK-PACKAGES VML-06) | Sonnet; Sonnet review FIX-FIRST then MERGE | merged 2026-11-17 (vbpub@3038974b4); not yet run against a real guest or a real btrfs reflink through the tool |
   | VML-07 | Hardening follow-ups of VML-02, VML-03b, VML-06a: signals, screendump ownership, per-call time budget, broken PATH vmscreen, promote interrupt cleanup | Sonnet; Sonnet review MERGE, four follow-ups folded in | merged 2026-11-17 (vbpub@522c8d69a); not yet run against a real guest |
   | VML-04 | Y5: capture console screens (Debian installer, Grml, GRUB, systemd-boot, ZFSBootMenu) into pwmcp's corpus, with `vmscreen ocr-pair` where exact text exists | open | after VML-03 |
   | VML-05 | Y4: one console interface, QMP backend and pwmcp browser backend | open | after VML-03 |
   | VML-06 | Y6: pwmcp test fixture serving a guest's VNC socket to its browser | open | after VML-05 |

   Routes, state 2026-11-12 late: luna answers again (controller's probe);
   deepseek still returns HTTP 402 (quota). Probe a route with a one-line job
   before relying on it. Implementation on luna, review by a Sonnet agent;
   Sonnet implements when no Codex route works.

   **Neither screen reader works in this devcontainer yet** (open, needs an
   operator decision): the exact-text reader needs `vmscreen` installed
   (`pip install` of `libraries/vmscreen`, editable is enough; it does not run
   from a bare source tree and is not in the release machinery, VMS-BL-01),
   and the OCR reader needs pwmcp's sidecar enabled (`pwmcp.ocr.enabled`).
   `console text` and `console wait-for-text` are unit-tested against fakes
   only. First real run: VML-04 or experiment E1.

   `libraries/vmscreen` (exact console text, `wait-text`, `send`, `ocr-pair`)
   was found only after VML-02; see the correction in the design guide,
   section 9.1. Read its README before writing the VML-03 brief.

   Review follow-ups of VML-02, VML-03b and VML-06a were all folded into VML-07
   (merged 2026-11-17). Left open: the key table exists in the VM tool and in
   vmscreen (VMS-BL-03, rule "same names" documented, no mechanical check); a
   `start` killed by SIGTERM or SIGKILL leaves a hidden `.<name>.partial` to
   remove by hand.

   **First real guest, 2026-11-17** (Debian 13 genericcloud, raw base in the CoW
   `downloads/vm-images`, state in the CoW `scripts/debian-rollback/vm-lab/`,
   QEMU image `debian-install-vm:cb76f8b5350b`, network `vbpub-cmqemn-network`,
   1 GiB, 2 CPUs, TCG): the raw reflink clone through `start` worked on real
   btrfs (GNU `cp` in this container); the guest reached the login prompt on
   ttyS0 in about 45 s; `disk promote` refused a running guest, then promoted the
   stopped guest's disk with all extents shared with the source; graceful
   `stop` worked. `console text` found no exact-text source (UEFI shows a
   graphical framebuffer, not the VGA text buffer) and the OCR engine failed
   (sidecar off): the serial-log reader (VML-04) is the way to read the screen,
   as planned. Still not verified live: QEMU 11's `screendump` writing into the
   recorder's pre-created file in place, the recorder itself, `console
   wait-for-text`, `console sendkey`.

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
