# tnt (truenas-tui)

A keyboard-driven terminal UI and a scriptable CLI for TrueNAS, built on the versioned JSON-RPC 2.0 API over
websocket. The look and menu follow the TrueNAS web UI loosely, so an experienced user finds things where they
expect them; the CLI does everything the TUI does, non-interactively. Its first job is the gap the web UI cannot
fill: free space and partitions of a disk, pools on partitions or free space, and boot-pool data management
(see `docs/DESIGN.md` and the action matrix).

Status: M0 scaffold (`CHANGES.md`). Read-only. Design: [`docs/DESIGN.md`](docs/DESIGN.md). Work list:
[`BACKLOG.md`](BACKLOG.md).

## Install

```bash
pip install 'tnt[tui]'        # the CLI alone: pip install tnt
```

`truenas_api_client` (LGPL-3.0, official) is not on PyPI and installs from a pinned git sha; the reason for the pin is in
`pyproject.toml`.

## Profiles

`$XDG_CONFIG_HOME/tnt/profiles.toml` (or `$TNT_CONFIG`, or `--config FILE`). The file never holds a key.

```toml
default = "lab"

[profiles.lab]
url = "wss://nas.example.net/api/current"   # ws:// is refused
username = "tnt"
api_key_file = "/home/me/.config/tnt/lab.key"  # mode 0600, else tnt refuses to read it
tls = "verify"                                 # default; "insecure" only for a throwaway host
auth = "scram"                                 # default; "plain" = API_KEY_PLAIN over TLS
```

The key comes from `api_key_file`, else `$TNT_API_KEY_FILE`, else `$TNT_API_KEY`; never from the command line.
A profile with `tls = "insecure"` additionally needs `--insecure` on every command and shows a red banner in the TUI.

## Use

```bash
tnt --version
tnt profile list
tnt doctor                       # offline checks
tnt doctor --online --profile lab  # also one login (never retried: TrueNAS rate-limits logins)
tnt disks --profile lab          # disks, partitions, free space (--json for scripts)
tnt tui --profile lab            # Storage > Disks is the one implemented screen at M0
```

TUI keys: arrows/enter navigate the menu, `ctrl+p` opens the command palette (every screen is a "Go to ..."
entry), `r` refreshes, `?` shows keys, `q` quits.

## Tests

```bash
pip install -e '.[test]'          # needs a version source: SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0.dev0 outside a tagged checkout
nice -n 10 python -m pytest tests -q
```

No test contacts a real host: they run against `tnt.testing.FakeServer`, a fake JSON-RPC websocket server on loopback.
Optional dependencies (textual, websockets) are required by the suite: a missing one fails the tests, it does not skip them.
