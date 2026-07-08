# P1 — Collector core + metric registry (`groop --once --json`)

**Cut:** v0 (spec §0.1). **Depends:** none (CONTRACTS.md only). **Blocks:** everything else.
Branch: `feat/groop-p1-collector`. Follow `groop/README.md` workflow protocol.

## Goal

The framework-free data backbone: walk the cgroup-v2 tree, join Docker
metadata, compute the zswap/refault math, emit one validated `Frame` as JSON.
Prove the data model before any UI exists.

## Spec references

§3.0 (banner facts), §3.1 (row model/entity kinds), §3.2 (metric registry +
column tables — implement every v1 metric marked core), §3.4 (drill-down data:
per-entity process list), §5 (exact file → column mapping — your source-file
bible), §6.1 (layering), §6.2 (permission modes), §6.3 (degradation matrix).

## Scope — in

1. `model.py`, `registry.py` exactly per CONTRACTS §2–§4. Every v1 core metric
   from spec §3.2 gets a `MetricSpec` with honest `locality`/`branch_policy`
   (read spec §3.2's registry semantics; when unsure whether a kernel file is
   subtree-inclusive, check kernel docs and say so in the glossary).
2. `collect/cgroup.py`: tree walk from a parametrized `cgroup_root`; reads
   memory.{current,min,low,high,max,stat,pressure,events,swap.current,
   zswap.current,zswap.max,zswap.writeback}, cpu.{stat,pressure,weight,max},
   io.{stat,pressure,weight,max,bfq.weight}, pids.current, cgroup.procs count.
3. `collect/zswapmath.py`: port the refault split from
   `scripts/gstammtisch-guide/files/usr/local/sbin/soulmask-zswap-monitor.py`
   (rf_z/s = Δzswpin; rf_d/s = Δworkingset_refault_anon − Δzswpin; rf_f/s =
   Δworkingset_refault_file; z_pool, z_eq, compression ratio). Keep the
   formulas identical — they are production-verified.
4. `collect/dockerjoin.py`: injectable inspect callable; enrich entities whose
   key matches `docker-<64hex>.scope`; extract DockerMeta incl. ptero_uuid.
5. `collect/host.py`: banner facts (host mem/swap/zswap totals from
   /proc/meminfo + /sys/module/zswap + debugfs-if-readable, loadavg, PSI
   /proc/pressure/*, uptime, kernel).
6. `collect/procs.py`: for one entity on demand: pids from cgroup.procs +
   /proc/<pid>/{comm,cmdline,status(VmRSS,VmSwap)} — permission-tolerant.
7. `collect/collector.py`: orchestration, prev-sample state, rate+reset
   handling per CONTRACTS §4.
8. `config.py`: `load()` for the spec §7 TOML (only the keys P1 needs:
   cgroup_root override, tiers, protected services, sample interval).
9. `cli.py`: `groop --once --json` (pretty and compact), `--cgroup-root PATH`
   for fixtures; NO textual import on this path.
10. Tests + fixtures per CONTRACTS §9, including: a realistic gstammtisch-like
    tree (game scope, pak slice, besteffort children), a permission-denied
    case, a counter-reset case. Generate golden frames
    `tests/fixtures/frames/*.jsonl`.

## Scope — out

UI (P5), recording (P2 — but your Frame must serialize cleanly, coordinate via
CONTRACTS §5 compact form), network (P3), drift (P4), DAMON, diagnostics.

## Acceptance

- `groop --once --json --cgroup-root tests/fixtures/cgroupfs/gstammtisch` emits
  a schema-valid frame matching the golden file.
- On the real host as root: runs < 1s, no crash; as non-root: degrades with
  `unavail_perm`, never zeros (spec §9 criteria 1–3 as applicable to v0).
- Every emitted metric exists in REGISTRY; a test enforces this.
- pytest green; report per README protocol.
