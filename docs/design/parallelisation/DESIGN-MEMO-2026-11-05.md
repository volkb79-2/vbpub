<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-05; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/PARALLELISATION-DESIGN-MEMO-2026-11-05.md -->
<!-- relation: parallelisation base memo -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Parallelising assay mutation campaigns and gate lanes (design memo, 2026-11-05)

Status: DESIGN, no implementation. Inputs: the jobs audit (report + review), the materialisation, ZFS and tmpfs-worker
work, the container/VM doctrine, run-gate SPEC, the assay docs, and read-only probes of <host>/<host> run on
2026-11-04 ~19:37Z (host clock). Evidence tags: **[M]** measured (here or in a cited report), **[F]** read from
code/docs, **[I]** inferred, **[U]** unknown. Paths are under `<checkout>` unless stated; `RP` =
`<controller-program-dir>`.

---

## Executive summary

**The biggest lever is not parallelism, it is fail-fast.** Live progress files show killed mutants running the
whole suite: cmru killed median 572.6 s vs survived 571.8 s (49 killed, 10 survived, 1 crashed of 4640 planned).
pwmcp shows the same: killed 301.5 s vs survived 289.6 s over 279 killed [M, <host>/<host> progress files today].
82-98 % of candidates are killed, so stopping each one at its first failing test cuts campaign wall time by an
estimated 2.5-4x, depending on how early the first failure comes [I]. Measurement M1 settles that factor. Assay
should own this as a candidate-only argv addition (`-x`, `--no-cov`), so baseline coverage is untouched (WP1).
Do this before building anything else.

**Q1: `cow` or `tmpfs-worker`?**
- Keep `cow` as the behaviour where btrfs exists. tmpfs is **not** better on every dimension.
- tmpfs does win on disk reads: 0 MB per candidate against 173.6 MB today, and 3.2 s against 6.0 s per candidate
  [M].
- But B219 (primed index) already takes `cow` to 37 MB and 3.5 s. B220 removes the remaining 36 MB pack read
  [M/I]. After those two fixes the real gap is about 0.3 s per candidate.
- tmpfs costs RAM that cannot be dropped:
  - about 175 MB per worker for a whole-repo snapshot;
  - charged to the lane container and to `dev-gates.slice`, whose `memory.high` is **4 GiB on both hosts** [M].
- "Forcing IO through zswap" is a cost, not a benefit. Clean page cache from a CoW clone is reclaimed for free.
  tmpfs pages must be compressed into a zswap pool shared with every lane, and they push the tightest tier into
  `memory.high` throttling. That produces memory PSI, which is exactly the signal that stops scheduling.
- tmpfs has three further weaknesses:
  - it reuses one tree in place, so isolation depends on a residue scan;
  - it has no drop-the-tree-after-anomaly rule yet;
  - Docker gives a container only 64 MiB of `/dev/shm`.
- Do we need btrfs provisioning? Today, yes: assay, run-gate and tmpfs-worker itself all require `cheap_clone`
  (btrfs) for more than one worker [F]. Both runner hosts already have it (455 GB `/srv/cow`) [M].
- Recommendation:
  - add `materialize = "auto"` (cow on btrfs, otherwise tmpfs-worker when capacity is proven, otherwise serial);
  - decouple tmpfs-worker from btrfs (WP4);
  - replace run-gate's hard-coded btrfs predicate with an assay-reported `parallel_capable` fact (WP3).
  New hosts then need no btrfs.
- Separately, put per-worker `TMPDIR`/`HOME` on a container tmpfs. That is where tmpfs actually helps
  (write-heavy suites), independent of how the tree is materialised.

**Q2: why do cmru, run-gate-project and cgroup-profiler get only 2 workers?** Each has a different reason.
- **cmru: a real memory limit.** Each candidate uses 0.95-1.75 GiB RSS at about 85 % of a core. With a 4g lane,
  75 % `ram_target` and 1536m per candidate, the auto formula gives 2. Two candidates (about 3.5 GiB) already fill
  the host's 4 GiB gates tier.
- **run-gate-project: the auto formula on a 2g lane.** 1.5g / 700m = 2. With 23 candidates the cap hardly
  matters.
- **cgroup-profiler: caution, not a measured limit.** The formula allows 3, and RSS has never been measured.
- Raising any of them needs: WP1 (no-cov candidates use less RAM and CPU), measurement M2, and tier-aware
  admission (WP8). The concurrent-window rule (B157) is no longer the reason; assay 11/12 lifted it.

**Q3: what about the problem lanes (ciu, mdt-cockpit, mdt python-debug, <private-host>-guide)?**
- **Split them inside assay, not across lanes.** A mutant is killed when any part of the suite fails, and R1
  coverage is the union of all parts. Both are per-candidate assay logic, so merging separate lane verdicts in
  the rigor record would be unsound. Assay gets `parts` with an `exclusive` part that holds a serial token. The
  parallel-safe part runs first; the exclusive part runs only for candidates that are still alive (WP9).
- **Placement boundary, already decided** (`tester-base/docs/DESIGN-GUIDE.md:145-150`,
  `mdt-cockpit/docs/CONTAINER-DOCTRINE.md:105-124`):
  - **Containers** for anything that drives Docker. This includes real Docker behaviour through a digest-pinned
    DinD sidecar (Docker-in-Docker: a nested daemon in its own container), as cmru `tester-gate --enable-docker`
    already does.
  - **QEMU/TCG VM** only for host-kernel state: loop devices, swap, sysctl, systemd as PID 1, slices, reboot.
- **No KVM on either host** (`vmx/svm` = 0, no `/dev/kvm`) [M]. TCG is 21-26x slower than native [M], so:
  - VM lanes are small R0/R1 acceptance lanes, never R2;
  - Firecracker, Kata and Cloud Hypervisor are out, because all three need KVM.
- **Per-lane findings:**
  - **<private-host>-guide** is parallel-safe now (ephemeral ports, `tmp_path`). Its one real-Docker file skips
    silently in tester-unified.
  - **ciu** lane containers have no Docker socket. Its in-tree shared fixture lives inside each snapshot. The
    real hazard is a shared `HOME`/`/tmp`, which per-worker dirs fix.
  - **mdt-cockpit** host_setup peaks above 4.1 GiB in one process. That breaks the 4 GiB tier even at jobs 1; find
    the cause first (M3).

**Q4: tests that use a fake Docker runner.**
- The uid-65534 `py_compile` smoke failure (`docs/reports/dstdns-p4-REVIEW.md:13-17`) is a container-tier problem,
  not a VM-tier one. It needs a real daemon, not a separate kernel.
- Add a `real_docker` marker that runs against a per-lane DinD sidecar (run-gate `docker = "dind"`, WP10). A skip
  must count as a failure, the same way debian-install-v2's `os_integration` plugin already works.
- Keep the fakes, which give fast and exhaustive branch coverage. Pin them to reality with paired
  contract cases run against both the fake and DinD.
- Use a VM only when the subject is the daemon or kernel itself, as in mdt's large-OCI VM plan.

**Cross-host sharding (the cmru-sized plan).**
- Assay already has `--shard i/N` with a keyed-digest partition and a library-level merge proof
  (`assay/src/assay/mutation.py:3019`). It lacks three things:
  - a CLI for that merge;
  - concurrent shards on one state directory (the whole-directory lock at `assay/src/assay/cli.py:933`);
  - run-gate support: run-gate has no `--shard` flag.
- Recommended shape, **sharded execution with an unsharded verdict**:
  - shards run anywhere;
  - their per-candidate state records are united with verification (`assay state import`);
  - one final `--resume` 0/1 sealing pass (all cache hits plus one baseline) produces an ordinary qualifying
    verdict.
- With that shape the rigor record, `assay verify` and `analyze export` stay unchanged (WP5-WP7). The tester image
  ID must become part of the judge identity, so that records from different images never combine.

**Measure first, on <host>/<host> (section 8):**

| Id | What | Decides |
|---|---|---|
| M1 | cmru time-to-first-failure from baseline durations (about 15 min) | whether whole-source cmru is feasible after WP1 |
| M2 | per-candidate RSS for cgroup-profiler, ciu and <private-host> | `jobs_max` for those lanes |
| M3 | mdt per-test-file RSS | what drives the >4.1 GiB peak |
| M4 | two concurrent ciu/<private-host> R0 containers, 3 times | whether those suites are concurrency-safe |
| M5 | the prepared tmpfs-worker A/B (OFF vs ON on the same lane) | whether tmpfs-worker earns its keep |
| M6 | one hour of gates-tier memory, zswap and PSI under current load | the real headroom for more workers |
| M8 | a DinD sidecar running the uid-65534 smoke | that the container tier catches this class of bug |
| M10 | `judge_sha256` equality across hosts | whether cross-host shard records can be combined |

**Build order:**

| Order | Package | Owner | Size |
|---|---|---|---|
| 1 | WP1 fail-fast candidates | assay | S |
| 2 | WP2 B219/B220/B222 | assay | S-M, branches exist |
| 3 | WP3 `jobs = "auto"` without the btrfs clamp | run-gate + assay | S |
| 4 | WP6 `--shard` passthrough | run-gate | S |
| 5 | WP5 shard-scoped lock + state import | assay | M |
| 6 | WP7 cross-host shard driver | remote-gate | M |
| 7 | WP8 tier-aware admission | run-gate | M |
| 8 | WP4 tmpfs-worker without btrfs | assay | M |
| 9 | WP9 suite parts | assay | M-L |
| 10 | WP10 DinD environment + `real_docker` | run-gate | M |
| 11 | WP11 VM environment contract | run-gate | M |

**Do not build:** a ZFS backend, an overlay backend (it needs privilege), a generic shared VM host,
merging of R2 shards inside rigor-record, or claim-queue work stealing within one host. Three workers per host is
already at the knee, and hosts are shared.

---

## 1. Facts this memo rests on

| Fact | Value | Source |
|---|---|---|
| Host CPU / RAM | <host> and <host>: 4 vCPU (EPYC, KVM guest), 7.9 GB RAM | [M] probe 2026-11-04 19:37Z |
| Hardware virtualisation | `egrep -c '(vmx\|svm)' /proc/cpuinfo` = 0 and `/dev/kvm` absent on **both** hosts | [M] probe; same on the controller host: `scripts/debian-install-v2/testing/vm/DESIGN.md:110-119` |
| Gates tier (`dev.slice/dev-gates.slice`) | `cpu.max` 4 CPUs; `memory.high` **4 GiB**; `memory.max` 5.5 GiB; `memory.swap.max` 12.4 GiB (<host>) / 6.0 GiB (<host>); `memory.zswap.max` max | [M] probe |
| zswap | enabled, zstd, `max_pool_percent=25` (about 2 GiB), `accept_threshold_percent=90`, shrinker on; `vm.swappiness=10`; swap 16 GiB (<host>) / 8 GiB (<host>, after ZFS took two partitions) | [M] probe; `RP/reports/zfs-eval-REPORT.md:25` |
| CoW root | `/srv/cow` btrfs 455 GB, `user_subvol_rm_allowed`, about 1 % used, on both hosts | [M] probe |
| `/dev/shm` | host 3.9 GB; **64 MiB inside a Docker lane container** (no run-gate key yet, RG-163) | [M] probe; `docs/reports/assay-tmpfs-CONTINUATION.md:15` |
| Root disk | <host> `/` **97 % full (1.4 GB free)**; <host> 77 % | [M] probe (risk for image loads and DinD stores) |
| Disk | one virtio disk with an IOPS cap of about 300; the reads in the steady state are file-by-file `git status` reads | [M] `RP/reports/materialize-experiment-REPORT.md:17,118` |
| Per-candidate materialisation (whole-repo tree, 127 MB plus 36 MB pack) | btrfs today: 173.6 MB read, 2.31 s; primed index (B219): 37.2 MB, 0.33 s; tmpfs-worker (d2): 0 MB, 0.16 s; overlay on tmpfs (e2): 0 MB, 0.29 s; totals 6.03 / 3.53 / 3.22 / 3.63 s | [M] `materialize-experiment-REPORT.md:34-44` |
| ZFS | no faster than primed btrfs; reflink on ZFS is 2.5x worse; DKMS/CDDL burden | [M] `zfs-eval-REPORT.md:7,42-52` |
| Unprivileged overlay in a lane container | impossible under default seccomp/AppArmor | [M] `materialize-experiment-REPORT.md:120-129` |
| Parallel requires btrfs | run-gate auto clamps to 1 unless `cheap_clone` (`run-gate-project/run_gate.py:4639-4640`); an explicit N > 1 refuses (`:4648-4657`); the probe hard-codes btrfs (`:11228-11252`); assay refuses too (`assay/src/assay/mutation.py:3639-3655`, `runner.py:6505-6515`); tmpfs-worker itself requires `cheap_clone` (`.worktrees/assay-tmpfs-20261105/assay/src/assay/isolation.py:960-967`) | [F] |
| Auto formula | `min(jobs_max, cpus, ram_target × lane memory / candidate_ram, remaining)`, then 1 without `cheap_clone` | [F] `run_gate.py:4626-4640`; `docs/reports/assay-jobs-audit-REVIEW.md:29-38` |
| Concurrent windows (B157) | a limit event is attributed to every overlapping window; a failing candidate in that overlap becomes `crashed` (then a serial retry); PASS stays `survived` | [F] `assay/README.md:348-361` |
| Coordinator knee | iobench jobs 3 = 2.9x, jobs 4 = 3.0x; integrity step 0.5 -> 9.7 -> 42 s; materialisations finish in lock-step (likely GIL [I]) | [M] `RP/reports/dstdns-parallelism-REPORT.md` |
| Sharding today | `assay plan/run --shard INDEX/COUNT`, keyed-digest partition, merge = manifest set proof (`merge_mutation_shards`, library only, no CLI); one 0/1 shard qualifies, a multi-shard part does not | [F] `assay/docs/DESIGN-GUIDE.md:1672-1674`, `assay/docs/CONSUMERS.md:1817-1826`, `assay/README.md:101-102`, `assay/src/assay/mutation.py:3019` |
| State lock | a whole state directory is locked per run ("already in use by another"), so one run per checkout (run-gate keys the state dir by checkout) | [F] `assay/src/assay/cli.py:933,1017`; `run-gate-project/SPEC.md` R-38 (:1721-1730); `RP/<host>/R2-BOARD-2026-11-05.md:72,82` |
| run-gate `--shard` | none (usage error, rc 2) | [M] `docs/reports/assay-tmpfs-CONTINUATION.md:17` |
| Live campaign buckets | cmru: killed 49 / survived 10 / crashed 1 after about 12 h (plan 4640); killed median 572.6 s, survived 571.8 s; first candidate materialise 34.8 s. pwmcp: killed 279 at 301.5 s, survived 4 at 289.6 s, materialise median 17.2 s. run-gate-project (has `-x`): killed 152 s, survived 208 s | [M] read-only parse of `progress-*.jsonl` on <host>/<host> today |
| cmru R2 argv | full suite with `--cov`, no `-x` | [F] `.worktrees/assay-jobs-audit-20261105/cmru/assay.toml:166-171` |
| TCG cost | about 66 s from start to SSH (1 vCPU, 1 GB); Geekbench TCG/native 21-26x slower; runner peak 3.09 GB with a 4 GiB guest | [M] `testing/vm/DESIGN.md:407-420`, `testing/vm/USAGE-TESTING.md:490-534` |
| Privileged systemd-as-PID1 container | cannot start on the controller's Docker daemon (control image `jrei/systemd-debian:12` also fails) | [M] memory `resume-2026-09-08-netcup-debian-install-v2-livetest.md:994-1010` |
| Operator rules | PSI some avg10 > 25 or heavy swapping limits scheduling; up to about cores + 1 single-thread jobs | brief; `docs/reports/AGENT-RULES.md` |

---

## 2. Q1: materialisation (`cow` vs `tmpfs-worker` vs overlay/ZFS)

### 2.1 Comparison

| Dimension | `cow` (btrfs snapshot, after B219+B220) | `tmpfs-worker` (B223 branch) | overlay (B221) / ZFS |
|---|---|---|---|
| Steady disk reads per candidate | 37.2 MB measured with B219; about 1 MB after B220 removes the 36 MB pack walk [M/I] | 0 MB [M] | 0 MB [M] |
| Materialise + integrity | 0.33 + 0.07 s [M] | 0.16 + 0.06 s [M] | 0.29 + 0.10 s (overlay); 0.30-0.50 s (ZFS) [M] |
| Memory | page cache only: clean and reclaimable for free; charged to the lane's memcg when first touched | **template-sized RAM pinned per worker** (175 MB for the whole vbpub repo) plus transient writes; shmem is charged to the writing task's memcg (the lane container), counts in `memory.current` and towards `dev-gates.slice` `memory.high` 4 GiB; reclaim only by swap-out | overlay upper 37 MB shmem per live candidate [M]; ZFS ARC outside the memcg |
| Under memory pressure | reclaim = drop clean pages (no CPU, no IO); re-access = disk read (IOPS cap about 300) | reclaim = compress into zswap (CPU), shared pool about 2 GiB across every lane; writeback to disk swap when the pool fills; every access to an evicted page = decompress | n/a |
| Tree size ceiling | 455 GB btrfs | min(`tmpfs_max_mib` default 1024, tmpfs size, lane memory). Docker default `/dev/shm` is **64 MiB**, so it needs a container tmpfs mount (RG-163) | overlay: host-side mount |
| Isolation | fresh clone per candidate; residue cannot cross candidates inside the tree | one tree reused in place; `settle()` restores and runs a stat-walk (type, mode, size, mtime_ns, link). It misses a same-size rewrite with restored mtime and inode; an unclean tree is dropped. **There is no drop-after-crash, hang, budget or limit rule** (B223 deviation, `docs/reports/assay-tmpfs-CONTINUATION.md:9`), so a leaked process can write after `settle()` | fresh per candidate |
| Resume / retry | candidate trees never persist (state records live in `--state-dir`); the template is rebuilt per invocation | same; the tree dies with the container; rebuild = one copy per worker | same |
| Provisioning | btrfs area (done on both hosts; `cowtree` ioctl, no root at run time) | none on the host, but **today it requires a `cheap_clone` workspace anyway** (`isolation.py:960-967`) | root mount helper per candidate (overlay) or DKMS + pool (ZFS) |
| Works without btrfs (controller devcontainer, CI) | no (parallel clamps to 1) | not yet (WP4 fixes it) | no |

### 2.2 The zswap question

tmpfs does not "force IO through zswap" in a useful way.

- **Without memory pressure:** both trees sit in RAM and behave the same.
- **With memory pressure:**
  - the CoW tree's pages are clean file cache, so the kernel drops them at no cost;
  - tmpfs pages are dirty anonymous-like memory, so the kernel must compress them into zswap. The 25 % pool is
    shared with every lane's heap, and the kernel decompresses on every later access.
- The pinned tmpfs bytes count against the gates tier's 4 GiB `memory.high`. They push the tier into `high`
  throttling sooner, which raises memory PSI. The operator rule then stops scheduling other lanes.
- tmpfs therefore turns a disk-IO problem into a memory-pressure problem, in the tier that is already the
  scarcest resource.

The disk-IO problem it would solve is almost gone after B219/B220.

What tmpfs genuinely saves is **writes**. Tests that write into the tree or into `TMPDIR`/`HOME` include:
- ciu, which renders into `test-repo/` (`ciu/tests/conftest.py:150-168`);
- coverage data and `__pycache__`.

On btrfs or the container's overlay root those writes reach the disk; on tmpfs they never do. That benefit does not
depend on how the tree is materialised. Give each worker a small `TMPDIR`/`HOME` on a **container-scoped** tmpfs
(`--mount type=tmpfs,tmpfs-size=…`, freed with the container) instead.

Do not use host `/dev/shm` bind mounts. Files left behind keep their memory charged to a dead cgroup and outlive the
lane. The current experiment's `/dev/shm/assay-tmpfs-exp` directories on both hosts must be removed afterwards.

### 2.3 The btrfs clamp in `jobs = "auto"`: fix

Today four places hard-code "parallel requires btrfs":
- run-gate's auto clamp and explicit refusal (`run_gate.py:4639-4657`);
- the inline cowtree probe (`:11228-11252`);
- two assay guards (`mutation.py:3639-3655`, `runner.py:6505-6515`).

Required change (WP3 + WP4):

1. Assay owns the fact. It adds `parallel_capable` and `parallel_reason` to the prepared repository and to its
   capacity probe:
   - true with a `cheap_clone` workspace;
   - true when `materialize` resolves to tmpfs-worker and the capacity proof
     (`template_bytes × jobs + headroom ≤ min(tmpfs free, tmpfs_max_mib)`) passes;
   - otherwise false, with the reason.
2. Run-gate stops importing cowtree in its probe and runs `assay probe-capacity --json` (or the inventory field)
   instead. One owner per fact, as in the rigor memo's producer rule.
3. When auto clamps, run-gate prints a **WARNING** naming the reason and the remedy. The INFO line today is easy
   to miss: the audit had to tell people to grep `cheap_clone_reason=`.
4. Assay's two guards test `parallel_capable`, not `cheap_clone`.
5. tmpfs-worker no longer needs a btrfs template. The template is built and sealed (read-only, fully verified once)
   in a private tmpfs directory, and workers copy from it. Without btrfs, baseline and nested materialisations use
   the existing plain-copy path.

### 2.4 Recommendation

- **Default: `materialize = "auto"`**, which resolves in this order:
  1. `cow` when a btrfs `cheap_clone` workspace exists;
  2. otherwise `tmpfs-worker` when the tree is at most 256 MiB and the capacity proof passes;
  3. otherwise serial copy.

  B223 states that a verdict does not depend on the path, so `auto` is identity-neutral. The resolved mode is
  recorded in progress.
- **Use `tmpfs-worker` explicitly only** for short-candidate, IO-bound campaigns on small trees, where the M5 A/B
  shows a gain. Examples: cli-extended (about 11 s per candidate), dstdns lanes (6-22 s). **Never** use it for
  long candidates such as cmru (570 s) or pwmcp (300 s): materialisation is under 1-6 % of their wall time.
- **Gate tmpfs-worker on the anomaly rule.** After any `crashed`, `hung`, `budget_exceeded`, signalled leader or
  positive limit event, drop the worker tree unconditionally. This is part of WP4.
- **btrfs provisioning:** keep it where it exists (sunk cost; it serves large trees and keeps RAM free). Provision
  it nowhere new for assay's sake once WP4 lands.
- **Overlay:** no. It needs a privileged host mount per candidate.
- **ZFS:** no (zfs-eval). Do keep COW-BL-08 (warn on reflink-on-ZFS) in cowtree.

---

## 3. Q2: why only 2 workers for cmru, run-gate-project and cgroup-profiler

| Lane | Binding constraint | Evidence | What raises it |
|---|---|---|---|
| cmru `assay-first-release-r2` | **Memory (real).** 0.95-1.75 GiB RSS per candidate, about 85 % CPU. 4g lane × 75 % / 1536m = 2. Two candidates ≈ 3.5 GiB, which is the whole gates tier (4 GiB `memory.high`). A third would swap the tier and inflate `crashed` through B157 window overlap | `assay-jobs-audit-REPORT.md:27`, `docs/reports/assay-jobs-audit-REVIEW.md:13-17`, `cmru/assay.toml:242-247` (worktree) | WP1: `--no-cov` cuts candidate RSS and CPU [I; measure]. Cross-host shards (WP5-7). Tier-aware admission (WP8) so cmru is not co-scheduled with other heavy lanes |
| run-gate-project `assay-r2` | **Auto arithmetic on a 2g lane** (1.5g / 700m = 2). Tests spawn Docker/doctor. Only 23 candidates, so the cap barely matters (about 0.5 h). The R0 baseline currently fails on the hosts (doctor mountinfo) | `assay-jobs-audit-REPORT.md:29`; R2 board :55 | Nothing needed. Fix the R0 baseline first |
| cgroup-profiler `r2` | **Caution, not a limit.** The memory formula allows 3 (1.5g / 500m); RSS is "not observed"; the comment only says "two workers halve" | `assay-jobs-audit-REPORT.md:28`, `scripts/cgroup-profiler/assay.toml:53-60` (worktree) | M2 (RSS). If the p95 is under 500 MiB, set `jobs_max = 3` |

Shared context:
- All three were chosen while <host> ran three campaigns on 4 vCPU. The operator rule (about cores + 1 = 5
  single-thread jobs per host) caps the **host**, but auto only sees the **lane**. That gap is WP8.
- B157 window overlap is no longer a reason. Assay 11/12 attributes conservatively, and a 56-candidate jobs 1 vs 3
  pilot agreed except for suite flakes (`assay-jobs-audit-REPORT.md:17`).
- The remaining cost of overlap is efficiency, not correctness. A memory event in one candidate turns overlapping
  failing candidates into `crashed`, and those then go through the serial retry tail. Heavy lanes near their cap
  should therefore prefer more **instances** (each with its own cgroup leaf) over more **workers**.

**cmru feasibility.** 4640 candidates × about 600 s is about 773 h serially.
- With the fail-fast factor f (killed candidates' mean fraction of the suite), wall time is
  ≈ 0.82·f + 0.18 of today's:
  - f = 0.25 gives 0.385, about 298 h serially;
  - with 4 workers (2 hosts × 2) that is about 74 h, or 50 h at 3 per host if `--no-cov` frees the memory [I].
- **Decision rule after M1:**
  - if f ≤ 0.25, run the whole-source anchor once as a two-host sharded campaign;
  - otherwise start the rigor base chain unanchored ("incremental since cmru-v7.2.0", rigor memo §3.5) and run the
    anchor as background filler.
- Either way, stop the running cmru campaign once WP1 is decided. 60 candidates in 12 h are cheap to lose, and
  continuing without `-x` wastes about 3x [I].

---

## 4. The parallelism axes our tools must support

Each axis has a purpose, a rule for when to use it, and what each tool must provide. Axes compose: a campaign can
run with A0 + A1 + A4 at once.

### A0 Work reduction (prerequisite)

- **What:**
  - candidate-only fail-fast (`-x`/`--maxfail=1`) and `--no-cov`;
  - covering-tests-first ordering;
  - changed-lines bases.
- **Assay:**
  - `judge.mutation.candidate_args = [...]`, appended only to candidate runs, never to the R0/R1 baseline. It is
    part of the judge identity. The cold-witness check must treat assay-owned fail-fast as known, instead of
    refusing as it does for a user `-x` (review LOW-3).
  - Optional later: `order = "covering-first"`. It needs per-test coverage contexts from the baseline. A survivor
    must still pass the full suite, so the result stays sound.
- **Rigor:** the base chain (rigor memo §3.5) already gives changed-lines R2 per lane.

### A1 Candidate workers within one run (`--jobs N`)

- **Use when:** the tree and candidate fit in one container's memory and CPU; up to 3 workers (the coordinator
  knee).
- **Assay:**
  - `parallel_capable` (section 2.3);
  - **per-worker scratch:** `TMPDIR`, `HOME`, `XDG_CACHE_HOME`, `COVERAGE_FILE` set to a per-worker directory,
    optionally on a container tmpfs. Lanes pass `HOME` through today (ciu `assay.toml:47-52`), and every worker
    shares it. Assay sets none of these per candidate [F: no `HOME`/`TMPDIR` handling in `mutation.py` or
    `isolation.py`].
  - Keep the RSS/wall two-ended dispatch and `--memory-budget-bytes`.
- **Run-gate:**
  - auto takes **tier headroom** as an extra input:
    `min(lane cap, tier memory.high − Σ reservations of admitted lanes)`;
  - a WARNING on clamp;
  - `resources.tmpfs = "<size>"` (RG-163).

### A2 Test-level xdist inside a candidate

- **Use when:** only for R0/R1 baselines and for the last few candidates of a campaign, when free slots exceed
  remaining candidates. Never as the main R2 axis: it multiplies processes per candidate, adds coverage combining,
  and gains latency rather than throughput.
- **Assay:** later, an option to give the tail candidates `-n k` (part of the judge identity, so off by default).
  Not in the first wave.

### A3 Several container instances of one lane on one host

- **Use when:** a suite shares container-global state (fixed `/tmp` paths, `HOME`, one cgroup leaf) or a lane needs
  separate cgroup attribution.
- **Why separate instances:** each instance gets its own `/tmp`, its own cgroup leaf (no B157 cross-attribution)
  and its own assay process (no GIL coordinator).
- **Assay:** a **shard-scoped lock**. The state-directory lock becomes per `(shard index, count)`. Records are
  per-candidate files with atomic writes, and shards are disjoint, so N shards can share one state directory. Each
  shard also gets its own progress and verdict names.
- **Run-gate:**
  - `--shard i/N` passthrough;
  - shard-suffixed artifacts (`.assay/verdict-<lane>.shard-<i>-of-<N>.json`) and container names (remote-gate
    notes a name collision today);
  - `--fan-out N` to start N admitted instances.
- **Cowtree:** nothing new. Each instance has its own run area.

### A4 Shards across hosts

- **Use when:** plans too large for one host (cmru), or to drain a queue on two hosts.
- **Assay:**
  - `assay state import --from DIR...`: a verified union of record files. It refuses conflicting records for one
    candidate ID and any record whose identity check fails.
  - Then a **sealing pass**: `assay run <lane> --resume` as shard 0/1 (or unsharded). It re-runs R0/R1 once and
    replays every record, which gives one ordinary qualifying verdict. No new verdict schema is needed, and
    `assay verify` / `analyze export` are unchanged.
  - Add `analyze export --progress P...` (several shard progress files, each tagged with a host) so per-candidate
    performance comes from the real executions, not from the replay.
  - `merge_mutation_shards` stays the coverage proof inside import: all indexes of one count present, one
    lane/commit.
  - **Identity:** the judge identity must include the tester image ID. Run-gate exports `RUN_GATE_LANE_IMAGE_ID`,
    and lanes add it to `env_passthrough`, so B142 fingerprints it. Records from different images then become
    cache misses.
- **Run-gate:** the same as A3, plus an `evidence` command that collects state, progress and verdict by shard.
- **Remote-gate / host runners:** a sharded campaign driver (section 6).
- **Rigor:** nothing new. The fragment comes from the sealing pass. Optionally add an assay-owned provenance
  `execution: {shards, instances}` to the export, with host names anonymised as the rigor memo §6.3 requires.
  Performance summaries pool per-candidate subjects across hosts using the per-host Geekbench normalisation (rigor
  memo §4.4-4.6).

### A5 Suite parts within one lane (the "serial-only lane" idea, done inside assay)

- **Use when:** some tests cannot run concurrently (shared Docker daemon, real paths, heavy memory). The rest can.
- **Assay:** `[[lanes.X.parts]] name, argv, exclusive = false|true, environment_requires = []`.
  - **Baseline:** runs all parts, combines coverage (`COVERAGE_FILE` per part, then `coverage combine`), and R1
    judges the union.
  - **Candidate:** runs parts in order. Stop at the first failing part (killed). `survived` requires every part to
    pass. An exclusive part holds a lane-wide serial token while parallel parts keep flowing. Most kills happen in
    the parallel part first, so the exclusive part runs only for candidates that are still alive.
  - One verdict, one claim.
- **Why not separate lanes merged in the record:**
  - a mutant's bucket is a per-candidate OR across parts;
  - coverage is a union.

  Both are assay judgments. Merging them in rigor-record would either be unsound (two partial verdicts, each with
  survivors that the other part kills) or would rebuild assay inside rigor-record.
- **Markers (estate convention):**
  - `serial_only`: state shared across workers;
  - `real_docker`: needs the DinD environment (A6-T1);
  - `os_integration`: VM only, already exists.

  The gate deselects markers explicitly. A required-marker plugin turns a skip into a failure where the
  environment promises the capability (generalise `debian_install_v2/tests/os_integration_plugin.py`).

### A6 Environment tiers for real behaviour (section 5)

- **T0:** fakes in tester-unified (default; all axes apply).
- **T1:** real Docker through a DinD sidecar (container; R0/R1/R2 possible; exclusive part or one sidecar per
  instance).
- **T2:** QEMU/TCG VM (R0, optionally R1; **never R2**; exclusive host admission).

### Requirements matrix

| Need | Assay | Run-gate | Cowtree | Rigor record | Remote-gate / runners |
|---|---|---|---|---|---|
| A0 | `candidate_args` (fail-fast, no-cov) in the judge identity; later covering-first | none | none | none | none |
| A1 | `parallel_capable`; per-worker scratch env; tmpfs-worker without btrfs | auto uses assay's fact plus tier headroom; WARNING on clamp; `resources.tmpfs` | COW-BL-08 only | none | none |
| A3 | shard-scoped lock; shard-named progress/verdict | `--shard`, `--fan-out N`, shard-suffixed names, N reservations in admission | none | none | host scripts pass through `"$@"` already |
| A4 | `state import` (verified union); sealing pass; multi-progress export; image ID in identity | `RUN_GATE_LANE_IMAGE_ID`; evidence by shard | none | none (fragment from the sealing pass); optional provenance | sharded driver: bundle + image digest per host, shard runs, state collection, sealing on one host |
| A5 | `parts`, exclusive token, combined coverage, required-marker plugin | lane environment must satisfy each part's `environment_requires` | none | none | none |
| T1 | none beyond A5 | `[environments.X.docker] mode = "dind"` (digest-pinned, gates slice, own limits, exact names, registry mirror) | none | toolset lists the DinD image digest | host pull-through registry cache |
| T2 | none (command lane), or argv wrapper for R1 | `mode = "vm"` harness contract, exclusive admission, budget, evidence | none | a VM lane is R0 ("declared, not judged") unless assay judges it | nothing new (QEMU runs in the project's unprivileged runner image) |

---

## 5. Container vs VM: the standing decision, applied

### 5.1 The decision (restated from the repo)

- **Doctrine** (`mdt-cockpit/docs/CONTAINER-DOCTRINE.md:105-124`):
  - a privileged Docker container is **not** a safe boundary for loop and partition devices, `mkswap`/`swapon`,
    initramfs, reboot or other host-kernel state, because those are not namespaced;
  - such tests belong in a guest;
  - a privileged container is acceptable only for userland/systemd tests that do not create host-visible devices
    or change global kernel state;
  - the default runner is QEMU TCG with user-mode networking: no `/dev/kvm`, no TAP.
- **debian-install-v2** (`scripts/debian-install-v2/testing/README.md:10-29`): loop, swap, initramfs, boot,
  systemd-as-PID1 and real `/dev` tests are VM-only. The privileged systemd-container harness was removed as an
  invalid boundary. Gate containers deselect `os_integration`; the VM lane requires it, and a skip there is a
  failure.
- **tester-base** (`tester-base/docs/DESIGN-GUIDE.md:145-150`): real low-level OS binaries (tc,
  systemd-tmpfiles, sfdisk, losetup, nsenter) run in the owning project's QEMU VM lane. **Docker-driving tests
  (SQL qualification, tester-gate DinD, ciu enroll) stay in containers, because they need CPU the TCG VM cannot
  give.**
- **Operator, 2026-09** (memory `resume-2026-09-08-…:551-578`):
  - no nested virtualisation, so TCG;
  - nothing runs in the devcontainer or in tester-unified;
  - project-scoped disposable runners;
  - no generic shared "KVM test environment".

### 5.2 What the VM tier costs on our hosts

- **KVM:** not available on <host>, <host> or the controller [M]. Firecracker, Kata and Cloud Hypervisor all require
  KVM, so they are **excluded by fact** (`testing/vm/DESIGN.md:114-119`).
- **gVisor and Sysbox:** neither needs KVM, but neither is installed or measured anywhere in the estate. They are
  not part of this plan. Reopen only if the T1 DinD sidecar demonstrably cannot express a needed real behaviour
  (for example systemd-as-PID1, which fails even privileged on the controller daemon).
- **TCG throughput** is about 1/21-1/26 of native for CPU work, and boot to SSH is about 66 s [M].
  - Example: an R2 campaign of ciu size (215 candidates × 3-5 min) would take on the order of 200-450 h inside a
    VM [I].
  - Therefore VM lanes are R0 acceptance (optionally R1 for code reached only by `os_integration` tests) on small,
    targeted suites. Existing examples: debian-install-v2 `r1-vm-real-commit` (30 m budget) and mdt-cockpit
    `vm-system` (155 m budget, `mdt-cockpit/run-gate.toml:77-83`).
- **Memory:** the runner peaked at 3.09 GB with a 4 GiB guest [M]. That is most of the 4 GiB gates tier, so a VM
  lane must be admitted **exclusively** in the tier (WP8/WP11). Guests stay at 1-2 GiB.
- **Code reached only by real-kernel tests:** R2 is out of scope by declaration (a waiver in
  `rigor/waivers.toml`). The design rule is a thin OS adapter plus fake-tested logic, so that set stays small.

### 5.3 The problem lanes

| Lane | Why it was left at jobs 1 | What is actually shared (evidence) | Decision |
|---|---|---|---|
| ciu `ciu` (215 candidates, 2g, `-n 0 --maxfail=1`) | "shared docker/cgroup/name state, writes real paths" | (1) The lane runs in tester-unified, which mounts **no Docker socket** (`run-gate.root.toml:12-30`). Real-network tests and the CIU-87 teardown are no-ops there [F]. (2) `test-repo/` renders happen **inside the snapshot** (`ciu/tests/conftest.py:150-198`), so each candidate has its own copy. Its fcntl lock lives in `/tmp`, keyed by path digest, so it does not collide. (3) `HOME` is passed through and tests write `~/.config` and `~/.cache` (`ciu/assay.toml:47-52`): **shared across workers**. (4) Governance tests only *read* `CGROUP_PARENT_DEV_BACKGROUND`. (5) The real-paths incident came from interactive ciu runs on the real checkout, not from the suite | T0 parallel: A1 per-worker `HOME`/`TMPDIR` (WP4b), then M4 (two concurrent containers × 3), then `jobs_max = 3`, memory from M2. Real behaviour (enroll: users, sshd, Docker) already runs in the T1 DinD lane `[lanes.enroll]` through cmru tester-gate. Tests that need real Docker networks or nested cgroup governance move into a `real_docker` exclusive part once WP9/WP10 exist |
| mdt-cockpit `assay-full` / `host_setup` (one 4237-line wizard; `whole_target`) | memory > 4 GiB | Peak "just over 4.1 GiB" at jobs 1, in a 6g lane (`mdt-cockpit/run-gate.toml:67-75`). That exceeds the tier's 4 GiB `memory.high` on its own, so the lane throttles the whole tier even serially | M3 first: per-test-file RSS high-water (each file in its own process, `ru_maxrss`, `/usr/bin/time -v`). If 1-3 files dominate, they become an `exclusive` part (WP9) and the rest runs at jobs 2-3 under a 3g lane. If the cause is coverage on one huge module or a fixture leak, fix it in the suite. Real systemd, slices and IO governance stay in the existing `vm-system` T2 lane (R0) |
| modern-debian-tools-python-debug `host_setup` | same suite | A **diverged copy** of the wizard: sha256 `a6ca974b…` vs mdt-cockpit `e71e500e…` [M] | Do not invest in parallelising a duplicate. Either retire its R2 lane in favour of mdt-cockpit, or reuse mdt-cockpit's parts layout once M3 has picked the split. Owner decision |
| <private-host>-guide `r2` (cap 400, 20m) | "docker-touching tests" | Fakes: an RCON server on `<host-address>:0` (`tests/fake_rcon_server.py:50-52`), `tmp_path`, monkeypatched subprocess. The one real-Docker file (`tests/test_soulmask_monitor_docker.py`) **self-skips** without a daemon, so it never runs in the gate (`run-gate.toml:5-7`) | T0: `jobs_max = 3` after M2/M4 (likely immediately safe). Make the Docker file `real_docker` with skip = fail in a T1 lane: today it is a silently missing check |

### 5.4 Q4: fake Docker runners

- The dstdns finding (`docs/reports/dstdns-p4-REVIEW.md:13-17`): the smoke argv `python -m py_compile /app/x.py` run
  as `--user 65534` fails in a root-owned `/app`, and the unit test cannot see it because it uses a fake runner.
- **Which tier:** this is permission semantics of a real image under a real daemon, with no kernel state involved.
  It belongs in **T1, not T2**. A VM would add 20x CPU cost and an image-transfer problem for no extra fidelity.
- **Rule:**
  - every argv the product hands to `docker run` for a smoke, entrypoint, bind mount, user or `--read-only`
    belongs to a `real_docker` test that runs it once against the real built image, in a DinD sidecar;
  - keep the fakes for branch coverage;
  - add paired "contract" cases that run the same scenario against the fake and the DinD daemon, and assert both
    give the same outcome. That keeps the fake honest.
- **What DinD gives over the host daemon:** a private image store, private names and networks (no ciu-style
  network leaks into the shared daemon), its own nested cgroup subtree under private cgroupns (real cgroup v2
  delegation tests: child cgroups, `memory.max`, `memory.events`), and bounded limits. cmru already ships the
  pattern: digest-pinned privileged DinD, `--pull=never`, gates slice, `--init`, exact names
  (`cmru/README.md:485-531`, `cmru/docs/CONSUMERS.md:789-825`).
- **Hard rules** (memories `cgroupns-host-loop-device-host-hang-incident`, `host-zombies-git-autodetach-no-init`):
  - never `--cgroupns=host`, `--pid=host` or `--net=host`;
  - always `--init`.
- **Docker Hub limits:** DinD stores start empty. Preload the lane's images from the host daemon
  (`docker save | docker load`, digest-checked), and point DinD at a host-local pull-through registry cache.
  The estate already hit 429 (R2 board :42).
- **When the VM is right:** only when the subject is the daemon, BuildKit, kernel or init system itself. Examples:
  the mdt large-OCI transfer plan (`mdt-cockpit/docs/plan-run-gate-vm-lane.md`), slices and swap, systemd units.

---

## 6. Recommended architecture

```
controller (cockpit)                          runner host <host> / <host> (4 vCPU, gates tier 4 GiB high)
-------------------                           -------------------------------------------------------
remote-gate sharded campaign driver  ──ssh──▶  run-r2-campaign (supervisor, attempts, resume)
  - ships commit bundle + image digest           └─ run-gate <lane> --shard i/N [--fan-out k]
  - runs shard i on host h                             ├─ tier admission: Σ reservations ≤ memory.high,
  - collects state/progress per shard                  │   Σ cpu ≤ cores+1, PSI < 25 (WP8)
  - imports state on the sealing host                  ├─ lane container(s) (one cgroup leaf each)
  - runs the sealing pass (--resume 0/1)               │    └─ assay run --jobs auto(≤3) --shard i/N
                                                       │         materialize=auto (cow | tmpfs-worker)
rigor-record sync ◀── fragment from sealing pass       │         parts: [parallel] → [exclusive token]
                                                       │         per-worker TMPDIR/HOME on container tmpfs
                                                       ├─ T1: DinD sidecar per lane instance (real_docker)
                                                       └─ T2: VM lane (exclusive admission, R0/R1, never R2)
```

Principles:
- **Workers before instances before hosts.** Use A1 up to 3 workers. Use A3 only when the suite needs separate
  `/tmp` or cgroups. Use A4 when a plan exceeds about one host-day after A0.
- **One verdict per lane and commit.** Shards and parts are execution detail inside assay. Rigor-record never
  merges R2.
- **Admission is host-wide and resource-based.** It is not opt-in per project and not a count. Auto worker counts
  read the headroom left at start; PSI waits stay dynamic.
- **Facts have one owner.** Assay reports parallel capability, run-gate reports environment and tier facts, and
  cgpd measures.

---

## 7. Work packages (ordered)

| # | Package | Owner | Size | Depends | Deliverable | Gate / acceptance | Risk |
|---|---|---|---|---|---|---|---|
| WP1 | Candidate-only fail-fast + `--no-cov` (`judge.mutation.candidate_args`) | assay | S | — | key in the judge identity; the baseline is unchanged; witness logic aware; docs | assay gate. On a 56-candidate pilot (assay jobs-pilot tooling), buckets are identical with and without the key; wall time drops | a suite whose first failure depends on test order (shows as flaky kills; same as the review's `-x` analysis) |
| WP1b | Adopt WP1 in cmru, pwmcp, ciu, cgroup-profiler, mdt | cmru + project owners | S | WP1, M1 | config only; restart campaigns between attempts | each project's R2 lane `--dry-run` shows the args; first campaign hours match the M1 estimate | campaign restarts lose partial state (argv identity) |
| WP2 | B219 primed index, B220 closure of new objects only, B222 tiered verification | assay | S-M (branches `fix-assay-mat-20261105`, `backlog-materialize-20261105`) | — | per-candidate reads drop from 174 MB to about 1 MB; materialise 2.3 s to 0.3 s | assay gate plus the tests listed in `materialize-experiment-REPORT.md:104` | B222 tiers weaken the per-candidate proof; keep `verify_clone = "full"` available |
| WP3 | `parallel_capable` fact; run-gate auto uses it; WARNING on clamp; RG-163 `resources.tmpfs` | assay + run-gate | S | WP2 | section 2.3 items 1-4 | run-gate tests: clamp reason visible; explicit `--jobs 2` without capability still refuses; tmpfs mount argv | probe drift between assay and run-gate versions (version-floor the field) |
| WP4 | tmpfs-worker without btrfs (template on tmpfs), anomaly drop rule, `materialize = "auto"` | assay | M | WP3, M5 | section 2.3 item 5; section 2.4 | B223 test list plus: crashed or hung candidate means the tree is rebuilt; a no-btrfs host runs jobs 2 | in-place reuse residue (stat-walk blind spot) |
| WP4b | Per-worker scratch env (`TMPDIR`, `HOME`, `XDG_CACHE_HOME`, `COVERAGE_FILE`) | assay | S | — | `isolation.per_worker_env` (default on for jobs > 1) | a test showing two workers never see each other's `HOME` | suites that require the real `HOME` (declare an opt-out) |
| WP5 | Shard-scoped state lock; `assay state import` (verified union); `analyze export --progress P...` with host tags; documented "sharded execution, unsharded verdict" | assay | M | — | concurrent shards on one state directory; import refuses conflicts and tampering | M10 equality; sealing pass on imported state = all replays plus one baseline, qualifying verdict; `assay verify` passes | identity drift across hosts makes everything a cache miss (M10 decides) |
| WP6 | `run-gate --shard i/N`, shard-suffixed artifacts and container names, `RUN_GATE_LANE_IMAGE_ID` | run-gate | S | WP5 for concurrency | passthrough and naming | run-gate tests; `--dry-run` argv | none significant |
| WP7 | Sharded campaign driver: `remote-gate campaign <lane> --shards N --hosts <host>,<host>`, image by digest, state collect, import, seal | remote-gate (scripts/netcup/runner) | M | WP5, WP6 | one command fans out, collects and seals | live run of a small lane (`libraries/worktree`) at 2 shards on 2 hosts equals the unsharded buckets | host identity and PSI rules; <host> root disk 97 % full |
| WP8 | Tier-aware resource admission (reservations vs `dev-gates.slice` `memory.high`/cpu, PSI wait), on by default on runner hosts; auto reads the headroom; VM lanes exclusive | run-gate | M | — | extend `run-gate admission` from count to resources | tests plus a live two-lane contention run | over-reservation idles hosts (calibrate from history p95) |
| WP9 | Suite `parts` with an exclusive token and combined coverage; required-marker plugin (`serial_only`, `real_docker`, `os_integration`) | assay (+ small shared pytest plugin) | M-L | WP4b | one verdict over all parts | assay gate; a ciu or mdt pilot shows identical buckets to the unsplit lane | part ordering changes kill timing only, never buckets; prove it |
| WP10 | `[environments.X.docker] mode = "dind"` sidecar (port of cmru tester-gate DinD), image preload, registry mirror | run-gate (cmru later delegates) | M | WP8 | per-lane-instance private daemon | M8 (uid-65534 smoke caught); exact-name cleanup; `--init`; gates slice | privileged sidecar; keep it digest-pinned and never `--cgroupns=host` |
| WP11 | `mode = "vm"` harness contract (`prepare/start/wait/exec/collect/destroy`), run-gate owns budget, evidence, cleanup and exclusive admission; the QEMU harness stays per project | run-gate | M | WP8 | debian-install-v2 and mdt `vm-system` move onto the contract | existing VM lanes pass unchanged | TCG time budgets; keep it out of aggregates |
| WP12 | COW-BL-08: warn or refuse reflink-on-ZFS for tree clones | cowtree | S | — | detect() WARNING | cowtree tests | none |
| WP13 | Lane retuning: cgroup-profiler 3, <private-host> 3, ciu 3 (after M2/M4); mdt split (after M3); cli-extended `kind = "assay"` between campaigns | project owners | S each | M2-M4, WP4b, WP9 | config | each lane's R2 dry-run plus first campaign | none beyond M results |

---

## 8. Measure first (cheap, read-mostly; <host>/<host> only, PSI < 25, one gate container at a time)

| # | Measurement | How | Cost | Decides |
|---|---|---|---|---|
| M1 | cmru time-to-first-failure | One baseline of the cmru R2 argv with `--junitxml` in tester-unified (2 GiB, gates slice). Then compute, for each of the 49 killed candidates, the cumulative time up to its recorded `first_failing_test` (progress already has it). Gives f | about 10-15 min of one core | cmru whole-source feasibility; the size of the WP1 win |
| M2 | Per-candidate RSS for cgroup-profiler, ciu and <private-host> | `/usr/bin/time -v` of one baseline per lane in its lane container; or `memory.peak` of the container | about 5 min per lane | `jobs_max` / `candidate_ram` |
| M3 | mdt host_setup per-file RSS | Each test file in its own `python -m pytest <file>` with `--cov`, record `ru_maxrss` | about 20 min | the WP9 split or a suite fix |
| M4 | Concurrency safety | Two lane containers of the same commit (separate checkouts or snapshots), run concurrently, 3 repetitions. Diff `HOME`/`/tmp` listings before and after | about 30 min per lane | `jobs_max` > 1 for ciu and <private-host> |
| M5 | tmpfs-worker A/B | The prepared OFF/ON arms (`docs/reports/assay-tmpfs-CONTINUATION.md:13-17`) with a bounded candidate subset. Compare `phase_seconds`, cgroup `io.stat`, container `memory.current`/`memory.stat shmem`, and zswap counters | about 1 h | whether WP4 matters beyond hosts without btrfs |
| M6 | Real tier headroom | cgpd or a 1 s sampler for 1 h on each host: `dev-gates.slice` `memory.current`, `memory.events high`, `memory.pressure`, `/proc/meminfo Zswap*`, CPU PSI under current campaigns | passive | WP8 reservations; how many workers really fit |
| M7 | Instances vs workers | After WP6: `libraries/worktree` or cli-extended, jobs 4 in one container vs 2 containers × jobs 2 | about 1 h | whether A3 is worth using for throughput or only for isolation |
| M8 | DinD on a runner host | Start the cmru tester-gate DinD sidecar on <host> (exact name, gates slice). Measure start time and idle RSS, preload one image, run the dstdns `docker-stats-exporter` smoke as uid 65534 | about 20 min | WP10 cost; proof that this bug class is caught |
| M9 | Current VM lane cost | Run debian-install-v2 `r1-vm-real-commit` once when quiet; record wall time and runner peak memory | about 30 min | WP11 admission sizing |
| M10 | Cross-host record identity | Same commit and image ID on both hosts, `--candidates-file` with 2 IDs; compare state records' `judge_sha256` | about 15 min | whether WP5's import works without further identity changes |

Housekeeping before measuring:
- remove `/dev/shm/assay-tmpfs-exp` on both hosts when the B223 experiment ends;
- free space on <host> `/` (97 %).

---

## 9. Risks and open points

- **Fail-fast changes the R2 argv identity:** every running campaign restarts. Schedule this between campaigns;
  cmru's 12 h of progress is the only material loss.
- **Shards across images or hosts:** without the image ID in the judge identity, records from different tester
  images could combine silently. WP6 and WP5 close this; M10 verifies it.
- **Tier saturation:** auto worker counts are per lane today, so three campaigns × 2 workers already exceed the
  operator's cores + 1 on a 4 vCPU host. Until WP8 lands, the controller's R2 board must do this accounting by hand.
- **tmpfs in-place reuse** stays opt-in until the anomaly rule exists and M5 shows a gain.
- **The VM tier** is slow and memory-heavy. Its scope is acceptance only. Code that only VM tests reach is excluded
  from R2 by explicit waiver, not silently.
- **The mdt >4 GiB baseline** violates the tier even at jobs 1. Treat it as a defect to diagnose (M3), not as a
  parallelism setting.
- **Unknowns [U]:** cmru's f; RSS without coverage; DinD overhead on these hosts; whether `judge_sha256` already
  matches across hosts.

## 10. Sources

`RP/reports/assay-jobs-audit-REPORT.md`, `docs/reports/assay-jobs-audit-REVIEW.md`, `materialize-experiment-REPORT.md`,
`zfs-eval-REPORT.md`, `docs/reports/assay-tmpfs-CONTINUATION.md`, `dstdns-parallelism-REPORT.md`, `remote-gate-REPORT.md`,
`docs/reports/dstdns-p4-REVIEW.md`, `RIGOR-EVIDENCE-DESIGN-MEMO-2026-11-05.md` (§0, §3.2, §3.5, §3.7), `docs/reports/AGENT-RULES.md`;
`RP/<host>/R2-BOARD-2026-11-05.md`.

Code and docs: `run-gate-project/run_gate.py:4600-4680,11200-11360`, `run-gate-project/SPEC.md` (R-07 :289,
R-08 :319, R-52 :755-800, R-38 :1694-1780), `run-gate.root.toml:12-30`, `assay/README.md:95-102,348-361,1310-1315`,
`assay/docs/DESIGN-GUIDE.md:1639-1674`, `assay/docs/CONSUMERS.md:1588-1826`, `assay/src/assay/cli.py:869-1027`,
`assay/src/assay/mutation.py:3019,3639-3655`, `assay/src/assay/runner.py:6500-6515`,
`.worktrees/assay-tmpfs-20261105/assay/src/assay/{_tmpfs_worker.py,isolation.py:600-1000}`,
`mdt-cockpit/docs/CONTAINER-DOCTRINE.md:105-124`, `mdt-cockpit/docs/plan-run-gate-vm-lane.md`,
`tester-base/docs/DESIGN-GUIDE.md:130-150`, `scripts/debian-install-v2/testing/README.md`,
`scripts/debian-install-v2/testing/vm/DESIGN.md:100-150,395-420`, `.../USAGE-TESTING.md:490-534`,
`scripts/debian-install-v2/run-gate.toml:117-126`, `cmru/README.md:485-531`, `cmru/docs/CONSUMERS.md:789-825`.

Worktree `assay-jobs-audit-20261105`: `{ciu,mdt-cockpit,modern-debian-tools-python-debug,cmru}/assay.toml`,
`mdt-cockpit/run-gate.toml:58-83`, `scripts/{cgroup-profiler,<private-host>-guide}/{assay,run-gate}.toml`,
`ciu/tests/conftest.py:150-330`, `scripts/<private-host>-guide/tests/*`.

Memory: `resume-2026-09-08-netcup-debian-install-v2-livetest.md:540-580,994-1010`,
`ciu-scratch-copy-writes-real-paths.md`.

Host probes: read-only ssh as `runner` after `host_api_check` / `host_api_check` (both OK), 2026-11-04 ~19:37Z.
