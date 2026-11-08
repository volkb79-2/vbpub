# Host daemon socket design

The host daemon is a root systemd service that exposes read-only telemetry to
approved local clients. Its public listener is owned by
`topos-observe.socket`; the service receives the named `observe` descriptor
from systemd and serves `/run/topos/observe/topos.sock` without binding,
chmodding, probing, or unlinking the socket node.

## Socket and state layout

The socket lives under `/run/topos/observe/`. Systemd creates the parent
directory as `root:root` mode `0755` through `DirectoryMode=0755`; only the
socket node gets the configured observe group and mode (default `0660`). The
daemon's private data lives under `state_dir`, created as `root:root` mode
`0700` through `RuntimeDirectoryMode=0700`. Keeping these paths separate
means socket readers do not gain access to daemon state files.

The configured `state_dir` also owns the daemon's BPF snapshot directory
(`state_dir/bpf`) and squeeze recovery directory (`state_dir/squeeze`).
`toposd run --bpf-state-dir PATH` overrides the BPF directory. The BPF
`state_dir` in the collector config does not select daemon output. Both
`topos squeeze` and `topos squeeze --recover` load the service config; an
absent default file uses the built-in `/run/topos/state`, while an invalid
file is an error.

The service uses `Requires=` and `After=` for the socket unit. It does not use
`PartOf=`: restarting the daemon must not restart the socket unit and replace
the inode that clients may already have open. `RemoveOnStop=no` also preserves
the socket node when the service stops; systemd remains its owner.

## Explicit self-bind

`toposd run` adopts only a listening Unix stream descriptor named `observe`.
It refuses to start when systemd did not pass that descriptor. Tests and local
development can opt into `toposd run --self-bind`; that mode binds the config
path, probes and removes a stale socket, applies the configured socket mode,
and unlinks only the socket node it created during shutdown. It never changes
the parent directory's ownership or mode. `--socket PATH` is available only
with `--self-bind`.

## Configuration and rendered files

`/etc/topos/toposd.toml` is the single source for the state directory and the
observe socket path, group, and mode. With no default config file, built-in
defaults apply. Unknown keys fail closed. `toposd unit print` renders the
socket, service, slice, and sysusers files; `toposd unit check` compares those
same bytes with the installed files. The packaged systemd assets are the
default rendering and are checked against it.

The observe group defaults to `topos-observe` and is declared through
`topos.sysusers`. If configuration maps the level to another existing group,
the rendered sysusers file contains a comment and creates no group.

The daemon serves the configured observe socket and adopts its systemd-passed
listener.

## Installation and removal

`toposd install` builds an isolated, immutable versioned virtual environment
from a pinned topos wheel, cli-extended wheel, and hashed daemon dependency
lock. `/opt/topos/current` is switched atomically after the version has been
built; `/etc/topos/install.toml` records the exact pins and installer-created
groups. This keeps the host service independent of whichever Python happens to
be installed on the host. `toposd doctor` checks rendered files, group access,
systemd socket state, runtime paths, running version and socket connectivity.

The installer inspects but does not migrate or repair the former host-setup
layout. Automatic chmod, ownership repair, or partial removal could hide a
competing tmpfiles rule or a container bind mount. Install reports every
conflicting path and prints the concrete [manual removal steps](DAEMON.md#manual-removal-of-an-old-installation).
Uninstall removes the service and volatile runtime state; it keeps groups and
memberships unless `--remove-groups` is requested and the install record says
the installer created them.

On a non-interactive run, the group must come from an explicit flag, the
existing config, or the install record. This keeps unattended installs tied to
an existing operator choice and makes absence an actionable error. When the
flag is supplied, the installer patches only `sockets.observe.group`, keeping
other config lines and comments intact; without it, the file remains the
source of truth. The doctor service check compares the hello version and then
requires one activated listener, so a matching daemon started with development
self-bind cannot appear healthy.

Uninstall prints its result before deleting the version tree that contains its
running interpreter. It keeps `current` through that output, then removes the
running tree, pointer, `PENDING`, version root, record, and empty config
directory. Those ownership markers remain until the end so a partial finish can
be retried. The installer writes its complete record after sysusers creates a
group and before the readiness wait, which preserves the group's ownership on
an interrupted first install. A failed `groupdel` does not block service and
file cleanup; the record stays, and the operator runs `groupdel` and removes the
record by hand, because the `toposd` that ran is removed with its version tree.

## Host endpoint record and client resolution

The installer owns the configured observe socket and its group, so it
publishes those facts through `augeaswrap.endpoints` after writing the install
record. The shared endpoint writer handles collision checks, atomic fragments,
and login-shell export without duplicating that machinery in topos. A dry-run
shows the fragment plan; `toposd doctor --check endpoint-record` compares the
published values with the active service config. Uninstall withdraws topos's
fragment before stopping or removing the service.

Clients read `TOPOS_OBSERVE_SOCKET` from their process environment. The
login-session environment is populated from the aggregate endpoint file; a
non-login caller can source that file or pass an explicit socket path. Client
modules have no default socket path: a missing variable is an actionable
usage error, and an explicitly wrong path remains visible as a connection
failure. This keeps service configuration as the single source of the path and
lets host-setup change it without a topos client release.

The topos-specific differences from cgprofiled are listed in the
[daemon operator guide](DAEMON.md#differences-from-cgprofiled). They follow from
topos's single read-only observe socket, volatile-only daemon state, and its
explicit 0.7.0 socket-activation boundary.
