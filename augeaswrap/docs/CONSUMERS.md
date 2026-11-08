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
    writer=f"cgpd install {version}",
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
        "or run `cgpd install`"
    ),
)
```

An unset or empty variable raises `CliFailure` naming the variable and the
remedy. A client may accept an explicit `--socket` override, but its implicit
path always comes from `require_env`. The `augeaswrap` CLI is useful for
host-side operations:

```bash
python3 -m augeaswrap endpoints publish \
  --owner cgprofile --writer 'cgpd install 2.3.0' \
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
