<!-- origin: 2026-10-30; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: COW-PLAN-OPUS-2026-10-30.md -->
<!-- relation: CoW workspace plan rev 3.1 -->

# Copy-on-write workspaces, parallel and remote R2, relaxed fsync: plan (rev 3.1)

Author: independent design pass (Opus, read-only), 2026-10-30. Code read at `main` @ `646d445ed` (assay 10.0.1 is tagged).

Inputs:
- `LANES-AUDIT-2026-10-29.md` Part B
- `ASSAY-DESIGN-OPUS-2026-10-30.md` (assay-WP0..WP8)
- `mdt-cockpit/docs/BTRFS-COW.md`
- `BTRFS-COW-OPTIONS.md` §9 (A1-A28)
- `run-gate-project/REMOTE-LANES-BUILDKITE.md`
- CIU v8 design notes
- operator feedback rounds 1-2 (2026-10-30)

Tags:
- **[M]** measured in this session on this host
- **[R]** verified on r1002 earlier
- **[K]** known behaviour, not re-verified
- **(guess)** unverified

Package names: CW* (CoW), PR* (parallel R2), RS* (remote shards), IO* (fsync), RT* (resources table). Assay redesign packages keep their names (assay-WP*).

**Rev 3 changes:**
- One `resources` settings table (§5) replaces `bind_env` and `[io_sync]`.
- Durability is marked per lane.
- Worker count is redesigned around average RAM per candidate and a caller RAM target (§4.8.4).
- Boundaries between mdt-cockpit, cgprofile, run-gate and assay are made explicit.
- New remote-shard seam (§4.9).
- assay 11.0.0 goes out under an extended B169 waiver.
- Deviations re-marked in §1b.

The plan stays improvable. Every decision names the alternative it rejected.

---

## 0. Facts [M] (unchanged from rev 2, plus three)

| Fact | Source |
|---|---|
| Native R2: **materialize 1-21 % of candidate wall (typically 2-6 %), median 1.2-4.2 s; command 70-98 %** (4,974 attempt records, 21 stores). | `phase_seconds` (`assay/src/assay/mutation.py:4587-4592`) under `/workspaces/vbpub/.run-gate/assay-state/**` |
| **Per-candidate peak RSS** p50/p90/max (MiB): ciu 153-180/253-363/376; cgprofile 284/462-473/504; nyxloom 169-415/178-458/474; run-gate 511/536/1955; worktree 69/129/130. **Per-candidate CPU 0.7-1.2 cores**; wall 6-180 s. | same records (`peak_rss_bytes`, `cpu_seconds`) |
| Whole-lane serial R2 containers: peak 372-2055 MiB, **0.49-1.19 cores avg**. | `footprint` lines, `<controller-program-dir>/logs/r2-*.log` |
| One vbpub P22 unit writes **37 MB seed + 159 MB snapshot**. 18 orphaned `assay-p23-*` dirs of ~190 MB each exist. | `du`/`ls` `.run-gate/` |
| `/workspaces/vbpub` and `/tmp` are ext4 on the root LV; there is no CoW area. No libeatmydata `.deb` is tracked. `tcg_vm.py` is untracked; vmctl uses qcow2 overlays with `cache=writeback` (`scripts/debian-install-v2/testing/vm/vmctl:273,304`). | df/git |
| **Serial R2 is structural** (process-tree subreaper per assay process with a global window lock, `process_tree.py:1-10,41`; cgroup limit counters per cgroup with a **read-only cgroup mount required**, `resource_limits.py:1-15`; verdict v16 refuses `jobs != 1`, `verdict.py:398-401,3224-3230`). | code |
| Sharding exists (`--shard INDEX/COUNT`, blake2b assignment `mutation.py:2735-2760`, manifest validator `merge_mutation_shards` `:2774`). **Any `--resume`/`--shard`/`--state-dir` run holds an exclusive non-blocking lock on its state store** (`cli.py:2180-2225`, `929`). There is no state-merge or verdict-merge CLI. | code |
| `judge_sha256` folds the passthrough **value fingerprints** (so per-worktree paths cause a cache miss), names only for infrastructure facts, and **not the per-candidate budget** (`mutation.py:1420-1500`). | code |
| Assay's child environment **replaces** the judge's (`runner.py:392-405`), so a container `LD_PRELOAD` reaches the judge but not the tests. | code |
| run-gate config: top-level keys `schema_version, environments, lanes, history, profile, footprint, project, assay, admission` (`run_gate.py:1627-1628`). **`resources` already exists** at lane scope (`memory, memory_max, memory_swap, cpu_weight, io_weight, shared, cpus, pids`, `:1236-1262`) and environment scope (`cpus`, `:1042-1056`). `[footprint]`/`[history]` resolve project, then central (`:1394-1411`). | code |
| Remote lanes: Buildkite is the queue (D-110.4). A pipeline generator and a trigger/collector exist, untested live (`REMOTE-LANES-BUILDKITE.md` §6). ciu v8 never drives hosts by SSH for jobs (§1 there). | docs |

---

## 1. Decision summary

### 1a. Decisions

1. **Library.** New stdlib-only `libraries/cowtree`, embedded in consumer wheels like `libraries/worktree`.
   - Backends: `btrfs`, `reflink` (XFS, ZFS ≥ 2.2 block cloning, bcachefs), `copy`.
   - One API: `open_run(tool)` always returns a `Run`; plain temp directory when the area is absent.
2. **Absent area.** Today's code path plus one INFO line. Set-but-broken → refused before allocation. Space is sized from the input (`SpaceEstimate`) and refused, never fallen back, when the estimate does not fit.
3. **One settings table: `resources`** (the table that already exists), at four scopes: central → environment → project → lane, plus restricted CLI overrides (§5).
   - **Rev 3.1:** keys sit by consumer. The top level of `resources` holds run-gate's own keys: the existing docker caps plus new `io_sync`, `ram_target`, `candidate_ram`. Consumer subtables hold everything else: `[resources.cowtree] mode`, `[resources.vm] disk_cache`.
   - run-gate forwards a subtable only into that lane's process, as `<CONSUMER>_<KEY>` environment variables. No consumer reads another consumer's keys.
   - run-gate validates it and **translates** it into binds, environment facts and argv.
   - Assay, cowtree and vmctl never read the table.
4. **Durability is per lane:** `resources.io_sync = "durable"`. A durability lane's tests verify fsync, crash consistency or storage behaviour (iobench, SQLite/Postgres durability, anything asserting data survives power loss). Under libeatmydata they would pass or fail for the wrong reason, so such a lane can never be relaxed by any scope or override.
5. **fsync relaxation: run-gate owns it end to end.**
   - Mechanism: a read-only `/etc/ld.so.preload` bind naming the image-labelled libeatmydata.
   - Assay hardens its own critical fsyncs with a raw syscall.
   - VM disks: vmctl uses `cache=unsafe` for per-run disks by default (Q-F accepted), driven by `[resources.vm] disk_cache`.
6. **CoW uses:**
   - assay P22 Level A (packs) and Level B (sealed, never-executed template + per-unit clone);
   - cmru fixtures;
   - VM images and runs on compressed trees.
   - Rollback = discard a clone. R2 state is never cloned.
7. **Parallel R2 (Q-A accepted):** N worker processes in one container and one cgroup.
   - A per-candidate "window host" process gives exact process-tree evidence.
   - Limit events are attributed conservatively to overlapping windows; no per-candidate sub-cgroups (Q-D).
   - Verdict v17 with assay-WP8; requires assay-WP1, WP4, WP7.
   - **assay 11.0.0 ships under an extended B169 waiver (Q-B).**
8. **Worker count (run-gate):** `N = min(floor(ram_target / avg_candidate_ram), floor(parent cpu limit in cores), jobs_max, candidates remaining)`.
   - Average RAM: run-gate history (from assay's per-candidate measurements) > committed footprint > declared `candidate_ram` > none (N=1).
   - Memory overcommit is accepted: the parent `memory.max` protects the host. Transient overshoot becomes conservative `crashed`, re-run by WP7.
9. **Boundaries (Q-E):**
   - mdt-cockpit provisions caps (derives the gates CPU cap per host).
   - cgprofile observes and verifies (effective limits, collateral), and is **not** the source of per-candidate averages.
   - run-gate decides (history, policy, admission, container shape).
   - assay executes `--jobs N`.
10. **Remote shards (§4.9).** Build now, host-agnostic in assay:
    - a pinned **campaign manifest** (identity + budget);
    - per-shard state;
    - `assay state merge`;
    - a merged verdict through a local replay (v16-compatible).

    run-gate passes `--shard`/`--campaign` through. Defer: Buildkite shard fan-out and collect, ciu v8 placement, signed results.

### 1b. Deviations

Rows marked **R3** are new in this revision.

| # | Requested | This plan | Reason / rejected |
|---|---|---|---|
| D1 | native ZFS backend | ZFS via reflink; native deferred | operator accepted (Q5) |
| D2 | plain copy is the correctness path | fallback = today's code per consumer | a copy of a base is no cheaper than a fresh write (`nyxloom/reference/TESTING-METHODOLOGY.md:472-477`) |
| D3 | rollback of mutated trees | clone-and-discard only | A4/A9/A10, B4 |
| D4 | R2 state on CoW | stays durable, never cloned | resume evidence, A9 |
| D5 | pipelining / parallel | parallel in one container; pipelining conditional | measured ~1 core per candidate. Rejected: sub-cgroups (read-only cgroup rule, Q-D); sharded containers as primary (kept as the remote seam, §4.9) |
| D6 | warm venv base | deferred | image + `link_paths`; A5 |
| D7 | mdt owns the manifest schema | cowtree owns it | single reader/writer |
| D8 | `<repo>/assay` layout | `.cowtree/runs/<nonce>` | A2/A8 |
| D9 | assay `snapshot_backend` key | auto-detect; `[resources.cowtree] mode` → `COWTREE_MODE` | requirement (a) |
| D10 | run-gate wraps the lane command | run-gate binds `/etc/ld.so.preload` | the wrapper hits the judge, and assay's environment replacement drops `LD_PRELOAD` before the tests (`runner.py:392-405`) |
| D11 | assay critical writes never under the shim | assay's critical fsyncs use a raw syscall | under `ld.so.preload` every dynamic process loads the shim. Immunity is simpler than exclusion |
| D12 | VM compressed and fsync-free | adopted; per-run disks `cache=unsafe`, bases durable; B5 picks the algorithm | Q4 / Q-F |
| D13 | btrfs-progs | raw ioctls | no image dependency |
| D14 | refuse or fall back on space | refuse | plain mode needs more space and writes more |
| D15 **R3** | `[io_sync] relaxed = "forbidden"` (rev 2) / generic `bind_env` (rev 1-2) | **both withdrawn**; one `resources` table with run-gate-owned `io_sync ∈ {durable, strict, relaxed}` and a `[resources.cowtree] mode` subtable (see D19) | a table named for one value is wrong (operator); `durable` folds the guard into the knob it guards (DRY); the cowtree `mode` replaces the generic bind with the one bind actually needed. Rejected: a new `[storage]`/`[io]` table (a second home for knobs `resources` already holds) |
| D16 **R3** | worker policy with IO bound and RSS p90 × 1.25 (rev 2) | **operator formula**: average RAM, caller RAM target, CPU-limit cap; no IO term | memory overcommit with parent `memory.max` protection; CPU is capped for free by cgroups. IO is capped by the slice's `io.max` and mostly removed by CoW (a slowdown only, which the budget absorbs). Rejected: peak-based sizing (it idles RAM) |
| D17 **R3** | "cgprofile should provide free-resource and per-lane footprint knowledge?" | **Split.** cgprofile = effective-limit and collateral **verification**. run-gate = per-lane history and policy. Per-candidate averages come from **assay's per-tree sampler** | cgprofile observes cgroups; with N candidates in one cgroup it cannot separate candidates. run-gate already stores footprints per lane (`run_gate.py:4595-4860`). Making admission depend on a daemon couples availability. Rejected: cgprofile as the admission data source |
| D19 **R3.1** | operator: one table per consumer (`[settings.assay]`, `[settings.cowtree]`, `[settings.vm]`) or consumer-prefixed keys | **Consumer subtables inside the existing `resources` table** (`[resources.cowtree]`, `[resources.vm]`). The top level of `resources` is run-gate's own. No `assay`/`cgprofile` subtable in v1: they have no forwarded key. Assay receives only derived argv (`--jobs`) | Subtables map 1:1 onto the forwarding unit, and existing un-prefixed keys (`cpus`, `memory`) stay valid without a migration (Q-J). Rejected: prefixed flat keys (they repeat the scope in every key and would either rename `cpus`/`memory` or mix two conventions). Rejected: a new `[settings]` root next to `resources` (two homes for resource knobs). `COWTREE_DISABLE` is renamed `COWTREE_MODE` so the forwarded variable follows the generic `<CONSUMER>_<KEY>` rule |
| D18 **R3** | outsource subsets to other hosts | assay gets host-agnostic **campaign + shard + state merge** now; placement (Buildkite queue or ciu v8) deferred | assay must not know hosts; the Buildkite seams are not yet live (`REMOTE-LANES-BUILDKITE.md` §6). Rejected: a direct verdict merge without replay now (needs v17; replay needs nothing new) |

---

## 2. Boundary table

| Component | Knows | Does NOT know | Owns |
|---|---|---|---|
| **mdt-cockpit** | host cores, game-server reservation, LV/dataset, mounts, per-user dir | tools, lanes, candidates | provisioning; **gates-slice caps derived per host** (`cpu.max` = host cores − game reservation; `memory.max`; `io.max`); CoW area contract; devcontainer lines + A14 guard |
| **cgprofile** | live cgroup series, effective limits (ancestor walk), collateral on neighbours | lanes, candidates, policy | **verification**: `run-gate doctor` asks it (when the daemon is present) that the gates slice's own caps equal its effective caps (no tighter ancestor), and reports collateral during benchmarks. Never an admission input |
| **cowtree** | `MDT_COW_ROOT`, `COWTREE_MODE`, ioctls, layout, leases | Git, assay, VMs, Docker, settings | detection, `Run` API, manifest, gc, space admission from consumer estimates |
| **run-gate** | the `resources` table at all scopes; gates-slice `cpu.max`/`memory.max` (its own placement target); per-lane history incl. per-candidate RAM average; image labels | filesystems, assay internals beyond `--jobs/--shard/--campaign` | validation and translation of `resources`; same-path CoW bind; io_sync delivery and durability guard; **worker count**; container shape; shard pass-through and evidence |
| **tester-unified image** | packages, uid 1003 | settings | `libeatmydata1` + `LABEL org.vbpub.tester.eatmydata-lib`; `COPY libraries/cowtree/` |
| **assay** | `WorkspaceProvider`; `--jobs N` ≤ `jobs_max`; `--shard`, `--campaign`; its durable-write helper | hosts, host load, settings, io_sync | identity proofs; template; window hosts; overlap attribution; campaign identity; state and verdict merge rules |
| **cmru** | `Run.ingest_tree/clone_tree` | backends | fixtures |
| **vmctl** | `VM_DISK_CACHE` fact (default `unsafe`), `cowtree detect/make-shared` | settings table, cowtree internals | images/runs placement, cache modes, disk lifecycle, A4 scan |
| **Buildkite / ciu v8** (later) | queues, enrolled hosts | assay internals | remote placement, artifact transport, provenance record |
| **ciu / worktree** (v7) | nothing new | everything here | — |

---

## 3. Adapter interface spec (`libraries/cowtree`)

Unchanged from rev 2; summarised:

- **API:** `detect()`, `open_run(tool, *, estimate: SpaceEstimate, …) -> Run`, and `Run.{ingest_tree, ingest_file, make_tree(compression), clone_tree, clone_file, seal, delete_tree, reserve, close}`.
- **Errors:** `RootInvalid`, `SpaceRefused(needed, available, estimate)`, `PathRefused`, `BackendFailed`, `LeaseHeld`.
- **Semantics per backend** (rev 2 §3.3):
  - btrfs: `SNAP_CREATE_V2` for a subvolume root, else an FICLONE walk;
  - FICLONE on reflink filesystems;
  - `copytree` in plain mode;
  - never a silent copy on `EXDEV`/`EOPNOTSUPP` except the explicit cross-filesystem `ingest_*`.
- **Ioctls** (verify in CW3):

  | Ioctl | Value |
  |---|---|
  | `FICLONE` | `0x40049409` |
  | `SUBVOL_CREATE` | `0x5000940E` |
  | `SNAP_DESTROY` | `0x5000940F` |
  | `SNAP_CREATE_V2` | `0x50009417` |
  | `SUBVOL_GETFLAGS` | `0x80089419` |
  | `SUBVOL_SETFLAGS` | `0x4008941A` |

  Parent fd plus one name, so no path traversal.
- **Detection:**
  - `COWTREE_MODE=off` or `MDT_COW_ROOT` unset → off, one INFO line. `COWTREE_MODE` is `auto` (default) or `off`; any other value → `RootInvalid`.
  - Validation: absolute, canonical, no symlink, exists (never created), owner == euid != 0, not o+w, mountinfo fstype not overlay/tmpfs.
  - Probe: btrfs → reflink → `RootInvalid`.
- **Layout:** `shared/<name>` (long-lived) and `.cowtree/runs/<tool>-<utc>-<16hex>/{owner.json, owner.lock, trees}`. Plain mode uses the same manifest and lease in TMPDIR.
- **gc:** deletes only lease-free `flock` runs with a valid manifest; no eviction.
- **Space (Q1):** `needed = ingest + max_live_clones × per_clone` (cheap clone) or `(1 + max_live) × max(ingest, per_clone)` (copy), plus a pending-delete allowance; compared against `statvfs` available.

  | Consumer | ingest | per-clone | live clones |
  |---|---|---|---|
  | assay | manifest blob sizes + pack | history p90 write bytes, else the full tree | jobs + spares + 1 |
  | cmru | template | template | 1 |
  | vmctl | base allocated bytes | base allocated bytes or `--disk-budget` | runs |
- **Identity hazards H1-H6:**
  - compare `(st_dev, st_ino)` pairs only;
  - sealed never-executed templates (stale bytecode);
  - zeroed index stat for replaced paths;
  - links planted per clone;
  - nothing persists an inode;
  - stage inside the destination tree (EXDEV).

---

## 4. Per-tool integration

### 4.1 run-gate CoW binding (now via `[resources.cowtree] mode`, CW4)

- `[resources.cowtree] mode = "auto" | "off"` (§5), forwarded as `COWTREE_MODE`.
- For an ephemeral container lane with effective `auto` and `MDT_COW_ROOT` set (absolute, normalized):
  - add `--mount type=bind,source=$MDT_COW_ROOT,target=$MDT_COW_ROOT` and `-e MDT_COW_ROOT=…`;
  - never `physical_path()` (`run_gate.py:4121-4155`; wrong for a btrfs subdirectory bind);
  - never a local stat (`AGENTS.md:146-150`).
- `off` → `-e COWTREE_MODE=off` and no bind.
- `host` mode: the variable reaches the process directly; `off` sets `COWTREE_MODE=off`.
- Logged with the forwarded-env line (`:7004-7016`).
- Backlog finding: `RUN_GATE_EXTRA_MOUNTS` uses `-v` (`:7459-7476`).

### 4.2 assay workspace seam, Level A/B (CW8, CW9), spare clones (CW12)

Unchanged from rev 2 §4.2:
- **Protocol:** `src/assay/workspace.py`; only `_cow_workspace.py` imports cowtree, lazily. `CowError` → `ERROR/EXEC_FAILED` with `Claim.detail`.
- **Hooks:**
  - `runner.py:378-385,6065,6266-6276,6493,6635,7207` (workspace factory);
  - `isolation.py:2009` (`prepare_snapshot(..., workspace=)`);
  - `:1202-1214` (`_copy_objects` → Level A);
  - `:815-916` (extract `_apply_replacement`);
  - `_ensure_template`;
  - `:745-780` (`cheap_clone` path → Level B; else today's code byte for byte);
  - close paths;
  - purity allowlist `gate/tests/test_dependency_purity.py:38`;
  - embedding in `assay/pyproject.toml:51-54`.
- **Release:** assay **10.2.0**, B169 window `<10.3.0` (operator decision).

### 4.3 cmru fixtures (CW7)

Unchanged:
- remove the duplicate `_copy_frozen_fixture` (`cmru/tools/mutation_campaign.py:717-723` shadowed by `:761-767`);
- `ingest_tree` once, `seal`, `clone_tree` per candidate.

### 4.4 VM lane (CW10)

- Placement: `shared/vm-images`, `shared/vm-runs`, both compressed (B5 picks the algorithm and level); `cp --reflink=always` for staging.
- vmctl reads `VM_DISK_CACHE` (forwarded by run-gate from `[resources.vm] disk_cache`). **Default when absent: `unsafe`** for per-run overlays (`-drive …,cache=unsafe,discard=unmap,detect-zeroes=unmap`; today `vmctl:304` uses `cache=writeback`).
- `prepare-base`/flatten/`snapshot` always use `writeback` + `sync -f` (bases are retained).
- vmctl owns deletion with the A4 holder scan.
- Risk: btrfs NOCOMPRESS heuristic; B5 checks `compsize` over a whole run.

### 4.5 tester images (CW5, IO1)

- `libeatmydata1`.
- `LABEL org.vbpub.tester.eatmydata-lib=<abs path>`: path from a build ARG computed by `build-image.sh`, verified by `RUN test -r`.
- No `/etc/ld.so.preload` in the image.
- `COPY libraries/cowtree/` beside `tester-unified/Dockerfile:315`.

### 4.6 mdt-cockpit (CW6, RT0)

- **CoW area contract doc** (rev 2 §4.6): ZFS/XFS notes; templates + A14 guard; selfcheck `--cowtree`.
- **Gates CPU cap per host (Q-E):** host-setup computes `cpu.max` of the gates slice as `(online cores − game-server reservation − interactive reservation) × period`. Reservations come from the host's memory note / `ciu.host.toml`-style host facts. Same for `memory.max`, so there is room for overcommit while protecting production.
  - Written as slice drop-ins. Replaces the fixed `cpus = "3"` (`run-gate.root.toml:29-30`), which becomes a per-lane override only.

### 4.7 ciu / worktree: what NOT to do

Unchanged from rev 2:
- no CoW worktrees;
- no symlinked stores;
- no cowtree import;
- no `.git` copies;
- no `defragment -c`;
- no eviction;
- no state, evidence or secrets under `.cowtree/`.

### 4.8 Parallel R2 in one container

#### 4.8.1 Per-candidate evidence and what breaks when N candidates share one cgroup

| Evidence | Shared cgroup with N candidates | Under the new classifier |
|---|---|---|
| Process tree (assay process = subreaper; `waitid(P_ALL)`; global window lock) | broken inside one process | fixed by **one window host process per candidate** |
| Limit events (`pids.events`, `memory.events` incl. `oom_kill`, own cgroup + ancestors) | ambiguous across overlapping windows; the OOM killer may pick a neighbour | conservative: every overlapping FAIL → `crashed`; PASS stays `survived`; a SIGKILL'd neighbour → `crashed`. No false kill or survive; WP7 re-runs serially |
| PSI / cpu.stat (liveness pause) | raised by our own workers | only delays `hung` |
| CPU growth, cpu_seconds, peak_rss (per-tree `/proc` walk) | unaffected | — |
| Per-candidate budget (`mutation.py:3256`) | slowdown if N exceeds capacity | `budget_exceeded` (incomplete); the CPU cap in the formula prevents CPU slowdown |
| Read-only cgroup integrity (`resource_limits.py:5-11`) | kept | the reason there are no sub-cgroups (Q-D) |
| v16 `jobs == 1` | refuses | v17 |

#### 4.8.2 Assay changes (PR1; assay 11.0.0, verdict v17)

1. **Window host:** `src/assay/_window_host.py` (`python -I -m assay._window_host`; JSON on stdin; result on fd 3). It reuses `ProcessTreeWindow` and the liveness sampler unchanged and is used for `jobs = 1` as well.
2. **Scheduler:** `_execute_mutation_jobs` (`mutation.py:4175-5100`) keeps its bounded queue (`:5045-5054`) and index-ordered events (`:1037`). Each thread drives one window host.
3. **`jobs > 1` requires `workspace.cheap_clone`** (else `BAD_LANE_CONFIG`); `workspace.reserve(max_live_clones=jobs+spares+1)`.
4. **`LimitLedger`:** coordinator-side counter snapshots at every window boundary. `limits_for(window)` = all deltas between its start and end sequence numbers, fed to `termination.classify` (assay-WP1).
5. **State:** only the coordinator writes, so the store lock is unchanged. Test: records resume across `jobs` values (`jobs` is not in `judge_sha256`).
6. **Config and CLI:** `judge.mutation.jobs_max` (alias `jobs`); `assay run --jobs N`, `1 ≤ N ≤ jobs_max`. Assay never derives N.
7. **v17:** `judgment.r2.jobs ≥ 1`, `judgment.r2.attribution ∈ {serial, overlap-conservative}`; plus the shard fields of §4.9.
8. **Prerequisite:** assay-WP7 (serial tail retry).
9. **Release (Q-B):** **assay 11.0.0 under an extended B169 waiver** (`assay/tools/self-qualification-gate.sh:816` window `<11.1.0`, B169 text updated). A real parallel R2 qualification is not required.

#### 4.8.3 Measurements needed (PR0, PR2)

- **Already measured [M]:** per-candidate RSS and cores.
- **Add (PR0):**
  - per-candidate `write_bytes` (`/proc/<pid>/io` summed in the tree sampler);
  - per-candidate peak RSS in the progress `candidate.resources` event (additive);
  - judge-only RSS.
- **Scaling (PR2, r1002 or new devcontainer):** cgprofile R2 subset at N = 1, 2, 4, 8, 16:
  - throughput;
  - `crashed`/`budget_exceeded` rates;
  - slice `memory.peak`;
  - bucket identity vs serial on judgeable candidates.

#### 4.8.4 Worker-count policy (PR3, run-gate)

```
avg_ram   = mean peak RSS per candidate for this lane           (source precedence below)
ram_target= effective resources.ram_target (caller-defined)     (§5; default "75%" of gates-slice memory.max (guess))
cpu_cap   = floor(gates-slice cpu.max quota / period)           (mdt derives it per host; "max" => refuse auto, N=1, INFO)
N = max(1, min(floor(ram_target / avg_ram), cpu_cap, jobs_max (assay.toml), candidates_remaining))
```

- **Candidates remaining:** run-gate obtains this from `assay plan --json` (the plan count minus resumed records). For a shard it is the shard's count.
- **No container `memory.max` beyond the lane's own explicit `resources.memory`.** Overcommit is intended: the gates slice `memory.max` protects the host (mdt).
- **Container `--cpus` = N**, never above `cpu_cap`; more workers than cores only waste RAM.

**Where `avg_ram` comes from, in precedence order:**
1. **run-gate history** (`.run-gate/history.json`, `run_gate.py:4595-4860`): rolling mean over the last 200 candidates of this lane, fed from assay's progress `candidate` events. Used when ≥ 20 samples exist.
2. **Committed footprint** (`run-gate.footprint.json`): `candidate_ram_avg`, written by `run-gate footprint` from history and reviewed.
3. **Declared bootstrap** `resources.candidate_ram` (lane).
4. None → **N = 1**; that run collects the samples.

Candidate class = lane, in v1. A per-target-file class is possible later if one lane mixes very different candidates.

**Update:** after every R2 run (complete or not), run-gate appends that run's per-candidate samples. The mean moves continuously. N is fixed at launch; there is no mid-run resizing in v1.

**Transient overshoot.** Large candidates can coincide.
- Reclaim and zswap happen first. Then an OOM kill inside the slice, possibly hitting another gate's process.
- Every affected FAIL becomes `crashed` through conservative attribution (ancestor events included); PASSes stand. WP7 re-runs `crashed` serially.
- The host is protected by the slice `memory.max`.
- run-gate reports the run's `memory.peak` against `ram_target`. A repeated > 10 % overshoot is printed as a hint to lower `ram_target` (no automatic change).

**Example [M]:** cgprofile R2, avg ≈ 284-300 MiB.

| Host | ram_target | cpu_cap | N |
|---|---|---|---|
| 32-core host | 12 GiB | 30 | min(40, 30) = **30** |
| this host today | 6 GiB | 3 | **3** |

### 4.9 Remote R2 shards (Q-A outsourcing seam)

**Goal.** A campaign's candidates can run partly on other enrolled hosts. Each host runs one container with N local workers (§4.8). Results merge into one verdict.

**Responsibilities:**

| Concern | Owner | Mechanism |
|---|---|---|
| Campaign identity | **assay** | `assay campaign init <lane> --shards K` writes `campaign.json`: `{lane, commit, tree_sha256, plan_sha256, judge_sha256, assay_version, budget_per_candidate_s (pinned once from one baseline), shard_count K, created}`; `campaign_sha256` = sha256 of canonical JSON. Extends the existing `campaign` verb (`cli.py:521-528`) |
| Shard run | **assay** | `assay run <lane> --campaign campaign.json --shard i/K --state-dir <per-shard> --jobs N`. **Refuses** when its own commit/tree/plan/judge digest differs from the campaign (loud, instead of the silent cache miss passthrough-value fingerprints would cause, `mutation.py:1460-1475`); uses the pinned budget |
| State merge | **assay** | `assay state merge --campaign campaign.json --into DIR SRC…`: revalidates every record against its candidate id and the campaign judge; identical duplicates are idempotent, conflicting duplicates refuse; checks shard coverage with the existing `merge_mutation_shards` rules (`mutation.py:2774`) |
| Verdict merge | **assay** | **now (v16):** `assay run <lane> --campaign … --resume --state-dir DIR` replays every record (no candidate re-executes; one local baseline) and emits the complete verdict. **later (v17, optional):** `assay verdict merge` without a baseline, adding `judgment.r2.shards[{index, verdict_sha256}]` + `campaign_sha256` |
| Placement, admission, transport | **run-gate now, ciu v8 later; Buildkite as the queue** | one Buildkite job per shard on a shared `r2` queue (hosts pull shards, which balances heterogeneous hosts; one gate container per host via the agent's concurrency 1). On each host, local run-gate derives that host's N from its own caps and history. `K ≈ 4 × hosts` (guess), so slow hosts do not straggle |
| Provenance, trust | **run-gate / collector** | per shard: host, build/job id, image digest (seam 3), artifact sha256 (collector, `REMOTE-LANES-BUILDKITE.md` §4). Assay sees no host. Trust model v1: enrolled, operator-controlled hosts. The merge proves **consistency** (identity, coverage, record validity), not truth. Signed shard results (minisign is in tester-base) are deferred |

**Build now** (host-agnostic, useful locally too):
- RS1: campaign manifest + `--campaign` check + pinned budget.
- RS2: `assay state merge`.
- RS3: run-gate pass-through `run-gate <lane> --assay-shard i/K --campaign FILE` + shard fields in run-gate evidence.

With these, a local multi-container shard run and replay merge works on v16 today.

**Defer:**
- the Buildkite shard pipeline and `collect`-then-merge orchestration (after seams 1/3/4 run live);
- ciu v8 `ciu gate` placement;
- the v17 direct verdict merge;
- signatures;
- cross-host CoW (each host uses its own area).

**Hazard.** Lanes whose passthrough values are host- or path-specific produce different `judge_sha256` on another host. RS1 makes that a refusal at shard start. The fix is lane-side: move such values to infrastructure facts, which are name-only.

---

## 5. The `resources` settings table (one table for storage, IO and resource knobs)

**Name and scopes.** The existing `resources` table, extended. It is declared at:
- central root `[resources]` (new; resolved like `[footprint]`, `run_gate.py:1394-1411`);
- `[environments.<e>.resources]` (exists: `cpus`);
- project `run-gate.toml` top-level `[resources]` (new);
- `[lanes.<l>.resources]` (exists).

**Precedence:** CLI override > lane > project > environment > central > built-in default, with the exceptions in the tables below.

**Layout by consumer (rev 3.1, D19).**

| Level | Consumer | What reaches the consumer |
|---|---|---|
| Top-level keys of `resources` | **run-gate** itself | Run-gate acts on them (docker flags, binds, worker count). Not forwarded as settings |
| `[resources.<consumer>]` subtables | **exactly one other tool** | Forwarded **only** as `<CONSUMER>_<KEY>=<value>` env vars into that lane's process |

Run-gate holds a closed per-consumer key schema and validates every subtable. An unknown consumer or key is refused.

**Forwarding rule.**
- A consumer reads only variables with its own prefix.
- Assay core reads none of them. Assay's embedded cowtree reads `COWTREE_*`, which is the cowtree consumer, not assay.
- A consumer never sees another consumer's subtable.
- **Cross-cutting knobs are run-gate-owned top-level keys.** Run-gate translates them into each consumer's own interface (argv or that consumer's variable). It never forwards them raw.

**Run-gate-owned keys (top level):**

| Key | Values | Default | Scopes | Validation / exceptions | Translation (what consumers see) |
|---|---|---|---|---|---|
| `cpus`, `memory`, `memory_max`, `memory_swap`, `cpu_weight`, `io_weight`, `pids`, `shared` | existing | existing | existing (env: `cpus` only) | existing (`run_gate.py:1236-1262`) | docker flags |
| **`io_sync`** | `durable`, `strict`, `relaxed` | `strict` | all; `durable` **lane only** | **`durable` is absolute**: no scope or `--io-sync` override relaxes that lane. `relaxed` needs ephemeral mode + the image label | `relaxed`: read-only `/etc/ld.so.preload` bind. Every lane: `RUN_GATE_IO_SYNC=<value>` (a run-gate fact; consumers need not read it) |
| **`ram_target`** (cross-cutting) | size (`"12g"`) or percent of gates-slice `memory.max` (`"75%"`) | `"75%"` (guess) | all + `--ram-target` | native-R2 assay lanes only | §4.8.4 → **`assay run --jobs N`** and container `--cpus N`. Assay never sees the RAM target |
| **`candidate_ram`** | size | unset | lane | native-R2 assay lanes only | bootstrap input to the same derivation; never forwarded |

**Consumer subtables:**

| Subtable.key | Values | Default | Scopes | Validation | Forwarded as | Read by |
|---|---|---|---|---|---|---|
| **`[resources.cowtree] mode`** | `auto`, `off` | `auto` | all + `--cow off` | ephemeral/host modes | `COWTREE_MODE=<value>`. With `auto` and `MDT_COW_ROOT` set, run-gate also adds the same-path bind and passes `MDT_COW_ROOT` through (the mdt-owned area fact) | cowtree (the library inside assay, cmru tools, vmctl's `cowtree detect`) |
| **`[resources.vm] disk_cache`** | `unsafe`, `writeback` | unset (vmctl default `unsafe`) | project, lane | `host`/`bare-host` lanes only | `VM_DISK_CACHE=<value>` | vmctl |

No `[resources.assay]` or `[resources.cgprofile]` in v1: neither has a key to forward. Assay gets derived argv only (`--jobs`, `--shard`, `--campaign`). A future consumer key adds one subtable row and one schema entry; nothing else changes.

**Not in the table:**
- `jobs_max` stays in `assay.toml` (assay's own ceiling, read by assay from its own config).
- Shard count stays a CLI/campaign fact.

**Example (`run-gate.toml`):**

```toml
[resources]                         # project defaults (run-gate-owned)
ram_target = "60%"

[resources.cowtree]                 # project default for cowtree -> COWTREE_MODE
mode = "auto"

[lanes.r2]                          # native-R2 assay lane
kind = "assay"
[lanes.r2.resources]                # run-gate-owned
cpus = "8"                          # existing docker cap (upper bound; N never exceeds it or the slice cap)
ram_target = "12g"                  # cross-cutting: run-gate -> assay run --jobs N (N = min(12g/avg, cpu cap, jobs_max, remaining))
candidate_ram = "300m"              # bootstrap average until history has >= 20 samples
io_sync = "relaxed"                 # tests run under the shim; assay's critical fsyncs bypass it

[lanes.iobench-r0.resources]
io_sync = "durable"                 # durability lane: never relaxed by any scope or override

[lanes.vm-system.resources.vm]
disk_cache = "unsafe"               # -> VM_DISK_CACHE=unsafe for vmctl (also its default)

[lanes.legacy-r2.resources.cowtree]
mode = "off"                        # -> COWTREE_MODE=off: plain P22 path, no bind
```

**Durability lane (definition):** a lane whose tests verify fsync, crash consistency or storage behaviour (iobench, SQLite/Postgres durability, anything asserting data survives power loss). Under libeatmydata those tests would pass or fail for the wrong reason. Declare `[lanes.<l>.resources] io_sync = "durable"`.

**fsync delivery (unchanged mechanism, now keyed by `io_sync`):**

| Process | Shim? | Why |
|---|---|---|
| command-lane tests | yes | `ld.so.preload` covers every dynamic exec |
| assay judge | loaded, but its **critical fsyncs bypass it** via `assay._durable.fsync` = `syscall(SYS_fsync)` | R2 state (`mutation.py:1689-1737`), `cli.py:1178-1194`, verdict (`output.py:159`) |
| assay's test children | yes | `ld.so.preload` is not environment-borne |
| static binaries | no | documented limit |
| run-gate, cmru, evidence writers | no | outside the container |

**Scan and report (Q3).**
- `run-gate --list --json` gains `resources_effective` per lane: each key's effective value and the scope it came from.
- `run-gate resources report [--json]` adds:
  - eligibility (mode, image label);
  - static hints (counts of `fsync|fdatasync|O_SYNC|O_DSYNC|PRAGMA synchronous|sync_file_range|syncfs|fio|iobench` in the lane project's tracked sources and tests);
  - last wall and footprint;
  - for R2 lanes: `avg_ram`, its source, sample count, and the N that would be chosen now.
- A/B override `--io-sync relaxed`: refused for `durable` lanes; recorded as `relaxed(override)`; not eligible for history or budgets.

**Checklist for the scan agent** (in `LANE-AUTHORING.md`):
1. Read the report.
2. Mark durability lanes `durable` first (iobench now).
3. Run F1 and F2 for eligible strict lanes.
4. Propose `relaxed` only when the gain is ≥ 10 % wall or game-server `io.pressure` drops measurably.
5. One reviewed commit per project.

**Minimum moving parts for fsync:**
1. run-gate: table keys, guard, bind, label read, report, override.
2. tester-unified: package + label.
3. assay: `_durable.fsync` (independent hardening).

**Not used:** btrfs `nobarrier`/`commit=`, ZFS `sync=disabled`, seccomp.

---

## 6. Work packages (dependency order)

Rules:
- Registered `run-gate` lanes in tester-unified only (`AGENTS.md:159-181`).
- One gate at a time, `nice`/`ionice`.
- Red-first tests.
- One review per package (Codex luna impl, sol xhigh review).
- No `pragma: no cover`.

### CoW

| WP | Scope | Tests that must fail first | Acceptance | Size | Start |
|---|---|---|---|---|---|
| CW0 | baselines B1, F1 | — | evidence dir | S | now |
| CW1 | cowtree core + `SpaceEstimate` admission + CLI + lanes (rev 2 test list unchanged) | rev 2 CW1 list (root validation table, mountinfo, run ids, manifest-before-trees, lease, gc, names, clone safety, sealed delete, close semantics, space formula / refusal numbers / reserve / pending-delete) | cowtree R0/R1 100 % | M | now |
| CW2 | backends + live suite | rev 2 CW2 list | unit PASS; live green on r1002 | M | unit now |
| CW3 | r1002 btrfs/XFS (/ZFS); B2-B5 | live suite | `[R]` evidence | S-M | after CW2 |
| CW4 | run-gate `[resources.cowtree] mode` → `COWTREE_MODE` + same-path bind (replaces `bind_env`); cowtree reads `COWTREE_MODE` (renamed from `COWTREE_DISABLE`) | `test_cow_auto_unset_root_adds_nothing`; `test_cow_auto_same_path_mount_and_env`; `test_cow_off_sets_no_bind`; `test_cow_refuses_bad_root_value`; `test_cow_no_stat_no_translate`; `test_probe_containers_same_binds`; `test_cow_precedence_table` | run-gate PASS | S | now (after RT1) |
| CW5 | image: `COPY libraries/cowtree/` | smoke `import cowtree` | builds | S | after CW1 |
| CW6 | mdt docs/templates/selfcheck | selfcheck red until CW2 | review | S-M | docs now |
| CW7 | cmru fixtures | rev 2 CW7 list | cmru PASS | S | after CW1 |
| CW8 | assay seam + Level A | rev 2 CW8 list | preflight PASS | M | develop now; merge after assay-WP5 |
| CW9 | assay Level B | rev 2 CW9 list | + 20-candidate pilot identical | M | after CW8; **assay 10.2.0**, B169 `<10.3.0` |
| CW10 | VM lane: placement, compression, `VM_DISK_CACHE` default `unsafe` | `test_run_disk_cache_default_unsafe_base_writeback`; `test_vm_disk_cache_fact_writeback_honoured`; rev 2 CW10 list | `vm-system` PASS on/off area | S-M | after CW3 + RT1 |
| CW12 | spare clones (conditional) | rev 2 list | B6 > 10 % | S | after CW9 |
| CW13 | new devcontainer re-verification | — | identical buckets; wbytes ≤ 5 % of plain | S | new devcontainer |

### Resources table

| WP | Scope | Tests that must fail first | Acceptance | Size | Start |
|---|---|---|---|---|---|
| **RT0** | mdt-cockpit: gates-slice caps derived per host (cpu.max, memory.max) + doc; `run-gate.root.toml` `cpus = "3"` becomes per-lane only | host-setup unit test of the derivation table (cores, reservations → quota); dry-run render | review; applied on r1002 first | S-M | now |
| **RT1** | run-gate: `resources` at central/project scopes + run-gate-owned keys + **consumer subtables with a closed per-consumer schema and the `<CONSUMER>_<KEY>` forwarding rule** + precedence + validation + `--list --json resources_effective` + `resources report` | `test_resources_scopes_and_precedence_table`; `test_unknown_consumer_subtable_refused`; `test_subtable_forwarded_only_as_own_prefix`; `test_no_consumer_receives_another_subtable`; `test_cross_cutting_ram_target_never_forwarded_raw`; `test_durable_is_lane_only_and_absolute`; `test_unknown_key_and_bad_value_refused_per_scope`; `test_vm_disk_cache_only_host_modes`; `test_ram_target_only_native_r2_assay_lanes`; `test_percent_ram_target_needs_readable_slice`; `test_list_json_shows_value_and_source`; `test_report_hints_and_eligibility` | run-gate PASS; SPEC/README/LANE-AUTHORING updated | M | now |

### fsync

| WP | Scope | Tests that must fail first | Acceptance | Size | Start |
|---|---|---|---|---|---|
| **IO1** | run-gate `io_sync` delivery (bind, label, facts, override, history split) + image package/label + iobench lanes `io_sync = "durable"` | `test_strict_default_no_bind_fact_set`; `test_relaxed_binds_generated_preload_readonly`; `test_relaxed_refused_without_label`; `test_durable_refuses_override_and_scope_relaxation`; `test_relaxed_refused_for_exec_mode`; `test_override_marked_not_history_eligible`; `test_history_split_by_io_sync` | PASS; manual A/B shows the shim mapped in a test process | M | after RT1 |
| **IO2** | assay `_durable.fsync` (raw syscall x86_64 nr 74, aarch64 nr 82; unknown arch + shim mapped → refuse) + monkeypatch test updates (`tests/core/test_mutation_executor_bound.py:985-995`, `test_mutation_coverage_closure.py:405-414`, `test_cli_coverage_closure.py:559-565`) | `test_durable_fsync_raw_syscall`; `test_state_and_verdict_writes_use_durable`; live `test_not_intercepted_under_preload_shim` | preflight PASS | S | now |
| **IO3** | scan agent run | — | reviewed opt-ins | S | after IO1, IO2, F1 |

### Parallel R2

| WP | Scope | Tests that must fail first | Acceptance | Size | Start |
|---|---|---|---|---|---|
| **PR0** | assay progress `candidate.resources` (peak RSS, write_bytes, cores) + run-gate history ingest (rolling mean, sample count, judge share) + `run-gate footprint` writes `candidate_ram_avg` | `test_candidate_event_has_peak_rss_and_write_bytes`; `test_history_rolling_mean_last_200`; `test_footprint_manifest_candidate_ram_avg` | collected on the next R2 runs | S | now |
| **PR1** | window host, scheduler, LimitLedger, `--jobs`/`jobs_max`, v17 | rev 2 PR1 list | preflight PASS | L | after assay-WP1/WP4/WP7 + CW9; with assay-WP8 → **11.0.0 under extended B169 waiver `<11.1.0`** |
| **PR2** | scaling measurement | — | bucket identity; crashed < 2 % (guess) | S | after PR1 |
| **PR3** | run-gate worker policy (§4.8.4) + container `--cpus N` + `--jobs N` pass-through + overshoot hint | `test_n_is_min_of_ram_cpu_jobsmax_remaining_table`; `test_avg_ram_source_precedence`; `test_no_samples_means_one`; `test_cpu_max_unlimited_means_one_with_info`; `test_never_more_workers_than_cpu_cap`; `test_overshoot_hint_after_repeated_peaks`; `test_policy_reads_no_psi_and_no_cgprofile` | PASS | M | after PR0 + RT1; enable with PR1 |

### Remote shards

| WP | Scope | Tests that must fail first | Acceptance | Size | Start |
|---|---|---|---|---|---|
| **RS1** | assay campaign manifest + `--campaign` + pinned budget | `test_campaign_identity_fields_and_digest`; `test_shard_refuses_on_commit_tree_plan_judge_mismatch`; `test_shard_uses_pinned_budget_not_own_baseline`; `test_campaign_without_shards_is_plain_run` | preflight PASS | M | after assay 10.1.0 (touches `cli.py`; disjoint from WP1-4 hunks) |
| **RS2** | `assay state merge` | `test_merge_revalidates_each_record`; `test_identical_duplicate_idempotent`; `test_conflicting_duplicate_refused`; `test_coverage_rules_reused`; `test_replay_after_merge_executes_no_candidate` | PASS; local 2-container shard + replay pilot = serial buckets | M | after RS1 |
| **RS3** | run-gate `--assay-shard`/`--campaign` pass-through + evidence fields (shard, campaign_sha256, host) | `test_shard_args_passed_only_to_native_r2_lanes`; `test_evidence_records_shard_and_host` | PASS | S | after RT1 |
| RS4 | deferred: Buildkite shard pipeline (seam 2 extension) + collect + merge orchestration; ciu v8 placement | — | — | M | after seams 1/3/4 live |
| RS5 | deferred: v17 `assay verdict merge`; signed shard results | — | — | M | with assay-WP8 / later |

**Order:**
1. Now, in parallel: CW0, CW1, RT0, RT1, IO2, PR0, CW6-docs.
2. Then CW2-unit, CW4, CW5, CW7, IO1, RS3.
3. CW8 → CW9 (assay 10.2.0).
4. RS1 → RS2 (after assay 10.1.0).
5. CW3 → CW10.
6. PR1 (after assay-WP1/4/7 + CW9; with WP8 → 11.0.0) → PR2; PR3 enable.
7. IO3 after F1.
8. CW12 conditional; CW13 last; RS4/RS5 deferred.

**Versus the assay redesign:**
- CW8/CW9: `isolation.py` and the new modules.
- IO2: fsync call sites only.
- RS1/RS2: `cli.py` campaign/state verbs, which are not touched by WP1-WP4.
- PR1: `mutation.py`/`verdict.py`/`verify.py` after WP4/WP7, together with WP8.

---

## 7. Benchmarks before claiming benefit

**Host guard:**
- off-peak, `nice -n19 ionice -c3`, one at a time, gates slice;
- cgprofile collateral on the game-server cgroup; abort at `io.pressure some avg10` > baseline + 2 (guess);
- CoW comparisons only on r1002 until a dedicated LV exists.

| ID | Where | What | Claim rule |
|---|---|---|---|
| B0/M | done | §0 | inputs |
| B1 | this host | 20-candidate R2 pilot, plain: wbytes per candidate, wall | CW13 baseline |
| B2 | r1002 | P22 plain vs Level A vs Level B, n=5, cold/warm: wall, CPU, wbytes, exclusive bytes | ≥ 20 % wall or ≥ 80 % wbytes |
| B3 | r1002 | clone walk vs snapshot vs copytree | CW12 relevance |
| B4 | r1002 | delete latency, space return | documented |
| B5 | r1002 | **compression algorithm/level for VM images and runs** (btrfs `zstd:1`/`zstd:3`/`lzo`; ZFS `zstd-1`/`lz4`), `cache=unsafe`: host bytes written, `compsize` over a whole run, guest p99, CPU | lowest bytes written within 20 % of best p99/CPU |
| B6 | r1002/new dc | materialize share after CW9 | CW12 if > 10 % |
| F1 | this host | sync census per lane (host bpftrace on the gate container's cgroup) | scan input |
| F2 | this host | `--io-sync relaxed` A/B, n=3 | ≥ 10 % or a pressure drop |
| PR2 | r1002/new dc | scaling at N = 1..16 | bucket identity; crashed < 2 % |
| RS2-pilot | this host or r1002 | 2 local shard containers + replay vs serial | identical buckets |

---

## 8. Risks and open questions (recommended default first)

**Risks**
1. **Host unmount is invisible** to containers; handled by the runbook.
2. **ioctl struct errors** fail loudly; CW2 layout test + CW3.
3. **`ld.so.preload` loads the shim everywhere** in a relaxed container; `durable` lanes and the scan prevent misuse.
4. **Overcommit overshoot** raises `crashed` (also in peer gates via ancestor events); WP7 retries; overshoot hint.
5. **Remote trust:** a compromised enrolled host could forge buckets. Accepted in v1; signing deferred (RS5).
6. **Host-specific passthrough values** break cross-host shard identity; RS1 refuses loudly.

**Questions**

| # | Question | Recommended default |
|---|---|---|
| Q-G | Default `ram_target` when the caller states none | **75 % of gates-slice `memory.max`** minus current slice usage at launch |
| Q-H | Shard granularity for remote runs | **K = 4 × participating hosts**, pulled from a shared `r2` queue |
| Q-I | Should the central root config get `[resources]` defaults, or keep central defaults only on environments? | **Both allowed** (central `[resources]` / `[resources.cowtree]` for estate-wide `ram_target` and cowtree `mode`; environments for image-bound facts) |
| Q-J | Move existing lane `memory`/`cpus` knobs, or leave them? | **Leave them**; new keys only (no consumer migration) |
| Q-K | Who runs the merge for remote shards: the controller's run-gate (later ciu v8), or a Buildkite final step? | **The controller's run-gate after `collect`**, so evidence stays local and commit-addressed |
| Q-L | v17 direct verdict merge (no baseline) | **Defer.** Replay merge costs one baseline per campaign |
