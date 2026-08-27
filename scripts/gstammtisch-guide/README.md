# gstammtisch — Memory/Swap Architecture Deliverables

Setup for the `gstammtisch` gaming+dev host: zswap (zstd) compressed swap,
cgroup-v2 priority/protection, Soulmask orderly shutdown + RCON tooling, and a
universal partition editor. Synthesizes `GAMINGHOST-SWAP-1.md`,
`GAMINGHOST-SWAP-2.md`, and the design discussion, corrected against the real
host and current (June 2026) kernel facts.

## Scope

This guide covers the **host and the game server**. Two neighbouring concerns on
the same host are owned elsewhere, and nothing here should duplicate them:

| Concern | Owner |
|---|---|
| Per-server game slices (`wings-<uuid>.slice`, memory floors, CPU/IO weight) | Wings itself — patch stack in [`../../wings-cgroups/`](../../wings-cgroups/). Rollout on this node: [WINGS-CGROUPS-ROLLOUT.md](WINGS-CGROUPS-ROLLOUT.md) |
| Dev tiers: `dev-interactive.slice` (devcontainers) + `dev-background.slice` (test/build/gate stacks), their measured IO caps, the fio baseline, BFQ setup, **and `/etc/docker/daemon.json`** | [`../../modern-debian-tools-python-debug/host-setup/`](../../modern-debian-tools-python-debug/host-setup/) — `sudo host-setup/install.sh --with-baseline` |

`setup-cgroups.sh` here is therefore **game-side only** — and RETIRED as of
the host dev-tier cgroup governance rollout: patched Wings now places and
configures each Soulmask container's cgroup natively (per-server
`wings-<uuid>.slice`), so the userspace retrofit this script + its watcher
service provided is superseded. Kept under `files/legacy/` for reference.
Adding dev-tier logic back to any surviving game-side script would give two
scripts the same cgroup attributes to write.

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
│   ├── etc/systemd/system/soulmask-graceful-stop.service
│   ├── etc/systemd/system/soulmask_tmpfs.service  soulmask_tmpfs.slice
│   │                        soulmask_tmpfs-ZSwapMax{0,1}.slice   # SOULMASK-TMPFS.md
│   ├── etc/systemd/oomd.conf.d/gstammtisch.conf
│   ├── legacy/                              # retired — kept for reference, not installed
│   │   ├── etc/systemd/system/gstammtisch-cgroups.service  soulmask-cgroup-watcher.service
│   │   │                        soulmask-paks.slice  soulmask-static.slice
│   │   │                        soulmask-pak-ramdisk.service  soulmask-static-ramdisk.service
│   │   └── usr/local/sbin/setup-cgroups.sh  soulmask-cgroup-watcher.sh
│   │                             soulmask-startup-cgroup.sh
│   │                             soulmask-pak-ramdisk-{setup,toggle,teardown}.sh
│   │                             soulmask-static-ramdisk-{setup,teardown}.sh
│   └── usr/local/sbin/soulmask-shutdown.sh  soulmask-instance-lib.sh  wings-ps.sh
│                         soulmask_tmpfs-{setup,teardown,toggle,restart-wings}.sh
│                         soulmask-monitor.{sh,py}  soulmask-mempress.sh
│                         soulmask-pak-mempress.sh  container-mempress.sh
│                         exec-soulmask-rcon.{sh,py}  soulmask_rcon.py  # RCON admin helper + engine
└── scripts/
    ├── install.sh                # orchestrator (copy files, enable units, sysctl, BFQ)
    ├── partition-editor.py        # universal MBR partition editor
    └── swap-health.sh             # one-glance monitoring
```

N-instance operations (config layout, watcher/shutdown/pak-ramdisk behavior
across several running Soulmask servers, how to add instance #2): see
[SOULMASK.md §9b "Multi-instance operations"](SOULMASK.md#9b-multi-instance-operations-implementation-2026-07-07).

## Runbook (on the host, as root)

```bash
# 0) copy this folder to the host, then from inside it:
sudo scripts/install.sh
#    -> copies configs, enables zswap-config/graceful-stop/oomd,
#       applies sysctl, brings up zswap+zstd live. Prints the next steps.

# 1) swap partitions — DRY-RUN first, then commit
sudo scripts/partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2
sudo scripts/partition-editor.py --disk /dev/vda add-swap --count 2 --size fill --labels gswap1,gswap2 --commit

# 2) GRUB: make sure GRUB_CMDLINE_LINUX has NO zswap.* tokens (now post-boot).
#    Optional latency: add `preempt=full`.  Then: sudo update-grub   (only if changed)

# 3) per-server memory floor/weight: set via patched Wings itself now
#    (docker.per_server_slices config + admin-only WINGS_CG_* egg overrides —
#    see ../../wings-cgroups/SETUP.md), not gstammtisch. setup-cgroups.sh /
#    SOULMASK_MIN / `systemctl restart gstammtisch-cgroups` are retired.

# 4) priorities
#    - Pterodactyl panel: set Soulmask memory/CPU/IO limits.
#    - dev/test/build containers: governed by mdt host-setup, not this guide —
#      sudo <vbpub>/modern-debian-tools-python-debug/host-setup/install.sh --with-baseline

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
- **cgroup v2**: Soulmask `memory.min`/`memory.low`/`zswap.writeback=0`, `io.bfq.weight=1000`, `cpu.weight=800`. Dev/test/build tiers are a separate concern — see [scope](#scope) below.
- **BFQ I/O scheduler** on `vda` (not `[none]`): only scheduler that enforces cgroup `io.weight`/`io.bfq.weight` — without it, all I/O priority settings are no-ops. `io.latency` unavailable (`CONFIG_BLK_CGROUP_IOLATENCY` not set in Debian 13); `io.cost.qos` is the available alternative (see SOULMASK.md §2b).
- **KSM optional** (dev opts containers in via `prctl`; never the game); **THP=madvise**.
- **fq_codel only** for network; skip speculative QoS.
- **Kernel 7.0.10** (non-LTS) — fine; plan toward 6.18 LTS via backports later.
- `sfdisk --append` can't place logicals in extended free space → `partition-editor.py` rewrites the full table safely on a mounted disk.
