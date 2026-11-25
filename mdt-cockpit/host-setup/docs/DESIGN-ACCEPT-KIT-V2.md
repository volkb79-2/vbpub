# Design: acceptance kit v2 — squeeze test and DAMON floors (P-plat-19k)

Status: design only (2026-11-25), from the operator ruling of 2026-11-24 19:4xZ
(`.controller-state/operator-decisions.md`): re-measure host memory with a **squeeze test**, take the core services'
hot sets from **DAMON**, make **pressure and ssh/service latency the hard criteria**, refaults **informational**,
re-check **M**, and plan a **third run at a lower reserve**. The next package implements this. It supersedes the
refault criterion of `DESIGN-DEDICATED-DEV-MEMORY.md` §4.1 and keeps that note's `K = F + M` model, bounds and unit
table. Scope: `HOST_ROLE=dedicated-dev`, r1002 first.

## 1. Today (v1, P-plat-19g) versus v2

| | v1 | v2 |
|---|---|---|
| load | two capped probes (`accept-run.sh:44-59,89-96`), 3.8G incompressible anon + 4G file, one in dev-gates and one in dev-background | **one** dummy in dev-gates, inflated **128M/step**, held resident by `memory.min`; the dev/tier ceiling is raised per step so the **host reserve** is what shrinks |
| what varies | nothing: one 30 min window at one K | `K(s) = K0 − s·128M`, 6 steps, 300 s dwell, each step judged alone |
| floors | `1.25 × max(active_anon+active_file)` (`accept-eval.py:221`) — a proxy of the hot set | DAMON hot set per unit, factor 1.10, printed **only when this run proved it under squeeze** |
| refault | hard criterion, 100 pages (`accept-eval.py:88-97,188`) | informational only (§3.4) |
| pressure | system.slice + user.slice `some avg10 ≤ 1` | + cgprofile.slice, root `some`/`full`, `MemAvailable` guards (§3.1, §2.4) |
| ssh | one timing, `loaded ≤ 2 × idle` | per step, p95 vs the step-0 p95 (§3.2) |
| "doing their job" | none | seven liveness probes with commands, timeouts, pass rules (§3.3) |
| M | whole window | p99 over the **deepest passing step**, unpressured cross-check (§5) |
| DAMON | unused | paddr + DAMOS `memcg` filter per unit, 400ms/8s (§4) |
| cleanup | traps armed before start (`accept-run.sh:61-74`) | one name + **write-ahead state file** + `accept-restore.sh` (§2.6) |
| evidence | `samples.csv`, `manifest.env`, `ssh.txt` | + `probes.csv`, `hotset.csv`, `squeeze-state.env` (§6) |

## 2. (a) The squeeze procedure

**2.1 K0, steps, ladder.** K0 = `DEV_HOST_MEMORY_KEEP` from `$HOSTENV` when set, else `mdt_capacity.host_floors(...).keep`;
neither readable → exit 3, never an invented K0 (`k_tested`). Step **Δ = 128 MiB**, dwell **300 s** (60 s settling, the
last 240 s judged). Step s writes ceiling `MemTotal − K(s)`, `K(s) = K0 − s·Δ`, on `dev.slice` **and** `dev-gates.slice`;
`--steps` default 6. A step is only taken while `K(s) ≥ F_installed + 128M`, `F_installed` = Σ `MemoryMin` of the
`DEV_HOST_MEMORY_MIN` units + `system.slice` + `cgprofile.slice` + `user.slice` read from `systemctl show` (unreadable →
exit 3). Only **raises** are written during the run (lowering happens only in the restore, under §2.6's 64M rule).
**The mechanism, spelled out.** The dummy lives **inside** dev.slice, so its anon counts against the ceiling that just
rose, and its older pages sit under `memory.min` (the tier's and dev.slice's too), so they are not the kernel's first
choice. Once the dev tree plus what the host really needs (kernel memory, PID 1, watermarks, the floored units'
above-floor growth) exceeds `MemTotal`, reclaim and allocation stalls land on the **unprotected** memory — page cache
and the host services' working set above their floors. That is the squeeze §3 measures, and it is why the ladder ends
when the host stops answering, not when the dev tree is full. Without §2.4 the kernel would OOM the dummy (adj 0) first;
those guards stop the ladder that one sample earlier and call it a FAIL.

**2.2 The dummy container.** `tester-unified:local` (v1's default: no new host dependency, it has `python3`);
`docker run -d --rm --name mdt-accept-squeeze --label mdt.accept=1 --network none --cpus 1 --cgroup-parent
dev-gates.slice --memory <deepest ceiling> --memory-swap <that + 16G>`, so a declared cap never binds before the tier
ceiling under test; placement and caps read back, mismatch → exit 3 (like `accept-run.sh:90-96`). Allocator: 256 MiB at
start, **+128 MiB per step** of `os.urandom` (zlib ratio > 0.95, pinned by
`test_the_probe_body_is_incompressible_and_ends_by_itself`); older pages are never re-touched — `memory.min` keeps them
resident. A 1 Hz read of the newest 16 MiB proves the job runs; the process exits by itself after
`warmup + steps × dwell + 300 s`.

**2.3 Which cgroup file, who writes it, with what bound.** The kit writes kernel files **directly** and never
`systemctl set-property` (README: no hand-set memory properties; check.sh FAILs on runtime overrides and on a
systemd/kernel mismatch). It therefore `systemctl stop`s `mdt-dev-governance-reconcile.timer` (read back) and restarts
it in `finish`; it refuses (exit 3) when `/run/mdt/reconcile/memory-deferred` exists. Per step, parent before child:

| # | file | value | refused before the write |
|---|---|---|---|
| 1 | `dev.slice/memory.max` | `MemTotal − K(s)` (raise) | non-raise vs the value read at start |
| 2 | `dev.slice/dev-gates.slice/memory.max` | the same | > the parent's new value |
| 3 | `dev.slice/memory.min` | `start value + Σ dummy anon` | > `dev.slice/memory.max − 1G` (design note §4) |
| 4 | `dev.slice/dev-gates.slice/memory.min` | `start value + Σ dummy anon` | > the tier's `memory.max` |
| 5 | `dev.slice/dev-gates.slice/docker-<id>.scope/memory.min` | the dummy's anon high-water | > its `memory.current` |

Each `min` is a **raise from its start value**, never a replacement: dev.slice's min is Σ every child Min key
(design note §5.2), and overwriting it would strip the other tiers' floors. A child's effective `memory.min` is capped
by its ancestors (design note §2.2), hence 3 and 4.

**2.4 The bound: the host is never squeezed into OOM.** At every 5 s tick: (1) `MemAvailable ≥ 128 MiB`, else abort
`mem_available`; (2) Σ `memory.min` over root children ≤ `MemTotal − SQUEEZE_UNPROTECTED`, 256 MiB = 2.5 × the largest
measured W (~100 MiB) — the only invented number here, it guarantees reclaimable cache and is never a measurement; a
step that would break it is not written; (3) global `oom_kill` delta > 0, or root `memory.pressure` `full avg10 ≥ 5`
held 10 s (psi-guard's own trigger numbers, `mdt-psi-guard.py:55,66-70`: `HOLD_SECONDS` 10, `host_memory_full` 0.05) →
abort. An abort is a step FAIL plus a ladder stop.

**2.5 Stop rule.** The first step that fails any §3 hard criterion, or where the next `K(s) < F_installed + 128M`, or
`--steps` exhausted. Recorded: `squeeze_last_pass_step`/`k_last_pass`, `squeeze_first_fail_step`/`k_first_fail`. A
failing step 0 is INCONCLUSIVE, not PASS: the limits were already not survivable.

**2.6 Cleanup on every exit path, including SIGHUP.** Every side effect is appended to `$EVID/squeeze-state.env`
**before** it is applied (write-ahead): the five original cgroup values (max ×2, min ×3), the timer's `is-active`,
`nr_kdamonds`, `k_tested`, `started_utc`, `pid`. Restore derives from that file, never from in-memory variables — the
exact gap in MDT-110's HUP finding (a HUP skipped the gates probe's removal because `finish()` read a variable the
interrupted `start_probe` had not set). Traps armed **before** the first state line: `EXIT`, `INT 130`, `TERM 143`,
`HUP 129`; `finish` begins with `trap '' INT TERM HUP` and a re-entry guard, then: (1) `docker rm -f` the exact name
(`--rm` not relied on); (2) poll `dev.slice/memory.current ≤ original − 64M` for ≤ 60 s; (3) write the originals back,
child before parent, each only when `memory.current ≤ T − 64M` (design note §6.2) — an unwritable value leaves
`RESTORE-PENDING <file> <target> <current>` in `manifest.env` and keeps the state file; (4) remove the kit's kdamonds
and restore `nr_kdamonds`; (5) restart the reconcile timer if it was active; (6) `end_utc`, `restore=ok|pending`, probe
end states, and delete the state file only on `ok`. **`accept-restore.sh`** (new verb) repairs a state file left by
SIGKILL: idempotent, exit 0 ok / 3 nothing to do / 1 pending, and a later `accept-run.sh` that finds one restores first
with one NOTE. No `pkill`, no pattern `kill`.

## 3. (b) Hard criteria, with numbers

**3.1 Pressure** — every 5 s; per step the **max** over the last 240 s of the dwell.

| cgroup | field | ≤ | why |
|---|---|---|---|
| `system.slice` | `memory.pressure` `some avg10` | 1.00 | v1's threshold; run 2 measured 0.00 |
| `user.slice` | `some avg10` | 1.00 | v1; recovery shell, `sudo`, `systemctl` |
| `cgprofile.slice` | `some avg10` | 1.00 | new: a floored host service outside system.slice |
| root | `some avg10` | 5.00 | 50× `unit_memory_some` (0.10), `mdt-psi-guard.py:66-70`; a coarse host-wide ceiling |
| root | `full avg10` | 1.00 | the host-latency measure a squeezed reserve breaks; ≥ 5 for 10 s aborts (§2.4) |

**3.2 ssh latency** — the idle baseline is the **largest of 3** `ssh <host> true` round trips taken at step 0 before
any raise (its slow sample included, so one warm-up outlier cannot license every later step); the step value is the
p95 of 10 round trips across its judged window; pass ⇔ `p95 ≤ max(2 × idle_p95, idle_p95 + 0.5 s)`. Anchors: 0.566 s
passed at K 1507M (run 2), 5.5 s failed at K 767M (2026-11-15). Samples come from `MDT_ACCEPT_SSH_TARGET` on the host;
unset, the operator writes `step <n> idle <s> loaded <s>` lines into `$EV/ssh.txt` (v1's single line reads as step 0).
No samples → INCONCLUSIVE.

**3.3 Per-service liveness probes** — every 30 s inside the judged window (8 per step), each `timeout`-wrapped, rc +
seconds + detail into `probes.csv`. `latency rule` = `max(2 × step-0 p95, step-0 p95 + 0.5 s)`.

| unit | probe | timeout | pass rule |
|---|---|---|---|
| sshd | `systemctl is-active ssh.service` + raw TCP to `127.0.0.1:22` whose first bytes are `SSH-2.0-` | 2 s / 2 s | active **and** banner seen |
| journald | `logger -t mdt-accept-<nonce> <nonce>`, then `journalctl -t mdt-accept-<nonce> --since @<t0> -o cat -n 1` prints it | 5 s | exit 0, nonce visible, within the latency rule |
| dockerd | `docker info -f '{{.ServerVersion}}'` | 10 s | exit 0, non-empty, within the latency rule |
| containerd | `docker inspect -f '{{.State.Running}}' mdt-accept-squeeze` **and** `systemctl show -p NRestarts containerd.service` = 0 | 10 s | `true` **and** no restart (the shim still lives) |
| cgprofiled | `cgprofilectl --json connect` | 5 s | exit 0 and a live socket; absent → `systemctl is-active` + socket file, recorded `degraded`, **never a pass** |
| psi-guard | `systemctl is-active` + `utime+stime` of its PID advances ≥ 0.01 s over 5 s | 10 s | both: "active" alone does not prove the 1 s loop runs |
| fleetd agent, hostobs probe (`MDT_ACCEPT_EXTRA_UNITS`, default `fleetd-agent.service hostobs-probe.service`) | as psi-guard | 10 s | `not-installed` when `LoadState` ≠ `loaded`: a host fact, never evidence for a floor |

**3.4 Refaults: informational only.** `workingset_refault_{anon,file}` deltas per unit/step go to `samples.csv` and are
printed with an `INFO` prefix; they never move the verdict. Both runs of 19g failed *only* on it while every
behavioural criterion passed (run 2: `docker.service=+1593`, `systemd-journald.service=+53102`, psi 0.00, ssh 0.566 s,
no OOM), and a rising refault is the evidence that warm pages were paged out — the intended result. One derived INFO
per step: `refault ≥ 0.25 × the unit's hot set` prints `floor likely too small` (a hint for the next run).

**3.5 Step and run verdicts.** Per step each criterion is PASS/FAIL/INCONCLUSIVE, missing data names itself and is
never a pass (v1's rule). The run is PASS when ≥ 1 step passed and F, M, K are derivable; FAIL when a hard criterion
failed at step 0; INCONCLUSIVE otherwise. Exit codes stay 0/1/4/2.

## 4. (c) DAMON hot set

**4.1 Method: paddr + DAMOS `memcg` filter, not vaddr.** `cgprofile`'s daemon has only per-pid **vaddr** targets
(`CP-3`: paddr/DAMOS-filter mode open, not built) and `cgprofilectl` cannot target a cgroup or slice yet (`CP-55`).
So the kit uses system-wide **paddr with a DAMOS `memcg` ops-filter** per unit — the mechanism
`scripts/damon-analysis/analyze_container.py:207-214` already uses (`damo start paddr --damos_action stat
--damos_filter 'reject none memcg <path>'`, paddr-only per guide §4.7). Mechanics: one kdamond per unit in parallel
(guide §14) through `scripts/damon-analysis/lib/damon_analysis.py`'s `SysfsInterface`; the ops-filter write
(`.../schemes/0/ops_filters/0/*`) is the one step it has no method for, and is the kit's only new sysfs writer (the
accept checkout therefore gains that lib path, §6). `MEMCG_FILTER_SUPPORTED` is a **preflight fact**, not an
assumption: one 30 s self-test per unit, and `sz_tried` = 0 while `memory.current` is above its floor refuses that
unit's floor (an unreadable filter is never "no hot pages").

**4.2 Parameters and "hot".** `sample_us`/`aggr_us` = 400 ms / 8 s (guide §12.3's recommendation); `min/max_regions`
10/1000; `refresh_ms` 10 s; session length = the dwell (≥ 300 s, the guide's 5-minute minimum); region cost is
`age × aggr_us` (guide pitfall 13). **Hot** ⇔ `access_rate ≥ 5 %` **and** `age ≥ 20 s` (8 s/400 ms = 20 samples per
aggregation, so 5 % is one sampled access per interval; 20 s = 2.5 windows of unchanged pattern). `H_unit` = max over
steps of Σ(`end − start`) of qualifying regions. Units: the six floored ones, `user.slice`, `dev-interactive.slice`
(only to size its `MemoryMin`, never its Low), plus the optional units when installed. A slice is measured at the slice's
own cgroup.

**4.3 The floor.** `floor(unit) = ceil(1.10 × H_unit)` (`FLOOR_FACTOR` = 1.10: one page per region per 400 ms plus
merge/split churn; 1.25 was a blind factor over a proxy that counted cold but LRU-active pages, and the operator's
ruling replaces it). A row is printed **only when this run proved it** (that unit's probes and ssh passed at every step
with that floor); an unproved unit keeps its table row and the eval names it. A probe FAIL while
`memory.current ≤ floor` means the floor is too small → raise that row to `ceil(1.10 × its last-passing
memory.current)`, repeat once (design note §4.1). Floors stay raise-only against a foreign drop-in, and the derived
`system.slice` floor is still summed post-raise under the 460M / 10 % bounds.

**4.4 The `mdt-damon-stat` conflict (guide §5.3).** `units/mdt-damon-stat.conf` installs
`/etc/tmpfiles.d/mdt-damon-stat.conf` writing `N` to `/sys/module/damon_stat/parameters/enabled` at boot
(`host-setup.env.example:103`, `DAMON_STAT_DISABLE_DEFAULT="yes"`). At `Y`, `damon_stat` owns the kernel-wide DAMON
interface and every manual kdamond fails `Device or resource busy`. The kit **reads** it and refuses (exit 3) when it is
not `N`, naming the tmpfiles file and `install.sh --require-damon-disable`; it never writes the parameter (host policy,
install.sh's job). Recorded as `damon_stat_enabled`. Second conflict: `cgprofiled` also multiplexes kdamonds — a live `cgprofilectl --json sessions`
entry refuses the run (exit 3) instead of changing `nr_kdamonds` under a daemon; the CLI's absence is a NOTE.

## 5. (d) The margin M re-check

The formula at design note `:122-124` **stays**: `M = p99(U) + p99(init.scope) + W + 64M`, `U = MemTotal − MemFree −
Σ memory.current of every root child`, `W = Σ zone high × page size`. What changes is **which ticks**: the deepest
**passing** step's judged window, because that is where the reserve was actually squeezed — `U` there is the memory no
cgroup can give back, which is what M means. Also reported, not used: `M_unpressured` (same formula over step 0) and the
failing step's `U` as the bracket's upper edge. Kept from v1: never below the margin the run was made at, never below
64M (`accept-eval.py:222-226`). New: `M_squeeze` and `M_unpressured` differing by ≥ 25 % prints a NOTE and the **larger**
is taken — two disagreeing measurements are not evidence for the smaller, and the smaller is the one that can OOM a
host.

## 6. (e) Output

**`samples.csv`** keeps every v1 column and appends `step, k_target, dev_max, tier_max, dummy_min, mem_available,
root_some_avg10, root_full_avg10, cgprofile.psi_some_avg10, containerd_restarts, limit_source`. New per-event files:
**`probes.csv`** (`utc, step, probe, kind, rc, seconds, detail`), **`hotset.csv`** (`utc, step, unit, ops, filter,
regions, hot_bytes, p95_rate, max_age_s, stats_sz_tried`), **`squeeze-state.env`** (§2.6; absent after a clean run).
**`manifest.env`** gains `kit_version=2, squeeze_step, squeeze_dwell, squeeze_steps_run, squeeze_last_pass_step,
squeeze_first_fail_step, k_tested, k_last_pass, k_first_fail, damon_stat_enabled, damon_measure=ok|failed:<unit>:<why>,
floors_snapshot, limit_source, restore=ok|pending, probe_image, host_setup_env_sha256`. A v1 directory (no
`kit_version`) is **refused with exit 2**, naming the v1 kit: v2 criteria on v1 data would turn a missing squeeze into a
pass.

Verdict lines (`INFO` is never folded into the verdict). The numbers are **illustrative** (K0 = 1560M, the run-2 pin,
Δ 128M, so the ladder is 1560/1432/1304/1176/1048; the floors and M are placeholders, not measurements):

```
STEP 0 K=1560M PASS   psi 0.00/0.00/0.00 root 0.00 ssh p95 0.57<=0.88 probes 8/8
STEP 4 K=1048M FAIL   psi system.some 1.90 > 1.00; probes 6/8 (journald 2 timeout)
SQUEEZE last pass K=1176M (step 3), first fail K=1048M (step 4): stopped by the psi criterion
INFO refault (informational, pages): docker.service +1593, systemd-journald.service +53102 (step 4)
INFO hot set systemd-journald.service 21.4M (DAMON, rate>=5% age>=20s) -> floor 24M (table 24M, source damon)
FLOOR ssh.service=15M source=damon:18.6M x1.10 tested=every step
M measured=742M (deepest passing step 3) unpressured=747M set=780M
K proposed=1338M = F 558M + M 780M
VERDICT PASS
== host-setup.env
DEV_HOST_MEMORY_MIN="ssh.service=15M docker.service=231M containerd.service=92M systemd-journald.service=24M"
DEV_HOST_MEMORY_MARGIN=780M
DEV_HOST_MEMORY_MEASURED="2026-11-25 /root/accept-..."
```

The FAIL header keeps P-plat-19h's fix and extends it: it prints `pin DEV_HOST_MEMORY_KEEP=<K>M` where `<K>` is a K
this run **tested** — `k_last_pass` when one exists, else `k_tested` — and no number at all when none is derivable
(points at the evidence instead). The string `1280M` appears nowhere in any output;
`test_the_fail_pin_advice_names_the_measured_k_never_a_fixed_1280m` keeps that true. The header's `K proposed` on a
FAIL prints `k_last_pass` (the deepest K whose step passed), so the advice can never name a K the run did not test.

**README run order** (main's 19g finding, extended): the host accept checkout needs `mdt-cockpit/` **AND**
`scripts/debian-install-v2/tools/` (`git sparse-checkout set mdt-cockpit scripts/debian-install-v2/tools`; `install.sh`
exits 2 without the second) **plus `scripts/damon-analysis/lib/`** for §4. The kit's preflight checks all three and
refuses naming the missing path.

## 7. (f) Third-run plan on r1002 (root, in order)

1. Sparse-check-out both trees + `scripts/damon-analysis/` at the reviewed commit; `kit_sha`/`kit_sha256` into
   `manifest.env`.
2. Preflight: `mdt-host-check.sh` no FAIL, `NeedDaemonReload=no` on `dev.slice` and both tiers, no
   `/run/mdt/reconcile/memory-deferred`, `damon_stat_enabled=N`, no cgprofile session; drain and **hold** the drain
   (design note §8 step 0).
3. Squeeze run: `systemd-run --unit mdt-accept-1 --collect <checkout>/mdt-cockpit/host-setup/tools/accept/accept-run.sh
   --evidence /root/accept-<ts>-1 --steps 6 --step 128M --dwell 300`; operator part: one gate lane, the cockpit attached
   and idle, one ssh login, `ssh.txt` step lines (or `MDT_ACCEPT_SSH_TARGET`).
4. Eval and apply: `accept-eval.py /root/accept-<ts>-1`, read `SQUEEZE`/`FLOOR`/`M`/`K proposed`/the `INFO refault`
   hints, put the printed `DEV_HOST_MEMORY_MIN`/`MARGIN`/`MEASURED` in (**no KEEP pin**: K is derived again), then
   `sudo ./install.sh`, `mdt-host-check.sh` (no FAIL) and the reconcile timer running.
5. Rollback (any failure, or `restore=pending`): `accept-restore.sh --evidence <EV>` (exit 1 = a lowering is deferred:
   report it, never force it), then `DEV_HOST_MEMORY_KEEP=1560M` (today's live r1002 pin) and `sudo ./install.sh`.
6. Third run at the lower reserve **K = 1280M** (`DEV_HOST_MEMORY_KEEP=1280M`): the only value below 1560M ever measured
   good (2026-11-15: 1280M passed, 767M failed with 107 MiB `MemAvailable` and 5.5 s ssh). 1077M is the *default* K
   (F 565 + M 512) and run 2 does **not** support it (measured M 746.7M, so floors + M = 1560M). Step 3's
   `k_last_pass ≤ 1280M` would make 1280M evidence-backed; above it, run at the printed K and record 1280M as untested.
   Nothing below `F_installed + 128M` is proposed.
7. `daemon-reload-check.sh` (design note §5.3 precedence); then the result into the host-setup backlog.

**Duration**: preflight 10 min; step 3 = 600 s warm-up + 6 × 300 s + 300 s margin ≈ 37 min; step 6 ≈ the same; eval and
apply ≈ 10 min → **≈ 2.5 h on r1002**, no reboot.

## 8. (g) Test plan for the kit package

Seams (no root, no DAMON, no wall clock in a verdict):

| seam | fakes |
|---|---|
| `MDT_ACCEPT_CGROUP`, `MDT_ACCEPT_PROC` (existing) | cgroupfs and `/proc`, now also the **write** path of §2.3 (accept writes, read back) |
| `MDT_ACCEPT_HOTSET_JSON`, `MDT_ACCEPT_DAMON` (new) | per-step/per-unit region fixtures instead of a session; and a fake `/sys/kernel/mm/damon/admin` with non-sequential `tried_regions` names, `age` in intervals, `avail_operations` without `paddr`, a failing filter write |
| `MDT_ACCEPT_DAMON_STAT` (new) | the fake `enabled` file: `N` proceeds, `Y` refuses exit 3 with no write |
| `MDT_ACCEPT_PROBE_CMD_<NAME>`, `MDT_ACCEPT_PROBE_TIMEOUT` (new) | per-probe argv/timeout; an overrunning probe is killed, recorded `rc=timeout`, and does not hang the step |
| `MDT_ACCEPT_SQUEEZE_STATE`, `MDT_ACCEPT_DWELL_TICKS`, `MDT_ACCEPT_TIMER_UNIT`, `MDT_ACCEPT_EXTRA_UNITS`, `MDT_ACCEPT_SSH_TARGET` (new) | the WAL path; the dwell counted in **ticks** (`MDT_ACCEPT_INTERVAL=0`), so no wall clock enters a verdict; `stop`/`is-active`/`start` of the reconcile timer on the existing fake `systemctl`; optional-unit presence; the ssh sample source |

New tests: the ladder (passing, a stop at the first psi failure, the `F + 128M` ceiling, an `--steps` cap); the
stop-rule arithmetic; `MemAvailable` and root-full aborts; one refusal per §2.3 bound; a HUP before the first write and
after the third with every original restored; `--rm` not relied on; `accept-restore.sh` twice on one state file, and on
one whose target is above `current − 64M` (`RESTORE-PENDING`, exit 1); a stale state file handled by the next run (one
NOTE); the v1-evidence refusal; `1280M` cannot appear in any output. Changed: `test_accept_kit.py`'s `HEAD` and
`write_run` fixtures (new columns, two new CSVs); `test_refault_fails_and_the_env_lines_are_not_to_be_applied` →
`…is_informational_and_does_not_change_the_verdict` (rc 0, `INFO`); the M and floor assertions move to `hotset`;
`test_the_probe_body_is_incompressible…` gains the per-step inflation and never-re-touch rule; the shell tests'
`TIER_MAX`/fake-run expectations move to the deepest-ceiling cap; `test_a_load_that_never_hit_the_ceiling_is_inconclusive`
becomes its per-step form. Lane: the existing `smoke`/`r0-r1` lane (the module is already in it); shellcheck extends to
the two new scripts. No new lane.

## 9. (h) Risks and open questions (default taken)

1. **systemd may rewrite a container scope's `memory.min`** (any property change or reload), silently un-protecting the
   dummy mid-step. Default: no `daemon-reload` during the run (refuse when `NeedDaemonReload=yes`), read the scope's
   `memory.min` back after every dwell, record a `min_drift` INFO, and mark the step INCONCLUSIVE on drift.
2. **The DAMOS `memcg` filter may attribute nothing for a slice** on this kernel (`SCRIPTS.md:258`). Default: per-unit
   self-test, refuse that unit's floor with a NOTE and keep its table row — never read 0 hot bytes as "no hot set".
3. **DAMON overhead could distort the latencies we judge** (up to 8 kdamonds × 1000 regions at 400 ms). Default: keep
   the sessions during the dwell (the operator wants hot sets *while in use*) and add one control pass with DAMON
   stopped at step 0; > 25 % difference prints INFO, and the hard criteria keep the DAMON-on numbers (conservative).
4. **The squeeze can OOM before a criterion fails.** Default: the §2.4 guards abort the ladder and mark the step FAIL —
   never trade a host OOM for one more data point.
5. **1280M may be the wrong target for run 3** (DAMON floors can be lower *or* higher than today's 644M, and
   `M_squeeze` may exceed `M_unpressured`). Default: run at `max(1280M, K proposed)`, and treat anything below
   `F_installed + 128M` as untestable rather than as a candidate.

## Appendix: the kit package's file list (next package)

`tools/accept/accept-run.sh` (steps, writes, WAL, probes, DAMON phase); `tools/accept/accept-eval.py` (step verdicts,
DAMON floors, M re-check, v1 refusal); new `tools/accept/accept-hotset.py` (paddr + `memcg` filter via
`scripts/damon-analysis/lib`) and `tools/accept/accept-restore.sh`; `tests/test_accept_kit.py`; `README.md`
"Acceptance and measurement kit"; `mdt-cockpit/CHANGES.md` `[Unreleased]`. No new stored key is planned, so
`host-setup.env.example` stays untouched.
