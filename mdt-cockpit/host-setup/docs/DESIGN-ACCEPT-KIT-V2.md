# Design: acceptance kit v2 — squeeze test and DAMON floors (P-plat-19k)

Status: design only (2026-11-25; corrected the same day from the P-plat-19k r2 review — one H, ten M, and the L/nit items — which are binding for the
kit package), from the operator ruling of 2026-11-24 19:4xZ (`.controller-state/operator-decisions.md`): re-measure host memory with a **squeeze
test**, take the core services' hot sets from **DAMON**, make **pressure and ssh/service latency the hard criteria**, refaults **informational**,
re-check **M**, and plan a **third run at a lower reserve**. The next package implements this. It supersedes the refault criterion of
`DESIGN-DEDICATED-DEV-MEMORY.md` §4.1 and keeps that note's `K = F + M` model, bounds and unit table. Scope: `HOST_ROLE=dedicated-dev`, r1002 first.

## 1. Today (v1, P-plat-19g) versus v2

| | v1 | v2 |
|---|---|---|
| load | two capped probes (`accept-run.sh:44-59,89-96`), 3.8G anon + 4G file, one per tier | **one** dummy in dev-gates, sized to dev.slice's non-reclaimable part under `memory.max(K0)` and inflated by **exactly 128M/step**, so dev.slice stays **at its ceiling**; `memory.min` keeps its protected part resident, bound 3 caps that, and the ceiling is raised per step so the **host reserve** shrinks |
| what varies | nothing: one 30 min window at one K | `K(s) = K0 − s·Δ`, steps 1-6, 300 s dwell, each step judged alone |
| floors | `1.25 × max(active_anon+active_file)` (`accept-eval.py:221`) — a proxy | DAMON `sz_ops_filter_passed` per leaf ×1.10, printed **only when this run proved it under squeeze** (lower rows stay candidates, §4.3) |
| refault | hard criterion, 100 pages (`accept-eval.py:88-97,188`) | informational only (§3.4) |
| pressure | system.slice + user.slice `some ≤ 1` | + cgprofile.slice, all three also `full ≤ 1`; root rows INFO; `MemAvailable` and unit-`full` aborts (§3.1, §2.4) |
| ssh | one timing, `loaded ≤ 2 × idle` | per step, p95 of 10 vs step-0 `ssh_base`, judged by §3.3's contention-agnostic rule; `MDT_ACCEPT_SSH_TARGET` required **and** verified to be this host (§3.2) |
| "doing their job" | none | seven liveness probes, commands, timeouts, contention-agnostic latency (§3.3) |
| M | whole window | `p99(U) + p99(init.scope) + W + 64M` over the **deepest passing step**, unpressured cross-check (§5) |
| DAMON | unused | one paddr kdamond, one `stat` scheme per leaf, chain `memcg <leaf>` **reject** → `young` **allow**, 400ms/8s (§4) |
| cleanup | traps armed before start (`accept-run.sh:61-74`) | reconcile **lock** pause + one name + **WAL** + ExecStopPost restore (§2.3, §2.6) |
| evidence | `samples.csv`, `manifest.env`, `ssh.txt` | + `probes.csv`, `hotset.csv`, `squeeze-state.env` (§6) |

## 2. (a) The squeeze procedure

**2.1 K0, steps, ladder.** K0 = `DEV_HOST_MEMORY_KEEP` from `$HOSTENV` when set, else `mdt_capacity.host_floors(...).keep`; neither readable → exit 3,
never an invented K0 (`k_tested`). Step **Δ = 128 MiB**, dwell **300 s** (60 s settling, the last 240 s judged). Step s writes ceiling `DEV_MAX_START
+ s·Δ` on `dev.slice` **and** `dev-gates.slice`, where `DEV_MAX_START` is `dev.slice/memory.max` read at start (step 0 writes nothing — it is the read
itself); `K(s) = K0 − s·Δ` is the **label** printed beside it. The preflight proves the labels are true: `DEV_MAX_START = MemTotal − MDT_EXCLUDED_RAM
− K0` and no `DEV_MEMORY_MAX` pin, else exit 3 (L-b: the old `MemTotal − K(s)` ignored the excluded RAM, and a pin makes every K label a lie).
`--steps` default 6, i.e. steps 1-6 after the always-taken step 0, so a run has `--steps + 1` dwells. A step is only taken while `K(s) ≥ F_installed +
128M`; `F_installed` = `MemoryMin` of `system.slice` + `cgprofile.slice` + `user.slice` from `systemctl show` — the units' floors are already inside
system.slice's derived value and are **not** added again (that double-counts ≈ 420M on r1002 and stops the ladder about two steps in); unreadable →
exit 3. Only **raises** are written during the run (lowering happens only in the restore, §2.6's 64M rule). **The mechanism, spelled out.** Raising
`memory.max` takes nothing from the host by itself, so the dummy must be the dev tree's bulk (a 1 GiB dummy under a 6.6 GiB ceiling squeezes nobody).
At §2.2's size it carries the dev tree at its ceiling, its protected pages sit under `memory.min` (the tier's and dev.slice's too) so they are not the
kernel's first choice, and once the dev tree plus what the host really needs (kernel memory, PID 1, watermarks, the floored units' above-floor growth)
exceeds `MemTotal`, reclaim and allocation stalls land on the **unprotected** memory — page cache and the host services' working set above their
floors. That is the squeeze §3 measures, and it is why the ladder ends when the host stops answering, not when the dev tree is full.

**2.2 The dummy container.** `tester-unified:local` (v1's default: no new host dependency, it has `python3`); `docker run -d --rm --name
mdt-accept-squeeze --label mdt.accept=1 --network none --cpus 1 --cgroup-parent dev-gates.slice --memory <deepest ceiling> --memory-swap <that + 16G>
--oom-score-adj 1000`, so a declared cap never binds before the tier ceiling under test; placement and caps read back, mismatch → exit 3 (like
`accept-run.sh:90-96`). `--oom-score-adj 1000` is deliberate: a global or dev OOM before §2.4 fires then picks the dummy **before any adj-0 task**
(the operator's gate lane), and the services carrying `OOMScoreAdjust −900` of their own design are picked last of all. Allocator: the dummy starts at
`A0 = dev.slice/memory.max(K0) − D0 − 64M`, never below 256M, where `D0 = anon + kernel + shmem + file_mapped` of `dev.slice/memory.stat` at start —
dev's non-reclaimable part. dev's own limit reclaim then evicts the stale cache to make room, instead of global reclaim draining dev (a 256M headroom,
and `memory.current` counting GiBs of stale page cache, would leave the dummy at max − 256M or worse, outside §3.5's 128M tolerance; 64M is smaller
than that tolerance). It grows by **exactly Δ per step**, on `SIGUSR1` (§2.3's sequence), never on a timer, so **dev.slice stays at its ceiling while
the ceiling rises** — the mechanism §2.1 needs. The anon is `os.urandom` (zlib ratio > 0.95, pinned by
`test_the_probe_body_is_incompressible_and_ends_by_itself`); the **protected** part is never re-touched — `memory.min` keeps it resident, and §2.3's
bound 3 caps how much of the dummy is protected. The **unprotected** part is kept hot: every second the dummy reads one byte per 4 KiB page of its
newest `dummy_unprotected` bytes, round-robin, at most 64 MiB/s — that is what holds dev.slice at its ceiling instead of letting kswapd page out the
coldest anon on the machine. A 1 Hz read of the newest 16 MiB proves the job runs. The process exits by itself after `warmup + (steps + 2) × dwell +
600 s` — a **safety cap only** (step 0, the DAMON-off control pass and slack are in it), and the kit always removes the dummy by name; at v1's `warmup
+ steps × dwell + 300 s` it exits inside step 6, taking the containerd probe (`docker exec` fails = a hard FAIL) and the scope's `memory.min` with it
(N3).

**2.3 Which cgroup file, who writes it, with what bound.** The kit writes kernel files **directly** and never `systemctl set-property` (README: no
hand-set memory properties; check.sh FAILs on runtime overrides and on a systemd/kernel mismatch). It therefore **pauses the reconcile instead of
stopping it**: `accept-run.sh`, `accept-restore.sh` and `daemon-reload-check.sh` take the lock exclusively; the run holds it for its whole length.
`install.sh` and `mdt-dev-governance-reconcile.sh` take it shared. Their shared holds do not identify one another as a live acceptance run, while
either is refused or skipped when an exclusive kit operation holds the lock. `install.sh` holds its shared lock from the commit point through all
drop-in writes and `systemctl daemon-reload`, so an exclusive run cannot start in that interval; the reconcile's timer tick or explicit service start
may run during install's shared hold. A converge-only skip is not enough: install.sh's drop-ins plus `systemctl daemon-reload` also write the K0-based
`MemoryMax` straight into dev.slice, up to 6 × 128M below `memory.current`, with no safe-lowering rule, and the 5 s readback would see it only after
the reclaim and the dev OOM. The reconcile **holds** its shared lock (not merely tests it) while it runs its own memory block, so an exclusive kit
operation cannot begin during that block. The kernel releases an exclusive kit lock on **any** death of the kit, SIGKILL included, so the next timer tick converges the host back to systemd's
values under its own safe-lowering rule (`mdt-dev-governance-reconcile.sh:329-344`) — no stopped timer to restart, no stuck lowered reserve, no wait
for a reconcile service that is mid-run. It refuses (exit 3) when `/run/mdt/reconcile/memory-deferred` exists. **Mid-run drift.** A foreign
`install.sh`, a daemon-reload or the converge re-applies dev.slice/dev-gates properties, and a `memory.max` written below `memory.current` loops
reclaim then OOMs *inside* dev.slice while the mins are stripped; so **every 5 s tick reads all five files back**, and any value differing from the
last kit write aborts the ladder, marks the step INCONCLUSIVE `drift:<file>` and runs `finish` — not only the scope `min`, and not only once per
dwell. All computed values are PAGESIZE multiples (MemTotal, MiB steps, `memory.current`), and the comparison is against **the read-back of the kit's
own write**, never against the value it asked for (L-m). The kit's own processes run in `mdt-accept.slice` (`--slice=mdt-accept.slice`, §7 step 3): a
root child with no floor, counted in Σ root children and in `U`, instead of landing in system.slice and charging its stalls against the system.slice
criteria; §7 step 3 gives that unit `-p MemoryMin=64M` (L-j), so the 5 s guard loop is not the least protected process under the squeeze it guards
(the adj-1000 dummy is the other backstop). Per step, parent before child:

| # | file | value | refused before the write |
|---|---|---|---|
| 1 | `dev.slice/memory.max` | `DEV_MAX_START + s·Δ` (raise) | non-raise vs the value read at start (L-b: step 0 also proves the start value) |
| 2 | `dev.slice/dev-gates.slice/memory.max` | the same | > the parent's new value |
| 3 | `dev.slice/memory.min` | `start value + min(dummy anon, dev max − 1G − start)` | > `dev.slice/memory.max − 1G` (design note §4) |
| 4 | `dev.slice/dev-gates.slice/memory.min` | the same protected amount | > the tier's `memory.max` |
| 5 | `dev.slice/dev-gates.slice/docker-<id>.scope/memory.min` | `min(dummy anon, its memory.current)` | > its `memory.current` |

Each `min` is a **raise from its start value**, never a replacement: dev.slice's min is Σ every child Min key (design note §5.2), and overwriting it
would strip the other tiers' floors. A child's effective `memory.min` is capped by its ancestors (design note §2.2), hence 3 and 4. Bound 3 therefore
caps how much of the dummy is protected; the unprotected rest is recorded as `dummy_unprotected` in `samples.csv` (§6). **The order inside a step** —
the dummy never grows before the ceiling is raised, and no min is raised before the anon exists:

1. rows 1-2 (raise both maxes);
2. `docker kill -s USR1 mdt-accept-squeeze`; the dummy allocates Δ more;
3. wait until the dummy's scope has grown by ≥ Δ − 16M for ≤ 60 s, else the step is INCONCLUSIVE `grow`;
4. bound 2 (§2.4), then rows 3-5;
5. read back all five files, and only then start the 60 s settling window.

The dummy's argv carries `A0` and `Δ`. It allocates `A0` at start and exactly `Δ` on each `SIGUSR1`, never on a wall-clock tick, so it cannot drift
against the kit's ladder.

**2.4 The bound: the host is never squeezed into OOM.** At every 5 s tick: (1) `MemAvailable ≥ 128 MiB`, else abort `mem_available`; (2) Σ
`memory.min` over root children ≤ `MemTotal − SQUEEZE_UNPROTECTED`, 256 MiB = 2.5 × the largest measured W (~100 MiB) — the only invented number here,
it guarantees reclaimable cache and is never a measurement; a step that would break it is not written. With §2.2's multi-GiB dummy this bound is
**defence in depth**, not the guard: `Σ min ≤ F_installed + dev min ≤ F_installed + (dev max − 1G) ≤ MemTotal − 1152M` by bound 3 and the ladder
floor, so it cannot bind (L-a) — and the same arithmetic proves ≥ 1.1 GiB is always unprotected, which is why §2.2 keeps that part hot. **Bound 3 plus
the ladder floor are the real guard.** Bound 2 stays, checked before §2.3's writes 3-5; (3) global `oom_kill` delta > 0, or system.slice **or**
user.slice `memory.pressure` `full avg10 ≥ 5` held 10 s (psi-guard's own trigger numbers, `mdt-psi-guard.py:55,66-70`: `HOLD_SECONDS` 10,
`host_memory_full` 0.05) → abort. A failed safety guard is a step FAIL; inability to append its WAL or apply/read back a cgroup write is INCONCLUSIVE.

**2.5 Stop rule.** The ladder stops at the first step that FAILs any §3 hard criterion, or is INCONCLUSIVE for any reason except **load was real**
(including missing data, a failed WAL append, or a cgroup write/readback failure), or where the next `K(s) < F_installed + 128M`, or `--steps`
is exhausted. A step whose only miss is **load was real** does not stop the ladder: it is INCONCLUSIVE, never `k_last_pass`, and the next step is taken. Recorded: `squeeze_last_pass_step`/`k_last_pass`,
`squeeze_first_fail_step`/`k_first_fail`. A **failing step 0 is FAIL (exit 1)**: K0, the live configuration, is itself not survivable. The FAIL header
then prints **no K at all**: no K of this run passed, so any number would name a K the run never exercised — step-0 `K0 + 128M` was exactly that
invented value. It prints `pin DEV_HOST_MEMORY_KEEP above the tested K0=<K0>M (step 0 failed at the live configuration); this run measures no value`
(v1's no-number form, `accept-eval.py:244`), and main then runs §7 R. A step-0 INCONCLUSIVE (missing data or load not real) is INCONCLUSIVE (§3.5).

**2.6 Cleanup on every exit path, including SIGHUP, SIGKILL and an OOM kill.** Every side effect is appended to `$EVID/squeeze-state.env` **before**
it is applied (write-ahead): the five original cgroup values **and the value the kit wrote for each** (max ×2, min ×3), `boot_id`, `k_tested`,
`started_utc`, `pid`, the kdamond index and every docker name it created. Restore derives from that file, never from in-memory variables. The MDT-110
HUP flake is the precedent and its cause stays **unexplained** (`mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md:2016-2023`: the r1002 run read `rm bg, rm
bg`, and `finish()` removes both names unconditionally at `accept-run.sh:67`); v2 adds the backlog's two subtests (a HUP between a WAL line and its
write, a HUP during `docker run` before the id is known) and logs every `rm` with its exact name. Traps armed **before** the first state line: `EXIT`,
`INT 130`, `TERM 143`, `HUP 129`; `finish` begins with `trap '' INT TERM HUP` and a re-entry guard, then: (1) `docker rm -f` the exact names (`--rm`
not relied on) with every name logged; (2) poll `dev.slice/memory.current ≤ original − 64M` for ≤ 60 s; (3a) the three `memory.min` originals first,
child before parent, **unconditionally** — a lowering of min never OOMs, and a missing scope directory (the dummy's `docker-<id>.scope`, removed by
finish step (1)) counts as restored; (3b) then the two `memory.max` originals, child before parent, each only while `memory.current ≤ T − 64M` (design
note §6.2), else up to 8 chunks of `memory.reclaim` 256M on dev-gates.slice (never dev.slice itself, the reconcile's own rule) and then a deferral: a
deferred max leaves `RESTORE-PENDING <file> <target> <current>` in `manifest.env` and keeps the state file, and the reconcile — unlocked by the ending
run — completes it; (4) write `off` to the WAL-named kdamond's `state` and read it back — the kit **never writes `nr_kdamonds` in the restore**: any
write to it removes and recreates **every** kdamond directory and fails `EBUSY` while one is `on`, so there is no append and no per-index removal
(v1's "append one index, remove only that index" was unimplementable), and an `off` kdamond directory is harmless; **at setup** any kdamond `on` →
exit 3, `nr_kdamonds = 0` → write 1 and use index 0, otherwise use the highest index (which is `off`), record it in the WAL and overwrite its
configuration (a cgprofiled session started mid-run then fails `EBUSY` on its own write — a cgprofile NOTE, never a host effect, and §4.4's
`sessions_live == 0` makes it unlikely); (5) `end_utc`, `restore=ok|pending`, probe end states, and delete the state file only on `ok`. **Compare,
then restore.** The five originals are captured **once**, at start (`DEV_MIN_START`/`TIER_MIN_START` beside `DEV_MAX_START`), and every WAL line of
a relpath carries that start value as its original — a `memory.min` read again at step s is the kit's own step s−1 raise, never the original. In the
restore the **first** WAL line of a relpath supplies the original and **any** value the kit wrote for it counts as the kit's (a death between a line
and its write leaves the previous step's value, which is still the kit's own). A value the kit never wrote means another writer has taken over: it is
NOTEd, **not** overwritten, and reported loudly as `restore=foreign:<rel>`; the restore then exits **1** and **keeps the WAL** (the stricter of the
two options: only a later write or the reconcile can prove what that file should hold, and `restore=ok` must never be printed over an untouched foreign
value). A failed WAL append aborts the run before the cgroup write it protects (no write without a record), and the preflight checks free space on the
**evidence** filesystem (`/root`, not the Docker data root), because a full disk would silently degrade write-ahead to write-only. Every `docker rm -f`
on a restore path (the kit's own `finish` and `accept-restore.sh`) is bounded by a timeout (`MDT_ACCEPT_RM_TIMEOUT`, 30 s) whose failure is logged and
**never** stops the min restores or the kdamond `off`; a squeezed dockerd stalls `rm` exactly as it stalls the probes. **A reboot mid-run** is a different `boot_id`: the boot already reset the cgroup files, so the
restore writes nothing, clears the state file with a NOTE and exits 0. **`accept-restore.sh`** (new verb) repairs a state file left by SIGKILL:
idempotent, exit 0 ok / 3 nothing to do / 1 pending, and a later `accept-run.sh` that finds one restores first with one NOTE. It takes the same flock
(`-w`, see below) and refuses while a live run holds it. **A unit that ends by itself.** The `systemd-run` unit carries its own restore: `-p
ExecStopPost='/…/accept-restore.sh --from-unit --lock-timeout 30 --evidence <EV>'` runs after **any** end of the main process — SIGKILL and an OOM
kill included, which the traps never see — so a kit death cannot leave the host at the last lowered reserve with the reconcile unpaused. `--from-unit`
makes it suitable for ExecStopPost: exit 3 ("nothing to do", the normal outcome after a clean run whose trap already restored) becomes exit 0 so the
unit does not end failed, and it waits up to `--lock-timeout` seconds with `flock -w` instead of `-n`, because a slow-dying child can still hold the
inherited descriptor for a moment. No `pkill`, no pattern `kill`. **The WAL's lines** (one per write, appended before the effect):
`MAX=<relpath>|<original>|<value the kit wrote>|<utc>` and `MIN=<relpath>|…`; for each relpath, restore uses the original from its first line and
accepts any value written by the kit (a later line's original is the previous value the kit wrote), plus `container=<exact docker name>` per created name, `boot_id=<id>`, `kdamond=<idx>`,
`dev_orig=<bytes>`, `k_tested=<label>`, `pid=<kit pid>`, `started_utc=`. A restore that runs as the kit's **own** finish takes `--in-run`, because that
caller already holds the lock (waiting on it would deadlock); a lock that cannot even be opened is **indeterminate**, never "no run is live" (install.sh
and the reconcile distinguish "held" — `flock -E 99` — from "cannot open", and both refuse or fall back on the correct side of that difference), and an
unreadable `boot_id` restores rather than skipping, because restoring is the safe direction. Anyone else reading a WAL: `restore=pending` never means
the host is still squeezed in a max, only that one lowering waits for the reconcile.

## 3. (b) Hard criteria, with numbers

**3.1 Pressure** — every 5 s; per step the **max** over the last 240 s of the dwell. The root rows are **INFO only**: with dev.slice at its ceiling
(§2.2) the dummy and the lane stall on dev's own limit reclaim continuously, so root `some`/`full` include those stalls and would FAIL a step for the
load doing exactly what it is meant to do.

| cgroup | field | ≤ | why |
|---|---|---|---|
| `system.slice` | `memory.pressure` `some avg10` | 1.00 | v1's threshold; run 2 measured 0.00 |
| `user.slice` | `some avg10` | 1.00 | v1; recovery shell, `sudo`, `systemctl` |
| `cgprofile.slice` | `some avg10` | 1.00 | new: a floored host service outside system.slice |
| the same three | `full avg10` | 1.00 | hard too: full = every task stalled |
| root | `some`/`full avg10` | — | **INFO**, printed, never judged (§2.4 aborts on the units' `full`) |

`some`/`full` are stall **fractions**, so `avg10` is a percentage: psi-guard's `unit_memory_some` 0.10 is a 10 % stall (`mdt-psi-guard.py:65-70`), not
a 0.10 % one — 5.00 is **0.5×** that threshold, not 50×.

**3.2 ssh latency** — `MDT_ACCEPT_SSH_TARGET` is **required** on v2 (preflight exit 3 when unset): root on r1002 needs a non-interactive login to it,
and §7 step 2 verifies one BatchMode round trip before the run. **The target must be this host over loopback** — e.g. `root@127.0.0.1` with a
dedicated key: the preflight runs `ssh <target> cat /proc/sys/kernel/random/boot_id` and refuses (exit 3) unless it equals the local `boot_id`,
because a remote target would time *that* machine's sshd and the network, so every step could pass while r1002's sshd, PAM and user.slice are stalled.
Each sample is `timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=10 <target> true`; a timeout counts as a 15 s sample. The baseline `ssh_base` is the
**max of 3** at step 0 before any raise (its slow sample included, so one warm-up outlier cannot license every later step); the step value is the p95
of 10 samples across its judged window; the rule is `p95 ≤ max(2 × ssh_base, ssh_base + 0.5 s)`, judged **contention-agnostically** like §3.3: a step
FAILs when ≥ 2 of the 10 samples exceed it, or any sample hits the timeout, or the median exceeds it; a single outlier is INFO (the p95 of 10 is the
maximum, and one slow sample under a starting lane would otherwise fail a step for a contention fact, not a memory verdict). Both 19g anchors (0.566 s
passed at K 1507M in run 2, 5.5 s failed at K 767M on 2026-11-15) were **remote** round trips: they are INFO, never comparable thresholds, and the
rule stays relative to this run's own `ssh_base`. The v1 manual `ssh.txt` path is **removed** for v2 (it cannot deliver "p95 of 10 per step" by hand,
and a single hand line would leave steps ≥ 1 with no data); the samples go to `probes.csv` as `probe=ssh`. No samples → INCONCLUSIVE.

**3.3 Per-service liveness probes** — every 30 s inside the judged window (8 per step), each `timeout`-wrapped, rc + seconds + detail into
`probes.csv`. **A probe that wrote no row at all is MISSING DATA, so the step is INCONCLUSIVE, never a pass** (the same rule as §3.5's missing hard
criterion, applied per probe: the kit cannot see the harm it could not measure, L-i); a row with a failing rc, or a latency rule breach, is a FAIL.
`latency rule` = `max(2 × step-0 p95, step-0 p95 + 0.5 s)`, judged **contention-agnostically**: a step FAILs a latency rule when **≥ 2
samples exceed it, or any sample hits the timeout, or the median exceeds it**; a single outlier is INFO. The rule applies to the probes whose pass
rule **is** latency; the two exceptions are named in the judge (`LATENCY_EXEMPT`): `psi_guard`, whose pass rule is "active + ticks advance" and whose
recorded seconds are its own probe duration (never the run's elapsed time, which grows by a dwell per step and would FAIL a healthy daemon around
step 3-4), and `cgprofiled` in both forms (CLI present or degraded), whose pass rule is status/version or INFO respectively, never latency (the p95 of 10 samples is the max, so one slow
`docker info` under a starting lane would otherwise fail a step for a contention fact, not a memory verdict). `systemctl_read` calls use
`timeout -k 1 2`; the degraded cgprofiled fallback uses `timeout -k 1 5`. Timeout rc is recorded as `timeout`; a missing or malformed `NRestarts`
makes the containerd criterion INCONCLUSIVE.

| unit | probe | timeout | pass rule |
|---|---|---|---|
| sshd | `systemctl is-active ssh.service` + raw TCP to `127.0.0.1:22` whose first bytes are `SSH-2.0-` | 2 s / 2 s | active **and** banner seen |
| journald | `logger -t mdt-accept-<nonce> <nonce>`, then poll `journalctl -t mdt-accept-<nonce> --since @<t0> -o cat -n 1` every 100 ms until it prints the nonce, up to 5 s (an immediate read races `logger`) | 5 s | exit 0, nonce visible, seconds = time to visibility, within the latency rule |
| dockerd | `docker info -f '{{.ServerVersion}}'` | 10 s | exit 0 and version-shaped output, within the latency rule |
| containerd | `docker exec mdt-accept-squeeze true` **and** `systemctl show -p NRestarts containerd.service` unchanged since step 0 | 10 s / 2 s | exit 0 **and** a present unchanged restart count (the shim still lives; `docker inspect` never reaches containerd) |
| cgprofiled | `cgprofilectl --json status`: exit 0 and a daemon version present | 10 s default (CLI) / 5 s (degraded) | exit 0 and version; CLI absent → `systemctl is-active` + socket file, recorded `degraded` as **INFO**, and the step verdict ignores it (§4.4's absence is a NOTE, not a refusal) |
| psi-guard | `systemctl is-active` + `utime+stime` of its PID advances ≥ 1 tick over a 30 s window | 10 s | both: "active" alone does not prove the 1 s loop runs; USER_HZ=100 makes a 5 s / 0.01 s window flaky |
| fleetd agent, hostobs probe (`MDT_ACCEPT_EXTRA_UNITS`) | `systemctl is-active` + MainPID alive (event-driven daemons burn ≈ 0 CPU, so a CPU-advance probe would FAIL forever) | 10 s | active and MainPID alive, **INFO** until each has a health verb; `not-installed` when `LoadState` ≠ `loaded`: a host fact, never evidence for a floor |

**3.4 Refaults: informational only.** `workingset_refault_{anon,file}` deltas per unit/step go to `samples.csv` and are printed with an `INFO` prefix;
they never move the verdict. Both runs of 19g failed *only* on it while every behavioural criterion passed (run 2: `docker.service=+1593`,
`systemd-journald.service=+53102`, psi 0.00, ssh 0.566 s, no OOM), and a rising refault is the evidence that warm pages were paged out — the intended
result. One derived INFO per step: `refault ≥ 0.25 × the unit's hot set` prints `floor likely too small` (a hint for the next run).

**3.5 Step and run verdicts.** Per step each criterion is PASS/FAIL/INCONCLUSIVE, missing data names itself and is never a pass (v1's rule). **Load
was real** is a per-step hard criterion, so a step cannot PASS without a squeeze: `dev.slice/memory.current ≥ dev.slice/memory.max − 128M` on ≥ 80 %
of the judged ticks, **and** `MemFree ≤ 2 × W` on ≥ 50 % of them; otherwise the step is INCONCLUSIVE, never PASS, and it can never be `k_last_pass`.
It is the only INCONCLUSIVE cause that continues the ladder (§2.5). The run is PASS when ≥ 1 step passed, F, M, K are derivable, and no blocking
INCONCLUSIVE occurred; FAIL when a hard criterion failed at step 0; INCONCLUSIVE for other missing or unmeasured data (including WAL or cgroup write
failures), even if an earlier step passed. Exit codes stay 0/1/4/2.

## 4. (c) DAMON hot set

**4.1 Method: one paddr kdamond, one `stat` scheme per leaf.** `cgprofile`'s daemon has only per-pid **vaddr** targets (`CP-3`: paddr/DAMOS-filter mode
open, not built) and `cgprofilectl` cannot target a cgroup or slice yet (`CP-55`). So the kit drives the generic sysfs interface through
`scripts/damon-analysis/lib/damon_analysis.py`'s `SysfsInterface` (the accept checkout therefore gains that lib path, §6): **one** kdamond, **one**
context, 400 ms / 8 s, 10/1000 regions (guide §12.3, §13; guide §14's parallel kdamonds are not needed). **The filters make the per-unit number, not
the regions.** A paddr region is a physical-address range of the *whole* RAM, and the DAMOS `memcg` ops-filter decides which pages of a region the
scheme counts, not which regions exist — so Σ(`end − start`) of qualifying regions is the **machine's** hot set, identical for every unit and
dominated by the dummy, the lane and the page cache. Each leaf therefore gets its own `stat` scheme and the access pattern `nr_accesses 0..max, age
0..max`; the leaf's hot bytes are the scheme's filter-passed bytes. **The chain is an AND** — with both filters `allow` and the memcg filter first,
every page of the leaf would pass hot or cold, which is the `sz_ops_filter_passed` = "leaf resident size + the machine's young bytes" flaw again:

- `ops_filters/0`: `type memcg`, `memcg_path <leaf>`, `matching N`, `allow N` — **rejects** every page that is not the leaf's, so the young
  check below never runs on, or clears (mkold), another leaf's pages;
- `ops_filters/1`: `type young`, `matching Y`, `allow Y` — the leaf's young pages pass; the leaf's old pages match nothing and fall to the
  default, which after an allow filter is a reject.

DAMOS evaluates ops-filters in order and **the first filter a page matches decides** (`DAMON-GUIDE.md:278-279`; the kernel's `damos_pa_filter_out()`
returns at the first match), and a page matching no filter gets the reverse of the last filter's decision. So `sz_ops_filter_passed` is the leaf's
pages accessed since the previous apply. The polarity follows `analyze_container.py:209` (`reject none memcg <path>`). The ops-filter write
(`.../schemes/<i>/ops_filters/<0|1>/*`) is the one step the lib has no method for, and is the kit's only new sysfs writer
(`lib/damon_analysis.py:282-312` never reads `sz_filter_passed` and `:417` uses `end − start`, so that tool's physical mode has the same flaw — a
backlog entry goes to `scripts/damon-analysis/KNOWN_ISSUES_TODO_BACKLOG.md` for both, and for one more thing: `DAMON-GUIDE.md:269` lists `young` as
*core-layer handled*, while the kernel handles it as an **ops filter**, which is why the kit writes it under `ops_filters/` and not under
`core_filters/`). **A slice is the sum of its leaf memcgs** (§4.2), never the slice's own cgroup: the kernel's memcg filter matches the folio's memcg
id exactly, not its descendants, so `user.slice` and `dev-interactive.slice` read ≈ 0 at their own directory. **Self-test.** `sz_tried` counts regions
that matched the access pattern *before* the ops filters, so it is non-zero whenever anything on the host is hot and proves nothing:
`sz_ops_filter_passed = 0` while that unit's `memory.current` is above 1 MiB refuses that unit's floor (an unreadable filter is never "no hot pages").
A seam fixture pins the chain: a cold page of the leaf counts 0, a young page of **another** leaf counts 0, and a young page of the leaf counts its
folio size. **Capability checks.** Setup requires `stats/sz_ops_filter_passed`, the `young` filter, the `ops_filters/` directory with its `allow` file,
and an exact `paddr` line in `avail_operations` after creating context 0; the kernel lists one operation per line, and a refusal names every listed
operation. Read-only preflight requires the exact `CONFIG_DAMON_PADDR=y` line in `/boot/config-$(uname -r)` when that file is readable; if it is absent
or unreadable, it warns and setup's live `avail_operations` check remains authoritative. The kit never falls back to `end − start`. **How many
schemes, and their cost.** One scheme per **leaf**, not per unit or slice: a slice has no own hot set. The complete input set is freshly enumerated and
rewritten at setup, after warm-up and before step 0's settle window, and at every later step boundary. A boundary retry re-enumerates and rewrites the
whole set before committing it to the running kdamond's `state`; after all attempts fail the boundary is INCONCLUSIVE. Setup retries the same full
enumeration and input write before starting the selected daemon. Samples never write scheme inputs or send `commit`; they issue only the stats and
`tried_regions` state commands, then read the committed scheme results. A sample compares each unit's live leaf paths and directory inodes with the last
committed set both before and after the apply. If either set differs, it records a step-scoped drop reason and emits no row for that whole unit; other
units still produce rows and the run continues. The next boundary can configure the changed set so that unit can be measured again. If no unit retains
its full committed leaf set, the sample is INCONCLUSIVE. A configured floored unit with no leaf has no row and retains its recorded unit refusal. A
topology drop does not create the run-wide `hotset-refused` marker. `user.slice` may therefore remain untested when its session scopes change between
boundaries; its installed floor stays in force. Each `stat` scheme walks **all of RAM** folio by folio every aggregation interval: with 15-20 leaves
that is 30-40 M folio checks per 8 s. Risk 3's "1000 regions at 400 ms" understates the cost; it is that walk the DAMON-off control pass measures.

**4.2 Parameters and "hot".** `sample_us`/`aggr_us` = 400 ms / 8 s (guide §12.3's recommendation); `min/max_regions` 10/1000; `refresh_ms` **0** — the
kit drives the updates itself and never lets the kernel's periodic stats refresh alias the scheme's 8 s apply interval (at 10 s, consecutive deltas
span 1, 1, 1, 2 applies every 40 s, the p95 lands on the 2-apply ones and H ≈ 2× the real hot set); session length = the dwell (≥ 300 s, the guide's
5-minute minimum); region cost is `age × aggr_us` (guide pitfall 13). **Hot** ⇔ accessed within one 8 s interval, p95 over the **judged window** — the
last 240 s of every 300 s session, the same window §3 judges (one window, not two). **The per-interval value is Σ `tried_regions/*/sz_filter_passed`
after `update_schemes_tried_regions`**: exactly one apply, read once per aggregation interval; `refresh_ms` = 0 keeps its meaning independent of the
apply cadence. `H_unit` = the p95 of those values over the judged window, max over steps; the `stats/sz_ops_filter_passed` delta over the whole window
divided by its apply count (judged seconds / 8) is printed as an INFO mean. The regions-based Σ(`end − start`) number is printed beside it for
comparison only. The rate ≥ 5 % ∧ age ≥ 20 s rule is kept only as an INFO column (its integer threshold is ≥ 3 aggregation intervals: 20 s / 8 s =
2.5). Units: the six floored ones, `user.slice`, `dev-interactive.slice` (only to size its `MemoryMin`, never its Low), plus the optional units when
installed; a slice is enumerated once per step as the sum of its leaves (`user.slice/*/…scope|service`, `dev-interactive.slice/docker-*.scope`).

**4.3 The floor.** `floor(unit) = ceil(1.10 × H_unit)` (`FLOOR_FACTOR` = 1.10: one page per region per 400 ms plus merge/split churn; 1.25 was a blind
factor over a proxy that counted cold but LRU-active pages, and the operator's ruling replaces it). A row is printed **only when this run proved it**
(that unit has a measured row and no topology drop at every passing step, and its probes and ssh passed at every step with that floor); an unproved
unit keeps its table row and the eval names it. A candidate line includes the step and reason for any topology drop. A probe FAIL while
`memory.current ≤ floor` means the floor is too small → raise that row to `ceil(1.10 × its last-passing memory.current)` and repeat once (design note
§4.1); under §7 the repeat is **step 6's run 3 with that row installed**, not a second ladder here. Floors stay raise-only against a foreign drop-in,
and the derived `system.slice` floor is still summed post-raise under the 460M / 10 % bounds. **Proved, or only a candidate.** The squeeze runs with
the **installed** floors, so a derived floor ≥ the installed row is *proved* when that unit's probes and ssh passed at every step with it in force; a
derived floor **below** the installed row was never in force and is printed as `FLOOR <unit>=<v> source=damon candidate (installed <x>, untested)` —
it enters the `host-setup.env` lines only commented out, and it is proved only by a run with it installed (§7 step 6). Losing a floor is safe (the
host keeps its current, higher one); claiming one this run never exercised is not.

**4.4 The `mdt-damon-stat` conflict (guide §5.3).** `units/mdt-damon-stat.conf` installs `/etc/tmpfiles.d/mdt-damon-stat.conf` writing `N` to
`/sys/module/damon_stat/parameters/enabled` at boot (`host-setup.env.example:103`, `DAMON_STAT_DISABLE_DEFAULT="yes"`). At `Y`, `damon_stat` owns the
kernel-wide DAMON interface and every manual kdamond fails `Device or resource busy`. The kit **reads** it and refuses (exit 3) when it is not `N`,
naming the tmpfiles file and `install.sh --require-damon-disable`; it never writes the parameter (host policy, install.sh's job). Recorded as
`damon_stat_enabled`, and when the file does not exist at all the kernel has no DAMON_STAT: it records `damon_stat_enabled=absent` and **proceeds**,
never refuses. Second conflict: `cgprofiled` also multiplexes kdamonds — a live session refuses the run (exit 3) instead of adding a kdamond index
under a daemon; the check is `cgprofilectl --json status`'s `sessions_live == 0` (or `sessions --state live`), not bare `sessions`, which lists stored
sessions too and would refuse on any old one. The CLI's absence is a NOTE, not a refusal.

## 5. (d) The margin M re-check

The formula at design note `:122-124` **stays**: `M = p99(U) + p99(init.scope) + W + 64M`, `U = MemTotal − MemFree − Σ memory.current of every root
child`, `W = Σ zone high × page size`. What changes is **which ticks**: the deepest **passing** step's judged window, because that is where the
reserve was actually squeezed — `U` there is the memory no cgroup can give back, which is what M means. Also reported, not used: `M_unpressured` (same
formula over step 0) and the failing step's `U` as the bracket's upper edge. New: `M_squeeze` and `M_unpressured` differing by ≥ 25 % prints a NOTE
and the **larger** is taken — two disagreeing measurements are not evidence for the smaller, and the smaller is the one that can OOM a host. **`F_new`
is defined here: it is `mdt_capacity.host_floors` over the INSTALLED rows with the PROVED raises of §4.3 applied.** Candidate (below-installed) rows
never enter it, so `F_new ≥ F_installed` always holds and `K_proposed` is never lowered by a floor this run never exercised — which is exactly what
the illustrative block used to do. **v1's two clamps are replaced by one.** `K_proposed = max(F_new + M, k_last_pass)`, printed with
`DEV_HOST_MEMORY_MARGIN = K_proposed − F_new`, where `M` itself is never below 64M (`accept-eval.py:226`). The old "never below the margin the run was
made at" (`:226`) would forbid any lower reserve on r1002 (run 2's base margin was 916M, eval-run2.txt:22), and "never below the tested K"
(`:158-162`) would pin K ≥ K0: a K below `k_last_pass` was not tested and is never proposed, but a K above it is exactly what the squeeze is looking
for.

## 6. (e) Output

**`samples.csv`** keeps every v1 column and appends `step, k_target, dev_max, tier_max, dummy_min, dummy_unprotected, mem_available, root_some_avg10,
root_full_avg10, system.psi_full_avg10, user.psi_full_avg10, cgprofile.psi_some_avg10, cgprofile.psi_full_avg10, containerd_restarts, limit_source`.
v1 records only `psi_some` and §3.1 judges all three `full` rows too, so without those columns "missing data is never a pass" would make every step
INCONCLUSIVE. New per-event files: **`probes.csv`** (`utc, step, probe, kind, rc, seconds, detail`; the ssh samples live here too, as `probe=ssh`, one
row per sample), **`hotset.csv`** (`utc, step, unit, memcg_leaves, sz_filter_passed_one_apply, sz_tried, regions_hot_bytes_info`),
**`squeeze-state.env`** (§2.6; absent after a clean run). **`manifest.env`** gains `kit_version=2, squeeze_step, squeeze_dwell, squeeze_steps_run,
squeeze_last_pass_step, squeeze_first_fail_step, k_tested, k_last_pass, k_first_fail, damon_stat_enabled, damon_measure=ok|failed:<unit>:<why>,
floors_snapshot, limit_source, restore=ok|pending, probe_image, host_setup_env_sha256`. A v1 directory (no `kit_version`) is **refused with exit 2**,
naming the v1 kit: v2 criteria on v1 data would turn a missing squeeze into a pass.

Verdict lines (`INFO` is never folded into the verdict). The numbers are **illustrative** (K0 = 1560M, the run-2 pin, Δ 128M, **7 dwells**, steps 0-6
= 1560/1432/1304/1176/1048/920/792; the floors and M are placeholders, not measurements). The block is internally consistent: `F_new` 644M are
installed rows, `M` 742M vs unpressured 747M (under 25 %, so the squeeze's 742M is taken), `K proposed = max(644 + 742, 1176) = 1386M`, `MARGIN = 1386
− 644 = 742M`, and `ceil(1.10 × 18.6) = 21M`:

```
STEP 0 K=1560M PASS   load real 100%  psi 0.00/0.00/0.00 root 0.00 ssh p95 0.57<=1.13 probes 8/8 STEP 4 K=1048M FAIL   load real 100%  psi system.some
1.90 > 1.00; probes 6/8 (journald 2 timeout) SQUEEZE last pass K=1176M (step 3), first fail K=1048M (step 4): stopped by the psi criterion INFO
refault (informational, pages): docker.service +1593, systemd-journald.service +53102 (step 4) INFO hot set systemd-journald.service 21.4M (DAMON
sz_filter_passed_one_apply p95) -> floor 24M (table 24M, source damon) FLOOR ssh.service=21M source=damon:18.6M x1.10 tested=every step FLOOR
cgprofiled.service=26M source=damon:23.6M x1.10 tested=every step FLOOR mdt-psi-guard.service=5M source=table candidate (installed 5M, DAMON refused:
sz_ops_filter_passed 0) M measured=742M (deepest passing step 3) unpressured=747M set=742M (= K proposed 1386M - F 644M) K proposed=1386M = F 644M + M
742M (>= k_last_pass 1176M) VERDICT PASS == host-setup.env DEV_HOST_MEMORY_MIN="ssh.service=21M docker.service=231M containerd.service=92M
systemd-journald.service=24M" DEV_HOST_MEMORY_MARGIN=742M DEV_HOST_MEMORY_MEASURED="2026-11-25 /root/accept-..."
```

A floor **above** the installed row that passed its probes is **proved** (proved form); one genuinely **below** it is the `candidate (installed <x>,
untested)` form — the two are not interchangeable (§4.3).

The FAIL header keeps P-plat-19h's fix in **one** form: a FAIL is a step-0 FAIL, so no K of this run passed and the header prints **no number** — `pin
DEV_HOST_MEMORY_KEEP above the tested K0=<K0>M (step 0 failed at the live configuration); this run measures no value` (v1's no-number form,
`accept-eval.py:244`), and main runs §7 R. `K proposed` is not printed at all on a FAIL. Pinning `K0` — the value that just failed — is the one advice
that must never appear, and the old "`k_last_pass` when one exists, else `k_tested`" branch was dead text (§3.5 makes a FAIL exactly the case with no
`k_last_pass`). The string `1280M` appears nowhere in any output; `test_the_fail_pin_advice_names_the_measured_k_never_a_fixed_1280m` keeps that true.

**README run order** (main's 19g finding, extended): the host accept checkout needs `mdt-cockpit/` **AND** `scripts/debian-install-v2/tools/` (`git
sparse-checkout set mdt-cockpit scripts/debian-install-v2/tools`; `install.sh` exits 2 without the second) **plus `scripts/damon-analysis/lib/`** for
§4. The kit's preflight checks all three and refuses naming the missing path.

## 7. (f) Third-run plan on r1002 (root, in order)

1. Sparse-check-out both trees + `scripts/damon-analysis/` at the reviewed commit; `kit_sha`/`kit_sha256` into `manifest.env`.
2. Preflight: `mdt-host-check.sh` no FAIL, `NeedDaemonReload=no` on `dev.slice` and both tiers, no
   `/run/mdt/reconcile/memory-deferred`, `damon_stat_enabled=N` (or `absent`), `cgprofilectl --json status` with
   `sessions_live == 0`, `MDT_PSI_GUARD=report` so enforce mode cannot re-weight the tiers mid-run, and
   `MDT_ACCEPT_SSH_TARGET` set to this host over loopback with **one verified BatchMode round trip from root** (its `boot_id` must
   match the local one, §3.2); drain and **hold** the drain (design note §8 step 0).
3. Squeeze run: `systemd-run --setenv=MDT_ACCEPT_SSH_TARGET=root@127.0.0.1 --unit mdt-accept-1 --collect --slice=mdt-accept.slice -p KillMode=mixed -p TimeoutStopSec=600 -p
   MemoryMin=64M -p ExecStopPost='<checkout>/mdt-cockpit/host-setup/tools/accept/accept-restore.sh --from-unit --lock-timeout 30
   --evidence /root/accept-<ts>-1' <checkout>/mdt-cockpit/host-setup/tools/accept/accept-run.sh --evidence /root/accept-<ts>-1
   --steps 6 --step 128M --dwell 300`; operator part: one gate lane, the cockpit attached and idle, one ssh login.
4. Eval and apply: `accept-eval.py /root/accept-<ts>-1`, read `SQUEEZE`/`FLOOR`/`M`/`K proposed`/the `INFO refault` hints, put
   the printed `DEV_HOST_MEMORY_MIN`/`MARGIN`/`MEASURED` in (**no KEEP pin**: K is derived again), then `sudo ./install.sh`,
   `mdt-host-check.sh` (no FAIL) and the reconcile timer running.
5. Rollback **R (on any failure of steps 3-6, or `restore=pending`)**: `accept-restore.sh --evidence <EV>` (exit 1 = a lowering
   is deferred: report it, never force it), then `DEV_HOST_MEMORY_KEEP` back at the pre-run value (the live r1002 pin, 1560M
   today) and `sudo ./install.sh`. It covers step 6 as well, because a failed third run leaves the same state a failed squeeze does.
6. Third run, at the K that step 4 installed. **ONE rule**: run-3 K = `K_proposed = max(F_new + M, k_last_pass)` (§5); there is no
   second rule, and 1280M plays no part in it (it was a no-op in both branches — if `1280M ≤ k_last_pass` then `K_proposed ≥ 1280M`
   anyway — and it read as if it could change K). Run it as `accept-run.sh … --steps 0`: step 0 only, one 300 s dwell at the
   installed K, all §3 criteria. A FAIL → R. That single confirmation dwell is also where §4.3's "raise the row and repeat once"
   lands. Run-2 check of the rule (`eval-run2.txt:18-22`):
   - run 2 tested K 1507M with the floors **591M** in force and a margin of 916M (1507 − 916); "F_installed 644M" is run 2's
     **proposed** F_new = 1560 − 916, not what was in force;
   - its proposed floors are F_new 644M and its measured M is 746.7M;
   - v2 on run 2's numbers gives `max(644 + 746.7, 1507) = max(1390.7, 1507) = 1507M`; v1's 916M clamp gave 1560M.

   A run-3 K below 1507M therefore needs the squeeze's `k_last_pass` below it. 1077M (F 565 + M 512) has no support from run 2.
7. `daemon-reload-check.sh` (design note §5.3 precedence); then the result into the host-setup backlog.

**Duration**: preflight 10 min; step 3 = warm-up + **7 dwells** (steps 0-6) + the DAMON-off control pass + a margin. The warm-up is **600 s** (v1's
`--warmup`, and §2.2's allocator needs dev.slice to reach A0 before step 0 is judged): `600 + 7 × 300 + 300 (control pass) + 300 (margin and step
transitions) = 3300 s` = **55 min**, not 45 (45 omitted step 0's dwell and the control pass, and the old dummy cap would have ended the run inside
step 5). Step 6 adds one 300 s dwell; eval and apply ≈ 10 min → **≈ 3 h on r1002**, no reboot.

## 8. (g) Test plan for the kit package

Seams (no root, no DAMON, no wall clock in a verdict):

| seam | fakes |
|---|---|
| `MDT_ACCEPT_CGROUP`, `MDT_ACCEPT_PROC` (existing) | cgroupfs and `/proc`, now also the **write** path of §2.3 (accept writes, read back) |
| `MDT_ACCEPT_HOTSET_JSON`, `MDT_ACCEPT_DAMON` (new) | per-step/per-unit scheme fixtures instead of a session; and a fake `/sys/kernel/mm/damon/admin` with non-sequential `tried_regions` names, `age` in intervals, `stats/sz_ops_filter_passed`, `avail_operations` without `paddr`, a failing filter write |
| `MDT_ACCEPT_DAMON_STAT` (new) | the fake `enabled` file: `N` proceeds, `Y` refuses exit 3 with no write, absent proceeds |
| `MDT_ACCEPT_PROBE_CMD_<NAME>`, `MDT_ACCEPT_PROBE_TIMEOUT` (new) | per-probe argv/timeout; an overrunning probe is killed, recorded `rc=timeout`, and does not hang the step |
| `MDT_ACCEPT_SQUEEZE_STATE`, `MDT_ACCEPT_DWELL_TICKS`, `MDT_ACCEPT_LOCK`, `MDT_ACCEPT_EXTRA_UNITS`, `MDT_ACCEPT_SSH_TARGET` (new) | the WAL path; the dwell counted in **ticks** (`MDT_ACCEPT_INTERVAL=0`), so no wall clock enters a verdict; the **live lock**, so the reconcile's and install.sh's skip is testable without root; optional-unit presence; the ssh sample source |
| `MDT_ACCEPT_HOTSET_EVERY`, `MDT_ACCEPT_JUDGE` (new) | the one-sample-per-8 s throttle of §4.2 (a 5 s tick would read one apply twice and skip the next — the aliasing N5 removed from `refresh_ms`; 0 derives it from `MDT_ACCEPT_INTERVAL`); a fake `accept-eval.py --live-step`, so the ladder tests drive PASS/FAIL/INCONCLUSIVE without the real judge |

New tests: the ladder (passing, a stop at the first psi failure, the `F + 128M` ceiling, an `--steps` cap, a step whose **load-was-real** criterion
misses → INCONCLUSIVE and never `k_last_pass`); the stop-rule arithmetic including step 0 (FAIL vs INCONCLUSIVE); `MemAvailable` and unit-`full`
aborts; one refusal per §2.3 bound; mid-run drift on `dev.slice/memory.max` and on the tier's min → INCONCLUSIVE `drift:<file>`; a HUP before the
first write, after the third, between a WAL line and its write, and during `docker run` before the id is known (the MDT-110 subtests), with every
original restored; `--rm` not relied on; ExecStopPost restoring after a SIGKILL of the unit; `accept-restore.sh` twice on one state file, on one whose
target is above `current − 64M` (`RESTORE-PENDING`, exit 1), on one whose min scope is gone (restored, not pending), on one whose value a foreign
writer changed (NOTEd, not overwritten), and on a stale `boot_id` (nothing written, exit 0); a stale state file handled by the next run (one NOTE);
the v1-evidence refusal; `damon_stat` absent → proceed; the filter chain (cold page of the leaf 0, young page of another leaf 0, young page of the
leaf = folio size); `sz_ops_filter_passed = 0` with `memory.current > 1 MiB` → that unit refused; `1280M` cannot appear in any output. Changed:
`test_accept_kit.py`'s `HEAD` and `write_run` fixtures (new columns, two new CSVs); `test_refault_fails_and_the_env_lines_are_not_to_be_applied` →
`…is_informational_and_does_not_change_the_verdict` (rc 0, `INFO`); the M and floor assertions move to `hotset`;
`test_the_proposed_k_is_never_below_the_tested_one_on_fail_and_on_pass` → `…never_below_k_last_pass` (one clamp, §5);
`test_the_probe_body_is_incompressible…` gains the per-step inflation and never-re-touch rule; the shell tests' `TIER_MAX`/fake-run expectations move
to the deepest-ceiling cap; `test_a_load_that_never_hit_the_ceiling_is_inconclusive` becomes its per-step form. The existing `smoke` lane pins the
sample write log (no scheme inputs or commit), additions/removals/re-inodes/child gains before or after apply (whole-unit drop, another unit's row,
and continued run), remeasurement after the next boundary, the post-warm-up step-0 reconfigure, full rewrite after a rejected commit and leaf removal,
setup retry after a leaf vanishes, INCONCLUSIVE after all boundary attempts fail, and eval's displayed drop reason. No new lane.

## 9. (h) Risks and open questions (default taken)

1. **systemd may rewrite a container scope's `memory.min`** (any property change or reload), silently un-protecting the dummy
   mid-step. Default: §2.3's drift rule (no `daemon-reload` during the run, all five files read back every 5 s, step
   INCONCLUSIVE `drift:<file>` on a difference).
2. **The `memcg` ops-filter may attribute nothing for a leaf** on some setups (`SCRIPTS.md:258`), and whether the r1002 kernel has
   `stats/sz_ops_filter_passed` and the `young` filter is a preflight fact, not a known number. Default: the §4.1 per-unit self-test
   and preflight, refuse that unit's floor with a NOTE and keep its table row — never read 0 hot bytes as "no hot set". The chain is an
   **AND** (`memcg matching=N allow=N` first, then `young matching=Y allow=Y`); an allow-first memcg filter would return the leaf's whole
   resident size plus the machine's young bytes, and every floor built on it would be a machine-wide number.
3. **DAMON overhead could distort the latencies we judge** (one kdamond × 1000 regions at 400 ms, not eight separate ones; and one
   full-RAM folio walk per leaf scheme every 8 s). Default: keep the session during the dwell (the operator wants hot sets *while in
   use*) and add one control pass with DAMON stopped at step 0; > 25 % difference prints INFO, and the hard criteria keep the DAMON-on
   numbers (conservative). The step-0 latency baselines are taken with DAMON **on**, like every step; the control pass is INFO.
4. **The squeeze can OOM before a criterion fails.** Default: the §2.4 guards abort the ladder and mark the step FAIL, and
   §2.2's `--oom-score-adj 1000` makes the dummy the victim, never the gate lane — never trade a host OOM for one more data
   point.
5. **1280M may be the wrong target for run 3** (DAMON floors can be lower *or* higher than the installed rows, and `M_squeeze` may
   exceed `M_unpressured`). Default: §7 step 6's **one** rule, `K_proposed = max(F_new + M, k_last_pass)`; 1280M is a report note and
   never enters the arithmetic, and anything below `F_installed + 128M` is untestable rather than a candidate.
6. **Is the squeeze the "third run"?** The operator asked for "re-check M, then a third run at a lower reserve". Default: **the
   squeeze is the re-check and §7 step 6 is the third run** — run 1 was the 1077M default, run 2 the 1507M re-run, so counting
   the squeeze as a run as well would add a fourth without saying so. Run 3 is step 0 only, one dwell, not a second ladder.

## Appendix: the kit package's file list (next package)

`tools/accept/accept-run.sh` (steps, writes, WAL, probes, DAMON phase); `tools/accept/accept-eval.py` (step verdicts, DAMON floors, M re-check, v1
refusal); new `tools/accept/accept-hotset.py` (one kdamond, one `stat` scheme per leaf, the AND chain `memcg reject` → `young allow`, via
`scripts/damon-analysis/lib`) and `tools/accept/accept-restore.sh` (`--from-unit`, exit 3 → 0, `flock -w`); `tests/test_accept_kit.py`; `README.md`
"Acceptance and measurement kit"; `mdt-cockpit/CHANGES.md` `[Unreleased]`. Three things outside the kit: the accept-lock skip in
`scripts/mdt-dev-governance-reconcile.sh` (its memory block, holding the lock), `install.sh`'s refusal (exit 3) while the lock is held (§2.3), and a
`scripts/damon-analysis/KNOWN_ISSUES_TODO_BACKLOG.md` entry for `sz_filter_passed` and the `young` filter's layer (§4.1). No new stored key is
planned, so `host-setup.env.example` stays untouched.
