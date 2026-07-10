# groop Operations

This is the practical runbook for the current implementation.

## Install

From the repository root:

```bash
pip install -e groop/
groop --version
```

For test/dev without installing:

```bash
PYTHONPATH=groop/src python3 -m groop.cli --once --json
```

## Common Commands

Collect one frame as JSON:

```bash
groop --once --json
```

Open the live TUI:

```bash
groop
```

Record while viewing:

```bash
groop --record /tmp/groop-live.jsonl
```

Replay:

```bash
groop --replay /tmp/groop-live.jsonl --step
```

Use a fixture cgroup root:

```bash
groop --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch
```

Run a release smoke (rootless safe-path evidence):

```bash
PYTHONPATH=groop/src python3 -m groop.acceptance smoke \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --pretty-json
```

Run a steady-state collector loop (release confidence, rootless):

```bash
PYTHONPATH=groop/src python3 -m groop.acceptance steady \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --samples 5 --interval-s 0 --pretty-json
```

Run a TUI smoke release evidence (rootless, subprocess-based):

```bash
PYTHONPATH=groop/src python3 -m groop.acceptance tui-smoke \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --pretty-json
```

Run with a custom profile:

```bash
PYTHONPATH=groop/src python3 -m groop.acceptance tui-smoke \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --profile minimal --json
```

Inspect an incident snapshot:

```bash
groop snapshot inspect /path/to/groop-incident-*.tar
```

Check daemon deployment and protocol status (non-root, read-only):

```bash
groop daemon status                              # default socket and group
groop daemon status --json                       # JSON output
groop daemon status --pretty-json                # indented JSON
groop daemon status --socket /custom/path.sock --group mygroup
```

Retrieve one canonical frame from the daemon socket:

```bash
groop daemon current                             # default socket
groop daemon current --socket /custom/path.sock --pretty-json
```

Attach to a running daemon (interactive TUI or one-shot):

```bash
groop --attach                                   # default socket, interactive UI
groop --attach --once --json                     # default socket, one frame
groop --attach /run/groop/groop.sock             # explicit socket
```

Run the daemon deployment preflight check:

```bash
groop daemon preflight                           # default socket
groop daemon preflight --socket /custom/path.sock --json
```

View the safe install plan for the packaged daemon templates:

```bash
groop daemon install-plan                        # text plan
groop daemon install-plan --json                 # JSON plan
```

Snapshots are written to `[snapshots].dir` when configured, otherwise
`$XDG_STATE_HOME/groop/incidents` or `~/.local/state/groop/incidents`.
They include bounded frame history, selected cgroup files, provider status,
fresh `systemctl show` output when the selected row maps to a systemd unit, and
a redacted Docker inspect summary when the selected row is Docker-backed.
Set `[snapshots] redact = true` to remove Docker environment variables and
labels from the bundle.

Stop groop-owned DAMON sessions:

```bash
sudo groop damon stop --all-mine
```

Start a manual paddr host heat session from CLI:

```bash
sudo groop damon paddr start --confirm START
```

## TUI Keys

| Key | Action |
|---|---|
| `F5`, `t` | Toggle tree/container view. |
| `Tab`, `p` | Cycle column profile. |
| `F6`, `s` | Cycle sort. |
| `/` | Filter rows. |
| `Left`, `h` | Collapse selected tree branch or move to parent. |
| `Right`, `l` | Expand selected tree branch. |
| `Up`, `Down` | Move selection. |
| `Enter` | Entity drill-down. |
| `d` | From entity drill-down: open DAMON vaddr typed-confirmation start modal. |
| `p` | From host-memory screen: open DAMON paddr typed-confirmation start modal. |
| `s` | From DAMON drill/host-memory screens: stop groop-owned DAMON sessions only. |
| `Space` | Play/pause replay while in `--replay`. |
| `,`, `.` | Step replay backward/forward one frame. |
| `+`, `-` | Change replay speed while in `--replay`. |
| `x` | Save incident snapshot for selected entity. |
| `m` | Host-memory / paddr DAMON status. |
| `b` | Collapse/expand banner. |
| `k` | Reserved v2 admin action; current builds report that executable admin actions are not implemented. |
| `F1`, `?` | Metric glossary/help. |
| `q` | Quit. |

## Safety Model

- Normal collection is read-only.
- `--once --json` and replay paths should not import Textual.
- DAMON control writes are the only current mutating feature. They require root,
  typed `START`, groop ownership markers, and audit logs.
- `groop damon stop --all-mine` only stops sessions with groop markers.
- TUI DAMON start modals show the planned sysfs writes and require exact
  `START`. Foreign DAMON sessions remain read-only; the TUI cleanup path calls
  the same groop-owned marker logic as the CLI.
- Incident snapshots write only under the configured snapshot directory or the
  XDG state fallback. They do not collect arbitrary file/log content.
- File/log/content browsing and executable Docker/systemd admin actions are not implemented.
- Pressing a reserved v2 admin key in the TUI reports that the action is
  unavailable in the current build instead of failing silently.
- `groop action preview --kind docker-restart --target NAME --admin --json`
  prints an exact argv preview and never executes it. Omit `--admin` to verify
  that the preview is denied. Use `--audit-log PATH` to append an explicit
  preview-only JSONL record.

## Compressed Swap Interpretation

Current builds expose zswap metrics, host-level zram metrics, and active
swap-backend classification. The per-cgroup `swap_disk` name is still a legacy
compatibility label: on zram-only hosts it represents logical non-zswap swap
usage, not physical disk IO; on mixed hosts the kernel does not expose
per-cgroup backend attribution. See `docs/COMPRESSED-SWAP.md`.

## Configuration

Default config path:

```text
$XDG_CONFIG_HOME/groop/config.toml
```

Everything is optional. Useful current sections:

```toml
[general]
interval = 5.0
default_view = "tree"
default_column_profile = "auto"

[history]
full_resolution_seconds = 14400
entity_grace_seconds = 30.0

[record]
flush_every_frames = 1
fsync = false

[snapshots]
frames = 60
redact = false

[damon]
hot_rate = 50.0
warm_rate = 5.0
cold_age = 30.0
idle_age = 120.0
vaddr_sample_us = 100000
vaddr_aggr_us = 2000000
vaddr_update_us = 1000000
paddr_sample_us = 400000
paddr_aggr_us = 8000000
paddr_update_us = 1000000
max_concurrent_targets = 4
```

## What To Check Before A Release Claim

See the canonical release readiness document at `docs/RELEASE-READINESS.md`
for the full checklist mapping `TUI-SPEC.md` §9 gates to evidence sources,
rootless automated check commands, live-host evidence templates, and explicit
non-claims.

Quick reference (see `docs/RELEASE-READINESS.md` for the exact commands):

- Full test suite.
- `py_compile` over `src/groop`.
- `--once --json` on a real host and fixture root.
- Replay UI smoke.
- Acceptance smoke/steady/tui-smoke (P33/P35/P38).
- Editable install and wheel/sdist/pipx packaging.
- `MEASUREMENTS.md` CPU/RSS evidence.
- Live-root DAMON acceptance if claiming controlled DAMON support.
