# downloads

Large, immutable inputs shared by all projects of this repository. Put a sha256
(or the upstream checksum file) next to each file so it can be verified without
refetching.

| Folder | Holds | Notes |
|---|---|---|
| `iso/` | ISOs | not compressed (already compressed data) |
| `vm-images/` | qemu base images: cloud images, installed golden disks | **raw preferred on btrfs** (convert a fetched qcow2 once: `qemu-img convert -O raw -S 4k`); compressed (`zstd`); read-only after creation; a VM's disk is `cp --reflink=always` of one of these into `<project>/vm-lab/<vm>/` |
| (top level) | other fetched inputs | |

Fetched third-party inputs and base images that several projects start from
belong here. A VM's own disks, runs and state are the project's, in
`<project>/vm-lab/`; reports and other build output go in `<project>/artifacts/`.
See AGENTS.md "Directory conventions and VM files". Files that predate the
subfolders (the Debian genericcloud qcow2, the TrueNAS ISO) move into them when
their consumers are updated.

Everything in this folder except this README and `.gitignore` is ignored and
regenerable by re-downloading; do not commit its contents. Scripts verify the
checksum before use and take a lock while fetching, so concurrent runs are safe.

This folder may be a plain directory, a CoW subvolume or a CoW clone of the main
checkout's folder (created by `ciu worktree`). Without a clone a fresh checkout
or worktree starts empty and the scripts download again.
