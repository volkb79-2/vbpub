# TUI-SPEC Review - judgement, product direction, and interview notes

Review target: `TUI-SPEC.md` as of 2026-07-08.

Local context checked while reviewing:

- Host: `gstammtisch`, kernel `7.0.12+deb13-amd64`.
- BPF prerequisites are present locally: `CONFIG_BPF=y`, `CONFIG_BPF_SYSCALL=y`,
  `CONFIG_CGROUP_BPF=y`, `CONFIG_BPF_JIT=y`, `CONFIG_DEBUG_INFO_BTF=y`, and
  bpffs mounted at `/sys/fs/bpf`.
- The review also sampled `CGROUP-MONITORING.md`, `MEASUREMENTS.md`,
  `MEMORY-ARCHITECTURE.md`, `OBSERVATION.md`, `plan-host-resource-governance.md`,
  and `files/usr/local/sbin/soulmask-zswap-monitor.py`.

## Executive judgement

The spec has a real product hidden inside it, not just another `top` clone. The
distinctive value is the combination of:

1. cgroup-v2-native accounting, including non-container systemd slices;
2. zswap-aware pressure analysis, especially `rf_z/s` vs. `rf_d/s`;
3. governance drift detection, so live kernel state is compared with the owner of
   that state, usually systemd;
4. DAMON integration for working-set classification;
5. enough Docker and process metadata to turn kernel paths into operator-visible
   causes.

That is a stronger thesis than "replace top". The product should be described as:

> a host pressure inspector and cgroup forensics TUI that is fast enough to use
> first, with enough process, IO, network, Docker, zswap, and DAMON context to
> avoid switching tools during the first diagnosis pass.

It can replace the first 60-90 seconds of `top`, `iostat`, `docker stats`,
`ctop`, `ip -s`, `ss`, and manual cgroup grepping. It should not promise to
replace every specialized workflow those tools support.

The current spec is technically rich but too large for a credible v1. It mixes a
minimum viable operator console, a forensics workbench, a DAMON controller, a file
browser, a Docker admin panel, a record/replay system, optional GPU/ZFS plugins,
and future daemon/web architecture. The right move is not to delete those ideas,
but to separate the shipping path from the vision.

## Strong parts to keep

- The row identity is correct: cgroup path first, Docker metadata as enrichment.
  This is the only way to see bare slices such as `soulmask-paks.slice`.
- The zswap math is the core differentiator. Generalizing the existing monitor's
  `z_pool`, `z_eq`, ratio, `rf_z/s`, `rf_d/s`, and `rf_f/s` to all cgroups is the
  feature most other tools do not have.
- The "origin of limits" feature is worth building. Finding-D-class drift is a
  real operator failure mode, and showing the live value without showing who owns
  it is incomplete.
- The spec's degradation posture is right: missing kernel files should produce
  unavailable fields, not zeroes and not crashes.
- Record/replay through the same model is the right architecture. It makes bug
  reports, tuning sessions, and future daemon work much easier.
- The DAMON passive/controlled split is well chosen. Passive reading must never
  mutate someone else's DAMON session.

## Major concerns

### 1. v1 scope is too broad

The current v1 includes:

- full Textual TUI;
- adaptive table;
- full cgroup tree and Docker join;
- origin detection;
- process drill-down;
- Docker volumes, overlay layer browser, log tailing, and content browser;
- ring-buffer history and charts;
- record/replay;
- DAMON passive and active control;
- paddr host DAMON mode;
- non-root mode;
- optional GPU and ZFS plugins;
- a large hotkey system.

That is not a v1. It is a multi-release roadmap. A practical v1 should be:

1. read-only TUI;
2. host banner with pressure verdict;
3. cgroup tree and container projection;
4. core memory/zswap/CPU/PSI/IO columns;
5. network columns from the netns approximation, clearly source-labelled;
6. Docker join and friendly names;
7. process drill-down;
8. origin/drift detection for memory and CPU/IO controls;
9. JSONL record and replay;
10. graceful non-root mode.

Move these out of v1:

- DAMON control-stage writes;
- paddr auto-start;
- content browser over host paths;
- Docker update/start/stop/kill actions;
- GPU and ZFS plugins;
- web UI;
- overlay layer archaeology.

DAMON passive detection can stay in v1 if the implementation is small. DAMON
control should be v1.5 or v2 because it mutates kernel state and complicates the
safety story.

### 2. "Fast upfront information" needs a product shape

The spec lists many columns, but the first viewport should answer:

- Is the host healthy right now?
- If not, what resource is causing visible pressure?
- Which cgroup or process is the likely cause?
- Is the game protected, and are its limits still applied?
- Is the network path clean enough for the game?

Suggested first screen:

```text
HOST  OK|WARN|CRIT
CPU 18% usr 4% sys 0.1% steal | MEM 3.1G avail | PSI mem full 0.0 io full 0.0 cpu some 3.2
ZSWAP 1.9G pool / 5.8G stored / 3.1x / wb +0/s | DISK vda 12MB/s 25% util | NET uplink 4.2M down 0.9M up

TOP PRESSURE
1 soulmask game     mem: rf_d 0/s rf_z 18/s psi 0.0  zswap 1.8G  drift none
2 buildkit          io:  31MB/s cap hit          psi io some 8.1
3 authentik-worker  mem: high events +12         headroom 91%
```

if history data is available from daemon we could also visualize historic data (like btop) up front, show the previous 1min or so from before the tool start. 

Then the table below becomes supporting detail rather than the only interface.
This is what makes the tool feel like a replacement for multiple commands.

### 3. Metric semantics need a formal registry

The spec says branch rows sum additive child metrics while limits and PSI use the
branch cgroup's own values. That is not precise enough for cgroup v2.

For every metric, the implementation needs a semantic tag:

- `local`: value belongs only to that cgroup;
- `subtree`: kernel already includes descendants;
- `counter`: monotonically increasing, rate requires reset handling;
- `gauge`: instantaneous value;
- `derived`: computed from several fields;
- `aggregatable`: can be summed across children;
- `non_aggregatable`: should never be summed;
- `source_confidence`: exact, estimated, netns-approximation, unavailable.

Without this registry, the UI can accidentally double-count memory or IO on branch
rows, especially if it mixes parent cgroup files with child sums. Branch display
should explicitly choose one of:

- kernel subtree value from the branch cgroup file;
- local-only value if the kernel exposes one;
- userspace aggregate of selected descendants.

The table header should show which mode is being used. This matters for
trustworthiness.

### 4. The content browser is read-only but still sensitive

The spec treats the file browser as safe because it does not mutate. Read-only root
access can still expose secrets, tokens, environment files, mounted volumes, and
container logs. It should not be in the read-only core by default.

Recommendation:

- move content browsing to v2;
- require explicit `--inspect-files` or a config flag;
- show source paths and metadata in v1, but do not browse arbitrary host files;
- allow log path discovery in v1, but tailing logs should be opt-in.

### 5. The tool needs a diagnostics engine, not just columns

Columns are necessary but not enough. The unique value is interpretation. Add a
small rule engine that emits "findings" from metrics:

- `rf_d/s > 0 on protected game`: cold tail is touching disk; check writeback and
  memory.min.
- `rf_f/s sustained on game`: file cache is too small; do not lower swappiness.
- `memory.events high rising`: `memory.high` is actively throttling this cgroup.
- `memory.current > memory.high` with PSI memory full: reclaim is user-visible.
- `io.pressure full high` plus capped `io.max`: expected throttling, not a bug.
- systemd-recorded limit differs from sysfs: drift/raw write risk.
- `sock` memory rising with network pps: socket buffers are material.
- network source is `host-netns`: per-row network number is intentionally absent.

This should render as a short "why this row is red" panel. It turns kernel
metrics into operator action.

## Recommended release split

### v0 - collector proof

Goal: prove that the data model is correct before investing in UI polish.

- CLI `--once --json`.
- Cgroup tree walk.
- Docker join.
- Core zswap/refault formulas.
- CPU, PSI, IO, pids.
- Host banner facts.
- Metric registry with source and semantics.
- Reset handling.

No Textual dependency required yet.

### v1 - fast read-only TUI

Goal: daily replacement for first-pass triage.

- Textual table/tree.
- Host pressure banner.
- Cgroup and container views.
- Core columns and adaptive profiles.
- Process drill-down.
- Origin/drift detection.
- Netns-based network columns, source-labelled.
- JSONL record/replay.
- Non-root degraded mode.

Default policy from the interview: v1 is read-only. It may collect, display,
record, and replay; it should not start DAMON sessions, mutate cgroups, restart
containers, or alter BPF state.

### v1.5 - DAMON analysis

Goal: working-set inspection without destabilizing v1.

- Passive DAMON session detection.
- DAMON hot/warm/cold columns.
- DAMON detail panel.
- Optional controlled vaddr session behind root and explicit confirmation.

### v2 - active governance and eBPF network accounting

Goal: close the largest blind spots.

- eBPF per-cgroup network accounting.
- Admin actions behind explicit gating.
- File/log browser behind explicit inspection flag.
- Optional daemon so BPF/DAMON/root state is owned by one process.

The tool should stay game-agnostic for public use. In docs and UI, prefer terms
such as "latency-critical workload", "protected service", "management traffic",
and "best-effort workload". Host health, system behavior, and resource usage are
the public product center; the Soulmask setup is the reference deployment and
testbed, not the product identity.

## Missing idea: privileged daemon as read broker

The current spec treats the future root daemon mostly as a way to avoid duplicate
collection, own BPF/DAMON state, and retain history. It should also be specified
as a deliberate privilege boundary:

> a root-owned daemon can collect root-only data once, then expose a controlled
> read-only subset to non-root users over a local socket.

This is a major product idea. It lets ordinary operators get a high-fidelity view
without running the TUI as root and without being placed in the `docker` group.

Root-only or privileged data the daemon could broker:

- zswap debugfs counters: `written_back_pages`, reject counters,
  `decompress_fail`, pool limit hits;
- full process metadata that may be hidden by `hidepid`, ptrace restrictions, or
  procfs permissions;
- Docker/container metadata without giving the client direct Docker socket access;
- systemd origin/drift reads and, later, tightly scoped set-property actions;
- DAMON passive/control state;
- BPF maps and provider status;
- host file/log metadata if file inspection is explicitly enabled.

The security model should be explicit:

- local Unix socket, not TCP, for v1/v2;
- socket group such as `gts-top`, with mode `0660`;
- read-only API available to `gts-top` group members;
- mutating API disabled unless the daemon is started with an admin capability
  flag and the client is authorized separately;
- never expose arbitrary file reads, arbitrary command execution, or arbitrary
  sysfs writes through the daemon;
- every field in the API carries source and sensitivity metadata;
- every mutating request is logged with user identity, command/sysfs operation,
  old value, and new value;
- the non-root client must be unable to ask for "run this command as root".

This improves non-root mode substantially:

- standalone non-root TUI: degraded direct reads, no root-only fields;
- attached non-root TUI: full read-only view of daemon-approved fields;
- root/admin client: optional mutating actions, still gated and logged.

This should become a first-class architecture section in the spec, not a side
effect of the daemon.

## Network and eBPF review

The network section is directionally right: cgroup v2 has no native network
accounting controller, and `/proc/<pid>/net/dev` is a network-namespace counter,
not a cgroup counter. The spec correctly avoids pretending otherwise.

However, because this tool wants to replace first-pass network inspection too, the
network design needs to be more explicit. Network data should have three tiers:

1. host/interface truth;
2. netns approximation;
3. BPF per-cgroup accounting.

### Tier 1 - host/interface truth

Always include this in v1:

- `/proc/net/dev`: rx/tx bytes, packets, errors, drops per interface;
- `tc -s qdisc show`: fq_codel backlog, drops, overlimits if available;
- `/proc/net/softnet_stat`: host receive backlog drops/time_squeeze;
- `/proc/net/snmp` and `/proc/net/netstat`: TCP retransmits, resets, UDP errors;
- optionally `ethtool -S` where available, but do not make it required.

This catches host-level network trouble even when per-cgroup attribution is weak.
For a game host, "host network unhealthy" is often enough to decide where to look.

### Tier 2 - netns approximation

The current v1 plan is acceptable if it is labelled honestly:

- Read `/proc/<pid>/net/dev` for a representative process.
- Deduplicate by `/proc/<pid>/ns/net` inode.
- Treat the value as per-network-namespace, not per-process.
- Show `n/a (host netns)` for host-network containers and bare host services.
- Do not aggregate branch cgroups from this source unless every child has a
  distinct private netns and the aggregation code can prove it.

Suggested UI source labels:

- `net:BPF` - exact cgroup BPF provider;
- `net:NS` - network namespace approximation;
- `net:HOST` - host interface only;
- `net:N/A` - not attributable.

These labels should be visible in the drill-down and optionally as a small table
glyph, because network numbers are otherwise easy to over-trust.

Implication for v1/v2 refactoring: define a network provider interface in v1 even
if only the host and netns providers exist. Each provider should emit the same
shape:

- entity key;
- rx/tx bytes and packets;
- optional protocol split;
- source label;
- confidence;
- aggregation policy;
- reason when unavailable.

Then v2 can add BPF as another provider without changing the table model,
history schema, or drill-down contract. The extra v1 work is small compared with
the cost of retrofitting source/confidence semantics into every network column
later.

### Tier 3 - custom cgroup_skb eBPF provider

Given the checked host config, a BPF backend is realistic here. It should be
designed now even if implemented later.

Recommended design:

- A root-owned network provider loads one ingress and one egress cgroup-skb
  program.
- Attach as high in the cgroup tree as possible, ideally the unified cgroup root,
  so descendants are covered without per-container attach churn.
- The BPF program increments counters and always returns allow/pass.
- Key counters by cgroup ID, direction, address family, protocol, and optionally
  a coarse port role.
- Use per-CPU maps to reduce contention on high packet rates.
- Pin programs and maps under `/sys/fs/bpf/gts-top/`.
- Userspace maps cgroup IDs back to cgroup paths during the normal cgroup tree
  walk, then aggregates leaf counters to branch rows.

Minimum map shape:

```text
key:
  cgroup_id: u64
  direction: ingress|egress
  family: ipv4|ipv6|other
  proto: tcp|udp|icmp|other

value:
  bytes: u64
  packets: u64
```

Optional later map dimensions:

- local port bucket;
- remote port bucket;
- TCP retransmit count from tracepoints;
- socket cookie for short-lived flow correlation;
- drop reason where the kernel exposes stable tracepoints.

The BPF program must not store path strings. The path mapping belongs in
userspace. Validate the exact cgroup ID to path mechanism during implementation;
do not assume string paths or Docker IDs are visible inside BPF.

### BPF limitations to document

The eBPF path is much better than netns counters, but it is not magic:

- cgroup-skb is socket/cgroup oriented, not a universal wire tap;
- it will not replace host interface counters;
- it may not account traffic generated by kernel subsystems in the way an
  operator intuitively expects;
- ARP and some non-IP traffic may be outside the useful accounting scope;
- forwarded/bridged traffic that is not associated with a local socket may need
  TC/XDP instrumentation instead;
- per-packet map updates have real cost and must be benchmarked.

The UI should therefore show BPF network accounting as "per-cgroup socket traffic",
not "all bytes physically observed on the NIC".

### Why not systemd IPAccounting as the main path

`IPAccounting=` is useful for systemd-native services and slices. It is not enough
for this product because the tool's hard cases are Docker/Wings scopes and
post-hoc cgroup discovery. Keep it as an optional provider for systemd units, but
do not build the network model around it.

### BPF ownership model

BPF should not be owned by an ephemeral TUI process. If the TUI exits badly, the
operator should not be left with uncertain pinned programs or duplicate attach
state.

Recommendation:

- v1 TUI has no BPF.
- v2 introduces either:
  - a root daemon that owns BPF programs/maps; or
  - a small root helper with explicit start/stop/status commands.
- Multiple TUI sessions read from the same provider.
- Provider status is visible: loaded, attached, map path, program IDs, attach
  point, last read, packet rate, and estimated overhead.

### BPF acceptance tests

Add a network measurement section to `MEASUREMENTS.md` before enabling BPF by
default:

1. Baseline `iperf3` or equivalent without BPF.
2. Same traffic with BPF loaded.
3. Measure throughput, CPU softirq, provider CPU, packet loss, and map read cost.
4. Test UDP-heavy traffic resembling the game server.
5. Test container churn: start/stop 50 short-lived containers and verify stale
   cgroup IDs age out.
6. Test host-netns services and prove the UI labels them correctly.
7. Test branch aggregation: several containers under `besteffort.slice` should
   sum correctly.

### Network drill-down ideas

Add a dedicated network detail screen eventually:

- host interface table with drops/errors;
- qdisc status for the uplink and Docker bridge;
- per-cgroup rx/tx bps and pps;
- UDP vs TCP split;
- TCP retransmits and resets by cgroup where BPF/tracepoint support exists;
- socket memory (`memory.stat:sock`) next to network pps;
- listen sockets from `ss -H -tunlp` mapped to cgroups;
- "game ports" profile so the Soulmask UDP ports are highlighted.

This is how the tool becomes credible as a partial replacement for network-focused
commands without pretending to be Wireshark.

Traffic priority model from the interview:

- player-facing game traffic is critical;
- other game-server traffic is high priority;
- root SSH and Wings management traffic should also be prioritized;
- dev and background traffic is best effort.

For the public tool, represent this as configurable traffic classes, not
Soulmask-specific rules. Example classes:

- `interactive_admin`: SSH, management UI/API;
- `latency_critical`: player/game UDP and other configured service ports;
- `service_control`: container runtime, Wings, orchestration;
- `background`: Docker pulls, builds, backups, telemetry.

The TUI should observe and explain these classes first. Actual prioritization via
tc/qdisc/nftables/DSCP should remain a separate future governance feature unless
measurements show starvation.

## Additional product ideas

### Pressure score

Create a single sortable `pressure` score per entity. It should not hide raw
metrics, but it helps first-pass triage.

Inputs:

- memory PSI full/some;
- IO PSI full/some;
- CPU PSI some;
- `rf_d/s`;
- `rf_f/s`;
- memory high events;
- OOM kills;
- IO cap saturation;
- network drops/retransmits if attributable.

The detail panel should show the score breakdown.

### Source confidence and explanations

Every derived or approximate number should carry source metadata:

- exact cgroup file;
- derived from exact cgroup files;
- estimated from netns;
- host-only;
- unavailable due to permissions;
- unavailable due to kernel support.

This can be compact in the UI but must be present in JSONL. It will prevent bad
automation later.

### Incident snapshots

Add a key to save a self-contained incident bundle:

- current frame;
- previous N frames from the ring buffer;
- relevant cgroup files for the selected entity;
- systemd `show` output;
- Docker inspect summary;
- BPF provider status if enabled.

This would make "what happened during the login stall?" much easier to answer and
share.

### Configurable local history

History should be configurable. A 4-hour default is a good starting point, but
operators should be able to tune retention, sampling interval, downsampling, and
whether full-resolution history stays in memory or is also recorded to disk.

Cost estimate for a 4-hour profile with 40 entities, 24 numeric series, 5-second
samples, and `float32` storage:

```text
4 h * 3600 / 5 = 2880 samples per series
40 entities * 24 metrics * 2880 samples * 4 bytes ~= 11.1 MB raw samples
```

Allowing for ring-buffer structure, entity indexes, timestamps, and Python object
overhead, a realistic in-memory budget is roughly 20-40 MB beyond the TUI
baseline if implemented carefully with arrays rather than Python float lists.

Plain full-frame JSONL on disk is much larger. A rough order-of-magnitude for 40
entities is 60-120 KB per frame, or about 170-350 MB for 4 hours at 5-second
sampling. Streaming zstd should compress that heavily, likely into the tens of
MB, because keys and shapes repeat.

Recommendation: ship v1 with configurable history and a 4-hour default profile.
Keep the in-memory ring efficient with numeric arrays; make compressed recording
an early follow-up if unattended recording is expected.

### Metric glossary from code, not prose duplication

The spec wants glossary text in the drill-down. Do that, but keep it in a structured
metric registry used by both UI and JSON schema validation. Avoid maintaining a
separate prose glossary that can drift from the code.

### Profiles by job, not just width

Column profiles should match operator questions:

- `triage`: pressure, RAM, CPU, PSI, rf_d, IO, net source;
- `memory`: zswap, anon/file/shmem, refaults, reclaim, memory events;
- `network`: rx/tx, pps, drops, retransmits, sock memory;
- `governance`: live limits, origin, drift, events;
- `damon`: hot/warm/cold, target PIDs, sample age.

Width tiers are necessary, but task profiles are how the tool feels useful.

### Framework direction

The v1 experience should be a terminal/console application. It can and should use
a library/framework if that reduces implementation cost and improves iteration
speed. The constraint is architectural, not anti-framework: the collector and
metric registry should be framework-independent so the UI can change without
rewriting the kernel/cgroup logic.

Practical options:

- Python + Textual: fastest path to a rich TUI, good tables/screens, good for
  proving the product. Startup/RSS will be higher than a compiled tool, but likely
  acceptable on this host.
- Python + curses/urwid: lower dependency surface, but more custom UI work and
  weaker table/screen ergonomics.
- Go + Bubble Tea/Lip Gloss: single binary, good public distribution story, but
  table-heavy dense UIs require care.
- Rust + ratatui: best long-term fit for low overhead, static-ish distribution,
  and future BPF integration through Rust ecosystem pieces; higher development
  cost.

Recommendation: build v0 as a framework-free collector and JSON emitter. For the
first public TUI, use whichever console framework gets a correct dense table,
drill-down screen, and key handling fastest. Textual is a reasonable default for
iteration speed; Rust ratatui is a better default if the first public artifact
must be a lean binary. Do not let framework-specific concepts leak into the
collector/model contract.

## Specific spec edits I would make

1. Add a "v1 cut line" near the top. Mark everything outside it as roadmap.
2. Add a metric semantics registry requirement before the column table.
3. Change branch row language from "sums additive children metrics" to a more
   explicit policy per metric.
4. Move content browser from v1 to v2.
5. Move DAMON control-stage start/stop from v1 to v1.5/v2 unless you explicitly
   want v1 to be root-mutating.
6. Add a first-viewport "pressure verdict" section.
7. Add network source labels and provider states.
8. Add a future BPF provider interface now, even if implementation remains deferred.
9. Add `MEASUREMENTS.md` tasks for BPF overhead and DAMON overhead.
10. Add a security section covering docker group, root read access, file browsing,
    BPF pinned objects, and command confirmation.
11. Add the root daemon as a privileged read broker for non-root clients, not only
    as a history/BPF owner.
12. Rename game-specific concepts to public, game-agnostic terms while keeping
    Soulmask as the reference deployment.

## Interview questions

These are the questions I would ask before converting the spec into an
implementation plan.

### Product priority

1. What must the first screen answer in under five seconds: host health, game
   safety, biggest offender, or governance drift?
2. Is this primarily for you over SSH, or for other operators who do not know the
   host's memory architecture?
3. Should v1 be strictly read-only, or are root-mutating DAMON controls acceptable
   in the first release?
4. Is replacing `top` more important than replacing `docker stats`, or should the
   cgroup/container view dominate?

### Network

5. Which network traffic matters most: Soulmask UDP latency, Docker pulls, reverse
   proxy traffic, database traffic, or all container traffic equally?
6. Do you need per-slice network aggregation, or is per-container attribution
   enough for v1?
7. Are flow-level details acceptable from a privacy/noise perspective, or should
   v1/v2 stay at bytes/packets/protocol only?
8. Can v2 run a root-owned daemon/helper that pins BPF programs under
   `/sys/fs/bpf/gts-top/`?
9. What packet-rate range should the BPF backend be benchmarked against on this
   host?
10. Which ports should be treated as "game-critical" in network views?

### Memory and DAMON

11. Is the main DAMON use case "size the Soulmask hot/warm set", or continuous
    live classification of multiple cgroups?
12. How much DAMON overhead is acceptable during gameplay?
13. Should paddr DAMON be a manual diagnostic mode rather than a persistent banner
    feature?

### History and storage

14. How far back do you actually need to answer "what happened": 15 minutes,
    4 hours, 24 hours, or several days?
15. Should recordings include Docker labels, environment-derived metadata, and
    paths, or should there be a privacy-reduced mode?
16. Is plain JSONL enough for v1, or do you expect unattended multi-day recording
    immediately?

### Governance and actions

17. Should the tool ever apply fixes, or only show the exact command the operator
    can run?
18. Should file/log browsing be considered safe read-only inspection, or should it
    require an explicit flag because it can expose secrets?
19. Which drift conditions are page-worthy: any raw write, only protected game
    drift, or only drift that changes effective protection?

### Implementation constraints

20. Is Python/Textual a hard requirement for v1, or just a convenient first
    implementation?
21. Should the collector be packaged as a library from day one so a future daemon
    can import it cleanly?
22. Is Debian/gstammtisch the only target at first, or should the tool handle
    Fedora/Ubuntu/systemd variation immediately?

## My answers if you want a default

Updated with operator answers from 2026-07-08:

- v1 is read-only except record/replay.
- The public product is game-agnostic: host health, system behavior, and resource
  usage are key. The game server is a latency-critical workload profile, not the
  whole product.
- Network v1 should stay simple, but with a provider abstraction and source labels
  so v2 BPF does not require table/schema refactoring.
- A v2 root daemon/helper is acceptable and should own BPF programs/maps.
- Traffic classes should prioritize player-facing game traffic first, then other
  game-server traffic, then root SSH and Wings management, with dev/background
  traffic as best effort.
- File/log browsing may have full access, but should still be an explicit mode
  because full read access can expose secrets.
- History should be configurable. A 4-hour default profile budgets roughly
  20-40 MB additional in-memory if stored as numeric arrays; plain JSONL for
  4 hours may be hundreds of MB before compression.
- v1 should be terminal/console and may use a framework if that reduces
  implementation effort and increases speed. Keep the collector independent;
  choose Textual for iteration speed or Rust ratatui for a lean public binary.
