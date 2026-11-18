# vm-lab

VM state of the mounttrace test lane (`testing/vm/run-vm-tests.sh`, which drives the shared harness
`scripts/debian-install-v2/testing/vm` with `MDT_VM_PROJECT_DIR=scripts/mounttrace`): `images/` (sealed bases such as
`mounttrace-ready-v2`), `runs/<name>/` (one disposable overlay per test round) and `ssh/` (the test-only guest key).

This is the plain fallback location of the `vm-lab` entry of the repo path config (cowtree; project `scripts/mounttrace`).
On a CoW host the resolver (`libraries/cowtree/repo-path vm-lab --project scripts/mounttrace`, add `--provision` once to
create the compressed subvolume) points at a btrfs subvolume instead; the scripts do not care which.

Everything in this folder except this README and `.gitignore` is ignored and regenerable (the first lane run rebuilds the
base, typically 15 to 40 minutes). Do not commit its contents: this placeholder `.gitignore` is the only thing that keeps VM
disks out of `git status`.

Owner: the checkout's user; the owning project is `scripts/mounttrace`.
