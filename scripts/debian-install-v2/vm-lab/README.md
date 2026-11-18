# vm-lab

State of the debian-install-v2 QEMU/TCG test-VM harness
(`testing/vm/run-vm-harness.sh`, `testing/vm/vmctl`). Other projects that reuse the
harness (`MDT_VM_PROJECT_DIR`) keep their own `<project>/vm-lab/`; this one is the plain
fallback location of the `vm-lab` entry of the repo path config (cowtree; project
`scripts/debian-install-v2`). On a CoW host the resolver
(`libraries/cowtree/repo-path vm-lab --project scripts/debian-install-v2`) points at a
compressed btrfs subvolume instead, and the scripts do not care which. They take the
folder from the resolver, never from a literal path.

- `images/` staged base images (`<case>-base.qcow2`), written once and never
  replaced by the tooling; overlays use them as backing files.
- `runs/<name>/` one disposable VM per directory: qcow2 overlay, cloud-init seed,
  QMP socket, pidfile, serial log.
- `ssh/` the test-only SSH key for the guests.
- Private NoCloud seed ISOs for `start --seed-iso` may be kept here too (they can
  carry credentials; that is why everything here is ignored).

Everything in this folder except this README and `.gitignore` is ignored and
regenerable (`vmctl prepare-base` fetches the cloud image into the `vm-images` folder of
the repo path config, `downloads/vm-images/` in plain mode, and stages it here). Do not
commit its contents: this placeholder `.gitignore` is the only thing that keeps VM disks
out of `git status`.

Owner: the checkout's user. The harness runs `vmctl` as the owner of this folder
and refuses a root-owned one. A fresh checkout or worktree starts with this empty
folder unless a CoW clone of the main checkout's folder exists; the harness then stages
again.
