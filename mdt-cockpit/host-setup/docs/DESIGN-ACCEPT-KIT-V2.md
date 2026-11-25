# Design: acceptance kit v2 — squeeze test and DAMON floors (P-plat-19k)

Status: design only (2026-11-25), from the operator ruling of 2026-11-24 19:4xZ (`.controller-state/operator-decisions.md`): re-measure host
memory with a **squeeze test**, take the core services' hot sets from **DAMON**, make **pressure and ssh/service latency the hard criteria**,
refaults **informational**, re-check **M**, and plan a **third run at a lower reserve**. The next package implements this. It supersedes the
refault criterion of `DESIGN-DEDICATED-DEV-MEMORY.md` §4.1 and keeps that note's `K = F + M` model, bounds and unit table. Scope:
`HOST_ROLE=dedicated-dev`, r1002 first.

## 1. Today (v1, P-plat-19g) versus v2

| | v1 | v2 |
|---|---|---|
| load | two capped probes (`accept-run.sh:44-59,89-96`), 3.8G anon + 4G file, one per tier | **one** dummy in dev-gates, sized to dev.slice's remaining room under `memory.max(K0)` and inflated by **exactly 128M/step**, so dev.slice stays **at its ceiling**; `memory.min` keeps it resident, bound 3 caps that, and the ceiling is raised per step so the **host reserve** shrinks |
| what varies | nothing: one 30 min window at one K | `K(s) = K0 − s·128M`, steps 1-6, 300 s dwell, each step judged alone |
| floors | `1.25 × max(active_anon+active_file)` (`accept-eval.py:221`) — a proxy | DAMON `sz_ops_filter_passed` per unit ×1.10, printed **only when this run proved it under squeeze** (lower rows stay candidates, §4.3) |
| refault | hard criterion, 100 pages (`accept-eval.py:88-97,188`) | informational only (§3.4) |
| pressure | system.slice + user.slice `some ≤ 1` | + cgprofile.slice, all three also `full ≤ 1`; root rows INFO; `MemAvailable` and unit-`full` aborts (§3.1, §2.4) |
| ssh | one timing, `loaded ≤ 2 × idle` | per step, p95 of 10 vs step-0 `ssh_base`; `MDT_ACCEPT_SSH_TARGET` required (§3.2) |
| "doing their job" | none | seven liveness probes, commands, timeouts, contention-agnostic latency (§3.3) |
| M | whole window | `p99(U) + p99(init.scope) + W + 64M` over the **deepest passing step**, unpressured cross-check (§5) |
| DAMON | unused | one paddr kdamond, one `stat` scheme + `memcg`/`young` ops-filters per unit, 400ms/8s (§4) |
| cleanup | traps armed before start (`accept-run.sh:61-74`) | reconcile **lock** pause + one name + **WAL** + ExecStopPost restore (§2.3, §2.6) |
| evidence | `samples.csv`, `manifest.env`, `ssh.txt` | + `probes.csv`, `hotset.csv`, `squeeze-state.env` (§6) |

## 2. (a) The squeeze procedure

**2.1 K0, steps, ladder.** K0 = `DEV_HOST_MEMORY_KEEP` from `$HOSTENV` when set, else `mdt_capacity.host_floors(...).keep`; neither readable →
exit 3, never an invented K0 (`k_tested`). Step **Δ = 128 MiB**, dwell **300 s** (60 s settling, the last 240 s judged). Step s writes ceiling
`MemTotal − K(s)`, `K(s) = K0 − s·Δ`, on `dev.slice` **and** `dev-gates.slice`; `--steps` default 6 (steps 1-6; step 0 is always taken). A step
is only taken while `K(s) ≥ F_installed + 128M`; `F_installed` = `MemoryMin` of `system.slice` + `cgprofile.slice` + `user.slice` from
`systemctl show` — the units' floors are already inside system.slice's derived value and are **not** added again (that double-counts ≈ 420M on
r1002 and stops the ladder about two steps in); unreadable → exit 3. Only **raises** are written during the run (lowering happens only in the
restore, §2.6's 64M rule). **The mechanism, spelled out.** Raising `memory.max` takes nothing from the host by itself, so the dummy must be the
dev tree's bulk (a 1 GiB dummy under a 6.6 GiB ceiling squeezes nobody). At §2.2's size it carries the dev tree at its ceiling, its older pages
sit under `memory.min` (the tier's and dev.slice's too) so they are not the kernel's first choice, and once the dev tree plus what the host
really needs (kernel memory, PID 1, watermarks, the floored units' above-floor growth) exceeds `MemTotal`, reclaim and allocation stalls land on
the **unprotected** memory — page cache and the host services' working set above their floors. That is the squeeze §3 measures, and it is why
the ladder ends when the host stops answering, not when the dev tree is full.

**2.2 The dummy container.** `tester-unified:local` (v1's default: no new host dependency, it has `python3`); `docker run -d --rm --name
mdt-accept-squeeze --label mdt.accept=1 --network none --cpus 1 --cgroup-parent dev-gates.slice --memory <deepest ceiling> --memory-swap <that +
16G> --oom-score-adj 1000`, so a declared cap never binds before the tier ceiling under test; placement and caps read back, mismatch → exit 3
(like `accept-run.sh:90-96`). `--oom-score-adj 1000` is deliberate: a global or dev OOM before §2.4 fires then takes the dummy, **not the
operator's gate lane** (adj 0), and could only reach services that carry OOMScoreAdjust −900 of their own design. Allocator: the dummy starts at
`A0 = dev.slice/memory.max(K0) − dev.slice/memory.current(at start) − 256M` (never below 256M) and grows by **exactly Δ per step**, so
**dev.slice stays at its ceiling while the ceiling rises** — the mechanism §2.1 needs. The anon is `os.urandom` (zlib ratio > 0.95, pinned by
`test_the_probe_body_is_incompressible_and_ends_by_itself`); older pages are never re-touched — `memory.min` keeps them resident, and §2.3's
bound 3 caps how much of the dummy is protected. A 1 Hz read of the newest 16 MiB proves the job runs; the process exits by itself after `warmup
+ steps × dwell + 300 s`.

**2.3 Which cgroup file, who writes it, with what bound.** The kit writes kernel files **directly** and never `systemctl set-property` (README:
no hand-set memory properties; check.sh FAILs on runtime overrides and on a systemd/kernel mismatch). It therefore **pauses the reconcile
instead of stopping it**: it holds `flock -n /run/mdt-accept.lock` for the whole run, and `mdt-dev-governance-reconcile.sh` and `install.sh`'s
converge skip the dedicated-dev memory block with a NOTE while that lock is held (the lock `daemon-reload-check.sh:12` already honours). The
kernel releases it on **any** death of the kit, SIGKILL included, so the next timer tick converges the host back to systemd's values under its
own safe-lowering rule (`mdt-dev-governance-reconcile.sh:329-344`) — no stopped timer to restart, no stuck lowered reserve, no wait for a
reconcile service that is mid-run. It refuses (exit 3) when `/run/mdt/reconcile/memory-deferred` exists. **Mid-run drift.** A foreign
`install.sh`, a daemon-reload or the converge re-applies dev.slice/dev-gates properties, and a `memory.max` written below `memory.current` loops
reclaim then OOMs *inside* dev.slice while the mins are stripped; so **every 5 s tick reads all five files back**, and any value differing from
the last kit write aborts the ladder, marks the step INCONCLUSIVE `drift:<file>` and runs `finish` — not only the scope `min`, and not only once
per dwell. The kit's own processes run in `mdt-accept.slice` (`--slice=mdt-accept.slice`, §7 step 3): a root child with no floor, counted in Σ
root children and in `U`, instead of landing in system.slice and charging its stalls against the system.slice criteria. Per step, parent before
child:

| # | file | value | refused before the write |
|---|---|---|---|
| 1 | `dev.slice/memory.max` | `MemTotal − K(s)` (raise) | non-raise vs the value read at start |
| 2 | `dev.slice/dev-gates.slice/memory.max` | the same | > the parent's new value |
| 3 | `dev.slice/memory.min` | `start value + min(dummy anon, dev max − 1G − start)` | > `dev.slice/memory.max − 1G` (design note §4) |
| 4 | `dev.slice/dev-gates.slice/memory.min` | the same protected amount | > the tier's `memory.max` |
| 5 | `dev.slice/dev-gates.slice/docker-<id>.scope/memory.min` | `min(dummy anon, its memory.current)` | > its `memory.current` |

Each `min` is a **raise from its start value**, never a replacement: dev.slice's min is Σ every child Min key (design note §5.2), and
overwriting it would strip the other tiers' floors. A child's effective `memory.min` is capped by its ancestors (design note §2.2), hence 3 and
4. Bound 3 therefore caps how much of the dummy is protected; the unprotected rest is recorded as `dummy_unprotected` in `samples.csv` (§6).

**2.4 The bound: the host is never squeezed into OOM.** At every 5 s tick: (1) `MemAvailable ≥ 128 MiB`, else abort `mem_available`; (2) Σ
`memory.min` over root children ≤ `MemTotal − SQUEEZE_UNPROTECTED`, 256 MiB = 2.5 × the largest measured W (~100 MiB) — the only invented number
here, it guarantees reclaimable cache and is never a measurement; a step that would break it is not written. With §2.2's multi-GiB dummy this
bound can no longer *never* bind (v1's Σ min ≈ 2.2 GiB ≪ MemTotal − 256M): it is the real guard, checked before §2.3's writes 3-5, and its
arithmetic holds because bound 3 (`dev min ≤ dev max − 1G`) caps the protected part of the dummy; (3) global `oom_kill` delta > 0, or
system.slice **or** user.slice `memory.pressure` `full avg10 ≥ 5` held 10 s (psi-guard's own trigger numbers, `mdt-psi-guard.py:55,66-70`:
`HOLD_SECONDS` 10, `host_memory_full` 0.05) → abort. An abort is a step FAIL plus a ladder stop.

**2.5 Stop rule.** The first step that fails any §3 hard criterion, or where the next `K(s) < F_installed + 128M`, or `--steps` exhausted.
Recorded: `squeeze_last_pass_step`/`k_last_pass`, `squeeze_first_fail_step`/`k_first_fail`. A **failing step 0 is FAIL (exit 1)**: K0, the live
configuration, is itself not survivable, and the pin advice prints K0 + 128M. A step-0 INCONCLUSIVE (missing data, load not real) is
INCONCLUSIVE (§3.5).

**2.6 Cleanup on every exit path, including SIGHUP, SIGKILL and an OOM kill.** Every side effect is appended to `$EVID/squeeze-state.env`
**before** it is applied (write-ahead): the five original cgroup values **and the value the kit wrote for each** (max ×2, min ×3), `boot_id`,
`k_tested`, `started_utc`, `pid`, the kdamond index and every docker name it created. Restore derives from that file, never from in-memory
variables. The MDT-110 HUP flake is the precedent and its cause stays **unexplained** (`mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md:2016-2023`: the
r1002 run read `rm bg, rm bg`, and `finish()` removes both names unconditionally at `accept-run.sh:67`); v2 adds the backlog's two subtests (a
HUP between a WAL line and its write, a HUP during `docker run` before the id is known) and logs every `rm` with its exact name. Traps armed
**before** the first state line: `EXIT`, `INT 130`, `TERM 143`, `HUP 129`; `finish` begins with `trap '' INT TERM HUP` and a re-entry guard,
then: (1) `docker rm -f` the exact names (`--rm` not relied on); (2) poll `dev.slice/memory.current ≤ original − 64M` for ≤ 60 s; (3a) the three
`memory.min` originals first, child before parent, **unconditionally** — a lowering of min never OOMs, and a missing scope directory (the
dummy's `docker-<id>.scope` after step 1) counts as restored; (3b) then the two `memory.max` originals, child before parent, each only while
`memory.current ≤ T − 64M` (design note §6.2), else up to 8 chunks of `memory.reclaim` 256M on dev-gates.slice (never dev.slice itself, the
reconcile's own rule) and then a deferral: a deferred max leaves `RESTORE-PENDING <file> <target> <current>` in `manifest.env` and keeps the
state file, and the reconcile — unlocked by the ending run — completes it; (4) remove **only** the kdamond index the WAL names (`nr_kdamonds` is
appended to, never rewritten: rewriting it destroys and recreates every kdamond directory and fails `EBUSY` while any is on, which would break a
cgprofiled session started mid-run, or let one clobber the kit's configuration); (5) `end_utc`, `restore=ok|pending`, probe end states, and
delete the state file only on `ok`. **Compare, then restore.** An original is written back only while the file still holds the value the kit
wrote (both in the WAL); a differing value means another writer has taken over and is NOTEd, not overwritten. **A reboot mid-run** is a
different `boot_id`: the boot already reset the cgroup files, so the restore writes nothing, clears the state file with a NOTE and exits 0.
**`accept-restore.sh`** (new verb) repairs a state file left by SIGKILL: idempotent, exit 0 ok / 3 nothing to do / 1 pending, and a later
`accept-run.sh` that finds one restores first with one NOTE. It takes the same flock and refuses while a live run holds it. **A unit that ends
by itself.** The `systemd-run` unit carries its own restore: `-p ExecStopPost='/…/accept-restore.sh --evidence <EV>'` runs after **any** end of
the main process — SIGKILL and an OOM kill included, which the traps never see — so a kit death cannot leave the host at the last lowered
reserve with the reconcile unpaused. No `pkill`, no pattern `kill`.

## 3. (b) Hard criteria, with numbers

**3.1 Pressure** — every 5 s; per step the **max** over the last 240 s of the dwell. The root rows are **INFO only**: with dev.slice at its
ceiling (§2.2) the dummy and the lane stall on dev's own limit reclaim continuously, so root `some`/`full` include those stalls and would FAIL a
step for the load doing exactly what it is meant to do.

| cgroup | field | ≤ | why |
|---|---|---|---|
| `system.slice` | `memory.pressure` `some avg10` | 1.00 | v1's threshold; run 2 measured 0.00 |
| `user.slice` | `some avg10` | 1.00 | v1; recovery shell, `sudo`, `systemctl` |
| `cgprofile.slice` | `some avg10` | 1.00 | new: a floored host service outside system.slice |
| the same three | `full avg10` | 1.00 | hard too: full = every task stalled |
| root | `some`/`full avg10` | — | **INFO**, printed, never judged (§2.4 aborts on the units' `full`) |

`some`/`full` are stall **fractions**, so `avg10` is a percentage: psi-guard's `unit_memory_some` 0.10 is a 10 % stall
(`mdt-psi-guard.py:65-70`), not a 0.10 % one — 5.00 is **0.5×** that threshold, not 50×.

**3.2 ssh latency** — `MDT_ACCEPT_SSH_TARGET` is **required** on v2 (preflight exit 3 when unset): root on r1002 needs a non-interactive login
to it, and §7 step 2 verifies one BatchMode round trip before the run. Each sample is `timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=10
<target> true`; a timeout counts as a 15 s sample. The baseline `ssh_base` is the **max of 3** at step 0 before any raise (its slow sample
included, so one warm-up outlier cannot license every later step); the step value is the p95 of 10 samples across its judged window; pass ⇔ `p95
≤ max(2 × ssh_base, ssh_base + 0.5 s)`. Anchors: 0.566 s passed at K 1507M (run 2), 5.5 s failed at K 767M (2026-11-15). The v1 manual `ssh.txt`
path is **removed** for v2 (it cannot deliver "p95 of 10 per step" by hand, and a single hand line would leave steps ≥ 1 with no data). No
samples → INCONCLUSIVE.

**3.3 Per-service liveness probes** — every 30 s inside the judged window (8 per step), each `timeout`-wrapped, rc + seconds + detail into
`probes.csv`. `latency rule` = `max(2 × step-0 p95, step-0 p95 + 0.5 s)`, judged **contention-agnostically**: a step FAILs a latency rule when
**≥ 2 samples exceed it, or any sample hits the timeout, or the median exceeds it**; a single outlier is INFO (the p95 of 10 samples is the max,
so one slow `docker info` under a starting lane would otherwise fail a step for a contention fact, not a memory verdict).

| unit | probe | timeout | pass rule |
|---|---|---|---|
| sshd | `systemctl is-active ssh.service` + raw TCP to `127.0.0.1:22` whose first bytes are `SSH-2.0-` | 2 s / 2 s | active **and** banner seen |
| journald | `logger -t mdt-accept-<nonce> <nonce>`, then poll `journalctl -t mdt-accept-<nonce> --since @<t0> -o cat -n 1` every 100 ms until it prints the nonce, up to 5 s (an immediate read races `logger`) | 5 s | exit 0, nonce visible, seconds = time to visibility, within the latency rule |
| dockerd | `docker info -f '{{.ServerVersion}}'` | 10 s | exit 0, non-empty, within the latency rule |
| containerd | `docker exec mdt-accept-squeeze true` **and** `systemctl show -p NRestarts containerd.service` unchanged since step 0 | 10 s | exit 0 **and** no new restart (the shim still lives; `docker inspect` never reaches containerd) |
| cgprofiled | `cgprofilectl --json status`: exit 0 and a daemon version present | 5 s | exit 0 and version; absent → `systemctl is-active` + socket file, `degraded`, **never a pass** |
| psi-guard | `systemctl is-active` + `utime+stime` of its PID advances ≥ 1 tick over a 30 s window | 10 s | both: "active" alone does not prove the 1 s loop runs; USER_HZ=100 makes a 5 s / 0.01 s window flaky |
| fleetd agent, hostobs probe (`MDT_ACCEPT_EXTRA_UNITS`) | `systemctl is-active` + MainPID alive (event-driven daemons burn ≈ 0 CPU, so a CPU-advance probe would FAIL forever) | 10 s | active and MainPID alive, **INFO** until each has a health verb; `not-installed` when `LoadState` ≠ `loaded`: a host fact, never evidence for a floor |

**3.4 Refaults: informational only.** `workingset_refault_{anon,file}` deltas per unit/step go to `samples.csv` and are printed with an `INFO`
prefix; they never move the verdict. Both runs of 19g failed *only* on it while every behavioural criterion passed (run 2:
`docker.service=+1593`, `systemd-journald.service=+53102`, psi 0.00, ssh 0.566 s, no OOM), and a rising refault is the evidence that warm pages
were paged out — the intended result. One derived INFO per step: `refault ≥ 0.25 × the unit's hot set` prints `floor likely too small` (a hint
for the next run).

**3.5 Step and run verdicts.** Per step each criterion is PASS/FAIL/INCONCLUSIVE, missing data names itself and is never a pass (v1's rule).
**Load was real** is a per-step hard criterion, so a step cannot PASS without a squeeze: `dev.slice/memory.current ≥ dev.slice/memory.max −
128M` on ≥ 80 % of the judged ticks, **and** `MemFree ≤ 2 × W` on ≥ 50 % of them; otherwise the step is INCONCLUSIVE, never PASS, and it can
never be `k_last_pass`. The run is PASS when ≥ 1 step passed and F, M, K are derivable; FAIL when a hard criterion failed at step 0;
INCONCLUSIVE otherwise. Exit codes stay 0/1/4/2.

## 4. (c) DAMON hot set

**4.1 Method: one paddr kdamond, one `stat` scheme per unit.** `cgprofile`'s daemon has only per-pid **vaddr** targets (`CP-3`:
paddr/DAMOS-filter mode open, not built) and `cgprofilectl` cannot target a cgroup or slice yet (`CP-55`). So the kit drives the generic sysfs
interface through `scripts/damon-analysis/lib/damon_analysis.py`'s `SysfsInterface` (the accept checkout therefore gains that lib path, §6):
**one** kdamond, **one** context, 400 ms / 8 s, 10/1000 regions (guide §12.3, §13; guide §14's parallel kdamonds are not needed). **The filters
make the per-unit number, not the regions.** A paddr region is a physical-address range of the *whole* RAM, and the DAMOS `memcg` ops-filter
decides which pages of a region the scheme counts, not which regions exist — so Σ(`end − start`) of qualifying regions is the **machine's** hot
set, identical for every unit and dominated by the dummy, the lane and the page cache. Each unit therefore gets its own `stat` scheme with two
ops-filters — `memcg <leaf path>` (`matching=Y allow=Y`) and `young` (`matching=Y allow=Y`) — and the access pattern `nr_accesses 0..max, age
0..max`; the unit's hot bytes are the scheme's `stats/sz_ops_filter_passed` delta. The ops-filter write (`.../schemes/<i>/ops_filters/0/*`) is
the one step the lib has no method for, and is the kit's only new sysfs writer (`analyze_container.py:207-214` uses a `damo` CLI filter instead;
`lib/damon_analysis.py:282-312` never reads `sz_filter_passed` and `:417` uses `end − start`, so that tool's physical mode has the same flaw — a
backlog entry goes to `scripts/damon-analysis/KNOWN_ISSUES_TODO_BACKLOG.md` for both). **A slice is the sum of its leaf memcgs** (§4.2), never
the slice's own cgroup: the kernel's memcg filter matches the folio's memcg id exactly, not its descendants, so `user.slice` and
`dev-interactive.slice` read ≈ 0 at their own directory. **Self-test.** `sz_tried` counts regions that matched the access pattern *before* the
ops filters, so it is non-zero whenever anything on the host is hot and proves nothing: `sz_ops_filter_passed = 0` while that unit's
`memory.current` is above 1 MiB refuses that unit's floor (an unreadable filter is never "no hot pages"). **Preflight.** It requires
`stats/sz_ops_filter_passed` and the `young` filter on the r1002 kernel and refuses with exit 3 naming what is missing; it never falls back to
`end − start`.

**4.2 Parameters and "hot".** `sample_us`/`aggr_us` = 400 ms / 8 s (guide §12.3's recommendation); `min/max_regions` 10/1000; `refresh_ms` 10 s;
session length = the dwell (≥ 300 s, the guide's 5-minute minimum); region cost is `age × aggr_us` (guide pitfall 13). **Hot** ⇔ accessed within
one 8 s interval, p95 over ≥ 300 s. `H_unit` = the **p95 over the judged window of the per-interval `sz_ops_filter_passed` deltas**, max over
steps; the regions-based Σ(`end − start`) number is printed beside it for comparison only. The rate ≥ 5 % ∧ age ≥ 20 s rule is kept only as an
INFO column (its integer threshold is ≥ 3 aggregation intervals: 20 s / 8 s = 2.5). Units: the six floored ones, `user.slice`,
`dev-interactive.slice` (only to size its `MemoryMin`, never its Low), plus the optional units when installed; a slice is enumerated once per
step as the sum of its leaves (`user.slice/*/…scope|service`, `dev-interactive.slice/docker-*.scope`).

**4.3 The floor.** `floor(unit) = ceil(1.10 × H_unit)` (`FLOOR_FACTOR` = 1.10: one page per region per 400 ms plus merge/split churn; 1.25 was a
blind factor over a proxy that counted cold but LRU-active pages, and the operator's ruling replaces it). A row is printed **only when this run
proved it** (that unit's probes and ssh passed at every step with that floor); an unproved unit keeps its table row and the eval names it. A
probe FAIL while `memory.current ≤ floor` means the floor is too small → raise that row to `ceil(1.10 × its last-passing memory.current)`,
repeat once (design note §4.1). Floors stay raise-only against a foreign drop-in, and the derived `system.slice` floor is still summed
post-raise under the 460M / 10 % bounds. **Proved, or only a candidate.** The squeeze runs with the **installed** floors (644M on r1002), so a
derived floor ≥ the installed row is *proved* when that unit's probes and ssh passed at every step with it in force; a derived floor **below**
the installed row was never in force and is printed as `FLOOR <unit>=<v> source=damon candidate (installed <x>, untested)` — it enters the
`host-setup.env` lines only commented out, and it is proved only by a run with it installed (§7 step 6). Losing a floor is safe (the host keeps
its current, higher one); claiming one this run never exercised is not.

**4.4 The `mdt-damon-stat` conflict (guide §5.3).** `units/mdt-damon-stat.conf` installs `/etc/tmpfiles.d/mdt-damon-stat.conf` writing `N` to
`/sys/module/damon_stat/parameters/enabled` at boot (`host-setup.env.example:103`, `DAMON_STAT_DISABLE_DEFAULT="yes"`). At `Y`, `damon_stat`
owns the kernel-wide DAMON interface and every manual kdamond fails `Device or resource busy`. The kit **reads** it and refuses (exit 3) when it
is not `N`, naming the tmpfiles file and `install.sh --require-damon-disable`; it never writes the parameter (host policy, install.sh's job).
Recorded as `damon_stat_enabled`, and when the file does not exist at all the kernel has no DAMON_STAT: it records `damon_stat_enabled=absent`
and **proceeds**, never refuses. Second conflict: `cgprofiled` also multiplexes kdamonds — a live session refuses the run (exit 3) instead of
adding a kdamond index under a daemon; the check is `cgprofilectl --json status`'s `sessions_live == 0` (or `sessions --state live`), not bare
`sessions`, which lists stored sessions too and would refuse on any old one. The CLI's absence is a NOTE, not a refusal.

## 5. (d) The margin M re-check

The formula at design note `:122-124` **stays**: `M = p99(U) + p99(init.scope) + W + 64M`, `U = MemTotal − MemFree − Σ memory.current of every
root child`, `W = Σ zone high × page size`. What changes is **which ticks**: the deepest **passing** step's judged window, because that is where
the reserve was actually squeezed — `U` there is the memory no cgroup can give back, which is what M means. Also reported, not used:
`M_unpressured` (same formula over step 0) and the failing step's `U` as the bracket's upper edge. New: `M_squeeze` and `M_unpressured`
differing by ≥ 25 % prints a NOTE and the **larger** is taken — two disagreeing measurements are not evidence for the smaller, and the smaller
is the one that can OOM a host. **v1's two clamps are replaced by one.** `K_proposed = max(F_new + M, k_last_pass)`, printed with
`DEV_HOST_MEMORY_MARGIN = K_proposed − F_new`, where `M` itself is never below 64M (`accept-eval.py:226`). The old "never below the margin the
run was made at" (`:226`) would forbid any lower reserve on r1002 (run 2's base margin was 916M, eval-run2.txt:22), and "never below the tested
K" (`:158-162`) would pin K ≥ K0: a K below `k_last_pass` was not tested and is never proposed, but a K above it is exactly what the squeeze is
looking for.

## 6. (e) Output

**`samples.csv`** keeps every v1 column and appends `step, k_target, dev_max, tier_max, dummy_min, dummy_unprotected, mem_available,
root_some_avg10, root_full_avg10, cgprofile.psi_some_avg10, containerd_restarts, limit_source`. New per-event files: **`probes.csv`** (`utc,
step, probe, kind, rc, seconds, detail`), **`hotset.csv`** (`utc, step, unit, memcg_leaves, sz_ops_filter_passed, sz_tried,
regions_hot_bytes_info`), **`squeeze-state.env`** (§2.6; absent after a clean run). **`manifest.env`** gains `kit_version=2, squeeze_step,
squeeze_dwell, squeeze_steps_run, squeeze_last_pass_step, squeeze_first_fail_step, k_tested, k_last_pass, k_first_fail, damon_stat_enabled,
damon_measure=ok|failed:<unit>:<why>, floors_snapshot, limit_source, restore=ok|pending, probe_image, host_setup_env_sha256`. A v1 directory (no
`kit_version`) is **refused with exit 2**, naming the v1 kit: v2 criteria on v1 data would turn a missing squeeze into a pass.

Verdict lines (`INFO` is never folded into the verdict). The numbers are **illustrative** (K0 = 1560M, the run-2 pin, Δ 128M, so steps 0-6 are
1560/1432/1304/1176/1048/920/792; the floors and M are placeholders, not measurements):

```
STEP 0 K=1560M PASS   load real 100%  psi 0.00/0.00/0.00 root 0.00 ssh p95 0.57<=1.13 probes 8/8
STEP 4 K=1048M FAIL   load real 100%  psi system.some 1.90 > 1.00; probes 6/8 (journald 2 timeout)
SQUEEZE last pass K=1176M (step 3), first fail K=1048M (step 4): stopped by the psi criterion
INFO refault (informational, pages): docker.service +1593, systemd-journald.service +53102 (step 4)
INFO hot set systemd-journald.service 21.4M (DAMON sz_ops_filter_passed p95) -> floor 24M (table 24M, source damon)
FLOOR ssh.service=15M source=damon:18.6M x1.10 tested=every step
FLOOR cgprofiled.service=26M source=damon candidate (installed 4M, untested)
M measured=742M (deepest passing step 3) unpressured=747M set=780M (= K proposed 1338M - F 558M)
K proposed=1338M = F 558M + M 780M (>= k_last_pass 1176M)
VERDICT PASS
== host-setup.env
DEV_HOST_MEMORY_MIN="ssh.service=15M docker.service=231M containerd.service=92M systemd-journald.service=24M"
DEV_HOST_MEMORY_MARGIN=780M
DEV_HOST_MEMORY_MEASURED="2026-11-25 /root/accept-..."
```

The FAIL header keeps P-plat-19h's fix and extends it: it prints `pin DEV_HOST_MEMORY_KEEP=<K>M` where `<K>` is a K this run **tested** —
`k_last_pass` when one exists, else `k_tested` — and no number at all when none is derivable (points at the evidence instead). The string
`1280M` appears nowhere in any output; `test_the_fail_pin_advice_names_the_measured_k_never_a_fixed_1280m` keeps that true. The header's `K
proposed` on a FAIL prints `k_last_pass` (the deepest K whose step passed), so the advice can never name a K the run did not test.

**README run order** (main's 19g finding, extended): the host accept checkout needs `mdt-cockpit/` **AND** `scripts/debian-install-v2/tools/`
(`git sparse-checkout set mdt-cockpit scripts/debian-install-v2/tools`; `install.sh` exits 2 without the second) **plus
`scripts/damon-analysis/lib/`** for §4. The kit's preflight checks all three and refuses naming the missing path.

## 7. (f) Third-run plan on r1002 (root, in order)

1. Sparse-check-out both trees + `scripts/damon-analysis/` at the reviewed commit; `kit_sha`/`kit_sha256` into `manifest.env`.
2. Preflight: `mdt-host-check.sh` no FAIL, `NeedDaemonReload=no` on `dev.slice` and both tiers, no
   `/run/mdt/reconcile/memory-deferred`, `damon_stat_enabled=N` (or `absent`), `cgprofilectl --json status` with
   `sessions_live == 0`, `MDT_PSI_GUARD=report` so enforce mode cannot re-weight the tiers mid-run, and
   `MDT_ACCEPT_SSH_TARGET` set with **one verified BatchMode round trip from root**; drain and **hold** the drain (design note
   §8 step 0).
3. Squeeze run: `systemd-run --unit mdt-accept-1 --collect --slice=mdt-accept.slice -p KillMode=mixed -p TimeoutStopSec=600 -p
   ExecStopPost='<checkout>/mdt-cockpit/host-setup/tools/accept/accept-restore.sh --evidence /root/accept-<ts>-1'
   <checkout>/mdt-cockpit/host-setup/tools/accept/accept-run.sh --evidence /root/accept-<ts>-1 --steps 6 --step 128M --dwell
   300`; operator part: one gate lane, the cockpit attached and idle, one ssh login.
4. Eval and apply: `accept-eval.py /root/accept-<ts>-1`, read `SQUEEZE`/`FLOOR`/`M`/`K proposed`/the `INFO refault` hints, put
   the printed `DEV_HOST_MEMORY_MIN`/`MARGIN`/`MEASURED` in (**no KEEP pin**: K is derived again), then `sudo ./install.sh`,
   `mdt-host-check.sh` (no FAIL) and the reconcile timer running.
5. Rollback **R (on any failure of steps 3-6, or `restore=pending`)**: `accept-restore.sh --evidence <EV>` (exit 1 = a lowering
   is deferred: report it, never force it), then `DEV_HOST_MEMORY_KEEP=1560M` (today's live r1002 pin) and `sudo ./install.sh`.
   It covers step 6 as well, because a failed third run leaves the same state a failed squeeze does.
6. Third run at the lower reserve. **ONE rule**: run-3 K = `max(K_proposed, k_last_pass)`, which is `K_proposed` as step 4 printed
   it because §5 already takes `k_last_pass` as a floor; 1280M is a **floor on the target**, not a literal to pin, so K =
   `max(K_proposed, 1280M)` only while 1280M ≤ `k_last_pass` from step 3, else 1280M is recorded as untested and the run goes at
   `K_proposed`. Nothing below `F_installed + 128M` is proposed either way. The arithmetic, with
   the run-2 numbers (`/workspaces/vbpub/.controller-state/main/accept19g/eval-run2.txt:18-22`): the tested K was 1507M,
   `F_installed` 644M and the measured `M` 746.7M, so `M` rounds up to the tested margin 916M (the run's own margin, which M
   may not undercut) and `F_new + M` is today `644 + 916 = 1560M`; the *unclamped* floors + measured-M value is
   `644 + 746.7 ≈ 1391M`. Run 2's `K 1507M` passed every behavioural criterion, so 1391M would be the lower candidate **only**
   once step 3's `k_last_pass` says so. 1077M stays the *default* K (F 565 + M 512) with no support from run 2.
7. `daemon-reload-check.sh` (design note §5.3 precedence); then the result into the host-setup backlog.

**Duration**: preflight 10 min; step 3 = warm-up + 6 × 300 s + 300 s margin. The warm-up is **600 s** (v1's `--warmup`, and §2.2's allocator
needs dev.slice to reach A0 before step 0 is judged): `600 + 6 × 300 + 300 = 2700 s` = **45 min**, not 37. Risk 3's DAMON-off control pass adds
one more dwell at step 0 (≈ 5 min). Step 6 ≈ the same; eval and apply ≈ 10 min → **≈ 2 h 50 min on r1002**, no reboot.

## 8. (g) Test plan for the kit package

Seams (no root, no DAMON, no wall clock in a verdict):

| seam | fakes |
|---|---|
| `MDT_ACCEPT_CGROUP`, `MDT_ACCEPT_PROC` (existing) | cgroupfs and `/proc`, now also the **write** path of §2.3 (accept writes, read back) |
| `MDT_ACCEPT_HOTSET_JSON`, `MDT_ACCEPT_DAMON` (new) | per-step/per-unit scheme fixtures instead of a session; and a fake `/sys/kernel/mm/damon/admin` with non-sequential `tried_regions` names, `age` in intervals, `stats/sz_ops_filter_passed`, `avail_operations` without `paddr`, a failing filter write |
| `MDT_ACCEPT_DAMON_STAT` (new) | the fake `enabled` file: `N` proceeds, `Y` refuses exit 3 with no write, absent proceeds |
| `MDT_ACCEPT_PROBE_CMD_<NAME>`, `MDT_ACCEPT_PROBE_TIMEOUT` (new) | per-probe argv/timeout; an overrunning probe is killed, recorded `rc=timeout`, and does not hang the step |
| `MDT_ACCEPT_SQUEEZE_STATE`, `MDT_ACCEPT_DWELL_TICKS`, `MDT_ACCEPT_LOCK`, `MDT_ACCEPT_EXTRA_UNITS`, `MDT_ACCEPT_SSH_TARGET` (new) | the WAL path; the dwell counted in **ticks** (`MDT_ACCEPT_INTERVAL=0`), so no wall clock enters a verdict; the **live lock**, so the reconcile's and install.sh's skip is testable without root; optional-unit presence; the ssh sample source |

New tests: the ladder (passing, a stop at the first psi failure, the `F + 128M` ceiling, an `--steps` cap, a step whose **load-was-real**
criterion misses → INCONCLUSIVE and never `k_last_pass`); the stop-rule arithmetic including step 0 (FAIL vs INCONCLUSIVE); `MemAvailable` and
unit-`full` aborts; one refusal per §2.3 bound; mid-run drift on `dev.slice/memory.max` and on the tier's min → INCONCLUSIVE `drift:<file>`; a
HUP before the first write, after the third, between a WAL line and its write, and during `docker run` before the id is known (the MDT-110
subtests), with every original restored; `--rm` not relied on; ExecStopPost restoring after a SIGKILL of the unit; `accept-restore.sh` twice on
one state file, on one whose target is above `current − 64M` (`RESTORE-PENDING`, exit 1), on one whose min scope is gone (restored, not
pending), on one whose value a foreign writer changed (NOTEd, not overwritten), and on a stale `boot_id` (nothing written, exit 0); a stale
state file handled by the next run (one NOTE); the v1-evidence refusal; `damon_stat` absent → proceed; `sz_ops_filter_passed = 0` with
`memory.current > 1 MiB` → that unit refused; `1280M` cannot appear in any output. Changed: `test_accept_kit.py`'s `HEAD` and `write_run`
fixtures (new columns, two new CSVs); `test_refault_fails_and_the_env_lines_are_not_to_be_applied` →
`…is_informational_and_does_not_change_the_verdict` (rc 0, `INFO`); the M and floor assertions move to `hotset`;
`test_the_proposed_k_is_never_below_the_tested_one_on_fail_and_on_pass` → `…never_below_k_last_pass` (one clamp, §5);
`test_the_probe_body_is_incompressible…` gains the per-step inflation and never-re-touch rule; the shell tests' `TIER_MAX`/fake-run expectations
move to the deepest-ceiling cap; `test_a_load_that_never_hit_the_ceiling_is_inconclusive` becomes its per-step form. Lane: the existing
`smoke`/`r0-r1` lane (the module is already in it); shellcheck extends to the two new scripts. No new lane.

## 9. (h) Risks and open questions (default taken)

1. **systemd may rewrite a container scope's `memory.min`** (any property change or reload), silently un-protecting the dummy
   mid-step. Default: §2.3's drift rule (no `daemon-reload` during the run, all five files read back every 5 s, step
   INCONCLUSIVE `drift:<file>` on a difference).
2. **The `memcg` ops-filter may attribute nothing for a leaf** on some setups (`SCRIPTS.md:258`), and whether the r1002 kernel has
   `stats/sz_ops_filter_passed` and the `young` filter is a preflight fact, not a known number. Default: the §4.1 per-unit self-test
   and preflight, refuse that unit's floor with a NOTE and keep its table row — never read 0 hot bytes as "no hot set".
3. **DAMON overhead could distort the latencies we judge** (one kdamond × 1000 regions at 400 ms, not eight separate ones).
   Default: keep the session during the dwell (the operator wants hot sets *while in use*) and add one control pass with DAMON
   stopped at step 0; > 25 % difference prints INFO, and the hard criteria keep the DAMON-on numbers (conservative). The
   step-0 latency baselines are taken with DAMON **on**, like every step; the control pass is INFO.
4. **The squeeze can OOM before a criterion fails.** Default: the §2.4 guards abort the ladder and mark the step FAIL, and
   §2.2's `--oom-score-adj 1000` makes the dummy the victim, never the gate lane — never trade a host OOM for one more data
   point.
5. **1280M may be the wrong target for run 3** (DAMON floors can be lower *or* higher than today's 644M, and `M_squeeze` may
   exceed `M_unpressured`). Default: §7 step 6's **one** rule, `K_proposed = max(F_new + M, k_last_pass)`, with 1280M only a floor
   on the target while `k_last_pass` supports it; anything below `F_installed + 128M` is untestable rather than a candidate.
6. **Is the squeeze the "third run"?** The operator asked for "re-check M, then a third run at a lower reserve". Default: **the
   squeeze is the re-check and §7 step 6 is the third run** — run 1 was the 1077M default, run 2 the 1507M re-run, so counting
   the squeeze as a run as well would add a fourth without saying so.

## Appendix: the kit package's file list (next package)

`tools/accept/accept-run.sh` (steps, writes, WAL, probes, DAMON phase); `tools/accept/accept-eval.py` (step verdicts, DAMON floors, M re-check,
v1 refusal); new `tools/accept/accept-hotset.py` (one kdamond, one `stat` scheme and two ops-filters per unit, via `scripts/damon-analysis/lib`)
and `tools/accept/accept-restore.sh`; `tests/test_accept_kit.py`; `README.md` "Acceptance and measurement kit"; `mdt-cockpit/CHANGES.md`
`[Unreleased]`. Two files outside the kit: the accept-lock pause in `scripts/mdt-dev-governance-reconcile.sh` and `install.sh`'s converge
(§2.3), and a `scripts/damon-analysis/KNOWN_ISSUES_TODO_BACKLOG.md` entry for `sz_filter_passed` (§4.1). No new stored key is planned, so
`host-setup.env.example` stays untouched.
