# Consumer examples

The installed `mounttrace` command runs on Python 3.11 or newer and depends on
the released `cli-extended` library. The analysis core remains
stdlib-and-no-subprocess, but the former single-file copy deployment was
replaced by a wheel/venv install.

All commands use the same path syntax: an absolute path means the invoking
process's view; `PID:/path` means that process's root and mount namespace.

## Install without PyPI

The package names are deliberately absent from PyPI. Install both verified wheels
from the cli-extended and mounttrace GitHub Releases into a host-local venv:

```bash
install_root=/opt/mounttrace
python3 -m venv "$install_root/venv"
"$install_root/venv/bin/python" -m pip install --no-index --find-links wheels \
    'cli-extended==0.6.0' 'mounttrace==3.2.0'
sudo ln -s "$install_root/venv/bin/mounttrace" /usr/local/sbin/mounttrace
"$install_root/venv/bin/mounttrace" --version
```

`wheels` is a directory containing the SHA-256-verified release artifacts.
Choose the installed mounttrace version explicitly as well; the example above
relies on a local index containing only the reviewed pair. The command and
analysis read live `/proc` data; no network or package lookup happens at run
time.

Explain the write target and visible mount boundaries:

```bash
host_path=${HOST_PATH:?set this to an absolute host path}
container_path=${CONTAINER_PATH:?set this to an absolute path inside the container}
container_id=${CONTAINER_ID:?set this to the Docker name or ID}
container_pid=$(docker inspect -f '{{.State.Pid}}' "$container_id")
mounttrace explain "$host_path" --submounts
sudo mounttrace explain "$container_pid:$container_path"
```

Compare a host path with its view in a container. A difference returns status
3, so scripts can distinguish it from an operational error (status 1):

```bash
volumes_root=${PTERODACTYL_VOLUMES_ROOT:?set this to the host volume root}
instance_uuid=${INSTANCE_UUID:?set this to the full instance UUID}
container_id=${CONTAINER_ID:?set this to the Docker name or ID}
container_pid=$(docker inspect -f '{{.State.Pid}}' "$container_id")
sudo mounttrace compare \
    "$volumes_root/$instance_uuid" "$container_pid:/home/container"
```

The `compare` table includes each input path as well as its view. This is
important when the same view appears more than once:

```bash
volumes_root=${PTERODACTYL_VOLUMES_ROOT:?set this to the host volume root}
instance_a=${INSTANCE_A:?set this to an instance UUID}
instance_b=${INSTANCE_B:?set this to an instance UUID}
instance_c=${INSTANCE_C:?set this to an instance UUID}
sudo mounttrace compare \
    "$volumes_root/$instance_a/WS/Saved/Logs" \
    "$volumes_root/$instance_b/WS/Saved/Logs" \
    "$volumes_root/$instance_c/WS/Saved/Logs"
```

Each result row remains identifiable by its `PATH`, even though every row's
`VIEW` is `host`.

Audit mountpoints under a host volume prefix and a container prefix:

```bash
volumes_root=${PTERODACTYL_VOLUMES_ROOT:?set this to the host volume root}
instance_uuid=${INSTANCE_UUID:?set this to the full instance UUID}
container_id=${CONTAINER_ID:?set this to the Docker name or ID}
container_pid=$(docker inspect -f '{{.State.Pid}}' "$container_id")
sudo mounttrace audit "$volumes_root/$instance_uuid" \
    "$container_pid:/home/container"
```

Predict propagation, or run the combined Soulmask state and volume audit. The
`soulmask` state file defaults to `/run/soulmask_tmpfs.state`; pass
`--state-file FILE` to `soulmask` only when the service uses another path:

```bash
volumes_root=${PTERODACTYL_VOLUMES_ROOT:?set this to the host volume root}
instance_uuid=${INSTANCE_UUID:?set this to the full instance UUID}
container_id=${CONTAINER_ID:?set this to the game container name or ID}
wings_container=${WINGS_CONTAINER:?set this to the Wings container name or ID}
wings_pid=$(docker inspect -f '{{.State.Pid}}' "$wings_container")
game_pid=$(docker inspect -f '{{.State.Pid}}' "$container_id")
volume_path="$volumes_root/$instance_uuid/WS/Saved/Logs"
sudo mounttrace predict "$volume_path" "$wings_pid" "$game_pid"
sudo mounttrace soulmask "$wings_pid" --volumes-root "$volumes_root"
```

The path passed to `predict` may be a not-yet-created mountpoint. Its missing
leaf is expected and does not add a lexical-analysis note. When `explain`
reports an overlay `upperdir`, it is the writable backing layer named by
mountinfo, not a separate volume; mounttrace shows that value without resolving
it through the inspected process's path view.

To avoid path resolution and filesystem probes, add `--no-probe`; `soulmask`
still reads its state file. Use `--json` for one structured report per pass,
including when `--watch` is active. Human output escapes terminal controls as visible
`\xNN` and selected bidi or format controls as `\uNNNN`; pass the
original path to the command.

`--watch` is line-buffered. Redirecting the first pass and later change reports
to a file or pipe works without `PYTHONUNBUFFERED`:

```bash
sudo mounttrace --json --watch 1 soulmask "$wings_pid" \
    --volumes-root "$volumes_root" > soulmask-watch.ndjson
```

At 80 columns, crowded tables put long paths on their own labeled line and
middle-cut them there; the end, including the `WS/Saved/Logs` tail, remains
visible. Use `-v` or `--json` when a difference could fall in the omitted
interior of a path, including a difference only in the first UUID group.

## Run the gates

From `scripts/mounttrace`, use the registered namespace-free gate. The VM lane
adds user-namespace, btrfs and Docker scenarios on its isolated QEMU guest.

```bash
run-gate suite
testing/vm/setup-vm-lab.sh   # once per checkout, on a configured CoW host
testing/vm/run-vm-tests.sh
```

`run-vm-tests.sh` validates `tests/test-inputs.txt` before building or booting;
`run-vm-tests.sh --check-inputs-only` runs just that local preflight.
