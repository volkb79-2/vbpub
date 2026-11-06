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
must match the topos wheel. For mdt hosts that already use the `docker` group,
map observe access to that existing group:

```bash
sudo toposd install --dry-run \
  --wheel 0.7.3@sha256:<topos-wheel-sha256> \
  --cli-extended-wheel 0.5.2@sha256:<cli-extended-wheel-sha256> \
  --daemon-lock 0.7.3@sha256:<daemon-lock-sha256> \
  --wheel-source https://github.com/volkb79-2/vbpub/releases/download \
  --observe-group docker --observe-users alice,bob --enable
sudo toposd install --yes \
  --wheel 0.7.3@sha256:<topos-wheel-sha256> \
  --cli-extended-wheel 0.5.2@sha256:<cli-extended-wheel-sha256> \
  --daemon-lock 0.7.3@sha256:<daemon-lock-sha256> \
  --wheel-source https://github.com/volkb79-2/vbpub/releases/download \
  --observe-group docker --observe-users alice,bob --enable
sudo toposd doctor --json
topos daemon status
```

The release base resolves assets under the `topos-v<version>` tag. A local
`--wheel-source` directory may be used instead; it must contain both pinned
wheels, the pinned lock, and all third-party wheels used by the lock. The
installer writes `/etc/topos/install.toml` with those pins, the source, observe
group, requested users, enable choice, and the groups it created. A mapped
group must already exist; `docker` is allowed.

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

The clients use `/run/topos/observe/topos.sock` by default. For example:

```bash
topos --attach --once --json
topos daemon current --pretty-json
topos mcp serve
```

The client account needs permission to connect to the socket through its
configured group. Use `toposd doctor --check NAME` for a focused host check and
`topos daemon status` for client-side socket and protocol status. Uninstall
without removing group memberships by default:

```bash
toposd uninstall --dry-run
sudo toposd uninstall --yes
```

`--remove-groups` removes only groups listed as created by `toposd install` in
the install record. The old path `/run/topos/topos.sock` is no longer served.

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
