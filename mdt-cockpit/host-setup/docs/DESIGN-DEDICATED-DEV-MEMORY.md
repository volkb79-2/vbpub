# Design: memory model of a dedicated test/compute host (P-plat-19e)

Status: design note, round 2 (2026-11-24), revised after review r1 (REVISE) and the operator's binding answers of
2026-11-24 ~09:16Z (`.controller-state/operator-decisions.md`). No code yet. P-plat-19d is merged (0fe391134 in
origin/main); the implementer branches from current origin/main. One 19d decision is reversed: 19d derives a
`MemoryHigh` for dev-gates and dev-background, this note removes it (section 5).
Scope: `HOST_ROLE=dedicated-dev` only. Other roles (incl. the game-server controller host) render byte for byte as
today (pinned by the identity tests); new check.sh findings are WARN there. Appendix A maps every r1 finding.

## 1. Requirement and the model

Operator, 2026-11-24: protect only the basic host services, "no reason to set `high` on our test containers",
unprotected memory goes to the load. The answers add: **Q1** every load container carries its own memory cap so a
runaway cannot disturb the rest; the cockpit (devcontainer) must not be OOM-killed; an OOM lands on
gates/background. **Q2** ship without a freeze; the fleetd freeze is backlog **P-fd-05**. **Q3** no fixed 1280M
reserve: the host keeps the measured working sets of its core services (hostobs included once it ships) plus a
minimal margin; everything else goes to the load and swap absorbs the rest. **Q4** a 64M `user.slice` floor.
**Q5** `OOMPolicy=kill` on `docker-.scope`, dedicated-dev only.

1. **Host floors:** `MemoryMin` on sshd, journald, dockerd, containerd, psi-guard, cgprofiled (via its slice), the
   fleetd agent and the hostobs probe once they exist, and `user.slice`; `system.slice` carries the derived sum.
2. **One RAM ceiling for the dev tree:** dev.slice `MemoryMax = MemTotal − MDT_EXCLUDED_RAM − K`, with
   `K = Σ floors + margin` (section 4), and a large `MemorySwapMax`. Above it the dev tree pages.
3. **No `MemoryHigh` anywhere in the dev tree**: not on dev.slice, any tier, the docker-.scope default, nor
   psi-guard's enforce stage on this role. cgprofile.slice's own High/Max bound a host service and stay.
4. **Load tiers:** dev-gates and dev-background each get one derived, non-throttling `MemoryMax = dev MemoryMax`
   (P-plat-19h; it was dev Max − Σ protected-tier MemoryLow). Equal to dev's Max, the tier Max is effectively inert:
   reclaim and OOM happen at dev.slice, where the cockpit's Min/Low and −900 decide, and the load tiers no longer
   isolate each other; with `OOMPolicy=kill` on the container scope only the victim's container dies.
5. **Cockpit protection:** dev-interactive gets a `MemoryMin` floor (dev.slice carries Σ child floors), and on
   this role the devcontainers run with `--oom-score-adj=-900`, so every OOM domain picks a load task first.
6. **Per-container default** (operator, 2nd answers): the `docker-.scope` drop-in renders `MemoryMax=8G` on this
   role (12G elsewhere), swap stays 16G, no High; spawners may declare tighter caps (5.3). **Admission** keeps a
   memory term from the gates `MemoryMax` (6.3); PSI `full avg10 >= 3` stays the stop signal; no freeze (P-fd-05).

## 2. Kernel semantics relied on

1. **`memory.min` vs `memory.low`** act only on reclaim from outside the cgroup: global reclaim (kswapd, direct
   reclaim at the zone watermarks) or an ancestor's limit reclaim. `min` is never breached (the kernel OOMs
   instead); `low` holds only while unprotected memory remains elsewhere. Both cover at most `min(usage, setting)`,
   reserve nothing and play no part in OOM victim selection. (The 2026-08-04 "MemoryLow inert" finding was a
   missing `memory_recursiveprot`, which check.sh FAILs on.)
2. **Effective protection is inherited** (`mm/memcontrol.c effective_protection()`): a child's floor counts only
   up to what its parent hands out; oversubscribed siblings are scaled down proportionally and silently; under
   `memory_recursiveprot` an unclaimed parent floor is spread over the children by unprotected usage. Hence:
   (i) a docker.service floor is inert while system.slice has `min = 0` (v1001: 231–251 MiB refaults without the
   slice floor, 0–4 MiB with it, `CONTROL-PLANE-MEMORY.md`); (ii) systemd does not sum children into the parent
   (`DefaultMemoryMin=` is only a child default), so host-setup writes the parent value; (iii) dev.slice
   `MemoryMin` must equal Σ its children's `MemoryMin` — more leaks to the gate tiers (`dev.slice.in` comment).
3. **Limit reclaim inside dev.slice** makes dev.slice the reclaim root; its children's `min`/`low` hold relative to
   it (the cockpit's floor holds against gate load). system.slice/user.slice protection matters only globally.
4. **`memory.high`** reclaims and then sleeps the allocating task (up to 2 s) even while RAM is free: rejected.
5. **`memory.max` with swap.** It bounds `memory.current` (anon + page cache + charged kernel memory + zswap
   pool); disk swap is bounded by `memory.swap.max`. At `memory.max` the charging task reclaims synchronously
   (swap-out, cache drop), with no penalty sleep. A memcg OOM follows when reclaim **makes no progress** within
   `MAX_RECLAIM_RETRIES`: swap.max reached, the rest protected or unreclaimable, or pages stuck in writeback
   (risk R3). A finite `memory.max` below RAM plus a large `memory.swap.max` means "page to swap here", not "kill".
6. **Writing `memory.max` below `memory.current`** (`memory_max_write()`) loops reclaim and then calls
   `mem_cgroup_out_of_memory()` in that cgroup: a live lowering can kill. `memory.reclaim` never OOMs; it returns
   `-EAGAIN` when it falls short.
7. **OOM victim selection** (`oom_badness()`): `rss + swap + page tables + oom_score_adj × totalpages / 1000`,
   with `totalpages` = RAM+swap globally, the memcg's max + swap in a memcg OOM. On these hosts −900 subtracts
   about 60 GiB of pages, so any adj-0 load task outranks a −900 task (netcup: adj-0 scores barely depend on size).
   `memory.oom.group=1` (`OOMPolicy=kill`) on the victim's scope kills that whole container and nothing else.
   Domains: a `--memory` container OOMs in its scope, a tier inside the tier; dev.slice/global OOMs land on adj-0.

## 3. Who wrote `/run/systemd/system.control/system.slice.d/50-MemoryMax.conf`

No repository path writes it. The main controller session did (`PROGRAM.md` 05:58Z, 06:09Z): a "restore"
`set-property --runtime system.slice MemoryMax=5905580032` in one command with the two load tiers; its other runtime
memory/oomd files outrank `/etc` and are removed in section 8. Prevention: check.sh flags such overrides (7);
install.sh converges the kernel itself (6.2); README: never `set-property` or `systemctl revert` for memory.

## 4. Host reserve K: floors plus a measured margin (Q3)

**K = F + M.** F = Σ top-level floors: the derived system.slice floor + cgprofile.slice's own 128M + user.slice
64M (cgprofiled's 4M lives inside cgprofile.slice and is not added again). M = the RAM the host needs outside every
floor: kernel memory charged to no cgroup, PID 1 (`init.scope`, no floor), the zone `high` watermarks (so host
allocations do not enter direct reclaim behind dev swap-out), and slack. `DEV_HOST_MEMORY_KEEP` becomes a pin of K:
refused below F + 128M, and install.sh prints a NOTICE whenever it is set, because its old meaning was RAM+swap
(r1 12b).

**Floor table.** debian-install-v2 owns the measured values (`templates.py CONTROL_PLANE_MEMORY_MIN`,
`CONTROL-PLANE-MEMORY.md`: 1.25 × hot set H, 2026-11-15, both 8 GiB hosts);
`mdt_capacity.DEDICATED_HOST_MEMORY_MIN_DEFAULTS` mirrors it with a parity test, as fleetd does.

| unit | floor | parent | note |
|---|---:|---|---|
| ssh.service | 10M | system.slice | logins live in user.slice; `OOMScoreAdjust` on ssh stays forbidden |
| docker.service | 210M | system.slice | grows with container count (DIV2-BL-11) |
| containerd.service | 92M | system.slice | holds the shims |
| systemd-journald.service | 24M | system.slice | |
| mdt-psi-guard.service | 5M | system.slice | optional unit |
| fleetd agent, hostobs probe | — | system.slice | no unit at origin/main; a row enters DI2's table when measured (4.1) |
| **system.slice** | **373M** | root | derived after the raise (below): 341 + 32 |
| cgprofiled.service | 4M | cgprofile.slice | the slice carries `MemoryMin=128M`, cgprofile-owned |
| **user.slice** | **64M** | root | Q4: recovery shell, `sudo`, `systemctl`; host-setup-owned |

**F = 373 + 128 + 64 = 565M.** Initial **M = 512M** until measured: netcup's control-plane measurement found about
0.3 GiB of kernel memory on these hosts, the `high` watermarks are about 0.1 GiB with THP's raised
`min_free_kbytes`, plus about 0.1 GiB slack. **K = 1077M.** The only 8 GiB measurements: K = 767M (7 GiB ceiling,
no floors) failed with 107 MiB `MemAvailable` and 5.5 s ssh latency; K = 1280M passed. 1077M lies between them;
the floors now hold the host's hot set resident, which the failed run lacked, but the value is unmeasured, so the
post-migration acceptance run (section 8, step 6) decides. On failure, pin `DEV_HOST_MEMORY_KEEP` at the K that
accept-eval's FAIL header prints (floors + measured margin, never below the tested one; P-plat-19h), the documented
fallback until the measurement gives the value. The old fixed 1280M was only the second measurement above.

### 4.1 Measurement method

- **Who:** the main controller (root on the host). Hot sets with DI2's protocol, `tools/hotset.py` from the
  evidence directory `/workspaces/vbpub/artifacts/release-program-20261027/evidence/control-plane-hotset/tools/`
  (gitignored, devcontainer-local, `SHA256SUMS` there; r1 13). Time series with `cgprofilectl profile <unit>`.
- **Load:** the dev tree held at its `MemoryMax` with active page-out: one capped probe container in dev-gates and
  one in dev-background (`--memory 4G --memory-swap 12G`; 8G together > the 6858M dev max), each cycling about
  3.8G anon plus random reads of a 4G file; one real gate lane; the cockpit attached and idle; an ssh login in
  user.slice. dev.slice `memory.events` `max` must rise during the window.
- **How long:** 10 min warm-up, then a 30 min window; two runs per host flavour; take the max.
- **Per floored unit** (sshd, journald, dockerd, containerd, psi-guard, cgprofiled, fleetd agent, hostobs probe,
  user.slice, and dev-interactive only to size its 512M `MemoryMin`, never its Low): H by the hotset protocol; `memory.current`;
  `memory.peak` (upper sanity bound only, it includes cold cache); `memory.stat` `active_anon + active_file`;
  `workingset_refault_{anon,file}` deltas.
- **Margin**, sampled every 10 s: U = `MemTotal − MemFree − Σ memory.current of every root child`;
  `init.scope` `memory.current`; W = Σ zone `high` × page size from `/proc/zoneinfo`.
  M = p99(U) + p99(init.scope) + W + 64M.
- **Acceptance** (else raise the failing value and repeat): floored-unit refault deltas ≈ 0 (**superseded**: v2 makes
  them informational only, below); system.slice and user.slice `memory.pressure` `some avg10 ≤ 1`; `ssh host true` ≤
  2 × idle baseline; no `oom_kill` delta in global vmstat, dev.slice or tier `memory.events`.
- **Stored in `/etc/mdt/host-setup.env`:** `DEV_HOST_MEMORY_MIN` (empty = mirror table, `-` = none, else
  space-separated `unit=size` row overrides such as `docker.service=300M`; `.slice` names and sizes < 1M refused);
  `DEV_HOST_MEMORY_MARGIN` (M, default 512M, ≥ 64M); `DEV_INTERACTIVE_MEMORY_MIN` (cockpit floor, 5.2);
  `DEV_HOST_MEMORY_MEASURED` (free text `<UTC date> <evidence dir>`, never read by the derivation; check.sh NOTEs
  while it is empty). A floor change valid for both flavours goes to DI2's table and both mirrors follow.

**v2 (P-plat-19k, 2026-11-25) supersedes this method.** `docs/DESIGN-ACCEPT-KIT-V2.md` replaces the single 30 min
window with a step-wise **squeeze test** (a dummy container held in anon by its `memory.min`, the ceiling raised 128M
per step so the host reserve shrinks), takes the floors' hot set `H` from **DAMON** (paddr with a DAMOS `memcg` filter,
`floor = 1.10 × H`) instead of `active_anon + active_file` × 1.25, and makes **pressure and ssh/service latency the
hard criteria**: the per-unit **refault criterion above is informational only from v2 on** (both 19g runs failed solely
on it while pressure, ssh, swap and OOM all passed). `M` keeps its formula but is read over the deepest passing step.

**Bounds** (refused before any write, exit 2, naming the key): F ≤ 10 % MemTotal (DIV2-BL-12); derived
system.slice floor ≤ 460M (DIV2-BL-10); K ≥ F + 128M; dev.slice `MemoryMin` ≤ dev `MemoryMax` − 1G (r1 10);
a `DEV_MEMORY_MAX` pin ≤ MemTotal − MDT_EXCLUDED_RAM − K.

**Writing** (install.sh dedicated-dev block): one owned `95-mdt-memory-min.conf` per unit (and on system.slice and
user.slice) with `MemoryMin=` only, sorting after DI2's `90-`; an absent unit is skipped with a note. Raise-only:
`max(planned, highest MemoryMin in the unit's other fragment/drop-ins)` via `systemctl show -p FragmentPath,DropInPaths`.
**The system.slice floor is summed from the post-raise values** and the 460M/10 % bounds apply to that result
(r1 7), so a foreign drop-in raising docker cannot leave the slice under-covering its children.
system.slice `MemoryLow` (in `50-mdt-dedicated-dev.conf`) is **derived from K**, no longer KEEP or a constant 1G:
`max(system.slice MemoryMin, K − M − cgprofile.slice Min − user.slice Min)` = max(373, 1077 − 512 − 128 − 64) =
**373M**. A 1G Low above a 373M floor would be honoured under global reclaim while dev has unprotected pages, so
kswapd would squeeze dev below its Max and host allocations would wait behind dev swap-out (r2 N1). At defaults
the Low equals the Min and the line may be omitted; it exceeds the Min only when a KEEP pin lifts K above F + M.
**Invariant:** Σ max(Min, Low) over the non-dev root children + M ≤ K (373 + 128 + 64 + 512 = 1077 ≤ 1077).
`--apply-protection` stays the shared-prod path (it refuses on `MDT_EXCLUDED_RAM=0`).
Raw MemTotal − K is deliberate, unlike netcup's nominal rounding (`DESIGN-GUIDE.md:384-396`, which reproduces a
chosen ceiling): K is measured host need, so a kernel reserving more lowers dev `MemoryMax` and preserves K (r1 10).

## 5. Cockpit protection, load tiers, per-container caps (Q1)

**5.1 Load tiers.** dev-gates and dev-background: `MemoryMax = dev MemoryMax` (operator ruling 2026-11-24, P-plat-19h;
it was `max(1G, dev MemoryMax − P)`), no `MemoryHigh`, with
`P = Σ DEV_{INTERACTIVE,BUILDKITD,CACHE}_MEMORY_LOW` (the existing `_PROTECTED_LOW_KEYS`) now sizing only the swap
ceiling AD2-0 (dev swap − P) and the Low protection, which stay. The cockpit's protection is its Min/Low, the −900
score and `OOMPolicy=kill`, not a smaller tier ceiling. Equal to dev's Max, the tier Max is effectively inert: the
charge walk stops at the first counter over its max, and the tier's usage is always below dev's while any other dev
child holds memory, so reclaim and OOM happen at dev.slice. The OOM victim is picked across all of dev.slice (the
cockpit last, by its Min/Low and −900), and a runaway gate can take reclaim from background and buildkitd and OOM one
of their containers: the load tiers no longer isolate each other. Per-container `--memory` caps and the 8G scope
default still bound a single runaway (5.3).

**5.2 Cockpit.** `DEV_INTERACTIVE_MEMORY_MIN` defaults to **512M** on dedicated-dev, unmeasured (netcup measured
cockpit/run-gate ≈ 0.3 GiB; 1.25 × that, rounded up; 4.1 measures it), and must be ≤ `DEV_INTERACTIVE_MEMORY_LOW`.
dev.slice `MemoryMin` = Σ of **every** dev child Min key: `DEV_MEMORY_MIN_GUARANTEED_CEILING` + `DEV_{INTERACTIVE,
GATES,BACKGROUND,BUILDKITD}_MEMORY_MIN` (2.2 iii; r2 N4b); other roles keep the guaranteed ceiling alone, and the
`dev.slice.in` "MUST equal the guaranteed slice" comment is rewritten to "Σ child Min". Role-scoped score (r2 N2):
the `runArgs` of `.devcontainer/devcontainer.json`, `mdt-cockpit/templates/devcontainer.json` and
`mdt-cockpit/templates/host-folder/devcontainer.json` gain `"--oom-score-adj", "${localEnv:MDT_COCKPIT_OOM_SCORE_ADJ:0}"`
(the `CGPROFILE_CONTROL_GID` localEnv pattern). host-setup publishes `MDT_COCKPIT_OOM_SCORE_ADJ=-900` only on
dedicated-dev, through the same endpoint path that delivers the GIDs, so the controller host and shared-prod hosts
keep 0. runc gives `docker exec` processes (the attaching IDE server) the container's score; it is create-time, so
an existing cockpit needs a rebuild. −900 ties the cockpit with the host services, where size decides (a runaway
cockpit is still picked before a small daemon). The netcup runner keeps `COCKPIT_OOM_SCORE_ADJ` (−500).
dev-interactive and dev-buildkitd stay without a tier `MemoryMax` (19d): a runaway cockpit is bounded by dev.slice.

**5.3 Per-container caps, surveyed at origin/main (information only).** Capped: tools/dev `hostobs-dev-*`, vm-lab,
cmru tester-gate, cgprofile gate, assay self-qualification/qualify_sql, iomon-bpf, netcup smokes, truenas gate, ciu
services with `mem_limit`. Uncapped: run-gate lanes without `resources.memory` (`run_gate.py:18168-18181`),
exec-mode lanes, the path probe, cmru wheel-build/buildx, ciu stacks without `mem_limit`, mdt OCI push and
host-exec, nyxloom audit/scaffold, srdm gate/canary, the cgprofile probe, the cockpits, mdt-buildkitd.

**Per-container default (operator, 2nd answers; r2 E4).** install.sh renders an empty
`DOCKER_SCOPE_BACKSTOP_MEMORY_MAX` as **8G on dedicated-dev, 12G on every other role** (the backstop also reaches
production containers on shared hosts, so other roles stay byte for byte). `MemorySwapMax` stays 16G; no
MemoryHigh. The watcher keeps today's `disabled`/`-`; buildkitd gets no `--memory` and is bounded by dev Max and
the 16G scope swap. Admission's `gates_max / ADMISSION_MEMORY_PER_GATE` term (6.3) is a concurrency budget, not a
per-container cap. No backlog item is filed just for a cap; one is filed only where a spawner has its own reason to
declare a tighter cap (r2 E6). If someone enables the watcher, its runtime `MemorySwapMax` (about 48 GiB) outranks
the scope's 16G.

**Effective cap (r2 E5).** A container's RAM bound is the smallest `memory.max` on its ancestor chain:
- gates/background container on r1002 (and v1001 with host-setup): min(8G, tier 6858.22 MiB, dev 6858.22 MiB) =
  dev Max (the tier Max equals it); cockpit and buildkitd: min(8G, dev Max) = dev Max. On both 8 GiB hosts the 8G line never binds RAM;
  it binds only where dev Max > 8G (MemTotal above about 9.1 GiB at today's K).
- The swap line binds: an undeclared runaway fills dev.slice's RAM and pages its dev.slice neighbours; at 16G of its own
  swap it can no longer be reclaimed; dev.slice OOMs (the tier Max equals dev's since P-plat-19h), the victim is the
  largest rss + swap in dev.slice, normally the runaway, and `oom.group`
  kills its container. The cockpit (Low 2.5G, Min 512M, −900) and the host floors are untouched; the other load
  tier is not.
- Precedence: drop-ins in `/etc/systemd/system/docker-.scope.d/` override the transient unit's `MemoryMax`, but
  runc (systemd driver) also writes a declared `--memory` straight into the scope's `memory.max` after start, so a
  tighter declared cap holds in the kernel while systemd's property reads 8G. Nothing in the repo records this
  today (unmeasured): acceptance step 6 checks that a `--memory 2G` container reads `memory.max` = 2G before and
  after a daemon-reload. The converge and check.sh never compare or rewrite docker scopes (6.2 scope).
- Exec-mode `rg-<token>` leaves and the run-gate path probe are not docker scopes; only the tier Max (= dev Max in effect) bounds them.
- Running containers keep their old 12G scope value until re-created. That is harmless (their tier/dev Max is
  lower); no live lowering of scopes takes place.

**5.4 `OOMPolicy=kill` (Q5).** `docker-.scope.d/60-mdt-dedicated-dev.conf`, `[Scope] OOMPolicy=kill`: each container
scope gets `memory.oom.group=1`. Needs systemd ≥ 253 (trixie: 257), else skipped with a NOTICE; removed off-role.

## 6. Resulting unit table, converge, admission

**6.1 Inputs.** r1002: MemTotal 8320679936 B = **7935.22 MiB**, SwapTotal ≈ 63 GiB, `DEV_INTERACTIVE_MEMORY_LOW=2.5G`,
other tier Lows and `DEV_MEMORY_MIN_GUARANTEED_CEILING` empty. v1001: MemTotal 8320081920 B, SwapTotal 65535 MiB;
host-setup was installed there on 2026-11-24 (12:27Z; the runner's `20-memory-policy.conf`, `MemoryMax=6656M`, was
retired): dev 7190765568 B, default K 1077M, and with P-plat-19h the tiers equal dev (they were 4506411008 B =
dev − P); the column below shows that render.

1. F = 373 + 128 + 64 = 565M; K = 565 + 512 = 1077M = 1129316352 B.
2. dev `MemoryMax` = 8320679936 − 1129316352 = **7191363584 B = 6858.22 MiB** (203 MiB more for the load than
   KEEP=1280M, 6655.22 MiB).
3. Tier `MemoryMax` = dev `MemoryMax` = **7191363584 B = 6858.22 MiB** (P-plat-19h; the former value was 7191363584 −
   P, P = 2560M = 2684354560 B, = 4507009024 B; P now sizes only the swap ceiling). The live r1002 dev.slice is
   pinned at `DEV_HOST_MEMORY_KEEP=1560M`: 8320679936 − 1635778560 = 6684901376 B = 6375.22 MiB, and so is the tier.
4. dev `MemoryMin` = 0 + 512M = 536870912 B ≤ 6858.22 − 1024 MiB.
5. Bounds: F 565M ≤ 793.52 MiB (10 %); system.slice 373M ≤ 460M; K 1077M ≥ F + 128M = 693M.
6. Admission memory term = ⌊7191363584 / 2 GiB⌋ = **3** (⌊6684901376 / 2 GiB⌋ = 3 at the pinned 6375M), one more than
   the 4G-High / 2G term and the old minus-P term; on the unpinned formula the CPU term (4 vCPU / 2 = 2) still binds,
   and both live hosts pin `ADMISSION_MAX_CONCURRENT=4`, which wins.

| unit | property | r1002 live today | new r1002 | new v1001 (installed 2026-11-24) | rule |
|---|---|---|---|---|---|
| dev.slice | MemoryMax | 7.5 GiB (runtime file) | 7191363584 | ≈ 6858M | MemTotal − EXCLUDED − K |
| dev.slice | MemoryHigh | 7.0 GiB | none | none | `DEV_MEMORY_HIGH` ignored, NOTICE |
| dev.slice | MemoryMin / Low | 0 / 2.5G | 512M / 2.5G | 512M / Σ Lows | Σ every child Min key / Σ tier Low |
| dev.slice | MemorySwapMax | SWAP_MDT | ≈ 62.5 GiB | 65023M | SwapTotal − MDT_HOST_SWAP_MARGIN |
| dev-gates, dev-background | MemoryHigh | 4 GiB | none | none | HIGH pins ignored, NOTICE |
| dev-gates, dev-background | MemoryMax | 5.5 GiB | 7191363584 | ≈ 6858M | = dev Max (P-plat-19h); MAX pin ≤ dev Max |
| dev-gates, dev-background | MemorySwapMax | dev − P | ≈ 60 GiB | 62463M | AD2-0 unchanged |
| dev-interactive | Min / Low / High / Max | 0 / 2.5G / – / – | 512M / 2.5G / – / – | same | 5.2; no tier ceiling (19d) |
| dev-buildkitd | High / Max | none | none | none | 19d; bounded by dev Max + 16G scope swap |
| system.slice | MemoryMin / Low | 0 / 1G | 373M / 373M | 373M / 373M | derived post-raise / K − M − 192M (≥ Min) |
| user.slice | MemoryMin | 0 | 64M | 64M | Q4 |
| ssh, docker, containerd, journald, psi-guard | MemoryMin | 0 | table | table | `95-mdt-memory-min.conf` |
| docker, containerd, cgpd, cgprofiled, psi-guard, journald, oomd | OOMScoreAdjust | −900 | unchanged | unchanged | `50-mdt-dedicated-dev.conf` |
| cockpit devcontainers | oom_score_adj | 0 | −900 | −900 | localEnv runArg, dedicated-dev only, create-time |
| docker-.scope | Max / SwapMax / OOMPolicy | 12G / 16G / stop | 8G / 16G / kill | same | role default (12G elsewhere) + `60-` drop-in |
| undeclared container | effective RAM cap | tier/dev Max | tier Max | tier Max | 8G scope default inert on 8 GiB hosts (5.3) |
| cgprofile.slice | Min / High / Max | 128M / 768M / 1G | unchanged | unchanged | cgprofile-owned |

**6.2 Converge step** (`mdt-dev-governance-reconcile.sh`, run by install.sh after daemon-reload and by the timer).
Scope: `memory.{min,low,high,max,swap.max}` of dev.slice, its tiers, system.slice, user.slice and the floored
units, never a `docker-*.scope`. It compares `systemctl show -p <Property>` with the cgroup file, writes the file
directly and reads it back; never `set-property`.
- **psi-guard invariant (r1 1):** the converge never writes a key psi-guard's `state.json` lists under `owned`
  (`"<tier>/memory.high"`), and check.sh reports an owned key as INFO, not a mismatch. On dedicated-dev psi-guard
  runs stage 1 (weights) only: `HIGH_TIERS` is empty on this role, so enforce adds no `MemoryHigh` either.
- **Lowering `memory.max` (r1 4):** raising, and every `min`/`low` write, applies directly. A lower target T is
  written only when `memory.current ≤ T − 64M`; otherwise the step writes `memory.reclaim` in chunks of ≤ 256M (at
  most 8 per run) and re-reads; if still above T − 64M it defers with a NOTE, records the deferral
  (`/run/mdt/reconcile/memory-deferred`, cgroup and target), and the timer retries. The PSI predicate is dropped:
  low PSI does not make a below-usage write safe.

**6.3 Admission (r1 2).** `mdt_admission.gates_memory_high()` becomes `gates_memory_ceiling()`: on dedicated-dev it
returns `gates_max` (basis "dedicated-dev gates MemoryMax"); other roles keep `DEV_GATES_MEMORY_HIGH`. The docstring
formula reads "gates MemoryMax (dedicated-dev) or MemoryHigh", so the memory term never vanishes silently.

## 7. Changes per file (`mdt-cockpit/host-setup/` unless named)

- **mdt_capacity.py:** `dedicated_memory()`: `high` = None with `DEV_MEMORY_HIGH` and the tier HIGH pins ignored
  and reported; `maximum` = RAM − K (K from new `host_reserve()` → (F, M, K), or the KEEP pin); `gates_max`,
  `background_max` = `max(1G, maximum − protected)`, MAX pins ≤ `maximum`; `gates_high`, `background_high` = None;
  new `dev_min`. The `max_ceiling = ram + swap − keep` path and docstring go. New constants
  `DEDICATED_HOST_MEMORY_MIN_DEFAULTS`, `DEDICATED_SYSTEM_SLICE_HEADROOM = 32M`, `DEDICATED_USER_SLICE_MIN = 64M`,
  `DEDICATED_MEMORY_MARGIN_DEFAULT = 512M`, `DEDICATED_INTERACTIVE_MIN_DEFAULT =
  512M`; validators for `DEV_HOST_MEMORY_MIN`/`MARGIN`; `host_floors(values, mem_total, raised)` sums system.slice
  after the raise and applies the bounds; `system_slice_low()` derives the Low from K (section 4, replacing a
  constant). The `dedicated-memory` CLI adds `DEDICATED_FLOOR <unit>=<bytes>`,
  `DEDICATED_MEM_MIN`, `DEDICATED_RESERVE F M K`, `DEDICATED_IGNORED=<keys>`.
- **mdt_admission.py:** `gates_memory_ceiling()` and docstring (6.3).
- **install.sh:** pre-flight ignore loop gains `DEV_MEMORY_HIGH` and `DEV_{GATES,BACKGROUND}_MEMORY_HIGH`; NOTICE on
  a KEEP pin with its new meaning. Host-protection block: read the raise inputs, call `host_floors()`, write the
  `95-mdt-memory-min.conf` files (units, system.slice, user.slice), render the system.slice `MemoryLow` from
  `system_slice_low()` (omitted when equal to the Min), write `docker-.scope.d/60-mdt-dedicated-dev.conf`, removal
  loops for both new names off-role. An empty `DOCKER_SCOPE_BACKSTOP_MEMORY_MAX` renders 8G on dedicated-dev and
  12G on every other role; an explicit value wins. Publish `MDT_COCKPIT_OOM_SCORE_ADJ=-900` on dedicated-dev only,
  via the GID endpoint path (5.2). Render before `daemon-reload`, then converge.
- **units/dev.slice.in:** `MemoryMin=@DEV_MEMORY_MIN@` (= guaranteed ceiling on other roles, byte-identical; = Σ
  every child Min key on dedicated-dev), comment "Σ child Min"; `dev.slice.in`/`dev-gates.slice.in` comments: no
  `MemoryHigh` on this role, `MemoryMax` is the paging point. **units/docker-scope-default-limits.conf.in** comment:
  "per-container default on dedicated-dev".
- **scripts/mdt-psi-guard.py:** empty `HIGH_TIERS` on dedicated-dev; docstring states the converge invariant.
  **scripts/mdt-dev-governance-reconcile.sh:** the converge block (6.2).
- **host-setup.env.example** 4.0b: formulas rewritten; `DEV_HOST_MEMORY_MIN=`, `DEV_HOST_MEMORY_MARGIN=`,
  `DEV_HOST_MEMORY_MEASURED=`; KEEP text "pin of K, RAM outside dev.slice"; `DOCKER_SCOPE_BACKSTOP_MEMORY_MAX=`
  empty (was the literal 12G pin at :874), its comment stating the role rule and "per-container default on
  dedicated-dev"; "Allowed" lines for `test_env_example_allowed_values.py`. `runtime-optional-keys.re` gains the new keys.
- **scripts/check.sh:** dedicated-dev FAILs: dev.slice `memory.high` ≠ max; dev `memory.max` ≠ derivation or ≥
  MemTotal; tier `memory.max` ≠ derivation; tier `memory.high` ≠ max unless psi-guard owns it; system.slice,
  user.slice or dev.slice `memory.min` ≠ derived; a floored unit's `MemoryMin` below plan (message names
  `DropInPaths`); kernel ≠ systemd property ("run the reconcile"), but a WARN while
  `/run/mdt/reconcile/memory-deferred` records a deferred lowering for that cgroup (r2 N4a). Docker scopes are
  never compared (5.3). Runtime overrides under
  `{/run,/etc}/systemd/system.control/` of memory or `ManagedOOM*` keys on system.slice, user.slice or `dev*.slice`:
  **FAIL on dedicated-dev, WARN on other roles** (r1 8a), printing path, key and remedy; IO runtime files stay OK.
  Foreign memory drop-ins in `/etc/systemd/system/{system,user,dev*}.slice.d/`: DI2's `90-vbpub-memory-min.conf`
  (MemoryMin only) allowed; the runner's `20-memory-policy.conf` **WARNs naming its owner (`provision-gates-limits.sh`,
  NC-nn) until that follow-up lands**, then FAILs (r1 8b); other foreign files FAIL. The "system.slice current >
  KEEP" WARN becomes "system.slice `memory.current − inactive_file` > its MemoryMin + M" (r1 8c). New, dedicated-dev only:
  WARN on a dev-interactive container task with `oom_score_adj > −900` ("rebuild the devcontainer"); NOTE while
  `DEV_HOST_MEMORY_MEASURED` is empty.
- **mdt-host-setup-wizard.py:** memory step shows F, M, K, dev and tier `MemoryMax`, the floors and the cockpit Min;
  proposes no HIGH. **README.md** "Memory of the dedicated-dev role" + **CGROUP-NOTES.md:** table per section 6;
  2.5 replaces "memory.max is unreachable"; 19d's "bounded only by dev.slice's MemoryHigh" → "MemoryMax"; new
  "Do not hand-set memory properties"; the acceptance run (section 8, step 6).
- **The three devcontainer.json files:** `"--oom-score-adj", "${localEnv:MDT_COCKPIT_OOM_SCORE_ADJ:0}"` (5.2).
- **tests** (`test_testhost_mem.py` unless named): derivation on 8320679936 B / 63 GiB (dev max 7191363584, tier
  max 7191363584 (= dev max, P-plat-19h), dev min 536870912, no High anywhere, swap per AD2-0); KEEP pin wins with NOTICE, refused below
  F + 128M; ignored HIGH pins named; shared-prod and empty role unchanged (identity test); 19d's
  `test_dedicated_dev_keeps_a_finite_memory_max_on_gates_and_background…` (`:407`) becomes "tier Max = dev Max,
  no tier High". `test_tree_cache.py:104-116`: `gates_high` None, `gates_max` equals dev max whatever the cache Low (P-plat-19h).
  `test_admission.py`: dedicated-dev term from `gates_max` (r1002 → 3 after P-plat-19h). `test_host_floor_table_parity.py`: DI2
  `templates.py` (ast) + markdown table vs the mirror. Floors: ssh floored and never `OOMScoreAdjust`, absent unit
  skipped, raise-only against a higher and a lower `90-vbpub` fake, **a raised docker raises the system.slice sum**,
  role change removes every owned file. Refusals: each section 4 bound, bad `DEV_HOST_MEMORY_MIN` entries, margin
  < 64M, interactive Min > Low. Converge (fake cgroupfs as in `test_io_governance.py`): writes on a difference,
  never `set-property`, skips psi-guard-owned keys, **lowers max only when current ≤ T − 64M, else reclaims in
  chunks and defers**. `test_psi_guard.py`: no memory.high stage on dedicated-dev. system.slice Low = K − M − 192M
  (373M at defaults, line omitted) and above the Min under a KEEP pin; dev.slice Min sums every child Min key.
  Scope default: dedicated-dev renders 8G, shared-prod and the empty role 12G, an explicit value wins.
  `MDT_COCKPIT_OOM_SCORE_ADJ` published on dedicated-dev only. check.sh (the extracted function the 19d oomd test
  uses): runtime file FAIL on dedicated-dev / WARN on shared-prod, IO-only OK, `20-memory-policy.conf` WARN naming
  the owner, mismatch FAIL, mismatch WARN with a deferral record, owned key INFO. `test-render.sh` header item 10 reworded.

## 8. Migration of an installed host (r1002; main controller, root)

1. Run `mdt-host-check.sh` (19d already FAILs on non-`auto` `ManagedOOM*`) and
   `grep -rs ManagedOOM /etc/systemd/system/{system,dev*}.slice.d/`: nothing in `/etc` may set `kill` before the
   runtime oomd files go (r1 12a).
   **Step 0 (impl review r1 F2, r2 M2):** only on `HOST_ROLE=dedicated-dev`; drain the lanes AND HOLD the drain until
   step 4 is verified (also vm-lab and the edge demo stacks in dev-gates/dev-background); the reconcile timer's IO
   reload applies a lowering like install.sh's. Re-derive the targets with `mdt_capacity.py dedicated-memory` after
   step 3 and check each `memory.current` <= target - 64M.
2. Copy, then delete, the memory and oomd keys of the runtime files of `{system,user,dev*}.slice.d` under both
   `/run/systemd/system.control/` and `/etc/systemd/system.control/` (the set check.sh FAILs on; r2 M1). Never
   `systemctl revert`; **no daemon-reload by hand** (install.sh renders first, then reloads). The exact block is
   in the implementation report.
3. `/etc/mdt/host-setup.env`: SET the HIGH/MAX pins and `DOCKER_SCOPE_BACKSTOP_MEMORY_MAX` (or `8G`) to empty
   (`KEY=`); never delete those lines (install.sh refuses a missing declared key; F1). `DEV_HOST_MEMORY_KEEP=` empty;
   leave `DEV_HOST_MEMORY_MIN` and `DEV_HOST_MEMORY_MARGIN` empty; keep `MDT_PSI_GUARD=report`.
4. `sudo ./install.sh`: renders, reloads, converges. dev 7.5 → 6.70 GiB (tiers 5.5 → 4.20 GiB at the time of this note;
   P-plat-19h: tiers = dev Max, a raise): the 64M predicate
   (6.2) guards only the reconcile's own write; a reload can make systemd itself write the lower `memory.max`, so
   step 0 is what makes this safe. Slices and containers are not restarted, so `memory.events` counters are kept.
5. Rebuild the cockpit devcontainer when convenient (`--oom-score-adj` runArg; check.sh WARNs until then).
   `mdt-host-check.sh`: no FAIL.
6. **Acceptance and measurement run** (r1 5): the 4.1 load and window on r1002 under the new limits, passing the
   4.1 acceptance criteria (including no `oom`/`oom_kill` delta on dev.slice and the tiers while swap is free).
   Also: a `--memory 2G` container reads `memory.max` = 2G before and after a daemon-reload (5.3 precedence).
   Write the measured floor rows, M and `DEV_HOST_MEMORY_MEASURED`, re-run install.sh. On failure pin
   `DEV_HOST_MEMORY_KEEP` at the K accept-eval's FAIL header prints and file the result. (v1001 carried the runner
   policy at the time of this note; host-setup has been installed there since 2026-11-24, see 6.1.)

## 9. What can go wrong, and what covers it

- **R1, protected sum too large:** refused by the bounds; parent floors are derived sums, so nothing is scaled
  down. docker/containerd growth above the floor is reclaimable; the hot-usage WARN shows it (DIV2-BL-11).
- **R2, runaway load container:** its declared cap, else the 8G scope default, then the 16G scope swap. Since
  P-plat-19h the tier Max equals dev Max and is effectively inert, so the runaway's reclaim and OOM happen at
  dev.slice (`oom.group` kills the victim's container only; 5.3), and the victim is chosen across the whole dev tree,
  so a gate runaway can take reclaim from, or OOM, a background or buildkitd container; the load tiers no longer
  isolate each other. Gates + background reach dev Max: dev.slice reclaims the
  unprotected load while the cockpit's Min/Low and the host floors hold. An OOM follows only when reclaim makes no
  progress (neighbours down to protected or unreclaimable memory, or writeback stalls, R3); its victim is adj-0
  load. Host allocations find K free (section 4 invariant), so they do not wait behind dev swap-out. Until then
  neighbouring lanes slow down; nothing freezes earlier — operator-accepted (Q2), backlog **P-fd-05**.
- **R3, memcg OOM with free swap:** dev.slice's `io.max` (MDT-072) throttles swap writeback, so reclaim at a max can
  exhaust `MAX_RECLAIM_RETRIES` on pages under writeback. Unmeasured (the 6656M evidence had no dev.slice
  `io.max`); gated by section 8 step 6. If it fires, the remedy is an IO-model question, not a MemoryHigh.
- **R4, cockpit as victim:** −900 (2.7) in every domain that holds load; an unrebuilt cockpit has adj 0 (WARN).
- **R5, swap thrash / kernel memory growing into K:** lanes page by design, swap IO bounded by `io.max`, host
  latency by K and the floors; a global OOM goes to adj-0 load via −900.

## 10. Cross-tool interplay (for the controller)

- **`scripts/netcup/runner/provision-gates-limits.sh`** writes `dev-gates.slice.d/20-memory-policy.conf`
  (MemoryMax 6656M), `OOMPolicy=kill` on docker-.scope and `OOMScoreAdjust=-900` on ssh.service. Follow-up
  **NC-nn** (netcup backlog): skip the slice and scope halves when host-setup's `dev-gates.slice` fragment exists,
  drop ssh from the −900 list (sessions inherit it). Sequence NC-nn before host-setup lands on any runner host;
  check.sh WARNs meanwhile (section 7).
- **debian-install-v2:** DIV2-BL-08 only on hosts without host-setup. DI2's exact-equality `MemoryMin` readback
  (`installer.py:1167-1172`) fails after a host-setup raise (r1 12c): DI2 backlog, accept ≥ when a
  `95-mdt-memory-min.conf` is in `DropInPaths`. New floor rows go to DI2's table first; check in `tools/hotset.py`.
- **Spawner caps:** no backlog item just for a cap; one is filed only where a spawner has its own reason to declare
  a tighter cap (5.3). **fleetd:** P-fd-05 carries the freeze.

## 11. Open questions for the operator

None. Answered 2026-11-24 ~09:45Z: `DEV_INTERACTIVE_MEMORY_LOW` stays 2.5G and is never lowered; no watcher cap,
the 8G `docker-.scope` default is the per-container default (5.3).

## Appendix A: review r1 disposition

Fixed: 1 (6.2, 7), 2 (6.3), 3 (5.1, 5.2), 4 (6.2), 6 (Low derived from K, r2 N1; 4), 7 (4), 8 (7, 10), 10 (4, 6.1),
11 (header), 12 (4, 8, 10), 13 (4.1). Gated: 5 (R3, section 8 step 6). Operator-accepted: 9 (Q2, P-fd-05).
r2: N1 (4, 6.1), N2 (5.2), N4 (5.2, 6.2, 7), E1-E6 (5.3, 6.1, 7, 8, 9, 10, 11).
