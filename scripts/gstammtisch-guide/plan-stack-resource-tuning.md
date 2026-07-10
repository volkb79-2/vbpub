# Plan: Stack Resource Tuning — besteffort relaxation, interactive sizing, measured profiles

**Status:** in flight — decisions locked via interview 2026-07-10 (dstdns supervisor session).
**Driver:** dstdns needs its full stack (infra + apps + observability, 12–15 containers) running
for meaningful development; the host (8c/16GB, 70G swap, zswap/zstd) is RAM-constrained with
Soulmask as protected prod. Extends `plan-host-resource-governance.md` — that doc's design
stands; this one changes tier-2/tier-3 *values* based on new decisions and adds a measurement
program.

## Decisions (locked 2026-07-10)

| # | Decision | Value |
|---|---|---|
| D1 | besteffort memory | `MemoryHigh=8G`, `MemoryMax=12G`, `MemorySwapMax=24G` (was 4G/6G/12G). Rationale: tests mostly un-throttled; game `min=6G` + interactive `low` claw RAM back reactively. oomd kill stays as leak backstop. |
| D2 | besteffort disk IO | Dynamic slice-level caps from `io-baseline.env` at `BE_IO_CAP_PCT` (default **40%**) of measured ceilings, applied by `setup-cgroups.sh` (replaces the static 31M/100/400 as the operative values; unit statics remain only as boot-window fallback). At the 2026-07-08 baseline: rbps 859614221, wbps 206510649, riops 36069, wiops 23478. |
| D3 | Soulmask `min` 6144→5500M | **Hold.** Not needed yet; revisit only if measured stack demand requires it. `high` stays ≥6G per operator (below causes rf_z/rf_d in game). |
| D4 | devcontainer / interactive.slice | Measure **in place first** (no rebuild yet): stepped `memory.high` squeeze on the running devcontainer scope via `container-mempress.sh` (operator runs as root), size the slice from the squeeze point, then do the `--cgroup-parent=interactive.slice` runArgs change + rebuild once with final numbers. |
| D5 | Recording tooling | groop verified working on host (see below). Interim recorder = looped `groop --once --json`; file headless-record + report **feature specs into groop's package queue** (spec-first, groop's own P-workflow implements). |

## Live state (2026-07-10)

- **Applied on host as `systemctl set-property --runtime besteffort.slice`** (reverts on reboot;
  PKG-1 persists): `MemoryHigh=8G MemoryMax=12G MemorySwapMax=24G`, `IO*Max` at 40% of baseline
  (values above). Verified in systemd + cgroupfs.
- **groop smoke test (host, root, venv at `/root/groop-venv`, editable install from
  `/home/vb/volkb79-2/vbpub/groop`)**: `groop --once --json` works; 447KB frame, 89 entities
  including `besteffort.slice` children (docker-name join intact), the devcontainer and Soulmask
  scopes in `system.slice`. Per-entity metrics include `rf_z/rf_d/rf_f_per_s`, `z_pool`, `z_eq`,
  `swap_disk`, per-cgroup PSI, `mem_events_*_per_s`, `cpu_throttled_pct`, io/net rates, headroom,
  governance-drift. **Caveat:** `_per_s`/derived fields are `[None,'derived',<raw counter>]` in a
  one-shot frame — rates must be derived across consecutive frames at read time (raw counters are
  embedded, so a `--once` loop is sufficient for recording).
- **Devcontainer footprint** (ungoverned, `system.slice/docker-f0f02e8d…scope`): 3.5G resident
  (2.1G anon, 1.1G file), ~970M zswapped-equivalent, 4.3G swap. Squeeze run pending (operator).
- `container-mempress.sh` deployed to `/usr/local/sbin/` and smoke-tested (no-op step + restore).

## Operator action (whenever convenient)

```
sudo container-mempress.sh dstdns-devcontainer-vb
```
Defaults: 256M steps, 15s settle, stop at PSI some>10%/full>5% or refaults>200/s, floor 1G,
restores `memory.high=max` on exit/Ctrl-C. Watch IDE responsiveness; Ctrl-C is safe. Expect
~10–20 min. Output JSONL lands in `/var/log/mempress/`; the summary line's `squeeze_point` is
the hot+warm working set → basis for interactive.slice `high`.

## Packages

### PKG-1 `[Sonnet, vbpub]` — persist governance changes in gstammtisch-guide
- `files/etc/systemd/system/besteffort.slice`: D1 memory values; keep static `IO*Max` as
  boot-window fallback with a comment that `setup-cgroups.sh` overrides from baseline post-boot.
- `setup-cgroups.sh`: new step applying slice-level `IO*Max` on `besteffort.slice` from
  `/var/lib/gstammtisch/io-baseline.env` at `BE_IO_CAP_PCT` (default 40); plus fix the
  interactive.slice gap — unit comment promises `memory.zswap.writeback=0` but code only handles
  `dev-workloads.slice`; apply it to `interactive.slice` when the cgroup exists.
- `scripts/install.sh`: also enable `interactive.slice` and `besteffort.slice` (currently only
  dev-workloads/soulmask-paks are enabled).
- Docs: update tier table in `MEMORY-ARCHITECTURE.md` + decision log in
  `plan-host-resource-governance.md` (dated 2026-07-10).
- Gate: `bash -n` all touched scripts (+ shellcheck if available). No live host application —
  controller applies post-review.

### PKG-2 `[Sonnet, vbpub/groop]` — spec headless record + report packages (spec-only)
- Following groop's handoff package format (see `handoff/P48-*.md`, `P49-*.md`; verify next free
  P-numbers), add two queued specs + README/ROADMAP/STATUS table entries:
  1. **Headless record driver**: `groop --record FILE --headless [--interval N]
     [--duration S | --frames K]` — drive the existing collector loop + `RecordWriter` without
     importing textual; clean SIGINT/SIGTERM finalization; note that in-process consecutive
     sweeps make `_per_s` fields live from frame 1 (unlike a `--once` loop).
  2. **`groop report FILE [--window last:Ns|all] [--group-by slice|entity] --json`**: per-entity
     p50/p95/max for key gauges (ram, anon, z_pool, z_eq, swap_disk, psi_*), deriving `_per_s`
     rates from embedded raw counters across frames when the live rate is None. This is the
     "steady-state profile" consumer for the measurement program below.
- No implementation in this package.

### PKG-3 `[supervised]` — Phase B measurement program (after PKG-1 review + squeeze run)
1. Interim recorder on host: systemd service looping `/root/groop-venv/bin/groop --once --json`
   every 10s → `/var/log/groop/rec-<date>.jsonl` (retire once groop headless-record lands).
2. Game-impact watch running concurrently: `soulmask-zswap-monitor.py --json` + `rcon_probe.py`
   (gates: game `rf_d` < 100/s sustained, RCON RTT p95 stable).
3. Bring dstdns stack up tier-by-tier as `ciu up` would (infra → apps → observability), ≥60s
   settle per tier; note any remaining ciu 10s-hook timeouts (should be fixed by D2).
4. DAMON `timeseries-container` (400ms/8s sampling, 5–15min) on the heavy containers: postgres,
   skywalking-oap, banyandb, controller, worker-io, worker-db.
5. Deliverable: `container-memory-profiles.md` (this directory) — per container: steady RSS,
   hot/warm/cold/idle split, recommended `mem_reservation`/`mem_limit`; slice-level totals vs the
   8G/12G envelope; feed per-service overrides back into ciu governance (today: blanket
   `mem_limit=2g`).
6. From the devcontainer squeeze result: final interactive.slice `low/high/max`, then the
   devcontainer.json runArgs change + single rebuild (operator-timed).

## Out of scope (tracked elsewhere)
- CIU-9 (`reset_service` DooD cleanup no-op) — `ciu/KNOWN_ISSUES_TODO_BACKLOG.md`.
- ciu post-compose hook 10s timeouts — re-evaluate after D2; if still failing, file against ciu.
- groop P49 systemd memory governance (the *apply* side) — groop's own queue.
