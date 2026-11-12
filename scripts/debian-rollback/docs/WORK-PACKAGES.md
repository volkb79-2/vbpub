# Work packages to finish the project

Written 2026-11-12 by the controller session (Fable) as the hand-over plan.
It covers everything from today's state to a released product. Read
[DESIGN-GUIDE.md](DESIGN-GUIDE.md) first for decisions D1 to D16 and questions
Q1 to Q9, and [EXPERIMENTS.md](EXPERIMENTS.md) for scenarios S1 to S12 and
variants V0 to V4. [../TODO.md](../TODO.md) keeps the live state table; this
file is the plan behind it. When a package is done, update the table in
`TODO.md`, not this file, unless the plan itself changes.

Contents: 1 state, 2 who does what (model routing), 3 frontier decisions,
4 operator prerequisites, 5 process per package, 6 to 13 the packages by
track, 14 order and dependencies, 15 risks, 16 definition of done.

## 1. State on 2026-11-12

On `main`:

| Item | Where | Note |
|---|---|---|
| Research, decisions, experiment plans | `scripts/debian-rollback/docs/` | Q4 answered, Q5 partly |
| General VM tool | `scripts/vm-lab/` | start/stop/resume, UEFI, console keys, reset, recorder, typing, `console text`, `console wait-for-text` |
| Exact console text library | `libraries/vmscreen/` | has `--qemu-dump-dir` for container guests; not installed anywhere |
| TrueNAS front of the VM tool | `scripts/truenas/tcg_vm.py` | behaviour unchanged |

Not done, in one sentence each:

- No real guest has been started by this project. Everything in the VM tool
  since the extraction is unit-tested against fakes only.
- Neither screen reader works in the devcontainer (vmscreen not installed,
  pwmcp's OCR sidecar off).
- No installer, no snapshot hook, no boot-menu generator, no experiment run.
- The r1002 lease is agreed but not started.

## 2. Who does what

Four tiers. The estate rules stay in force: implementation and review on
different routes; one frontier review per tool at most; every merged change
gets an independent review whatever its size.

| Tier | Model | Use for |
|---|---|---|
| Frontier | Fable or Opus as the controller session | The decisions in section 3; writing every brief; reading review reports and deciding each finding; merges; all work on a real host; interpreting experiment results; talking to the operator and the other controller session |
| Reviewer | Sonnet subagent (fresh, never a fork), or a fresh luna session when the implementer was not luna | Adversarial review against the brief, with file and line evidence; delta reviews |
| Implementer | Codex luna through `scripts/controller/codex-impl.sh` (default); deepseek when its quota is back (`CODEX_ROUTE=deepseek`); Sonnet subagent when no Codex route answers | Implementation from a brief that leaves no design choice open; fix rounds |
| Mechanical | Whichever implementer route is cheapest | Doc moves, renames, regenerating generated files, backlog entries |

What experience from VML-01 to VML-03b says about the tiers:

- A brief that names every decision produced mergeable work in two rounds. A
  brief with a gap produced a wrong guess each time (the empty environment
  variable rule, the "runs from source" assumption). Close gaps in the brief.
- Implementers on every route write tests that pass without proving the
  behaviour. Ask the reviewer, for each new test, "would it fail if the fix
  were reverted", and for surviving mutations.
- Unverified work from a route that died mid-run compiled and looked complete
  but had a defect that made its main feature dead from the real command line.
  Never merge on "it compiles and the lane is green" without the review.
- Small fixes after a MERGE-with-follow-ups verdict are cheaper done by the
  controller directly than through another round, but they still need the
  reviewer's eyes or must be exactly the fix the reviewer proposed.
- Probe a Codex route with a one-line job before a package depends on it.
  Routes went down twice in one day (usage limit, then quota).

## 3. Decisions that need the frontier model

Each has a point in the plan where it must be taken, the facts it depends on,
and the current recommendation, so that a session without frontier access can
still proceed on the recommendation and flag it as provisional. None of these
should be delegated to an implementer inside a brief.

| # | Decision | Taken in | Depends on | Recommendation today |
|---|---|---|---|---|
| F1 | How tests read a UEFI guest's screen | VML-04 | Under UEFI there is no VGA text buffer, so vmscreen's exact VGA source does not apply to any guest of this project (D4). Exact text exists only on the serial line; graphical screens need OCR | Serial log is the primary reader; OCR is the fallback for screens that never reach serial; the VGA engine stays for BIOS consumers only |
| F2 | How tests assert on the inside of a guest (which state booted, is the package there, is the marker file there) | VML-05 | Screen text is the wrong tool for this. Options: QEMU guest agent, a root shell on the serial line with a marker protocol, SSH through a forward | QEMU guest agent in the test images, new verb `guest exec`: gives exit code and output without parsing a terminal. The product itself never needs the agent |
| F3 | How a boot menu is driven and verified | DR-10 | Curses menus on a serial line are a stream of cursor movements, not readable lines. A menu choice can be made blind with keys or set from inside the guest (one-shot entry) | Drive with keys after `wait-for-text` on a stable banner line; verify the result inside the guest (F2), never from the menu's rendering; keep one scenario per bootloader that checks the listed entries by OCR or serial as far as it goes |
| F4 | Cut the bootloader matrix before E1 | DR-10 | Four ZFS options and two btrfs options times twelve scenarios under emulation is weeks | Test four: btrfs with GRUB, btrfs with systemd-boot, ZFS with ZFSBootMenu, ZFS with GRUB on one restricted pool. Drop the two-pool ZFS layout unless the single pool fails |
| F5 | Where GRUB's menu and environment file live | DR-21 | Debian's signed GRUB reads a small config on the ESP that jumps into `/boot/grub` of one fixed subvolume or dataset. After a rollback that path names the wrong state, and GRUB 2.12 cannot write its environment on btrfs | Keep the real menu and `grubenv` on the ESP (FAT, writable, state-independent); kernels and initramfs stay inside each state. This also removes the Debian 13 limitation for phase 2. Verify that the signed GRUB image contains the btrfs and zfs modules |
| F6 | Rollback mechanics on btrfs (Q3) | DR-20 | Read-only snapshot with overlay needs initramfs work Debian lacks | Always boot a writable copy of the snapshot; no overlay |
| F7 | btrfs subvolume layout and how snapper is used | DR-20 | snapper expects `.snapshots` nested under the root subvolume; a flat layout rolls back more cleanly; `snapper rollback` assumes the openSUSE layout and default-subvolume switching | Flat layout with our own "make permanent" step; use snapper for taking, listing and pruning snapshots and for its apt hook; do not use `snapper rollback` unless E1 shows it works on this layout unchanged |
| F8 | Which parts of `/var/lib` stay outside the OS state | DR-01 | Section 3 of the design guide names examples only. Wrong placement either loses service data on rollback or lets dpkg state drift from `/usr` | Inside by default; outside by an explicit list in the install config (containers, databases, VM images, plus what the host profile adds). `/var/lib/systemd` and `/var/lib/dpkg` inside |
| F9 | Who owns the partition table (Q9) | DR-01 | `debian-install-v2` refuses mounts over `/`, `/var`, `/etc`, `/boot` and creates swap and a data area itself | Our installer owns the whole table and writes a layout file; `debian-install-v2` runs afterwards in a mode that only consumes the regions the file hands to it. Needs a reading of its code and one small change there |
| F10 | How the live system gets ZFS, and the rescue path | DR-02 | Provider rescue systems have no ZFS; building the module by DKMS inside a live session is slow and needs RAM | Bake the ZFS module for the live kernel into our ISO at build time. From a provider rescue system, load our live kernel and initrd with kexec instead of installing from the rescue system directly |
| F11 | Form of the product tool | DR-30 | The tool must work on a half-broken system. The estate norm is a `cli-extended` wheel | One Python file, standard library only, shipped as a `.deb`; ask the operator to accept the exception to the wheel rule, or decide otherwise |
| F12 | Health check for phase 2 (Q7) | DR-40 | What "booted well" means differs per host | A fixed core (reached `multi-user.target`, no failed unit from a configured list, default route, SSH listening) plus a hook directory; state it in config, default conservative |
| F13 | How pruning and kernel removal interact (Q6) | DR-32 | With kernels inside each state, removing a kernel package in the current state cannot break an older state; with kernels copied to the ESP it can, and the ESP is small | Keep N states by count and by ESP or pool free space; for ESP-copy designs pin copies per state and deduplicate by hash |
| F14 | Reading E1: is a candidate out | DR-19 | Counts of lines written, manual steps, failed scenarios | Written judgement with evidence, not a score |
| F15 | The E2 decision rule and reading its numbers | DR-50, DR-59 | Baseline spread is unknown until V0 has run | Operator confirms the rule before the run; the frontier model checks every run for outside disturbance before it counts |
| F16 | The filesystem and bootloader decision (D14, Q1, Q2) | DR-60 | E1 and E2 reports | Operator decides on a frontier-written recommendation |
| F17 | Every action on r1002 that destroys data, and the hand-back | DR-55 to DR-58 | Lease terms in EXPERIMENTS.md | Controller session runs or directly supervises; no unattended agent holds host credentials |
| F18 | Secure Boot and encryption scope for the first release (D10) | DR-33 | ZFS needs a locally enrolled key in any case; the GRUB environment block does not work on an encrypted btrfs | First release: Secure Boot supported for btrfs with signed GRUB or signed systemd-boot; ZFS documented as "enrol your own key"; encryption deferred with the reason written down |

## 4. What the operator must decide or provide first

| # | Item | Blocks | Default if no answer |
|---|---|---|---|
| OP1 | Install `vmscreen` in the devcontainer (editable from the repo, or through the release path, backlog VMS-BL-01) | VML-04 | none; the package cannot verify its serial reader without it |
| OP2 | Enable pwmcp's OCR sidecar (`pwmcp.ocr.enabled`) | OCR fallback in VML-04, corpus in VML-06 | proceed with serial only, mark OCR untested |
| OP3 | Start real guests on the shared host, and download Debian and Grml images (where to cache them) | all of track B onward | none |
| OP4 | Project name and final location (Q8) | DR-30 naming, the `.deb` name | keep `debian-rollback` as working name |
| OP5 | Confirm the E2 decision rule and its margin (F15) | DR-59 | run and report without a verdict |
| OP6 | Lease timing for r1002; tell the other controller session | track E | wait for "lease start" |
| OP7 | Accept F11 (standard-library tool as a `.deb`) | DR-30 | follow the recommendation, flagged |
| OP8 | Fix job for TN-15 and TN-16 (TrueNAS lanes red on `main`) | nothing here; it hides new TrueNAS failures behind three known ones | leave as is |

## 5. Process for every package

1. Controller writes the brief under `artifacts/debian-rollback/briefs/`:
   worktree and base commit, files to read first, every decision fixed, the
   tests each of which must fail when the behaviour is removed, the gate
   command, the report path, the rules (Edit tool only, no local pytest, no
   Docker or QEMU unless the brief allows it, nothing outside the named
   directories, commit trailer, a `description` on every shell call).
2. `ciu worktree create <id>`; one package per worktree; packages that touch
   the same files run one after another.
3. Implementer runs. Gate lanes only through
   `/home/vscode/vbpub-release-program-20261027/codex/gate-run.sh`, in the
   foreground. Before launching a Codex job check that anonymous memory in the
   devcontainer is under 5 GB; at most one implementation and one review job
   at a time; run names start with the package id.
4. `scripts/controller/job-check.sh` for Codex jobs; read the report.
5. Independent review on the other route. The reviewer gets the code and the
   brief, not the controller's opinion. It writes a report with a verdict line.
6. Fix round by resuming the implementer; delta review by resuming the
   reviewer. Stop after three rounds and rethink the brief.
7. Merge `origin/main` into the branch, rerun the lanes the change can affect,
   merge to `main` with `--no-ff` and a message naming lanes, head and review
   verdicts, push, `ciu worktree rm`.
8. Update `TODO.md`, file follow-ups in the owning backlog, tell the other
   controller session what changed for others.

Packages that start guests add: the image cache path, the admission limits of
the VM tool (at most three guests on the host, memory headroom), one guest at
a time for this project, and "act only on the guest named in the brief".

Sizes below: S is one implementer round of under an hour, M is one to two
rounds, L is several rounds or needs a real guest loop.

## 6. Track A: make the VM tool usable for UEFI guests

### VML-04 Serial reader and first real guest (M, needs OP1, OP3)

Goal: `console text` and `console wait-for-text` can read a UEFI guest, proven
on a real guest.

- Add engine `serial`: `vmscreen text --source serial --serial-log
  <state>/serial.log`, with vmscreen's mark tokens so a wait only sees text
  written after the wait began (or after a mark the caller passes). Engine
  order for `auto` becomes: `serial` when the guest was started with a serial
  log and the log has new text, then `vga`, then `ocr`. An empty serial result
  means "does not apply yet", not a match failure.
- `console mark` verb: prints a mark of the serial log for later waits.
- Start a Debian 13 cloud image (UEFI, serial console on by default) with the
  VM tool, wait for the login prompt on serial, read it with `console text`,
  record frames with `console record`, send a key, reset, wait again.
- Fetch script with pinned checksums for the images, cache outside the repo.
- Report: boot time under emulation, which engines produced what, every
  mismatch between the fakes and the real tools. Fix those mismatches.

Frontier: F1; reading the real-guest report. Acceptance: a recorded run, kept
under `artifacts/`, of "wait for login prompt" passing and of a wrong pattern
timing out with exit 3. Closes the "untested against a real guest" note for
VML-02 and VML-03b and vmscreen backlog VMS-BL-04 as far as serial goes.

### VML-05 In-guest command channel (M)

Goal: a test can run a command inside the guest and get exit code and output.

- Per F2: optional guest-agent device in the generated QEMU command
  (`--guest-agent`), socket in the state directory, verbs `guest ping` and
  `guest exec -- CMD...` with timeout, exit code passthrough, output limits.
- No change to guests started without the option; TrueNAS front unchanged.
- Real-guest check with the VML-04 image after installing the agent.

Frontier: F2 (confirm or replace the recommendation after reading how the
TrueNAS consumer reaches its guest today).

### VML-06 Disk checkpoints and failure injection (M)

Goal: scenarios start from a saved disk state instead of reinstalling, and can
break things on purpose.

- `disk checkpoint NAME` on a stopped guest: freeze the current overlay as a
  named backing image; `start --from-checkpoint NAME` creates a fresh overlay
  on it. Firmware variable store saved and restored with the checkpoint, since
  boot entries and one-shot settings live there.
- `disk list`, `disk drop`.
- Failure injection stays inside the guest through `guest exec` (truncate an
  initramfs, fill a filesystem); the VM tool only needs `console reset` (done)
  and power-off without shutdown (`stop --hard`, if not present).

### VML-07 Hardening follow-ups (S)

The open review items in `TODO.md`: handler inert during final cleanup; the
recorder's cleanup must not delete a foreign temp file; missing tests; time
budget re-decremented between calls of one poll; fall through from a broken
`vmscreen` on PATH; one key table (vmscreen backlog VMS-BL-03, decide which
copy stays).

### VML-08 Console screens into pwmcp's corpus (Y5) (M, needs OP2)

Run after DR-10 so the screens exist: installer, GRUB, systemd-boot,
ZFSBootMenu, emergency shell. Graphical screens through the recorder; where a
serial copy of the same instant exists store it beside the frame as reference
text. Deliver into pwmcp's corpus layout with its manifest.

### VML-09 One console interface, two backends (Y4) (L)

Needed before a rollback can be exercised on a rented host through the
provider's web console (DR-70). A small interface "screen text, send keys,
wait for text" with the QMP backend from the VM tool and pwmcp's browser
backend for a VNC canvas. Frontier: the interface itself, written in the
brief after VML-04 to VML-06 have shown what scenarios really call.

### VML-10 pwmcp test fixture with a guest's VNC socket (Y6) (M)

Lowest priority. Only pwmcp benefits; schedule when pwmcp work wants it.

## 7. Track B: the installer

### DR-01 Layout specification and install config (S, frontier writes it)

A document and a JSON schema, no code: partition table for both candidates
(ESP 1 GiB, swap partitions, root filesystem or pool, optional data area),
subvolume or dataset list with mount options, the inside/outside table of the
design guide made exact (F8), the layout file handed to `debian-install-v2`
(F9), names of states, what the first state is called. Review by a Sonnet
reader against the design guide for contradictions.

### DR-02 Live ISO that runs the installer unattended (L)

Modelled on `scripts/truenas/golden-iso` (read its three steps and its
tests). Debian Live, UEFI boot, ZFS module for the live kernel built at image
build time (F10), the install config and the installer on the image, result
reported on serial and by webhook, power-off at the end. Built in a
container; no loop devices on the host, no host namespaces. Unit tests for
the builder; the first real boot of the image is part of DR-10.

### DR-03 Installer core for btrfs (L)

One program run from the live system: partition, make filesystems, create the
layout of DR-01, bootstrap Debian 13 (mmdebstrap), configure fstab, hostname,
network, users and SSH keys from the config, install kernel and bootloader,
install the snapshot tool and hook (DR-20), write the layout file. Idempotent
checks before destroying anything: refuses a disk that is not the one named
in the config by size and serial. Runs with stable and with backports kernel.

### DR-04 Installer core for ZFS (L)

Same program, second backend: pool creation with fixed properties, datasets
of DR-01, `zfs-dkms` in the target for both kernels, hostid, cachefile, ZFS in
the initramfs, bootloader per variant (DR-22, DR-23). Records the exact pool
feature set.

### DR-05 Rescue-system path (M, after DR-02)

From a provider rescue system: fetch our live kernel and initrd, kexec into
them with the config on the kernel command line or fetched by URL (F10).
Tested in the VM lab by booting Grml and running the same steps.

### DR-06 `debian-install-v2` as the configuration stage (M)

The one change F9 needs there, with its own gate, plus a test that our layout
file is honoured and that nothing mounts over the protected paths.

## 8. Track C: the rollback machinery, both candidates

Each of these is installed by the installer and is also the first cut of the
product (track F reuses the code).

| Package | Content | Size |
|---|---|---|
| DR-20 | btrfs: snapper configuration for the flat layout (F7), apt hook check for manual and unattended runs, "make permanent" as a writable copy becoming the default (F6) | M |
| DR-21 | GRUB menu generator on the ESP for btrfs states: one entry per state with its own kernel, initramfs and `rootflags=subvol=`, default and one-shot through `grubenv` on the ESP (F5) | M |
| DR-22 | systemd-boot variant for btrfs: kernels copied to the ESP per state, entries per state, deduplication, boot counting left off until phase 2 | M |
| DR-23 | ZFS: apt hook that snapshots the active boot environment; clone, activate, promote commands; ZFSBootMenu built from upstream release with a pinned checksum, installed on the ESP, its properties set | L |
| DR-24 | ZFS with GRUB on one `compatibility=grub2` pool: same generator as DR-21 with a ZFS backend | M |

Frontier writes each brief from DR-01 and the E1 scenario list; F5, F6, F7 are
taken before DR-20 and DR-21 start. DR-21 and DR-24 share one generator with
two backends: write DR-21 with that in mind.

## 9. Track D: experiment E1

### DR-10 Scenario runner and first install (L, needs VML-04 to VML-06, DR-02, DR-03)

A runner in `scripts/debian-rollback/testing/` that drives one scenario:
start from a checkpoint, steps as data (wait for text, send keys, guest exec,
reset, expect), evidence per step (serial excerpt, frame, command output),
verdict file. S1 for the btrfs GRUB variant as the first scenario; checkpoint
"installed" afterwards. Local package repository on an extra disk or ISO with
small dummy packages in versions 1 to 30, so S2, S3 and S8 need no network
and take minutes. F3 and F4 are taken here.

Expect long runs: installation under emulation can take an hour or more.
Runs are background jobs with a watch; a run disturbed by host load is
inconclusive and repeated.

### DR-11 to DR-14 Scenarios per variant (L each, mostly waiting)

| Package | Variant | Scenarios |
|---|---|---|
| DR-11 | btrfs with GRUB | S1 to S12 |
| DR-12 | btrfs with systemd-boot | S1 to S6, S12 |
| DR-13 | ZFS with ZFSBootMenu | S1 to S12 |
| DR-14 | ZFS with GRUB, one pool | S1 to S6, S11 |

Also in these runs: Q5 on a Debian 14 guest (does `grub-install` set up the
btrfs environment block), Secure Boot through shim to systemd-boot and to
GRUB with OVMF's secure-boot firmware (Q4's open part), and the check that
the signed GRUB image has the modules F5 needs.

Implementer writes scenario data and fixes machinery; the frontier model
reads every failed scenario before anyone "fixes" it, because a failure here
is often a finding about the candidate, not a bug in the test.

### DR-19 E1 report (frontier, F14)

`docs/E1-REPORT-<date>.md`: per variant what was used unchanged, what was
written and how many lines, manual steps, scenario table with evidence paths,
surprises, and a recommendation on which variants go on.

## 10. Track E: experiment E2 on r1002

| Package | Content | Tier | Size |
|---|---|---|---|
| DR-50 | Workload script (the seven steps), collectors (PSI, ARC statistics, zswap, vmstat, per-step IO), result format, report generator; dry run in a guest for correctness only | implementer; frontier fixes the measurement definitions in the brief | L |
| DR-51 | Install configs for V0 (through `debian-install-v2` as today), V1, V2 with both data placements; V3 and V4 as parameter changes; each proven in the VM lab with DR-10's runner | implementer | M |
| DR-52 | Runbook: every command of the lease in order, time budget, cut order if time runs short (V2 both placements, V1, V0, then V3, V4), abort criteria, hand-back steps copied from EXPERIMENTS.md and checked against the controller's config | frontier | S |
| DR-55 | Lease: wait for "lease start"; install and run per runbook; action log to the program directory | frontier runs or supervises (F17) | L |
| DR-58 | Hand-back: reinstall with the controller's config, runner bring-up, runner smoke, report to the controller session; if it fails, leave the host in rescue and report exactly | frontier | M |
| DR-59 | E2 report with raw data paths, disturbed runs marked inconclusive, and the reading against the confirmed rule (F15) | frontier | M |

Do not start DR-55 unless DR-50 to DR-52 are merged and have passed a dry run.
A 48-hour lease does not leave room to debug the workload script on the host.

## 11. Decision

### DR-60 Filesystem and bootloader (operator, on a frontier recommendation, F16)

Inputs: DR-19, DR-59. Output: D14, Q1 and Q2 closed in the design guide, the
losing variants' code removed or parked with a note, and the scope of the
first release fixed (F18).

## 12. Track F: phase 1 product

| Package | Content | Size |
|---|---|---|
| DR-30 | The tool (F11, name from OP4): `list`, `status`, `snapshot`, `boot-once STATE`, `make-permanent STATE`, `prune`, `doctor`; backends from track C for the chosen candidate; exit codes and `--json` | L |
| DR-31 | apt integration: hook for manual and unattended runs, refusal with a clear message when the snapshot cannot be taken (S9), state description from the apt command line | M |
| DR-32 | Pruning and kernel retention (F13); warning when a data volume was written by a newer state than the one being booted | M |
| DR-33 | Secure Boot per F18; documented key enrolment where needed | M |
| DR-34 | Packaging as a `.deb`, installer installs it, release through the estate's release tool, version 0.1.0 | M |
| DR-35 | Documentation: README, consumer guide, "what rolls back and what does not", recovery from a state that does not boot, limits | M |
| DR-36 | Acceptance: S1 to S11 through the released package on a fresh install in the VM lab, with stable and backports kernel | L |

One frontier review of the tool as a whole before 0.1.0 (the per-tool frontier
review of the estate rules); everything else reviewed on the reviewer tier.

## 13. Tracks G and H: phase 2 and delivery

| Package | Content | Size |
|---|---|---|
| DR-40 | Health check and probation (F12): a new state boots once on trial; a unit after boot runs the check and makes it the default, or leaves the previous default in place. systemd-boot: boot counting and `systemd-bless-boot`. GRUB: one-shot entry in `grubenv` on the ESP and a promote step | L |
| DR-41 | Integration with `vbpub-reboot-check.timer` as the only reboot mechanism; no second timer | M |
| DR-42 | S12 and failure cases in the VM lab: check fails, check hangs, power loss during promotion | L |
| DR-70 | Acceptance on a rented host through the provider's web console with VML-09: install by ISO attach, upgrade, choose the earlier state in the boot menu, automatic fallback. Needs a host lease | L |
| DR-80 | Cloud image: capture the installer's output as an image, cloud-init grows it on first boot, first-boot identity (machine ID, SSH host keys, ZFS hostid) regenerated; tested in the VM lab with a larger disk | L |
| DR-81 | Later, optional, each its own decision: upgrade inside a clone; mirrored root; encryption | - |

## 14. Order and dependencies

```
OP1..OP3 ─► VML-04 ─► VML-05 ─► VML-06 ─► DR-10 ─► DR-11..14 ─► DR-19 ─┐
DR-01 ─► DR-02, DR-03, DR-04 ──────────────┘                            ├─► DR-60 ─► DR-30..36 ─► DR-40..42 ─► DR-70, DR-80
DR-01 ─► DR-20..24 ───────────────────────► (installed by DR-03/04)     │
DR-50, DR-51, DR-52 ─► DR-55 ─► DR-58 ─► DR-59 ────────────────────────┘
VML-07 any time; VML-08 after DR-10; VML-09 before DR-70; VML-10 last
```

Shortest path to the decision: VML-04, VML-05, VML-06 and DR-01 first (DR-01
needs no guest and can be written today). Then DR-02 with DR-03 and DR-20,
DR-21 for the btrfs GRUB variant, then DR-10 and DR-11. The ZFS side (DR-04,
DR-23, DR-13) follows on the same runner. Track E preparation (DR-50 to
DR-52) runs in parallel with track D because it touches other files; it must
be finished before the lease, so start it as soon as DR-10 exists.

If the lease comes before E1 is finished: E2 needs only DR-03 and DR-04 at
"installs and boots" level plus DR-50 to DR-52. Do not wait for the rollback
machinery to measure the filesystems.

Parallelism is limited by files, not by people: packages in `scripts/vm-lab`
are serial; installer backends for btrfs and ZFS can run side by side once the
shared core of DR-03 is merged; one guest of this project at a time.

## 15. Risks and what to do about them

| Risk | Effect | Handling |
|---|---|---|
| Emulation without KVM is slow | An install takes an hour or more; twelve scenarios times four variants is days of machine time | Checkpoints (VML-06), local dummy package repository, reduced matrix (F4), runs in the background with watches |
| Shared host under load from production and other sessions | Timeouts that look like failures | Verdicts independent of load; a disturbed run is inconclusive and repeated; never raise limits to get green |
| Serial text of curses menus is not readable as lines | Menu content cannot be asserted exactly | F3: assert outcomes inside the guest; menu rendering checked best effort |
| DKMS build of ZFS against the backports kernel fails | ZFS variant not installable with backports | That is a finding for D9, record it; the installer must fail clearly, and an earlier kernel must stay selectable |
| The signed GRUB image lacks a needed module | F5 falls back to an unsigned GRUB or to systemd-boot | Check first thing in DR-21 |
| ZFSBootMenu under emulation, kexec | May fail for reasons that do not apply on real hardware | Mark inconclusive, retest on r1002 during the lease if time allows |
| Lease too short | E2 incomplete | Cut order in DR-52; everything rehearsed in a guest first |
| Hand-back of r1002 fails | Other sessions lose a host | Rehearse the hand-back install in a guest; lease terms say leave in rescue and report |
| Codex routes go down again | Packages stall mid-run | Probe first; commit unfinished work as a labelled work-in-progress commit and hand to another implementer with "treat as untrusted, never executed" |
| Reviewer and implementer share a blind spot | A defect passes two rounds | The first real-guest run of each feature is part of its acceptance, not a later package |
| Tool installs in the devcontainer get replaced by other agents | A reader works one day and not the next | `which -a` and `pip show` before trusting a tool; the VM tool already probes vmscreen once per run |

## 16. Definition of done

1. D14, Q1 to Q9 are closed in the design guide with the evidence named.
2. The installer produces a booting Debian 13 with the chosen layout from the
   ISO, from a rescue system and as a cloud image, with stable and backports
   kernel.
3. Every apt run, manual or unattended, leaves a state the boot menu offers;
   S1 to S11 pass through the released package.
4. Phase 2: a state that fails its health check is not the default at the
   next boot (S12), driven by the existing reboot timer.
5. One rollback has been performed on a rented host through its web console.
6. The tool is released through the estate's release path with README and
   consumer guide, and the limits are written down: service data moves forward
   only, `/etc` goes back with the OS, what Secure Boot and encryption cover.
7. r1002 is handed back and the controller session has confirmed its smoke.
8. Backlogs hold every follow-up that was not done; no worktree of this
   project is left.
