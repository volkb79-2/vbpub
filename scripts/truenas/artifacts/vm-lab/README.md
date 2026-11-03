# vm-lab

VM state of the `scripts/truenas` tools: one directory per VM, `<vm>/`.

- `tcg_vm.py` guests: `artifacts/vm-lab/<name>/` is the default `--state-dir`
  (QCOW2 overlay, extra data disks, OVMF variables, QMP/VNC/serial sockets,
  serial log, `runner.json`). The base image and the installer ISO are inputs and
  are mounted read-only.
- Golden-image source VM (`golden-iso/step1-install-prepare-truenas.py`): its
  installed disk, UEFI variables, SSH key, `known_hosts` and console log live in
  `artifacts/vm-lab/golden-source-<release>/`.
- Installer ISOs, the Debian live base and other fetched inputs are not kept
  here: they go to the repository's `downloads/` folder with a `.sha256`.
  Built outputs that are not VM state (patched ISOs, captured golden images with
  their secrets TOML, evidence logs) stay in the owning project's `artifacts/`.

Disk cache policy: run overlays and sparse data disks are disposable and use
`cache=unsafe` (no guest fsync reaches the host). The installed golden source
disk is a base that later runs clone from and uses `cache=writeback`; never
`unsafe`.

Everything in this folder except this README and `.gitignore` is ignored and
regenerable. Do not commit its contents. It may hold credentials (SSH keys,
OVMF state of a configured guest), which is why all of it is ignored.

Owner: the checkout's user. This folder may be a plain directory, a CoW
subvolume or a CoW clone of the main checkout's folder (created by
`ciu worktree`); the scripts do not care. Without a clone a fresh checkout or
worktree starts empty.
