# Design: compute hosts and distributed lane campaigns (2026-11)

Status: DESIGN, decision record, **rev 4**, proposed, not implemented.
Backlog: run-gate RG-275, netcup NC-52. Amends
`run-gate-project/docs/DESIGN-lane-rework-2026-11.md` §3.3 (see §3.0). No host
names, addresses, controller paths or credentials appear in this file; hosts
are "host A/B".

**Revision history:**

- **rev 1:** Run-Gate allocator.
- **rev 2:** operator interview 1, giving a central service with sockets,
  overcommit 1.5, parking, the guard, and KSM as policy.
- **rev 3:** the independent review, adding key custody, a lease protocol, a
  deterministic preparation overlap, and the guard ordering.
- **rev 4:** operator interview 3:
  - **Performance:** a lane declares RAM and wall time at a normalised
    reference performance. Hosts carry Geekbench scores, and fleetd
    extrapolates with one learned correction factor **per host**. The
    lane × flavor fits are removed.
  - **RAM is the key placement variable.** Per-candidate RAM from Assay
    history drives **per-host candidate lists** that mix low- and high-RAM
    candidates, route big ones to big hosts, and retry memory crashes on the
    biggest host.
  - **Dispatch:** named jobs + a progress-based stall deadline replace leases,
    generations, renewals and epochs. A double run is harmless. fleetd
    exposes progress and ETA.
  - **Guard:** memory PSI `full` only. It freezes + reclaims the lane with the
    least progress per RAM, and its rules live in `/etc/cgprofile/guard.toml`
    with a drop-in directory. The control plane is protected with
    `memory.min` and a high `cpu.weight`.

## 0. Decisions in one screen

| # | Decision | Section |
|---|---|---|
| D1 | One **central scheduler/distribution service** (working name `fleetd`) on the controller host owns host state, running jobs, allocation and the tick. It runs as its own user without Docker access. | §3.2 |
| D2 | Clients use **only Unix sockets**: observe (read) and control (write), gated by group membership, with no authentication inside the service. | §3.3 |
| D3 | **Run-Gate is the lane layer:** its client turns a lane into a work request, and on hosts it executes jobs. ciu projects ask through Run-Gate (later `ciu gate`) and never own hosts. | §3.4 |
| D4 | Enrollment and keys come from the implemented **`ciu host enroll`**, run as the fleetd user. Provider credentials stay outside fleetd. | §3.5, §6.1 |
| D5 | A lane declares **RAM per unit and wall time at the reference CPU speed**, plus its efficient upper bound. It never declares workers or a hardware flavor. | §4.1 |
| D6 | **Wall time is extrapolated** from Geekbench single-core scores × a learned factor per host. It matters less than RAM. | §4.2 |
| D7 | **RAM drives placement:** per-candidate RAM, per-host candidate lists, low/high mix, routing of big candidates to big hosts, and memory-crash retry on the biggest host. Expected charges are summed against `RAM for lanes × 1.5`; PSI is the safety net. | §4.3, §4.4 |
| D8 | **Named jobs + a progress-based deadline:** idempotent job names, re-adoption after a restart, a lost-host timeout, harmless duplicates (the merge manifest picks one record), and stall detection instead of a tight wall budget. No leases. | §3.9 |
| D9 | The **guard** acts on memory PSI `full` only: it freezes + reclaims the lane with the least progress per RAM and thaws when pressure stays low. Its rules live in a host-owned `/etc` file. The **control plane** is protected by `memory.min` and `cpu.weight`. | §3.6, §3.7 |
| D10 | **Preparation** overlaps plan, workspace setup and staging. Candidates start after the host-local baseline, which is measured once per host per campaign. | §3.10 |
| D11 | **The controller host runs no fleet work in the MVP.** A locked-down small-lane runner comes later, on approval (WP9). | §3.8 |

## 1. Problem and non-goals

### 1.1 Problem

- Long lane work (native Assay R2 campaigns of 300 to 1,500 candidates, 3 to 10
  hours; later fuzz/variation) must **saturate every CPU of every compute
  host**, for several projects.
- Today `scripts/netcup/runner/run-r2-campaign.sh` (1,069 lines of bash)
  supervises one host:
  - cross-host splits and store copies are manual
    (`scripts/netcup/runner/README.md:168-181`);
  - the memory guard charges each instance the whole lane cap
    (`run-r2-campaign.sh:568-594`). A PWMCP lane capped at 3 GiB ran 3 workers
    at about 430 MiB and about 1 CPU, two instances were refused, and CPUs
    idled;
  - NC-48 patches the arithmetic through host environment variables.
- **Provider coupling.** Host installation is provider-specific:
  `install-host.py`, the SCP client, debian-install-v2, `pre-reinstall*.py`.
  At runtime, two more pieces are coupled to the provider:
  - `api_check`, a provider API identity check required by
    `scripts/netcup/runner/remote-gate.py:196-198` and also called by
    `host-sat-queue.sh:156,288`;
  - the hard `ALLOWED_HOSTS` (`remote-gate.py:30`).

  Everything else is generic SSH + Docker + Run-Gate code. This design moves
  the provider check to provisioning and replaces the allowlist with
  enrollment (§3.5). NC-52 moves supervision out of `scripts/netcup`.

### 1.2 Non-goals

- No change to verdicts or judge identity. Assay's store format gains only the
  additions listed in §4.5.
- No authentication or accounts in the service, no provider code or
  credentials in it, no cluster manager.
- No automatic change to host-wide policy beyond what the host owns in its own
  `/etc` files (§3.6).
- No tight wall-clock budgets for campaign jobs: progress decides (§3.9).
- No fleet execution on the controller host in the MVP.

## 2. Landscape

### 2.1 What exists (reuse, do not rebuild)

| Concern | Existing component | File refs |
|---|---|---|
| Host table, provider check, on-host guard | controller-private JSON used by `remote-gate.py` / host-sat queue | `scripts/netcup/runner/remote-gate-hosts.example.json`, `scripts/netcup/runner/remote-gate.py:190-237` |
| **Host enrollment** | `ciu host enroll` (**implemented on v7**, CIU-93): per-row key pair on the controller, TOFU fingerprint, opt-in `from=`, pending generations + `--revoke-old` | `ciu/docs/CIU-HOST-ENROLLMENT-PROPOSAL.md:3-4, 46-62, 121` |
| One-shot remote lane, serial queue | `remote-gate run/status/collect`, `host-sat-queue.sh` | `scripts/netcup/runner/README.md:772-838` |
| Campaign supervision | `run-r2-campaign.sh` (attempt loop on exit 4, progress-based stall counter, exact-name cleanup, stop/status) | `scripts/netcup/runner/README.md:85-216` |
| Code transport | `run-job` (named detached jobs, refuses a duplicate name), `sync-from-devcontainer` (git bundle) | `scripts/netcup/runner/README.md:68-83` |
| Candidate selection | Assay `--only-candidates/--exclude-candidates` (B531), `plan --list-pending`, `--merge-state` (same-judge copy), `--carry-over`; Run-Gate passes them through (RG-265) | `assay/src/assay/cli.py:415-526`; `run-gate-project/CONSUMERS.md:770-856` |
| Hash shards + strict merge | `--shard K/N`, `--merge-shards N`, `shard.manifest` | `assay/src/assay/mutation.py:1788-1793, 3497-3530` |
| In-instance RAM mixing | Assay `--memory-budget-bytes` admission; at most one high-RSS candidate at a time; two-ended matching (longest first, RSS mix) | `assay/README.md:393-418`; `docs/design/RESOURCE-PROFILES.md` §5 |
| Candidate predictions | previous profile of the same candidate id → file median → operator median | `docs/design/RESOURCE-PROFILES.md` §5.6 |
| Per-candidate budget | `max(3 × baseline, baseline + 60 s)` from the host's own baseline | `assay/src/assay/mutation.py:4685` |
| Socket design | cgprofile levels: `root:root 0755` dirs, group-owned socket, env-only endpoints, distinct GIDs | `cgprofile/docs/DESIGN-GUIDE.md:855-885`; `run-gate-project/docs/DESIGN-GUIDE.md:380-430` |
| Per-lane sessions | cgprofile sessions, `--progress-stream` (the daemon reads the lane's progress NDJSON), explicit-policy `cgroup.kill` | `cgprofile/docs/RG55-INTERFACE-CONTRACT.md:549-660` |
| CPU speed facts | cgprofile `machine/1.speed`: Geekbench `single_core`, `multi_core`, `geekbench_major`, `baseline_score`, read from `/var/lib/mdt/host-facts/cpu-benchmark.json`; majors never mixed | `cgprofile/docs/PROTOCOL.md:600-625` |
| Tier PSI responder | `mdt-psi-guard` (`report` by default): protected-unit memory `some` > 10%, host memory `full` > 5%; acts on `dev-gates`/`dev-background` (the lane parents) | `mdt-cockpit/host-setup/README.md:199-228` |
| Host policy, protected units | `/etc/mdt/host-setup.env`, slices, protected units with `OOMScoreAdjust` (code list `mdt-cockpit/host-setup/mdt_capacity.py:360`) | `mdt-cockpit/host-setup/README.md:27-157, 1144` |
| KSM opt-in | `ciu/src/ciu/ksm.py` (`governance.ksm_optin`); Run-Gate default in progress (job ksm-default2) | |
| Estate rules | PSI > 25 or heavy swap stops new work; about one job more than the cores | `AGENTS.md:196-226` |
| Estate status | ctlstat §G/H/I | `docs/design/ctlstat/REQUIREMENTS.md` |

### 2.2 What Assay already measures, and what is missing

| Data | Where | Notes |
|---|---|---|
| Per-candidate `elapsed_seconds`, `phase_seconds`, `startup_seconds` | `candidate` progress events and state records | always present |
| Per-candidate `profile` (`resource-profile/1`) | progress events and state records (Assay 12; the flat keys were removed, B206, `docs/design/RESOURCE-PROFILES.md:265`) | **only with the cgprofile observer on** |
| Per-candidate RAM | `profile.memory_peak_bytes` (cgroup charge) in exclusive-cgroup mode; otherwise `rss_peak_bytes`, **a lower bound** that excludes page cache and tmpfs | the workspace's tmpfs/CoW size must be added when RSS is the source (§4.4) |
| Candidate ids, pending set | `assay plan`, `plan --list-pending` | exact; ids change when the mutated source changes |
| Per-lane history, footprint | `.run-gate/history.json`; `run-gate.footprint.json` (only run-gate-project) | per checkout |

**Missing:**

1. Profiles on the hosts, which run with `RUN_GATE_PROFILE=off` (LR-KI-11).
2. Collection of state records back to a central place.
3. A per-host speed factor.
4. Per-host candidate lists and a strict merge of list-based stores (§4.5).
5. A host-local baseline cache (§3.10).

## 3. Architecture and ownership

### 3.0 Relation to earlier decisions

- **Amends `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` §3.3:** the
  central service replaces per-host claim loops. Single-host lane execution and
  the T3 ledger format are kept.
- **Departs from mdt-psi-guard's "never freezes"**, under the invalidation
  rule (§3.7).
- **Needs an amendment of D-651 Q4 / ciu `SPEC-V8.md` S21.4.1** ("the
  cgroup-profiler daemon … decides nothing") for the guard (§7.2 Q1).

### 3.1 Picture

```text
 controller host (systemd)                                  clients
 ┌──────────────────────────────────────────────┐ observe ┌────────────────────────────────┐
 │ fleetd (user `fleetd`, no docker group)      │◄────────│ run-gate campaign plan/status  │
 │  state.json (0600), results/ (0700)          │ control │ ctlstat (progress, ETA, hosts) │
 │  inventory + keys: ciu host enroll as fleetd │◄────────│ run-gate campaign submit/stop  │
 │  tick 30 s: probe, adopt, dispatch batches,  │         └────────────────────────────────┘
 │   stall check, collect, re-plan, merge       │
 └───────────────┬──────────────────────────────┘
                 │ SSH as `runner` (restrict, from=, forced command)
   ┌─────────────┴───────────┐
   ▼ host A                  ▼ host B (any enrolled)
 ┌──────────────────────────────────────────────────────────────────────┐
 │ fleet-host-entry: probe | import | start-job | stop-job | fetch      │
 │ run-job <campaign>.<host>.<slot>.b<n> → run-gate campaign supervise   │
 │   → run-gate <lane> --only-candidates <batch> --merge-state <slot store>
 │ ctl.slice (memory.min, cpu.weight 10000): sshd, docker, containerd,   │
 │   cockpit, cgprofiled(+guard), mdt-psi-guard, runner's user slice     │
 │ dev-gates.slice (cpu.weight 100): lane containers                     │
 └──────────────────────────────────────────────────────────────────────┘
```

### 3.2 The central service (`fleetd`, working name)

**Why:** several projects want work run on the same hosts. Host ownership must
not sit in a project, a devcontainer or an agent session (operator: option 3).

| It owns (state, outside the repository) | It does not own |
|---|---|
| Inventory view (reach facts from fleetd's own ciu rows) + host state (`enabled`/`draining`/`identity-changed`) | key creation and rotation (`ciu host enroll`) |
| Host facts cache incl. Geekbench scores and the **learned per-host factor** | host-wide policy (stays on each host, §3.6) |
| Campaigns, per-host candidate lists, batches, named jobs, the candidate table (RAM/time per candidate id), results, ledger rows | lane definitions (projects' `run-gate.toml`), verdicts, Assay stores |

**Smallness:**

- one stdlib daemon running as user `fleetd`;
- one `flock` on `/var/lib/fleetd/lock`, so a second instance refuses to
  start;
- `state.json` (atomic replace + fsync), a bounded `events.jsonl` and
  `results/`;
- two socket-activated sockets;
- a 30 s tick: one composite probe per host, re-adopt, dispatch, stall check,
  collect finished batches, re-plan, merge, ledger.

**Host facts (`host-facts/1`).** Each leaf may be `null` with a `*_reason`.

```json
{"schema": "host-facts/1", "boot_id": "…", "host_now_boottime_s": 123456.7,
 "cpu": {"model": "…", "online": 4, "for_lanes": 4.0, "oversubscribe_max": 1.25},
 "speed": {"geekbench_major": 6, "geekbench_version": "6.x.y", "baseline_score": 2500,
           "single_core": 2100, "multi_core": 7800, "basis": "geekbench6-measured"},
 "memory": {"total_bytes": …, "for_lanes_bytes": …, "swap_for_parking_bytes": …, "overcommit": 1.5},
 "ksm": {"run": 1, "advisor_mode": "…", "advisor_max_cpu": 8, "pages_sharing": …, "lane_optin_shim": true},
 "kernel_interfaces": {"cgroup_freeze": true, "memory_reclaim": true, "memory_reclaim_swappiness_arg": true},
 "control_plane": {"slice": "ctl.slice", "memory_min_bytes": …, "cpu_weight": 10000},
 "guard": {"enabled": true, "config_digest": "…", "state": "idle|parked:1"},
 "policy": {"host_role": "dedicated-dev", "psi_refuse_some_avg10": 25, "max_lane_containers": 8},
 "jobs": [{"name": "c42.hostA.s1.b7", "running": true, "exit": null, "progress_mtime_s": …,
           "candidates_done": 31, "candidates_total": 40}],
 "tools": {"run_gate": ["25.5.0"], "images": {"tester-unified:local": "sha256:…"}}}
```

The learned per-host factor is fleetd state (§4.2), not a host fact.

### 3.3 Sockets

| Level | Socket | Group | Verbs |
|---|---|---|---|
| observe | `/run/fleetd/observe/fleetd.sock` | `fleet-observe` | `version`, `hosts`, `facts`, `state`, `campaigns`, `campaign <id>` (progress, per-host lists, ETA), `plan <request>`, `fetch <campaign>`, `events --since` |
| control | `/run/fleetd/control/fleetd.sock` | `fleet-control` | `submit`, `cancel`, `drain/undrain`, `priority` |

**As in cgprofile:**

- `root:root 0755` level directories, a group-owned socket inode;
- GIDs non-zero, distinct from each other and from the Docker GID;
- endpoints published as `/etc/host-endpoints.d/fleetd.env`.

**Deliberate deviations:**

- **No peer-uid authorization.** Any `fleet-control` member can cancel any
  campaign. The peer uid/gid/pid is recorded for audit.
- **The unit needs outbound SSH:** `RestrictAddressFamilies=AF_UNIX AF_INET
  AF_INET6`, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/fleetd`,
  `ProtectHome=yes`, `NoNewPrivileges=yes`, an empty capability set.
- **Socket activation.**
- **`SCM_RIGHTS` bundles:** regular files only, at most 2 GiB,
  `git bundle verify`.

Client delivery depends on **MDT-086**, the gap in the standard devcontainer
template that also blocks cgprofile's variables.

### 3.4 Who decides what, and ciu v8

| Step | Owner |
|---|---|
| Lane → `work-request/1` (lane, commit, image digest, environment mode, floors, demand §4.1, plan + pending list, carry-over source + reason) | **Run-Gate client** |
| Candidate lists per host, batches, order across projects | **fleetd** |
| Execute a batch, progress stream, stall-tolerant attempt loop, merge run | **Run-Gate on the host** (`supervise`) |
| Judge, timeouts from the host-local baseline, in-instance RAM mixing | **Assay** |
| Park/thaw under memory pressure | **guard** |

**ciu v8:**

- `ciu gate <lane> --campaign submit` is the ported client (D-647 #1).
- Demand lives at `[testing.lanes.<l>.demand]`, and measured values in
  `ciu.footprint.json`.
- v8.1's warm-working-set charge (S21.5.1) can replace "expected peak × 1.5"
  once measured.
- `ciu lease` (S14.4.7-8) is a local lock key, unrelated to fleetd.
- Every local S21 ticket still applies to every lane start on a host.

### 3.5 Enrollment and transport

- **Enrollment:** `sudo -u fleetd ciu host enroll <alias> --global …`. The key
  lands in fleetd's XDG state and the row in fleetd's `hosts.toml`. fleetd has
  no enroll verb.
- **Provider identity:** the provider API check runs at provisioning time
  only, by the operator. After enrollment, the pinned host key plus the on-host
  guard command (hostname + `/etc/vbpub/server-id`) prove identity. fleetd
  holds no provider credentials.
- **Which hosts are contacted:** only enrolled ones, and a host whose facts
  show `HOST_ROLE=shared-prod` is refused. This replaces `ALLOWED_HOSTS`.
- **Transport:** SSH as `runner` to a forced-command entry (§6.1); bundles
  through `sync-from-devcontainer --no-current-link`; jobs through `run-job`.
  v8.2 `ciu ssh`/push may replace the adapter later.

### 3.6 The guard and control-plane protection

**Configuration: host-owned, under `/etc`, like every service.**

- The main file is `/etc/cgprofile/guard.toml`, plus a drop-in directory
  `/etc/cgprofile/guard.toml.d/*.toml` merged in lexical order.
- cgprofiled reads them at start and on `SIGHUP`.
- They are root-owned. They are written by the host installer (debian-install-v2
  via host-setup) or by fleetd's provisioning step, but **owned by the host**:
  a local drop-in wins.
- The config digest is reported in the facts.

```toml
# /etc/cgprofile/guard.toml (defaults)
enabled = true
mode = "enforce"                 # report | enforce
[trigger]                        # memory PSI `full` only
host_full_avg10_pct = 2.0        # /proc/pressure/memory full avg10
slice_full_avg10_pct = 5.0       # dev-gates.slice memory.pressure full avg10
sustained_s = 20
[thaw]
full_avg10_below_pct = 0.5       # both signals below this
sustained_s = 120
[victim]
policy = "least-progress-per-ram"
[reclaim]
step_bytes = "256M"
swappiness = 200                 # used when the kernel supports the memory.reclaim swappiness= argument
max_parked = 2
max_park_s = 7200
[log]
io_pressure = true               # io.pressure is logged, never a trigger
```

**What is observed:**

- `/proc/pressure/memory` (`full`) and `dev-gates.slice/memory.pressure`
  (`full`) are the **only triggers**.
- CPU and IO pressure are **not** triggers. IO pressure is logged.
- Stalls where **all** tasks wait on memory (`full`) mean thrashing, while
  `some` also fires on ordinary reclaim. That is why the trigger is `full`.

**Victim choice: "least progress per RAM".**

- For each lane session cgprofile already tracks the progress stream
  (`--progress-stream`).
- The guard computes `progress_rate / memory.current`: candidates finished in
  the last 10 min per GiB charged.
- It parks the session with the lowest value, which is the one paying most
  RAM for the least work. Sessions that `--shed never` are skipped.
- The guard decides nothing beyond this rule table (§7.2 Q1).

**Ordering with mdt-psi-guard.**

- psi-guard acts at protected-unit memory `some` > 10% or host memory `full`
  > 5%, by throttling the lane parents.
- The lane guard's defaults trigger earlier (host `full` > 2%) and more
  selectively.
- The guard never thaws while psi-guard reports an active action. psi-guard's
  `enforce` is enabled on a compute host only after the lane guard's.
- They write different keys: leaf `cgroup.freeze`/`memory.reclaim` for the
  guard; tier weights/`MemoryHigh` for psi-guard.

**Control-plane protection** (installed by host-setup, owned by the host):

- **The slice:** a `ctl.slice` holds `sshd.service`, `docker.service`,
  `containerd.service`, `cgprofiled.service` (with the guard),
  `mdt-psi-guard.service`, the runner cockpit container (`--cgroup-parent
  ctl.slice`) and the runner's `user-<uid>.slice` (SSH sessions, the
  forced-command entry, run-job supervisors) via a drop-in.
- **CPU:** `CPUWeight=10000` on `ctl.slice` against `100` for
  `dev-gates.slice`. Lanes cannot starve SSH, Docker or the guard.
- **Memory:**
  - `MemoryMin = clamp(256 MiB + 6% × MemTotal, 768 MiB, 2 GiB)`. That is
    768 MiB on an 8 GiB host and about 1.2 GiB on 16 GiB.
  - WP1 measures the members' real working set. The default is then replaced
    by `1.25 × measured p95`, still clamped.
  - The protected units keep their `OOMScoreAdjust` drop-ins.
  - `memory.min` is subtracted from `memory.for_lanes` in the facts.

### 3.7 Parking: freeze + `memory.reclaim`

1. **Target:**
   - the lane container's own cgroup (the Docker scope under
     `dev-gates.slice`), which holds every process of the instance;
   - not the cgprofile placement leaf;
   - the guard never uses `docker pause`;
   - `cgroup.kill` works on a frozen group, and `docker stop` waits for thaw.
2. **Freeze:** write `cgroup.freeze = 1` and wait for `frozen 1` in
   `cgroup.events` (bounded wait).
3. **Reclaim** in steps with `swappiness=` (when the kernel supports it).
   Stop at `EAGAIN`, or when `memory.stat` `anon + file + shmem − zswapped`
   reaches the target, or when the trigger releases. The zswap pool stays
   charged, so `memory.current` alone does not show progress.
4. **Re-evaluate:**
   - thaw one at a time when `full` stays below the thaw threshold for
     `sustained_s`;
   - fleetd also asks for a thaw when an instance on that host finishes or a
     RAM need changes;
   - after `max_park_s` the parked job is stopped, and its unfinished
     candidates return to fleetd's queue.
5. **Wall clocks:**
   - A candidate in flight across a freeze is **discarded and re-run**.
     cgprofile adds `frozen_seconds` to each observer window, and Assay treats
     `frozen_seconds > 0` as not finished, because candidate timeouts run on
     `CLOCK_MONOTONIC`.
   - cgprofile liveness clocks pause while frozen.
   - Job progress deadlines (§3.9) pause while parked.
   - There are no tight wall budgets to protect.
6. **Fallback:** stop + re-queue, used when the kernel lacks freeze/reclaim, a
   lane is `--shed stop`, or swap headroom is exhausted.

### 3.8 The controller host: no fleet work in the MVP

- In the MVP fleetd has no Docker access and runs nothing on the controller
  host. "Small lanes only" is served by `plan` math.
- **WP9, later and only on explicit approval:**
  - a locked-down runner with its own group and socket;
  - an operator allow-list of lane ids + image digests, so the command and
    image never come from a client bundle;
  - no client Docker flags, `--network none`, a read-only root, only the work
    dir mounted;
  - ≤ 2 GiB, one lane at a time, `dev-gates.slice`;
  - memory PSI some ≤ 10 at admission;
  - the controller-local hold line and gate slots read from fleetd policy.

### 3.9 Dispatch and failure model: named jobs + progress deadline (no leases)

- **Job names:**
  - Each batch runs as one `run-job` named `<campaign>.<host>.s<slot>.b<n>`,
    where the slot is the instance slot on that host.
  - `run-job` refuses a duplicate name, so a retried `start-job` is a no-op.
    The name is the idempotency key.
- **Re-adoption:**
  - After a fleetd restart, or at every tick, the probe lists the host's
    named jobs: running or EXIT code, plus progress file mtime and counts.
  - fleetd adopts what runs, collects what finished, and re-queues the
    unfinished candidates of jobs that died.
  - `state.json` loss is recoverable: campaigns are rebuilt from the job
    names, the hosts' stores and `results/`. Only never-dispatched requests
    are lost (ledger "missing").
- **Lost host:**
  - unreachable for longer than `lost_after` (default 30 min, measured on
    fleetd's monotonic clock) → its unfinished batches are re-queued on other
    hosts.
  - If the host returns, its old jobs keep running unless fleetd stops them;
    fleetd stops a returned job only when all of its candidates are already
    finished elsewhere.
- **A double run is harmless.** The merge manifest (§4.5) names one owner
  store per candidate, and records in other stores are ignored. The closeout
  counts duplicate disagreements as evidence of flakiness.
- **Deadline = stall detector, not a wall budget:**
  - A job continues while its progress (`candidate` events) advances.
  - It is **stalled** when no candidate finished for
    `T_stall = max(15 min, 3 × p95 predicted candidate time on that host)`.
    Parked time is not counted.
  - A stalled job is stopped. Its finished candidates stay valid, and its
    unfinished ones are re-queued.
  - Run-Gate's lane `budget` is set generously for fleet jobs. The
    supervisor's exit-4 relaunch loop continues while candidates advance, and
    there is no max-attempt cap for fleet jobs.
- **Progress and ETA:** `campaign <id>` on the observe socket returns, per
  lane and per host:
  - candidates done/total;
  - rate;
  - parked/stalled jobs;
  - the predicted remaining time (§4.2).

  ctlstat reads the same JSON.
- **Cancel and drain** queued while a host is unreachable are applied at the
  next contact, before any new start on that host.

### 3.10 Preparation phase

- Assay derives each candidate's timeout from **this host's** baseline
  (`assay/src/assay/mutation.py:4685`). Speculative starts and foreign
  baselines are therefore excluded.
- **Overlap:** while the host-local baseline runs, the instance builds its
  plan, creates CoW workspaces, checks tools, and stages the first candidate
  workspace per worker. During execution it stages the next candidate and the
  next batch's workspace.
- **One baseline per host per campaign.**
  - The first job on a host writes a host-local baseline record keyed by
    commit, `judge_sha256`, image digest, host id and `boot_id`.
  - Later batches, slots and re-attempts on that host and boot reuse it.
  - The baseline wall time and budget are recorded as provenance: resource
    facts, not judge identity.
- **Assay changes (WP13):** `run --baseline-cache DIR` with strict key
  validation; `prepare --stage-only`; one-ahead staging.

## 4. Lane demand and RAM-first placement

### 4.1 Schema (`[lanes.<lane>.demand]`; v8: `[testing.lanes.<lane>.demand]`)

```toml
[lanes.r2.resources]                 # EXISTING: per-container caps = runaway bounds
memory = "3g"
cpus = "3"

[lanes.r2.demand]                    # NEW (RG-275); estimates until measured
unit = "candidate"                   # candidate | worker | run
instance_memory = "250m"             # fixed charge per instance (judge, container)
unit_memory = "120m"                 # median charge per running unit incl. its workspace (tmpfs/CoW)
unit_memory_high = "600m"            # p95 / "high" class charge (RESOURCE-PROFILES §5.3)
unit_seconds_ref = 95                # median wall seconds per unit at the REFERENCE speed
# reference speed = the Geekbench baseline_score of the major in use (GB6: 2500)
# efficient upper bound per instance = Assay judge.mutation.jobs_max (or workers_max)
```

- A lane says **nothing about hosts or hardware**.
- Measured per-candidate values replace these estimates (§4.4).
- Removed from operators and lanes: fixed campaign `jobs`, the runner's
  `--instances/--shard-*`, and NC-48's `R2_*` memory variables.

### 4.2 Wall time from Geekbench (secondary, for ETA and batch sizing)

**What the scores mean.**

- Geekbench scores are **linear** against a per-major baseline machine.
  Primate Labs: "Higher scores are better, with double the score indicating
  double the performance".
  - GB6 baseline: 2,500 = Dell Precision 3460 with Core i7-12700.
  - GB7's baseline is per cgprofile's table.
- **Majors are never mixed.** Minor versions shift scores too (6.1 raised
  single-core by up to 5%), so the exact version is recorded.
- For a CPU-bound workload like Geekbench's, **2 × the single-core score ≈
  half the wall time**.

**How it is used.**

- An R2 candidate is a **serial** process (one pytest run per worker), so its
  time scales with the **single-core** score:

  ```text
  t_h(c) = t_ref(c) × baseline_score / single_core_h × f_h
  ```

- The **multi-core** score is not used as a divisor:
  - GB6's multi-core test is a shared-task workload that deliberately does
    not scale linearly with cores;
  - lane parallelism is bounded by CPUs and by the lane's efficient upper
    bound J (`jobs_max`);
  - host throughput = Σ over running workers of 1 / t_h, with workers ≤
    `cpu.for_lanes × oversubscribe_max`, and at most J per instance.
- **`f_h` is ONE learned correction factor per host.**
  - It covers memory bandwidth, cache, disk, steal time and contention:
    everything the benchmark does not show.
  - It is an EWMA of `actual / predicted` over all finished candidates of all
    lanes on that host, default 1.0, bounded to [0.5, 2.0].
  - It is not per lane and not pooled across hosts.
  - It is reset on CPU-model change or re-enrollment.
- **The ETA** of a lane is Σ predicted remaining time per host list ÷ that
  host's running workers, plus one batch's tail. It is reported, never
  enforced.

### 4.3 Host RAM budget

```text
B_h = (memory.for_lanes_h − control_plane.memory_min_h) × overcommit_h(1.5)
      − Σ expected charge of other running jobs − Σ instance_memory of this campaign's slots on h
W_h = worker slots = floor(cpu.for_lanes_h × oversubscribe_max_h) − running workers of other jobs
b_h = B_h / W_h                   (average RAM per running worker the host can afford)
cap_h = B_h − (W_h − 1) × ram_low (the largest single candidate h can run beside low ones)
ram_low = median r(c) of the lane's low class (§4.4); declared unit_memory until measured
```

- Expected charges, not caps, are summed. Container caps are runaway bounds
  and may sum above RAM.
- **Safety net, in order:**
  1. Assay's in-instance admission (`--memory-budget-bytes`, one high at a
     time);
  2. the per-container cap;
  3. the guard (§3.6);
  4. psi-guard;
  5. the slice `MemoryMax`.
- fleetd starts nothing new on a host with memory or IO PSI some avg10 > 25,
  or with a parked job.
- **KSM** is reported in the facts (ksmd on, advisor CPU limit about 5-10% of
  one core, the opt-in shim) and never added to `B_h`. Measured sharing
  lowers the measured charges.

### 4.4 Per-candidate RAM and per-host candidate lists

**Candidate RAM `r(c)` and normalised time `t_ref(c)`**, from the first
source available:

1. this candidate id's own record: from a previous batch, the main store or a
   carry-over store (`profile`; for RAM `memory_peak_bytes`, else
   `rss_peak_bytes` + the workspace's tmpfs/CoW charge);
2. the median of finished candidates in the same source file (same judge or
   carried-over judge);
3. the lane's measured median;
4. the declared `unit_memory` / `unit_seconds_ref`.

`t_ref = t_measured × single_core_h / baseline_score / f_h` normalises a
measurement taken on host h.

**List building (each re-plan; greedy bin-packing with a mix constraint):**

```text
classes: low if r(c) ≤ min_h b_h; high otherwise
1. ROUTE   H(c) = { h : r(c) ≤ cap_h }        (hosts that can run c at all)
           H(c) empty → UNPLACEABLE (attention), except memory-crash retries (step 5)
2. ORDER   by |H(c)| ascending (most constrained first), then t_ref descending (longest first)
3. ASSIGN  for each c: candidate hosts h ∈ H(c) with
              mean_r(list_h ∪ {c}) ≤ b_h                     (mix constraint)
           choose the h with the smallest projected finish
              F_h = Σ_{c' ∈ list_h} t_h(c') / W_h + t_h(c) / W_h
           none satisfies the mix → the h ∈ H(c) with the largest b_h (more RAM), flagged
4. BATCH   cut list_h into batches of ≈ 4 × W_h candidates, each batch interleaving
           high and low candidates (two-ended: longest/high first, paired with short/low),
           so the running mean inside Assay's admission stays near b_h
5. RETRY   a candidate that ended `crashed` with memory evidence (memory.events oom_kill
           in its window, or SIGKILL at the container cap) is retried ONCE alone
           (batch of 1, --jobs 1) on the host with the largest cap_h; a second memory crash
           there is final (the judge's own verdict)
```

- **Dispatch is incremental.** fleetd keeps about two batches in flight or
  queued per slot. It starts the next batch of a slot when the current one is
  three-quarters done.
- **Unassigned candidates are re-planned every tick** with the newest `r(c)`,
  `t_ref(c)` and `f_h`. A host that runs faster or has more RAM free
  therefore takes more, and the CPUs stay busy.
- "Higher-RAM work on the host with more RAM, so the small host still runs
  low-RAM work" falls out of steps 1-3.

**Learning from finished batches.** At each batch's end, fleetd fetches the
batch's state records:

- the `profile`, `elapsed_seconds` and outcome per candidate id;
- the baseline provenance.

It then:

- updates its candidate table (keyed by lane, judge, candidate id) and the
  per-file and lane medians;
- updates `f_h` from `actual / predicted`.

The candidate table is fleetd state. Across commits, candidate ids change, so
the file medians carry the knowledge.

### 4.5 What Assay and Run-Gate must expose

| Need | Exists | New |
|---|---|---|
| Run a chosen list | `--only-candidates FILE` (B531); Run-Gate passes it (RG-265) | — |
| Pending set | `plan --list-pending` | — |
| Reuse a slot store across batches | `--merge-state` (same judge) and the store lock | — |
| Per-candidate RAM/time in records | `profile`, `elapsed_seconds` (observer on) | the observer on host runs (lift LR-KI-11) |
| **Strict merge of list-based stores** | strict `--merge-shard-store` covers hash shards only | `--merge-manifest FILE` (`assay-merge-manifest/1`: per candidate id, one owner store). Strict: every planned id has exactly one owner; the owner holds a final same-judge record; records in other stores are **ignored** (duplicates); disagreements are counted in the closeout; missing → refuse by name |
| Frozen-candidate invalidation | — | `frozen_seconds` in observer windows (cgprofile) → not finished (Assay) |
| Host-local baseline cache | — | `--baseline-cache DIR` (§3.10) |
| Memory-crash evidence | `crashed` bucket | `memory_events.oom_kill` delta in the candidate's profile window |

Run-Gate passes `--merge-manifest` and `--baseline-cache` through. Its
supervisor gets a fleet mode: progress-based relaunch and no attempt cap.

## 5. Migration and work packages

### 5.1 Compatibility

- The two live campaigns (PWMCP on host A, ciu on host B) finish under the
  legacy script.
- The new supervisor uses the same lock and `.active` paths.
- `run-r2-campaign.sh` becomes a wrapper for one release.
- `remote-gate.py` and `host-sat-queue.sh` keep their own keys until they
  submit through fleetd (WP5). Their keys are then revoked. Private keys are
  never copied.

### 5.2 Work packages

| WP | Scope | Owner | Tests | Risk | Depends |
|---|---|---|---|---|---|
| **WP0** | Schemas: `host-facts/1`, `work-request/1`, `assay-merge-manifest/1`, `guard.toml` | fleetd (new) | schema fixtures | low | — |
| **WP1** | (a) facts collector CLI incl. Geekbench record + control-plane state; (b) guard in **report** mode reading `/etc/cgprofile/guard.toml(.d)`, SIGHUP reload | fleetd, cgprofile | `/proc`, `/sys` fixtures; PSI `full` trace replay; victim ranking | low | WP0 |
| **WP2** | `fleet plan` library + CLI (§4.2-4.4: budgets, routing, mix, batches, retry rule) + `run-gate campaign plan` | fleetd, run-gate | properties: never above `B_h`/`cap_h`, mix holds or is flagged, big candidates only on big hosts, per-host factor isolation; hand mutants | low | WP0 |
| **WP3** | daemon skeleton: lock, `state.json`, sockets, install (unit hardening, groups, endpoint fragment), inventory from fleetd's ciu rows, probe tick; new cmru project + gate | fleetd | socket gating, single instance, restart re-adoption from fake job listings | medium | WP1(a), WP2, MDT-086 |
| **WP4** | host forced-command entry + `run-gate campaign supervise` (fleet mode) + wrapper; `ciu host enroll` `--restrict/--forced-command` | run-gate, fleetd, ciu, netcup | parity cases from `scripts/netcup/tests/test_run_r2_campaign.py`; idempotent job start; stall detection | medium | WP0 |
| **WP5** | batches, dispatch, collection, candidate table + `f_h` learning, re-plan, merge via `--merge-manifest`, ledger; remote-gate/queue migration | fleetd, run-gate, assay (manifest merge) | fake-transport campaign end to end; double run ignored; lost host re-queue | medium | WP3, WP4 |
| **WP6** | control-plane slice (`ctl.slice`, `memory.min` formula, `cpu.weight`) in host-setup / debian-install-v2 | mdt-cockpit host-setup | unit rendering tests; host acceptance | low | — |
| **WP7** | guard **enforce**: freeze + reclaim + thaw, least-progress-per-RAM, invalidation (`frozen_seconds`), ordering with psi-guard | cgprofile, assay, run-gate | a frozen candidate is never final; trace replays; host rehearsal | medium-high | WP1(b), WP5, §7.2 Q1 |
| **WP8** | profiled host runs (lift LR-KI-11), memory-crash evidence | run-gate, assay | records carry `profile` on hosts | low | WP4 |
| **WP9** | controller-host locked-down runner | fleetd | allow-list enforcement | high | WP5, explicit approval |
| **WP10** | ctlstat reads fleet state (progress, ETA, hosts, guard) | ctlstat | goldens | low | WP3 |
| **WP11** | ciu v8 client and `[testing.lanes]` demand | ciu8 | client parity | medium | ciu8 checkpoint D |
| **WP12** | move generic runner scripts out of `scripts/netcup` (NC-52) | netcup | contract tests | low | WP5 |
| **WP13** | host-local baseline cache, `prepare --stage-only`, staging | assay, run-gate | key mismatch refused; one baseline per host | medium | WP4 |

**First week:** WP0 + WP1(a) + WP2, with no daemon.

- Schemas, the facts collector including Geekbench, and `plan` as a library
  and CLI.
- `plan` prints per-host candidate lists and the matching `run-gate <lane>
  --only-candidates` commands, which today's runner can execute.
- WP4, WP6 and WP13 can run in parallel.

## 6. Security and failure modes

### 6.1 Key custody

- **Per-host keys from `ciu host enroll`**, run as `fleetd`, live in fleetd's
  XDG state (0700/0600), readable only by `fleetd` and root. No socket verb
  returns them, and they are not in the repository or `state.json`.
- **Runner `authorized_keys`:**
  `restrict,from="<controller pattern>",command="<runner home>/fleet/bin/fleet-host-entry" ssh-ed25519 …`.
  - The entry accepts only `probe | import | start-job | stop-job | fetch`
    with validated arguments, and runs the on-host guard command first.
  - `restrict` and `command=` need the WP4 extension of `ciu host enroll`,
    which writes `from=` today.
- **Blast radius.** `runner` is in the docker group, so it is root on its
  compute host. A stolen key is narrowed by the forced command. A compromised
  controller reaches every compute host, but not the controller host itself
  through fleetd, which has no Docker group there.
- **Rotation, revocation and reinstall:**
  - rotation: `ciu host enroll --replace` and `--revoke-old`, on demand and
    immediately on suspected compromise;
  - revocation: drain the host, remove the `authorized_keys` line, delete the
    row;
  - **reinstall:** the pin fails, so the host becomes `identity-changed`, its
    batches are re-queued, and `f_h` is reset. The operator runs the provider
    check and re-enrolls.
- **`state.json`, `results/`:** owner `fleetd`, 0600/0700, not backed up
  (rebuilt from hosts, §3.9). Addresses live only in fleetd's ciu inventory.
  There are no provider credentials anywhere in fleetd.

### 6.2 Access

- `fleet-control` membership means "may run committed lanes on every compute
  host". It is Docker-group-equivalent there, and grants nothing on the
  controller host in the MVP.
- Groups are granted by the operator, through devcontainer `--group-add`.

### 6.3 Failure modes

| Failure | Behaviour |
|---|---|
| fleetd restart / controller reboot | jobs keep running on hosts; fleetd re-adopts them by name |
| `state.json` lost | rebuilt from job names, stores, `results/`; un-dispatched requests are re-submitted |
| Host unreachable > 30 min | its unfinished batches are re-queued elsewhere; a returning duplicate is ignored by the merge manifest |
| Job without progress for `T_stall` | stopped; finished candidates kept, the rest re-queued |
| Memory pressure (PSI `full`) | the guard parks the least-progress-per-RAM lane; it thaws when pressure stays low |
| Memory-crashed candidate | retried once, alone, on the biggest host |
| Candidate too big for every host | UNPLACEABLE, attention |
| Control plane starved | prevented by `ctl.slice` `memory.min` + `cpu.weight 10000` |
| Host reinstalled | `identity-changed`, re-enroll |

## 7. Operator decisions and open questions

### 7.1 Decisions taken (inputs, not reopened)

1. Only host installation is provider-specific; any compute host must work.
2. A central service on the controller host, handed hosts after install.
   Running happens through Run-Gate.
3. Sockets only, read and write separate, by group, no auth.
4. Host state lives outside the repository with the service; policy stays on
   the host.
5. Lanes state RAM and normalised wall time plus an efficient bound, never
   workers or hardware.
6. Overcommit 1.5 for expected peaks, PSI as the safety net.
7. The controller host takes small lanes only (later, WP9, on approval); the
   first week has no daemon.
8. A guard per host, in a protected cgroup. The scheduler runs the tick.
9. Shedding: freeze + `memory.reclaim`, park until something changes.
10. **Geekbench extrapolation + one learned correction factor per host** (not
    per lane, not pooled). Wall time is secondary; **RAM is key**.
11. **Per-host candidate lists** with low/high RAM mix and big candidates on
    big hosts.
12. **Named jobs + a progress-based deadline; no leases.** fleetd and ctlstat
    show progress and ETA.
13. **The guard** acts on memory PSI `full` only, with the least-progress-per-RAM
    victim, thaw when low, and a configurable host-owned `/etc` file.
    **Control-plane protection** uses `memory.min` + a high `cpu.weight`.
14. KSM is host policy. Standing rules: CPU overcommit; PSI > 25 stops new
    work; tests are load-independent.

### 7.2 Remaining open questions (recommended default first)

1. **Amend D-651 Q4 / ciu `SPEC-V8.md` S21.4.1** so cgprofile may execute the
   host's guard rule table (park/thaw by the configured victim policy)?
   *Default: yes.* Otherwise the guard is a separate small daemon with the same
   `/etc` file.
2. **Guard defaults:** trigger at host `full` avg10 > 2% or `dev-gates` `full`
   > 5% for 20 s; thaw below 0.5% for 120 s; at most 2 parked; 2 h maximum
   park? *Default: as listed.*
3. **Control-plane `memory.min`:** `clamp(256 MiB + 6% × MemTotal, 768 MiB,
   2 GiB)` until measured, then `1.25 × measured p95`? *Default: yes.*
4. **List and batch parameters:** batches of about 4 × W_h, two in flight per
   slot, a mix slack of 0, `lost_after` 30 min,
   `T_stall = max(15 min, 3 × p95)`? *Default: as listed.*
5. **`f_h` bounds and reset:** EWMA over all lanes, bounded [0.5, 2.0], reset
   on CPU change or re-enrollment? *Default: yes.*
6. **Service name and home:** a new cmru project `fleetd/` (working name)?
   *Default: yes.*
7. **Priority across projects:** `release-t3` > `campaign` > `light`, FIFO
   within a class? *Default: yes.*
8. **Forced-command entry** (`restrict`, `command=`) as a `ciu host enroll`
   extension? *Default: yes.*

## 8. Verification boundary

- This is a design. Nothing was run on a host, and no code was changed.
- **Geekbench semantics** come from Primate Labs' GB6 internals document
  (<https://www.geekbench.com/doc/geekbench6-benchmark-internals.pdf>):
  - linear scores against a 2,500 baseline (Dell Precision 3460,
    i7-12700);
  - "double the score indicating double the performance";
  - a shared-task multi-core model.
- The 6.1 minor-version shift is from Primate Labs' 6.1 announcement
  (<https://www.geekbench.com/blog/2023/06/geekbench-61>). How well
  single-core scores predict lane times is what `f_h` measures.
- Assay facts come from:
  - `assay/src/assay/mutation.py:4685` (budget) and `:296-301` (legacy keys);
  - `docs/design/RESOURCE-PROFILES.md` §2.1, §5.3, §5.6 and `:265`;
  - `assay/src/assay/cli.py:415-526`.
- Guard and psi-guard facts come from `mdt-cockpit/host-setup/README.md:199-228`
  and `cgprofile/docs/RG55-INTERFACE-CONTRACT.md:549-660`. Enrollment facts
  come from `ciu/docs/CIU-HOST-ENROLLMENT-PROPOSAL.md`.
- The `memory.min` default and the guard thresholds are proposals to be
  measured in WP1 and WP6.
- The KSM Run-Gate default (job ksm-default2) and ksmd tuning are in-flight
  work reported by the coordinator, not verified here.
