# Host daemon socket design

The host daemon is a root systemd service that exposes read-only telemetry to
approved local clients. Its public listener is owned by
`topos-observe.socket`; the service receives the named `observe` descriptor
from systemd and serves `/run/topos/observe/topos.sock` without binding,
chmodding, probing, or unlinking the socket node.

## Socket and state layout

The socket lives under `/run/topos/observe/`. Systemd creates its parent and
socket with `root`, the configured observe group, and the configured mode
(default `0660`). The daemon's private data lives separately under
`/run/topos/state/`, created as `root:root` mode `0700`. Keeping these paths
separate means socket readers do not gain access to daemon state files.

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

There is no compatibility listener or client fallback. The old
`/run/topos/topos.sock` path is no longer served, and the former BPF and squeeze
state paths are no longer used. A host upgrading to this layout needs a fresh
install. `toposd install`, `uninstall`, and `doctor` are coming in the same
release and are not part of this package.
