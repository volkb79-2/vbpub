# Consuming cowtree

`cowtree` is embedded from `libraries/cowtree/src/cowtree` into a consumer
wheel. Do not install a separately published or index-resolved package. During
development, put the source directory on `PYTHONPATH`.

The consumer owns its input facts and supplies them as a `SpaceEstimate`:

```python
from pathlib import Path

from cowtree import SpaceEstimate, open_run

input_tree = Path("/srv/templates/base")
ingest_bytes = sum(
    path.lstat().st_size
    for path in input_tree.rglob("*")
    if not path.is_symlink() and path.is_file()
)
estimate = SpaceEstimate(
    ingest_bytes=ingest_bytes,
    per_clone_bytes=ingest_bytes,  # conservative when divergence history is unavailable
    max_live_clones=1,
)

with open_run(
    "example-tool",
    estimate=estimate,
    labels={"purpose": "fixture"},
) as run:
    template = run.ingest_tree(input_tree, "template")
    run.seal(template)
    candidate = run.clone_tree(template, "candidate-001")
    if run.caps.cheap_clone:
        run.reserve(SpaceEstimate(0, ingest_bytes, 1))
    print(f"Prepared {candidate} with the {run.caps.backend} backend")
```

The estimate must come from the consumer's input or measured write history; do
not substitute a fixed size or percentage. `SpaceRefused` carries
`needed_bytes`, `available_bytes`, and the estimate. Catching it to switch to a
plain directory would bypass the admission decision.
After sealing a copy or reflink template with `Run.seal()`, `clone_tree()`
restores owner write permission recursively, so the candidate can be changed
and discarded. Unsealed source modes remain unchanged; btrfs
snapshots become writable by clearing the snapshot's read-only flag.

## Area selection and commands

With `MDT_COW_ROOT` unset, cowtree logs one INFO line and returns a `Run` using
the `copy` backend under the selected plain directory (by default
`tempfile.gettempdir()`). Set `COWTREE_MODE=off` to force this path even when
the root variable is set. The only accepted mode values are `auto` and `off`;
other values raise `RootInvalid`. A set but invalid root raises `RootInvalid`;
callers must surface the infrastructure error rather than inventing another
root.

While a run is open, cowtree checks that its run directory, `trees/`, and
run-owned top-level trees or files still refer to their original
`(st_dev, st_ino)` pairs. If a path is already replaced when the check runs,
the operation refuses. Use the owning `Run` methods instead of mutating its
managed paths from a second process. Cowtree does not isolate against a hostile
process with the same effective UID; see the [design guide](DESIGN-GUIDE.md#ownership-and-identity).
If an exclusive create loses a destination-name race, cowtree raises
`PathRefused` and leaves the path created by the other writer untouched.
Partial tree cleanup removes only backend-created entries; an unowned child is
preserved, and `close()` can report that the run directory still contains it.

`clone_file` can write into an existing nested directory under `trees/`; its
top-level directory then belongs to the run and is removed at close. A
top-level file destination is tracked and removed the same way.

```console
python -m cowtree detect --json
python -m cowtree survey --json
python -m cowtree gc --json
python -m cowtree gc --scan /tmp/cowtree-runs --json
python -m cowtree make-shared template-cache --compression zstd:3
```

`gc` deletes a flock-owned run only after it acquires the lease and verifies
the recorded owner process is dead. It keeps runs whose owner identity cannot
be compared from the current host, boot, or PID namespace, along with external
or invalid runs. A boot-ID difference on another hostname is not proof of
death. For btrfs runs it destroys top-level subvolumes through the
btrfs backend before removing the run directory.

The backend values are `btrfs`, `reflink`, and `copy`; CLI detection reports
`off` when no area is enabled. `make-shared` accepts `zstd`, `zstd:N` (levels
1 through 15), `lzo`, or `none`; non-btrfs backends ignore per-tree compression
with an INFO message. On ZFS, configure a root with block cloning available;
cowtree selects `reflink` there and has no native ZFS backend. Shared trees are
retained by `gc`.

An external owner can make its reference explicit:

```python
base_image = Path("/srv/vm/base.qcow2")
base_allocated_bytes = base_image.stat().st_blocks * 512
run = open_run(
    "vmctl",
    estimate=SpaceEstimate(base_allocated_bytes, base_allocated_bytes, 1),
    holder="external",
    external_ref="vm-run:example-42",
)
```

External references are opaque owner labels, not proof of a live VM. Cowtree
never reaps an external holder; the VM owner must close and delete its run
after checking its own running and stopped users.

## Qualify the library source

The registered `cowtree` lane runs in `tester-unified` and enforces 100% line
and branch coverage over the library. It includes fake btrfs ioctl operations
and the opt-in live suite, which skips unless `COWTREE_LIVE_ROOT` points at a
CoW-capable area. `COWTREE_LIVE_ROOT` is reserved for a scratch-VM live run;
the root must also be visible inside the tester container. Run the lane from
this directory:

```console
run-gate cowtree
```
