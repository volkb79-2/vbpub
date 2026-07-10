# Container memory profiles — dstdns stack on gstammtisch

**Status:** living measurement record · started 2026-07-10 (first-ever full-stack deploy)
**Method:** groop one-shot frames (`/root/groop-venv/bin/groop --once --json`, recorder unit
`groop-recorder.service` → `/var/log/groop/rec-<date>.jsonl`), DAMON
`timeseries-container` (400 ms / 8 s sampling per `damon-analysis/DAMON-GUIDE.md` §11 —
defaults produce all-cold garbage), and `container-mempress.sh` stepped `memory.high`
squeezes. Companion decisions: `plan-stack-resource-tuning.md` (D1–D5).

## 1. Steady state, full stack (2026-07-10, ~10 min after first 20-stack deploy)

25 containers in `besteffort.slice`. Slice totals: **RAM 3245 M** (high = 8192 M),
z_pool 691 M, disk swap 651 M, `psi_mem_some10` 1.55. Game (Soulmask): RAM 6099 M at its
floor, `psi_mem_full` **0.0**, `psi_io_full` **0.0** — completely unaffected by the deploy.

| container | RAM MB | anon | z_pool | z_eq | disk_sw |
|---|---|---|---|---|---|
| skywalking-oap | 758 | 266 | 434 | 1677 | 181 |
| authentik-worker | 469 | 281 | 0 | 0 | 0 |
| otel-aggregator | 354 | 5 | 2 | 19 | 6 |
| skywalking-banyandb | 321 | 259 | 0 | 1 | 37 |
| vault | 228 | 23 | 0 | 1 | 5 |
| authentik-server | 216 | 49 | 88 | 320 | 21 |
| consul | 120 | 18 | 0 | 1 | 3 |
| minio | 113 | 10 | 2 | 15 | 70 |
| skywalking-ui | 100 | 25 | 1 | 8 | 0 |
| postgres | 74 | 14 | 2 | 22 | 25 |
| worker-db-1/2 | ~55 ea | 22 | 27 | ~110 | 1 |
| webapp-server | 53 | 23 | 24 | 92 | 0 |
| controller | 47 | 22 | 19 | 75 | 19 |
| worker-io-1/2 | ~45 ea | 14 | 27 | ~100 | 3 |
| redis / adminer / otel-node / stats-exporter / pgadmin / webapp-ui / reverse-proxy / ui-react / test-runner | ≤33 ea | — | — | — | — |

**Readings:**
- The **apps tier is strikingly cheap** — every Python service (controller, workers,
  webapp-server) is ~45–55 M resident. The expensive tenants are the observability JVMs.
- `z_eq / z_pool` ≈ 3.9 for skywalking-oap — zstd compresses its cold JVM heap ~4:1, so
  1.7 G of "memory" costs only 434 M of RAM. This is the whole zswap thesis working.
- The old `high=4G` would have been survivable at steady state but tight during deploys;
  the actual deploy-blocking constraint was the **IO cap (100 r-IOPS)**, not memory —
  see plan D2. After relaxing to 40 % of the measured baseline, the full deploy passed.

## 2. DAMON deep profile — skywalking-oap (600 s, 400 ms/8 s, 35 snapshots)

Final: RSS ~518 M, swap 1.64 G, **majflt ≈ 240–312/s sustained**, DAMON hot = 0 B,
warm = 8 KB, cold ≈ 3 G, idle = rest of vaddr space; only 1 region with access > 0.

**Interpretation (the valuable part):** the OAP JVM has *no* DAMON-hot set at 400 ms
resolution, yet continuously faults ~1 MB/s back from zswap. Its working set is not a
stable hot core but a **slow cycle over more pages than stay resident** — every touched
page was cold long enough to be reclaimed, gets faulted back (cheap zswap-speed refault,
z_pool is large), is used briefly, and goes cold again. Consequences:
- **Don't cap it harder** — a lower `memory.high` just raises the churn rate (more
  majflt, more CPU in zstd) without recovering much RAM.
- **`mem_reservation` 512–768 M** would quiet the churn if we ever want OAP snappy;
  at 40 metric-samples/s ingest it's harmless as-is.
- Its refaults are zswap-speed (rf_z), not disk-speed (rf_d) — they do not touch the
  disk path the game is sensitive to.

## 3. Devcontainer working-set squeeze (container-mempress.sh, two runs, 2026-07-10)

Container `dstdns-devcontainer-vb` (VS Code server + claude/codex CLI agents), measured
in place in `system.slice` (ungoverned), agents mostly idle:

| run | step | memory.high | refaults/s | PSI some/full | verdict |
|---|---|---|---|---|---|
| 1 | 0 | 1792 M | 45 | 0/0 | comfortable |
| 1 | 1 | 1536 M | **375** | 1.8/1.8 | warm boundary crossed |
| 2 | 1 | 1536 M | 104 | 0.3/0.3 | passes after run-1 evicted warm pages |
| 2 | 2 | 1280 M | **5810** | 2.8/2.8 | hot-floor cliff |

**Interpretation:** two-run stratification — run 1 finds the *warm* boundary (~1.8 G:
pages used occasionally), run 2, starting from the pre-compressed state, finds the *hot
floor* (~1.5 G: below it the kernel evicts pages processes are actively using — refaults
jump 55× in one 256 M step, anon forced 810→412 M). Under active multi-agent load the
same container was observed at 3.5 G resident, so squeeze points scale with activity.

**Sizing verdict (plan D4 closed):** the staged `interactive.slice` values are correct
as-is — `MemoryLow=2G` (just above the idle-ish hot floor), `MemoryHigh=5G` (headroom
over the 3.5 G active footprint), `MemoryMax=7G` (never-OOM-the-IDE). The devcontainer
rebuild with `--cgroup-parent=interactive.slice` needs no config iteration.

## 4. Host-side note

claude/codex CLI sessions started from host shells live in
`user.slice/user-1003.slice/session-<N>.scope` and are governed by nothing (decision
2026-07-10: leave as-is, documented in `plan-stack-resource-tuning.md`).

## 5. postgres DAMON profile + the idle-stack lesson (2026-07-10)

postgres (480 s, same intervals): backends at ~13 M RSS, majflt 0, hot/warm/cold all 0,
CPU 0 — the DB is **idle**, and so is the whole apps tier (no tasks running yet).
**Decision: further DAMON profiles of controller/worker-io/worker-db are DEFERRED until
the stack is under real load** (a corpus run / integration suite) — idle-state profiles
of on-demand services measure nothing but their allocator floor. skywalking-oap (§2) was
the exception worth profiling idle because its ingest pipeline is always-on.

## 6. Post-settle IO pressure (by design, not a problem)

~40 min after deploy: besteffort RAM settled 3245→2855 M, `psi_mem_some10` ~1, but
`psi_io_some10` ROSE to ~54 — driven almost entirely by **authentik-worker** (42 io-some;
its cold pages get reclaimed during settle and every swap-in queues behind the whole
host at IOWeight 10 + the 40 % caps). The game stayed at `psi_io_full 0.0` throughout —
this is precisely the intended failure mode: the best-effort tier absorbs all the
waiting, prod feels nothing. Only worth revisiting if a *latency-sensitive* service ever
lands in besteffort.

## 7. Pending

- controller / worker-io / worker-db DAMON profiles **under load** (see §5)
- per-service `mem_reservation`/`mem_limit` recommendation table → ciu governance
  overrides (today: blanket `mem_limit=2g`, `mem_reservation=256m` — already generous
  vs. the measured ~50 M apps tier; the interesting overrides are OAP reservation up,
  and possibly authentik-worker)
- recorder `groop-recorder.service` dialed to 300 s cadence 2026-07-10 after the
  measurement window (~130 MB/day; stop it or rotate `/var/log/groop/` when done)

## Reproduction crib

```
# one-shot frame                      # continuous recording (60 s)
/root/groop-venv/bin/groop --once --json    systemctl status groop-recorder
# DAMON profile of one container (root, damon_stat must be off)
HOME=/root PATH=/root/damon-venv/bin:$PATH \
  /root/damon-venv/bin/python3 /home/vb/volkb79-2/vbpub/scripts/damon-analysis/damon_cli.py \
  timeseries-container <name> --duration 600 --interval 10 --sample-us 400000 --aggr-us 8000000 \
  --output-file /var/log/damon/ts_<name>.jsonl
# working-set squeeze
container-mempress.sh <container> [--step 256M --delay 15]
```
