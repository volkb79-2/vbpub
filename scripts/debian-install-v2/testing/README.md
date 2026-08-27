# Privileged test environment for inuse-partition-editor.py

`inuse-partition-editor.py`'s whole point is editing a partition table on a
disk that is currently in use — no unmount, no reboot. The parsing/planning
logic is covered by the ordinary (non-privileged) R0/R1 suite in
`debian_install_v2/tests/`, proven against real `sfdisk --dump` output on
plain regular files (no root needed for any of that). But two things can
only be tested with real root and a real kernel-backed block device:

- `Table.write(commit=True)` — refuses outright without `os.geteuid() == 0`.
- `partx --add` actually materializing `/dev/xxxN` partition device nodes,
  and `mkswap`/`swapon` activating them — the part of the tool that mirrors
  what happens on a real host after a reboot into stage2.

This directory provides a disposable, privileged, systemd-as-PID1 container
to exercise exactly that, without ever touching a host block device: every
test image here is a sparse file created inside the container's own tmpfs,
backed by a loop device, and discarded with the container.

## Why not the shared `tester-unified` image

`tester-unified` (`vbpub/tester-unified/Dockerfile`) is this repo's shared
gate image, but it runs as an unprivileged uid (1003), has no systemd, isn't
built with `--privileged`/cgroup access, and doesn't even `COPY`
`scripts/debian-install-v2` into its build context. Every one of those would
have to change to run this tool's real `--commit` path — that's the
"needs adaptation" case, so this gets its own small image instead of
bending a shared one built for a different job (plain Python dependency
closures for ciu/cmru/topos/nyxloom/cgroup-profiler).

## Quick start

```bash
scripts/debian-install-v2/testing/run-privileged-tests.sh
```

This builds the image (cached after the first run), boots the container,
waits for systemd to come up, runs the full R0+R1 suite inside it as root,
and always tears the container down afterward — regardless of the test
outcome. Pass pytest args to run a subset:

```bash
scripts/debian-install-v2/testing/run-privileged-tests.sh -k real_commit -v
```

Every test in the suite is safe to run this way — the fast fake-`run`
unit tests are just as harmless as root inside a throwaway container as they
are anywhere else. Only the tests gated on `os.geteuid() == 0 and
shutil.which("losetup")` change behavior here: everywhere else they're
skipped; inside this container they activate automatically.

## What the container needs, and why

- **`--privileged`**: needed for `CAP_SYS_ADMIN` (mounting devtmpfs, loop
  device ioctls) and unrestricted device access.
- **A real `devtmpfs` at `/dev`** (`docker-entrypoint.sh`, before exec'ing
  systemd): Docker's default `/dev` is a fixed tmpfs snapshot taken at
  container start (null/zero/random/tty/...) — it never gains new nodes as
  the kernel creates them. Without a real devtmpfs, `losetup --find` fails
  with `device node /dev/loopN (7:N) is lost` the moment it needs a loop
  minor that wasn't in that original snapshot, and `partx --add` has the
  same problem one level deeper for partition sub-devices. A real devtmpfs
  auto-creates nodes for anything the kernel knows about, with no udev
  involvement needed for the node itself.
- **Real systemd as PID 1**, not a bare `--privileged` shell: this
  container's whole reason to exist is rehearsing the actual deployment
  shape — stage2 is a systemd unit (`LoadCredential=`, `EnvironmentFile=`,
  a specific `WorkingDirectory=`/`ExecStart=`) that a real host starts after
  reboot. Running under real systemd is what would have caught the sibling
  `installer.py` review's unit-path bugs; a bash prompt never would. It also
  means `systemd-udevd` is genuinely running, so `udevadm settle` after
  `partx --add` does something real instead of being a silent no-op.
- **`fdisk` package, not just `util-linux`**: on Debian trixie, `sfdisk`
  moved out of `util-linux` into the separate `fdisk` package. `util-linux`
  alone gives you `losetup`/`partx`/`mkswap`/`swapon` but not `sfdisk` —
  easy to miss since every *other* tool this suite needs is still in
  `util-linux`.
- **`-v <host-path>:/work`, resolved via `docker inspect`, never a bare
  devcontainer path**: this devcontainer talks to the *host* Docker daemon
  (docker-outside-of-docker). Bind-mount sources are resolved in the
  daemon's own filesystem, not this container's, so a naive
  `-v $(pwd):/work` silently mounts an empty directory. The wrapper script
  resolves the real host path from this container's own recorded mounts
  before calling `docker run`.
- **Never bind-mount the host's real `/dev`.** Only a scratch directory is
  ever mounted in; every disk image the suite creates lives under the
  container's own `/tmp` (a `--tmpfs` mount, gone with the container). That
  isolation is the entire safety property of this setup — don't weaken it
  to "fix" a failing test.

## Known-harmless quirk: journald reports "degraded"

`systemd-journald.service` reliably fails to start in this container
runtime (`Error: code: 49 (Protocol driver not attached)` — an `EUNATCH`
from journald's socket setup, specific to this sandboxed docker-daemon
environment). `systemctl is-system-running` reports `degraded` as a result.
This has no effect on anything this suite exercises — `sfdisk`, `losetup`,
`partx`, `mkswap`, and `swapon` don't touch journald — so the wrapper script
treats "degraded" as ready rather than chasing it further.

## Manual / interactive use

```bash
docker build -f scripts/debian-install-v2/testing/Dockerfile \
    -t debian-install-privileged:local scripts/debian-install-v2
docker run -d --name ipe-test --privileged \
    --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    debian-install-privileged:local
docker exec -it ipe-test bash
# ... truncate -s 20G disk.img; sfdisk --force disk.img < plan; losetup --find --show --partscan disk.img ...
docker rm -f ipe-test
```
