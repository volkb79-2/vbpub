# Augeaswrap specification

This document defines the endpoint record API and on-disk format shipped by Augeaswrap.

## Endpoint records

`augeaswrap.endpoints` is the shared publisher, with `cli-extended` as its only
runtime dependency, for host endpoint facts. It does not choose socket paths, group IDs, or consumer
fallbacks. Each service installer resolves its own configured values and
publishes only the fragment for that service; consumers read the resulting
process environment.

The module lives in the `augeaswrap` package, which carries what
every host-configuration resource shares: the structured `Problem` record
and `augeaswrap/_fs.py` (symlink-safe paths, atomic writes, directory
`fsync`, and a lock whose path is a parameter). Endpoint fragment names are
validated by `augeaswrap.endpoints.validate_owner` with the alphabet
`^[a-z][a-z0-9-]{0,62}$`.

### API

The module provides these functions:

```python
publish(owner, entries, *, writer, root="/")
withdraw(owner, *, root="/") -> bool
plan(owner, entries, *, root="/") -> list[Problem]
verify(owner, expected, *, root="/") -> list[Problem]
check(*, root="/", ownership=None) -> CheckResult
require_env(name, *, remedy, environ=os.environ) -> str
render(owner, entries, *, writer, current_environment, existing_fragments, root="/")
    -> {path: (bytes, mode)}
render_withdraw(owner, *, current_environment, existing_fragments, root="/")
    -> {path: (bytes, mode) | None}
render_block(current, payload, keys, *, marker_name="host-endpoints", begin_line=None)
    -> bytes
```

`entries` and `expected` are mappings or iterables of key/value pairs.
`publish` replaces one owner's fragment and re-renders the shared files;
`withdraw` removes that fragment and re-renders, returning `False` and
changing nothing when the owner has no fragment. `plan` is read-only and
reports invalid input, key collisions, damaged environment markers, duplicate
unmanaged assignments, and unsafe targets. `verify` compares one fragment to
the expected mapping (a fragment is replaced atomically, so reading one file
needs no lock). `check` checks the whole record set, aggregate, generated
environment block, profile source, symlinks, modes, ownership, and stale
temporary files. `require_env` returns a non-empty environment value or
raises `CliFailure` naming the variable and the caller's remedy.

`render`, `render_withdraw` and `render_block` are pure: bytes in, a
`{path: (content, mode)}` plan out, nothing written. `publish` and `withdraw`
build that plan under the lock and apply it, so Augeaswrap's planned transaction runner
can reuse the same rendering. `render_block` takes the managed-block marker
name and BEGIN line as parameters; the endpoint block is the default.

Problems are `Problem` records with `code`, `message`, `path`, `owner` and
`key`; `str(problem)` is the message and a problem compares equal to its
message string. `check` returns a `CheckResult`: a list of problems with
`status` (`clean` or `problems`) and `message`. A host where nothing was ever
published (no `host-endpoints.d`, aggregate, profile script or block) is clean
with the message `no endpoints published`.

`check` holds the shared side of the writer lock, so it never reports false
drift while a publish is in flight. A caller that cannot open the root-owned
lock (non-root) gets an advisory check that re-runs up to three times before
it reports drift. The root:root ownership check applies to the real `/` by
default; a fake root skips it unless the caller passes `ownership`, a
`stat_result -> bool` callable.

### On-disk contract

| Path | Mode | Purpose |
|---|---:|---|
| `/etc/host-endpoints.d/` | `0755` | owner fragment directory |
| `/etc/host-endpoints.d/<owner>.env` | `0644` | one service's endpoint entries |
| `/etc/host-endpoints.d/.lock` | `0600` | `flock`: exclusive for publish and withdraw, shared for `check` |
| `/etc/host-endpoints.env` | `0644` | deterministic aggregate generated from fragments |
| `/etc/environment` | `0644` | unmanaged content plus the generated block |
| `/etc/profile.d/host-endpoints.sh` | `0644` | exports the aggregate for login shells |

Each fragment starts with `# host-endpoints v1`, `# owner=<owner>`, and
`# writer=<verb> <version>` header comments, followed by `KEY=VALUE` lines.
Keys match `[A-Z][A-Z0-9_]{0,63}`; values match
`[A-Za-z0-9_./:@+-]{1,4096}`. A key may appear in only one fragment. The
aggregate is sorted by owner and then key, with owner and writer comments for
each owner. Owner names match `^[a-z][a-z0-9-]{0,62}$`.

The 4096-byte value limit is this module's own bound. Whether the host's
`pam_env` accepts a line that long has not been verified; it is on the VM
verification list, so treat values above a few hundred bytes as untested.

These keys are reserved and refused at `plan`, `publish` and `check`, because
they change how every login shell, ssh, sudo, su or cron session behaves
(`pam_env` applies `/etc/environment` to all of them) or are read-only in a
POSIX shell. The rule is: a key is reserved when it is one of `PATH`, `ENV`,
`IFS`, `UID`, `EUID`, `PPID`, `SHELLOPTS`, `BASHOPTS`, `HOME`, `USER`,
`LOGNAME`, `SHELL`, `PWD`, `OLDPWD`, `PROMPT_COMMAND` (bash executes its value),
`SHLVL`, `GROUPS`, `RANDOM`, `SECONDS`, `LINENO`, `TMPDIR`, `CDPATH`,
`GLOBIGNORE`, `NODE_OPTIONS`, `SSH_AUTH_SOCK`, or starts with a reserved
family prefix: `LD_` (dynamic loader: `LD_PRELOAD`, `LD_LIBRARY_PATH`,
`LD_AUDIT`, ...), `PYTHON` (interpreter: `PYTHONPATH`, `PYTHONSTARTUP`, ...),
`BASH_` (`BASH_ENV`, `BASH_XTRACEFD`, ...) or `LC_` (locale). Use a
service-specific prefix such as `CGPROFILE_`.

The `/etc/environment` block uses these exact marker lines:

```text
# BEGIN host-endpoints (generated by cli_extended.hostcfg.endpoints; do not edit)
# END host-endpoints
```

The `BEGIN` text still names `cli_extended.hostcfg.endpoints` on purpose: the marker is part of the frozen on-disk format and existing hosts carry it, so the move to `augeaswrap` leaves it unchanged.

Publishing replaces a stale well-formed block while retaining unmanaged lines
and their order; the block is always rewritten at the end of the file, so an
existing block in the middle moves to the end. An unmanaged assignment of a
published key is a refusal, including `export KEY=...` and lines with
leading whitespace, which `pam_env` also accepts. Orphaned, duplicated,
malformed (for example trailing text after a marker), or reversed markers are
refused; the refusal can list several defects for one damaged block.
Withdrawing the final fragment removes the block; the aggregate then contains
only its version header.

Unmanaged bytes are preserved exactly, including CRLF, NUL and non-UTF-8
bytes and the file's trailing-newline state. Every line of the block, END
included, ends with a newline, so a later `echo X >> /etc/environment` (or a
`printf` without newline) never glues onto a marker. When the file had no
final newline, the END line is written as
`# END host-endpoints (no final newline)`; removing a block that is still the
last thing in the file then returns the original bytes exactly. If other text
was appended after the block, the newline is kept (`A=1` + block + `NEW=1\n`
withdraws to `A=1\nNEW=1\n`). If an editor strips the END line's newline and
text is glued after the marker, publish, withdraw and `check` refuse with
`BEGIN marker without END (line N starts with the END marker but has text
glued after it; put the marker alone on its line)`; restoring the newline
heals it.

The profile script is:

```sh
[ -r /etc/host-endpoints.env ] && { set -a; . /etc/host-endpoints.env; set +a; } || :
```

The trailing `|| :` keeps the script's status `0` when the file is unreadable,
so a `set -e` consumer is not aborted.

The helper refuses symlinked managed paths. Every generated replacement is
written to a temporary file in the destination directory, flushed with
`fsync`, renamed atomically, and the directory is `fsync`ed (also after a
withdraw unlinks a fragment). Directories the module creates are mode `0755`
regardless of umask. One exclusive lock covers each publisher's read,
validation, fragment update, and render, so concurrent owners cannot lose one
another's records. The multi-file write is per-file atomic, not transactional:
a crash between files leaves the fragment ahead of the aggregate until the
next publish or withdraw heals it, and `check` reports it. `publish` and
`withdraw` remove their own stale temporary files
(`.host-endpoints-tmp-*` in `/etc`, `host-endpoints.d` and `profile.d`) under
the lock and `check` reports them.

Damage is isolated per owner: one damaged fragment does not block another
owner's publish or withdraw (it stays in place and `check` reports it). The
damaged owner itself is healed by re-running its install verb: `publish`
replaces and `withdraw` removes a hand-edited or corrupt own fragment without
parsing it (only an unsafe target, such as a symlink, is refused).

On the real `/`, a root `publish` or `withdraw` also repairs wrong ownership:
a managed file whose uid/gid is not root:root is rewritten even when its bytes
and mode already match (fake roots keep the caller's ownership).

### Sharing `/etc/environment` with other managed blocks

`/etc/environment` may also carry an `mdt-buildkit` block. Every block
has its own `# BEGIN <id>` / `# END <id>` pair and a writer only rewrites the
lines between its own markers, so two blocks coexist (the tests render an
`mdt-buildkit` block and the endpoint block into one file and withdraw either
without touching the other). The lock is what prevents a lost update: a
writer of another block must hold `/etc/host-endpoints.d/.lock` while it
rewrites `/etc/environment` (a future Augeaswrap block resource must), because
a rewrite is a read-modify-replace of the whole file. Any writer of a second
block must take this lock; run writers serially until all of them do.

### Module CLI

Host-side shell callers can run:

```text
python3 -m augeaswrap endpoints publish --owner O --writer W --entry K=V [--entry K=V ...]
python3 -m augeaswrap endpoints withdraw --owner O
python3 -m augeaswrap endpoints check [--json]
python3 -m augeaswrap endpoints show
```

The CLI uses the shared CLI error and exit-status contract. `check` exits `0`
when the records are consistent and also when nothing was ever published (it
then prints `no endpoints published`), and `1` when drift is found.
`check --json` prints
`{"status": "clean"|"problems", "message": ..., "problems": [...], "records": [...]}`
where `problems` are the message strings and `records` the structured
`Problem` dictionaries. `withdraw` of an owner without a fragment exits `0`
and says so. Publishing refusals and invalid arguments exit `2`.

## Guarded applier protocol (HC-1a')

The core applier accepts strict TOML requests and Python `Request` values. Its
state is limited to transaction journals and exact pre-images. A request has a
non-empty, single-line, NUL-free `writer` and an ordered tuple of resources;
it has no owner or prune field. Callers remove resources with explicit
`state = "absent"` requests.
There is no ownership registry, takeover, tool-level uninstall, or automatic
rollback. `revert` is an explicit operation and refuses when a later
transaction changed an address in the transaction being reverted.

Every resource implements this frozen protocol. HC-1b-u and HC-1b-s resource
work, including the endpoint adapter, uses it without changing its signatures:

```python
class Resource(Protocol):
    kind: str
    def addresses(self) -> tuple[str, ...]: ...
    def observe(self, ctx) -> Mapping[str, object]: ...
    def diff(self, ctx, observed) -> list[Change]: ...
    def stage(self, ctx, change: Change) -> object: ...
    def commit(self, ctx, staged: object) -> object: ...
    def post_actions(self, changes: list[Change]) -> list[PostAction]: ...
    def verify(self, ctx) -> list[Problem]: ...
```

`observe` is read-only and returns one JSON-safe value per address. Each value
must represent every file, path, and external fact the resource read; the
engine fingerprints the complete observation and rechecks it under the
exclusive lock before apply. `diff` returns one `Change` or `noop` per
address. `stage` prepares and validates without changing the managed root; it
may write only below the private `ctx.stage_dir`. `commit` applies one staged
change and returns its post-image for the journal. `post_actions` returns
argv-only `PostAction` values. `verify` reads committed state and compares it
with the staged images in `ctx.expected`.

The stable context fields are `root`, `writer`, `txn_id`, `txn_dir`,
`stage_dir`, `pre_dir`, and `expected`. `root` is the selected real or fake
root. During planning, transaction fields may be `None` and `stage_dir` is a
private temporary directory; during apply it is transaction-local. There is
no state-file or ownership field. The protocol docstring in
[`src/augeaswrap/__init__.py`](src/augeaswrap/__init__.py) is the source
contract for parallel resource work.

The request schema starts with `augeaswrap = 1` and requires `writer`. Unknown
keys and missing required fields refuse during load. The registered kinds are:

| Kind | Changes host state | Revertible | Purpose |
|---|---:|---:|---|
| `file` | yes | yes | one whole file in the configuration allowlist |
| `unit` | yes | yes | one systemd unit file or drop-in below `/etc/systemd/system` |
| `check` | no | no | read-only argv or path metadata assertion |

File requests write or delete a whole allowed file. Unit requests manage a
systemd unit or drop-in. Check requests run a read-only argv or assert path
metadata. See the
[consumer examples](docs/CONSUMERS.md#guarded-applier) and the
[design rationale](docs/DESIGN-GUIDE.md#guarded-applier).

### File and check behavior

`File(path, state, text=None, mode=None, owner=None, group=None,
create_only=False, validate=None)` addresses one whole file. `state` is always
`present` or `absent`. A present file requires `text` and an explicit octal
`mode`; an absent file takes no content or metadata fields. Optional owner and
group are names or numeric ids. When omitted, an existing file keeps its
current uid/gid and a new file uses the process uid/gid. `create_only=true`
leaves an existing file and its metadata untouched; when the file is absent,
it stages the requested content. Paths must be normalized absolute paths
below `/etc`, `/root`, `/home`, `/usr/local/etc`, or
`/usr/local/lib/systemd`. Every component from the selected root to the target
must be free of symlinks, and the target must be a regular file or absent.

The optional `validate` field accepts `json`, `toml`, `udev`, `sudoers`,
`sshd`, or `cmd` followed by argv values. JSON validation rejects duplicate
keys. Command validators run against the staged copy with a 30-second
timeout; one `%s` placeholder is replaced with its staged path, or the path
is appended when no placeholder is present. Validators receive argv directly,
never a shell command string. A validation failure refuses the plan before
the managed root is changed.

`Check` has `state = "present" | "absent"` and exactly one predicate. An exec
predicate supplies `exec` as an argv array plus explicit `expect_exit` and
positive finite `timeout`. A path-mode predicate supplies `path`, `owner`,
`group`, `mode`, and `if_exists = "must-match" | "absent"`. Checks are
read-only. An absent check is skipped; a present check is observed during plan
and rechecked before commit and during verification.

### Unit and drop-in behavior

`Unit(name, state, text=None, mode=None, owner=None, group=None, dropin=None,
enable=None, on_change=None, preflight=None)` addresses one of:

- `/etc/systemd/system/<name>` when `dropin` is omitted;
- `/etc/systemd/system/<name>.d/<dropin>` when `dropin` is present.

The unit name uses `[A-Za-z0-9:_.\\@-]+` and must end in `.service`, `.socket`,
`.device`, `.mount`, `.automount`, `.swap`, `.target`, `.path`, `.timer`,
`.slice`, or `.scope`. A drop-in is a `*.conf` basename; path separators are
refused in both names. The selected path and every component below `--root`
must be free of symlinks.

A present whole unit requires `text`, `mode`, `enable`, and `on_change`. A
present drop-in requires `text`, `mode`, and `on_change`; `enable` is invalid.
An absent whole unit takes only `name` and `state`. An absent drop-in requires
`on_change`. Owner and group use the same name-or-numeric-id rules as `file`:
an omitted owner preserves an existing file's ids and uses the process ids for
a new file. `preflight` is an optional argv array for a present unit or drop-in;
it receives no shell and has a 30-second timeout.

Planning and staging do not write the managed root. A present unit is staged in
a private directory that also carries the unit's permission profile; the
resource runs `systemd-analyze --root=<staged-root> verify
/etc/systemd/system/<name>` before any managed write. Every path named by an
`Exec*=` assignment is then checked against the selected root: optional leading
`-@:+!` prefixes are stripped, the executable must exist, be a regular
root-owned executable, and every path component must be free of group/other
write permission. Executables at or below `/tmp`, `/var/tmp`, or `/home` are
refused. The optional preflight argv runs last and must exit zero within 30
seconds. A verify or preflight refusal aborts the plan before a managed write.

Changed units declare argv-only post-actions. Every changed address requests
`systemctl daemon-reload`; the engine deduplicates that action across the
request. A whole unit with `enable=true` also requests
`systemctl enable <name>`. `on_change` is required:

| Value | Action after a change |
|---|---|
| `restart` | `systemctl restart <name>` |
| `reload` | `systemctl reload <name>` |
| `none` | no runtime action |
| `pending` | record the restart in the journal without executing it |

A changed socket restarts only when its own unit text changed. A no-op socket
declares no action. A drop-in never enables its unit, but it does reload and
apply its `on_change` rule.

An absent whole unit stops and disables the unit before deleting its file, then
requests `daemon-reload` and `reset-failed`. On a fake root the pre-removal
commands are skipped. An absent drop-in deletes only its file, then requests
`daemon-reload` and its `on_change` action. The unit revert hooks snapshot the
exact bytes, mode, uid, and gid and restore them through a normal journaled
revert transaction; recorded actions are replayed under the existing
post-action rules.

**Known issue: host-only drop-in verification.** `systemd-analyze verify` needs
the base unit in the staged root. The unit kind copies a base unit only when it
exists at `/etc/systemd/system/<name>`. A drop-in for a distro unit whose base
fragment lives under `/usr/lib/systemd/system` (or another lower-priority
systemd search directory) therefore skips `systemd-analyze verify`; the exact
limitation is that the host-only base unit and its lower-priority drop-ins are
not reconstructed in the staged tree. Exec and preflight checks still run.

**Shadowing seam.** `augeaswrap.unit.shadow_check` is currently a documented
no-op returning an empty list. HC-1b-s's shared directive and effective-order
logic will connect through that seam; this release performs no directive-level
shadow analysis.

### Transaction lifecycle

The state directory is `/var/lib/augeaswrap/txn`; the exclusive host lock is
`/run/lock/augeaswrap.lock`. A plan takes a shared lock and records a
fingerprint for each resource observation. Apply takes the exclusive lock,
re-observes every address, and raises `StalePlan` if any observation differs.
It creates `txn/<id>/stage` and stages and validates every changed resource
before writing `txn/<id>/pre` or changing the host. A file pre-image contains
its exact bytes and prior mode, uid, and gid, or an explicit absent record.

Each committed file, post-action, and verification step updates
`journal.json`. The journal records the writer, request digest, changes,
pre-image metadata, post-image fingerprints, completed/failed/not-run steps,
and action results associated with the addresses that requested them. After
the first commit, an error stops the transaction;
the partial result and journal remain, and no rollback is attempted. Fake-root
post-actions report `skipped(root)`. If a failed commit's
post-image cannot be observed, the journal records that address as unknown
and revert refuses it.
The last 50 transaction journals and pre-images are retained. Transaction IDs
increase in allocation order, including when timestamps match or the wall clock
moves backwards, so a same-second transaction is still recognized as later.

`revert <txn>` is explicit. It compares each current file with the recorded
post-image and scans later journals for a transaction that touched the same
address. A mismatch refuses the revert and names the later transaction or
reports that the file changed outside Augeaswrap. A successful revert restores
the pre-images, reruns recorded post-actions for those addresses, and creates a
new transaction with its own pre-images and journal. A journal whose root
differs from the selected `--root` is refused. No-op plans and applies
create no transaction; repeating a successful request therefore writes
nothing.

### Adding a resource kind: registry and revert contract

This section is an additive addendum to the frozen seven-method `Resource`
protocol; none of its signatures change. A kind is added by writing one module
that registers a `KindSpec` when it is imported, and by importing that module
once in `augeaswrap/__init__.py`. The engine (`load_request`,
`Request.to_toml()`, plan rendering, journals, revert) contains no per-kind code:

```python
from augeaswrap.kinds import KindSpec, RevertHooks, register_kind

register_kind(KindSpec(
    kind="unit",                 # also the TOML array-of-tables name: [[unit]]
    parse=parse_unit,            # (table: Mapping) -> Resource; raise ValueError when invalid
    to_toml=unit_values,         # (resource) -> Mapping of table key -> value
    render=unit_lines,           # optional (change, plan) -> Sequence[str], extra plan lines
    revert=RevertHooks(snapshot=unit_snapshot, restore=unit_restore),   # or revert=None
))
```

`parse` and `to_toml` must round-trip: `load_request(request.to_toml())`
equals the request. `kinds.require_keys` and `kinds.resource_tables` give
parsers the same strict unknown/missing-key errors the core kinds use.
`revert` has no default: a kind states either hooks or `revert=None`
(declared unsupported). A kind that is not registered at all, such as a
library-only resource, is also unsupported for revert.

**Revert contract.** Revert restores a transaction's pre-images, so every kind
that writes host state should provide both hooks:

- `snapshot(change, observed) -> Mapping` runs before any commit with the
  planned change and the observation the plan took. It returns a JSON-safe
  record of what the commit overwrites. The engine stores it as
  `txn/<id>/pre/<sha256 of address>.json` (mode 0600). A record may carry
  `data_b64` (base64 bytes) together with `sha256` (digest of those bytes); the
  engine stores the bytes in a sibling `.data` file (mode 0600), checks the
  digest when saving and again when reading, and hands them back inline. The
  keys `schema`, `kind`, `address` and `data_file` are reserved.
- `restore(address, record, actions) -> Resource` runs at revert time with the
  stored record. It validates the record (raising `ValueError`, which refuses
  the revert) and returns an ordinary resource that, when planned, staged,
  committed and verified, puts the recorded state back. The restore resource's
  `observe` must return the same observation shape as the original kind, so the
  engine can compare the current state with the journal's post-image; its
  `commit` returns that observation; its `post_actions` returns `actions`, the
  post-actions that ran for the address in the original transaction.

The engine adds the rest: later-transaction and drift refusals, a new
journaled transaction with its own pre-images, post-action replay, and
verification. `file` and `unit` implement both hooks. `check` is read-only and never
creates a change, so it has nothing to revert.

A plan states revert support before anything is applied: each changed address
of a kind without hooks prints `revert: unsupported`, and `plan --json` carries
`"revertible": false` for it. Applying such a plan is still allowed; a later
`revert` refuses with `revert.unsupported`. Kinds currently revertible: `file`
and `unit`. HC-1b-s (`sysctl`, `sysusers`, `tmpfiles`,
`endpoint`), HC-2 (`json_key`) and HC-3 (`edit`) each add a module on this
pattern; none edits engine code. Their pre-image is the file the resource
writes; a kind whose effect is not file content (live sysctl values, group
membership) lists that effect in the journal's `irreversible_effects` instead.

`PostAction.pending=true` is the generic journal-only action form. Apply records
the action and its argv with status `pending` instead of executing it; the
result exposes the action names in `Result.pending`. Revert replays those
actions as pending declarations as well. This does not provide journal query
or maintenance commands; those remain HC-2 work.

**Post-action order.** The engine runs post-actions in the order their
resources appear in the request (first appearance wins when two resources
declare the same action). It does not reorder them into the fixed phase order of
the design (sysusers, tmpfiles, daemon-reload, sysctl, enable/restart, gpasswd,
endpoint). This is deliberate for HC-1a': the engine knows no kinds. A caller
that needs that order lists its resources in it; an optional `phase` on
`PostAction` is tracked in the backlog for HC-1b.

**Limits that are by design.**

- `plan` is read-only for the managed root but it does run `validate`
  commands and exec `check` commands (with stdin closed) and prints text
  diffs of changed files, so file content can reach the terminal or logs.
- Journals and pre-images keep the exact bytes of every changed file for the
  last 50 transactions, privately (directories 0700, files 0600).
- Directories created for a new file are not journaled and revert leaves them.
- After a revert, earlier transactions that touched the same address cannot be
  reverted, because the revert counts as a later transaction.
- An apply stopped by `KeyboardInterrupt`, `SystemExit` or `SIGTERM` is
  journaled with status `interrupted`; it is revertible like a failed one.
  A hard kill leaves status `applying`, which `revert` refuses.
- A transaction directory without `journal.json` (a crash before its first
  journal write) changed nothing: `journal list` skips it with a warning and it
  never blocks the revert of earlier transactions.
- `mode` may include setuid, setgid or sticky bits, and request and observed file
  sizes are not capped; both are the caller's responsibility.

### Core commands and exit codes

| Command | Exit | Behavior |
|---|---:|---|
| `augeaswrap plan REQ.toml [--root DIR] [--json]` | 0 / 10 / 2 | 0 means no changes, 10 means changes are pending, 2 means refused |
| `augeaswrap apply REQ.toml [--root DIR] [--yes] [--dry-run] [--json]` | 0 / 2 / 4 | 0 means applied or no changes, 2 means refused or stale before commit, 4 means a partial apply with journal retained |
| `augeaswrap revert TXN [--root DIR] [--yes] [--dry-run] [--json]` | 0 / 2 / 4 | Explicitly restores pre-images as a new transaction; exit 4 means revert itself partially applied |
| `augeaswrap journal [list\|show TXN] [--root DIR] [--json]` | 0 / 2 | Lists retained transactions or displays one journal; invalid or unreadable journal data is refused |

With `--json`, each command writes exactly one JSON document. `plan` writes its
plan object; `journal` writes a journal or journal array; `apply` and `revert`
write a result object containing `status`, `plan`, and `result`. A refused or
partial apply/revert uses one object with its status and the available
problems, transaction id, and failed step.

Mutating CLI verbs print their plan and use cli-extended confirmation. A
terminal can confirm interactively; non-interactive callers pass `--yes`.
`--dry-run` prints the plan and does not write. The library never prompts:
calling `apply(plan)` accepts that plan. `apply` also takes a `Request`, a
mapping, or a TOML path (`str` or `PathLike`); those are planned first, against
`root` or, when no root is passed, against `/`. A `Plan` carries its own root,
and passing a different `root` with it raises `StalePlan`.
