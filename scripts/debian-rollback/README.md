# debian-rollback (working name)

**Status: planning. No code, no installer, nothing tested yet.** Started
2026-11-12. This directory holds the research, the operator's decisions and the
plan, written so the work can be resumed without the original conversation.

## What this project is for

`apt upgrade` changes files, configuration and services on a running host, and
a new kernel makes a failed reboot more likely. Stock Debian has no way to get
back to the state before the upgrade. This project gives a Debian install a
safety belt:

- a snapshot of the operating system is taken automatically before every
  package change;
- an earlier state can be chosen in the boot menu and booted;
- user files and the data of installed services are not rolled back.

It is a product built from existing parts (Debian, an existing bootloader, an
existing snapshot tool), not a new distribution. Where Debian has a gap, this
project closes the gap and packages the result as an installer and an image.

## Documents

| Document | Holds |
|---|---|
| [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md) | Goals, the operator's decisions, the two candidate designs, what is rejected and why, open questions |
| [docs/RESEARCH-2026-11.md](docs/RESEARCH-2026-11.md) | Everything found in the November 2026 research, with sources and how far each claim was verified |
| [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | Planned experiments: the VM lab scenarios and the real-host filesystem comparison, with the host lease terms |
| [TODO.md](TODO.md) | Ordered next steps |

## How to resume

1. Read the "Decisions" and "Open questions" sections of the design guide.
2. Read `TODO.md` for the next step.
3. Before touching a host, read the lease terms in `docs/EXPERIMENTS.md`.
   Hosts r1002 and v1001 are coordinated by the Claude session
   `vbpub-controller-joint-release`; nothing may be done on r1002 before that
   session sends "lease start", and v1001 is never touched.

## Name and location

`scripts/debian-rollback/` is a working home next to `scripts/debian-install-v2`
(the existing installer) and `scripts/truenas` (whose VM launcher and
unattended-ISO builder this project reuses). The name is not decided; renaming
is one `git mv`. The planned general VM tool gets its own home,
`scripts/vm-lab/`, see the design guide.
