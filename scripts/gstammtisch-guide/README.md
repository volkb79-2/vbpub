# gstammtisch — Memory/Swap Architecture Deliverables

Setup for the `gstammtisch` gaming+dev host: zswap (zstd) compressed swap,
cgroup-v2 priority/protection, Soulmask orderly shutdown + RCON tooling, and a
universal partition editor. Synthesizes `GAMINGHOST-SWAP-1.md`,
`GAMINGHOST-SWAP-2.md`, and the design discussion, corrected against the real
host and current (June 2026) kernel facts.

## Read these
| Doc | What |
|---|---|
| [MEMORY-ARCHITECTURE.md](MEMORY-ARCHITECTURE.md) | The synthesis: guide comparison + verdicts, zswap/zstd, sysctl, **cgroup v2 reasoning**, KSM/THP, kernel facts |
| [OBSERVATION.md](OBSERVATION.md) | Environment observation & interpretation (`/sys/module/zswap`, `/sys/kernel/debug/zswap`, disk class/TRIM, PSI, cgroup, KSM, DAMON) |
| [SOULMASK.md](SOULMASK.md) | Game-server specifics: protection, orderly shutdown, RCON |
| [CGROUP-MONITORING.md](CGROUP-MONITORING.md) | Every cgroup v2 metric explained through live Soulmask data: memory.stat field-by-field, zswap math, swapcached/compression-ratio traps, PSI, CPU/IO, pak-slice decomposition, monitor column guide |
| [MEASUREMENTS.md](MEASUREMENTS.md) | Manual measurement procedures: refault-source split (zswap vs disk), pak hot set (vmtouch), login-latency gate for writeback, file-cache hotness + swappiness validation, io-baseline (fio), KSM estimate |
| [plan-host-resource-governance.md](plan-host-resource-governance.md) | Living plan: tiered slices, decisions log (§9), measurement plan (§10) |

## File manifest
```
gstammtisch-guide/
├── README.md  MEMORY-ARCHITECTURE.md  OBSERVATION.md  SOULMASK.md
├── files/                                   # drop-in config, mirrors target paths
│   ├── etc/modules-load.d/zstd.conf
│   ├── etc/modules-load.d/bfq.conf          # load BFQ module at boot
│   ├── etc/udev/rules.d/60-bfq-scheduler.rules  # switch vda to BFQ on boot
│   ├── etc/sysctl.d/99-gstammtisch-memory.conf
│   ├── etc/tmpfiles.d/thp.conf              # THP=madvise
│   ├── etc/tmpfiles.d/ksm.conf              # KSM (optional)
│   ├── etc/gstammtisch/instance-defaults.env      # per-instance defaults (N-instance, see SOULMASK.md §9b)
│   ├── etc/gstammtisch/instances.d/*.env(.example) # per-instance overrides (one file per server UUID)
│   ├── etc/systemd/system/zswap-config.service        # zstd post-boot fix
│   ├── etc/systemd/system/dev-workloads.slice         # dev limits + oomd
│   ├── etc/systemd/system/gstammtisch-cgroups.service # extra cgroup knobs
│   ├── etc/systemd/system/soulmask-graceful-stop.service
│   ├── etc/systemd/system/soulmask-pak-ramdisk.service  # shared pak tmpfs (opt-in per instance, §2c/§9b)
│   ├── etc/systemd/system/soulmask-paks.slice           # pak cgroup: writeback=yes, memory.min=150M
│   ├── etc/systemd/oomd.conf.d/gstammtisch.conf
│   └── usr/local/sbin/setup-cgroups.sh  soulmask-shutdown.sh  soulmask-instance-lib.sh
│                         soulmask-pak-ramdisk-setup.sh  soulmask-pak-ramdisk-toggle.sh
│                         soulmask-pak-ramdisk-teardown.sh
│                         soulmask-zswap-monitor.sh  soulmask-mempress.sh
│                         soulmask-pak-mempress.sh  soulmask-startup-cgroup.sh
└── scripts/
    ├── install.sh                # orchestrator (copy files, enable units, sysctl, BFQ)
    ├── partition-editor.py        # universal MBR partition editor
    ├── exec-soulmask-rcon.sh      # RCON admin helper
    └── swap-health.sh             # one-glance monitoring
```

N-instance operations (config layout, watcher/shutdown/pak-ramdisk behavior
across several running Soulmask servers, how to add instance #2): see
[SOULMASK.md §9b "Multi-instance operations"](SOULMASK.md#9b-multi-instance-operations-implementation-2026-07-07).

## Runbook (on the host, as root)

```bash
# 0) copy this folder to the host, then from inside it:
sudo scripts/install.sh
#    -> copies configs, enables zswap-config/dev-workloads/cgroups/graceful-stop/oomd,
#       applies sysctl, brings up zswap+zstd live. Prints the next steps.

# 1) swap partitions — DRY-RUN first, then commit
sudo scripts/partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2
sudo scripts/partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2 --commit

# 2) GRUB: make sure GRUB_CMDLINE_LINUX has NO zswap.* tokens (now post-boot).
#    Optional latency: add `preempt=full`.  Then: sudo update-grub   (only if changed)

# 3) measure Soulmask hot set (DAMON, kernel 7.0 auto-tunes intervals), then:
sudo sed -i 's/^SOULMASK_MIN=.*/SOULMASK_MIN="${SOULMASK_MIN:-<measured>G}"/' /usr/local/sbin/setup-cgroups.sh
sudo systemctl restart gstammtisch-cgroups

# 4) priorities
#    - Pterodactyl panel: set Soulmask memory/CPU/IO limits.
#    - dev containers:  docker run --cgroup-parent=dev-workloads.slice --label workload=dev ...

# 5) RCON + verify
docker pull itzg/rcon-cli
exec-soulmask-rcon.sh -d List_OnlinePlayers

# 6) monitor
swap-health            # or: swap-health watch
```

## Open items to verify on the host
These are runtime confirmations the deliverables assume but couldn't be tested from here:
1. **zstd active** — after install/reboot: `cat /sys/module/zswap/parameters/compressor` → `zstd` (the early `dmesg` lzo line is cosmetic — see MEMORY-ARCHITECTURE §3).
2. **Partition geometry** — run the `add-swap` dry-run and eyeball the proposed `vda6`/`vda7` before `--commit`.
3. **RCON whitelist** — `exec-soulmask-rcon.sh -d List_OnlinePlayers` should return players; if rejected, whitelist loopback/the helper IP in Soulmask's config (SOULMASK §4).
4. **`memory.min`** — set from a DAMON measurement, not the `4G` placeholder.
5. **Graceful shutdown** — after enabling the unit, do a real `reboot` and confirm the save/DB mtime advanced.

## Design decisions (one-liners; full reasoning in MEMORY-ARCHITECTURE.md)
- **zswap + zstd**, configured **post-boot** (built-in init races the zstd module → lzo fallback; GRUB tokens dropped).
- **swappiness=100** (zswap makes anon reclaim cheap; protect the game with `memory.min`, not swappiness).
- **2 labeled swap partitions** for `iostat` visibility — *not* for speed (striping is a no-op on one vda); thin-provisioned disk → `discard=once`.
- **cgroup v2**: Soulmask `memory.min`/`memory.low`/`zswap.writeback=0`, `io.bfq.weight=1000`, `cpu.weight=800`; bench containers `io.bfq.weight=1` + `io.max` hard IOPS cap; `dev-workloads.slice` + systemd-oomd kills dev first.
- **BFQ I/O scheduler** on `vda` (not `[none]`): only scheduler that enforces cgroup `io.weight`/`io.bfq.weight` — without it, all I/O priority settings are no-ops. `io.latency` unavailable (`CONFIG_BLK_CGROUP_IOLATENCY` not set in Debian 13); `io.cost.qos` is the available alternative (see SOULMASK.md §2b).
- **KSM optional** (dev opts containers in via `prctl`; never the game); **THP=madvise**.
- **fq_codel only** for network; skip speculative QoS.
- **Kernel 7.0.10** (non-LTS) — fine; plan toward 6.18 LTS via backports later.
- `sfdisk --append` can't place logicals in extended free space → `partition-editor.py` rewrites the full table safely on a mounted disk.
