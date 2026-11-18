# vm-lab

VM state of the `scripts/truenas` tools: one directory per VM, `<vm>/`. This is the plain fallback location of the
`vm-lab` entry of the repo path config (cowtree; project `scripts/truenas`); on a CoW host the resolver
(`libraries/cowtree/repo-path vm-lab --project scripts/truenas`) points at a compressed btrfs subvolume instead, and
the scripts do not care which. Scripts take the folder from the resolver (`scripts/truenas/repo_paths_access.py`),
never from a literal path.

- `tcg_vm.py` guests: `<vm-lab folder>/<name>/` is the default `--state-dir` (QCOW2 overlay, extra data disks, OVMF
  variables, QMP/VNC/serial sockets, serial log, `runner.json`). The base image and the installer ISO are inputs and
  are mounted read-only.
- Golden-image source VM (`golden-iso/step1-install-prepare-truenas.py`, `golden-iso/qmp.py`): its installed disk, UEFI
  variables, SSH key, `known_hosts`, QMP socket and console log live in `<vm-lab folder>/golden-source-27-20GiB/`.
- Installer ISOs go to the `iso` folder of the repo path config (`downloads/iso/`) and shared base images to `vm-images`
  (`downloads/vm-images/`), each with a `.sha256`; they are not kept here. Built outputs that are not VM state
  (patched ISOs, captured golden images with their secrets TOML, evidence logs) stay in the owning project's
  `artifacts/`.

Disk cache policy: run overlays and sparse data disks are disposable and use `cache=unsafe` (no guest fsync reaches
the host). The installed golden source disk is a base that later runs clone from and uses `cache=writeback`; never
`unsafe`.

Everything in this folder except this README and `.gitignore` is ignored and regenerable. Do not commit its
contents. It may hold credentials (SSH keys, OVMF state of a configured guest), which is why all of it is ignored:
this placeholder `.gitignore` is the only thing that keeps VM disks out of `git status`.

Owner: the checkout's user; the owning project is `scripts/truenas`. A fresh checkout or worktree starts with this
empty folder unless a CoW clone of the main checkout's folder exists.
