# iso

Installer and other ISOs shared by all projects of this repository: the `iso` entry of the repo path config
(`cowtree.toml`, owner: the cowtree path standard, `libraries/cowtree/SPEC.md` "Repo paths"). Each ISO has a
`<file>.sha256` next to it; not compressed (ISOs are already compressed data).

This directory is the plain-mode location of the `iso` entry and a committed placeholder: it exists after a fresh clone
or worktree, so scripts do not have to create it. On a CoW host (`MDT_COW_ROOT` set) the resolver
(`libraries/cowtree/repo-path iso`) points at the btrfs subvolume `$MDT_COW_ROOT/<repo-name>/downloads/iso` instead, this
directory stays empty, and `libraries/cowtree/repo-path iso --provision` creates that subvolume once. Scripts take the
folder from the resolver, never from this literal path.

Everything in this folder except this README and `.gitignore` is ignored and regenerable by downloading again (the
fetching scripts verify the checksum and take a lock). Do not commit its contents.
