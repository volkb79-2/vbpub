# fleetd

`fleetd` is the small, central scheduling service proposed for distributing
long Assay campaigns across enrolled compute hosts. This first package ships
the data contracts, a read-only host-facts collector, and a deterministic
RAM-first planner. It has no daemon, sockets, transport, or remote execution.

The planner produces per-host candidate lists and shell commands that use
Run-Gate's existing `--only-candidates FILE` option. Candidate RAM is resolved
from its own measurement, the same-file median, the lane median, then the
declared lane demand. Overcommit applies to expected sums; a single candidate
must still fit physical lane RAM. Geekbench single-core scores and one factor
per host inform ETA only. See the [design guide](docs/DESIGN-GUIDE.md) for the
reasons and the [consumer guide](docs/CONSUMERS.md) for runnable examples.
The planner writes candidate ID files in its checkout; copy the matching host
section and those files to the host's matching checkout before running them.
Fleetd has no transport.
Serialized campaign inputs use the versioned `host-facts/1`, `work-request/1`,
and `assay-merge-manifest/1` contracts.

`fleet facts collect` reads only host-owned inputs and the read-only `/proc`
and `/sys` interfaces. Its required policy and source paths come from
`/etc/fleetd/facts.toml`; `--root DIR` maps that config and every absolute
source path beneath a fixture root for reproducible collection. The host role
comes from the configured `/etc/mdt/host-setup.env` source so it cannot drift
from `HOST_ROLE`.

Runtime imports use only the Python standard library. The registered
`r0-r1` lane runs tests in `tester-unified` with full line and branch coverage.
The package does not include or start a daemon, socket, SSH client, Docker
client, or Geekbench process.
