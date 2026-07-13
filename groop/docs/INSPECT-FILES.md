# Inspect-Files Safety Contract

## Overview

The `groop inspect-files plan` command provides a disabled-by-default, read-only
planning interface for file/log inspection. The `groop inspect-files read`
command extends this with bounded, confined, read-only content reads for
allowlisted paths.

## Gating

File inspection requires **both** of these flags:

- `--inspect-files` — enables the inspection mode.
- `--admin` — enables admin preview mode.

Without either flag, the command prints a disabled message and exits with code 2.

```bash
groop inspect-files plan --kind docker-json-log --target my-container --inspect-files --admin
groop inspect-files read --kind docker-json-log --target <64hex> --inspect-files --admin
groop inspect-files read --kind systemd-journal --target ssh.service --inspect-files --admin
```

## Plan Command

### Safety Guarantees

#### No File Content Reads

The planning module builds path previews **lexically only** — it never calls
`open()`, `Path.read_text()`, `Path.read_bytes()`, or `os.open()`. Path objects
are constructed from allowlisted roots and targets, then normalized with string
path normalization. The planner does not call `Path.resolve()`, so symlinks are
not followed and existing path prefixes are not inspected.

#### No Subprocess Execution

The module does not import `subprocess`, `os.system`, or any command-execution
facility. Command previews are returned as structured argv lists for display
only — they are never executed.

#### No Host Mutation

All plans are immutable dataclasses. No files are created, modified, or deleted.
No system state is changed.

#### Path Safety

- Absolute path targets supplied directly by users are rejected unless they belong
  to the allowlisted kind.
- Docker container targets are validated against path-traversal patterns.
- Systemd unit targets are validated against unsafe character patterns and
  option-like tokens (starting with ``-``).
- Cgroup path targets are restricted to paths under `/sys/fs/cgroup/`.
- Symlinks are never followed.
- Path previews are normalised with `expanduser()` and lexical path
  normalization.

### Plan Kinds

| Kind | Description | Target format | Path previews | Command previews |
|---|---|---|---|---|
| `docker-json-log` | Plan expected Docker json-file log path | Container id or name | `.../containers/<id>/<id>-json.log` | `cat`, `tail` |
| `systemd-journal` | Plan journalctl query for a systemd unit | Unit name (e.g. `ssh.service`) | `/sys/fs/cgroup/system.slice/<unit>`, `/etc/systemd/system/<unit>` | `journalctl`, `systemctl status` |
| `cgroup-files` | List known cgroup filenames for snapshots | Cgroup path relative to `/sys/fs/cgroup/` | 20+ known cgroup file paths | None (plain file reads) |

### Output Formats

#### JSON (--json)

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

#### Text (default)

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

## Read Command

The `groop inspect-files read` command provides bounded content reads for
allowlisted file paths from the P29 catalog. It is disabled by default (requires
both `--inspect-files` and `--admin`).

### Read Safety Guarantees

#### Bounded Reads

- `--max-bytes` (default 65536): Hard byte limit **aggregate** across all
  files in a multi-file read (e.g. cgroup); truncated bytes are reported via
  the `truncated_bytes` field.  The limit applies to the **rendered** output
  content payload in encoded UTF-8 bytes, including per-file headers
  (``# /path``) and error annotations. The fixed typed text/JSON metadata
  envelope is structurally bounded separately and is not charged to this
  caller-selected content budget.
- `--max-lines` (default 5000): Hard line limit **aggregate** across all
  files; truncated lines are reported via the `truncated_lines` field.
- Both limits must be positive integers. Absolute maximums are enforced:
  `max_bytes ≤ 1 MiB`, `max_lines ≤ 100 000`.
- File-content reads (Docker logs, cgroup files) use **chunk-based** reads
  (fixed-size 64 KiB chunks), never line-by-line, so single giant lines with
  no newline never materialize unboundedly in memory.
- Journald reads use the ``-n`` flag on ``journalctl`` to bound lines at the
  source, plus additional byte/line enforcement via ``_bound_rendered_text``.
- Raw binary content is decoded with ``errors="replace"``.  Unsafe control
  characters (C0 codes 0x00-0x1F excluding ``\\n``/``\\t``, DEL 0x7F, C1
  codes 0x80-0x9F) are replaced with U+FFFD so terminal escape sequences,
  NUL bytes, or other control codes cannot replay in the returned text.
- `--json` output includes both truncation flags; text output prepends
  `[TRUNCATED]` notices.

#### Path Confinement (file-content reads only)

Every file-content read path undergoes four-stage validation:

1. **Catalog resolution**: The path is built from the allowlisted catalog and
   target metadata, never from user-supplied absolute paths.
2. **Root confinement**: The resolved path is verified to be under the
   allowlisted root via descriptor-relative traversal — the root directory is
   opened with `O_DIRECTORY | O_NOFOLLOW`, then each intermediate path
   component is walked with ``dir_fd`` and ``O_NOFOLLOW``. This prevents
   parent-component symlink escapes and is race-resistant.
3. **No-follow open + stat check**: The file is opened with
   `os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW`, then `fstat`-verified as
   a regular file (`S_ISREG`). Symlinks, devices, FIFOs, sockets, and
   directories are rejected.
4. **Descriptor-relative traversal**: All opens use `dir_fd` anchored at the
   allow_root directory descriptor — never absolute-path opens that could
   race with a symlink swap.

#### Journald Reads

Journald content reads use a **subprocess** — the only content-read kind that
does so. The implementation follows strict safety constraints:

- **Fixed absolute argv**: ``/usr/bin/journalctl`` — never configurable.
- **``shell=False``**: argv is a ``list[str]``, never a shell string.
- **Fixed flags**: ``--unit``, ``--no-pager``, ``--output=short-iso``,
  ``-n <max_lines>``. No ``--follow``.
- **Bounded timeout**: default 30 seconds, absolute maximum 60 seconds.
- **Bounded output**: stdout and stderr are fully captured.
- **No fallback**: a timeout or nonzero exit returns a typed error — never
  arbitrary content.
- **Injectability**: tests supply a mock runner that returns canned output
  without calling subprocess.

#### Docker Container IDs

Production Docker log reads require a **full 64-character lowercase hex
container ID**. Short IDs, container names, and partial prefixes are rejected.
Fixture seams may provide an alternate root via the `fixture_root=` Python
parameter (testing only — not exposed on the CLI).

#### Root Requirement

Production reads (no `fixture_root`, no `is_root`) require **root privileges**
(EUID 0), per TUI-SPEC §4.8 ("available only in root/admin or daemon-approved
modes").

When `fixture_root` is provided for testing, the root-PATH is redirected but
the root-EUID check is NOT bypassed.  An explicit `is_root` callable must be
provided to override the root check in tests:

```python
# Test with fixture root, simulating root:
build_inspect_read(..., fixture_root=..., is_root=lambda: True)
# Test with fixture root, simulating non-root:
build_inspect_read(..., fixture_root=..., is_root=lambda: False)
```

#### Cgroup Reads

Cgroup reads use the same allowlisted filename set as the catalog (memory.*,
cpu.*, io.*, pids.*, cgroup.*). Missing files are reported per-path with
clear error messages. Existing files are combined into a single output.

### Read Kinds

| Kind | Description | Target format | Read mechanism | Notes |
|---|---|---|---|---|
| `docker-json-log` | Bounded Docker json-file log read | Full 64-hex container ID | Direct descriptor I/O | Rejects short IDs and names for content reads |
| `systemd-journal` | Bounded journald snapshot | Systemd unit name (e.g. `ssh.service`) | Subprocess: fixed absolute `/usr/bin/journalctl` argv, `shell=False`, bounded timeout | Rejects option-like names (starting with `-`). Timeout/nonzero → typed error |
| `cgroup-files` | Bounded cgroup file reads | Cgroup path under `/sys/fs/cgroup/` | Direct descriptor I/O | Per-file error handling for missing files |

### Output Formats

#### JSON (--json)

```json
{
  "content": "{\"log\":\"...\"}...",
  "description": "Plan the expected Docker json-file log path...",
  "kind": "docker-json-log",
  "kind_label": "Docker JSON log",
  "mode": "content",
  "path": "/var/lib/docker/containers/<id>/<id>-json.log",
  "target": "<64hex>",
  "truncated_bytes": false,
  "truncated_lines": false
}
```

Error output (no content echoed):

```json
{
  "error": "path /etc/passwd is not under /var/lib/docker/containers",
  "kind": "docker-json-log",
  "mode": "error",
  "target": "/etc/passwd"
}
```

#### Text (default)

```
Read: docker-json-log
Target: aaaa...aaaa
Path: /var/lib/docker/containers/.../<id>-json.log

[TRUNCATED: byte limit exceeded]
<content starts here...>
```

For journald:

```
Read: systemd-journal
Target: ssh.service
Path: journalctl --unit ssh.service

2026-07-10T12:00:00+0000 host sshd[1234]: Accepted public key ...
[TRUNCATED: line limit exceeded]
```

### Error Handling

- Exit code **2**: Denied (gating flags not active) or parse error.
- Exit code **1**: Read error (file not found, not a regular file, path escape,
  journalctl timeout/failure, unsupported kind).
- Exit code **0**: Success (content returned, possibly truncated).

## Scope (what this is NOT)

- **Not a file browser** — no content reads, no directory listing, no tail/follow.
- **Not a general subprocess executor** — the only subprocess is the bounded,
  fixed-argv journald read. No Docker, arbitrary command execution, or shell
  invocation.
- **Not a daemon feature** — no daemon protocol integration.
- **Not a TUI screen** — CLI-only in this package.
- **Requires root** — production reads require EUID 0; testing uses
  ``is_root`` callable per the Python API.
- **Not arbitrary root reads** — every path must belong to the allowlisted catalog.
- **Not streaming** — bounded reads only; no follow/stream mode.

## Adding New Plan Kinds

1. Add a new member to `InspectFilesKind` enum in `catalog.py`.
2. Add a builder function returning `(list[Path], list[list[str]])`.
3. Add an entry to `INSPECT_CATALOG`.
4. Add validation rules in the builder.
5. Add tests in `test_inspect_files.py`.
6. For read support, add a resolution branch in `reader.py::build_inspect_read()`.
