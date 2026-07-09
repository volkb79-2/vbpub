# Inspect-Files Safety Contract

## Overview

The `groop inspect-files plan` command provides a disabled-by-default, read-only
planning interface for file/log inspection. It is the v2 safety skeleton for
future content browsing features, establishing the security contract before any
real file content reads or subprocess execution are implemented.

## Gating

File inspection requires **both** of these flags:

- `--inspect-files` — enables the inspection planning mode.
- `--admin` — enables admin preview mode.

Without either flag, the command prints a disabled message and exits with code 2.

```bash
groop inspect-files plan --kind docker-json-log --target my-container --inspect-files --admin
```

## Safety Guarantees

### No File Content Reads

The planning module builds path previews **lexically only** — it never calls
`open()`, `Path.read_text()`, `Path.read_bytes()`, or `os.open()`. Path objects
are constructed from string concatenation and normalised with
`Path.resolve(strict=False)`, which handles `..` segments without I/O.

### No Subprocess Execution

The module does not import `subprocess`, `os.system`, or any command-execution
facility. Command previews are returned as structured argv lists for display
only — they are never executed.

### No Host Mutation

All plans are immutable dataclasses. No files are created, modified, or deleted.
No system state is changed.

### Path Safety

- Absolute path targets supplied directly by users are rejected unless they belong
  to the allowlisted kind.
- Docker container targets are validated against path-traversal patterns.
- Systemd unit targets are validated against unsafe character patterns.
- Cgroup path targets are restricted to paths under `/sys/fs/cgroup/`.
- Symlinks are never followed.
- Path previews are normalised with `expanduser()` and `resolve(strict=False)`.

## Plan Kinds

| Kind | Description | Target format | Path previews | Command previews |
|---|---|---|---|---|
| `docker-json-log` | Plan expected Docker json-file log path | Container id or name | `.../containers/<id>/<id>-json.log` | `cat`, `tail` |
| `systemd-journal` | Plan journalctl query for a systemd unit | Unit name (e.g. `ssh.service`) | `/sys/fs/cgroup/system.slice/<unit>`, `/etc/systemd/system/<unit>` | `journalctl`, `systemctl status` |
| `cgroup-files` | List known cgroup filenames for snapshots | Cgroup path relative to `/sys/fs/cgroup/` | 20+ known cgroup file paths | None (plain file reads) |

## Output Formats

### JSON (--json)

```json
{
  "command_previews": [["cat", "/var/lib/docker/containers/abc/abc-json.log"]],
  "description": "Plan the expected Docker json-file log path...",
  "kind": "docker-json-log",
  "kind_label": "Docker JSON log",
  "mode": "plan",
  "path_previews": ["/var/lib/docker/containers/abc", "/var/lib/docker/containers/abc/abc-json.log"],
  "target": "abc"
}
```

### Text (default)

```
Inspection Plan: docker-json-log
Target: abc
Kind: Docker JSON log
Description: Plan the expected Docker json-file log path for a container id or name.

Path previews:
  /var/lib/docker/containers/abc
  /var/lib/docker/containers/abc/abc-json.log

Command previews (not executed):
  cat /var/lib/docker/containers/abc/abc-json.log
  tail -n 50 /var/lib/docker/containers/abc/abc-json.log

Mode: plan only; no file contents read, no commands executed
```

## Scope (what this is NOT)

- **Not a file browser** — no content reads, no directory listing, no tail/follow.
- **Not a subprocess executor** — no Docker, systemd/journalctl calls.
- **Not a daemon feature** — no daemon protocol integration.
- **Not a TUI screen** — CLI-only in this package.
- **Not root** — no privilege elevation.

## Adding New Plan Kinds

1. Add a new member to `InspectFilesKind` enum in `catalog.py`.
2. Add a builder function returning `(list[Path], list[list[str]])`.
3. Add an entry to `INSPECT_CATALOG`.
4. Add validation rules in the builder.
5. Add tests in `test_inspect_files.py`.
