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

Install the rendered files through the normal package or configuration
management flow, then let systemd create the configured group and start the
socket and service:

```bash
systemd-sysusers
systemctl daemon-reload
systemctl enable --now topos-observe.socket topos.service
toposd unit check --config /etc/topos/toposd.toml
topos daemon preflight
topos daemon status
```

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
configured group. The existing `topos daemon preflight`, `install-plan`, and
`status` commands remain available. The `toposd install`, `uninstall`, and
`doctor` commands are not implemented by this CLI.
