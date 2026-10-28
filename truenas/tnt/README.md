# tnt (truenas-tui)

`tnt` is a keyboard driven TUI and scriptable CLI for the vanilla TrueNAS v27 JSON-RPC API. The core workflows are
Datasets and Snapshots, Containers, Virtual Machines, and local Users. Containers and Virtual Machines are separate
top level menu entries. Shares, Apps, networking, pool creation, and boot-pool changes are outside this release.

The CLI and TUI share one query and action layer. Every write is planned first, displays the exact API calls, and is
applied through the default deny guard. Destructive actions require typing the target name. See the
[design guide](docs/DESIGN.md) for the safety rules and v27 schema choices, and [API method evidence](docs/API-METHODS-V27.md)
for the verified method signatures. The original storage action matrix remains available in
[`docs/ACTION-MATRIX.snapshot.md`](docs/ACTION-MATRIX.snapshot.md).
Scope and safety choices are explained in the [design guide](docs/DESIGN.md#1-goals-and-non-goals); copyable setup and
workflow examples are in the [consumer guide](docs/CONSUMERS.md#configure-a-profile).

## Install

```bash
pip install 'tnt[tui]'        # the CLI alone: pip install tnt
```

`truenas_api_client` (LGPL-3.0, official) is installed from a pinned git commit; the pin rationale is in `pyproject.toml`.

## Configure a host

Copy [`tnt.toml.example`](tnt.toml.example) to `tnt.toml`, fill in one or more profiles, then set mode `0600`:

```bash
cp tnt.toml.example tnt.toml
chmod 600 tnt.toml
```

The selected file must be a regular file owned by the current user with exactly mode `0600`; tnt refuses to start if
that check fails. Config lookup order is `--config PATH`, `$TNT_CONFIG`, `./tnt.toml`, then
`$XDG_CONFIG_HOME/tnt/tnt.toml` (or `~/.config/tnt/tnt.toml` when `XDG_CONFIG_HOME` is unset). `ws://` is refused.
TLS verification is on by default. A profile with `tls = "insecure"` also requires `--insecure` on each command.

Credentials may be inline by operator choice; see the [secret-handling rationale](docs/DESIGN.md#7-secrets). They never appear in command arguments, log messages, exceptions,
`profile show`, JSON output, or the TUI. `profile show` masks configured secrets as `***`.

```toml
schema_version = 1
default = "lab"

[profiles.lab]
url = "wss://nas.example.invalid/api/current"
username = "admin"
auth = "api_key"
api_key = "REPLACE_WITH_API_KEY" # Or use api_key_file = "/home/me/.config/tnt/lab.key".
tls = "verify"
ssh_user = "admin"
ssh_key_files = ["~/.ssh/id_ed25519"]
ssh_known_hosts = "~/.ssh/known_hosts"

[profiles.password-auth]
url = "wss://nas2.example.invalid/api/current"
username = "operator"
auth = "password"
password = "REPLACE_WITH_PASSWORD"
tls = "verify"
```

`schema_version` is required and currently `1`. The example contains placeholders only. API key authentication uses the pinned client's SCRAM flow. Password
authentication uses its supported `PASSWORD_PLAIN` login flow. Authentication failures are fatal and never retried.
SSH paths are retained for user key management; tnt never prints SSH key material.

## Use

```bash
tnt --version
tnt profile list
tnt profile show lab
tnt doctor --online --profile lab       # one login, no retry
tnt dataset list --profile lab --json
tnt dataset create --profile lab --name tank/home --type filesystem --allow-write
tnt snapshot create --profile lab tank/home nightly --allow-write
tnt container list --profile lab --json
tnt vm list --profile lab --json
tnt user list --profile lab --json
tnt tui --profile lab
```

Each write command requires `--allow-write`; destructive commands also require `--confirm TARGET`. Use `--dry-run` to
render the plan without applying it. Password entry uses a no-echo prompt or `--password-file`; tnt does not accept a
password argument. API payloads not covered by a minimal create wizard can be supplied as a JSON `--file`.

Container resource settings follow the v27 `container.create`/`container.update` schema: CPU affinity is exposed as
`cpuset`; the verified container CRUD schema has no memory limit or current image field. VM console information is
displayed as returned by v27; tnt does not embed a VNC client. tnt scans every non-read API payload for boot-pool
names and paths, including nested container, VM, sharing, app, and user fields, and resolves pool references against
the connected host before allowing a plan to apply.

TUI keys: arrows/enter navigate the TrueNAS menu, `ctrl+p` opens the command palette, `r` refreshes, `?` shows keys,
and `q` quits. Destructive action dialogs display the plan and ask for the exact confirmation target.

## Tests

Tests use the in-memory `tnt.testing.FakeServer` over loopback with the real `truenas_api_client`. No test contacts a
TrueNAS host. The optional Textual, websockets, and test dependencies are required by the test suite; missing
dependencies fail the suite.

```bash
PYTHONPATH=src nice python -m pytest -q -p no:xdist
```
