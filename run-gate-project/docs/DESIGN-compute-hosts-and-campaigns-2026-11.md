# Design: compute hosts and distributed lane campaigns (2026-11)

Status: DESIGN, decision record, rev 2 (operator interview 2026-11-12 folded
in), not implemented. Backlog: run-gate RG-272, netcup NC-49. Amends
`DESIGN-lane-rework-2026-11.md` §3.3 (see §3.0). No host names, addresses,
controller paths or credentials appear in this file; hosts are "host A/B".

Rev 2 changes, in brief:
- The provider is no longer mdt-cockpit. It is one small central scheduler
  service on the controller host, reached only through group-gated read and
  write sockets.
- The overcommit factor is 1.5, with PSI as the safety net.
- Shedding is now freeze + reclaim first, with stop + resume as the fallback.
- The controller host takes small lanes under a hard cap.
- Each compute host gets a guard, built as an extension of cgprofile.
- The tick loop runs in the service.
- Performance numbers are fitted per hardware flavor and never pooled across
  flavors.
- The preparation phase is computed once and overlapped with execution.
- Shedding parks an instance with freeze + reclaim until something changes.
- KSM is a host-policy item, not a savings assumption.

## 0. Decisions in one screen

| # | Decision | Section |
|---|---|---|
| D1 | One **central scheduler/distribution service** (working name `fleetd`) on the controller host owns the host inventory, host state, leases, allocation and the campaign tick. It holds the transport credentials. | §3.2 |
| D2 | Clients reach it **only through Unix sockets** built like cgprofile's: a read socket and a write socket, access by group membership, no authentication inside the service, and endpoints delivered by the host endpoints record. | §3.3 |
| D3 | **Run-Gate stays the lane layer.** It turns a lane into a work request (demand, image, commit, floors), submits it, and on each host executes the work (`run-gate <lane> --shard K/N`, `run-gate campaign supervise`). ciu projects ask for work through Run-Gate (later `ciu gate`) and never own hosts. | §3.4 |
| D4 | A lane declares **demand**, not parallelism: per-instance RAM, per-unit RAM/CPU/wall time and an efficient upper bound. Workers and instances are the scheduler's output. | §4 |
| D5 | Placement sums **expected peaks** against `RAM for lanes × 1.5`. Container caps may overcommit too. PSI is the runtime safety net. | §4.3 |
| D6 | A **guard on each compute host**, an extension of cgprofile in its protected unit, sheds load under pressure. The primary action is **freeze + `memory.reclaim`**: park the instance that no longer fits until another instance finishes or a RAM need changes, then thaw. **stop + resume from store** is the fallback. | §3.6, §3.7 |
| D7 | The controller host takes **small lanes only**, through the same allocator, under a hard cap. | §3.8 |
| D8 | Transport for the MVP is SSH with keys held by the service. ciu v8.2 enrollment can bootstrap or replace it later. | §3.5 |
| D9 | Performance is fitted **per hardware flavor** (a facts key). A rate measured on one flavor is never applied to another; a new flavor starts with a calibration shard. | §4.5 |
| D10 | The **preparation phase** (plan, baseline, workspace staging) is computed **once per commit + judge identity** and **overlapped** with candidate execution, instead of K times per campaign and again on every attempt. | §3.10 |
| D11 | **KSM is host policy** in the facts (ksmd on, opt-in shim in lane containers, ksmd CPU limit), never a savings assumption in placement. | §4.3 |

## 1. Problem and non-goals

### 1.1 Problem

- Long lane work (native Assay R2 campaigns of 300 to 1,500 candidates, 3 to 10
  hours; later fuzz/variation lanes) must keep **every CPU of every compute
  host** busy, on any host we provide.
- Several projects want work run: vbpub, dstdns, and later every ciu v8
  project.
- Today the only supervisor is `scripts/netcup/runner/run-r2-campaign.sh`
  (1,069 lines of bash), and it covers one host. Cross-host split is manual:
  the operator chooses `--shard-total/--shard-offset` per host and copies
  shard stores by hand before `--merge-only` (`runner/README.md:168-181`).
- The operator picks `--instances`, `--jobs` and the host. Nothing knows what
  the other hosts are doing.
- The memory guard charges each instance the lane's whole container cap
  (`run-r2-campaign.sh:568-594`). A PWMCP lane capped at 3 GiB ran 3 workers
  at about 430 MiB and about 1 CPU, but two instances were refused
  (2 x 3072 + 1024 > 6454 MiB available), and CPUs stayed idle. NC-48 (branch
  `r2-guard-20261112`) patches the arithmetic, but configures it through
  environment variables on the host.
- **"How is this netcup-specific?"** It is not. Only host installation is
  provider-specific: `install-host.py`, the SCP API client,
  debian-install-v2, `pre-reinstall*.py`.
  - The runner scripts, `remote-gate.py`, `host-sat-queue.sh` and the R2
    supervisor are generic SSH + Docker + Run-Gate code. They contain no
    provider API call, and their identity guard reads a generic
    `/etc/vbpub/server-id`.
  - They live under `scripts/netcup/runner/` only because runner provisioning
    was first written next to the netcup installer.
  - NC-49 moves the supervision logic out.

### 1.2 Non-goals

- No change to verdicts, judge identity or Assay's store format. Shards,
  strict merge and carry-over (RG-265, B529-B531) are used as they are.
- No authentication, accounts or tokens inside the service. Access is socket
  group membership, as with cgprofile (operator decision).
- No provider-specific code in the service. Provisioning ends with "the host
  is installed, then handed to the service".
- No cluster manager (Kubernetes, Nomad, Slurm, Buildkite). They are outside
  the estate's socket/group model and are not lane-aware.
- No automatic changes to host-wide policy (KSM, swap, zswap, slices). The
  scheduler and the guard read host policy. The guard acts only on lane
  cgroups.

## 2. Landscape

### 2.1 What exists (reuse, do not rebuild)

| Concern | Existing component | What it gives | File refs |
|---|---|---|---|
| Host table | controller-private JSON (`hosts.<name>.{ssh, api_check, guard, runner_home, cockpit, image, repos, queue_lanes}`) | reach a host, prove its identity, run a guarded command | `scripts/netcup/runner/remote-gate-hosts.example.json`, `scripts/controller/hosts.local.json.example`, `remote-gate.py:190-237` |
| Host choice | `remote-gate run auto` | the lowest CPU PSI below memory/IO PSI 25 | `remote-gate.py:240-269` |
| One-shot remote lane | `remote-gate run/status/logs/collect/cancel` | bundle → throwaway checkout → lane → collect | `remote-gate.py:738-975` |
| Serial lane queue | `host-sat-queue.sh` | per-host loop, pause flags, INFRA auto-pause, results TSV | `runner/host-sat-queue.sh`, `runner/README.md:772-838` |
| Campaign supervision | `run-r2-campaign.sh` | persistent checkout, attempt loop on exit 4, stall counter, stop/status, exact-name cleanup, shard fleet, merge, MemAvailable guard | `runner/run-r2-campaign.sh`, `runner/README.md:85-216` |
| Detached jobs, code transport | `run-job`, `sync-from-devcontainer` (git bundle) | survive SSH loss; hosts never need origin | `runner/README.md:68-83` |
| Shards, merge, carry-over | Run-Gate `--shard K/N`, STRICT `--merge-shards N`, `--carry-over`, `--merge-state`, `--only/--exclude-candidates` | independent instances with own store/container/verdict | `CONSUMERS.md:770-856`, `DESIGN-GUIDE.md:1130-1150` |
| Shard cut and manifests | Assay `blake2b(id) % N`, `shard.manifest` (`assay-shard-store/1`), `plan --list-pending` | deterministic split, strict-merge proof, pending lists | `assay/src/assay/mutation.py:1788-1793, 3497-3530`, `assay/src/assay/cli.py:504-526` |
| Worker sizing in one run | `jobs = "auto"` (`select_r2_jobs`), `r2_oversubscribe`, `candidate_ram`, `jobs_max` | workers from CPU cap, memory cap, RSS p95 | `run_gate.py:4889`, `DESIGN-GUIDE.md:1051-1128` |
| Socket design | cgprofile control/observer levels: `root:root` level directories, the socket inode's group grants access, run-gate reads endpoints from env only, the GID must differ from the Docker GID | the access model to copy | `DESIGN-GUIDE.md:380-430`, `cgprofile/docs/CGPROFILECTL-SPEC.md` |
| Endpoint delivery | per-service fragment `/etc/host-endpoints.d/<svc>.env`, aggregated by augeaswrap into `/etc/host-endpoints.env` and the login environment | how clients find a socket | `cgprofile/docs/DESIGN-GUIDE.md:864-885`, `augeaswrap/src/augeaswrap/endpoint.py:39-46` |
| Per-lane sessions, liveness policy | cgprofile `start` with consumer-authored policy (`--idle-bound`, `--ceiling`, `--on-stall kill\|report`), placement leaf with `memory.high/max`, `cgroup.kill` under explicit policy | the per-host lane authority the guard extends | `cgprofile/docs/RG55-INTERFACE-CONTRACT.md:549-660` |
| Per-host measurement | cgprofile `host` verb: `machine/1` (cpus, CPU-speed score) + HostSnapshot (meminfo, PSI, slices) | live host state and a normalisable speed | `cgprofile/docs/PROTOCOL.md:600-625`, `RG55-INTERFACE-CONTRACT.md:164-182` |
| Tier PSI responder | `mdt-psi-guard` (host-setup): weights to 1, then `MemoryHigh` of dev tiers → `0.9 × current`; `report` by default; "never freezes or kills" | the tier-level backstop | `mdt-cockpit/host-setup/README.md:199-228`, `docs/design/RESOURCE-PROFILES.md` §7.5 |
| Host-wide policy | `/etc/mdt/host-setup.env` (`HOST_ROLE`, `MDT_EXCLUDED_*`), slice tree, overcommitted tiers, zswap writeback per tier | the capacity object (`dev-gates.slice`) and its policy | `mdt-cockpit/host-setup/README.md:27-157` |
| Host readiness | ESTATE-REQUIREMENTS.toml + `estate_doctor.py` (branch `estate-req-20261112`) | what a host must provide, read-only probes | `mdt-cockpit/docs/ESTATE-REQUIREMENTS.toml` |
| Per-daemon admission | ciu v8 S21 tickets (`max_concurrent`, later bytes), Run-Gate `[admission]` | local count cap on one Docker daemon | `ciu/docs/SPEC-V8.md` S21, `CONSUMERS.md:2026-2070` |
| Estate rules | AGENTS.md "Compute use", "limits are host-owned" | stop only on memory/IO PSI > 25 or heavy swap; about one single-threaded job more than the core count; "When a tool needs a limit, it asks the host policy." | `AGENTS.md:196-226` |
| T3 request interface | entries + `t3-results.tsv` ledger (designed, not built) | stable request/result format | `DESIGN-lane-rework-2026-11.md` §3.4 |
| Estate status | ctlstat requirements §G (hosts), §I (campaigns/shards), G5 | the consumer of fleet state | `docs/design/ctlstat/REQUIREMENTS.md` |

### 2.2 "Assay already provides resource and performance data, correct?" Partly.

| Data | Producer | Exists today | Used for placement today |
|---|---|---|---|
| Per-candidate elapsed, CPU seconds, peak RSS, write bytes, judge RSS | Assay progress events (`event=candidate`); with an observer, cgprofile measurement windows (RESOURCE-PROFILES D1, §4) | yes, per run, in `.assay/progress-*.jsonl` | only inside one run: `jobs = "auto"` takes candidate RSS p95 (`candidate_ram_from_history`, `run_gate.py:11649`) |
| Candidate count and pending set | `assay plan`, `plan --list-pending`, shard manifests | yes | no |
| Schedule predictions (longest-first, RSS mix) | Assay `.schedule-history.v1` in the store | yes, inside a store | no |
| Campaign closeout (imported, executed, buckets) | `assay_analysis/campaign.py` | yes | no |
| Per-lane run duration, peak memory, cores, memory-full stall | Run-Gate `.run-gate/history.json` from the cgprofile session summary | yes, per checkout | R-45 headroom wait only (observational) |
| Committed per-lane footprint | `run-gate footprint --write` → `run-gate.footprint.json` | **only run-gate-project** has one | planned for T3 claim (not built) |
| Host CPU speed | cgprofile `machine/1.speed` from the host benchmark record | where measured | no |
| Live host memory, PSI, slices | cgprofile `host` verb | yes | `remote-gate auto` reads PSI over SSH |

**Missing:**

1. **Host runs are not profiled.** Remote launches set
   `RUN_GATE_PROFILE=off` (LR-KI-11), so the hosts where campaigns run
   produce no footprint.
2. **Data stays on the host.** History lives in each host's checkout, and
   nothing collects it.
3. **No per-instance fixed cost O.** Assay measures candidates, not the
   instance overhead (judge process, container, baseline). Without O,
   "3 x 1 vs 1 x 3" cannot be predicted (§4.4).
4. **No efficiency curve E(j).** Worker scaling was compared only by hand
   (`run-jobs-compare.sh`). Assay says counts above 3 were never run live.
5. **No hardware-flavor key on measurements.** History and progress records
   carry no flavor. Mixing them would blend different hardware: the
   operator states that runs on the two test hosts are not comparable. A
   single benchmark score cannot convert one flavor's rates into another's,
   because memory bandwidth, cache, disk and steal time differ (§4.5).
6. **The preparation phase is paid K + attempts times** (§3.10).
7. **Footprints committed for one project only.**

These gaps are WP8, except item 6, which is WP13.

### 2.3 What is missing in the architecture

- a machine-readable host capability record;
- allocation state and leases (LR-KI-10 overlaps; RG-56 atomic reservation is
  open);
- a placement function;
- lane demand that separates the expected peak from the cap;
- the cross-host campaign lifecycle (dispatch, store sync-back, merge
  placement, host loss);
- a host-side guard that sheds lane load before the host thrashes;
- a single source of estate state for ctlstat.

## 3. Architecture and ownership

### 3.0 Relation to earlier decisions

- **Amends `DESIGN-lane-rework-2026-11.md` §3.3** ("extend the host queue and
  `remote-gate`, one loop per host"):
  - **kept:** lane execution on a host stays single-host Run-Gate; the T3
    entry and ledger formats remain the request/result interface (the
    service writes ledger rows);
  - **changed:** per-host claim loops are replaced by the central service.
- **Supersedes rev 1 of this doc:**
  - mdt-cockpit is no longer the provider;
  - the allocator is no longer a Run-Gate module (the operator chose option 3,
    a central service).
- **Departs from `mdt-psi-guard`'s "never freezes".** The guard may freeze
  lane cgroups, but only under the invalidation rule of §3.7, which removes
  the stated reason ("a wall-clock budget that expired while frozen would
  change a result").

### 3.1 Picture

```text
 controller host (systemd)                                    clients (devcontainers, scripts)
 ┌────────────────────────────────────────────┐   observe   ┌───────────────────────────────┐
 │ fleetd  (one process, stdlib)              │◄────────────│ run-gate campaign plan/status │
 │  state: /var/lib/fleetd/state.json         │  socket     │ ctlstat (estate state)        │
 │  creds: /var/lib/fleetd/ssh/ (0600 fleetd) │   control   │ run-gate campaign submit/stop │
 │  tick (30 s): probe, renew, dispatch,      │◄────────────│ fleetctl drain/undrain        │
 │   sync stores, re-place, merge, ledger     │  socket     └───────────────────────────────┘
 │  small-lane pool for this host (cap §3.8)  │   endpoints from /etc/host-endpoints.env
 └───────────────┬────────────────────────────┘
                 │ SSH as `runner`, keys held only by fleetd
   ┌─────────────┴────────────┐        ┌────────────────────┐
   ▼ host A                   ▼ host B ▼ any handed-over host
 ┌──────────────────────────────────────────────┐
 │ runner: run-gate campaign supervise (per lane │  lease records: $RUNNER_HOME/fleet/leases/*.json
 │   instance) → run-lane.sh → cockpit → run-gate │  (renewed locally by the supervisor)
 │   <lane> --shard K/N --jobs j                  │
 │ cgprofiled (protected unit): sessions + GUARD  │  freeze/reclaim/thaw lane cgroups by policy,
 │   reads PSI, reports guard state               │  events → probed by fleetd
 │ mdt-psi-guard (tier backstop, unchanged)       │
 └──────────────────────────────────────────────┘
```

### 3.2 (ii) The central scheduler/distribution service (`fleetd`, working name)

**Why a service (operator: "option 3").**

- Several projects (and several ciu projects) want work run on the same hosts.
- Host ownership must sit in one place that is not a project, not a
  devcontainer and not an agent session.
- mdt is a devcontainer the operator uses, so it is not a provider.
- A ciu config would make ciu the owner of hosts. With several ciu projects,
  each would own them.

**What it owns, and what it does not:**

| It owns (state) | It does not own |
|---|---|
| Host inventory: alias, transport target, state (`enabled`/`draining`/`disabled`), labels, the class (`compute` or `controller-small`) | lane definitions and demand (the projects' `run-gate.toml`) |
| Host facts cache (`host-facts/1`, refreshed per tick) and the guard state per host | host-wide policy (stays in each host's `/etc/mdt/host-setup.env`; the service only reads it) |
| Leases (who holds which CPU/RAM share of which host, generation, expiry) | verdicts (Run-Gate/Assay produce them; the service collects files) |
| Campaigns: request, placement, shard → instance → host, generations, store-sync marks, results, ledger rows | Assay stores (they stay on hosts; the service only copies them) |
| Transport credentials (SSH keys, pinned host keys) | users, tokens, authentication |

**How it stays small:**

- One process: a stdlib Python daemon, like cgprofiled.
- One state file: `state.json`, atomic replace + fsync, plus a bounded
  `events.jsonl`.
- One collected-results directory.
- Two sockets.
- One tick every 30 s. A tick does:
  1. one composite SSH probe per host (facts, lease records, supervisor
     records, guard state, checkout disk);
  2. expire or adopt leases;
  3. dispatch pending shards to free capacity;
  4. copy shard stores every 15 min;
  5. re-place lost shards;
  6. start a merge when a campaign's shards are final;
  7. append ledger rows.
- Target size: about 2-3k lines with tests, the same order as `remote-gate.py`.

**Host facts (`host-facts/1`).** The record is collected each tick by one
composite probe. Every leaf may be `null` with a `*_reason`, never a guess, as
in cgprofile `machine/1`.

```json
{"schema": "host-facts/1", "at": "…",
 "flavor": {"id": "sha256:…", "cpu_model": "…", "cpus_online": 4, "hypervisor": true, "smt": false,
            "memory_total_bytes": …, "disk_scope_key": "…", "kernel": "7.x", "virt_class": "kvm"},
 "cpu": {"for_lanes": 4.0, "oversubscribe_max": 1.25},
 "memory": {"for_lanes_bytes": …, "swap_areas": 4, "swap_bytes": …, "overcommit": 1.5,
            "zswap": {"enabled": true, "writeback_gates": true}},
 "ksm": {"run": 1, "advisor_mode": "scan-time", "advisor_max_cpu": 8,
         "pages_shared": …, "pages_sharing": …, "general_profit": …,
         "lane_optin_shim": true},
 "kernel_interfaces": {"cgroup_freeze": true, "memory_reclaim": true, "zswap_writeback": true},
 "policy": {"host_role": "dedicated-dev", "psi_refuse_some_avg10": 25, "max_lane_containers": 8,
            "max_park_seconds": 7200, "class": "compute"},
 "speed": {"basis": "geekbench7-measured", "single_core": …, "baseline_score": 2500},
 "tools": {"run_gate": ["25.5.0"], "images": {"tester-unified:local": "sha256:…"}, "cockpit": "up"},
 "readiness": {"doctor": "PASS", "at": "…", "failed": []}}
```

- **`flavor`** identifies the hardware class: CPU model, online CPUs, SMT,
  hypervisor and virtualisation class, total RAM, disk identity (iobench's
  `disk_scope.key`) and kernel. `flavor.id` is a digest of those fields, and
  it keys every performance fit (§4.5). Two hosts share fits only when their
  `flavor.id` is equal.
- **`ksm`** is host policy and state:
  - whether ksmd runs, the advisor mode and its CPU limit (the planned host
    tuning is about 5-10% of one core);
  - the sharing counters;
  - whether lane containers get the opt-in shim. ciu already ships it
    (`ciu/src/ciu/ksm.py`, `governance.ksm_optin`), and Run-Gate lane
    containers are to receive it by default (job ksm-default2, in progress).

  Placement never assumes KSM savings (§4.3).

**Handing a host to the service** (after provisioning):

- As root on the controller host, run `fleetd enroll <alias> --target
  runner@<addr> --known-host <file>`. It creates a key pair under
  `/var/lib/fleetd/ssh/<alias>/` and prints the public key.
- Runner provisioning (or the operator) installs that key for the `runner`
  account. `fleetd` verifies it with a guarded `version` probe, and the host
  goes `enabled`.
- Credentials never pass through a socket, and no client ever sees them.
- An optional per-host identity command (provider-specific, outside the
  repository) can be configured. The service runs it before every launch, as
  `api_check` is run today.
- ciu v8.2 enrollment (`get.py enroll`, pinned key, `version --json`) can
  replace this bootstrap later (WP11).

**Controller host restart (and service restart).** systemd restarts `fleetd`.
It loads `state.json`, then **reconciles with the hosts, which are the truth
for what runs**:

- Each host keeps lease records (`$RUNNER_HOME/fleet/leases/<lease>.json`:
  campaign, shard, generation, commit, lane, renewed_at, expires_at). The
  local supervisor renews them, so nothing on a compute host depends on the
  controller being up.
- The first tick reads every host's records and supervisor active records:
  - running leases are adopted;
  - leases that expired while the service was down are released;
  - shards with no live record are re-dispatched.
- If `state.json` itself is lost, campaigns are rebuilt from the lease records
  plus the collected-results directory. Only queued-but-never-dispatched
  requests are lost; they are re-submitted (ledger "missing" attention).

### 3.3 Sockets (the cgprofile design, reused)

| Level | Socket | Group | Verbs |
|---|---|---|---|
| observe (read) | `/run/fleetd/observe/fleetd.sock` | `fleet-observe` | `version`, `hosts`, `facts <host>`, `state [<host>]`, `leases`, `campaigns`, `campaign <id>`, `plan <request>` (dry run, no lease), `fetch <campaign>` (stream collected results), `events --since` |
| control (write) | `/run/fleetd/control/fleetd.sock` | `fleet-control` | `submit <request>`, `cancel <campaign>`, `drain/undrain <host>`, `priority <campaign> <class>`, `light-run <request>` (controller pool) |

- **Directories:** level directories are `root:root 0755`, and the socket
  inode's group grants access. Both GIDs are non-zero and differ from the
  Docker GID (the rules of `DESIGN-GUIDE.md:401-430`).
- **No authentication in the service.** `SO_PEERCRED` uid/gid/pid is recorded
  as the `holder` of each request, for audit and for `cancel`, never as
  authorisation.
- **Endpoint delivery:** `fleetd install` writes
  `/etc/host-endpoints.d/fleetd.env` (`FLEETD_OBSERVE_SOCKET`,
  `FLEETD_CONTROL_SOCKET`, their directories and GIDs), and augeaswrap
  aggregates it.
  - Clients read only the environment and never embed a path, as Run-Gate
    does for cgprofile.
  - A devcontainer receives the variables, read-only identity binds of the
    level directories, and `--group-add` of the groups it may use.
  - ctlstat needs only `fleet-observe`.
  - The same MDT-086 delivery gap that blocks cgprofile variables in the
    standard template applies, and must be closed for both.
  - ESTATE-REQUIREMENTS gains a `fleetd_endpoints` component (coordinate with
    the estate-req branch).
- **Code upload without path translation:** `submit` passes the git bundle as
  an **open file descriptor** (`SCM_RIGHTS`). Devcontainer paths and host
  paths then never have to agree.

### 3.4 (iii) Who decides what: service vs Run-Gate vs projects

| Step | Owner |
|---|---|
| Resolve lane → work request: lane, project dir, commit, image, environment mode, `shardable` (RG-265 refusals), floors (Run-Gate/Assay), demand (§4.1) with measured fits, units remaining (`assay plan`), carry-over source + reason | **Run-Gate client** (`run-gate campaign plan\|submit <lane>`) |
| Choose hosts, shapes (instances × workers), shard count, order across projects | **fleetd** |
| Execute one instance on a host: lease renewal, attempts, stall, cleanup, `--shard K/N --jobs j`, merge run | **Run-Gate on the host** (`run-gate campaign supervise`) |
| Judge candidates, shard manifests, strict merge | **Assay** (unchanged) |
| Shed load under pressure on one host | **guard** (cgprofile) by the policy in §3.6 |

**How a ciu v8 project asks for work without owning hosts:**

- Today, from a dstdns or vbpub worktree: `run-gate <lane> --campaign submit`.
- In v8 the same verb is `ciu gate <lane> --campaign submit` (Run-Gate is
  merged into ciu, D-647 #1). The client part moves with it, and its socket
  protocol does not change.
- The ciu project needs only `fleet-control` membership and the endpoint
  variables. Its own `ciu.hosts.toml` (deployment targets) is unrelated to
  compute hosts.
- Projects compete through **priority classes**, not ownership:
  `release-t3` > `campaign` > `light`, FIFO within a class.
- A host's local per-daemon admission (ciu S21 tickets, Run-Gate
  `[admission]`) still applies to every lane started there. fleetd's leases
  are the cross-host intent, and local tickets are the local order. They do
  not conflict, because fleetd never places more than the host's published
  `max_concurrent`.

### 3.5 (iv) Transport

- **MVP: SSH as the `runner` account**, with keys and pinned `known_hosts`
  held by fleetd and `BatchMode=yes`.
  - The on-host guard command (hostname + `/etc/vbpub/server-id`) is prefixed
    to every remote script, as `remote-gate.py:229-237` does.
  - The optional identity command runs before every launch or write.
  - Code travels as a git bundle imported by `sync-from-devcontainer
    --no-current-link` into `$RUNNER_HOME/checkouts/<commit>`.
  - Long processes start through `run-job`.
- **ciu later:**
  - the transport adapter (`run`, `put`, `get`) is the only seam;
  - v8.2 enrollment and `ciu ssh` can serve it;
  - fleetd keeps the inventory, state and leases either way.
- **Not Docker contexts:** admission and observers are local to a daemon.

### 3.6 (v) The per-host guard: extend cgprofile, keep mdt-psi-guard as backstop

**Is cgprofile already the guard?** Half of it.

- **Already there:**
  - a root daemon in a protected unit (host-setup lists it among the
    `OOMScoreAdjust=-900` services);
  - one session per lane cgroup, with consumer-authored policy
    (`--idle-bound`, `--ceiling`, `--on-stall kill|report`);
  - placement leaves under `dev-gates.slice` with `memory.high/max`;
  - authority to write `cgroup.kill` under explicit policy;
  - the HostSnapshot with PSI per slice;
  - `throttled` detection;
  - the observer measurement windows that Assay already uses.
- **Missing:**
  - a host-level pressure policy;
  - freeze/thaw/reclaim actions;
  - the mapping from session to lease and priority;
  - freeze accounting in measurement windows;
  - a guard state view.

**Decision: extend cgprofile (`cgprofiled guard`) rather than add a daemon.**

- A second root daemon would duplicate the session registry and race cgprofile
  over the same leaf cgroups.
- The objection "the daemon measures and decides nothing" (ciu S21.4.1) is met
  the way `--on-stall kill` already meets it: **the policy is authored by the
  consumer**, and the daemon executes it mechanically.
  - **Per session:** Run-Gate passes `--shed freeze|stop|never`,
    `--shed-rank <int>` and the lease id at `start`. fleetd sets the rank from
    priority class and lease age, youngest and lowest class first.
  - **Per host:** thresholds come from host policy, a root-owned
    `[guard]` section in cgprofile's config set by host install.

**Guard policy (per host, defaults):**

| Signal | Threshold | Action |
|---|---|---|
| host memory PSI some avg10 | > 25 for 30 s (estate rule) | **park one**: freeze + reclaim the highest-rank sheddable session (§3.7); stop it instead only when it is `--shed stop` |
| host memory PSI full avg10 | > 10 for 10 s | park one immediately, then re-evaluate every 30 s |
| swap-in rate | > policy pages/s for 60 s | park one |
| a re-evaluation event (§3.7) and all signals below half the threshold | for 120 s | **thaw one** (lowest rank first), only when `MemAvailable` ≥ that session's expected peak |

- **Division of work with `mdt-psi-guard`:**
  - the cgprofile guard acts on **lane leaves** at the lower thresholds;
  - mdt-psi-guard keeps acting on **tiers** (weights, tier `MemoryHigh`) at
    its own thresholds, as the backstop for non-lane load.
  - They write different cgroups, so they never fight over one key.
  - Both write events.
- **State reported** (`cgprofilectl guard status --json`, also on the observer
  socket): per session: lease, rank, state (`running`, `frozen`,
  `reclaiming`, `stopped-by-guard`), frozen seconds, swap of the leaf, last
  action; per host: signals and the policy in force. fleetd's probe reads it
  every tick.

**Interface to ctlstat.** Estate state comes from **one** read: `fleetd
observe state --json`.

- It covers hosts, facts, guard state per host, leases, campaigns, shards and
  ETA, which are ctlstat sections G, H and I (`REQUIREMENTS.md` §3).
- ctlstat no longer needs its own `--remote` SSH probe per host
  (`REQUIREMENTS.md` item 9). The probe moves into fleetd's tick, which runs
  anyway.
- Attention items (stalled shard, host lost, guard froze something for more
  than N min, lease without supervisor, INFRA streak) are fields in that JSON,
  derived mechanically (ctlstat G2).

### 3.7 Shedding: what it means, and freeze + reclaim

**Shedding** means that when a host approaches thrashing (memory/IO PSI above
the estate limit, heavy swap-in), the guard takes **one** lane instance out of
the competition for RAM and CPU, so the remaining instances progress at full
speed instead of all of them stalling. It does not change what is computed,
only when.

**Mechanisms** (kernel 7.x, cgroup v2 on our Debian 13 hosts; facts confirm
per host):

| Interface | Effect | Gives back RAM? |
|---|---|---|
| `cgroup.freeze` = 1 (what `docker pause` does on v2) | every task in the cgroup stops; `cgroup.events` shows `frozen 1` | **No, not by itself.** Pages stay resident until global reclaim finds them, and under pressure the kernel reclaims from every cgroup, so the running ones still thrash. |
| `memory.reclaim` (write a byte count, optional `swappiness=`) | proactive reclaim **of this cgroup only**: its cold anon pages go to zswap/swap, its file pages are dropped | yes, deliberately and only from the frozen group |
| `memory.high` lowered to ≈ current − target | the kernel throttles and reclaims the group continuously | yes, but slower, and running tasks are throttled (mdt-psi-guard's method) |
| `memory.swap.max`, `memory.zswap.max`, `memory.zswap.writeback` | bound where reclaimed pages go (zswap first, disk under policy) | n/a (placement of reclaimed pages) |
| `cgroup.kill` | ends the instance | yes, entirely (cgprofile already uses it under explicit policy) |

- **Primary action: park with freeze + reclaim** (operator: "park one thing
  that apparently doesn't fit anymore on this host until something else
  finished or RAM requirements changed"):
  1. **Choose** the instance that no longer fits: the highest shed rank,
     which is the youngest lease of the lowest priority class. Among equals,
     choose the one whose measured peak exceeds its expected peak the most,
     because that is the one that "apparently doesn't fit".
  2. **Freeze** its leaf (`cgroup.freeze = 1`) and wait for `frozen 1` in
     `cgroup.events`.
  3. **Reclaim:** write `memory.reclaim` in steps (for example 256 MiB) until
     the leaf's `memory.current` reaches its non-reclaimable floor or the host
     signal releases.
  4. **Account:** the lease stays held and is marked `parked`. Its expected
     peak moves from the RAM budget to the swap budget in fleetd's
     accounting, so the scheduler does not refill the freed RAM with new work
     on that host while something is parked there.
  5. **Re-evaluate on events, never on a timer alone.** fleetd and the guard
     re-check the parked instance when:
     - another instance on the host finishes or releases its lease;
     - a running instance's measured peak drops, or a lane's measured or
       declared RAM need changes (new fit, new commit, demand edit);
     - host policy changes (facts);
     - another host gains room. fleetd may then **move** the parked shard:
       stop the parked instance and re-dispatch the shard from its store on
       the host with room. This uses the stop + resume path, under a new
       generation.
  6. **Thaw** when it fits again: one at a time, lowest rank first, only with
     free RAM ≥ its expected peak and signals below half the threshold.
     Pages fault back in on demand, and the one-at-a-time rule bounds the
     swap-in burst.
- Swap: 4 swap areas on each host's install layout, plus zswap writeback per
  tier (host-setup). zswap keeps compressed pages in RAM (about a third of
  their size), so heavy reclaim of a large group also needs disk writeback,
  which host-setup allows for `dev-gates`.
- **The cost, stated precisely: wall-clock time keeps running for a frozen
  instance.**
  - `CLOCK_MONOTONIC` advances during a freeze (freeze is not suspend).
  - On thaw, a candidate's per-candidate timeout and any test's own sleep or
    timeout may fire. A candidate could then be recorded `hung` or
    falsely `killed`, which changes a result. That is exactly why
    mdt-psi-guard refused to freeze.
  - Run-Gate's lane `budget` also counts frozen time unless handled (below).
- **Wall-clock deadlines of a parked group.** Each deadline is handled
  explicitly:

  | Deadline | Clock | Handling while parked |
  |---|---|---|
  | Candidate timeout, test-internal sleeps and timeouts | inside the frozen processes, `CLOCK_MONOTONIC` | cannot be paused from outside, so the **invalidation rule** applies: a candidate in flight across a freeze is discarded and re-executed |
  | Run-Gate lane `budget` (hard exit 4) | Run-Gate client, outside the leaf | Run-Gate subtracts the session's `frozen_seconds` (read from cgprofile) from elapsed budget time; exit 4 stays resumable regardless |
  | cgprofile liveness (`idle-bound`, `ceiling`, stall/hung) | daemon | paused while the session is frozen, as the idle clock already pauses under memory pressure; `--on-stall kill` can never fire on a parked session |
  | Assay campaign deadline (`campaign init --hours`) | persisted wall-clock end | counts active time only: the supervisor extends it by the parked duration, and the record shows how much was added |
  | Supervisor stall counter, fleetd lease TTL | host supervisor, outside the leaf | a parked attempt is not a stall; the supervisor keeps renewing the lease, which shows `parked` |

- **Invalidation rule (makes freeze safe):**
  1. cgprofile adds `frozen_seconds` to each observer measurement window
     (contract-3 window, RESOURCE-PROFILES §3) and to the session summary.
  2. Assay treats a candidate whose window has `frozen_seconds > 0` as **not
     finished**. Its outcome is discarded and it is re-queued, like a
     contaminated measurement. Finished candidates are untouched.
  3. Non-campaign lanes (plain pytest suites with timeouts) are `--shed stop`
     or `never` unless the lane declares `freezable = true`.
  4. Until this rule ships (WP7), the guard runs freeze in `report` mode and
     acts by stop + resume.
- **Fallback: stop + resume from store**, used when:
  - a lane is `--shed stop`;
  - the host has no swap or zswap headroom left to reclaim into;
  - the instance has been parked longer than the policy limit (default 2 h);
  - fleetd moves the shard to another host;
  - the host must drain;
  - the lane runs on the controller host.

  Steps:
  1. `cgroup.kill` the leaf, or SIGTERM through the supervisor.
  2. Finished candidates stay in the shard store; in-flight ones are lost.
  3. The lease returns to fleetd, and the shard is re-dispatched later, to
     this or another host.

| | freeze + reclaim | stop + resume |
|---|---|---|
| Lost work | in-flight candidates (re-executed) | in-flight candidates + one baseline + container start on resume |
| RAM freed | cold pages, after reclaim (seconds to minutes) | everything, at once |
| Resume cost | swap-in of the working set on thaw | full restart; can move to another host |
| Correctness needs | the invalidation rule and deadline handling (WP7) | nothing new |
| Resumes on | thaw on the same host after a re-evaluation event | dispatch to any host with room |
| Host-loss interaction | a parked instance is lost with its host | none |
| Best for | short pressure spikes on a host that otherwise fits | long overload, or a host that must be drained |

### 3.8 The controller host: small lanes only

The controller host runs production (the game server's slice). It joins as
class `controller-small` with a **hard cap**:

- **Eligible lanes:**
  - `unit = "run"` (no campaigns, no shards);
  - declared `resources.memory` ≤ **2 GiB**;
  - footprint median duration ≤ 20 min;
  - not `resources.shared`;
  - priority class `light`.
- **At most one** fleet lane at a time, and it **takes one of the two
  controller gate slots**. fleetd holds the slot through the same lock the
  gate wrapper uses, so agent gate runs and fleet lanes never exceed two.
- **Admission check, every launch:**
  - host memory PSI some avg10 ≤ **10** (stricter than 25);
  - the devcontainer's anon memory is below the **5.0 GB hold line**, and
    anon + 2 GiB stays below its high mark;
  - `dev.slice` usage + 2 GiB ≤ `RAM_MDT` (host-setup);
  - no guard or psi-guard action in the last 10 min.
- **Placement:**
  - the lane runs in `dev-gates.slice`, never in the production slice;
  - the production slice's `memory.min` protection is untouched;
  - when the production slice pins CPUs, `dev-gates.slice` gets the
    complement through host policy (`AllowedCPUs`), a host-setup setting that
    fleetd only checks.
- **Shedding:** stop only, never freeze. The lane is re-queued, and nothing
  is parked on the controller host.

### 3.9 Campaign lifecycle (service tick + host supervisor)

- **Host side (`run-gate campaign supervise`, through `run-job`):**
  - A port of `run-r2-campaign.sh` with the same behaviour:
    - clean persistent checkout;
    - attempt loop on exit 4;
    - stall counter on Assay `event=candidate`;
    - exact-name cleanup;
    - `stop`/`status`;
    - fresh verdict copy;
    - `final-exit` + `campaign-summary.json`.
  - It keeps the same lock, `.active` and `campaigns/<start>/` layout, so
    legacy and new supervisors exclude each other.
  - New: it renews its lease record every 60 s and checks the generation
    before each attempt and each next shard (fencing).
  - It passes `--shed`/`--shed-rank`/lease id to the cgprofile session.
  - It runs with the observer on (lifts LR-KI-11).
  - It reads its next shard from a dispatch file that the service writes.
- **Service side (tick):**
  - dispatch;
  - store sync-back every 15 min;
  - merge on the host that already holds the most shard stores (missing
    stores are pushed there; STRICT `--merge-shards N`);
  - carry-over: the main store is pushed to each chosen host before the first
    attempt, with the reason from the request;
  - T3 ledger rows.
- **Host loss:** a host unreachable for 2 x TTL (30 min) has its shards marked
  `lost`. They are re-placed from the last synced store under a new
  generation. When the host returns, its supervisors see a revoked
  generation and stop, and an old-generation store is never merged.

### 3.10 Preparation phase: once per judge identity, overlapped with execution

**Today:**

- Every Assay run, which means every shard instance, every re-attempt after
  exit 4 and the merge run, repeats the preparation phase:
  1. workspace setup (cowtree/btrfs clones);
  2. plan building (candidate generation, plan digest);
  3. the **baseline**, a full unmutated suite run that proves the suite
     passes in this environment;
  4. only then candidate execution.
- With K shard instances and A attempts, the baseline runs about K × A + 1
  times. Each run costs about one candidate's wall time with the whole suite
  in memory, and baseline peaks coincide, which is why the runner staggers
  starts today.
- CPUs sit partly idle during the baseline (one suite, not j workers).

**Target (operator: "preparation phase should run in parallel to execution
phase"):**

1. **Shared preparation artifact, computed once.**
   - Assay gets `assay prepare --emit DIR`, which writes plan + baseline
     result: per-test outcome and durations, plan digest, judge identity,
     commit, tester image digest.
   - The artifact is keyed by **(commit, `judge_sha256`, image digest)**.
   - `assay run --prepared DIR` validates every key and refuses a mismatch by
     name, just as `--merge-shard-store` refuses a foreign store.
   - fleetd runs the prepare job first, on any eligible host, and distributes
     the artifact with the shard stores.
   - Re-attempts and the merge run reuse it, so a campaign pays **one** full
     baseline instead of K × A + 1.
2. **Overlap: speculative execution held until the baseline is proven.**
   - Shard instances do **not** wait for the artifact. Each builds its plan
     locally (deterministic; the plan digest is checked against the artifact
     later) and starts candidates at once.
   - Each outcome is **held** (written to a pending area, not to the final
     store) until the shared baseline has passed and the host's environment
     check has passed (point 3).
   - If the baseline fails, the held outcomes are discarded and the lane
     reports the baseline failure as today. The cost is wasted speculative CPU
     only, never a wrong verdict.
3. **Per-host environment proof stays.**
   - The full baseline also proves that this host's environment runs the
     suite. A cached artifact from another host must not remove that proof.
   - Each instance therefore runs a short **environment check** in parallel
     with its first candidates:
     - collection only (`pytest --collect-only` or the runner's equivalent);
     - a fixed sample of the baseline's tests named in the artifact: the
       shortest test per test file, bounded to ≤ 10% of the baseline time.
   - Held outcomes of that instance are committed only after its check
     passes.
   - Instances on a flavor without a passed full baseline for this judge run
     the full baseline themselves (in one worker slot, overlapped).
4. **Staging ahead.**
   - Assay keeps one prepared candidate workspace per worker staged ahead
     (clone + mutation applied) while the current candidates run.
   - Depth is 1 per worker, bounded by free disk and by the in-instance memory
     budget.
   - Staging hides clone latency. With btrfs reflinks it costs metadata and
     page cache only.

**What changes where (WP13):**

| Component | Change |
|---|---|
| Assay | `prepare --emit`, `run --prepared` (strict validation), held outcomes (a pending area committed on baseline + environment-check success), the environment-check mode, one-ahead workspace staging; the judge identity is unchanged (the artifact is an input keyed by it, not part of it) |
| Run-Gate | pass `--prepared` through; artifact location `.run-gate/assay-prepared/<project>/<lane>/<judge>`; a campaign client phase 0 "prepare"; budget accounting for the held state |
| fleetd | schedule prepare first, distribute the artifact, start shards speculatively in parallel, merge and re-attempts reuse the artifact |
| cgprofile | none (windows already cover the prepare job) |

**Risk, stated.**

- The artifact is a cache of a proof. Its keys must cover everything the
  baseline depends on, so the image digest is included, and so is the host
  flavor when a lane's baseline is flavor-sensitive (declared by the lane).
- Speculative candidates on a broken host waste CPU until the environment
  check fails. That is bounded by the check's short duration.

## 4. Lane demand instead of workers per instance

### 4.1 Schema (`[lanes.<lane>.demand]`, RG-272)

Four new keys plus one flag. Everything else reuses existing keys. All are
estimates, and measured values win (§4.4).

```toml
[lanes.r2.resources]              # EXISTING, unchanged meaning
memory = "3g"                     # per-container hard cap = runaway bound, NOT demand
cpus = "3"                        # per-container CPU cap
candidate_ram = "450m"            # EXISTING per-unit RAM floor (default for unit_memory)

[lanes.r2.demand]                 # NEW
unit = "candidate"                # candidate (native R2) | worker (xdist) | run (unsplittable)
instance_memory = "250m"          # fixed RAM per instance above its units (judge, container, baseline)
unit_cpu = 0.6                    # busy cores per running unit; < 1 means IO phases leave room
unit_seconds = 95                 # cold-start estimate of wall seconds per unit; per-flavor measurements replace it (§4.5)
freezable = false                 # only for non-campaign lanes; campaigns use the invalidation rule (§3.7)
# efficient upper bound of units per instance = Assay judge.mutation.jobs_max (native R2)
# or workers_max (xdist, RESOURCE-PROFILES §6.2); not duplicated here
```

- **Taken out of the lane's and operator's hands:**
  - fixed `jobs = N` for campaigns;
  - the runner's `--instances/--shard-total/--shard-offset`;
  - `R2_INSTANCE_MEM_MIB`, `R2_WORKER_MEM_MIB`, `R2_OVERCOMMIT`,
    `R2_HOST_RESERVE_MIB` (NC-48).
- **`jobs_max` now means the efficient upper bound:** beyond it, one instance
  gains nothing (a shared browser, one database, GIL-bound collection). A
  badly scaling lane states a small bound, and the scheduler adds instances
  instead. That is the answer to "pwmcp often still only uses a single CPU".

### 4.2 Allocator math (in fleetd; `plan` exposes it read-only)

For each eligible host h (enabled, readiness PASS for the lane's needs, floors
met, image digest equal to the request's, memory/IO PSI some ≤ 25, class
allows the lane):

```text
C_h = cpu.for_lanes × oversubscribe_max (default (cores+1)/cores) − Σ leased cpu
M_h = memory.for_lanes × 1.5 − max(Σ leased expected peaks, gates.current − parked)
f   = flavor.id of host h; every lane fit below is the fit FOR f (§4.5)
```

For lane L, with O = instance memory, U = unit memory, c = unit CPU, J = the
efficient bound and E(j) the measured efficiency (default 1 for j ≤ J):

```text
cpu(j) = j × c_f    mem(j) = O_f + j × U_f    rate(j) = j × E_f(j) / unit_seconds_f
   (no fit for flavor f yet → declared demand for c/O/U, E = 1, unit_seconds = declared,
    and the host first runs ONE calibration shard; the ETA for f is marked "uncalibrated")
per host: over j = J..1 with E(j) ≥ 0.8:
   k(j) = min(floor(C_h / cpu(j)), floor(M_h / mem(j)), max_lane_containers − running)
   choose the j maximising k(j) × rate(j); ties → larger j (fewer baselines, less O)
   leftover CPU ≥ c with memory → one extra instance of j' = floor(leftover / c)
shards N = Σ_h k_h × 2, each shard ≥ 40 units
ETA = units_remaining / Σ rate  + one shard's tail
```

**Worked example.** A 4-core host with 6 GiB for lanes and factor 1.5
(M = 9 GiB), oversubscribe 1.25 (C = 5). The lane fit is O = 190 MiB and
U = 80 MiB (3 workers → 430 MiB), with c = 0.35, J = 3 and E(3) = 0.95.

- j = 3 → cpu 1.05, mem 430 MiB;
- k = min(4, 21) = **4 instances x 3 workers**;
- 1.7 GiB expected, about 4.2 busy cores.
- The old guard refused 2 instances. If E(3) were 0.5, j = 1 wins, bounded by
  CPU and `max_lane_containers`.

### 4.3 Overcommit (operator: factor 1.5, monitor PSI)

- **Expected peaks** are summed against `memory.for_lanes × 1.5`. The factor is
  host policy with default 1.5, and the facts carry it so a host can state
  another.
- **Container caps** are runaway bounds. Their sum is unconstrained beyond the
  slice `MemoryMax`.
- **Safety net, in order:**
  1. Assay's in-instance `--memory-budget-bytes` admission;
  2. the per-container cap (a runaway candidate is `crashed` inside its
     instance);
  3. the guard's shedding (§3.6/§3.7);
  4. mdt-psi-guard on the tiers;
  5. the slice `MemoryMax`.
- fleetd also stops dispatching to a host whose guard reports an action in the
  last 10 min, or whose memory PSI some avg10 > 25.
- **KSM is a policy item, not a savings assumption.**
  - KSM merges only pages of processes that opt in (`MADV_MERGEABLE`,
    `PR_SET_MEMORY_MERGE`).
  - The estate provides the opt-in: ciu's shim (`ciu/src/ciu/ksm.py`,
    `governance.ksm_optin`), and Run-Gate lane containers are to get it by
    default (job ksm-default2).
  - The host keeps ksmd running with its CPU bounded (advisor, about 5-10% of
    one core).
  - The facts report all of this under `ksm` (§3.2). fleetd shows the
    counters but adds no savings to `M_h`.
  - Measured sharing (`pages_sharing`, `general_profit`) can only make the
    **measured** per-flavor peaks smaller (§4.5), which the allocator then
    uses as they are. The 1.5 factor stays host policy. It rests on swap +
    zswap and the cold share of lane memory, not on KSM.

### 4.4 What "3 x 1 ≈ 1 x 3 in RAM" needs

- RAM(k instances x j) = k × O + k × j × U.
- They are equal only when O ≪ U. With the fit above, 3 x 1 = 810 MiB
  against 1 x 3 = 430 MiB.
- Each instance pays its own judge and container. Today it also pays its own
  baseline on every attempt; §3.10 removes that.
- The scheduler therefore measures O and prefers fewer, wider instances while
  E(j) holds.
- KSM sharing between instances of one lane, once opted in, lowers the
  measured O and U; it is never assumed.

### 4.5 Learning and refinement, per hardware flavor

- **Hosts of different flavors are never compared** (operator: runs on the two
  test hosts are different hardware and cannot be compared).
- **Every fit is keyed by (lane, `flavor.id`).** Samples from one flavor never
  enter another flavor's fit. No benchmark-score conversion is used for
  placement: a single score cannot capture memory bandwidth, cache, disk or
  steal time.
- **A flavor with no fit for a lane** uses the declared demand with
  E(j) = 1. Its first dispatch is **one calibration shard**, followed by the
  rest once that shard has produced a fit. Its ETA is shown as
  "uncalibrated".
- **The fits**, from clean samples (RESOURCE-PROFILES §6.1) that fleetd
  collects from every host after each attempt (history + progress files):
  - U_f = p95 candidate RSS;
  - O_f = intercept of instance peak P(j) = O + j·U over at least two
    distinct j (until then, the declared `instance_memory`);
  - c_f = p50 `cpu_seconds/wall`, floor 0.25;
  - E_f(j) from candidates per minute at j against j = 1;
  - `unit_seconds_f` = p50 elapsed.
- **Where the fits go.** Per-flavor fits are measured state: they live in
  fleetd's state, next to the facts, outside the repository.
  - The repository keeps only the lane's declared demand (§4.1) and Run-Gate's
    footprint manifest.
  - AGENTS.md's host-independent "rigor evidence" summary (normalised by a
    published reference score) remains a **reporting** artefact for releases.
    Placement never uses it.
- **Predicted against actual.** Each tick records predicted against actual
  rate per instance and flavor, and `doctor` warns on drift.

## 5. Migration and work packages

### 5.1 Compatibility and the two live campaigns

- **The two live campaigns** (PWMCP on host A, ciu on host B) **finish under
  the legacy script**:
  - runner scripts are not replaced on a host while its campaign is active;
  - the new supervisor uses the same lock and `.active` paths, so the old and
    new supervisors refuse each other's campaign base;
  - fleetd's first tick reports legacy campaigns as `unmanaged`, read-only,
    from their campaign directories, and treats their measured footprint as
    occupied capacity.
- **`run-r2-campaign.sh` becomes a wrapper** for one release:
  - its single-campaign form calls `run-gate campaign supervise` with the same
    arguments;
  - its fleet options print the equivalent `run-gate campaign submit`;
  - after that it is deprecated.
- **`remote-gate.py` and `host-sat-queue.sh`** keep running. They submit their
  lanes as `light` requests, or at least take a lease, so they never overlap
  fleet work unseen (closes LR-KI-10). They are retired once fleetd's light
  class covers them.
- **What moves:**
  - supervision → Run-Gate (`supervise`);
  - allocation, host table, leases and tick → fleetd;
  - the private host table's reach facts → fleetd's enrollment (keys move
    into fleetd's state dir);
  - generic runner scripts → a provider-neutral home after WP6 (NC-49).

### 5.2 Work packages (dependency order)

| WP | Scope | Owner | Tests | Risk | Depends |
|---|---|---|---|---|---|
| **WP1** | Host facts + guard, read-only: (a) the `host-facts/1` collector, one composite runner-side script (hardware `flavor` + `flavor.id`, host-setup env, cgprofile `host` verb, `/proc`, KSM policy and counters incl. the advisor limit and lane opt-in shim, zswap, swap areas, kernel interfaces, tools/images, doctor result); (b) cgprofile `guard` in **report** mode (signals, session ranks, the action it would take) and `guard status --json` | fleetd project (collector), cgprofile (guard) | fixtures for `/proc`, `/sys`, env file and cgprofile replies; a `null`+reason for each unreadable leaf; guard decision table on recorded PSI traces | low | — |
| **WP2** | fleetd skeleton: daemon, `state.json`, observe/control sockets with group levels, `fleetd install` (unit, groups, endpoint fragment), `fleetd enroll`, tick probing hosts, verbs `hosts/facts/state/events` | new project `fleetd` | socket permission tests (group gating, separate levels); state round trip; fake transport; restart rebuild from fake host lease records | low-medium | WP1(a) |
| **WP3** | `[lanes.*.demand]` in Run-Gate + `run-gate campaign plan <lane>` client; fleetd `plan` (allocator §4.2, pure) prints the arithmetic, the ETA and the equivalent legacy runner commands per host | run-gate + fleetd | table cases; properties (never above C/M, never above J, monotonic in capacity); hand mutants of each min() term | low | WP2 |
| **WP4** | `run-gate campaign supervise` (Python port of the host supervisor) with lease-record renewal and fencing; `run-r2-campaign.sh` becomes a wrapper | run-gate (+ netcup wrapper) | parity cases ported from `scripts/netcup/tests/test_run_r2_campaign.py`; lock interop with the legacy script; one short-budget host rehearsal | medium | — |
| **WP5** | fleetd leases + `submit/cancel/fetch`, dispatch, store sync-back, merge placement, carry-over distribution, T3 ledger rows, priority classes; remote-gate/host-sat queue submit as `light` | fleetd (+ run-gate client, netcup) | fake-transport campaign end to end; a crash at each tick step is idempotent on re-run | medium | WP2-WP4 |
| **WP6** | host loss + controller restart: grace, re-placement from synced stores, generation fencing, full rebuild from host lease records | fleetd | chaos with the fake transport (partition, return, double run, state file lost) | medium | WP5 |
| **WP7** | guard **enforce**, park/thaw: freeze + `memory.reclaim` of the instance that no longer fits; `parked` leases; re-evaluation on events (instance finished, RAM need changed, policy changed, room elsewhere → move); the invalidation rule (cgprofile `frozen_seconds` in windows and summary; Assay discards overlapped candidates); deadline handling (Run-Gate budget minus frozen time, cgprofile liveness paused, Assay campaign deadline extended); stop + resume fallback; `--shed` options | cgprofile, assay, run-gate, fleetd | PSI trace replays; a frozen candidate is re-queued, never `killed`/`hung`; `--on-stall kill` never fires on a parked session; thaw ordering and event triggers; a host rehearsal under synthetic pressure | medium-high | WP1(b), WP5 |
| **WP8** | measurement per hardware flavor: profiled host runs, history collection, (lane, flavor) fits of O/U/c/E/unit_seconds in fleetd state, calibration shard for a new flavor, prediction-error record; KSM counters reported per flavor once the default opt-in shim lands | run-gate, fleetd | fit math on recorded progress files; a flavor-isolation test (samples of flavor A never change flavor B's fit) | low | WP4 |
| **WP9** | controller-host small-lane pool (§3.8): class, cap, gate-slot interlock, admission checks | fleetd (+ controller gate wrapper) | admission table; slot interlock with two concurrent gate runs | medium | WP5 |
| **WP10** | ctlstat reads `fleetd observe state --json` for §G/H/I; drops its own per-host SSH probe | ctlstat | golden outputs from a recorded fleet state | low | WP2 |
| **WP11** | ciu v8: `ciu gate --campaign` client (port of the Run-Gate client); optional enrollment bootstrap via v8.2 | ciu8 | WP3/WP5 client tests as parity oracles | medium | ciu8 checkpoints D and G |
| **WP12** | move the generic runner scripts out of `scripts/netcup/runner/`; netcup keeps install only (NC-49) | netcup | path/doc contract tests | low | WP5 |
| **WP13** | preparation once per (commit, judge, image), overlapped (§3.10): `assay prepare --emit` / `run --prepared`, held outcomes, environment check, one-ahead workspace staging; Run-Gate pass-through and phase 0; fleetd prepare-first scheduling and artifact distribution | assay, run-gate, fleetd | a mismatched artifact key is refused by name; held outcomes are discarded when the baseline fails; a failed environment check on one host discards only that host's held outcomes; baseline count per campaign = 1 + flavors without a full baseline | medium | WP5 (fleetd parts); the Assay part can start at once |

**First stage, shippable in a week:** WP1(a) + WP2 (read-only fleet view over
the sockets) + WP3 (`campaign plan`).

- Together they give the operator and ctlstat a central view and the placement
  answer, with commands that today's supervisor can run.
- No process on a compute host changes in this stage.
- WP1(b) (guard report mode) and WP4 run in parallel.

## 6. Failure modes and security

| Failure | Behaviour |
|---|---|
| Controller host restart / fleetd crash | Compute hosts keep running; supervisors renew lease records locally. fleetd reloads `state.json` and reconciles with the host records: adopt live leases, release expired ones, re-dispatch shards with no record. |
| `state.json` lost | Campaigns are rebuilt from host lease records + collected results. Queued-but-never-dispatched requests are lost and raise ledger "missing" attention. |
| Host unreachable | Its leases count as in use. After 30 min its shards are `lost` and re-placed from the last synced store (at most 15 min of work lost per shard). A parked (frozen) instance on that host is lost with it. |
| Host returns after a partition | Revoked generation → its supervisors stop. Duplicate execution only wastes CPU (results are judge-deterministic), and only the current generation's store is merged. |
| Supervisor crash | The lease expires (TTL 15 min). The next tick restarts the next `-s<N>` on the same host from the persistent checkout. |
| Freeze changes a result | Prevented by the invalidation rule and the deadline table (§3.7). Until WP7 lands the guard does not freeze (stop + resume only). |
| Parked forever | Re-evaluation on events (§3.7). A park longer than `max_park_seconds` (default 2 h) becomes stop + resume, possibly on another host. |
| Thaw swap storm | Thaw one at a time, only with free RAM ≥ the expected peak; the guard re-parks when the signal returns. |
| Wrong rate from another flavor | Impossible by construction: fits are keyed by `flavor.id`, and an uncalibrated flavor starts with one calibration shard. |
| Bad shared preparation artifact | Strict key validation (commit, judge, image). Held outcomes are committed only after the baseline and the host environment check pass, and are discarded otherwise. |
| Judge mismatch between hosts | Placement requires equal image digest and Run-Gate/Assay versions; the strict merge refuses a foreign judge anyway. |
| Runaway candidate (OOM) | The per-container cap ends it inside the instance (`crashed`). The guard and the slice protect the others. |
| Two guards fighting | The cgprofile guard writes lane leaves only; mdt-psi-guard writes tiers only. |
| Hash imbalance on small sets | N = 2 x instances and ≥ 40 units per shard. `--only-candidates` batches later. |
| Lease leak | TTL expiry; ctlstat attention for a lease without a live supervisor. |
| Disk full (LR-KI-12) | Facts carry `checkout_disk_free`, and a host below the threshold gets no new work. |
| Controller host overload from small lanes | §3.8 cap: 1 lane, ≤ 2 GiB, PSI ≤ 10, anon hold line, gate-slot interlock, stop-only shedding. |

**Security:**

- **No authentication in the service, by decision.** Access is the socket
  group: `fleet-observe` for reading, `fleet-control` for writing. Granting
  `fleet-control` is the authorisation step, done by the operator through
  devcontainer `--group-add`.
  - A `fleet-control` member can make any handed-over host run any committed
    lane of any submitted bundle. Treat it like the docker group on a test
    host.
  - Neither GID may equal the Docker GID.
- **Credentials:**
  - SSH private keys and pinned host keys live only in
    `/var/lib/fleetd/ssh/` (owner `fleetd`, 0600) on the controller host;
  - no socket verb returns them;
  - none are in the repository;
  - the identity command and target addresses live in fleetd's state, outside
    the repository.
- **Least privilege on hosts.**
  - fleetd logs in as `runner`, never root. It writes `checkouts/`,
    `campaigns/`, `jobs/` and `fleet/`.
  - Host policy and cgprofile's guard config are root-owned and read-only to
    it.
  - Known gap: `runner` is in the `docker` group, which is root-equivalent.
    That is acceptable on dedicated test hosts. A `HOST_ROLE=shared-prod` host
    is never enrolled as class `compute`.
- **Leases expire.** The local supervisor renews them, and expiry is computed
  on the host's clock. Revocation bumps the generation.
- **No inbound listener on compute hosts and no origin access.** Bundles are
  addressed by commit, and dirty checkouts are refused.

## 7. Operator decisions and open questions

### 7.1 Decisions taken by the operator (inputs, not reopened)

1. Only host installation is provider-specific. After install, hosts are
   reached over SSH or ciu, and any compute host must work.
2. **Option 3:** one central scheduler/distribution service on the controller
   host owns the hosts, their resources, state and allocation. It is
   handed control of each host after installation. Running happens through
   Run-Gate.
3. No authentication in the service. Access goes through the existing socket
   design only, read and write separate, by group membership.
4. Host config, capabilities and host-wide policy are state, kept outside the
   repository by that one service (policy itself stays on each host).
5. A lane states an efficient upper bound, its resource need and its wall time
   relative to host performance. It never states workers per instance.
6. RAM overcommit factor **1.5** for expected peaks, with PSI monitored. KSM
   should be in place. Saturate all CPUs. 3 x 1 should cost about the same as
   1 x 3 (checked in §4.4).
7. The controller host runs **small lanes only**.
8. A small guard on each compute host, in a protected cgroup, freezes other
   load to protect the host. The scheduler runs the tick loop, never agent
   sessions.
9. **Shedding = freeze + `memory.reclaim`.** Park the instance that no longer
   fits until something else finishes or the RAM requirements change.
10. **Hosts of different hardware flavors are not comparable.** Performance
    numbers are per flavor and never pooled.
11. **The preparation phase runs in parallel with the execution phase.**
12. **KSM:** ciu's opt-in shim is in place, and Run-Gate lane containers get
    it by default. ksmd is tuned to about 5-10% of one core per host. KSM is
    host policy, not a savings assumption.
13. Standing rules: CPU overcommit by design; only memory/IO PSI > 25 or heavy
    swap stop new work; tests are load-independent; limits are host-owned.

### 7.2 Remaining open questions (recommended default first)

1. **Guard home:** extend cgprofile (recommended, §3.6), fold mdt-psi-guard
   into it later, or write a separate tiny daemon? *Default: extend cgprofile;
   mdt-psi-guard stays the tier backstop.*
2. **Parking limits:** discard and re-execute a parked instance's in-flight
   candidates (the invalidation rule), and convert a park longer than 2 h into
   stop + resume elsewhere? *Default: yes to both. Until WP7, the guard sheds
   by stop + resume only.*
3. **Service name and home:** a new cmru project `fleetd/` in vbpub with its
   own release? *Default: yes.* The name is a working name.
4. **Priority across projects:** classes `release-t3` > `campaign` > `light`,
   FIFO within a class, no per-project quota? *Default: yes.* Add quotas only
   when a project starves.
5. **Credential bootstrap:** fleetd-generated key installed by runner
   provisioning now, ciu v8.2 enrollment later? *Default: yes.*
6. **Shards and host loss:** N = 2 x initial instances with ≥ 40 units per
   shard, store sync every 15 min, and a host counts as lost after 30 min
   unreachable? *Default: yes.*
7. **Preparation proof per host:** for an instance reusing another host's
   baseline artifact, is the short environment check (collection + the
   shortest test per file, ≤ 10% of the baseline time) enough, with a full
   baseline once per new flavor? *Default: yes (§3.10).*
8. **The controller small-lane cap:** 1 lane, ≤ 2 GiB, PSI some ≤ 10, the
   5.0 GB anon hold line, a shared gate slot (§3.8)? *Default: as proposed.*

## 8. Verification boundary

- This is a design. Nothing was run on a host, and no code was changed.
- File references were read on origin/main `4f1ae7d70`, plus the in-flight
  branches `r2-guard-20261112` (NC-48) and `estate-req-20261112`.
- Measured numbers are quoted from the 2026-11-11/12 host reports.
- The kernel interfaces in §3.7 are cgroup v2 features (`cgroup.freeze` since
  5.2, `memory.reclaim` since 5.19, zswap writeback control since 6.8). Their
  presence on each host is a `host-facts/1` field that WP1 verifies, not
  assumed.
- O, U and E(j) in the worked example come from one observation. KSM benefit
  is unmeasured.
- The ciu KSM shim (`ciu/src/ciu/ksm.py`) was read. The Run-Gate default
  (job ksm-default2) and the ksmd advisor tuning are in-flight work reported
  by the coordinator, not verified here.
- The preparation-phase costs in §3.10 (one baseline per attempt) follow the
  2026-11-11 flex-R2 record. The overlap gain is not measured yet.
