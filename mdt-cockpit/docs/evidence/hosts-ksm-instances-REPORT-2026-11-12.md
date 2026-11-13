<!-- origin: 2026-11-12 -->
<!-- status: historical host evidence report, scrubbed copy -->
<!-- copied from the controller program dir 2026-11-12 -->

# KSM eligibility and multi-instance R2 (r1002 and v1001) (operator directive a/b/c)

**Status: DONE** — work 2026-11-12 12:25Z - 13:57Z. Host-operations agent: Claude Sonnet. Hosts r1002 and v1001 only, through the identity-checking wrappers. Nothing under <checkout> modified, nothing committed or filed. Queue pause flags untouched, no io.cost / IO limit / swap / vda5 changes, no docker or containerd restart, no tokens/keys/addresses written here.

## 0. Summary
1. **The premise changed: both campaigns had already finished before this task started.** pwmcp `t3-pwmcp-v1.64.1-r7-r2-s4` ended 12:00:27Z (final-exit 2, verdict ERROR/EXEC_FAILED, 1164/1165 completed: 991 killed, 171 survived, 2 crashed, 1 budget_exceeded, 0 pending); ciu `t3-ciu-v7.25.0-ciu-s1` ended 09:38:59Z (final-exit 4 `stalled`, all 265 pending done; only terminal `budget_exceeded` records left). Nothing was left to restart (a resume only repeats the baseline: ciu-r2-7241-REPORT section 4), so I did **not** restart or alter either campaign. The real multi-instance evidence was produced by pilots (section 6).
2. **KSM was already on and persisted on both hosts** (`/etc/tmpfiles.d/vbpub-ksm.conf`, written by debian-install-v2 `_configure_ksm`: run=1, advisor_mode=scan-time, target 200 s, use_zero_pages=1; kernel 7.2.12, `ksmd` running). I changed no KSM setting (setting `pages_to_scan=1000` would fight the advisor, which owns that knob). Reason it "does nothing": KSM only scans memory of processes that opted in; `pages_sharing` was 0 on both hosts before the pilots (lane containers never opt in).
3. **Probe (r1002, container `ksm-probe-20261112`, removed):** two processes with 180 MiB of identical pages: no opt-in -> `pages_sharing` 0; `prctl(PR_SET_MEMORY_MERGE)` in the parent before fork+exec of the children -> **91,824 pages shared (359 MiB saved) within 15 s**; per-mapping `madvise(MADV_MERGEABLE)` -> 91,800. The flag survives fork **and** exec. `systemd-run -p MemoryKSM=yes` on systemd 257 gives children `ksm_merge_any: yes` (without it: no).
4. **But real lane workloads gain ~nothing from KSM:** three pytest processes of the ciu suite in one container (tester-unified, ~130 MiB anon each), with the prctl-before-exec wrapper: `pages_sharing` 207-279 (~1 MiB of ~390 MiB anon), `general_profit` **negative** (-3.7 to -4.3 MB: KSM bookkeeping exceeds the gain), container memory unchanged within noise (430 vs 432 MiB at 30 s, 619-638 MiB at 120 s). Python heaps are not page-identical across processes. KSM is worthwhile for identical-image workloads (VMs, JVM fleets), not for these pytest/assay workers. Directive (c) is therefore "in place, but the overcommit plan must not count on it for python test lanes" (proposal, section 5).
5. **Directive (a), measured (ciu pilot, same lane config, 3 CPUs of work):** 1 instance x 3 workers (v1001) = 804 MiB container working set, ~255-300 % CPU; 3 instances x 1 worker (r1002) = 365+354+395 = 1114 MiB, ~285 % CPU. So ~+310 MiB (+38 %, about +100 MiB fixed cost per instance: its own baseline suite, clone, assay process), not "the same", but small against a 3 GiB lane cap each. Throughput is the same within the candidate-mix noise (3 x 1: 28 candidates in 16.5 min = 1.7/min; 1 x 3: 22 in the same window = 1.3/min, then 118 in 48 min = 2.4/min average; 4 x 1: 31 in 17 min = 1.75/min).
6. **Instance count does not raise CPU use.** One container with 3 workers already holds ~255-300 % (v1001) when 3 candidates run; 3 x 1 gives ~275-285 %. The 4-core hosts do not go beyond ~3.2-3.3 busy cores for the lane: the 4 x 1 pilot ran slice CPU 298-332 %, host CPU PSI some avg10 29-40 %, containers 51-93 % each, and its rate (1.75 candidates/min) equals 3 x 1 (1.7). So "pwmcp uses only one CPU" is not cured by more instances; the pwmcp samples (section 1) show the lane was never CPU-saturated (CPU PSI p50 1.1 %), i.e. the low-CPU windows are workers idling in long/hung candidates (15 min budget_exceeded/crashed) and the memory-runaway swap phase, plus the drained tail, not a missing instance. Where K x J would exceed ~3 workers on these hosts, only memory and tail-latency behaviour changes.
7. **Memory guard:** `R2_INSTANCE_MEM_MIB=1024 R2_HOST_RESERVE_MIB=1024` admitted 3 and 4 instances ("4 instance(s) x 1024 MiB + 1024 MiB reserve <= MemAvailable 6566 MiB"); measured per-instance working set for ciu 250-520 MiB (peak sample 515 MiB), so 768 would also be safe for ciu; the declared 3g cap x 3 + 1 GiB (10240 MiB) would have been refused.
8. **Finding:** `--base ciu-v7.22.0` on a checkout whose HEAD is the current `main` (b6a6274df) yields `INCONCLUSIVE/NO_MUTANTS` (planned=0) in both the single and the sharded form, while the same flag on the ciu-v7.25.0 commit gives 715 candidates. Without `--base` the ciu lane refuses ("delegates its comparison base; pass --base REF"). Not diagnosed; section 10 entry 1.
9. Host changes: only throw-away files (section 4); everything removed or listed there. All pilots stopped, 0 lane containers on both hosts at the end, queue flags untouched.
10. Recommendation: keep the single-instance `--jobs 3` start for ciu/pwmcp on 4-core hosts; use `--instances` only when a lane's workers cannot share one container (e.g. a lane with jobs_max 1 shards for a second host) or to split work across the two hosts (`--shard-total/--shard-offset`). KSM: leave as is; treat it as ~0 for python lanes.

## 1. Premise check (state found at 12:28Z)
- Previous report (07:10Z) said s4 (pwmcp) and s1 (ciu) were running. At 12:28Z `docker ps` showed only the cockpit (v1001) / cockpit + buildkitd (r1002), load 0.2.
- pwmcp: `campaigns/t3-pwmcp-v1.64.1-r7-r2-s4/attempts.log`: attempt 1 `exit=4 completed_candidates=880->1037` (09:42:50Z), attempt 2 `exit=2 completed_candidates=1037->1170` (12:00:27Z). Final `progress-r2.jsonl` events: 1170 candidate events (1165 + 5 re-run crashed), `end` buckets `{budget_exceeded 1, crashed 2, killed 991, survived 171}`, `campaign-r2.txt`: `incomplete outcome=ERROR planned=1165 completed=1164 pending=1`, blockers `coverage_not_reverified, lane_timeout_or_unstarted`, carry-over line `875 counted result(s) were IMPORTED`. Verdict R0 PASS, R1 PASS, R2 ERROR/EXEC_FAILED, exit 2. The "pending 1" is the budget_exceeded record (terminal; resume does not re-run it). Mem-pressure over the whole 6.3 h campaign (samples.csv, 10 s): gates-slice memory p50 1290 MiB, p90 1491 MiB, max 3427 MiB; memory PSI some avg10 > 20 % in 43 of 2265 samples (1.9 %, max 50.6 %, the cli.py 853-876 phase); CPU PSI some avg10 p50 1.1 %, p90 3.9 % (never CPU-saturated).
- ciu: attempts `0->265` (09:22:55Z), `265->265` (09:30:59Z), `265->265` (09:38:59Z), `stopping: stalled=2`, final-exit 4, `reason=budget stop_reason=stalled`. All 265 pending candidates done; the remaining non-terminal state is the 7 `budget_exceeded` oneshot.py mutants (ciu-r2-7241-REPORT section 4).
- Decision: nothing to restart (>100 pending criterion not met: 0 pending on both). Restarting pwmcp -s5 would only re-run the baseline (~7 min) and re-emit ERROR from the same stored records (2 crashed + 1 budget_exceeded).
- Evidence to collect (unchanged commands in the previous report section 5): pwmcp `campaigns/t3-pwmcp-v1.64.1-r7-r2-s4`, checkout `.assay` + `assay-state`; ciu `campaigns/t3-ciu-v7.25.0-ciu-s1`, checkout `.assay` + `assay-state`.

## 2. KSM facts, read-only, before (12:28Z)
| item | v1001 | r1002 |
|---|---|---|
| kernel / systemd | 7.2.12+deb13-amd64 / 257.13 | same |
| CONFIG_KSM | y | y |
| `ksmd` | running (pid 59) | running (pid 60) |
| run / sleep_millisecs / pages_to_scan | 1 / 20 / 30000 (advisor-driven) | 1 / 20 / 30000 |
| advisor_mode / smart_scan / use_zero_pages / max_page_sharing | scan-time / 1 / 1 / 256 | same |
| pages_shared / pages_sharing / unshared / volatile | 3 / 0 / 1 / 0 | 3 / 0 / 3 / 25 |
| general_profit | -256 | -1984 |
| `systemctl show docker.service containerd.service -p MemoryKSM` | no / no | no / no |
| KSM config on the host | `/etc/tmpfiles.d/vbpub-ksm.conf` (Nov 8) | same (Nov 12) |
| MemTotal / swap | 7.75 GiB / 64 GiB | same |
- Source of the config: `scripts/debian-install-v2/debian_install_v2/installer.py` `_configure_ksm` (`KSM_TMPFILES`: `w! run 1`, `advisor_mode scan-time`, `advisor_target_scan_time 200`, `use_zero_pages 1`); `Config.run_ksm = True`. mdt host-setup has no KSM item; the design memo `docs/design/iogovmon/DESIGN-rev9-2026-11-06.md` lists `ksm` as an iogovmon baseline item with the same defaults.
- Who already opts in: the **cockpit container** `mdt-cockpit-runner` (image bakes `LD_PRELOAD=/usr/local/lib/ksm-optin.so`, mdt-cockpit/Dockerfile ~1295): its `run-gate`/`docker` client processes show `ksm_merge_any: yes` and a few hundred merged pages (the 400-800 `pages_sharing` seen during the pilots). The **lane containers** (`tester-unified`, `tester-base`) carry no shim: 0 opted-in processes.
- Kernel facts: `/proc/<pid>/ksm_stat` has `ksm_merge_any` / `ksm_mergeable`; the dockerd/containerd shims (parent pid 1 after daemonising) are not opted in.

## 3. KSM probe results (r1002)
`docker run --rm --name ksm-probe-20261112 --cpus 0.5 --memory 512m --cgroup-parent dev-gates.slice --entrypoint /opt/tester-venv/bin/python tester-unified:local /p.py none prctl madvise` (script: parent forks+execs two child pythons, each fills 180 MiB with one repeating non-zero 4 KiB page, 75 s hold, global counters read every 15 s):

| mode | pages_sharing (peak) | pages_shared | general_profit | per-child `ksm_stat` |
|---|---|---|---|---|
| none | 0 | 3 (baseline) | -1984 | merging 0 |
| parent `prctl(67,1,0,0,0)`, children via fork+exec | **91,824** (359 MiB) after <=15 s | 375 | +370,110,592 | merging 46,095 each, `ksm_merge_any: yes` |
| children `madvise(MADV_MERGEABLE)` | 91,800 | 363 | +370,112,576 | merging 46,080 each, `ksm_mergeable: yes` |

`PR_GET_MEMORY_MERGE` returned 1; the prctl needs no capability and works under the default Docker seccomp profile. The flag is inherited by fork **and survives execve** (children were fresh `python -c` execs).

**Realistic workload (same container shape as a lane: `--cpus 3 --memory 3g`, checkout mounted read-only, three concurrent `python -m pytest tests -x` of the ciu suite, ~130 MiB anon each):**
| mode | t=30 s | t=90 s | t=120 s |
|---|---|---|---|
| none | cgroup anon 387 MiB, KSM 0 | anon 396 MiB | anon 431 MiB / current 638 MiB |
| prctl-then-exec wrapper | anon 385 MiB, `pages_sharing` 252, profit -3.70 MB | anon 429 MiB, sharing 279, profit -4.25 MB | anon 427 MiB / current 619 MiB, sharing 207, profit -4.22 MB |
=> ~1 MiB merged, negative net profit, no measurable container saving. (Proxy: the real R2 worker runs the same pytest process shape; I did not run an A/B on a full campaign because the lane image cannot opt in without a rebuild.)

`systemd-run --quiet --wait --pipe -p MemoryKSM=yes -p Slice=dev-gates.slice sh -c 'grep ksm_merge_any /proc/self/ksm_stat; sh -c "grep ksm_merge_any /proc/self/ksm_stat"'` -> `yes`, `yes`; a plain `sh` -> `no`. So `MemoryKSM=` works with this systemd and children inherit it.

## 4. What was changed on the hosts
No KSM sysfs value was written; no unit, tmpfiles, sysctl or docker setting changed on either host. Created (all temporary, pilot-only):
| host | path / object | purpose | state at end |
|---|---|---|---|
| both | `/usr/local/sbin/ksm-sample-20261112.sh` (root, 0755) | read-only sampler (cgroup/PSI/KSM) | removed at the end (see 9) |
| r1002 | container `ksm-probe-20261112` (3 runs, `--rm`) | probes | gone |
| r1002 | `<runner-home>/checkouts/f45050779d09eb535ab776a1a1375015b2a8f10e` | clone of the ciu-v7.25.0 checkout + 1 empty host-local commit (distinct checkout identity; the ciu-v7.25.0 checkout `a7f0f40b…` and its finished state were not touched) | removed |
| v1001 | `<runner-home>/checkouts/a7f0f40b6ee3d38de82ee1ebb7d54a399e04e125` (synced from the existing bundle, `--no-current-link`) and `<runner-home>/checkouts/194ed3472f4372b78a4b418d720c448cc7b4e641` (clone + empty commit) | pilot checkouts | removed |
| both | `<runner-home>/campaigns/pilot-ciu-20261112*`, `<runner-home>/jobs/pilot-ciu-20261112*` | pilot evidence | kept (small); see 8 |
| both | main checkout `b6a6274df…`: `ciu/.assay`, `.run-gate/assay-{state,shards}` created by the failed first pilots | | removed again (checkout clean, `git status` empty) |

## 5. Proposal: make lane containers KSM-eligible (only worth it for identical-image workloads)
Measured value for pytest/assay lanes is ~0 (section 3), so this is **optional**; do not count on KSM in the overcommit plan for them. If wanted, in order of preference:
1. **Host level, no image change, covers every container (including lanes and VM lanes):** drop-in `/etc/systemd/system/containerd.service.d/vbpub-ksm.conf` with `[Service]` / `MemoryKSM=yes`, then `systemctl daemon-reload && systemctl restart containerd` on a drained host (no lane running; the dockerd `live-restore=true` is on, the cockpit shim survives, but do it in a maintenance window, never with a campaign running). New containers' shims are forks of containerd and inherit the flag, container processes inherit it from the shim, exec keeps it (all three steps verified except the containerd restart itself, which is why it is a proposal; the `systemd-run -p MemoryKSM=yes` test proves the unit property and inheritance). `systemctl show containerd -p MemoryKSM` must read `yes`; then `grep ksm_merge_any /proc/<pid of a lane process>/ksm_stat`. Rollback: delete the drop-in, daemon-reload, restart containerd. Effect also on the cockpit and buildkitd when they are recreated.
2. **Image level (self-contained, travels with the image):** build the libc-free shim `ciu/src/ciu/data/ksm-optin.c` (already used by ciu, `-nostdlib`, zero DT_NEEDED) in `tester-base/Dockerfile` (python:3.14.7-trixie has gcc) and `ENV LD_PRELOAD=/usr/local/lib/ksm-optin.so` exactly like mdt-cockpit does; or an exec wrapper (`ksm-wrapper.c`, S15.20) as lane entrypoint; or, python-only and gcc-free, a `.pth` in `/opt/tester-venv` that runs `ctypes.CDLL(None).prctl(67,1,0,0,0)` at interpreter start. Needs a tester-base/tester-unified rebuild and release. run-gate's `forward_env` cannot be used for LD_PRELOAD (the cockpit's own `LD_PRELOAD` path does not exist in the lane image).
3. Not recommended: `MemoryKSM=yes` on `docker.service` (container processes are not dockerd's children; no effect).
Keep as is: run=1, advisor scan-time (the existing `vbpub-ksm.conf`); do not set a fixed `pages_to_scan` (the advisor adjusts it; the brief's `pages_to_scan=1000` would fight it).

## 6. Multi-instance: pilots
### 6.1 Why pilots and how
Campaigns were finished (section 1), so I ran throw-away ciu pilots, **fresh (no carry-over) on a distinct host-local commit of the ciu-v7.25.0 checkout** (`--base ciu-v7.22.0`, the real campaign's candidate set: 715 candidates), so the finished stores stayed untouched. First two attempts used the main checkout (b6a6274df) and ended `NO_MUTANTS` (section 0.8, 10.1); state removed.

Commands (runner; `run-job` is `<runner-home>/bin/run-job`, not on the non-login PATH of the wrapper):
```
# r1002: 3 instances x 1 worker
<runner-home>/bin/run-job pilot-ciu-20261112c-i3-s1 -- env R2_INSTANCE_MEM_MIB=1024 R2_HOST_RESERVE_MIB=1024 \
  bash <runner-home>/runner-scripts/run-r2-campaign.sh pilot-ciu-20261112c-i3-s1 f45050779d09eb535ab776a1a1375015b2a8f10e ciu ciu \
  --instances 3 --jobs 1 --base ciu-v7.22.0
# r1002: 4 instances x 1 worker (same, name ...d-i4-s1, --instances 4)
# v1001: control, 1 instance x 3 workers
<runner-home>/bin/run-job pilot-ciu-20261112c-i1-s1 -- bash <runner-home>/runner-scripts/run-r2-campaign.sh pilot-ciu-20261112c-i1-s1 \
  194ed3472f4372b78a4b418d720c448cc7b4e641 ciu ciu --jobs 3 --base ciu-v7.22.0
```
Guard output: `memory guard: 3 instance(s) x 1024 MiB + 1024 MiB reserve <= MemAvailable 6838 MiB` (4 instances: `<= 6566 MiB`). Fleet shape: one child campaign `<base>.iKofN-s1` per shard (20 s stagger), each its own lane container named `…-run-gate-ciu-ciu.shardKofN-…`, `--jobs 1` per child (clamp not needed), every container still `--cpus 3 --memory 3g` from the lane (the per-container cap does not limit the sum).

Per-instance jobs: `--jobs` is passed to every child and clamped to the lane `jobs_max` (3). With `--instances K --jobs J` the host runs K x J workers; the lane's `cpus = "3"` is per container, so K x J > nproc oversubscribes silently (the guard checks memory only).

### 6.2 Results (summary; samples in section 7)
| run | host | workers | containers' working set | slice CPU | candidates/min | host CPU PSI some avg10 |
|---|---|---|---|---|---|---|
| 1 x 3 | v1001 | 3 | 804 MiB (1.0 GiB at the first sample) | 255-300 % | 1.3 (22 in 16.7 min) | 5-13 % |
| 3 x 1 | r1002 | 3 | 365 + 354 + 395 = 1114 MiB | 271-282 % | 1.7 (28 in ~16.5 min) | 8-15 % |
| 4 x 1 | r1002 | 4 | 486 + 330 + 515 + 352 = 1683 MiB | 298-319 % | see 6.3 | 32-40 % |

### 6.3 4 x 1 on 4 cores
Admitted by the guard with R2_INSTANCE_MEM_MIB=1024. Mutation phase 13:34-13:52Z: 31 candidates in 17.1 min = 1.75/min (3 x 1: 1.7/min), slice CPU 298-332 % of 400 %, containers 51-93 % each (the fourth core is consumed by dockerd, cgprofile, the liveness sampler, ksmd and the cockpit client), host load average 5.5-7.2, **CPU PSI some avg10 29-40 %**, memory PSI 0.00, containers 340-568 MiB each (sum ~1.9 GiB). Conclusion: no gain over 3 workers on these 4-core hosts, only run-queue contention.

### 6.4 Shard merge plan (not run: pilots stopped, nothing to merge)
Fleet start `--instances K` already ends with an automatic strict merge run `<base>.merge-sN` (`run-gate <lane> --merge-shards K`) that needs every shard complete for the current judge+plan, folds them into the main store and writes the whole-lane verdict to the fleet's `verdict.json`. Controller commands:
- status: `bash <runner-home>/runner-scripts/run-r2-campaign.sh status <base>` (one line per start, child and merge run)
- stop (fleet: every child + its exact containers, then the fleet): `bash <runner-home>/runner-scripts/run-r2-campaign.sh stop <base>` (exit 143 everywhere)
- shard stores: `<checkout>/.run-gate/assay-shards/<project>/<lane>/<assay_lane>/{0of3,1of3,2of3}`, per-shard progress `<checkout>/<project>/.assay/progress-<assay_lane>.<lane>.shardKofN.jsonl`
- merge after a stop/crash or a split run: `<runner-home>/bin/run-job <base>-s2 -- bash <runner-home>/runner-scripts/run-r2-campaign.sh <base>-s2 <commit> <project> <lane> --merge-only K` (refuses by name on a missing/incomplete/foreign shard; shards that were stopped half-way must be resumed first by re-running the same `--instances K` start with the next suffix `-s2`, which resumes each shard store)
- carry-over with instances: `--carry-over-main "<reason>"` is accepted together with `--instances` (flag check only; I did not combine them live because the real campaigns were already finished). Finished candidates stay finished: each shard resumes its own store; the main store is only written by the merge.

## 7. CPU / memory / PSI samples (3 samples 10 s apart; slice = dev-gates.slice incl. page cache; container mem = docker stats usage minus inactive file)
**Before (idle, 12:36Z):** r1002 slice 0 %, load 0.15, MemAvailable 6854 MiB, PSI cpu/mem/io ~0; v1001 slice 0 %, load 0.22, MemAvailable 7000 MiB, PSI 0, swap used 17 MiB. KSM sharing 0 on both.

**v1001, 1 instance x 3 workers (pilot c-i1), baseline phase 12:40Z then mutation phase:**
```
12:40:30 slice_cpu=100% load=0.92 container cpu=147% mem=274 MiB   (baseline suite, 1 worker)
13:06:37 slice_cpu=284% slice_mem=2417MiB MemAvail=5985MiB load=3.41  psi cpu 5.58 / mem some 0.00 full 0.00 / io full 0.00
13:06:47 slice_cpu=289% slice_mem=2483MiB MemAvail=5849MiB load=3.53  psi cpu 6.25 / mem 0.00
13:06:57 slice_cpu=270% slice_mem=2508MiB MemAvail=5971MiB load=3.68  psi cpu 13.15 / mem 0.00     container 254.7%  1.003 GiB / 3 GiB  pids 14
13:19:52 slice_cpu=256% MemAvail=6130MiB load=3.81   psi cpu 8.26 / mem 0.00
13:20:02 slice_cpu=264% MemAvail=6085MiB load=3.68   psi cpu 8.54 / mem 0.00
13:20:12 slice_cpu=257% MemAvail=6085MiB load=3.58   psi cpu 10.83 / mem 0.00                container 299.7%  803.7 MiB / 3 GiB pids 12
13:52:55 slice_cpu=288% MemAvail=6155MiB load=3.96   psi cpu 7.30 / mem 0.00
13:53:05 slice_cpu=281% MemAvail=6194MiB load=3.89   psi cpu 8.05 / mem 0.00
13:53:15 slice_cpu=279% MemAvail=6209MiB load=3.76   psi cpu 6.21 / mem 0.00                 container 302.1%  979.1 MiB / 3 GiB pids 12
```
**r1002, 3 instances x 1 worker (pilot c-i3):**
```
13:05:59 slice_cpu=274% slice_mem=3179MiB MemAvail=5648MiB load=4.23  psi cpu 13.05 / mem 0.00 / io 0.00   containers 97.6% 309 MiB, 91.9% 313 MiB, 96.7% 330 MiB
13:06:09 slice_cpu=277% MemAvail=5743MiB load=3.96                    psi cpu 12.16 / mem 0.00
13:06:19 slice_cpu=277% MemAvail=5691MiB load=4.04                    psi cpu  9.90 / mem 0.00
13:19:18 slice_cpu=278% MemAvail=5527MiB load=5.19                    psi cpu  7.65 / mem 0.00   containers 95.0% 365 MiB, 84.2% 354 MiB, 96.9% 395 MiB
13:19:28 slice_cpu=282% MemAvail=5521MiB load=5.32                    psi cpu 10.39 / mem 0.00
13:19:39 slice_cpu=271% MemAvail=5466MiB load=5.43                    psi cpu 15.14 / mem 0.00
```
**r1002, 4 instances x 1 worker (pilot d-i4):**
```
13:38:51 slice_cpu=319% slice_mem=3793MiB MemAvail=5037MiB load=6.23  psi cpu 32.17 / mem 0.00 / io 0.00  containers 66%/93%/92%/66%, 352/330/515/486 MiB
13:39:01 slice_cpu=313% MemAvail=5048MiB load=6.26                    psi cpu 36.46 / mem 0.00
13:39:11 slice_cpu=298% MemAvail=4978MiB load=6.38                    psi cpu 39.72 / mem 0.00
13:52:20 slice_cpu=332% slice_mem=3994MiB MemAvail=5253MiB load=5.53  psi cpu 29.13 / mem 0.00           containers 87%/89%/86%/51%, 506/341/508/568 MiB
13:52:30 slice_cpu=326% MemAvail=5250MiB load=6.26                    psi cpu 33.11 / mem 0.00
13:52:40 slice_cpu=331% MemAvail=5244MiB load=7.19                    psi cpu 34.65 / mem 0.00
```
Memory PSI (some and full) stayed 0.00 throughout on both hosts; swap used unchanged (r1002 0 MiB, v1001 17-18 MiB). The stop rule "memory PSI full avg10 > 20 % for 3 min" never came near.

**KSM before/after (about 1.5 h):** v1001 run=1, `general_profit` -256 -> -256, `pages_sharing` 0 -> 0; r1002 run=1, `general_profit` -1984 -> -1984, `pages_sharing` 0 -> 0. During the pilots `pages_sharing` read 38-87 (v1001) / 410-790 (r1002), entirely from the cockpit's opted-in `run-gate`/`docker` client processes (`ksm_merge_any: yes`); lane containers contributed 0.

## 8. Controller commands (status / stop / collect)
- pwmcp (finished): `bash <runner-home>/runner-scripts/run-r2-campaign.sh status t3-pwmcp-v1.64.1-r7-r2`; collect with the tar command of hosts-r2-restart-REPORT section 5 (campaign dirs `…-r2-s4` and `…-r2`, the checkout's `pwmcp/.assay` and `.run-gate/assay-state/pwmcp`).
- ciu (finished): `status t3-ciu-v7.25.0-ciu`; collect per the same report.
- Pilot evidence (small, kept): `<runner-home>/campaigns/pilot-ciu-20261112*`, `<runner-home>/jobs/pilot-ciu-20261112*` on both hosts; safe to delete.

## 9. Rollback / cleanup (state at 13:57Z)
- No setting was changed, so there is nothing to roll back for KSM, docker, containerd, systemd, sysctl or tmpfiles.
- Removed: `/usr/local/sbin/ksm-sample-20261112.sh` (both hosts), temporary probe scripts under a temporary directory, the pilot checkouts (r1002 `f4505077…`; v1001 `194ed347…` and the synced `a7f0f40b…`), pilot state in the main checkout `b6a6274d…` (`ciu/.assay`, `.run-gate`); `git status` of the main checkouts is clean on both hosts.
- Kept (harmless evidence, delete with `rm -rf` of the exact names): `<runner-home>/campaigns/pilot-ciu-20261112{,b,c,d}-*`, `<runner-home>/jobs/pilot-ciu-20261112*` on both hosts.
- To undo the proposal in section 5 if ever applied: remove `/etc/systemd/system/containerd.service.d/vbpub-ksm.conf`, `systemctl daemon-reload`, restart containerd on a drained host.

## 10. Candidate backlog entries (not filed; check each backlog for duplicates first)
1. **run-gate / assay — `--base REF` on a `main`-HEAD checkout gives `INCONCLUSIVE/NO_MUTANTS` (planned=0).** Evidence (2026-11-12, r1002 and v1001, run-gate 25.5.0, tester-unified `ca0d86e5`/`c4e796bd`): `run-r2-campaign.sh … ciu ciu --jobs 1 --base ciu-v7.22.0` at `b6a6274df` (ciu-v7.25.0-9, ancestor of base verified, 4292 insertions in `ciu/src`) -> plan event, no candidates, exit 5; identical command on a clone of ciu-v7.25.0 + 1 empty commit -> 715 candidates. Without `--base` the lane refuses ("delegates its comparison base; pass --base REF (worktree has no upstream)"). Suspect the request-base scope (changed lines vs base) for non-tag HEADs / the 9 commits after the tag; needs a repro in run-gate/assay. Sharded form: `--base` was passed to all children; same result per shard.
2. **netcup runner — memory guard design (already being fixed by a separate job): measured per-instance figures for the record.** ciu pilot working sets 250-570 MiB per 1-worker instance (steady ~350-400, one 568 MiB sample), 1-instance x 3 workers 804-1003 MiB; `R2_INSTANCE_MEM_MIB=1024` is conservative, 768 would do for ciu. Proposed: the guard should use `resources.candidate_ram x jobs + fixed` (or a measured table), not the lane's `resources.memory` cap, and also warn when `instances x jobs > nproc - 1` (the lane `cpus = "3"` is applied per container, so nothing bounds the sum; 4 x 1 on 4 cores gave CPU PSI 29-40 % and no throughput gain).
3. **docs (runner README + CONSUMERS) — when `--instances` helps.** Proposed text: "On a 4-core runner one container with `--jobs 3` already uses ~3 cores; `--instances K --jobs 1` costs ~+100 MiB per instance (own baseline, clone, assay) and gives the same candidates/min (measured ciu 2026-11-12: 3 x 1 = 1.7/min, 4 x 1 = 1.75/min with CPU PSI some 29-40 %, 1 x 3 = 1.3-2.4/min). Use instances to split across hosts or when `jobs_max = 1` lanes shard, not to add CPUs on one host."
4. **AGENTS.md / overcommit plan — KSM expectation.** Proposed correction: "KSM is enabled host-wide (vbpub-ksm.conf, advisor scan-time) but only merges processes that opt in (prctl PR_SET_MEMORY_MERGE, MemoryKSM=, or the mdt-cockpit LD_PRELOAD shim). Lane containers do not opt in, and measured python/pytest workers share ~1 MiB of ~390 MiB anon with a net negative `general_profit`. Do not count on KSM for python test lanes; it pays for identical-image workloads (VMs)." (AGENTS.md line 222 currently lists KSM among the overcommit mechanisms.)
5. **tester-base / tester-unified — optional KSM opt-in** (only if a VM/identical-process lane needs it): ship `ksm-optin.so` + `LD_PRELOAD` or a `.pth` prctl hook (section 5.2); acceptance test: `grep ksm_merge_any /proc/1/ksm_stat` inside a lane. Low priority given the measurement.
6. **host-setup / debian-install-v2 — optional `MemoryKSM=yes` drop-in for containerd** (section 5.1) with a host checker (`systemctl show containerd -p MemoryKSM`, `ksm_merge_any` of a container process).
7. **pwmcp — final R2 verdict ERROR/EXEC_FAILED with 1164/1165 done** (2 crashed: cli.py memory-runaway mutants, 1 budget_exceeded). Already covered by the 07:10Z entry 8 (`memory_swap = "3g"`); add that the 5 re-run crashed became 2 crashed (853, 871) + 2 killed (855, 876) + 1 budget_exceeded (855), and that the supervisor stops on exit 2 (no retry), so the campaign ends without a PASS/FAIL R2 claim. Decide whether crashed/budget_exceeded records should be re-run with a larger budget/`memory_swap` as a deliberate follow-up lane.
8. **netcup runner — `run-job` not on PATH for non-login runner shells** (`<runner-home>/bin/run-job`); the host wrappers' `runuser -u runner -- bash -c` fail with `run-job: command not found`. Doc fix: use `<runner-home>/bin/run-job` in README examples or add `<runner-home>/bin` to the runner's non-login PATH.
9. **runner status — fleet line says `running` while only the merge run is active / after children failed**: with all three children `final_exit=1` the fleet still started a `merge-s1` run (observed running ~13:48Z; stopped with `stop`). Proposed: skip the merge when any child ended non-zero without completing its shard and report `merge-skipped`.
