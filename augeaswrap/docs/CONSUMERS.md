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
members_add = []
members_absent = []

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

The endpoint GID reference resolves during planning from the earlier
sysusers declaration when it includes a fixed `gid`, or from an existing
`/etc/group` entry. An automatically allocated GID has no trustworthy value at
plan time and is refused. Declare a group name in only one sysusers resource
per request. Shadow checks also recognize groups implied by vendor `u`/`u!`
user declarations and `m` membership lines. The group creation, membership changes, live sysctl
values and tmpfiles paths are listed as irreversible journal effects; revert
restores the fragment bytes only.

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
group. Do not include `members_add` or `members_absent` for a group removed by
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
