# vm-lab

State of the debian-install-v2 QEMU/TCG test-VM harness
(`testing/vm/run-vm-harness.sh`, `testing/vm/vmctl`).

- `images/` staged base images (`<case>-base.qcow2`), written once and never
  replaced by the tooling; overlays use them as backing files.
- `runs/<name>/` one disposable VM per directory: qcow2 overlay, cloud-init seed,
  QMP socket, pidfile, serial log.
- `ssh/` the test-only SSH key for the guests.
- Private NoCloud seed ISOs for `start --seed-iso` may be kept here too (they can
  carry credentials; that is why everything here is ignored).

Everything in this folder except this README and `.gitignore` is ignored and
regenerable (`vmctl prepare-base` fetches the cloud image into the repo's
`downloads/` and stages it here). Do not commit its contents.

Owner: the checkout's user. The harness runs `vmctl` as the owner of this folder
and refuses a root-owned one. This folder may be a plain directory, a CoW
subvolume mounted over it, or a CoW clone of the main checkout's folder (created
by `ciu worktree`); the scripts do not care. Without a clone a fresh checkout or
worktree starts empty and the harness stages again.
