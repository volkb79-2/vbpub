# Design: compute hosts and distributed lane campaigns (2026-11)

Status: DESIGN, decision record, **rev 3**, proposed, not implemented.
Backlog: run-gate RG-275, netcup NC-52. Amends
`run-gate-project/docs/DESIGN-lane-rework-2026-11.md` §3.3 (see §3.0). No host
names, addresses, controller paths or credentials appear in this file; hosts
are "host A/B".

**Revision history:**

- **rev 1:** Run-Gate allocator, mdt-cockpit as the provider.
- **rev 2:** the operator interview, giving a central service with
  group-gated sockets, overcommit 1.5, parking by freeze + reclaim, a
  per-host guard, per-flavor fits, the preparation phase once, and KSM as
  host policy.
- **rev 3:** answers the independent review of rev 2
  (`compute-design-REVIEW.md`, 14 findings):
  - backlog ids renumbered;
  - key custody based on the implemented `ciu host enroll`;
  - controller-host execution dropped from the MVP;
  - a deterministic preparation overlap;
  - a full lease/fencing protocol;
  - corrected Assay data claims (`profile`, RSS lower bound, cgroup charge);
  - the guard reconciled with mdt-psi-guard, with D-651 Q4 named;
  - freeze target and reclaim details;
  - WP dependencies;
  - citation fixes.

## 0. Decisions in one screen

| # | Decision | Section |
|---|---|---|
| D1 | One **central scheduler/distribution service** (working name `fleetd`) on the controller host owns host state, leases, allocation and the campaign tick. It runs as its own unprivileged user, with **no Docker access** in the MVP. | §3.2 |
| D2 | Clients reach it **only through Unix sockets**: observe (read) and control (write), gated by group membership, with no authentication inside the service. This deliberately deviates from cgprofile, which authorizes by peer uid (§3.3). | §3.3 |
| D3 | **Run-Gate stays the lane layer.** Its client turns a lane into a work request, and it executes instances on the hosts. ciu projects ask for work through Run-Gate (later `ciu gate`) and never own hosts. | §3.4 |
| D4 | **Host enrollment and keys come from the implemented `ciu host enroll`**, run as the fleetd user. Provider-specific identity checks (provider API tokens) stay outside fleetd, in provisioning. | §3.5, §6.1 |
| D5 | A lane declares **demand**, not parallelism. Workers and instances are the scheduler's output. Memory demand is fitted from the **cgroup charge** (which includes tmpfs and page cache), not from RSS. | §4 |
| D6 | Expected peaks are summed against `RAM for lanes × 1.5`. Caps may overcommit. PSI is the safety net. KSM is host policy, never a savings assumption. | §4.3 |
| D7 | A per-host **guard** (an extension of cgprofile) **executes** scheduler-given policy: it parks (freeze + `memory.reclaim`), thaws and self-fences expired leases by rule. This needs an explicit amendment of D-651 Q4 / ciu SPEC-V8 S21.4.1 (§7.2 Q1). | §3.6, §3.7 |
| D8 | **Leases are fenced on the host**: the generation is checked at every renewal, the guard enforces expiry, and an epoch blocks a stale scheduler. All expiry comparisons use the host's own clock. | §3.9 |
| D9 | Performance is fitted **per hardware flavor**, never pooled. | §4.5 |
| D10 | Preparation overlaps only what does not affect judgment: plan, workspace setup, and staging of the next workspaces. Candidates start only after the **host-local** baseline exists. The baseline is measured once per host per campaign instead of per instance and attempt. | §3.10 |
| D11 | **The controller host runs no fleet work in the MVP.** A locked-down small-lane pool is a later, separately gated WP. | §3.8 |

## 1. Problem and non-goals

### 1.1 Problem

- Long lane work (native Assay R2 campaigns of 300 to 1,500 candidates, 3 to 10
  hours; later fuzz/variation lanes) must keep **every CPU of every compute
  host** busy, on any host we provide, for several projects (vbpub, dstdns,
  later every ciu v8 project).
- Today the only supervisor is `scripts/netcup/runner/run-r2-campaign.sh`
  (1,069 lines of bash), and it covers one host.
  - Cross-host split is manual: the operator chooses
    `--shard-total/--shard-offset` per host and copies shard stores by hand
    before `--merge-only` (`scripts/netcup/runner/README.md:168-181`).
  - The memory guard charges each instance the lane's whole cap
    (`run-r2-campaign.sh:568-594`). A PWMCP lane capped at 3 GiB ran 3 workers
    at about 430 MiB and about 1 CPU, but two instances were refused, and CPUs
    stayed idle.
  - NC-48 (branch `r2-guard-20261112`) patches the arithmetic through
    environment variables on the host.
- **What is netcup-specific.** Host installation is: `install-host.py`, the
  SCP API client, debian-install-v2, `pre-reinstall*.py`. **Two runtime
  pieces are provider-coupled too:**
  - the per-host `api_check`, which `scripts/netcup/runner/remote-gate.py:196-198`
    requires for every host row and `:225` reports as a "Netcup identity
    check", and which `host-sat-queue.sh:156,288` also calls. It sources the
    provider API token;
  - the hard allowlist `ALLOWED_HOSTS` (`remote-gate.py:30`), today's
    "no other host is ever contacted" protection.
- **Everything else is generic SSH + Docker + Run-Gate code:** the supervisor,
  runner provisioning, run-job, bundles and lane execution.
- **This design replaces both provider-coupled pieces:**
  - identity is the pinned host key from enrollment plus the on-host guard
    command;
  - the provider API check runs only at provisioning time;
  - the allowlist becomes "only hosts enrolled into fleetd's inventory by the
    fleetd user, and never a host whose facts say `HOST_ROLE=shared-prod`".
- NC-52 moves the supervision logic out of `scripts/netcup`.

### 1.2 Non-goals

- No change to verdicts, judge identity or Assay's store format. Shards,
  strict merge and carry-over (RG-265, B529-B531) are used as they are.
- No authentication, accounts or tokens inside the service. Access is socket
  group membership (operator decision).
- **No provider-specific code and no provider credentials in fleetd.**
- No cluster manager (Kubernetes, Nomad, Slurm, Buildkite).
- No automatic changes to host-wide policy (KSM, swap, zswap, slices). The
  guard writes only lane cgroups, by rule.
- No fleet execution on the controller host in the MVP (§3.8).

## 2. Landscape

### 2.1 What exists (reuse, do not rebuild)

| Concern | Existing component | What it gives | File refs |
|---|---|---|---|
| Host table | controller-private JSON (`ssh`, `api_check`, `guard`, `runner_home`, `cockpit`, `image`, `repos`, `queue_lanes`) | reach a host, provider check, on-host guard | `scripts/netcup/runner/remote-gate-hosts.example.json`, `scripts/controller/hosts.local.json.example`, `scripts/netcup/runner/remote-gate.py:190-237` |
| **Host enrollment** | `ciu host enroll` (**implemented on v7**, ciu-P52 / CIU-93): two-step SSH enrollment; key pair on the controller, the private half never moves; TOFU fingerprint confirmation; `from=` restriction opt-in; pending key generations with an atomic switch and `--revoke-old` | identity, keys, rotation | `ciu/docs/CIU-HOST-ENROLLMENT-PROPOSAL.md:3, 23-65, 121`; `ciu/docs/SPEC-V8.md` S7.2.4 |
| One-shot remote lane | `remote-gate run/status/logs/collect/cancel` | bundle → throwaway checkout → lane → collect | `scripts/netcup/runner/remote-gate.py:738-975` |
| Serial lane queue | `scripts/netcup/runner/host-sat-queue.sh` | per-host loop, pause flags, INFRA auto-pause | `scripts/netcup/runner/README.md:772-838` |
| Campaign supervision | `run-r2-campaign.sh` | persistent checkout, attempt loop on exit 4, stall counter, stop/status, exact-name cleanup, shard fleet, merge | `scripts/netcup/runner/README.md:85-216` |
| Code transport | `run-job`, `sync-from-devcontainer` (git bundle) | survive SSH loss; no origin on hosts | `scripts/netcup/runner/README.md:68-83` |
| Shards, merge, carry-over | Run-Gate `--shard K/N`, STRICT `--merge-shards N`, `--carry-over`, `--merge-state`, `--only/--exclude-candidates` | independent instances | `run-gate-project/CONSUMERS.md:770-856` |
| Shard cut, manifests, store lock | Assay `blake2b(id) % N`, `shard.manifest`, `plan --list-pending`; an exclusive `flock` on a store for the whole run | deterministic split; one writer per store | `assay/src/assay/mutation.py:1788-1793, 3497-3530`; `assay/src/assay/cli.py:504-526` |
| Per-candidate budget | `auto_budget_per_candidate_seconds = max(3 × baseline, baseline + 60 s)`, from the baseline measured **on this host at run time** | why the baseline must be host-local (§3.10) | `assay/src/assay/mutation.py:4685` |
| Worker sizing in one run | `select_r2_jobs`, `candidate_ram_from_history`, `r2_oversubscribe`, `jobs_max` | workers inside one instance | `run-gate-project/run_gate.py:4889, 11649`; `run-gate-project/docs/DESIGN-GUIDE.md:1051-1128` |
| Socket design | cgprofile control/observer levels: `root:root 0755` directories, a group-owned socket inode, endpoints from env only, GIDs distinct from Docker's and from each other | the access model (§3.3 notes the deviations) | `cgprofile/docs/DESIGN-GUIDE.md:855-885`; `run-gate-project/docs/DESIGN-GUIDE.md:380-430` |
| Endpoint delivery | `/etc/host-endpoints.d/<svc>.env` aggregated by augeaswrap | how clients find a socket | `augeaswrap/src/augeaswrap/endpoint.py:39-46` |
| Per-lane sessions | cgprofile `start` with consumer-authored policy (`--idle-bound`, `--ceiling`, `--on-stall kill\|report`), placement leaf `rg-<token>` (migrated pids only), `cgroup.kill` under explicit policy | the per-host lane authority the guard extends | `cgprofile/docs/RG55-INTERFACE-CONTRACT.md:549-660` |
| Per-host measurement | cgprofile `host` verb: `machine/1` + HostSnapshot | live host state | `cgprofile/docs/PROTOCOL.md:600-625`; `cgprofile/docs/RG55-INTERFACE-CONTRACT.md:164-182` |
| Tier PSI responder | `mdt-psi-guard` (`report` by default): watches **protected units' `memory.pressure some` > 10%** and **host memory `full` > 5%**, each held 10 s; in `enforce` it sets weights of dev tiers to 1, then lowers `MemoryHigh` of `dev-gates`/`dev-background` (**the parents of lane cgroups**); never freezes | the tier backstop (§3.6) | `mdt-cockpit/host-setup/README.md:199-228` |
| Host-wide policy | `/etc/mdt/host-setup.env` (`HOST_ROLE`, `MDT_EXCLUDED_*`), slices, zswap writeback per tier, protected units (the code list `mdt-cockpit/host-setup/mdt_capacity.py:360` includes `cgprofiled.service`; the README table at `:1144` still says `cgpd`) | capacity and protection policy | `mdt-cockpit/host-setup/README.md:27-157` |
| Host readiness (**unmerged**) | ESTATE-REQUIREMENTS.toml + `estate_doctor.py` on branch `estate-req-20261112` | requirement catalogue and read-only probes | branch only |
| Per-daemon admission | ciu v8 S21 tickets, Run-Gate `[admission]` | local count cap on one Docker daemon | `ciu/docs/SPEC-V8.md` S21; `run-gate-project/CONSUMERS.md:2026-2070` |
| KSM opt-in | ciu shim (`ciu/src/ciu/ksm.py`, `governance.ksm_optin`); Run-Gate default in progress (job ksm-default2) | processes opt in to KSM | `ciu/src/ciu/ksm.py` |
| Estate rules | AGENTS.md "Compute use", "limits are host-owned" | PSI > 25 stops new work; about one job more than the cores | `AGENTS.md:196-226` |
| Estate status | ctlstat requirements §G/H/I, item 9 | the consumer of fleet state | `docs/design/ctlstat/REQUIREMENTS.md` |

### 2.2 "Assay already provides resource and performance data, correct?" Partly.

| Data | Producer | Notes |
|---|---|---|
| Per-candidate `elapsed_seconds`, `phase_seconds`, `startup_seconds`, `worker_count` | Assay `candidate` progress events | always present (Assay's own clock) |
| Per-candidate `profile` (`resource-profile/1`: CPU, memory, IO, PSI) | cgprofile observer windows, carried in the event | **only with the observer on.** The flat keys (`peak_rss_bytes`, `cpu_seconds`, `write_bytes`, `judge_rss_bytes`) were removed in Assay 12 (B206; `docs/design/RESOURCE-PROFILES.md:265`) and are legacy-read only (`assay/src/assay/mutation.py:296-301`) |
| `rss_peak_bytes` in a profile | summed process-tree RSS | **a lower bound**: page cache and tmpfs are excluded (`docs/design/RESOURCE-PROFILES.md` §2.1) |
| `memory_peak_bytes` | the cgroup charge (anon + page cache + tmpfs/shmem + charged zswap pool) | per instance from the cgprofile session (container scope); per candidate only in exclusive-cgroup mode |
| Candidate count, pending set | `assay plan`, `plan --list-pending`, shard manifests | exact |
| Campaign closeout | `assay/analysis/src/assay_analysis/campaign.py` | imported vs executed, buckets |
| Per-lane duration, peak memory, cores | Run-Gate `.run-gate/history.json` (cgprofile session summary) | per checkout |
| Committed footprint | `run-gate footprint --write` → `run-gate.footprint.json` | **only run-gate-project** has one |
| Host CPU speed | cgprofile `machine/1.speed` | not used for placement (§4.5) |

**Missing:**

1. Host runs are not profiled (`RUN_GATE_PROFILE=off`, LR-KI-11), so the
   `profile` and `memory_peak_bytes` that placement needs do not exist where
   campaigns run.
2. Nothing collects the data from the hosts.
3. The per-instance overhead O and the efficiency curve E(j) are not
   measured.
4. RSS-based numbers undercount exactly the load that is moving to tmpfs/CoW
   work trees. Placement must use the cgroup charge (§4.5).
5. Measurements carry no hardware-flavor key.
6. The baseline is paid once per instance per attempt (§3.10).

### 2.3 What is missing in the architecture

- a host capability record;
- leases with host-side fencing;
- a placement function;
- a demand model that separates the expected charge from the cap;
- the cross-host lifecycle (dispatch, store sync-back, merge placement, host
  loss);
- a host guard;
- one source of estate state for ctlstat.

## 3. Architecture and ownership

### 3.0 Relation to earlier decisions

- **Amends `run-gate-project/docs/DESIGN-lane-rework-2026-11.md` §3.3:**
  - **kept:** single-host lane execution and the T3 entry/ledger formats;
  - **changed:** per-host claim loops are replaced by the central service.
- **Departs from `mdt-psi-guard`'s "never freezes".** The guard freezes lane
  cgroups only under the invalidation rule and deadline table (§3.7).
- **Needs an amendment of D-651 Q4 / ciu `SPEC-V8.md` S21.4.1** ("The
  cgroup-profiler daemon measures and serves data and decides nothing"). See
  §3.6 and §7.2 Q1.

### 3.1 Picture

```text
 controller host (systemd)                                     clients (devcontainers, scripts)
 ┌───────────────────────────────────────────────┐  observe  ┌───────────────────────────────┐
 │ fleetd (user `fleetd`, no docker group)       │◄──────────│ run-gate campaign plan/status │
 │  lock: /var/lib/fleetd/lock (flock), epoch E  │  socket   │ ctlstat                       │
 │  state.json (0600), results/ (0700)           │  control  │ run-gate campaign submit/stop │
 │  inventory + keys: ciu host enroll, as fleetd │◄──────────│ fleetctl drain/undrain        │
 │  tick 30 s                                    │  socket   └───────────────────────────────┘
 └───────────────┬───────────────────────────────┘
                 │ SSH as `runner`; authorized_keys: restrict, from=, forced command
   ┌─────────────┴────────────┐
   ▼ host A (any enrolled)    ▼ host B
 ┌──────────────────────────────────────────────────────────┐
 │ fleet-host-entry (forced command): probe | import | dispatch | revoke | fetch | stop │
 │ fleet/ dispatch/*.json, leases/*.json, epoch, revoked/    │
 │ run-gate campaign supervise → run-lane.sh → cockpit → run-gate <lane> --shard K/N --jobs j
 │ cgprofiled + guard: sessions, park/thaw, self-fence expired leases │
 │ mdt-psi-guard (tier backstop)                              │
 └──────────────────────────────────────────────────────────┘
```

### 3.2 (ii) The central service (`fleetd`, working name)

**Why a service (operator: option 3):**

- several projects want work run on the same hosts;
- host ownership must not sit in a project, a devcontainer or an agent
  session;
- mdt is a devcontainer, not a provider;
- a ciu config would make every ciu project an owner.

| It owns (state) | It does not own |
|---|---|
| Inventory view: enrolled hosts (the reach facts are read from fleetd's own ciu inventory, §3.5) plus state `enabled`/`draining`/`disabled`/`identity-changed` and labels | keys' creation and rotation mechanics (`ciu host enroll`) |
| Host facts cache (`host-facts/1`) and guard state | host-wide policy (stays on each host) |
| Leases, dispatch intents, generations, epoch | verdicts and Assay stores (stay on hosts; fleetd copies them) |
| Campaigns, placement, per-flavor fits, results, ledger rows | lane definitions and demand (projects' `run-gate.toml`) |

**How it stays small:**

- One process: a stdlib daemon running as system user `fleetd`.
- It holds an exclusive `flock` on `/var/lib/fleetd/lock` for its lifetime. A
  second instance refuses to start.
- `state.json` (owner `fleetd`, 0600) uses atomic replace + fsync; there is
  also a bounded `events.jsonl`, plus `results/` (0700).
- Two sockets.
- A 30 s tick does:
  1. one composite probe per host (through the forced-command entry);
  2. reconcile;
  3. dispatch;
  4. store sync every 15 min;
  5. re-place;
  6. merge;
  7. ledger.
- Target: about 2-3k lines with tests.

**Host facts (`host-facts/1`).** One composite probe; each leaf may be `null`
with a `*_reason`.

```json
{"schema": "host-facts/1", "at_host_boottime_s": 123456.7, "boot_id": "…",
 "flavor": {"id": "sha256:…", "cpu_model": "…", "cpus_online": 4, "smt": false,
            "hypervisor": true, "memory_total_bytes": …, "disk_model": "…"},
 "covariates": {"kernel": "7.x", "disk_scope_key": "…|null", "virt_class": "kvm"},
 "cpu": {"for_lanes": 4.0, "oversubscribe_max": 1.25},
 "memory": {"for_lanes_bytes": …, "swap_for_parking_bytes": …, "overcommit": 1.5,
            "zswap": {"enabled": true, "writeback_gates": true}},
 "ksm": {"run": 1, "advisor_mode": "scan-time", "advisor_max_cpu": 8,
         "pages_shared": …, "pages_sharing": …, "general_profit": …, "lane_optin_shim": true},
 "kernel_interfaces": {"cgroup_freeze": true, "memory_reclaim": true,
                       "memory_reclaim_swappiness_arg": true, "zswap_writeback": true},
 "policy": {"host_role": "dedicated-dev", "psi_refuse_some_avg10": 25,
            "max_lane_containers": 8, "max_park_seconds": 7200, "guard_policy_digest": "…"},
 "tools": {"run_gate": ["25.5.0"], "images": {"tester-unified:local": "sha256:…"}},
 "readiness": {"doctor": "PASS|null", "at": "…"}}
```

- **`flavor.id`** digests the **hardware identity only**: CPU model, CPU
  count, SMT, hypervisor, RAM and disk model.
  - Kernel and `disk_scope_key` are recorded **covariates**. A kernel update
    does not force recalibration, and identical hardware remains poolable.
  - `disk_scope_key` comes from the unmerged iobench `disk_scope` work
    (`docs/design/iogovmon/DESIGN-rev9-2026-11-06.md:182`), so it may be
    `null`.
- **`ksm`** is policy and state:
  - whether ksmd runs, the advisor and its CPU limit (planned at about 5-10%
    of one core);
  - the counters;
  - whether the lane opt-in shim is present.
  - It is never a savings term.
- **`readiness`** is filled once estate-req merges, and is `null` until then.

### 3.3 Sockets: the cgprofile layout, with stated deviations

| Level | Socket | Group | Verbs |
|---|---|---|---|
| observe (read) | `/run/fleetd/observe/fleetd.sock` | `fleet-observe` | `version`, `hosts`, `facts`, `state`, `leases`, `campaigns`, `campaign <id>`, `plan <request>` (dry run), `fetch <campaign>`, `events --since` |
| control (write) | `/run/fleetd/control/fleetd.sock` | `fleet-control` | `submit <request>`, `cancel <campaign>`, `drain/undrain <host>`, `priority <campaign> <class>` |

**Same as cgprofile:**

- `root:root 0755` level directories and a group-owned socket inode;
- clients read endpoints only from the environment, which `fleetd install`
  publishes through `/etc/host-endpoints.d/fleetd.env`;
- the `fleet-observe` and `fleet-control` GIDs are non-zero, differ from each
  other, and differ from the Docker GID.

**Deliberate deviations:**

1. **No authorization by peer uid.** cgprofile authorizes with `SO_PEERCRED`
   (`allow_uids`; only uid 0 may stop another uid's session,
   `cgprofile/README.md:224-228`). fleetd records the peer uid/gid/pid as the
   request's `holder` for audit only. **Any `fleet-control` member can cancel
   any campaign.** This is the operator's choice ("no auth"). The record
   makes misuse visible but does not prevent it.
2. **Unit hardening differs.** cgprofiled runs with `PrivateNetwork` and
   `RestrictAddressFamilies=AF_UNIX`. fleetd needs outbound SSH, so its unit
   uses:
   - `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`;
   - `ProtectSystem=strict`, `ReadWritePaths=/var/lib/fleetd`;
   - `ProtectHome=yes`, `NoNewPrivileges=yes`, `PrivateTmp=yes`;
   - `CapabilityBoundingSet=` (empty);
   - `User=fleetd`.
3. **Socket activation:** the two sockets are systemd `.socket` units, so a
   fleetd restart never drops the listening endpoint.
4. **`SCM_RIGHTS` bundle passing:**
   - the fd must be a regular file (`fstat` `S_ISREG`);
   - at most 2 GiB by default;
   - it is read once into `/var/lib/fleetd/spool/` and verified as a git
     bundle (`git bundle verify`) before use.

**The MDT-086 gap.** The standard devcontainer template does not yet forward
endpoint variables and groups. That gap blocks fleetd exactly as it blocks
cgprofile, and WP2 depends on it (§5.2). ESTATE-REQUIREMENTS gains a
`fleetd_endpoints` component once that branch merges.

### 3.4 (iii) Who decides what

| Step | Owner |
|---|---|
| Lane → work request (`work-request/1`: lane, project dir, commit, image digest, environment mode, `shardable`, floors, demand §4.1, units remaining, carry-over source + reason) | **Run-Gate client** |
| Hosts, shapes (instances × workers), shard count, order across projects | **fleetd** |
| Execute an instance: renewal, fencing check, attempts, cleanup, merge run | **Run-Gate on the host** (`supervise`) |
| Judge candidates, manifests, strict merge, baseline and timeouts | **Assay** |
| Park/thaw/self-fence by rule | **guard** (§3.6) |

**ciu v8 integration:**

- **Requesting work.** A ciu project submits through `ciu gate <lane>
  --campaign submit`, the Run-Gate client ported under D-647 #1 (WP11). It
  needs only `fleet-control` and the endpoint variables, and never owns
  hosts.
- **Where demand lives under v8.** `[lanes.<l>.demand]` becomes
  `[testing.lanes.<l>.demand]` (ciu `SPEC-V8.md` S1.2 lane table). Measured
  per-lane values stay in `ciu.footprint.json` (`ciu gate footprint
  --write`), the v8 successor of `run-gate.footprint.json`.
- **Byte charge versus expected peak.** v8.1 S21.5.1 charges a lane its
  **warm working set**, not its peak, on one daemon. fleetd's "expected peak
  × 1.5" is the cross-host analogue in the absence of warm-set data. Once
  `ciu.footprint.json` carries warm sets, fleetd uses them as the expected
  memory with overcommit 1.0, so the two models converge. The local S21
  ticket still applies to every lane start on a host, and fleetd never places
  more than a host's published `max_concurrent`.
- **`ciu lease` (S14.4.7-8)** is a local lock key for instances and
  worktrees. It is unrelated to fleetd's compute leases, and the names do not
  collide in files: fleetd's are `fleet/leases/*.json` on hosts.
- **`ciu host enroll`** is the enrollment path now (§3.5), not "v8.2 later".

### 3.5 (iv) Enrollment and transport

- **Enrollment uses the implemented `ciu host enroll`.** It is run by the
  operator **as the `fleetd` user** (`sudo -u fleetd ciu host enroll <alias>
  --global …`).
  - The key pair lands in fleetd's XDG state (`~fleetd/.local/state/ciu/ssh/<alias>/`,
    0700/0600), and the row in `~fleetd/.config/ciu/hosts.toml`.
  - fleetd reads that row for its reach facts: `ssh_host`, `ssh_user`, port,
    key path, `known_host`.
  - fleetd has **no enroll verb**, and no socket verb touches keys.
- **Provider identity stays outside fleetd.**
  - The provider API check (today's `api_check`, which sources a provider
    token) runs at provisioning time, by the operator, in the provider
    tooling. It confirms the address belongs to the intended server before
    enrollment.
  - After that, fleetd's identity proof is the **pinned host key** plus the
    on-host guard command (hostname + `/etc/vbpub/server-id`), prefixed to
    every remote action.
  - fleetd never holds provider credentials.
- **Contacted hosts are only those enrolled** into fleetd's inventory whose
  facts show `HOST_ROLE=dedicated-dev`. A `shared-prod` host is refused by
  name. This replaces `ALLOWED_HOSTS`.
- **Transport is SSH as `runner`** to a **forced-command entry point**
  (§6.1). Bundles are imported by `sync-from-devcontainer --no-current-link`,
  and long processes start through `run-job`.
- **Later:** v8.2 `ciu ssh`/push may replace the adapter (`run`, `put`,
  `get`). Inventory and keys already come from ciu.

### 3.6 The per-host guard: extend cgprofile, ordered below mdt-psi-guard

**Why extend cgprofile:**

- it already holds the per-lane sessions, the PSI snapshot, the placement
  leaves and the explicit-policy `cgroup.kill` path, and runs in a protected
  unit;
- a second root daemon would race it over the same cgroups.

**D-651 Q4 / ciu `SPEC-V8.md` S21.4.1.** The recorded decision is that the
cgroup-profiler daemon "measures and serves data and decides nothing". A guard
that chooses which session to park is a decision. This design proposes an
**amendment** (§7.2 Q1):

- the guard **executes a rule table it is given**;
- it never invents policy.

| Input | Source |
|---|---|
| Thresholds, hold times, max park time, thaw conditions | the **guard policy file** `/etc/cgprofile/guard.toml` (root-owned), rendered by fleetd per host and installed at provisioning or by the operator. Its digest is reported in the facts, so fleetd sees drift. |
| Which session to act on | the **rank** fleetd assigns to each lease (class, age, measured-over-expected), passed by Run-Gate at session `start` (`--shed freeze\|stop\|never`, `--shed-rank`, `--lease`) |
| Lease validity | the host lease records (§3.9) |

The guard applies `freeze`, `reclaim`, `thaw` and `kill` (self-fencing) only
as the table says, and logs each action with the rule that fired.

**Ordering with mdt-psi-guard.** psi-guard watches protected units' memory
`some` > 10% and host memory `full` > 5%, held 10 s. In `enforce` it throttles
`dev-gates`, the **parent** of every lane cgroup, so it affects all lanes at
once. The lane guard must therefore act **earlier and more selectively**:

| Order | Signal (defaults in `guard.toml`) | Actor | Writes |
|---|---|---|---|
| 1 | `dev-gates.slice` memory `some` avg10 > 10 for 20 s, or host memory `full` avg10 > 2 for 10 s, or swap-in > policy rate | **lane guard** | park one lane (its container cgroup: `cgroup.freeze`, `memory.reclaim`) |
| 2 | protected-unit memory `some` > 10%, or host memory `full` > 5%, for 10 s | **mdt-psi-guard** (backstop) | `dev-gates`/`dev-background` weights, then `MemoryHigh` |
| — | host memory/IO PSI some avg10 > 25 | **fleetd** admission | no new dispatch (the AGENTS.md rule) |

- **They are not independent**, contrary to rev 2. psi-guard's parent
  `MemoryHigh` reclaims from every leaf, frozen or running.
- Rules:
  - the lane guard never thaws while psi-guard reports an active action;
  - psi-guard's `enforce` mode is enabled on a compute host only after the
    lane guard's `enforce` is accepted there;
  - both start in `report`;
  - neither writes the other's keys: leaf `cgroup.freeze`/`memory.reclaim`
    for the guard, tier `CPUWeight`/`IOWeight`/`MemoryHigh` for psi-guard.
- **State reported:** `cgprofilectl guard status --json` (also on the observer
  socket), probed by fleetd. Per session: lease, rank, state, frozen
  seconds, reclaimed bytes, last rule. Per host: signals, policy digest and
  psi-guard state.
- **ctlstat** reads `fleetd observe state --json` once for §G/H/I and drops
  its own per-host SSH probe (`docs/design/ctlstat/REQUIREMENTS.md` item 9).

### 3.7 Parking: freeze + `memory.reclaim` (primary), stop + resume (fallback)

**What is frozen.**

- The **lane container's own cgroup**: the Docker scope under `dev-gates.slice`,
  the highest cgroup that holds every process of the instance.
- Not the cgprofile placement leaf. That leaf holds only migrated pids, and a
  process entering through `docker exec` or created by runc lands elsewhere.
  Freeze is hierarchical in cgroup v2, so freezing the scope covers the leaf
  beneath it.

**Coexistence with Docker:**

- `docker pause` uses the same `cgroup.freeze`. The guard never calls Docker,
  and nothing else may pause lane containers.
- A frozen group still accepts `cgroup.kill` (SIGKILL is delivered on
  freeze). `docker stop` (SIGTERM) would wait until thaw, so the fallback
  path thaws first or uses `cgroup.kill`.
- `docker ps` keeps showing such a container as running. The guard's state
  is the truth.

**Steps:**

1. **Choose:** the highest rank (youngest lease of the lowest class; among
   equals, measured charge most over expected).
2. **Freeze:** write `cgroup.freeze = 1` and wait for `frozen 1` in
   `cgroup.events` (bounded wait; on timeout, fall back to stop).
3. **Reclaim** with explicit `swappiness=` (high, so anon goes to
   zswap/swap rather than dropping only file pages), in steps (for example
   256 MiB), until one of:
   - `memory.reclaim` returns `EAGAIN` (nothing more reclaimable: the
     terminal condition);
   - resident `anon + file + shmem` from `memory.stat`, minus `zswapped`,
     reaches the target;
   - the host signal releases.

   The zswap pool is charged to the cgroup, so `memory.current` alone cannot
   show progress. Read `memory.stat` (`anon`, `file`, `shmem`, `zswapped`)
   and `memory.swap.current`. Kernels without the `swappiness=` argument use
   `vm.swappiness` (the facts say which).
4. **Account:**
   - the lease becomes `parked`;
   - its expected charge moves from `M_h` to the **parking budget** `S_h` of
     §4.2 (swap available for parking);
   - parking is only allowed while `S_h` has room, otherwise the fallback
     applies.
5. **Re-evaluate on events:**
   - an instance on the host finishes or releases;
   - a measured or declared RAM need changes;
   - host policy changes;
   - another host has room. In that case fleetd may **move** the shard: stop
     the parked instance and re-dispatch it from its store, under a new
     generation.
6. **Thaw:**
   - one at a time, lowest rank first;
   - only with free RAM ≥ its expected charge, signals below half the
     threshold, and no active psi-guard action.

**Trade-off, stated: thaw-ability over saturation.** While an instance is
parked, fleetd dispatches new work on that host only if its expected charge
fits `M_h − parked expected charge`, keeping room to thaw. CPU may therefore
sit idle for a while. That idle time is bounded by `max_park_seconds`
(default 2 h, after which the park becomes stop + resume, possibly
elsewhere).

**Wall-clock deadlines of a parked group:**

| Deadline | Handling |
|---|---|
| Candidate timeout (`auto_budget_per_candidate_seconds`), test-internal timeouts | `CLOCK_MONOTONIC` keeps running while frozen, so the **invalidation rule** applies: cgprofile adds `frozen_seconds` to each observer measurement window and session summary, and Assay treats a candidate whose window has `frozen_seconds > 0` as **not finished**. Its outcome is discarded and it is re-queued. |
| Run-Gate lane `budget` | elapsed budget time excludes the session's `frozen_seconds`; exit 4 stays resumable |
| cgprofile liveness (`idle-bound`, `ceiling`, `--on-stall kill`) | paused while frozen |
| Assay campaign deadline (`campaign init --hours`) | extended by the parked duration and recorded |
| Supervisor stall counter, lease renewal | a park is not a stall; the lease shows `parked` and keeps renewing |

**Fallback, stop + resume from store,** applies when any of these holds:

- the lane is `--shed stop`;
- `S_h` is exhausted;
- `max_park_seconds` has passed;
- fleetd moves the shard;
- the host drains;
- WP7 has not shipped yet.

Steps: `cgroup.kill` (or thaw + SIGTERM through the supervisor). Finished
candidates stay in the store, in-flight ones are lost, and the lease returns
to fleetd.

| | park (freeze + reclaim) | stop + resume |
|---|---|---|
| Lost work | in-flight candidates | in-flight candidates + one container start (+ host baseline if not cached, §3.10) |
| RAM freed | the reclaimed share (zswap keeps a compressed part charged) | all, at once |
| Resumes | on the same host after an event | on any host with room |
| Needs | WP7 (invalidation, deadlines, guard enforce) | nothing new |

### 3.8 The controller host: no fleet execution in the MVP

The review found that a `light-run` pool would let any `fleet-control` member
run a bundle-defined lane command on the production-adjacent host through
fleetd's Docker access. That is a different risk class from a test host.

- **MVP:**
  - fleetd has **no Docker access** and runs **nothing** on the controller
    host;
  - the operator's "small lanes only" is met by the allocator **math only**:
    `plan` can show what would fit;
  - controller gate runs stay with the existing two-slot gate wrapper.
- **Later (WP9, separately approved), a locked-down runner:**
  - a separate group `fleet-controller` on a third socket;
  - an operator-maintained **allow-list of lane ids** with their **image
    digests** in fleetd's state. The request names an allow-listed lane; the
    command and image come from the allow-list, never from the client
    bundle;
  - no client-defined Docker flags, `--network none`, a read-only root
    filesystem, no host mounts except the per-run work directory, no Docker
    socket;
  - cap ≤ 2 GiB, one lane at a time, `dev-gates.slice` only;
  - stop-only shedding;
  - admission requires host memory PSI some ≤ 10.
  - The devcontainer anon "hold line" (5.0 GB) and the two gate slots are
    **controller-local policy** (release-program rules, not repository
    facts), so the runner reads them from fleetd's policy, not from code.

### 3.9 Lease and fencing protocol

**Identity of a dispatch:**

- `(campaign, shard, generation)`, where `generation = (epoch, n)`.
- `epoch` is incremented and fsynced in `state.json` at every fleetd start,
  before the first tick, under the exclusive state lock.
- Each host stores the highest epoch it has seen in `fleet/epoch`. The entry
  point **refuses any write carrying a lower epoch**. A stale fleetd (hung
  restart, restored `state.json`, manual run) is therefore fenced by every
  host it reaches.

**Records on the host** (written only through the forced-command entry, which
takes a per-host `flock`):

| File | Written by | Meaning |
|---|---|---|
| `fleet/dispatch/<campaign>.<shard>.json` | fleetd (`dispatch`) | **intent:** counts as a claim from the moment it exists. It carries the generation, `ttl_s`, `start_grace_s`, and the expected charge and rank. Re-writing the same generation is a no-op (idempotent). |
| `fleet/leases/<campaign>.<shard>.json` | the host supervisor | the **lease**: generation, `boot_id`, `renewed_at_boottime_s`, `ttl_s`, state (`running`/`parked`/`done`). It is renewed every 60 s. |
| `fleet/revoked/<campaign>.<shard>` | fleetd (`revoke`) | the revoked generation |

**Clock rule.**

- Every expiry comparison uses **the host's own `CLOCK_BOOTTIME`** together
  with its `boot_id`. Durations (`ttl_s`, grace) are issued by fleetd as
  seconds.
- The host's probe returns its own "now", and fleetd compares host values
  only with that probe's now. It never compares a host time with its own
  clock.
- fleetd measures **unreachability with its own monotonic clock** (time since
  the last successful probe).
- A changed `boot_id` means every lease on that host expired at the reboot.

**Rules:**

1. **Dispatch.**
   - fleetd writes the intent, then starts `supervise` through `run-job`
     named `<campaign>.<shard>.g<epoch>-<n>`. run-job refuses a duplicate
     name, so a retried dispatch cannot start twice.
   - An intent without a lease after `start_grace_s` (default 180 s) is
     **withdrawn on the host** (intent removed, verified that no supervisor of
     that generation is alive) before the shard is dispatched anywhere else.
   - While the host is unreachable, an intent counts as a live claim.
2. **Renewal fencing.**
   - At **every** renewal (60 s) the supervisor checks: no newer revocation,
     the intent's generation equals its own, and its epoch ≥ `fleet/epoch`.
   - On a mismatch it stops its attempt at once (thaw if parked, then SIGTERM
     through Run-Gate, `cgroup.kill` after 30 s).
   - An attempt can therefore run at most one renewal interval past a
     revocation it can see.
3. **Self-fencing by the guard.**
   - When a lane session's lease record is older than `ttl_s` by the host
     clock (dead supervisor, orphaned container), the guard kills the
     session's cgroup.
   - This is a rule from `guard.toml`, not a guard decision.
4. **Partitioned host (controller reachable from nowhere, host alive).**
   - Supervisors keep renewing, so work continues through a controller
     outage.
   - After `loss_grace` (default 30 min of unreachability) fleetd marks the
     shard `lost` and re-dispatches it elsewhere under a new generation.
   - On reconnection, the host sees the revocation and stops the old
     generation (rule 2).
   - To bound duplicate work during long partitions, a supervisor stops
     renewing after `contact_ttl` (default 2 h) without any fleetd contact.
     Its lease then expires, and the guard fences it (rule 3).
5. **Merge accepts only the current generation's store.** An old generation's
   outcomes are never merged.
   - Duplicate execution therefore costs CPU but never changes a verdict,
     even for timeout-bucket outcomes.
   - Rev 2's claim that "results are judge-deterministic" is withdrawn as
     the justification.
6. **Re-placement on the same host.**
   - The new generation uses a fresh shard store directory seeded from the
     last synced copy.
   - Assay's exclusive store `flock` and the supervisor's per-shard lock
     guarantee one writer. The new generation starts only after the entry
     point confirms that no process of the old generation is alive.
7. **Precedence.**
   - **Hosts win** for execution facts: what runs, progress, store contents,
     renewals.
   - **fleetd wins** for intent: cancel, drain, priority, revocations,
     campaign definitions.
   - Intents for an unreachable host wait in a per-host **outbox** in
     `state.json` and are applied at the next contact, before any new
     dispatch.

**Controller restart.**

- systemd restarts fleetd. It takes the lock, increments the epoch, loads
  `state.json`, and probes every host.
- Live leases of a known generation are adopted, expired ones are released,
  and intents past their grace are withdrawn.
- If `state.json` is lost, campaigns are rebuilt from host dispatch and lease
  records plus `results/`. Never-dispatched requests are lost, raise ledger
  "missing" attention, and are re-submitted.

### 3.10 Preparation phase: overlap without changing judgment

**The constraint:**

- Assay derives each candidate's timeout from the baseline measured **on this
  host at run time**: `max(3 × baseline, baseline + 60 s)`
  (`assay/src/assay/mutation.py:4685`). The same wall time feeds liveness
  phases.
- Starting candidates before the host's own baseline exists, or using another
  host's baseline, would change timeout buckets and break the per-flavor
  rule.
- Rev 2's speculative start and shared cross-host baseline are therefore
  **withdrawn**.

**Today:** each instance and each re-attempt repeats:

- workspace setup;
- plan building;
- the full baseline;
- then candidates.

Instances on one host start their baselines together, which is why the runner
staggers them.

**Rev 3 design:**

1. **Overlap only what does not affect judgment.** While the host-local
   baseline runs, the instance:
   - builds its plan (deterministic, checked against the campaign's plan
     digest);
   - creates the CoW workspaces;
   - checks tools and images;
   - **stages the first candidate workspace per worker** (clone + mutation
     applied).

   During execution, it stages the **next** candidate's workspace per worker
   and the **next shard's** workspace setup ahead of time. Candidate execution
   starts only when the baseline result exists on this host.
2. **One baseline per host per campaign, reused locally.**
   - The first instance on a host measures the baseline and writes a
     **host-local baseline record** keyed by:
     - commit;
     - `judge_sha256`;
     - tester image digest;
     - the host's `flavor.id`;
     - `boot_id`.

     The record holds the baseline outcomes, the wall time and the derived
     per-candidate budget.
   - Further instances, re-attempts and next shards **on the same host and
     boot** reuse it. A key mismatch (new boot, new image, new judge) forces
     a new measurement.
   - Other instances on that host start their setup and staging in parallel
     and wait only for this record, which replaces today's stagger.
   - Baselines per campaign drop from about K × A + 1 to about **H** (the
     number of hosts used), plus one per reboot.
3. **Provenance.**
   - The baseline wall time and derived budget are written into every
     record's provenance.
   - They are **resource facts, not judge identity**, consistent with B529:
     resources are not identity. The cache key makes a reused baseline valid
     only where it was measured.
   - Records judged on different hosts carry different budgets, which is
     already true of today's cross-host shards. The strict merge is
     unchanged.

**Rejected:** a conservative provisional timeout for speculative candidates.
Every `hung` outcome would have to be re-judged after the real baseline, and
correctness would then depend on that re-judging. This adds complexity for a
gain of about one baseline per host.

**What changes in Assay (WP13):**

| Change | Detail |
|---|---|
| `assay run --baseline-cache DIR` | read/write the host-local record and validate every key, refusing a mismatch by name |
| `assay prepare --stage-only` | plan + workspace setup without execution, so it can run in parallel |
| one-ahead staging | per worker, bounded by disk and the in-instance memory budget |
| provenance | baseline wall time + budget in each record |

Run-Gate passes the cache dir (host-local
`.run-gate/assay-baseline/<project>/<lane>/`), and fleetd orders the first
instance per host.

## 4. Lane demand

### 4.1 Schema (`[lanes.<lane>.demand]`; v8: `[testing.lanes.<lane>.demand]`)

```toml
[lanes.r2.resources]              # EXISTING: per-container caps = runaway bounds, not demand
memory = "3g"
cpus = "3"

[lanes.r2.demand]                 # NEW (RG-275); estimates, measured per-flavor fits win
unit = "candidate"                # candidate | worker | run
instance_memory = "250m"          # fixed cgroup charge per instance above its units
unit_memory = "120m"              # cgroup charge per running unit incl. tmpfs/cache (default: candidate_ram)
unit_cpu = 0.6                    # busy cores per running unit
unit_seconds = 95                 # cold-start wall seconds per unit
freezable = false                 # non-campaign lanes only
# efficient upper bound per instance = Assay judge.mutation.jobs_max / workers_max (not duplicated)
```

- **Removed from the operator's and lane's hands:** fixed campaign
  `jobs = N`, the runner's `--instances/--shard-*`, and NC-48's
  `R2_*_MEM_MIB`/`R2_OVERCOMMIT`/`R2_HOST_RESERVE_MIB`.
- **`jobs_max` means the efficient upper bound.** A badly scaling lane (for
  example PWMCP on one CPU) states a small bound and gets more instances.

### 4.2 Allocator math (fleetd; `plan` exposes it)

For each eligible host h (enrolled, `dedicated-dev`, readiness not FAIL,
floors met, image digest equal, memory/IO PSI some ≤ 25, no guard action in
the last 10 min):

```text
C_h = cpu.for_lanes × oversubscribe_max − Σ leased cpu
M_h = memory.for_lanes × 1.5 − max(Σ leased expected charge (running), gates.current − Σ parked resident)
S_h = swap_for_parking − Σ parked expected charge         (parking budget, §3.7)
f   = flavor.id(h); every fit below is for f
cpu(j) = j × c_f   mem(j) = O_f + j × U_f   rate(j) = j × E_f(j) / unit_seconds_f
   no fit for f → declared demand, E = 1, ONE calibration shard first, ETA "uncalibrated"
per host, over j = J..1 with E_f(j) ≥ 0.8:
   k(j) = min(floor(C_h / cpu(j)), floor((M_h − thaw_reserve_h) / mem(j)), max_lane_containers − running)
   thaw_reserve_h = Σ expected charge of instances parked on h
   pick the j maximising k(j) × rate(j); ties → larger j
shards N = Σ_h k_h × 2, each ≥ 40 units;  ETA per flavor = units_f / Σ rate_f  + one shard's tail
```

**Worked example** (unchanged arithmetic, verified by the review). A 4-core
host with 6 GiB for lanes: M = 9 GiB, C = 5. O = 190 MiB, U = 80 MiB,
c = 0.35, J = 3, E(3) = 0.95. That gives j = 3, k = 4, and **4 instances x 3
workers**: 1.7 GiB expected, about 4.2 cores.

### 4.3 Overcommit and KSM

- Expected **charges** are summed against `memory.for_lanes × 1.5` (host
  policy, default 1.5). Caps may overcommit.
- **Safety net, in order:**
  1. Assay's in-instance admission;
  2. the per-container cap;
  3. lane-guard parking;
  4. mdt-psi-guard;
  5. the slice `MemoryMax`.
- **KSM** is host policy and state in the facts:
  - ksmd on, advisor CPU limit about 5-10% of one core;
  - the opt-in shim from ciu, and Run-Gate by default once job ksm-default2
    lands.
  - It is never a savings term. Sharing can only lower the **measured** O_f
    and U_f.

### 4.4 What "3 x 1 ≈ 1 x 3 in RAM" needs

- RAM = k × O + k × j × U. They are equal only when O ≪ U. With the fit
  above, 3 x 1 = 810 MiB against 1 x 3 = 430 MiB.
- §3.10 removes the repeated baseline. The judge and container per instance
  remain.
- The scheduler prefers wider instances while E(j) holds.

### 4.5 Learning, per hardware flavor

- **Keys and pooling.** Fits are keyed by (lane, `flavor.id`). Samples are
  never pooled across flavors, and no benchmark conversion is used for
  placement.
- **Memory fits use the cgroup charge, not RSS:**
  - O_f and U_f come from per-instance container `memory_peak_bytes` at
    different j, P(j) = O + j·U. That includes tmpfs/shmem work trees and page
    cache, the load that RSS misses.
  - Exclusive-cgroup per-candidate `memory_peak_bytes`, where available,
    refines U_f.
  - RSS p95 is used only inside one instance, by Assay's admission.
- **Other fits:**
  - c_f = p50 `cpu_seconds/wall` from `profile`, floor 0.25;
  - E_f(j) from candidates per minute;
  - `unit_seconds_f` = p50 `elapsed_seconds`.
- **Where fits live.** All fits are fleetd state, outside the repository.
  - The repository keeps declared demand and the footprint manifest (in v8,
    `ciu.footprint.json`).
  - AGENTS.md's reference-score "rigor evidence" summary stays a release
    **reporting** artefact.

## 5. Migration and work packages

### 5.1 Compatibility and the two live campaigns

- The two live campaigns (PWMCP on host A, ciu on host B) finish under the
  legacy script.
- The new supervisor uses the same lock and `.active` paths, so it refuses a
  base the legacy script holds, and the reverse.
- fleetd shows legacy campaigns as `unmanaged` and counts their measured
  charge as occupied.
- `run-r2-campaign.sh` becomes a wrapper for one release, then is deprecated.
- `remote-gate.py` and `host-sat-queue.sh` keep their own keys and host table
  until they submit through fleetd (WP5). Then their keys are **revoked**
  (`authorized_keys` line removed with `ciu host enroll --revoke-old` or by
  hand) and their host table is deleted. **Private keys are never copied**
  into fleetd; fleetd enrolls its own.

### 5.2 Work packages

| WP | Scope | Owner | Tests | Risk | Depends |
|---|---|---|---|---|---|
| **WP0** | Schemas: `host-facts/1`, `work-request/1`, `fleet-dispatch/1`, `fleet-lease/1`, `guard-policy/1`, with example fixtures | fleetd (new) | schema validation tests; golden fixtures | low | — |
| **WP1** | (a) facts collector CLI (runner-side, read-only); (b) cgprofile guard in **report** mode, reading `guard.toml` | fleetd, cgprofile | `/proc`, `/sys`, env fixtures; decision table on recorded PSI traces | low | WP0 |
| **WP2** | `fleet plan` as a **library + CLI** over facts files (the allocator §4.2) + `run-gate campaign plan` emitting `work-request/1` and the legacy runner commands | fleetd, run-gate (release needed) | properties: never above C/M/J, monotonic; flavor isolation; hand mutants | low | WP0 |
| **WP3** | fleetd daemon skeleton: lock + epoch, `state.json`, socket-activated observe/control sockets, `fleetd install` (unit hardening, groups, endpoint fragment), inventory from fleetd's ciu rows, probe tick; new cmru project + gate | fleetd | socket gating (both levels, GIDs distinct), single-instance lock, restart rebuild from fake host records | medium | WP1(a), WP2, **MDT-086** (client delivery) |
| **WP4** | host entry point (forced command) + `run-gate campaign supervise` (port with renewal fencing) + `run-r2-campaign.sh` wrapper; `ciu host enroll` gains `--restrict` and `--forced-command` | run-gate, fleetd, ciu, netcup | supervisor parity cases from `scripts/netcup/tests/test_run_r2_campaign.py`; lock interop; epoch refusal; renewal-time stop | medium | WP0 |
| **WP5** | leases, dispatch intents, outbox, submit/cancel/fetch, store sync, merge placement, carry-over distribution, T3 ledger; remote-gate/host-sat queue migrate, old keys revoked | fleetd, run-gate, netcup | fake-transport campaign; crash at each tick step is idempotent; intent grace | medium | WP3, WP4 |
| **WP6** | host loss, partition, contact TTL, same-host re-placement, `state.json` loss | fleetd | chaos with the fake transport; skewed host clocks | medium | WP5 |
| **WP7** | guard **enforce**: park/thaw, self-fencing, the invalidation rule (`frozen_seconds`), deadline handling, ordering with psi-guard | cgprofile, assay, run-gate | trace replays; a frozen candidate is never final; a session over TTL is killed; a host rehearsal | medium-high | WP1(b), WP5, **§7.2 Q1** |
| **WP8** | profiled host runs, per-flavor fits from cgroup charge, calibration shard, prediction error | run-gate, fleetd | fit math; flavor isolation | low | WP3, WP5 |
| **WP9** | controller-host locked-down runner (§3.8) | fleetd | allow-list enforcement; no client flags reach Docker | **high** | WP5, **§7.2 Q4** |
| **WP10** | ctlstat reads fleet state | ctlstat | goldens | low | WP3 |
| **WP11** | ciu v8 client (`ciu gate --campaign`), demand under `[testing.lanes]`, warm-set charge use | ciu8 | WP2/WP5 client parity | medium | ciu8 checkpoint D (v8.1 for warm sets) |
| **WP12** | move generic runner scripts out of `scripts/netcup/runner/` (NC-52) | netcup | path/doc contract tests | low | WP5 |
| **WP13** | host-local baseline cache, `prepare --stage-only`, one-ahead staging (§3.10) | assay, run-gate | key mismatch refused; budget provenance; baseline count = hosts + reboots | medium | WP4 |

**First stage, realistic in a week:** WP0 + WP1(a) + WP2.

- These are schemas, a facts collector and `plan` as a library and CLI. They
  need no daemon, no MDT-086, no host change and no new socket.
- The operator gets the placement answer and the legacy commands.
- The daemon (WP3) follows once MDT-086 delivers endpoint variables to
  devcontainers and the `fleetd` cmru project and gate exist.
- WP4 and WP13 can run in parallel.

## 6. Failure modes and security

### 6.1 Key custody

- **Where the keys are.** One key pair **per host** (ciu enrollment is per
  row). The keys sit in fleetd's XDG state (`~fleetd/.local/state/ciu/ssh/<alias>/`,
  dir 0700, key 0600, owner `fleetd`).
  - Readable by `fleetd` and root only. fleetd's unit uses `ProtectHome` with
    an explicit read-write path for its state, so no other unit and no
    devcontainer can read them.
  - No socket verb returns key material. Keys are not in the repository and
    not in `state.json`.
- **Host side `authorized_keys`** (runner account):
  - `restrict,from="<controller address pattern>",command="<runner home>/fleet/bin/fleet-host-entry" ssh-ed25519 … fleetd@<controller>:<alias>`;
  - `restrict` removes forwarding, pty and agent;
  - the forced command accepts only `probe | import | dispatch | revoke |
    fetch | stop | withdraw` with validated arguments, and runs the on-host
    guard first.
  - ciu enrollment writes `from=` today (whitespace before the key type,
    `ciu/docs/CIU-HOST-ENROLLMENT-PROPOSAL.md:4`). `restrict` and `command=`
    need the small WP4 extension.
- **Blast radius, stated.**
  - `runner` is in the `docker` group, so code execution as `runner` is root
    on that compute host.
  - The forced command narrows what a stolen key can do through SSH, but a
    compromised **controller host** yields every enrolled compute host. It
    never yields the controller host itself through fleetd, which has no
    Docker group there.
  - Compute hosts must therefore hold no secrets beyond their test duties.
- **Rotation:**
  - `ciu host enroll --replace` creates a pending key generation, proves it on
    the host, switches atomically, and keeps the old one until
    `--revoke-old`, which removes the old line.
  - Default cadence: on operator demand and **immediately** on suspected
    controller compromise. There is no timed rotation, because keys never
    leave the controller.
- **Revocation of a host:**
  1. `fleetctl drain`;
  2. leases expire or are revoked;
  3. remove the `authorized_keys` line (with the key through
     `--revoke-old`, or at the console);
  4. delete the inventory row.
- **Host reinstall** (the operator reinstalls at will):
  - the new host key fails the pin, and fleetd marks the host
    `identity-changed`;
  - fleetd stops dispatching, treats all of the host's leases as lost
    (shards re-placed from synced stores), and keeps per-flavor fits (they are
    flavor-keyed, not host-keyed);
  - the operator runs the provider identity check, then re-enrolls with
    fingerprint confirmation (`ciu host enroll`) and runs `undrain`.
- **`state.json`, `results/`, spool:**
  - owner `fleetd`, mode 0600/0700, not readable by client groups (results go
    out only through `fetch`);
  - **not backed up by design**: they are rebuilt from host records (§3.9);
  - addresses live only in fleetd's ciu inventory, not in `state.json`.
- **Provider credentials:** none in fleetd (§3.5).

### 6.2 Access and the controller host

- `fleet-control` membership equals "may run committed lanes of any submitted
  bundle on every compute host". Treat it as Docker-group-equivalent on the
  **compute hosts**.
- It grants **nothing** on the controller host in the MVP (§3.8): fleetd has
  no Docker access there and runs no work there.
- The later controller pool needs the separate `fleet-controller` group and
  the allow-list.
- Granting groups is the operator's authorization step, done through
  devcontainer `--group-add`.

### 6.3 Failure modes

| Failure | Behaviour |
|---|---|
| fleetd crash / controller restart | systemd restarts it; it takes the lock, bumps the epoch, reconciles (§3.9). Hosts continue meanwhile. |
| Two fleetd instances | the second fails the state-dir `flock`; a stale one elsewhere is fenced by the hosts' epoch check |
| `state.json` lost | rebuilt from host dispatch/lease records + `results/` |
| Host unreachable | intents and leases count as claims; after `loss_grace` its shards are re-placed under a new generation; after `contact_ttl` the host stops itself |
| Partition heals | the old generation stops at its next renewal; only the current generation's store merges |
| Dispatch during host start-up | the intent is a claim; withdrawn on the host before any re-dispatch |
| Supervisor dead, container alive | the guard self-fences after `ttl_s` by the host clock |
| Clock skew | no cross-host comparison; host boottime + `boot_id` only |
| Freeze changes a result | prevented by the invalidation rule + deadline table; stop + resume until WP7 |
| Parked forever | events re-evaluate; `max_park_seconds` converts the park to stop + resume |
| Lane guard vs psi-guard | ordered thresholds (§3.6); no thaw during a psi-guard action; separate keys |
| Wrong rate from another flavor | impossible: fits are keyed by `flavor.id`; a calibration shard first |
| Undercounted memory (tmpfs/cache) | fits use the cgroup charge (§4.5) |
| Host reinstalled | `identity-changed`, leases lost, re-enroll (§6.1) |
| Runaway candidate | per-container cap → `crashed` |
| Disk full | `checkout_disk_free` in facts; no dispatch below the threshold |

## 7. Operator decisions and open questions

### 7.1 Decisions taken (inputs, not reopened)

1. Only host installation is provider-specific; any compute host must work.
2. Option 3: a central scheduler/distribution service on the controller host,
   handed hosts after installation. Running happens through Run-Gate.
3. No authentication in the service. Sockets only, read and write separate,
   by group.
4. Host capabilities and state live outside the repository with that service.
   Policy stays on each host.
5. Lanes state an efficient bound, resource need and wall time, never workers
   per instance.
6. Overcommit 1.5 for expected peaks, with PSI monitored.
7. Controller host: small lanes only (the MVP fulfils this with math only;
   see Q4).
8. A small guard per compute host, in a protected cgroup. The scheduler runs
   the tick loop.
9. Shedding is freeze + `memory.reclaim`: park until something finishes or the
   RAM need changes.
10. Hardware flavors are not comparable; numbers are per flavor.
11. Preparation runs in parallel with execution (rev 3: only the
    non-judgment parts, §3.10).
12. KSM is host policy (ciu shim, Run-Gate default, ksmd about 5-10% of one
    core).
13. Standing rules: CPU overcommit; only memory/IO PSI > 25 or heavy swap stop
    new work; tests are load-independent; limits are host-owned.

### 7.2 Remaining open questions (recommended default first)

1. **Amend D-651 Q4 / ciu `SPEC-V8.md` S21.4.1** so cgprofile may **execute**
   a scheduler-given guard rule table (park/thaw/self-fence), while still
   deciding no policy itself? *Default: yes, as worded in §3.6.* Without it,
   the guard must be a separate daemon.
2. **Parking limits:** discard and re-execute the in-flight candidates of a
   parked instance, and convert a park longer than 2 h into stop + resume?
   *Default: yes.*
3. **Lane-guard thresholds below psi-guard's** (§3.6 table), with psi-guard
   `enforce` on compute hosts only after the lane guard's `enforce` is
   accepted? *Default: yes.*
4. **Controller-host execution:** drop it from the MVP and build the
   locked-down allow-list runner (WP9) only on explicit approval? *Default:
   yes.*
5. **Service name and home:** a new cmru project `fleetd/` (working name)?
   *Default: yes.*
6. **Priority across projects:** `release-t3` > `campaign` > `light`, FIFO
   within a class, no quotas yet? *Default: yes.*
7. **Timers:** shards N = 2 × instances with at least 40 units each; store
   sync every 15 min; `loss_grace` 30 min; `contact_ttl` 2 h; lease `ttl_s`
   15 min? *Default: yes.*
8. **Forced-command entry and `restrict`** on the runner's `authorized_keys`
   (a `ciu host enroll` extension)? *Default: yes.*

## 8. Verification boundary

- This is a design. Nothing was run on a host, and no code was changed.
- References were read on `4f1ae7d70`, plus the branches `r2-guard-20261112`
  (NC-48) and `estate-req-20261112` (unmerged; marked where used).
- The rev 3 claims come from:
  - the Assay budget at `assay/src/assay/mutation.py:4685`;
  - the flat-key removal at `docs/design/RESOURCE-PROFILES.md:265` and
    `assay/src/assay/mutation.py:296-301`;
  - the psi-guard thresholds at `mdt-cockpit/host-setup/README.md:199-228`;
  - the enrollment features at `ciu/docs/CIU-HOST-ENROLLMENT-PROPOSAL.md:3-4, 46-62, 121`;
  - ciu v8 `ciu.footprint.json`, `[testing.lanes]`, S14.4.7-8 and S21.5.1
    in `ciu/docs/SPEC-V8.md`.
- Kernel interfaces are cgroup v2 features (`cgroup.freeze` 5.2+,
  `memory.reclaim` 5.19+, zswap writeback control 6.8+). The `swappiness=`
  argument of `memory.reclaim` is newer; its presence is a facts field and is
  not assumed.
- The KSM Run-Gate default (job ksm-default2) and the ksmd tuning are
  in-flight work reported by the coordinator, not verified here.
- O, U and E(j) in the example come from one observation.
