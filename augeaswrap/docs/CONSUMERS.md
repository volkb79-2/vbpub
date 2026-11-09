# Augeaswrap consumer guide

Use this guide to publish and consume host endpoint facts. The normative record contract is in [SPEC.md](../SPEC.md#endpoint-records); the command surface is in [CLI-SPEC.md](CLI-SPEC.md).

## Host endpoint records

`augeaswrap.endpoints` stores host facts supplied by their owner. The
service installer is the only place that selects a default socket path; it
publishes the already resolved path, its directory when static consumers need
one, and any host GIDs. A consumer reads only the process environment and does
not add a literal path or GID fallback.

An installer can publish from its post-configuration values:

```python
from augeaswrap.endpoints import publish

publish(
    "cgprofile",
    {
        "CGPROFILE_CONTROL_SOCKET": control_socket,
        "CGPROFILE_CONTROL_SOCKET_DIR": control_socket_dir,
        "CGPROFILE_CONTROL_GID": str(control_gid),
        "CGPROFILE_OBSERVER_SOCKET": observer_socket,
        "CGPROFILE_OBSERVER_SOCKET_DIR": observer_socket_dir,
        "CGPROFILE_OBSERVER_GID": str(observer_gid),
    },
    writer=f"cgprofiled install {version}",
)
```

`control_socket`, `observer_socket`, their derived directories, and group IDs
come from the service's active configuration and resolved host groups. There
is no helper default or compatibility path. `publish` replaces only the
`cgprofile` fragment, then renders the shared aggregate and generated
`/etc/environment` block. Installation and removal are serialized by
`/etc/host-endpoints.d/.lock`; `withdraw("cgprofile")` should run before the
installer removes its socket units or runtime directories.

If `publish` warns that another owner's fragment is damaged, that owner's
variables are omitted from the regenerated aggregate while valid owners are
preserved. Use `augeaswrap endpoints check --json` to inspect the damage, then
rerun the damaged owner's install verb so it can replace its own fragment.

A client requires its endpoint before connecting:

```python
from augeaswrap.endpoints import require_env

socket_path = require_env(
    "CGPROFILE_CONTROL_SOCKET",
    remedy=(
        "log in again, or `set -a; . /etc/host-endpoints.env`, "
        "or run `cgprofiled install`"
    ),
)
```

An unset or empty variable raises `CliFailure` naming the variable and the
remedy. A client may accept an explicit `--socket` override, but its implicit
path always comes from `require_env`. The `augeaswrap` CLI is useful for
host-side operations:

```bash
python3 -m augeaswrap endpoints publish \
  --owner cgprofile --writer 'cgprofiled install 3.0.0' \
  --entry CGPROFILE_CONTROL_SOCKET=/run/cgprofile/control/cgprofile.sock \
  --entry CGPROFILE_CONTROL_SOCKET_DIR=/run/cgprofile/control
python3 -m augeaswrap endpoints check --json
python3 -m augeaswrap endpoints show
python3 -m augeaswrap endpoints withdraw --owner cgprofile
```

Owner names match `^[a-z][a-z0-9-]{0,62}$` (`cgprofile`, not `CgProfile`), and
the shell and loader keys (`PATH`, `HOME`, `IFS`, `PROMPT_COMMAND`, `SHLVL`,
`TMPDIR`, ... and every `LD_*`, `PYTHON*`, `BASH_*` and `LC_*` key; the full
rule is in [`SPEC.md` Endpoint records](../SPEC.md#endpoint-records)) are reserved:
use service-specific keys such as `CGPROFILE_...`. A hand-edited fragment of
your own owner is healed by re-running the install verb.
`withdraw` of an owner that never published is a no-op that reports so.

`plan` performs the same alphabet, collision, symlink, and marker checks
without changing files. `verify` compares one owner's fragment with its live
expected mapping; `check` compares all fragments with the aggregate, verifies
the environment block and profile source, and reports wrong modes, wrong
ownership (real `/` only), stale temporary files, or symlinks. `check` waits
for a publish in flight, and on a host where nothing was published it passes
with `no endpoints published`. The exact header and marker format is in
[`SPEC.md` Endpoint records](../SPEC.md#endpoint-records) and the
[endpoint CLI specification](CLI-SPEC.md).

## Guarded applier

Use the applier when a caller has already decided the desired host
configuration. A request names every file and state explicitly; Augeaswrap
does not keep an owner registry, infer an uninstall set, or roll back a failed
apply. Keep a copy of the request with the caller's installation record when
the caller needs to remove its own files later.

This request creates a validated JSON configuration and checks that an
executable accepts the expected argv. The file's state and mode are explicit:

```toml
augeaswrap = 1
writer = "example-service install 2.1.0"

[[file]]
path = "/etc/example-service/settings.json"
state = "present"
text = "{\"listen\": \"/run/example-service.sock\"}\n"
mode = "0640"
validate = "json"

[[check]]
state = "present"
exec = ["/usr/bin/example-service", "--check-config", "/etc/example-service/settings.json"]
expect_exit = 0
timeout = 10.0
```

Preview the plan against a disposable root, then apply it. `apply` prompts on a
terminal; with non-interactive stdin, pass `--yes` after reviewing the plan.
Each path shown in the request is interpreted below the selected root.

```bash
augeaswrap plan request.toml --root ./fake-root
augeaswrap apply request.toml --root ./fake-root --yes
augeaswrap journal list --root ./fake-root
augeaswrap journal show 20261108T120000Z-a1b2 --root ./fake-root --json
```

With `--json`, each command emits one document. Apply and revert include both
the reviewed plan and the resulting transaction data, so a caller can parse
one response instead of combining separate plan and result streams.

To remove a file, submit an `absent` request for that exact path. The original
bytes and metadata remain in the transaction pre-image and can be restored by
an explicit revert while no later transaction has touched the path:

```toml
augeaswrap = 1
writer = "example-service uninstall 2.1.0"

[[file]]
path = "/etc/example-service/settings.json"
state = "absent"
```

```bash
augeaswrap plan remove-settings.toml --root ./fake-root
augeaswrap apply remove-settings.toml --root ./fake-root --yes
augeaswrap revert 20261108T120000Z-a1b2 --root ./fake-root --yes
```

The revert is recorded as a new transaction. It restores the saved bytes and
metadata, then reruns any recorded post-actions associated with the restored
path. A transaction journal is only readable under the same `--root` where it
was created.

Python callers use the same resource objects and do not prompt. The library
call to `apply(plan)` is the caller's acceptance of that plan:

```python
import augeaswrap as aw

request = aw.Request(
    writer="example-service install 2.1.0",
    resources=(
        aw.File(
            path="/etc/example-service/settings.json",
            state="present",
            text='{"listen": "/run/example-service.sock"}\n',
            mode="0640",
            validate="json",
        ),
        aw.Check(
            state="present",
            exec=("/usr/bin/example-service", "--check-config", "/etc/example-service/settings.json"),
            expect_exit=0,
            timeout=10.0,
        ),
    ),
)
plan = aw.plan(request, root="./fake-root")
result = aw.apply(plan)
if result.txn:
    print(aw.journal(result.txn, root="./fake-root"))
```

`Request.to_toml()` renders the same strict schema for a saved handoff. A
second plan after a successful apply contains only noops and creates no
transaction. If apply fails after committing files, inspect its journal; the
tool stops at the first failure and leaves the explicit revert decision to the
caller. If the journal says a post-image is unknown, revert refuses because
the current state cannot be compared safely. The frozen resource and context
contract is in the [specification](../SPEC.md#guarded-applier-protocol-hc-1a).

### JSON keys and pending restarts

Use `json_key` when one caller owns a particular nested setting in a shared
JSON file. The request below sets Docker's log driver, validates the staged
whole file with `dockerd`, and records a restart for a maintenance window:

```toml
augeaswrap = 1
writer = "docker-policy install 1.0.0"

[[json_key]]
file = "/etc/docker/daemon.json"
key = ["log-driver"]
state = "present"
value = "journald"
validate = "dockerd"
on_change = { unit = "docker.service", action = "pending" }
```

Plan and apply as usual, then inspect unresolved restarts:

```bash
augeaswrap plan docker-policy.toml
augeaswrap apply docker-policy.toml --yes
augeaswrap journal --pending --json
```

`key` is a list of object-key segments. Missing parents are created when
setting a value; an existing scalar parent is refused. Deleting a missing key
is a reported no-op that leaves the original file bytes untouched, including
noncanonical formatting. Changed JSON is emitted with two-space indentation
and a final newline, preserving existing key order and appending new keys. The
first changed request can therefore show a whole-file formatting diff.
`json_key` preserves existing file owner and mode. One request can include only
one `json_key` resource for a given file. The signed-64-bit and finite-float
domain checks apply to the request `value`; values already in the document
pass through unchanged when a sibling key is updated.

Each `json_key` request must include an `on_change` table and a unit. Use
`action = "restart"` to run the restart after commit, `"pending"` to leave it
for maintenance, or `"none"` to request no restart. `journal --pending`
queries `ActiveEnterTimestampMonotonic`, `ActiveState`, and `LoadState` and
requires all three properties. It checks the active-enter timestamp against
the transaction time. A unit that has never entered `active` or is missing
remains listed with its unit state. A non-zero query with the exact diagnostic
`Unit <unit> could not be found.` is also treated as a missing unit; its
active-enter timestamp is null. Other query errors, missing properties, and
inconsistent properties are refusals. Pending state is derived from journals
and is not a separate queue. `validate = "dockerd"` checks only the staged
configuration with the installed binary; it does not start Docker.

### Post-action phases

The engine assigns phases to recognized argv forms. When a third-party
resource kind declares a command the engine does not recognize, provide its
phase explicitly or planning refuses it:

```python
from augeaswrap import PostAction

refresh = PostAction(
    "refresh example index",
    ("example-index", "refresh"),
    30.0,
    phase="runtime",
)
```

The shared phases are `before_commit`, `sysusers`, `tmpfiles`,
`daemon_reload`, `sysctl`, `runtime`, `gpasswd`, and `endpoint`. Unknown argv
forms without an explicit phase are a breaking validation error for custom
`PostAction` callers. The engine orders actions by phase and keeps request order
within each phase; `before_commit` actions run immediately before their
resource's commit. `pending=True` records an action in the journal without
executing it and returns its name in `Result.pending`.

### Systemd units and drop-ins

Use `unit` for a whole unit or one drop-in. `state`, `text`, and `mode` are
explicit. Every present resource states `intent = "authoritative"` or
`intent = "fallback"` for the layer it contributes. Augeaswrap snapshots the
per-unit drop-in directories so edits or new `.conf` files make a saved plan
stale. It does not apply generic directive shadow analysis because systemd
directives can reset or accumulate values; `systemd-analyze verify` checks the
syntax. A whole present unit
also states its desired enablement and what to do after a change; a drop-in
never enables the unit. `intent` is invalid on an absent unit or drop-in.

```toml
augeaswrap = 1
writer = "example-service install 2.1.0"

[[unit]]
name = "example.service"
state = "present"
text = """
[Unit]
Description=Example service
After=network.target

[Service]
ExecStart=/usr/local/lib/example/bin/example
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""
mode = "0644"
intent = "authoritative"
enable = true
on_change = "restart"
preflight = ["/usr/local/lib/example/bin/example", "--check-config"]

[[unit]]
name = "docker.socket"
state = "present"
dropin = "50-example.conf"
text = """
[Socket]
ListenStream=/run/example-docker.sock
"""
mode = "0644"
intent = "fallback"
on_change = "pending"
```

On a live root, `enable` is reconciled with `systemctl is-enabled -- <name>`
even when the unit file already matches; no enable/disable action is emitted
when the requested state already holds. The enable state is visible in the
plan and its command is recorded in the transaction journal. Successful
post-images are captured after reconciliation, so later revert checks use the
final manager state.

The staged tree contains existing valid unit files and resolvable aliases from
the static `/usr/local/lib` and `/usr/lib` unit trees, the requested unit, its
same-name drop-ins, and referenced dependency units from every selected search
directory. Each `Exec*=` target is a temporary empty mode-0755 stub during
`systemd-analyze verify`; the selected-root executable check still inspects the
real target separately. Use `/usr` executable paths on merged-usr hosts because
`/bin`, `/sbin`, and `/lib` are symlinks and symlink components are refused.
Each executable and its parent components must be owned by uid 0 even under a
fake root. The plan says when
syntax verification is skipped:
either `systemd-analyze` is missing, or a drop-in has no base unit anywhere in
the selected root. In both cases Exec checks and any configured preflight
still run. Unit text cannot contain NUL and is limited to 1 MiB of UTF-8 bytes,
including drop-ins with skipped verification.

Each executable token must be absolute. Specifiers such as `%h` and variable
references such as `$BINARY` are not expanded. The normalized path is resolved
inside the selected root before `/tmp`, `/var/tmp`, and `/home` are refused;
raw `..`, symlink components, non-root-owned components, and group/other-write
components are refused. Live unit files default to owner/group `root:root`;
existing managed files and their parent directories must have safe ownership
and modes. Do not request `enable = true` for static, indirect, generated, or
transient units, which systemd cannot enable. When `systemctl is-enabled`
returns non-zero with no output for a not-yet-created unit, Augeaswrap treats it
as disabled and can plan the requested enablement; empty output with a
successful exit remains an error. `preflight` is argv-only with a 30-second timeout. The socket
drop-in records a pending restart in the journal instead of restarting the
live socket.

Remove one file explicitly. Removing a whole unit journals stop and disable
as separate pre-commit actions, deletes the file, then runs `daemon-reload`
and `reset-failed`. A removed
drop-in only deletes the drop-in and reloads the unit:

```toml
augeaswrap = 1
writer = "example-service uninstall 2.1.0"

[[unit]]
name = "example.service"
state = "absent"

[[unit]]
name = "docker.socket"
state = "absent"
dropin = "50-example.conf"
on_change = "pending"
```

```python
from augeaswrap import Request, Unit

request = Request(
    "example-service install 2.1.0",
    (
        Unit(
            name="example.service",
            state="present",
            text=unit_text,
            mode="0644",
            intent="authoritative",
            enable=True,
            on_change="restart",
            preflight=("/usr/local/lib/example/bin/example", "--check-config"),
        ),
    ),
)
```

Drop-ins whose base file is missing from every selected-root systemd search
directory skip staged `systemd-analyze verify`; the plan prints that reason,
and exec/preflight checks still run. A missing `systemd-analyze` binary also
skips only syntax verification and is reported in the plan. See the exact
staging and ownership contract in
[SPEC.md](../SPEC.md#unit-and-drop-in-behavior).


### Augeas-backed edits and fstab helpers

Use `[[edit]]` when a format-specific Augeas lens can update selected records
while preserving comments and unrelated lines. The edit kind stages a private
copy, shows the staged unified diff, validates the staged file, and journals
the original bytes and metadata for revert. It needs the `augeas-tools`
package (`augtool` and `augparse`). See the complete operation and validator
contract in [SPEC.md](../SPEC.md#augeas-backed-edit-behavior) and the
[design rationale](DESIGN-GUIDE.md#augeas-edit-resources).

This request adds one mount and declares the complete desired swap set. The
exclusive swap selector removes any existing swap row not listed here:

```toml
augeaswrap = 1
writer = "example-storage install 1.0.0"

[[edit]]
file = "/etc/fstab"
lens = "Fstab"
state = "present"
create = false
validate = "findmnt"
on_change = "daemon-reload"
ops = [
  { records = "*", key = "file", exclusive = false, items = [
    { spec = "UUID=data-1234", file = "/srv/data", vfstype = "ext4", opt = ["defaults", "noatime"], dump = "0", passno = "2" },
  ] },
  { records = "*[vfstype='swap']", key = "spec", exclusive = true, items = [
    { spec = "PARTUUID=swap-1234", file = "none", vfstype = "swap", opt = ["sw", "pri=10"], dump = "0", passno = "0" },
  ] },
]
```

`findmnt` validates against live mount and device state, so planning with a
fake `--root` reports `validate: skipped(root)` and does not run it. A live
install should schedule the request when the target devices and mount points
exist. Replacing a matching record normalizes that record's formatting; other
records and comments remain in the file. Repeating the same desired records
is a no-op.

For callers that build requests in Python, the helpers in
`augeaswrap.formats.fstab` produce correctly ordered Fstab lens records and
`records` operations. Each mount option is a repeated `opt` label, and swap
priority is written as `pri=N`. Each `opt` list element must be one comma-free
option. `mount_ops` returns one operation for each mountpoint, and each
operation sees the staged result of earlier operations.
Both wrappers validate field values and exact lens field order again, including
when passed hand-built mappings:

```python
from augeaswrap import Edit, Request
from augeaswrap.formats.fstab import mount_item, mount_ops, swap_item, swap_op

mount = mount_item("UUID=data-1234", "/srv/data", "ext4", ["defaults", "noatime"], "0", "2")
swap = swap_item("PARTUUID=swap-1234", 10)
edit = Edit(
    file="/etc/fstab",
    lens="Fstab",
    state="present",
    create=False,
    validate="findmnt",
    on_change="daemon-reload",
    ops=(*mount_ops([mount]), swap_op([swap])),
)
request = Request("example-storage install 1.0.0", (edit,))
```

The helpers reject empty devices, whitespace or `#` in fields, unexpected
fields, and duplicate mount points. Use `swap_op([])` only when the intended
result has no swap rows.

### pam_env overlay and environment helpers

Use the packaged `Pam_env` lens for `/etc/security/pam_env.conf` and simple
`/etc/environment` records. This request adds one environment value and one
`pam_env.conf` default/override pair:

```toml
augeaswrap = 1
writer = "example-login install 1.0.0"

[[edit]]
file = "/etc/environment"
lens = "Pam_env"
state = "present"
create = false
on_change = "none"
ops = [
  { records = "*[kind='environment']", key = "name", exclusive = false, items = [
    { kind = "environment", name = "SITE_MODE", value = "production" },
  ] },
]

[[edit]]
file = "/etc/security/pam_env.conf"
lens = "Pam_env"
state = "present"
create = false
on_change = "none"
ops = [
  { records = "*[kind='conf']", key = "name", exclusive = false, items = [
    { kind = "conf", name = "REMOTEHOST", default = "localhost", override = "@{PAM_RHOST}" },
  ] },
]
```

For Python callers, use the helpers so whitespace and equals-bearing values
get the correct quote delimiter. Ordinary commas remain literal:

```python
from augeaswrap import Edit, Request
from augeaswrap.formats.pam_env import (
    environment_item,
    environment_op,
    pam_env_item,
    pam_env_op,
)

environment = Edit(
    file="/etc/environment",
    lens="Pam_env",
    state="present",
    create=False,
    on_change="none",
    ops=[environment_op([
        environment_item("SITE_MODE", "production"),
        environment_item("GREETING", "hello world"),
        environment_item("CSV_VALUES", "one,two,three"),
    ])],
)
pam_env = Edit(
    file="/etc/security/pam_env.conf",
    lens="Pam_env",
    state="present",
    create=False,
    on_change="none",
    ops=[pam_env_op([
        pam_env_item("REMOTEHOST", default="localhost", override="@{PAM_RHOST}"),
        pam_env_item("LESS", default="M q e h15 z23 b80"),
        pam_env_item("LIST", default="a,b,c"),
    ])],
)
request = Request("example-login install 1.0.0", (environment, pam_env))
```

The helpers choose the other quote delimiter when a value contains one quote
character. They refuse values containing both quote characters (environment),
a double quote in a `pam_env.conf` value, `#` in an environment value, NUL,
newline, carriage return, unsupported whitespace, invalid names, duplicate
names, and hand-built mappings with the wrong fields or field order. The first
lens cut does not parse `export NAME=VALUE` or backslash-joined values; see
AW-BL-25 in [BACKLOG.md](../BACKLOG.md).


### Layered files, groups and endpoint records

Use `intent` on each sysctl, sysusers or tmpfiles resource. `authoritative`
means this file must supply the effective value; `fallback` means another
setting must win. `accept_shadow` records one reviewed exception by exact file
and key. A new drop-in or an edit to any observed drop-in makes the saved plan
stale.

A same-basename vendor fragment is a whole-file mask, even if the `/etc` copy
wins. Review and accept only that exact mask when it is intentional:

```toml
augeaswrap = 1
writer = "example-service install 2.1.0"

[[sysctl]]
file = "60-example.conf"
state = "present"
intent = "authoritative"
apply_now = false
entries = [{ key = "net.ipv4.ip_forward", value = "0" }]
accept_shadow = [{ file = "/usr/lib/sysctl.d/60-example.conf", key = "$file" }]
```

This request adds a fixed-GID service group before publishing its endpoint,
applies a sysctl fragment when asked, and creates a tmpfiles directory rule:

```toml
augeaswrap = 1
writer = "example-service install 2.1.0"

[[sysusers]]
file = "60-example.conf"
state = "present"
intent = "authoritative"
groups = [{ name = "example", gid = 430, not_gid = [0], distinct_from = ["users"] }]
members_add = [{ user = "alice.smith", group = "example" }]
members_absent = [{ user = "buildbot$", group = "users" }]

[[endpoint]]
name = "example"
state = "present"
entries = [
  { key = "EXAMPLE_SOCKET", value = "/run/example.sock" },
  { key = "EXAMPLE_GID", value = "@gid:example" },
]

[[sysctl]]
file = "60-example.conf"
state = "present"
intent = "fallback"
apply_now = true
entries = [{ key = "net.ipv4.ip_forward", value = "0", comment = "example host policy" }]
accept_shadow = [{ file = "/usr/lib/sysctl.d/50-vendor.conf", key = "net.ipv4.ip_forward" }]

[[tmpfiles]]
file = "60-example.conf"
state = "present"
intent = "authoritative"
text = "d /var/lib/example 0750 example example -\n"
```

The endpoint GID reference resolves during planning from the earlier effective
sysusers declaration when it includes a fixed `gid`, or from an existing
`/etc/group` entry. An automatically allocated GID has no trustworthy value at
plan time and is refused. A fixed GID held by another group also refuses the
plan, before endpoint publication can be scheduled. Existing `/etc/group`
references require a valid, unique GID. Sysusers names must match
`[A-Za-z_][A-Za-z0-9_-]{0,30}`; numeric GIDs are 32-bit except the reserved
values 65535 and 4294967295. Declare a group name in only one sysusers resource
per request. Shadow checks also recognize groups implied by vendor `u`/`u!`
user declarations and `m` membership lines. Membership account names may
contain periods and may end in `$`; they must refer to an existing account
when added, and membership groups must exist or be declared in the request.
The group creation, membership changes, live sysctl values and tmpfiles paths
are listed as irreversible
journal effects; revert restores the fragment bytes only.

### Membership-only sysusers requests

Set `groups = []` when the request should only add or remove memberships. This
keeps the named fragment's current bytes, including any old group rows; it does
not create an empty fragment when none exists. To update group definitions,
submit their desired rows explicitly.

```toml
augeaswrap = 1
writer = "example-service memberships 2.1.0"

[[sysusers]]
file = "60-example.conf"
state = "present"
intent = "fallback"
groups = []
members_add = [{ user = "alice", group = "users" }]
members_absent = []
```

The tmpfiles dry-run receives the absolute private staged fragment path, so it
checks the bytes in this request even when an older installed copy exists.
Entry types are passed to systemd as written. The caller owns their effects;
types such as `R` and `Z` can remove paths or recursively change ownership.

To withdraw an endpoint, submit an absent resource for that exact owner. If
the same request also removes a unit, place the endpoint table first; planning
refuses a request that schedules the unit removal first.

```toml
augeaswrap = 1
writer = "example-service uninstall 2.1.0"

[[endpoint]]
name = "example"
state = "absent"

[[group]]
name = "example"
state = "absent"
```

The endpoint withdrawal runs before the unit's removal action. Keep the
caller's group when it is still needed. The group removal above requests
`groupdel` and is refused while an existing user has that group as its primary
group; removal of a group with GID 0 is also refused. A changed `[[group]]`
resource makes its entire transaction non-revertible, including the endpoint
withdrawal and any sibling file changes. Put the group removal in a separate
request when those changes need an independent revert. Do not include
`members_add` or `members_absent` for a group removed by
the same request: planning returns `sysusers.member.group.removed` regardless
of resource order. A prior membership change can run in a separate request when
it remains meaningful before the later group removal.

When using `--root`, Augeaswrap writes endpoint fragments and generated files
inside the selected root. A plan's `skipped(root)` endpoint action means the
live CLI subprocess is skipped; it does not skip those local file writes.

For `sysusers` or `tmpfiles`, `state = "absent"` deletes the named drop-in;
it does not remove created accounts, groups, or paths. A separate
group resource can remove a caller-declared group when the result leaves all
users with valid primary GIDs. Group deletion is not revertible. A request also
cannot remove and define the same group. The kind schemas, shadow order and
effect journal are specified in
[SPEC.md](../SPEC.md#layered-resources-and-shadow-intent).
