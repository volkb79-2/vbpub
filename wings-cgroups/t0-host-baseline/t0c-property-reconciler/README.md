# T0c — reload-safe cgroup property reconciler (properties only)

A small host-side reconciler that applies systemd resource properties to
slices and Docker container scopes via `systemctl set-property` — the
**systemd-owned, daemon-reload-safe channel** (raw writes to
`/sys/fs/cgroup/.../*.scope/*` are silently wiped by any
`systemctl daemon-reload`; proven live, Finding D in the companion proposal).

## What it can and cannot do

- ✅ Ceilings (`MemoryHigh`, `MemoryMax`), weights (`CPUWeight`, `IOWeight`),
  and slice properties — reapplied idempotently, survive reloads.
- ❌ **Placement.** It cannot move a container out of `system.slice`; per-server
  `memory.min` floors remain arithmetically dead under an ancestor with
  `memory.min=0`. Placement needs T0b (dedicated node) or the T1/T2 Wings patch.

Role in the ladder: interim hardening of the current watcher approach, and the
property-owner companion to T1/T2 for values not carried by static slice units.
Superseded by the T3a slice-manager daemon.

## Usage

```bash
cp wings-cg-reconcile.sh /usr/local/sbin/ && chmod +x /usr/local/sbin/wings-cg-reconcile.sh
cp profiles.example.conf /etc/wings-cg-profiles.conf   # edit to taste
cp wings-cg-reconcile.service wings-cg-reconcile.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wings-cg-reconcile.timer
```

Config format (`/etc/wings-cg-profiles.conf`): one rule per line —
`<match> <systemd properties...>` where `<match>` is either `slice:<name>` or a
Docker container-name glob. Container rules resolve the container's transient
scope (`docker-<id>.scope`) and apply the properties there.
