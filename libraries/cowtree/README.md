# cowtree

`cowtree` is a stdlib-only workspace library for disposable trees and files. It
selects btrfs snapshots, reflink copies, or ordinary copies behind one `Run`
API ([backend behavior](DESIGN-GUIDE.md#one-adapter-for-three-storage-paths)),
keeps owner manifests and flock leases beside each run, and reaps only dead
flock-owned runs. While a lease is active it also refuses replaced run,
`trees/`, or run-owned tree paths when their identity check detects the change;
see the [ownership rules](DESIGN-GUIDE.md#ownership-and-identity). Cloning a
copy or reflink tree sealed with `Run.seal()` restores owner write permission
so the clone can be used as a candidate. A destination collision leaves the
existing path intact. It does not know about Git, assay, lanes, VMs, or Docker.
The embedded API advertises `API_VERSION = 1`.
The CLI provides `detect`, `survey`, `gc`, and `make-shared`. A run can declare
an external owner with `external_ref`; cowtree never reaps external runs.

ZFS uses the reflink backend when block cloning is available; cowtree does not
provide a native ZFS dataset backend.

The area is enabled by `MDT_COW_ROOT` when `COWTREE_MODE=auto` (the default).
With the root unset, cowtree logs one INFO line and uses a plain temporary
directory. `COWTREE_MODE=off` forces that plain path; any other mode is
refused. A configured but invalid area is refused before a run directory is
allocated. Space admission uses the consumer's input-derived `SpaceEstimate`
and the [backend-specific formulas](DESIGN-GUIDE.md#input-derived-space-admission);
it has no fixed floor.

See the [consumer guide](CONSUMERS.md) for copyable examples and the
[specification](SPEC.md) for operation semantics. The package is embedded in
consumer wheels; there is no index-bound `cowtree` dependency.

Qualify the source with its registered tester-unified lane:

```console
run-gate cowtree
```
