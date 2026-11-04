# tnt vm-lab

Local state of the TrueNAS test VMs owned by tnt, one folder per VM (`<vm-name>/`): installed disks, OVMF variable
stores and serial logs.

- Everything here except this README and `.gitignore` is gitignored and regenerable (reinstall from the ISO in
  `<repo root>/downloads/`, which keeps ISOs with their `.sha256`); never commit it.
- Owner: tnt. pwmcp's console-capture VM is a separate VM owned by pwmcp; it references the ISOs by explicit path and keeps
  no copy of tnt's VM state.
- Paths are derived from the script's own location. There is no environment variable that relocates this folder.
- Planned: declare this folder to cowtree (COW-BL-01) as a CoW-managed path once the declaration schema exists, so worktrees
  snapshot main's content on CoW hosts and start with an empty folder elsewhere.
- This folder may be a CoW subvolume or a clone of main's (see the root `AGENTS.md`, "Directory conventions and VM files").
