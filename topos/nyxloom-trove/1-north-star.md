---
kind: north-star
schema_version: 1
---

# topos north star

topos is a host pressure inspector and cgroup forensics TUI: one fast tool that
shows what every cgroup on a host is actually doing right now — process, I/O,
network, Docker, compressed-swap, and DAMON context together — instead of
stitching together `top`, `iostat`, `docker stats`, `ctop`, `ip -s`, `ss`, and
manual cgroup greps. It generalizes the zswap compression-ratio split math
proven on a live game-hosting reference deployment to the whole host, renders
the *entire* cgroup tree so slices with no container remain visible, and
detects when a live limit has drifted from its systemd-declared configuration.

The product is read-only and safe by default, and layers deliberately: a
collector/model core underlies a read-only Textual TUI; DAMON hot/warm/cold
classification and incident snapshots build on that; a privileged read-broker
daemon, bounded administrative actions (start/stop/restart/kill/update, gated
behind root, explicit typed confirmation, and durable fail-closed audit), and
BPF-based exact network accounting come last. Root-owned state and mutating
actions stay in the daemon; the TUI, MCP frontend, and any future web client
are thin clients over the same bounded, versioned, redaction-enforcing read
API — never a second aggregation engine, and never a raw-runtime fallback for
an owner-managed workload (Compose/CIU/Wings-owned or protected containers
refuse rather than silently mutate).

topos optimizes for a coherent operator-console product over raw provider
breadth: one bounded frame-query core, honest source/coverage/degradation
signaling, and named operator investigations drive what gets built next.
A new provider, detail lease, or comparison is admitted only when a concrete
operator scenario cannot already be satisfied — not for its own novelty.
