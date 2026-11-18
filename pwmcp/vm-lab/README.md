# pwmcp vm-lab

Local VM state owned by pwmcp's console-capture tooling (see `tools/console-capture/README.md`): the
installed-TrueNAS test VM (`<vm-name>/disk.qcow2`, `OVMF_VARS.fd`, `serial.log`, `qemu.pid`) and similar. This is the
plain fallback location of the `vm-lab` entry of the repo path config (project `pwmcp`); the resolver
(`libraries/cowtree/repo-path vm-lab --project pwmcp`) answers with a CoW subvolume instead on a CoW host.

- Everything here except this README and `.gitignore` is gitignored and regenerable (reinstall from the ISO in the
  `iso` folder of the repo path config, `downloads/iso/` in plain mode); never commit it.
- Owner: pwmcp. Other projects must not keep state for these VMs here.
- This folder may be a CoW subvolume or a clone of main's (see the root `AGENTS.md`, "Directory conventions and VM files").
