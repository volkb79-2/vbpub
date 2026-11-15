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

## Install the CLI

The `fleet` command is a registered `cli-extended` CLI: one grammar, one real
wheel dependency (`cli-extended>=0.5.0`), never vendored and never put on a
path. A managed host that runs `fleet facts collect` or `fleet guard validate`
installs both wheels offline; the host needs neither the repository nor an
`hx` wrapper.

```sh
python3 -m pip install --no-index --no-deps cli_extended-0.5.0-py3-none-any.whl
python3 -m pip install --no-index --no-deps fleetd-<version>-py3-none-any.whl
fleet --version
```

`cli-extended` is published on GitHub Releases only, so the two wheels come from
the same directory (a wheelhouse with both files lets you use one `pip install
--find-links` instead). The console script is `fleetd.entry:main`: it imports only
the standard library first, so a host missing the dependency gets one install
instruction and exit 3 rather than a traceback. `python -m fleetd` keeps working
for a source checkout. See [the CLI contract and host install steps](docs/SPEC.md#host-install-and-the-wheel-layout).

Agent skills ship inside the wheel (`fleetd/skills/fleetd-campaigns/SKILL.md`) and
are installed by the library verbs:

```sh
fleet skills install            # ~/.agents/skills and ~/.claude/skills
fleet skills check             # exit 1 unless the installed copies are current
fleet skills list --json
```

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
A batch becomes `STALLED` after
`max(15 minutes, 3 × predicted p95 candidate time for the batch on that host,
3 × observed p95 candidate time on that host)`. The prediction uses that
host's Geekbench score and correction factor, or the reference time when
scores cannot be compared. The first window adds the declared `baseline_seconds`
or the p95 baseline measured by earlier batches on that host. Later windows
start after a candidate finishes and do not add the baseline again. The timer
pauses while its host is unreachable. A batch that exits, goes missing, or is
stalled before finishing any candidate becomes `FAILED` and is not re-queued;
status and collection show its failure reason and exit code when available.
Batches that made progress continue to re-queue without an attempt cap. See
the [design guide](docs/DESIGN-GUIDE.md#campaign-stall-detection-and-no-progress-failures)
for why stall predictions and observations stay host-local.

Sync the plan's commit to every planned host before `fleet run`: start
refuses (and leaves the batch queued) when the persistent checkout is missing,
instead of creating its directory. A host the planner refused is never
contacted. Overlapping `run`/`status`/`tick`/`collect` calls on one campaign
fail fast with "busy" instead of racing on the state file. A re-plan with a different sorted candidate list gets a
different job name and stops the old job when it is no longer wanted. No lease
or wall-clock budget is maintained.

The controller probes idle planned hosts as well, so a host with no initial
batch can receive requeued work. Loss requeues unfinished candidates from
queued and running batches. A stalled batch that made progress keeps its
`STALLED` marker and produces one replacement. Runner ID files are
stored below the selected project checkout; the digest is written before the
campaign supervisor starts and can be reconstructed from that ID file during
startup. Each runner job stores the progress file size at its own start and
exit. Probe matches both that byte range and the job's candidate IDs, then uses
the job's verdict for Assay-resumed candidates. It resolves the Assay lane from
the checkout's Run-Gate config and reads its plain-run
`progress-<assay-lane>.jsonl` file, without relying on the optional instance
pointer. Probe requests carry file paths rather than candidate arrays.

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
wins, and later copies are counted as ignored duplicates. Never-started
candidates remain `missing` with a distinct reason; zero-progress failed
batches include the failure reason and exit code when available. Host transport or
contract errors are shown as `ERROR` in status while other hosts continue to
be supervised. Missing artifacts or candidates are reported. This lightweight result does not merge host Assay
stores with `--merge-manifest`; strict store ownership, the baseline cache,
forced-command host entry, always-on daemon, and active guard remain follow-up
work (see the [backlog](KNOWN_ISSUES_TODO_BACKLOG.md)).

## The default R2 campaign

Multi-host, multi-instance R2 is the default way mutation judging runs, and
fleetd supplies the four pieces that make it a mechanism rather than per-tool
scripts. None of them contacts a host.

- `fleet scope` produces the campaign's candidate-id set. The default scope
  judges only the lines added since the tool's previous release tag; a
  committed `r2-scope.toml` opts a project into a rolling whole-target
  baseline instead. An empty scope is an explicit `nothing-to-judge` result
  (exit 4), never an id file that judged nothing.
- `fleet sizing` derives instances per host, shards, each per-instance budget
  and Run-Gate's outer bound from the plan's own host budgets, a per-worker
  resource unit, and measured per-candidate seconds and memory — instead of a
  fixed `cpus = "3"` container and a hand-written `11h`/`12h` pair.
- `fleet collect` then `fleet measurement` record what each candidate actually
  cost in the campaign evidence, so the next campaign's sizing has a `measured`
  basis instead of a guess.
- `fleet merge-check` refuses a shard set whose shards judged a different
  commit, tester image or plan, or left an index missing.

See [the default R2 campaign](docs/R2-DEFAULT.md) for the mechanism and
[docs/CONSUMERS.md](docs/CONSUMERS.md#declare-the-campaign-scope) for commands
you can paste.

## Existing planning and facts commands

`fleet plan` writes no files except for stdout. `--commands` prints one serial
host section with one Run-Gate invocation per batch. It accepts Run-Gate exit 4
(`BUDGET_EXCEEDED`) so later batches can run and stops on other failures. The
whole-lane closeout still has to execute any selected candidates that remain
pending.

`fleet facts collect` reads host-owned inputs, read-only `/proc` and `/sys`
interfaces, and the fixed control-plane service list's `MemoryMin` properties
through `systemctl show`. Its required policy and source paths come from
`/etc/fleetd/facts.toml`; `--root DIR` maps those absolute paths beneath a
fixture root for reproducible collection without querying systemd. The host
role comes from the configured `HOST_ROLE` source. Fleetd never invokes
Geekbench. The planner reserves the sum of all positive service `MemoryMin`
values, or the host profile's fixed constant when any are missing; see the
[facts and planning contract](docs/SPEC.md#facts-collection).

Serialized campaign inputs use `host-facts/1`, `work-request/1`,
`candidate-table/1`, `fleet-host-set/1`, and `assay-merge-manifest/1`. The `fleet`
command itself is registered with the shared `cli-extended` library, its one
runtime dependency (see [the CLI and install section](docs/SPEC.md#host-install-and-the-wheel-layout));
all other imports are the Python standard library. The registered `r0-r1` lane runs
in `tester-unified` with full line and branch coverage. The package does not
start a daemon, socket, Docker client, or Geekbench process.
