# fleetd

`fleetd` plans and runs RAM-first Assay campaigns across configured compute
hosts. MVP-B-lite adds a one-shot campaign controller to MVP-A's strict data
contracts, read-only host-facts collector, and deterministic planner. It is a
one-shot loop with no daemon, socket, lease, or background service.

The planner produces per-host candidate batches and uses Run-Gate's existing
`--only-candidates FILE` option. `fleet run`, `status`, `tick`, and `collect`
close the basic dispatch and reporting loop through the controller kit's
identity-checked `hx.sh` wrapper and the existing runner's named jobs. The host
IDs in the plan must be aliases in the operator's local hx configuration; the
repository contains no host table, address, key, or provider credential.

`fleet plan` writes deterministic plan JSON. From a clean Git project it also
pins the full commit and project path in `execution`, which lets `fleet run`
reject a plan that cannot identify the runner checkout. `--commands` remains a
manual handoff. The planner resolves candidate RAM from its own measurement,
same-file median, lane median, then declared lane demand. Overcommit applies
to expected sums; each candidate must still fit physical lane RAM. Geekbench
single-core scores inform ETA only. See [the design guide](docs/DESIGN-GUIDE.md)
for the invariants and [the consumer guide](docs/CONSUMERS.md) for examples.

## Run a planned campaign

The remote runner must already have the plan's commit under its persistent
checkout root, with the selected project and lane available there. `fleet run`
uploads only the small candidate-ID files and starts the first available batch
on each host. Re-running it adopts jobs by their deterministic names; it does
not start a second job with the same candidate-list digest.

```sh
fleet plan r2 --candidates candidates.json --hosts hosts.json \
  --demand lane-demand.toml > plan.json
fleet run plan.json --transport hx --campaign r2-week46
fleet status r2-week46
fleet tick r2-week46
fleet collect r2-week46
```

The default state directory is `$XDG_STATE_HOME/fleetd/campaigns`, or
`~/.local/state/fleetd/campaigns` when `XDG_STATE_HOME` is unset. State is local,
versioned JSON written atomically and must stay outside Git. If `--state-dir`
is supplied to `run`, pass the same value to `status`, `tick`, and `collect`.
`status` probes progress and ETA; `tick` performs one scheduling step and
returns. There is no polling loop. Use repeated `tick` calls from the
controller that owns the campaign.

`tick` re-queues unfinished work when a host has been unreachable for more than
30 minutes, measured from its last successful contact: call `tick` at least
every few minutes, or one failed probe after a long gap marks the host lost.
A batch with no finished candidate becomes `STALLED` after
`max(15 minutes, 3 × observed p95 candidate time on that host)`; the timer
pauses while its host is unreachable. Until the first candidate on a host
finishes the threshold is the 15-minute floor, which also has to cover the
batch's baseline; lanes slower than that stall and re-queue without end
(FD-015). A batch that exits with unfinished candidates is re-queued on every
exit without limit (FD-016), so watch `status` for repeated `EXIT` batches.

Sync the plan's commit to every planned host before `fleet run`: start
refuses (and leaves the batch queued) when the persistent checkout is missing,
instead of creating its directory. A host the planner refused is never
contacted. Overlapping `run`/`status`/`tick`/`collect` calls on one campaign
fail fast with "busy" instead of racing on the state file. A re-plan with a different sorted candidate list gets a
different job name and stops the old job when it is no longer wanted. No lease
or wall-clock budget is maintained.

The controller probes idle planned hosts as well, so a host with no initial
batch can receive requeued work. Loss requeues unfinished candidates from
queued and running batches. A stopped stalled batch remains `STALLED` after
its runner exit is observed and produces one replacement. Runner ID files are
stored below the selected project checkout; the digest is written before the
campaign supervisor starts and can be reconstructed from that ID file during
startup. Probe resolves the Assay lane from the checkout's Run-Gate config and
reads its plain-run `progress-<assay-lane>.jsonl` file, without relying on the
optional instance pointer. Probe requests carry file paths rather than
candidate arrays.

State schema 2 stores monotonic and UTC timestamps with the controller boot
ID. Same-boot deadline calculations use monotonic time; a reboot rebases them
from UTC. A backward UTC step is clamped to zero and delays detection; a
forward UTC step counts as downtime and can advance detection after reboot.
Candidate ownership uses event timestamps from separate host clocks, so skew
may select a different valid duplicate owner.
The campaign client passes generated `--jobs` and `--only-candidates` only;
options such as `--base` and `--carry-over-main` are outside this MVP.

`fleet collect` fetches available progress and verdict files and writes one
combined `fleet-campaign-result/1` JSON file plus a Markdown report in the
state directory. Each candidate has one owner: the earliest finished record
wins, and later copies are counted as ignored duplicates. Missing artifacts or
candidates are reported. This lightweight result does not merge host Assay
stores with `--merge-manifest`; strict store ownership, the baseline cache,
forced-command host entry, always-on daemon, and active guard remain follow-up
work (see the [backlog](KNOWN_ISSUES_TODO_BACKLOG.md)).

## Existing planning and facts commands

`fleet plan` writes no files except for stdout. `--commands` prints one serial
host section with one Run-Gate invocation per batch. It accepts Run-Gate exit 4
(`BUDGET_EXCEEDED`) so later batches can run and stops on other failures. The
whole-lane closeout still has to execute any selected candidates that remain
pending.

`fleet facts collect` reads only host-owned inputs and read-only `/proc` and
`/sys` interfaces. Its required policy and source paths come from
`/etc/fleetd/facts.toml`; `--root DIR` maps those absolute paths beneath a
fixture root for reproducible collection. The host role comes from the
configured `HOST_ROLE` source. Fleetd never invokes Geekbench.

Serialized campaign inputs use `host-facts/1`, `work-request/1`,
`candidate-table/1`, `fleet-host-set/1`, and `assay-merge-manifest/1`. Runtime
imports use only the Python standard library. The registered `r0-r1` lane runs
in `tester-unified` with full line and branch coverage. The package does not
start a daemon, socket, Docker client, or Geekbench process.
