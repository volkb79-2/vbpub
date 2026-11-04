# downloads

Large, immutable inputs shared by all projects of this repository: ISOs, cloud
images and similar downloads (for example the Debian genericcloud qcow2 used by
the debian-install-v2 VM harness). Put a sha256 (or the upstream checksum file)
next to each file so it can be verified without refetching.

Only fetched third-party inputs belong here. Anything a project builds itself (patched or golden
ISOs, restore images, VM disks, reports) is an artifact of that project and goes in its
`<project>/artifacts/`.

Everything in this folder except this README and `.gitignore` is ignored and
regenerable by re-downloading; do not commit its contents. Scripts verify the
checksum before use and take a lock while fetching, so concurrent runs are safe.

This folder may be a plain directory, a CoW subvolume or a CoW clone of the main
checkout's folder (created by `ciu worktree`). Without a clone a fresh checkout
or worktree starts empty and the scripts download again.
