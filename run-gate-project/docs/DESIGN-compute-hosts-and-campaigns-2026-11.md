# Design: compute hosts and distributed lane campaigns (2026-11)

Status: DESIGN, decision record, not implemented. Backlog: run-gate RG-272,
netcup NC-49. Amends `DESIGN-lane-rework-2026-11.md` §3.3 (see §3.0). No host
names, addresses, controller paths or credentials appear in this file; hosts
are "host A/B".

## 0. Decisions in one screen

| # | Decision | Section |
|---|---|---|
| D1 | Run-Gate owns the **scheduler/allocator** for lane campaigns: a new `run-gate campaign` layer in its own module, not in `run_gate.py`. | §3.3 |
| D2 | The **compute provider** (host inventory, capabilities, host-wide policy, live state, leases) is **mdt-cockpit**: hosts publish their own facts and keep their own leases; the controller only aggregates. Its state lives on the hosts and in the controller's private host table, never in the repository. | §3.2 |
| D3 | A lane declares **demand**, not parallelism: per-instance RAM, per-unit RAM/CPU/wall time and an **efficient upper bound** of units per instance. Workers per instance and instance counts are the allocator's output. | §4 |
| D4 | Transport for the MVP is **SSH** through the existing private host table, identity check and on-host guard. ciu v8.2's enrolled inventory replaces it later behind the same adapter. | §3.4 |
| D5 | Per-host supervision stays **on the host** (it must survive controller loss) but moves out of `scripts/netcup` into `run-gate campaign supervise`. `run-r2-campaign.sh` becomes a wrapper. | §3.5, §5 |
| D6 | Placement counts **expected peaks**; container caps are runaway bounds and may sum above RAM (overcommit by design). Pressure (memory/IO PSI) is the runtime bound. KSM savings are not counted until measured. | §4.3, §4.5 |

## 1. Problem and non-goals

### 1.1 Problem

- Long lane work (native Assay R2 campaigns of 300 to 1,500 candidates, 3 to 10
  hours; later fuzz/variation lanes) must keep **every CPU of every compute
  host** busy, on any host we provide, not only on the two current test hosts.
- Today the only supervisor is `scripts/netcup/runner/run-r2-campaign.sh`
  (1,069 lines of bash). It works on one host: one campaign, or K shard
  instances on that host. Cross-host split is manual: the operator chooses
  `--shard-total/--shard-offset` per host and copies shard stores by hand
  before `--merge-only` (`runner/README.md:168-181`).
- The operator chooses `--instances`, `--jobs` and the host. Nothing knows
  what the other hosts are doing.
- The memory guard charges each instance the lane's whole container cap
  (`run-r2-campaign.sh:568-594`). A PWMCP lane capped at 3 GiB ran 3 workers
  at about 430 MiB and about 1 CPU, but two instances were refused
  (2 x 3072 + 1024 > 6454 MiB available). CPUs stayed idle. NC-48 (branch
  `r2-guard-20261112`) patches the arithmetic. It still configures the guard
  through environment variables on the host.
- **Answer to "how is this netcup-specific?"** It is not. Only host
  installation is provider-specific: `install-host.py`, the SCP API client,
  debian-install-v2, `pre-reinstall*.py`. The runner scripts,
  `remote-gate.py`, `host-sat-queue.sh` and the R2 supervisor are generic
  SSH + Docker + Run-Gate code. They contain no provider API call, and their
  identity guard reads a generic `/etc/vbpub/server-id`. They live under
  `scripts/netcup/runner/` because runner provisioning was first written next
  to the netcup installer. This design moves the supervision logic out (NC-49).

### 1.2 Non-goals

- No change to verdicts, judge identity or Assay's store format. Shards,
  strict merge and carry-over (RG-265, B529-B531) are used as they are.
- No new daemon on the controller and no inbound listener on hosts.
- No provider-specific code in the scheduler. Netcup's role ends at "the host
  is installed and registered".
- No cluster manager (Kubernetes, Nomad, Slurm, Buildkite): §3.3 explains
  why.
- No automatic enablement of KSM, swap or host-policy changes. The scheduler
  reads host policy and never writes it.
- Not a CI system for short lanes. The host-sat queue keeps serial light
  lanes; it only learns to take leases (WP4).

## 2. Landscape

### 2.1 What exists (reuse, do not rebuild)

| Concern | Existing component | What it gives | File refs |
|---|---|---|---|
| Host table | controller-private JSON (`hosts.<name>.{ssh, api_check, guard, runner_home, cockpit, image, repos, queue_lanes}`) | reach a host, prove its identity, run a guarded command | `scripts/netcup/runner/remote-gate-hosts.example.json`, `scripts/controller/hosts.local.json.example`, `remote-gate.py:190-237` |
| Host choice | `remote-gate run auto` | picks the host with the lowest CPU PSI below memory/IO PSI 25 | `remote-gate.py:240-269` |
| One-shot remote lane | `remote-gate run/status/logs/collect/cancel` | bundle → throwaway checkout → lane → collect | `remote-gate.py:738-975` |
| Serial lane queue | `host-sat-queue.sh` | per-host loop, pause flags, INFRA auto-pause, results TSV | `runner/host-sat-queue.sh`, `runner/README.md:772-838` |
| Campaign supervision | `run-r2-campaign.sh` | persistent checkout, attempt loop on exit 4, stall counter, stop/status, exact-name cleanup, fleet of shard children, merge, MemAvailable guard | `runner/run-r2-campaign.sh`, `runner/README.md:85-216` |
| Detached jobs, code transport | `run-job`, `sync-from-devcontainer` (git bundle import) | survive SSH disconnect; hosts never need origin | `runner/README.md:68-83` |
| Shards, merge, carry-over | Run-Gate `--shard K/N`, STRICT `--merge-shards N`, `--carry-over`, `--merge-state`, `--only/--exclude-candidates` | independent instances with own store/container/verdict | `CONSUMERS.md:770-856`, `DESIGN-GUIDE.md:1130-1150` |
| Shard cut and manifests | Assay `blake2b(id) % N`, `shard.manifest` (`assay-shard-store/1`), `plan --list-pending` | deterministic split, strict-merge proof, pending lists for uneven splits | `assay/src/assay/mutation.py:1788-1793, 3497-3530`, `assay/src/assay/cli.py:504-526` |
| Worker sizing inside one run | `jobs = "auto"`, `r2_oversubscribe`, `candidate_ram`, `ram_target`, `jobs_max` | workers from CPU cap, memory cap, RSS p95 | `DESIGN-GUIDE.md:1051-1128` |
| Lane cost history | `.run-gate/history.json`, `run-gate footprint` → tracked `run-gate.footprint.json` | per-lane peak memory, cores, duration | `CONSUMERS.md:2175-2453`, `SPEC.md:2584-2630` |
| Resource profiles | `resource-profile/1`, `lane-profile/1`, scaling mode (designed) | per-candidate/per-worker distributions, speedup S(N)/N | `docs/design/RESOURCE-PROFILES.md` §2, §6.2-§6.5 |
| Per-host measurement | cgprofile `host` verb: `machine/1` (cpus, CPU-speed score) + HostSnapshot (meminfo, PSI, slices) | live host state and a normalisable speed | `cgprofile/docs/PROTOCOL.md:600-625`, `cgprofile/docs/RG55-INTERFACE-CONTRACT.md:164-182` |
| CPU speed fact | `/var/lib/mdt/host-facts/cpu-benchmark.json` (`mdt-host-cpu-benchmark/1`) | `single_core`, `multi_core`, `baseline_score`; `x_ref = x * single_core / baseline` | `cgprofile/docs/PROTOCOL.md:614-625` |
| Disk performance | iobench (disk only, per disk, `/var/lib/iobench`) | IOPS/bps caps; **no CPU score** | `libraries/iobench/README.md:16-25`, `docs/SPEC.md:295` |
| Host-wide policy | mdt host-setup: `/etc/mdt/host-setup.env` (`HOST_ROLE`, `MDT_EXCLUDED_RAM/_SWAP/_CPU_CORES`), slice tree, overcommitted tier fractions | the capacity object (`dev-gates.slice`) and its policy | `mdt-cockpit/host-setup/README.md:27-157`, `docs/design/RESOURCE-PROFILES.md` §7 |
| Per-host daemon (designed) | mdtd observe verbs `status capacity/admission`, control `admission-publish` | future home of host-side verbs | `docs/design/mdtd/DESIGN-rev4-2026-11-07.md` §3.5-§3.6 |
| Host readiness | ESTATE-REQUIREMENTS.toml + `estate_doctor.py` (branch `estate-req-20261112`) | what a host must provide, read-only probes | `mdt-cockpit/docs/ESTATE-REQUIREMENTS.toml` |
| Estate rules | AGENTS.md "Compute use" and "limits are host-owned" | stop only on memory/IO PSI > 25 or heavy swap; about one single-threaded job more than the core count; "When a tool needs a limit, it asks the host policy." | `AGENTS.md:196-226` |
| T3 request interface | entries + `t3-results.tsv` ledger (designed, not built) | a stable request/result format | `DESIGN-lane-rework-2026-11.md` §3.4 |
| ciu v8 hosts | inventory row `[hosts.<name>]`, enrolled SSH key, `version --json` proof (v8.2, draft) | future identity and transport | `ciu/docs/CIU-HOST-ENROLLMENT-PROPOSAL.md`, `ciu/docs/SPEC-V8.md` S7.2, S17 |

### 2.2 What is missing

1. **A host capability record.** Facts are spread across `/proc`, the
   host-setup env file, cgprofile, iobench and the private host table. No
   machine-readable answer exists for "CPUs and RAM for lanes, overcommit
   policy, speed, tools present" (NC-35 and lane-rework §1.5 say so).
2. **Allocation state.** Nothing records which campaign holds which share of
   which host. Manual runs and queue runs overlap (LR-KI-10). Run-Gate's R-45
   headroom wait is observational by design, and atomic reservation (RG-56)
   is open.
3. **A placement function.** Nothing turns lane demand plus host capacity
   into instances x workers per host.
4. **Lane demand that separates expected peak from cap.** `resources.memory`
   is both the container cap and the only admission number (LR-KI-11). Hosts
   run with `RUN_GATE_PROFILE=off`, so host runs produce no footprint.
5. **Cross-host campaign lifecycle.** Shard dispatch, store sync-back, merge
   placement, host-loss recovery and a central status are all missing.

## 3. Architecture and ownership

### 3.0 Relation to earlier decisions

- `DESIGN-lane-rework-2026-11.md` §3.3 chose "extend the host queue and
  `remote-gate`, one loop per host" and said "Keep the gate tool single-host".
  The operator now leans to Run-Gate owning allocation. This design keeps the
  part of §3.3 that matters and changes one thing:
  - **kept:** lane execution on a host remains single-host `run-gate <lane>
    --shard K/N`. The T3 entry and ledger formats (§3.4 there) remain the
    stable request/result interface.
  - **changed:** the per-host claim loops are replaced by one allocator that
    places work on all hosts.
- Buildkite (`REMOTE-LANES-BUILDKITE.md`) stays a possible scale-out
  transport. Section 3.3 explains why it is not the allocator.

### 3.1 Picture

```text
  controller (any machine with the private host table)
  ┌──────────────────────────────────────────────────────────────────────┐
  │ run-gate campaign  (D1: allocator + campaign lifecycle, tick-based)  │
  │   reads: lane demand + footprint history (repo)   ──┐                │
  │   reads: fleet state  ◄── mdt fleet (D2, client) ───┼─ private host  │
  │   writes: campaign dir + ledger (controller state)  │   table (reg.) │
  └───────────────┬─────────────────────────────────────┴────────────────┘
                  │ SSH (D4): guard + identity check on every write
     ┌────────────┴──────────────┐                ┌───────────────────────┐
     ▼ host A                    ▼ host B   ...   ▼ any enrolled host
  ┌─────────────────────────────────────┐
  │ mdt host-facts / mdt lease  (D2)    │ facts: /etc/mdt/host-setup.env,
  │   lease ledger (on host, TTL, gen)  │ cgprofile machine + snapshot,
  │ run-gate campaign supervise (D5)    │ KSM sysfs, tools/images, doctor
  │   └─ run-job ─ run-lane.sh ─ cockpit ─ run-gate <lane> --shard K/N --jobs j
  │                                      └─ Assay store .run-gate/assay-shards/…/KofN
  └─────────────────────────────────────┘
```

### 3.2 (ii) Host inventory and state: the compute provider

**Rule (operator, directive b):** host capabilities, host-wide policy and
current allocation are state. They belong to the one tool that provides
compute hosts, not to this repository and not to the lane tool.

**Split of the state:**

| State | Kind | Kept where | Writer |
|---|---|---|---|
| Registry: which hosts exist, how to reach them, enabled/draining | private config | the controller's private host table (existing file, extended) | operator, or the provider's `register` after an install |
| Capabilities and host-wide policy | host-owned facts | on the host: `/etc/mdt/host-setup.env` (existing), `/var/lib/mdt/host-facts/*.json` (existing directory) | mdt host-setup, the host benchmark tool |
| Live state (memory, PSI, slices, lane containers, free disk) | measured | read live on the host (cgprofile `host` verb; `/proc` fallback) | nobody; read-only |
| Allocation (leases) | state | on the host: `/var/lib/mdt/leases/` | `mdt lease`, under a host-local flock |
| Readiness | cached check | on the host: the last estate-doctor result | `estate_doctor.py` (estate-req branch) |

**Contract `compute-provider/1`.** CLI first, JSON on stdout, closed exit
codes 0 / 2 bad input / 3 unreachable / 75 no capacity:

| Verb (controller side, `mdt fleet …`) | Host side | Returns |
|---|---|---|
| `hosts` | none | `[{name, state: enabled\|draining\|disabled, labels}]` from the registry; no addresses printed |
| `facts <host>` | `mdt host-facts --json` | `host-facts/1` (below) |
| `state <host>` | `mdt host-state --json` | `{at, mem_available, swap_free, psi{memory,io,cpu}, gates{current,high,max}, leases[], lane_containers, checkout_disk_free}` |
| `lease acquire <host> --holder H --cpu C --mem-expected B --ttl S` | `mdt lease acquire …` | `{lease_id, generation, expires_at}`, or exit 75 with the binding constraint |
| `lease renew <host> <id> [--ttl S]` | `mdt lease renew` (also callable locally by the supervisor) | new `expires_at`, or exit 2 when expired or revoked |
| `lease release <host> <id>` / `lease list <host>` | same | |
| `drain <host>` / `undrain <host>` | sets the registry state; also writes the existing host-sat pause flag | |

`host-facts/1` (sketch; every leaf may be `null` with a `*_reason`, never
guessed, as in cgprofile `machine/1`):

```json
{"schema": "host-facts/1", "at": "…",
 "cpu": {"online": 4, "for_lanes": 4.0, "oversubscribe_max": 1.25},
 "memory": {"total_bytes": …, "for_lanes_bytes": …, "swap_bytes": …,
            "overcommit": 1.0, "ksm": {"run": 1, "pages_sharing": 0}},
 "policy": {"host_role": "dedicated-dev", "psi_refuse_some_avg10": 25,
            "max_lane_containers": 8, "source": "/etc/mdt/host-setup.env"},
 "speed": {"basis": "geekbench7-measured", "single_core": …, "baseline_score": 2500},
 "tools": {"run_gate": ["25.5.0"], "assay_floor_ok": true,
           "images": {"tester-unified:local": "sha256:…"}, "cockpit": "up"},
 "readiness": {"doctor": "PASS", "at": "…", "failed": []}}
```

- `cpu.for_lanes` = `CPU_MDT`, the cores minus `MDT_EXCLUDED_CPU_CORES`.
- `oversubscribe_max` = (cores + 1) / cores by default, following the
  AGENTS.md rule "about one single-threaded job more than the core count".
- `memory.for_lanes_bytes` = `dev-gates.slice` `MemoryHigh` (host policy).
- `overcommit` = the policy factor for summed **expected** peaks. 1.0 is the
  default until KSM/zswap savings are measured (§4.5).

**Candidates for the provider:**

| Candidate | For | Against |
|---|---|---|
| ciu v8 host enrollment | the estate's future SSH identity, pinned key and version proof; inventory rows are gitignored | v8.2 annex, unbuilt. Explicit non-goals: no capacity, allocation, heartbeats or remote job execution ("Enrollment ends at 'ciu can SSH in, and ciu is installed there'"). The inventory is config, not state. |
| **mdt-cockpit (host-setup now, mdtd later)** | already owns host-wide policy (`HOST_ROLE`, `MDT_EXCLUDED_*`, overcommitted tiers, the gates slice as "the admission capacity object"), `/var/lib/mdt/host-facts/`, the published admission cap and ESTATE-REQUIREMENTS; mdtd plans `status capacity/admission` | per-host by design, so the fleet view is a new client half; mdtd is not built yet |
| netcup SCP-API tools | know provider ids and install state | provider-specific, which violates "any compute host"; know nothing about slices or policy |
| a new minimal service | clean scope | another tool and process to secure and release; would copy mdt's policy model; the operator prefers simple |
| Run-Gate itself | no new owner | violates directive (b): host state would sit in the lane tool; Run-Gate is archived into ciu at 8.0 (D-647 #1, V8-19) |

**Recommendation: mdt-cockpit.** It already provides the resources and the
host-wide policy, and AGENTS.md already says "a tool … asks the host policy".

- MVP host side: one stdlib script installed by host-setup. It provides
  `mdt host-facts`, `mdt host-state` and `mdt lease`. Leases are JSON files
  under `/var/lib/mdt/leases/` behind a `flock`, writable by a dedicated lease
  group that the runner account joins.
- Later the same verbs become mdtd observe/control verbs, and the wire
  contract does not change.
- Controller side: `mdt fleet` fans out over SSH using the registry.
- There is **no central database**. The central view is computed per tick.
  Host loss therefore takes that host's allocation with it, and two
  controllers sharing hosts see the same leases.
- `lease acquire` checks capacity atomically on the host:
  `Σ mem_expected + new <= for_lanes × overcommit − unleased gates usage`.
  This is RG-56's atomic reservation at host granularity, without a daemon.

**Downside and mitigation.** mdt becomes a cross-host client, a scope it did
not have.

- The client half is small: a registry read, an SSH fan-out and JSON merging.
- It has no scheduling logic, so the provider never decides placement.

### 3.3 (iii) Scheduler/allocator: Run-Gate `campaign` layer

**Why Run-Gate:**

- Run-Gate already resolves everything the allocator needs from a lane:
  declaration, environment, image, caps, `jobs` auto math, footprint and
  history, shard/merge/carry-over flags, Assay floors and refusals
  (exec/`shared` lanes cannot shard).
- Assay is the judge. It knows candidates and plans but should not know
  hosts. It already exposes what the allocator needs: shard manifests and
  `plan --list-pending`.
- ciu v8 plans no cross-host scheduling (agent survey of ciu docs; S21
  admission is per Docker daemon), and v8 is unbuilt.
- A cluster manager or Buildkite would bring agents, accounts and outbound
  access, and would still need our lane-aware math. Buildkite capacity is
  "agent count and tags, not RAM-aware" (lane-rework §3.2).

**Downsides of the operator's lean, and mitigations:**

| Downside | Mitigation |
|---|---|
| Run-Gate is to be merged into ciu and archived at 8.0 (D-647 #1, V8-19). Anything built here must be ported. | Build it as a separate module `run_gate_campaign.py` with its own tests and stable file formats (campaign spec, plan, ledger). Its interface to the lane resolver is narrow. The ciu8 port moves the module, and its tests become parity tests (WP9). |
| Remote execution widens the gate tool's trust surface (SSH keys). | Run-Gate stores no keys. The transport is an injected adapter that runs argv from the private host table; tests inject a fake (as `remote-gate.py`'s `Runner` does). Lane execution on a host keeps its single-host semantics. |
| A campaign lasts hours, but the controller devcontainer is memory-capped and agent sessions die. | No long-lived controller process. `run-gate campaign tick <id>` is idempotent: all state is in files, and a detached loop or timer calls it. Host supervisors run without the controller (§3.5). |
| `run_gate.py` is already 20,050 lines. | A new module, as `run_gate_admission.py` and `run_gate_profile.py` are. |
| Version skew: hosts run different Run-Gate versions. | Host facts list the Run-Gate versions; the allocator applies the same floors as `remote-gate.py` repo profiles. |

Verbs: `run-gate campaign plan|start|tick|status|stop|collect|supervise`.

- `plan` is pure and read-only: demand + facts → placement + ETA.
- `start` writes the campaign spec and takes the first leases.
- `tick` reconciles: dispatch, renew, sync, re-place, merge.
- `supervise` is the host-side half (§3.5).

### 3.4 (iv) Execution transport

- **MVP: SSH**, exactly as `remote-gate.py` and `host-sat-queue.sh` do today:
  - argv from the private host table;
  - provider identity check (`api_check`) before every launch or remote write;
  - the on-host `guard` prefixed to every remote script (`remote-gate.py:229-237`);
  - `BatchMode=yes` with a pinned `known_hosts`.
- Code travels as a git bundle imported by `sync-from-devcontainer
  --no-current-link` into `$RUNNER_HOME/checkouts/<commit>`. Hosts never
  contact origin.
- Long processes start through `run-job`, so they survive SSH loss.
- **ciu v8 later:**
  - the adapter's three operations (`run(host, argv)`, `put(host, file)`,
    `get(host, path)`) map to the v8.2 inventory row, the enrolled key and
    `version --json` proof;
  - the registry's reach fields are then read from `ciu.hosts.toml`, while
    capability/policy/lease state stays with mdt;
  - nothing else changes.
- **Not Docker contexts:** admission and observers are local to one daemon
  (`CONSUMERS.md:2026-2070`; ciu S21).

### 3.5 (v) Per-host supervision, overcommit, status, merge, carry-over, host loss

**Host side (`run-gate campaign supervise`, launched through `run-job`):**

- It is a port of `run-r2-campaign.sh` with the same behaviour:
  - the clean persistent checkout check;
  - the attempt loop while Run-Gate exits 4;
  - the stall counter on Assay `event=candidate`;
  - `R2_MAX_ATTEMPTS`;
  - exact-name container cleanup by label or recorded name;
  - `stop` / `status`;
  - the fresh-verdict copy and `final-exit` + `campaign-summary.json`.
- It keeps the same `locks/<base>.lock`, `.active` record and
  `campaigns/<start>/` layout, so old and new supervisors exclude each other
  on one host.
- New behaviour:
  1. **Lease renewal on the host.** It runs `mdt lease renew` every 60 s
     locally, without the controller. Before each attempt it checks that its
     lease generation is current; a revoked lease ends the instance (fencing,
     §6).
  2. **Shard queue.** One instance holds one lease and runs shards one after
     another (`--shard K/N --jobs j`). After a shard ends it asks the
     controller's dispatch file on the host for the next shard. The
     controller writes that file at each tick.
  3. **Pressure shedding.** When memory PSI some avg10 > 25 for 2 minutes,
     or swap-in exceeds the policy rate, the supervisor holding the
     **youngest** lease on the host stops its attempt (SIGTERM, then Run-Gate
     cleanup; finished candidates are kept) and marks its lease `yielded`.
     The choice is deterministic from `mdt lease list`, so no coordination is
     needed. The controller re-places the work later.
  4. **Profiling on.** Instances run with the cgprofile observer where the
     host provides it, so host runs produce footprint data. This lifts
     `RUN_GATE_PROFILE=off` (LR-KI-11).

**Controller side (`tick`):**

- **Dispatch:** assign pending shards to instances; acquire, renew and release
  leases as instances start and end.
- **Status:** `run-gate campaign status <id> [--json]`, one row per instance
  and shard: host, lease, j, judged/total, rate, ETA. ctlstat §G/§I reads
  this JSON and `mdt fleet state --json`; it does not re-probe hosts.
- **Stop:** `run-gate campaign stop <id>` runs the supervisor `stop` on every
  host and releases the leases.
- **Store sync-back:** every 15 minutes, each running shard store
  (`.run-gate/assay-shards/<project>/<lane>/<assay_lane>/<K>of<N>`) is copied
  to the controller campaign dir as a tar with a size bound. Finished stores
  are copied at shard end.
- **Merge:**
  - when every shard is final, choose the merge host (the one already holding
    the most shard stores);
  - push the missing stores into its checkout;
  - run `supervise --merge-shards N` there. The strict merge stays the only
    step that produces the whole-lane verdict;
  - collect verdict, summary and attempts;
  - append the T3 ledger row (`DESIGN-lane-rework-2026-11.md` §3.4 format;
    the `host` field may hold several hosts).
- **Carry-over:** the campaign spec holds `carry_over = {store, reason}`.
  Before the first attempt, the controller pushes that main store into each
  chosen host's checkout. Every shard reads it with `--merge-state` and
  imports with `--carry-over` as today (B530). The reason is recorded once in
  the spec and passed unchanged.
- **Host loss:**
  - a host unreachable for longer than the grace period (default 2 x TTL =
    30 min) has its shards marked `lost`;
  - those shards are re-placed on other hosts from the last synced store
    copy, under a new generation;
  - when the host returns, its supervisors find their leases revoked and
    stop;
  - a stale store from the old generation is never merged (§6).
- **Controller loss:** hosts continue. Leases are renewed on the host, and
  shard queues already written on the host keep running. A new controller
  rebuilds the view from the campaign dir and `mdt fleet state`.

## 4. Lane demand instead of workers per instance

### 4.1 Schema (`[lanes.<lane>.demand]`, RG-272)

Four keys are new. Everything else reuses existing keys. All are estimates,
and measured values win (§4.4).

```toml
[lanes.r2.resources]              # EXISTING, unchanged meaning
memory = "3g"                     # per-container hard cap = runaway bound, NOT demand
cpus = "3"                        # per-container CPU cap
candidate_ram = "450m"            # EXISTING per-unit RAM floor (becomes unit_memory default)

[lanes.r2.demand]                 # NEW
unit = "candidate"                # candidate (native R2) | worker (xdist) | run (unsplittable)
instance_memory = "250m"          # fixed RAM per instance above its units (judge, container, baseline)
unit_cpu = 0.6                    # busy cores per running unit; < 1 means IO phases leave room
unit_seconds = 95                 # wall seconds per unit at reference speed (single_core == baseline_score)
# efficient upper bound of units per instance = Assay judge.mutation.jobs_max
# (native R2) or workers_max (xdist, RESOURCE-PROFILES §6.2); not duplicated here
```

- **Removed from the lane's and operator's hands:**
  - fixed `jobs = N` for campaigns (the allocator passes `--jobs j`);
  - the runner's `--instances/--shard-total/--shard-offset`;
  - `R2_INSTANCE_MEM_MIB`, `R2_WORKER_MEM_MIB`, `R2_OVERCOMMIT` and
    `R2_HOST_RESERVE_MIB` (NC-48). These become lane demand plus host policy.
- **`jobs_max` is renamed in meaning only.** It now means "beyond this many
  units, one instance gains nothing", for example because of a shared
  browser, one database or GIL-bound collection. A lane that scales badly
  states a small bound. The allocator then adds instances instead of workers.
  This answers "pwmcp often still only uses a single CPU".
- `unit = "run"` lanes, and lanes Run-Gate refuses to shard (exec mode,
  `resources.shared`), get exactly one instance.

### 4.2 Allocator math

For each eligible host h (enabled, readiness PASS for the lane's needs,
Run-Gate/Assay floors met, image present, memory and IO PSI some avg10 <= 25):

```text
C_h = cpu.for_lanes × cpu.oversubscribe_max − Σ leased cpu
M_h = memory.for_lanes × memory.overcommit
      − max(Σ leased mem_expected, gates.current − unleased_known)
s_h = speed.single_core / speed.baseline_score      (null → lane history ratio → 1.0, disclosed)
```

For lane L, with O = instance memory, U = unit memory, c = unit CPU, J = the
efficient bound and E(j) the measured efficiency (default 1 for j <= J):

```text
cpu(j) = j × c          mem(j) = O + j × U          rate(j) = j × E(j) × s_h / unit_seconds

per host: for j in J..1, keep only j with E(j) >= e_min (0.8):
   k(j) = min( floor(C_h / cpu(j)), floor(M_h / mem(j)), max_lane_containers − running )
   pick the j maximising k(j) × rate(j); ties → larger j (fewer baselines, less O)
   leftover CPU >= c and memory → one extra instance with j' = floor(leftover / c)
shards N  = max(Σ_h k_h × over_partition (2), …) with every shard >= min_units (40)
ETA       = units_remaining / Σ_h Σ_i rate(j_i)  + one shard's tail
```

- **Memory caps.** Each container keeps its lane cap: Run-Gate has no
  per-invocation override, and a cap is a runaway bound. The sum of caps may
  exceed RAM, as host policy intends.
- **Runtime protection** comes from the slice `MemoryHigh/Max`, PSI shedding
  (§3.5) and Assay's own `--memory-budget-bytes` inside each instance.
- **Optional (WP5):** a lowering-only `--memory-cap` on Run-Gate, so the cap
  can follow the shape (`O + j × U_max × 1.5`).

**Worked example.** A 4-core host with 6 GiB for lanes, overcommit 1.0 and
oversubscribe 1.25. The lane's measured fit is O = 190 MiB and U = 80 MiB
(3 workers → 430 MiB), with c = 0.35, J = 3 and E(3) = 0.95.

- Calculation:
  - C = 5;
  - j = 3 → cpu 1.05, mem 430 MiB;
  - k = min(floor(5 / 1.05) = 4, floor(6144 / 430) = 14) = **4 instances x 3 workers**;
  - 1.7 GiB expected, about 4.2 busy cores.
- The old guard refused 2 instances, and NC-48's guard admits them but leaves
  the count to the operator.
- If E(3) were 0.5, j = 1 wins: 14 instances by CPU, capped by
  `max_lane_containers`.

### 4.3 What "3 x 1 ≈ 1 x 3 in RAM" needs (operator directive c, checked)

- RAM(k instances x j) = k × O + k × j × U.
- 3 x 1 equals 1 x 3 only when O ≪ U. With the fit above, 3 x 1 = 810 MiB
  against 1 x 3 = 430 MiB.
- Each instance pays its own judge process, container and baseline run (about
  one candidate's time), and pays them again on every attempt.
- The allocator therefore measures O and prefers fewer, wider instances while
  E(j) holds, and narrower ones when it does not. The operator's expectation
  becomes a measured property instead of an assumption.
- Stagger instance starts by one baseline's duration so baseline peaks do not
  coincide. This generalises today's `R2_INSTANCE_STAGGER_S`.

### 4.4 Learning and refinement

- **Sources, all existing:**
  - Assay candidate progress (`peak_rss_bytes`, `cpu_seconds`, elapsed);
  - cgprofile container `memory.peak`, CPU and PSI per instance (D1 of
    RESOURCE-PROFILES: the only number source when present);
  - Run-Gate `.run-gate/history.json` on each host, collected by `tick`.
- **Fits**, per lane, from clean samples (RESOURCE-PROFILES §6.1
  contamination rule):
  - U = p95 candidate RSS;
  - O = intercept of instance peak P(j) = O + j·U over at least two distinct
    j. Until then, O = `instance_memory`, else peak(j) − j·U;
  - c = p50 `cpu_seconds/wall`, floor 0.25;
  - E(j) = rate(j) / (j × rate(1)), from campaign attempts or `--scaling`
    (RESOURCE-PROFILES §6.5);
  - `unit_seconds` = p50 elapsed × s_h, normalised to the reference score.
- **Committed numbers.** `run-gate footprint --write` gains the `demand` fits
  per lane in `run-gate.footprint.json`. This is lane demand normalised by the
  reference benchmark, never by host name (AGENTS.md "Rigor evidence"), so it
  belongs in the repository. Host facts never do.
- **Predicted against actual.** Each tick records predicted against actual
  rate per instance in the campaign dir (as RESOURCE-PROFILES §5.7). `doctor`
  warns when the committed demand drifts by more than `tolerance_pct`.

### 4.5 Overcommit and KSM

- RAM overcommit is applied in two places:
  - caps may sum above RAM;
  - expected peaks are summed against `for_lanes × overcommit`.
- **KSM counts for nothing until measured.** KSM merges only pages of
  processes that opt in (`MADV_MERGEABLE`, `PR_SET_MEMORY_MERGE`), and
  pytest workers do not opt in today. NC-48 already reports eligibility as
  "unverified".
- **WP8** measures an opt-in in the tester image (`pages_sharing` deltas per
  instance count). The provider raises `overcommit` only from such evidence.
- Swap and zswap are host policy. The allocator only reads `overcommit`.

## 5. Migration and work packages

### 5.1 Compatibility and the two live campaigns

- Two campaigns are running under the legacy script: a PWMCP campaign on host
  A and a ciu campaign on host B. They **finish under the legacy script**:
  - runner scripts are not replaced on a host while its campaign is active
    (the existing drain rule);
  - the new supervisor uses the same lock and active-record paths, so it
    refuses to start a campaign base the legacy script holds, and the reverse
    is also true;
  - `run-gate campaign adopt <base>` reads a legacy campaign directory for
    status only.
- `run-r2-campaign.sh` stays as a wrapper for one release:
  - its single-campaign form calls `run-gate campaign supervise` with the same
    arguments;
  - its fleet options print the equivalent `run-gate campaign plan/start` and
    still work locally;
  - after one release it is deprecated, then removed.
- `remote-gate.py` stays for one-shot lanes, and `host-sat-queue.sh` stays for
  serial light lanes. Both take a lease (WP4) so that they never overlap a
  campaign unseen (closes LR-KI-10).
- **What moves:**
  - supervision → Run-Gate;
  - memory guard and fleet → allocator + provider;
  - host-table schema ownership → the provider, with the same file and the
    existing keys plus optional `state` and `labels`;
  - generic runner scripts → a provider-neutral home after WP5 (NC-49).

### 5.2 Work packages (dependency order)

| WP | Scope | Owner | Tests | Risk | Depends |
|---|---|---|---|---|---|
| **WP1** | `host-facts/1` + `mdt host-facts`/`host-state` (host-setup env, cgprofile `host` verb, `/proc` fallback, KSM sysfs, tool and image inventory, cached doctor result); `mdt fleet hosts/facts/state` over the private host table; read-only | mdt-cockpit | fixtures for `/proc`, `/sys`, env file and cgprofile replies; fake SSH runner; `null`+reason for each unreadable leaf | low | — |
| **WP2** | `[lanes.*.demand]` parse/validate/`--list`; `run-gate campaign plan <lane> --facts FILE` (pure allocator, §4.2): JSON plan, printed arithmetic, ETA, and the equivalent legacy `run-r2-campaign.sh --instances/--shard-total/--shard-offset` lines per host | run-gate | table cases; properties: never exceeds C_h/M_h, never above J, monotonic in capacity; hand mutants of each min() term | low | — |
| **WP3** | `run-gate campaign supervise` (Python port of the host supervisor), with `run-r2-campaign.sh` as a wrapper | run-gate (+ netcup wrapper) | parity: port `scripts/netcup/tests/test_run_r2_campaign.py` cases; lock interop with the legacy script; one short-budget host rehearsal | medium | — |
| **WP4** | on-host lease ledger (`acquire/renew/release/list`, flock, TTL, generation, capacity check against policy); `mdt fleet lease`; lease use in remote-gate and the host-sat queue | mdt-cockpit (+ netcup) | concurrency (two acquirers, one wins); expiry; revocation; refusal names the binding constraint | medium | WP1 |
| **WP5** | `campaign start/tick/status/stop/collect`; SSH adapter; shard queue; store sync-back; merge placement; T3 ledger row; optional lowering-only `--memory-cap` | run-gate | fake transport end to end; a crash at each tick step is idempotent on re-run | medium | WP2-WP4 |
| **WP6** | host loss: grace, re-placement from the synced store, generation fencing, stale-store refusal at merge | run-gate | chaos with the fake transport (partition, return, double run) | medium | WP5 |
| **WP7** | profiled host runs (lift `RUN_GATE_PROFILE=off` where the observer exists), history collection, O/U/c/E fits, `footprint --write` demand fields, prediction-error record | run-gate (+ runner) | fit math on recorded progress files; drift warning | low | WP3 |
| **WP8** | runtime pressure shedding (youngest lease yields); KSM opt-in experiment in the tester image, measured | run-gate, mdt-cockpit, tester-unified | PSI fixtures; host measurement report | medium | WP4, WP7 |
| **WP9** | port `run_gate_campaign` into ciu8's gate (V8-19 parity); move the transport to v8.2 inventory/enrollment | ciu8 | WP2-WP6 tests as parity oracles | medium | ciu8 checkpoints D and G |
| **WP10** | move the generic runner scripts out of `scripts/netcup/runner/`; netcup keeps install only | netcup | path/doc contract tests | low | WP5 |

**First stage, shippable in a week:** WP1 + WP2, both read-only.

- They give the operator the placement answer and the exact commands for
  today's supervisor on every host, with no new process on any host.
- WP3 can run in parallel with them.

## 6. Failure modes and security

| Failure | Behaviour |
|---|---|
| Host unreachable | Its leases are unknown, so its CPU and memory are treated as in use. After the grace period its shards are `lost` and re-placed from the last synced store. At most 15 min of work per shard is lost. |
| Host returns after a partition | Its supervisors see a revoked or older lease generation and stop. Duplicate candidate execution only wastes CPU, because results are judge-deterministic. The merge accepts only the store of the generation recorded in the campaign plan. |
| Supervisor crash | The lease expires after its TTL (15 min). The next tick sees no live supervisor and restarts the next `-s<N>` on the same host from the persistent checkout, as legacy re-supervision does. |
| Controller loss | Hosts keep running and renewing locally. Ticks are idempotent and rebuild state from files. |
| Judge mismatch between hosts (another image or Assay) | Shards place only on hosts whose image digest and Run-Gate/Assay version match the campaign spec. The strict merge refuses a foreign judge in any case. |
| Runaway candidate (OOM) | The per-container cap kills it inside the instance. Assay records `crashed`. Other instances are protected by the slice and PSI shedding. |
| Overcommit spiral (swap storm) | PSI shedding: the youngest lease yields. The provider refuses new leases above PSI 25. |
| Hash imbalance on small sets | Over-partitioning (N = 2 x instances, at least 40 units per shard). Later, `--only-candidates` batches from `plan --list-pending`. |
| Lease leak | TTL expiry; `lease list` shows holder and age; ctlstat raises attention when a lease has no live supervisor. |
| Disk full from retained checkouts (LR-KI-12) | Facts carry `checkout_disk_free`, and the allocator skips a host below the threshold. Checkout cleanup stays LR-KI-12. |
| Stale host facts | The speed basis is disclosed and falls back to the lane history ratio. Doctor results carry an age, and a FAIL excludes the host. |

**Security:**

- **No credentials in the repository.** SSH argv, key paths, pinned
  `known_hosts`, addresses and provider ids stay in the controller-private
  host table, as today. Lease files and host facts contain no secrets. The
  campaign dir and ledger name hosts by alias only.
- **Least privilege.**
  - The allocator logs in as the non-root runner account.
  - It writes only the lease ledger (group-writable), `checkouts/`,
    `campaigns/` and `jobs/`.
  - Host policy files are root-owned and read-only to it.
  - Known gap: the runner is in the `docker` group, which is root-equivalent.
    That is acceptable on dedicated test hosts. mdtd's tool groups ("never
    docker") are the long-term fix, and a shared-prod host (`HOST_ROLE`) is
    never enrolled for campaigns without an explicit policy.
- **Identity.** Every launch or remote write runs `api_check` plus the on-host
  `guard`, as in `remote-gate.py`. The read-only `state` probe may skip
  `api_check`, as the queue's periodic probe does today.
- **Leases expire** (TTL, renewed only by the local supervisor). Revocation
  bumps the generation. Expiry is computed on the host's clock, so controller
  clock skew cannot extend a lease.
- **No inbound listener and no origin access on hosts.** Bundles are
  addressed by commit, and the supervisor refuses a dirty checkout.

## 7. Operator decisions and open questions

### 7.1 Decisions already taken by the operator (inputs, not reopened)

1. Only host installation is provider-specific. Once a host is up, we talk to
   it over SSH or `ciu`. The solution must work with any compute host we
   provide.
2. Run-Gate owns allocation (lean "1"). It must understand lane and candidate
   performance and resource needs, and distribute to known hosts by their
   known capabilities. A central instance knows the available hosts, their
   resources and state.
3. Host config, capabilities and host-wide policy are state. They belong
   outside this repository, with the one tool that provides the hosts.
4. "Workers per instance" is not a lane property. A lane states an efficient
   upper bound, its resource requirement, and its wall time relative to host
   performance.
5. RAM is overcommitted by plan, and KSM should be in place. 3 x 1 should
   cost about the same RAM as 1 x 3. The goal is to saturate all CPUs of
   every host.
6. Standing rules: CPU overcommit by design; only memory/IO PSI > 25 or heavy
   swap stop new work; tests are load-independent; limits are host-owned
   (AGENTS.md).

### 7.2 Open questions (recommended default first)

1. **Provider home:** mdt-cockpit host-setup now, mdtd verbs later (§3.2)?
   *Default: yes.* The alternative is a new minimal tool.
2. **Memory overcommit factor for expected peaks:** *default 1.0 until WP8
   measures KSM/zswap savings.* Caps are overcommitted from day one. Should
   hosts with swap start at 1.25 instead?
3. **Enroll the controller host as a compute host?** *Default: no.* It is
   shared with production. If yes, only with an explicit small
   `for_lanes` policy.
4. **Shard granularity:** *default N = 2 x initial instances, at least 40
   units per shard.* Dynamic candidate batches (`--only-candidates`) come
   later.
5. **Host-loss grace and sync interval:** *default sync every 15 min, lost
   after 30 min unreachable.*
6. **Fold the host-sat queue into the allocator later** (light lanes as
   1-unit campaigns)? *Default: not now.* The queue only takes leases (WP4).
7. **Runtime shedding:** may the youngest instance be stopped automatically
   under memory PSI > 25 (§3.5)? *Default: yes.* Its work resumes, and
   nothing is lost but the in-flight candidates.
8. **Where the tick loop runs:** *default: a detached controller loop like
   the host-sat queue (`run-job`-style, pause flag).* A systemd user timer
   is the alternative. Agent sessions never host it.

## 8. Verification boundary

- This is a design. Nothing was run on a host, and no code was changed.
- File references were read on origin/main `4f1ae7d70`, plus the in-flight
  branches `r2-guard-20261112` (NC-48) and `estate-req-20261112`
  (ESTATE-REQUIREMENTS).
- Measured numbers are quoted from the 2026-11-11/12 host reports.
- Model assumptions that WP7 must confirm, marked as such:
  - O and U in the worked example come from a single observation;
  - E(j) values and KSM effects are unmeasured.
