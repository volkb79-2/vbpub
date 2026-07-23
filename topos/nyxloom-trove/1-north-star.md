---
kind: north-star
schema_version: 1
---

# topos north star

topos is the one read-only tool a sysadmin opens first for host observability and cgroup
forensics — a single fast TUI that aims to answer roughly **95% of day-to-day host-operations
questions**, instead of stitching together `htop`, `iostat`, `docker stats`, `ctop`, `ip -s`,
`ss`, `systemd-cgtop`, `damo`, and manual `/sys` greps. That ~95% is a real target, not a
slogan: it is pinned to a named operator-scenario acceptance model
(`docs/OPERATOR-QUESTIONS.md`), so coverage is measured against concrete investigations.

It shows what every cgroup on a host is doing right now — process, CPU, memory, I/O, network,
and Docker/container context — and renders the *entire* cgroup tree, so slices with no
container stay visible. It also detects when a live cgroup limit has drifted from its
systemd-declared configuration. Every value carries honest source / coverage / degradation
signaling; topos never guesses silently.

Memory is a first-class forensic pillar: topos gives the *true* memory picture across both
compression and deduplication — **KSM** (kernel same-page merging), **zram** (compressed RAM
block devices), **zswap** (compressed swap), and **ZFS ARC** (and dataset compression) —
reporting real-versus-apparent usage, per-cgroup compression and dedup ratios, and the
disk-versus-zswap refault split (`rf_d`/`rf_z`) that reveals which cgroup is faulting from real
disk rather than from cache — per cgroup and across the whole host. DAMON working-set
hot/warm/cold classification and incident snapshots build on the same core.

topos is read-only and safe by default, and it layers deliberately: a collector/model core
underlies a Textual TUI; DAMON and incident capture build on that; and a privileged read-broker
daemon, bounded administrative actions (start/stop/restart/kill/update — gated behind root, an
explicit typed confirmation, and a durable fail-closed audit), a versioned loopback read gateway,
and BPF-based exact network accounting come last. Root-owned state and every mutating action stay
in the daemon; the TUI, MCP frontend, and any future web client are thin clients over the same
bounded, versioned, redaction-enforcing read API — never a second aggregation engine, and never a
raw-runtime fallback for an owner-managed workload (systemd / Compose / CIU / Wings / Podman /
Kubernetes-owned or protected containers refuse rather than silently mutate).

topos optimizes for a coherent operator-console product over raw provider breadth: one bounded
frame-query core, honest signaling, and named operator investigations drive what gets built next.
A new provider, detail lease, or comparison is admitted only when a concrete operator scenario
cannot already be satisfied — not for novelty.
