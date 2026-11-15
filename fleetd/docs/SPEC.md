# fleetd specification

## Scope

The package contains strict versioned contracts, an injectable read-only facts
collector, a deterministic RAM-first planner, and a one-shot lite campaign
controller. The controller uses the existing identity-checked hx wrapper and
runner jobs; it is not a daemon and has no socket or lease protocol.

## Contracts

- `host-facts/1` carries CPU, Geekbench, memory, PSI, cgroup v2, KSM, and
  kernel-interface, control-plane, guard, policy, job, and tool facts. When a
  job or tool inventory is not available, that object is `null` with a
  non-empty sibling `jobs_reason` or `tools_reason`. A missing nullable leaf
  must carry a non-empty sibling `<field>_reason`.
- `work-request/1` carries the lane, commit and image identity, floors, demand,
  planned and pending candidate IDs, and optional carry-over provenance.
- `assay-merge-manifest/1` maps each planned candidate ID to exactly one owner
  store. The owner is authoritative; copies in other stores are ignored.
- `guard.toml` accepts only the host-owned keys and closed vocabularies from
  the compute-host design. This package parses and validates the rules; it does
  not apply them.

Unknown fields are rejected at every object boundary. Validators identify the
field path and the missing, extra, or invalid value. See the [consumer guide](CONSUMERS.md)
for examples and the [design guide](DESIGN-GUIDE.md) for invariants.
Every candidate ID in candidate tables, work requests, and merge manifests is
the lowercase 64-character SHA-256 digest emitted by native Assay R2.

### Lane demand

A TOML file passed to `--demand` carries a `[lanes.<lane>.demand]` table (released
Run-Gate does not yet accept that key in its own file; see RG-276) with these
required keys: `unit`, `instance_memory`, `unit_memory`, `unit_seconds_ref`,
and `efficient_upper_bound`. `unit_memory_high` and `baseline_seconds` are
optional. `baseline_seconds` is a host-independent estimate of the Assay
baseline wall time used to extend a batch's first progress window; when absent,
fleetd uses measured baselines from completed batches on that host. Units are
the closed set `candidate`, `worker`, and `run`; the CLI converts memory strings
using binary k/m/g suffixes. This R2 candidate planner requires
`unit = "candidate"` and refuses the other valid schema units because their
placement meaning is not defined yet. The high estimate cannot be lower than
the median estimate. Unknown keys are refused.

### Merge ownership

`assay-merge-manifest/1` has `planned_ids` and an `owners` object. The planned
ID list must be unique, and its set must exactly equal the owner keys. For each
ID, only the record in its named owner store is selected. A record with the
same ID in any other store is ignored. A missing record from the owner store
is an error.

## Facts collection

`fleet facts collect [--root DIR]` reads `/proc` and `/sys` beneath the
selected root, reads required versioned configuration at
`/etc/fleetd/facts.toml`, reads the Geekbench record from its configured
host-owned path, and emits `host-facts/1` JSON. The config declares
`overcommit`, `oversubscribe_max`, PSI refusal threshold, control-plane slice,
and host source paths; missing required policy fails. `HOST_ROLE` is read from
the configured host-setup environment file, an existing host-owned source, and
is not shadowed in fleetd policy. `host-facts/1` accepts `dedicated-dev` and
`shared-prod` as host roles; missing, duplicate, empty, or unknown values are
represented as `null` with a reason. Optional facts whose source is absent are
also represented as `null` with a reason. Collection is read-only: it never
invokes Geekbench or writes sysfs/cgroup files.

The collector exports only the benchmark's matching model and online-CPU
count. It accepts measured Geekbench 6/7 records with their matching reference
score, and never uses a table fallback. Guard config is validated after
lexically merging TOML drop-ins; its digest covers the main file and drop-ins.
The guard runtime state and KSM opt-in shim are nullable unless their paths are
configured. `memory.for_lanes_bytes` is raw `MemTotal`; the planner applies a
control-plane reserve once per §4.3:
`reserve_h = max(reported memory_min_bytes, clamp(256 MiB + ceil(6% × MemTotal), 768 MiB, 2 GiB))`.
If the slice or `memory.min` is absent, the clamped design default is used.
The 2 GiB cap applies to that default, not to a larger reported value. Host
plans expose `reserve_bytes` and `reserve_source` (`reported` or
`design-default`). A missing MemTotal still refuses planning because the
default cannot be derived. The later measured-p95 replacement is not part of
this package and is tracked under
[FD-007](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-007-calibrate-the-control-plane-reserve-from-measured-p95).
`fleet facts validate FILE`
validates a serialized record without contacting the host.
The collector and planner do not read `dev-gates.slice/memory.max`; the
planner uses the design's physical-RAM budget and leaves slice `MemoryMax` as a
downstream safety net. The missing host-owned ceiling fact is tracked under
[FD-004](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-004-host-slice-memory-ceiling-is-not-part-of-the-planner-facts).

## Planning

`fleet plan` consumes a candidate table, host facts plus per-host correction
factors, and a Run-Gate lane demand table. RAM determines eligibility and
candidate routing. Expected charges are compared with the overcommitted sum
budget; individual candidates are always bounded by physical RAM after the
control-plane reserve. Lists prefer a low/high mix and batch around four times
the host worker capacity. Retry candidates with recorded memory evidence are
assigned alone to the host with the largest physical alone-capacity.

The planner uses eligible hosts' Geekbench single-core scores for normalized
time and ETA, never multi-core scores. A refused host does not change the ETA
reference. One bounded correction factor is read per host, with
the design default `1.0`; factors are not learned or pooled by lane in this
package. The host-level EWMA and self-predicted-sample rule are tracked under
[FD-005](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-005-learn-the-per-host-correction-factor).
Identical inputs produce byte-identical JSON and stable command
ordering.
When no host is eligible, `time_reference.reason` says so separately from the
case where eligible hosts have no Geekbench records.

The planner's host-set input requires explicit other-running rows and rejects
hosts with unknown role, shared-prod role, PSI above its configured threshold,
or unknown MemTotal, physical lane memory, CPU, or PSI facts. A missing
control-plane slice or unreadable `memory.min` uses the design default reserve
instead of refusing the host. Worker capacity is the CPU-derived worker count
after other work, bounded by the lane's efficient upper bound. MVP-B-lite uses
one campaign slot per host because concurrent selected runs would share a
Run-Gate lane instance and Assay store; lifting this accepted capacity limit is
tracked under
[FD-006](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-006-lift-the-manual-one-slot-per-host-and-j-cap).
It computes:

- reserve_h = max(reported control_plane.memory_min_bytes, design default);
- design default = clamp(256 MiB + ceil(6% × MemTotal), 768 MiB, 2 GiB);
- P = memory.for_lanes_bytes - reserve_h;
- O = other running expected charges + campaign_slots × instance_memory;
- B = P × overcommit - O;
- b = B / W for W worker slots;
- cap = min(B - (W - 1) × ram_low, P - O - (W - 1) × ram_low);
- cap_alone = P - O.

Each wave's expected charge must be no greater than B. Each individual
candidate must fit cap_alone, including alone and retry batches. Retry goes
only to the host with the largest cap_alone and must also fit that host's B.
Overcommit applies to expected sums and never enlarges the physical cap.

Candidate and host inputs use candidate-table/1 and fleet-host-set/1. Candidate
measurements resolve by own id, same-file median, lane median, then declared
demand. Candidate timing follows the same precedence after normalization to
the common Geekbench major, exact version, and baseline. If timing is
incomparable, placement still uses RAM and the output includes an ETA reason.
When no comparable ETA exists, assignment balances normalized reference work
per worker as a placement tie-break; it does not claim that the hosts have
comparable speeds. An old measurement whose host is absent from the host set is
ignored for timing and recorded with a fallback reason.
The batch target defaults to four times W and is configurable with
--batch-multiplier. The command renderer emits one invocation per host batch,
with `--jobs` no greater than W, and runs batches serially within each copied
host section. Selected-only runs normally return Run-Gate exit 4
(`BUDGET_EXCEEDED`) because unselected candidates remain pending. A batch that
actually exhausts its budget uses the same exit code; the generated script
accepts 0 or 4 and stops on other codes, and the whole-lane closeout runs any
candidates still pending. `efficient_upper_bound`
must not exceed the lane's Assay `jobs_max`; the standalone planner cannot
derive that value until the Run-Gate campaign client is implemented.

This package deliberately omits the Run-Gate campaign-plan client command;
the integration contract is tracked under
[RG-276](../../run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md#rg-276-the-run-gate-campaign-plan-client-is-not-implemented).

## Campaign execution

`fleet plan` adds an optional `execution` object only when it is run from a
clean Git project that has a Run-Gate configuration:

```json
{
  "schema": "fleet-plan-execution/1",
  "commit": "<full lowercase Git commit>",
  "project": "<relative project path or .>"
}
```

`fleet run PLAN.json --transport hx --campaign ID --state-dir DIR` requires
this object. It validates the plan, creates a `fleet-campaign-state/2` file,
probes the planned hosts, and starts available batches up to the plan's
per-host campaign slot limit. A subsequent `fleet run` with the same campaign
and plan re-adopts existing jobs by name. The same campaign ID is bound to the
original commit, project, and lane. A changed plan stops jobs whose names are
no longer wanted and creates names from the replacement candidate lists.

Transport is limited to `hx` in this MVP. `Transport.run(host, argv_or_script,
timeout)` returns `(rc, stdout, stderr)`; `put` and `get` handle small
home-relative files. `HxTransport` invokes the existing
`scripts/controller/hx.sh` wrapper, which resolves host aliases from local
configuration. `FakeTransport` supplies in-memory job tables, files, a fake
clock, unreachable/slow states, starts, stops, and host restart behavior for
tests. No SSH keys, addresses, provider credentials, or host aliases are
stored in fleetd source or tests.

The runner job name is
`<campaign>.<host>.s<slot>.b<n>.<list8>`. `list8` is the first eight hex
characters of SHA-256 over the batch's sorted candidate IDs with one newline
per ID. Start probes the job table and full list digest before starting a new
runner job. The uploaded ID file lives below the selected project in the
persistent checkout. The first detached command writes `list.sha256` before it
invokes `run-r2-campaign.sh`; if the controller or session ends before that
write, probe recomputes the digest from the uploaded ID file. An existing
matching name is a no-op; a mismatched digest refuses. Probe sends job names
and ID-file paths rather than candidate arrays, so its command size grows with
the number of batches on a host, not with the number of candidates. One probe
is a single remote shell argument, which the kernel caps at 128 KiB: about 100
batches per host in one campaign (see FD-014). It resolves the Assay lane from the checkout's Run-Gate
declaration and reads the plain run's `progress-<assay-lane>.jsonl` under the
project `.assay` directory; it does not require the optional instance
`progress-file` pointer. Each runner job records the progress file's byte size
at its own start and end. Probe attributes events only when their candidate ID
is in that job and their byte range falls between those markers. The job's own
verdict supplies candidate outcomes resumed from Assay state, including cases
with no new progress event. Probe returns running/EXIT/missing state, list
digest, candidate progress count, progress file modification time, per-job
progress bounds, verdict outcomes, and artifact paths.

`fleet status ID` refreshes state and reports per-host and per-batch state,
done/total counts, candidates per second, blended ETA, and `STALLED`/`LOST`
markers. ETA blends the planner's Geekbench estimate with the observed rate
when both exist. `fleet tick ID` performs one scheduling step: adopt current
jobs, detect exits and stalls, handle host loss, and start the next available
batch. Neither command runs a polling loop.

The stall threshold is `max(900 seconds, 3 × p95 predicted candidate time for
this batch on this host, 3 × p95 observed candidate time on this host)`. The
prediction uses each candidate's `time_ref_seconds`, the host's matching
Geekbench baseline and single-core score, and that host's `f_h`; when scores
cannot be compared, fleetd uses the candidate's reference time. Observations
and predictions are never pooled across hosts. The declared `baseline_seconds`
or p95 of completed baseline measurements on this host is added to the first
window, before any candidate has finished in the batch. Later windows measure
silence since the last candidate finished without adding the baseline again.
The threshold never acts as a campaign wall budget. The stall clock pauses
while the host is unreachable. More than 1800 seconds without host contact marks the host lost.
All work owned by the lost host, including batches that were queued but never
started, is marked lost and considered for re-planning. Only unfinished
candidate IDs are sent to the reachable planned host with the largest
`cap_alone_bytes`, using the existing planner placement functions. If no
target is available the work is deferred. Idle planned hosts are probed even
when they had no initial batch, so they can become requeue targets. A host
the planner refused (shared-prod, unknown role, PSI, missing facts) that owns
no job of the campaign is never contacted by `run`, `status`, `tick`, or
`collect`. Host loss is measured from the last successful contact, so call
`tick` at least every few minutes: after a longer gap a single failed probe
marks the host lost at once (FD-020). Dispatch does not re-check PSI; the
plan-time refusal is the only PSI admission (FD-022). An `EXIT`, `missing_job`,
or stalled batch with no finished candidate from its own start becomes
`FAILED`, is not re-queued, and appears in status and collection with its
failure reason and exit code when available. Re-queue remains unlimited for batches that made progress. A stalled
batch that made progress keeps its `STALLED` marker and creates one replacement
batch. A returning host may finish a duplicate; no lease or renewal is used.
Transport or contract errors mark only the affected host `ERROR`; a tick still
supervises the other hosts and reports the error in status.

Before uploading a batch's ID file, start reads the probe's `checkout_ready`
flag for the missing job: the persistent checkout must be a non-symlink
directory with `.git` and the selected project directory. Otherwise start
refuses and the batch stays queued; the upload would otherwise create
`checkouts/<commit>/` as a plain directory that the supervisor refuses and the
runner's checkout sync cannot replace. Probe reports a home-relative
progress path computed lexically, so a symlinked `checkouts` directory works;
if the path cannot be expressed below the remote home it reports `null`
instead of failing the probe.

Every `run`, `status`, `tick`, and `collect` holds an exclusive non-blocking
lock on `<campaign>.lock` in the state directory for its whole
read/modify/write interval. An overlapping command fails with "campaign ...
is busy" and changes nothing.

The only persistent controller state is plain JSON under `--state-dir`,
written to a temporary file, fsynced, and atomically renamed. The
`fleet-campaign-state/2` record stores monotonic and UTC timestamps plus the
controller boot ID. On the same boot elapsed time uses the monotonic clock;
after reboot it is rebased from UTC. A backward UTC step is clamped to zero,
which delays a detector; a forward UTC step counts as downtime and can advance
detection after reboot. A restarted controller reconstructs remote state by
probing deterministic job names.

## Collection

`fleet collect ID` fetches known host progress streams and per-batch verdicts,
then writes `fleet-campaign-result/1` JSON plus a Markdown report. Candidate
ownership is selected by earliest valid finished-record timestamp; stable
host/job order breaks ties or missing timestamps. Event timestamps come from
host clocks, so clock skew can change which duplicate is selected, while the
one-owner rule and candidate outcome remain unchanged. Other finished copies are
ignored and counted. The result includes one candidate row each, killed,
survived, timeout, crashed, equivalent, and missing totals, duplicate count,
per-host wall time/rate, failed batches, and missing artifact details. Assay
`budget_exceeded` and `hung` outcomes map to `timeout` only when process-tree
evidence does not say the candidate never started. Never-started candidates
remain `missing` with a distinct reason. Zero-progress failed batches list
their failure reason and exit code when available for missing candidates. Older records in
the append-only progress file are excluded by each runner job's own byte-range
markers; resumed outcomes come from that job's verdict.

MVP-B-lite reports candidate outcomes but does not merge Assay state stores or
create a strict whole-lane verdict. Assay's `--merge-manifest` is tracked as
B544, Run-Gate's pass-through as RG-277, and the baseline cache is already
tracked by Assay B542. The restricted forced-command host entry is CIU-218;
the fleetd daemon is FD-011; campaign-aware guard enforcement is cgprofile
CP-64. Those are explicit later work, not implicit behavior of these commands.

The campaign client passes only the generated `--jobs` and
`--only-candidates` arguments to Run-Gate; it does not forward arbitrary
options such as `--base` or `--carry-over-main`. Project paths must match
`run-r2-campaign.sh`'s relative-path contract and cannot contain `..` anywhere
in a component. hx timeout/unreachable results mark a host unreachable;
identity-check and guard refusals (exit 97/98) remain fail-closed transport
errors and do not silently become host-loss signals.
