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
listener. `toposd install`, `uninstall`, and `doctor` are not part of this
package.
