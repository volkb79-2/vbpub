# Consuming the topos host daemon

## Configure and render the service

Install the config below as `/etc/topos/toposd.toml`:

```toml
state_dir = "/run/topos/state"

[sockets.observe]
path = "/run/topos/observe/topos.sock"
group = "topos-observe"
mode = "0660"
```

Validate it and render the four files:

```bash
toposd config check --config /etc/topos/toposd.toml
toposd unit print --config /etc/topos/toposd.toml
```

`unit check` compares the socket and service under
`/etc/systemd/system/`, the slice in the same directory, and the sysusers file
at `/etc/sysusers.d/topos.conf`. To check a staged root tree, pass
`--root /path/to/root`.

Install a release with the topos wheel, cli-extended wheel, and daemon lock
pins. Pins use `<version>@sha256:<64 lowercase hex>`; the daemon-lock version
must match the topos wheel. Observe access is a group of its own, `topos-observe`
by default: add every operator account that needs it (login user, runner) with
`--add-user observe=USER` (repeatable), and give a container the group through its own group setting
(a numeric `group_add` of the host gid). The `docker` group is never an observe
group:

```bash
sudo toposd install --dry-run \
  --wheel 0.8.0@sha256:<topos-wheel-sha256> \
  --cli-extended-wheel 0.5.3@sha256:<cli-extended-wheel-sha256> \
  --augeaswrap-wheel 0.1.0@sha256:<augeaswrap-wheel-sha256> \
  --daemon-lock 0.8.0@sha256:<daemon-lock-sha256> \
  --wheel-source https://github.com/volkb79-2/vbpub/releases/download \
  --add-user observe=alice --add-user observe=bob --enable
sudo toposd install --yes \
  --wheel 0.8.0@sha256:<topos-wheel-sha256> \
  --cli-extended-wheel 0.5.3@sha256:<cli-extended-wheel-sha256> \
  --augeaswrap-wheel 0.1.0@sha256:<augeaswrap-wheel-sha256> \
  --daemon-lock 0.8.0@sha256:<daemon-lock-sha256> \
  --wheel-source https://github.com/volkb79-2/vbpub/releases/download \
  --add-user observe=alice --add-user observe=bob --enable
sudo toposd doctor --json
set -a
. /etc/host-endpoints.env
set +a
topos daemon status
```

The release base resolves assets under the `topos-v<version>` tag. A local
`--wheel-source` directory may be used instead; it must contain the three explicitly
pinned wheels (topos, cli-extended, augeaswrap), the pinned lock, and every
additional runtime wheel named by the lock. `augeaswrap` is on no package index,
so it is never in the lock. The
installer writes `/etc/topos/install.toml` with those pins, the source, observe
group, requested users, enable choice, and the groups it created. A mapped
group must already exist, and `docker` is refused (also in `toposd.toml`).

If `/etc/topos/toposd.toml` already selects an observe group, omit
`--observe-group` from both install commands above: the file's group is used and
saved into the install record. If the flag is supplied, it updates only
`sockets.observe.group`; the dry-run result reports that field when it changes.
With no config group, install record, or flag, a non-interactive install fails
with a request to supply `--observe-group`.

The service receives its listener from `topos-observe.socket`. The configured
`state_dir` is private to root (mode `0700`) and becomes the systemd runtime directory. The
daemon writes BPF snapshots under `state_dir/bpf` and squeeze recovery records
under `state_dir/squeeze`. `toposd run --bpf-state-dir PATH` overrides the
BPF snapshot directory. `topos squeeze` and `topos squeeze --recover` use
the same service config; an absent default file selects `/run/topos/state`,
and an invalid file is reported as a config error.

## Run for development

For tests or development without systemd socket activation, bind an explicit
socket with `--self-bind`:

```bash
toposd run --self-bind --socket /tmp/topos-observe.sock \
  --config /etc/topos/toposd.toml
```

`--socket` requires `--self-bind` and overrides `sockets.observe.path` for
that run. Production `toposd run` uses the configured path and requires the
systemd-passed `observe` descriptor.

## Connect a client

`toposd install` publishes the configured observe socket and group ID through
`augeaswrap.endpoints` as `/etc/host-endpoints.d/topos.env`. The aggregate
`/etc/host-endpoints.env` is loaded into login sessions. A process launched
outside a login session can load it explicitly:

```bash
set -a
. /etc/host-endpoints.env
set +a
```

Clients require `TOPOS_OBSERVE_SOCKET`; they do not guess a socket path. With
the endpoint environment available, use:

```bash
topos --attach --once --json
topos daemon current --pretty-json
topos mcp serve
```

An unset `TOPOS_OBSERVE_SOCKET` is a usage error naming the missing variable
and exits with code 2. For explicit overrides, use `--socket PATH` with a
daemon command or `topos mcp serve`, `--daemon-socket PATH` with
`topos gateway serve`, or `--attach PATH` with `topos`. The client
account needs permission to connect to the configured socket through its
group. Use `toposd doctor --check endpoint-record` to verify the published
record and `topos daemon status` for client-side socket and protocol status.
Inside a container it accepts any socket group, because the host's group names
usually do not resolve there; `--group NAME` also requires that group on the
host. Uninstall without removing group memberships by default:

```bash
toposd uninstall --dry-run
sudo toposd uninstall --yes
```

`--remove-groups` removes only groups listed as created by `toposd install` in
the install record. If group removal fails, the command prints which group was
kept, exits 2, and leaves the record; remove that group with `groupdel` and
delete the record by hand, because the `toposd` that ran is gone with its version tree.
The old path `/run/topos/topos.sock` is no longer served.

## Manual removal of an old installation

There is no automatic migration, repair, or chmod of the old layout. If
`toposd install` refuses an old installation, run the following commands and
recreate containers that bind-mount `/run/topos`:

```bash
sudo systemctl stop topos.service
sudo systemctl disable topos.service
sudo rm -f /etc/tmpfiles.d/mdt-topos.conf
sudo rm -f /etc/systemd/system/topos.service.d/50-mdt-socket-group.conf
sudo rm -f /etc/systemd/system/topos.service /etc/systemd/system/topos.slice
sudo systemctl daemon-reload
sudo rm -rf /run/topos
```
