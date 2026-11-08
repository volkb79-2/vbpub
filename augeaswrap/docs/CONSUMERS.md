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

### Systemd units and drop-ins

Use `unit` for a whole unit or one drop-in. `state`, `text`, and `mode` are
explicit. Every present resource states `intent = "authoritative"` or
`intent = "fallback"` for the layer it contributes; current directive-level
shadow enforcement is provided by the shared HC-1b-s work. A whole present unit
also states its desired enablement and what to do after a change; a drop-in
never enables the unit.

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
directory. The plan says when
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
and modes. `preflight` is argv-only with a 30-second timeout. The socket
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
