# tnt vm-lab

Local state of the TrueNAS test VMs owned by tnt, one folder per VM (`<vm-name>/`): installed disks, OVMF variable
stores and serial logs. This is the plain fallback location of the `vm-lab` entry of the repo-root `vm-lab.toml`
(project `truenas/tnt`); on a CoW host the resolver (`libraries/cowtree/repo-path vm-lab --project truenas/tnt`)
points at a compressed btrfs subvolume instead, and tnt does not care which. tnt takes the folder from the resolver
(`src/tnt/_paths.py`), never from a literal path.

- Everything here except this README and `.gitignore` is gitignored and regenerable (reinstall from the ISO in the
  `iso` folder of `vm-lab.toml`, `downloads/iso/`, which keeps ISOs with their `.sha256`); never commit it. This
  placeholder `.gitignore` is the only thing that keeps VM disks out of `git status`.
- Owner: tnt. pwmcp's console-capture VM is a separate VM owned by pwmcp; it references the ISOs by explicit path and
  keeps no copy of tnt's VM state.
- The only environment input is `MDT_COW_ROOT`, read by the resolver; no other variable relocates this folder.
- A worktree shares the main checkout's CoW folders; its plain fallback is this folder inside the worktree (see the
  root `AGENTS.md`, "Directory conventions and VM files").
