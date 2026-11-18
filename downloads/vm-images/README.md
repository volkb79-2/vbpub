# vm-images

Shared qemu base images (cloud images, installed golden disks) of this repository: the `vm-images` entry of the repo path
config (`cowtree.toml`, owner: the cowtree path standard, `libraries/cowtree/SPEC.md` "Repo paths"). Each image has its
`.sha256` (or the upstream checksum file) next to it; raw is preferred on btrfs (convert a fetched qcow2 once with
`qemu-img convert -O raw -S 4k`); read-only after creation. A VM's disk is a `cp --reflink=always` copy of one of these
into `<project>/vm-lab/<vm>/`.

This directory is the plain-mode location of the `vm-images` entry and a committed placeholder: it exists after a fresh
clone or worktree, so scripts do not have to create it. On a CoW host (`MDT_COW_ROOT` set) the resolver
(`libraries/cowtree/repo-path vm-images`) points at the compressed (`zstd`) btrfs subvolume
`$MDT_COW_ROOT/<repo-name>/downloads/vm-images` instead, this directory stays empty, and
`libraries/cowtree/repo-path vm-images --provision` creates that subvolume once. Scripts take the folder from the
resolver, never from this literal path.

Everything in this folder except this README and `.gitignore` is ignored and regenerable by downloading again (the
fetching scripts verify the checksum and take a lock). Do not commit its contents.
