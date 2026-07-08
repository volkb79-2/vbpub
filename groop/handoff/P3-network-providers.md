# P3 — Network providers: host truth + netns approximation

**Cut:** v1. **Depends:** P1 merged. Branch: `feat/groop-p3-network`.
Follow `groop/README.md` workflow protocol.

## Goal

The two v1 network tiers behind the provider interface, source-labelled so the
UI can never over-trust a number. The v2 BPF provider must slot in later with
ZERO changes to the table model or frame schema — the interface is the point.

## Spec references

§3.2 network columns + provider interface, Appendix B (three-tier model, BPF
design — you implement tiers 1+2 only), §5 (source files), §6.3 (degradation).

## Scope — in

1. `providers/base.py`: `Provider` protocol + `NetSample` per CONTRACTS §6.
2. `providers/net_host.py` (tier 1 — host truth, feeds the banner and a host
   pseudo-entity): /proc/net/dev per-interface rx/tx bytes/pkts/errs/drops;
   /proc/net/softnet_stat (backlog drops, time_squeeze); /proc/net/snmp +
   /proc/net/netstat (TCP retransmits/resets, UDP errors); `tc -s qdisc show`
   parsed if the binary exists (subprocess, injectable for tests).
3. `providers/net_netns.py` (tier 2): for each entity with processes, read one
   representative `/proc/<pid>/net/dev`; dedupe by `/proc/<pid>/ns/net` inode;
   host-netns entities → `NetSample(source_label="net:N/A",
   unavailable_reason="host netns")`; private-netns entities →
   `source_label="net:NS"`, confidence="estimated". Branch rows only aggregate
   when EVERY child proved private-netns (CONTRACTS §6 aggregation rule).
4. Rates: raw counters go through the collector's reset handling (coordinate
   with P1's mechanism — providers return cumulative, collector rates them; if
   that needs a small P1 extension, propose it in your report, don't hack it).
5. Traffic classes config (spec §3.4a/E3): `[net.classes]` TOML —
   interactive_admin / latency_critical / service_control / background as port
   lists; classification is metadata on samples (observe/explain only).
6. Tests: fixture /proc snapshots (textual copies) for both providers; netns
   dedup test (two pids, same ns inode → one sample); host-netns labeling
   test; aggregation-refusal test.

## Scope — out

BPF (v2 — but do not paint it out of the interface), IPAccounting provider,
qdisc mutation of any kind, the network drill-down screen (P5/P7 consume your
samples).

## Acceptance

- Frames gain net metrics with correct source labels on the gstammtisch-like
  fixture (game container: private netns → net:NS; host-net service → net:N/A).
- Registry entries exist for every emitted net metric (aggregatable=False for
  netns-sourced ones unless the private-ns proof applies).
- pytest green; report per README protocol.
