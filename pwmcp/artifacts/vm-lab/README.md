# pwmcp vm-lab

Local VM state owned by pwmcp's console-capture tooling (`tools/console-capture/launch-truenas-vm.sh`): the
installed-TrueNAS test VM (`<vm-name>/disk.qcow2`, `OVMF_VARS.fd`, `serial.log`, `qemu.pid`) and similar.

- Everything here except this README and `.gitignore` is gitignored and regenerable (reinstall from the ISO in
  `<repo root>/downloads/`); never commit it.
- Owner: pwmcp. Other projects must not keep state for these VMs here.
- This folder may be a CoW subvolume or a clone of main's (see the root `AGENTS.md`, "VM files and downloads").
