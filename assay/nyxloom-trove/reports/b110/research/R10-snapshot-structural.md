# R10: Why the per-mutant snapshot is shaped as it is, and which structural changes pay

Read-only research. Clone `.worktrees/.assay-b105-ciu-root-20260926-30eec294-copy`, branch `assay-b105-evidence-integrity`, HEAD `95622e3a`. Paths are relative to `assay/` unless marked `vbpub:`. Nothing was edited. Probes ran only in `scratchpad/r10/probe/` under `nice -n 19 ionice -c3`, with host load at 10–13 on 8 cores. The probe copies have been deleted. No gate, container or test suite was started.

**Labels:**
- [M] measured here;
- [C] computed from measured values;
- [A] approximation or estimate;
- [I] inferred from code or docs, not executed;
- [D] documented externally (URL given).

The static inventory of what the tests read was produced by a helper read-only agent and spot-checked by me. It is static evidence only.

---

## Summary

1. **Full history is no longer needed once A-468(a) lands.** After `test_python_qualification.py` is ignored and the two tag tests are deselected, no collected test needs history, tags, an old commit, a clean real checkout, or any worktree file outside `assay/`.
   - Only 17 test functions (24 cases) touch the real repository through git, and they need only the HEAD commit and HEAD tree.
   - A conftest probe decides whether 33 further cases run or skip.
   - **The snapshot must still be a git repository whose HEAD is the exact child commit.** Some tests build a wheel from the committed HEAD (`git clone` of the snapshot), and the dirty/HEAD check (A-195) and the deterministic child identity (A-186) need it too.
   - The minimal shape is therefore:
     - a **depth-1 (A-451 default) repository of the full monorepo commit**, with **only `assay/` materialized** (the rest skip-worktree);
     - the full index;
     - one named extra path, `run-gate-project/run-gate.py`. It is the target of the tracked symlink `assay/run-gate.py`, and no test reads it.
2. **What the committed-object snapshot guarantees that a worktree copy cannot:**
   - it binds to the exact recorded revision;
   - it keeps dirt and ignored residue out (for example stale `.pyc`, coverage files, `.assay/`);
   - it runs no committed filter or hook;
   - it is deterministic (fixed mtime and mode, deterministic child OID);
   - it protects the operator's checkout. A ciu worktree's `.git` is a **gitfile** pointing into `vbpub/.git/worktrees/<name>` [M], so a copied worktree would let a mutant's `git` commands mutate the real repository.

   **Build once per lane, copy per candidate** (Design B) keeps every guarantee under R2's five conditions, but on this ext4/overlay2 storage the copy is no cheaper than today's write: `cp -a` of the 85 MB tree took 0.6–2.7 s, against 0.8–1.3 s for today's worktree write [M]. It only pays with reflink.
3. **Environment options:**
   - **The gate container's `/tmp`** is the container's **overlay2 writable layer on host ext4** [M]. run-gate adds no tmpfs, so there is no reflink.
   - **Git calls zero fsyncs** anywhere in the per-candidate snapshot path [M, strace]. So eatmydata or `core.fsync=none` saves nothing there. The test suite's own clones fsync 3× per clone, about 80 ms in total [M].
   - **tmpfs** is charged to the container's 2 GiB memcg, and it converts today's reclaimable page cache into swap-backed memory on a host that already has 24 GiB in swap [M].
   - **overlayfs and fuse-overlayfs** need privileges the uid-1003 container does not have, and they amount to "tree reuse" (an A-472 amendment).
   - **Alternates, `--shared` and `--reference`** are forbidden (A-184/A-185, `isolation.py:1068-1071`), and they would save only the pack copy.
4. **Ranked recommendation:**
   1. **Shallow snapshot for both B105 lanes** (no host change, no privileges, no A-472 conflict):
      - measured 2.5 s → 1.5 s per candidate snapshot;
      - plus about 3.3 s per candidate that reaches `build_release`'s two clones;
      - it makes P5's C2 (the incremental child-closure bound) nearly moot.
   2. **An `assay/`-only worktree** through a new `snapshot_selection` value (needs a decision against A-161/A-269 §2): measured 1.5 s → 0.46 s.
   3. **A reflink scratch filesystem**, which is a host change on the production host.
   4. **tmpfs**, only at the small footprint.

   With 1 and 2 adopted, C1 plus its C4 sweep saves about 0.03–0.1 s, so C1 should be deferred. The sweep's closing of the **existing** assume-unchanged hole is worth keeping, but on its own merits.

---

## 1. Q1: Do we need the whole vbpub repository, with full history, per mutant?

### 1.1 What the repository-dependent tests read or execute

The "≈68" figure is 32 (`test_python_qualification.py`) + 32 (`test_distribution_build_release.py`) + 4 (`test_b105_report_check.py`).

| Module / category | Test count | Reads / executes | Needs |
|---|---|---|---|
| `test_python_qualification.py` (module-wide `requires_parent_repository`, `:46`), **ignored by A-468(a)** | 32 fns | `gate/python/qualify_topos.py`:<br>• pinned `INPUT_REVISION = 9f522a72…` and topos tree `1bc8a512…` (`:31-33`);<br>• `verify_pinned_inputs` runs `rev-parse 9f522a72^{commit}` and `9f522a72:topos` and checks the 1.2.5 wheel's sha256 (`:655-665`);<br>• `_extract_pin` runs `git archive --format=tar 9f522a72 .gitignore topos \| tar`, then `ls-tree -r 9f522a72 -- topos`, which must give 966 entries (`:340-370`);<br>• `git status` of the checkout before and after (`:317-320`; tests `:291-297`);<br>• runs the unmutated PATH / 1.2.5 wheels (A-468 rationale) | a **historical commit** (full history); **FAILS** (does not skip) in a shallow snapshot [I] |
| `test_distribution_build_release.py`, fixture `built` (`:362-388`), calls `build_release.build(REPO_ROOT, …)` twice | 13 fns / 13 cases use it (`:391 … :656`); 19 more use synthetic `tmp_path` repos but share the module marker (`:49`) | `gate/distribution/build_release.py`:<br>• `describe --tags --exact-match --match assay-v* HEAD`, where no tag is fine (`:191-193`);<br>• `rev-parse HEAD` (`:168`);<br>• **`git clone --no-local --no-checkout` of the snapshot** (`:172`);<br>• sparse-checkout of `assay` (`:173-174`);<br>• `checkout --detach <oid>` (`:175-177`);<br>• `log -1 --format=%ct` (`:247`);<br>• setuptools-scm `describe` inside the clone, which falls back to `0.1.1.devN+g…` without tags [I]. | HEAD commit plus tree, and **HEAD not tagged `assay-v*`**. The wheel is built from **committed HEAD**, which is the mutant's child commit. |
| `test_b105_report_check.py` (**no** skip marker) | 4 fns / 11 cases (`:142, :181, :195, :211`) | `rev-parse HEAD` and `HEAD^{tree}` with `cwd=REPO_ROOT` (`:23-31, :62, :114-116`); `tools/b105_report_check.py:46-52` `rev-parse <c>^{tree}` | HEAD only; **FAILS** without a repository |
| conftest probe (`tests/conftest.py:276-335`) | gates the 33 build-release cases | `git -C REPO_ROOT rev-parse --show-toplevel` must equal `REPO_ROOT` | "is the repository top" only. A missing repository gives a **silent SKIP**, not a failure. |
| `test_runner_snapshot_selection.py` `:863, :906` | 2, **deselected** (`assay.toml:85-86`) | `git show <tag>:…`, `c56a13ea` | tags plus history; out of scope |
| `test_gate_qualify_dstdns_sql.py:73-76,193-197` | 1 | `git hash-object <fixture file>` | no repository needed [I] |
| `tests/qualification/*` | 9 | the real Go/JS toolchains | always skipped: env `{}` plus `PATH` only (`assay.toml:91-92`) |

**Filesystem reads outside `assay/`:** none in the remaining suite [static]. Every `topos/`, `cmru/` or `ciu/` string in `tests/` names a path inside a synthetic temporary repository. The only real reads of `REPO_ROOT` are git `cwd`/`-C` arguments and `REPO_ROOT/assay/gate/distribution/*`.

**Inside `assay/`, outside `src/` and `tests/`:** heavily used, all under `assay/`:
- `pyproject.toml` (asserted when conftest loads);
- `assay.toml`, `run-gate.toml`;
- `tools/`, `gate/`;
- `nyxloom-trove/carve-assets/`, `nyxloom-trove/nyxloom.toml`;
- `README.md`, `docs/`.

So a narrowed worktree must keep **all of `assay/`**, not just `src/` and `tests/`.

**Pins that change with the history setting:**
- `tests/test_self_lane.py:102` asserts `snapshot_history == "full"`.
- `:241-246` requires the preflight and qualification lanes to have identical `isolation` tables and argv.
- `assay.toml:73-74` states "history is also required by tests that read pinned commits", which becomes false once A-468(a) lands.

### 1.2 Minimal repository content for the remaining suite

| Shape | Remaining-suite effect | Verdict |
|---|---|---|
| No git repository (plain directory) | 11 report-check cases **fail**; 33 build-release cases **skip silently** (B110 forbids silently running less, `4-backlog.md:11076-11077`); the dirt/HEAD check (A-195) and the child identity (A-186) are impossible | ✗ |
| Commit tree containing only `assay/` (a synthetic commit) | tests pass [I], but the executed commit is **not** the recorded commit, which breaks A-161's content identity | ✗ |
| **Depth-1 shallow** at the judged commit, full tree (A-451 default) | all remaining tests pass [I]:<br>• `git clone --no-local` from a shallow repository with a detached HEAD and no refs works and yields a shallow clone (`rev-list --count` = 1) [M];<br>• the version string stays valid [I] | ✓ minimal history |
| Full commit, shallow, **only `assay/` materialized** (rest skip-worktree, as A-268/A-269 already do for 3 leaves) | passes [I]: `build()` reads only `assay/gate/distribution` from the worktree and clones from the object store | ✓ minimal worktree, but needs a decision (§4) |

### 1.3 Which decisions require a git repository of the exact committed tree

- **A-161:** the state is rebuilt from the resolved commit's tracked objects, and "the recorded commit [is] a real content identity". This excludes worktree copies and synthetic commits. It does **not** require worktree completeness beyond what the command needs, but the text says "complete repository topology … preserved", so narrowing needs a decision.
- **A-186/A-188:** the mutant is a deterministic `commit-tree` child with a fixed identity. Tests that consume HEAD through git (`build_release` clones HEAD and builds a wheel from it; report-check `rev-parse`) see the mutant **only because it is committed**. A plain directory would make the mutant invisible to those 13 tests.
- **A-195:** the post-unit `DIRTY_TREE`/`HEAD_CHANGED` check needs the index and HEAD (`mutation.py:1843-1874`, `git.py:826-905`).
- **A-269 / A-268:** the skip-worktree set must equal the declared omissions, and the index tree must equal the HEAD tree (`isolation.py:1050-1066`).
- **A-185/A-451:** the history boundary is verified per materialization (`isolation.py:1022-1039`). **A-451 explicitly makes shallow the default and `full` an opt-in.**
- **A-184/A-194:** assume one private object store per unit, with a copy or reflink and never shared.
- **A-120, A-160 and A-193 are neutral.** A-120 originally even said `copytree`; A-161 superseded that.

---

## 2. Q2: "Why not just the latest commit, or the worktree state?"

"Just the latest commit" means shallow, and that is already A-451's default. B105 opted out only for the pinned-commit tests (`assay.toml:73-74`). The real alternative to question is the **worktree copy**.

| Guarantee | Committed-object snapshot | Copy of the live worktree |
|---|---|---|
| Exact-revision binding | bytes = the recorded commit's tree; the mutant = a deterministic child commit (A-161, A-186) | uncommitted edits and ignored files enter; the verdict names a commit that was not what ran |
| Dirty-tree policy (B102) | even `--allow-dirty` dirt never reaches R1/R2 units, which are built from objects | dirt is copied into every unit |
| Ignored residue | none: manifest-only writes (`test_isolation.py:309`) | stale `__pycache__`, `.pytest_cache`, `.assay/coverage-*.json` and build directories are copied. Stale bytecode is a **false-kill** hazard (reproduced in R2 §4). |
| Hostile content | no checkout or archive, so no committed filter or hook runs (DESIGN-GUIDE "Why raw objects", `git archive` witnessed running a filter) | copying is inert, but the copied `.git` carries hooks and config |
| Operator-checkout protection | fresh `.git`, no alternates or source path in config (`isolation.py:1068-1079`) | **ciu worktrees have a gitfile `.git`** (`gitdir: /workspaces/vbpub/.git/worktrees/<name>`) [M]. A copy shares the real admin directory, so a test's `git commit`, `update-index` or `config` would move the real branch or index. `copytree` also dereferences symlinks by default (A-161 rationale). |
| Determinism | fixed mtime and mode, no clock-dependent directory state (`_FIXED_MTIME`, `isolation.py:67, 1942, 1957`) | wall-clock mtimes and umask vary per copy |

**Is "one base per lane, cheap copy per candidate" enough?** Yes, it keeps these guarantees, provided R2's five conditions hold:
1. copy by explicit manifest, never `copytree`;
2. symlinks stay symlinks;
3. re-`utime` the swapped file;
4. never copy a refreshed index; keep a per-candidate hash pass;
5. never run a command in the base or template.

**Add a sixth condition: never pre-compile bytecode into the base.** With `_FIXED_MTIME` and same-size compare-swaps, a shared `.pyc` would silently mask mutants.

**The cost argument fails on this storage:**
- `cp -a` of the 85 MB, 5.3k-file tree took 0.62 s, 1.94 s and 2.73 s in three consecutive reps. It grew as dirty pages accumulated and writeback throttling set in [M].
- Today's worktree write (`cat-file --batch` plus writes) took 0.78–1.28 s [M].
- **The copy is cheaper only when it is a reflink** (§3).

---

## 3. Q3: Environment options

### 3.1 What the gate actually runs on

- **Container launch:** `run-gate.py:7470-7556` builds `docker run -d --init --cgroup-parent … -v <repo dual mounts> --memory 2g --memory-swap 8g --cpus 3 tester-unified:local`.
  - There is **no `--tmpfs`, `--cap-add` or `--device`**.
  - Extra mounts exist only as bind `-v` entries through `RUN_GATE_EXTRA_MOUNTS` (`:170, :7493-7506`).
  - Lane resources are at `run-gate.toml:33-45`.
- **The image** (`vbpub:tester-unified/Dockerfile`) has no `VOLUME` and no `TMPDIR`, and it runs as `USER 1003`, which is non-root (`:124`).
- **Docker daemon:** storage driver `overlay2`, backing filesystem `extfs`, cgroup v2, kernel 7.1.8, security options apparmor + seccomp builtin + cgroupns [M, `docker info`].
- **So the container's `/tmp` is the overlay2 upper layer on host ext4** [M/I]. The analysis report (§7.2) says "/tmp is ext4", which describes the devcontainer (`findmnt`: `/dev/mapper/gstammtisch--vg-root` ext4 [M]). The conclusion, no reflink, is unchanged.
- **Scratch root:** assay's snapshot scratch root is `tempfile.TemporaryDirectory(prefix="assay-p23-")` (`runner.py:315-318`), so `TMPDIR` moves only the snapshots.
  - The child command's environment is lane env plus `PATH` (`assay.toml:91-92`, `runner.py:880-897`). Tests' own `tmp_path`, clones and venvs therefore stay on `/tmp` whatever assay's `TMPDIR` is [I].
- **Host** [M, `/proc`]:
  - 16 GiB RAM, MemAvailable 3.8 GiB at probe time;
  - swap 69 GiB, of which 24 GiB was in use;
  - `vm.swappiness=10`, `dirty_expire_centisecs=3000`, `dirty_background_ratio=10`.
  - The disk is a VM virtio disk (`/dev/vda6,7` swap) with an LVM root.
  - This host also runs the production game server.
- **git 2.55.0** in the devcontainer [M]; the gate image records 2.55.0 too (`4-backlog.md:9036`). Python 3.14.7 `shutil.copyfile` uses `copy_file_range` (`/usr/local/lib/python3.14/shutil.py:59, 137, 328`) [M]; the gate image is 3.14.6 [M: image env], with the same behavior [I].

### 3.2 Probe: a snapshot-equivalent per candidate (the `_build` steps plus the post-command dirt check, then `rmtree`)

**Method** (`scratchpad/r10/probe.py`):
- Seed = a `--no-local --bare` clone of the working clone: 41.6 MB pack, 6,343 commits.
- HEAD-only seed = `pack-objects --stdout` without `--revs` (as P22 does) over `rev-list --objects --no-walk HEAD`, plus a `shallow` file: 23.4 MB.
- Each rep runs:
  - `git init` with an empty template;
  - a `copyfile` of the packs;
  - `read-tree`, then skip-worktree for the omissions;
  - `hash-object -w`, `update-index --cacheinfo`, `write-tree`, `commit-tree`;
  - a full `rev-list --objects` plus `batch-check` plus a `--no-walk` walk;
  - `cat-file --batch` worktree writes with chmod and `utime(946684800)`;
  - `[update-index --refresh]`;
  - `rev-parse`, `rev-list --count`, `status`, `write-tree`, `ls-files -v`;
  - the dirt check (`status` plus `ls-files --others`);
  - `rmtree`.
- This is **not** P22 itself. It omits the manifest proof, the stale-site read and the Python validation, so absolute values are a floor. **The differences between variants are what matter.** n=3, median.

| Variant | Files | Total s (3 reps) | Pack copy | Closure | Worktree | Refresh | Verify | Dirt | rmtree |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| full history, whole tree (today's shape) | 5,351 | **2.71** (2.47–2.84) | 0.28 | 0.35 | 0.94 | – | 0.41 | 0.28 | 0.25 |
| full + C1 refresh | 5,351 | **2.50** (2.14–2.51) | 0.02 | 0.38 | 0.91 | 0.36 | 0.13 | 0.07 | 0.23 |
| shallow (depth 1), whole tree | 5,351 | **1.60** (1.59–2.04) | 0.02 | 0.04 | 0.78 | – | 0.29 | 0.30 | 0.16 |
| shallow + refresh | 5,351 | **1.50** (1.44–1.79) | 0.01 | 0.03 | 0.80 | 0.28 | 0.07 | 0.06 | 0.17 |
| full + refresh, `assay/`-only worktree | 1,016 | **1.08** (0.98–1.10) | 0.03 | 0.37 | 0.25 | 0.10 | 0.10 | 0.02 | 0.04 |
| **shallow + refresh, `assay/`-only worktree** | 1,016 | **0.46** (0.46–0.49) | 0.01 | 0.04 | 0.19 | 0.09 | 0.03 | 0.02 | 0.04 |

**Test-side cost that scales with history:** `build_release`'s clone plus sparse checkout of `assay` (the `built` fixture does this twice), 2 reps each:
- full snapshot: 2.58 s and 2.53 s, 42 MiB pack;
- shallow snapshot: 0.89 s and 0.91 s, 23 MiB pack, clone is shallow, count = 1 [M].

**Sizes** [M/C]:
- Full closure: 55,118 objects, 1.00 GB uncompressed; the pack without `--revs` is **43.4 MB** from this clone.
- R2's "62 MiB" came from the main checkout. That repository has 9 packs (62 MiB) plus 3,802 loose objects (51 MiB), and loose objects carry no deltas [M, `count-objects`], so the seed pack size depends on the source's packing state.
- HEAD closure: 5,473 objects, 71 MB uncompressed, **23.4 MB** packed.
- Tree: 5,328 regular files, 72.3 MB, **81.3 MiB page-rounded**. `assay/`: 1,015 files, 21.8 MB, **22.9 MiB** page-rounded. Index: 0.7 MB.

**fsync** [M, `strace --seccomp-bpf -w -f -c -e trace=fsync,fdatasync,sync_file_range,syncfs,sync,msync`]:
- A whole per-candidate run, with and without `-c core.fsync=none`: **0 calls**.
- `git init` + `add` + `commit` + `update-index --refresh` + `hash-object -w`: **0 calls**.
- `git clone --no-local --bare` of the 41.6 MB repository: **3 fsyncs, 80 ms of wall time in total**, against a 9.3 s clone under ptrace.
- A Python sanity trace captured both `fsync` and `sync`, so the method works.
- This matches git's documented default `core.fsync=committed,-loose-object`, i.e. packs only [D: https://git-scm.com/docs/git-config#Documentation/git-config.txt-corefsync]. P22 never creates a pack in a child: it copies the pack in Python and writes only loose objects and the index.

### 3.3 Per option

- **tmpfs for assay's `TMPDIR`:**
  - **Not configured** today. It needs a run-gate change, either a new lane `tmpfs` key that emits `--tmpfs /scratch:size=…` or a host-side tmpfs bind through `RUN_GATE_EXTRA_MOUNTS`. `--tmpfs` needs no container privileges, since the daemon mounts it [D: https://docs.docker.com/engine/storage/tmpfs/].
  - **Memory:** shmem pages are charged to the memcg that first touches them, i.e. the container's 2 GiB cap [D: https://docs.kernel.org/admin-guide/cgroup-v2.html, "memory.stat: shmem"; "a memory area is charged to the cgroup which instantiated it"]. They are reclaimable only by swapping (the lane has 6 GiB of swap headroom, `--memory-swap 8g`).
  - **Footprint per live snapshot** [C]:
    - full: pack 41–62 + tree 81.3 + index 0.7 + directories ≈3 + run residue ≈10–20 [A] ≈ **140–170 MiB**;
    - shallow: ≈ **120 MiB**;
    - shallow + `assay/`-only: ≈ **55–65 MiB**;
    - plus the seed once (23–62 MiB).
  - **Against the measured peak** of 738–987 MiB (analysis §4.5, jobs = 1; `assay.toml:173`):
    - jobs = 1 full: ≈1.2 GiB, which fits;
    - jobs = 3 (P4): 3 × 160 + 62 ≈ 0.55 GiB of shmem on top of an RSS that is itself about 2.2–3 GiB if each worker peaks as today [A], so the lane is already over 2 GiB and swapping;
    - at the shallow + `assay/`-only size: 3 × 60 + 23 ≈ 0.2 GiB.
  - **Nuance:** today's ext4 writes are also charged to the memcg, as page cache, but that cache can be dropped after writeback. tmpfs turns it into swap-backed memory on a host with 24 GiB already swapped [M]. If the §4.5 figure is cgroup `memory.peak` rather than RSS, it already includes that page cache [I].
  - **Saving:** only the I/O part: no writeback or throttling, and a faster `rmtree`. The zlib inflate (≈0.48 s CPU, R2) and about 16k Python syscalls remain. **Estimate 0.3–0.8 s per candidate** for the full shape, **≈0.1–0.2 s** for shallow + `assay/`-only [A]. It also keeps about 130 MB per candidate (≈490 GB per campaign) of writeback off the production host's disk.
  - **No A-472 conflict, and no stale-bytecode effect:** trees are still fresh.
- **eatmydata / `core.fsync=none`:**
  - The snapshot path saves **0 s** [M: 0 fsyncs].
  - Test-side, the `build_release` clones save at most about 0.16 s per candidate that runs them [M: ≈27 ms per fsync, 3 per clone].
  - eatmydata is **not installed** [M]. It would need `LD_PRELOAD` in the child environment, a declared-env change (A-019, `decisions.md:31`), which alters the measured command.
  - `-c core.fsync=none` in `_FIXED_CONFIG` would be harmless but pointless.
  - Not recommended.
- **Reflink (XFS `reflink=1`, btrfs):**
  - **Not available.** Both the host root and the docker data root are ext4 [M].
  - It needs a **host change on the production host**: a new LV or partition formatted XFS/btrfs, mounted and bind-mounted into the gate through `RUN_GATE_EXTRA_MOUNTS`, with `TMPDIR` pointed at it for the assay process only. The VG free space is unknown from here.
  - **Do not** use a loop-file filesystem: loop devices on this host caused the 2026-09-09 host hang (memory `cgroupns-host-loop-device-host-hang-incident`).
  - No container privileges are needed.
  - `shutil.copyfile` → `copy_file_range`, which reflinks on XFS and btrfs [D: https://man7.org/linux/man-pages/man2/copy_file_range.2.html].
  - A-184 explicitly permits "reflinks with distinct inodes". The worktree half needs Design B (the optional C4 template copy), because today the tree is written from `cat-file`, not copied.
  - **Saving:** the pack copy becomes metadata-only and, with a template, so does the worktree data. The per-file clone, chmod and utime remain. **Estimate 0.5–1.0 s per candidate** for the full shape [A], plus the 130 MB of writeback per candidate.
  - Stale bytecode: safe if the template is never executed and holds no `.pyc`.
- **overlayfs** (lower = a verified base materialization, upper = fresh per candidate):
  - It needs `mount(2)` inside the container, meaning `CAP_SYS_ADMIN` plus an AppArmor exception, or a user namespace. Docker's default seccomp profile gates `unshare`/`mount` behind `CAP_SYS_ADMIN` [D: https://docs.docker.com/engine/security/seccomp/], and the container runs as uid 1003.
  - That is a **privilege change to the gate container** on a production host.
  - **It is "tree reuse", which A-472 forbids.** The lower layer is shared read-only; writes copy up, so siblings stay isolated, unlike hardlinks.
  - **G1 needs re-specification.** Overlay reports the underlying lower inode for non-directories with the overlay's own `st_dev` [D: https://docs.kernel.org/filesystems/overlayfs.html, "Inode properties"]. The `(st_dev, st_ino)` pairs differ between mounts even though the data is shared.
  - **The lower is reachable by path** by the same uid, so a per-candidate content hash (C1) stays mandatory.
  - The lower must never have been executed (no `.pyc`).
  - **Estimate ≈0.4–0.6 s per candidate** [A], no better than the unprivileged shallow + `assay/`-only shape (0.46 s [M]).
- **fuse-overlayfs:** needs `/dev/fuse` plus `CAP_SYS_ADMIN` or a userns seccomp change [D: https://github.com/containers/fuse-overlayfs]. It has the same A-472/G1 issues, and FUSE overhead applies to every file the 5.8k-test suite reads, so it is likely a net loss [A]. Not recommended.
- **git alternates / `clone --shared` / `--reference` / plain local `clone` (which hardlinks):**
  - forbidden by A-184/A-185/A-194 and refused at runtime (`isolation.py:1068-1071`; seed `:2078`);
  - a same-uid command can write into the shared store;
  - saves only the pack copy, 0.01–0.28 s [M];
  - not recommended.

### 3.4 Options table

| Option | Applies in the container today? | Privileges / host change | Memory-cgroup effect | Conflicts with A-472 / stale bytecode? | Est. saving per candidate | Effect on the claim | Effort |
|---|---|---|---|---|---|---|---|
| **Shallow snapshot (A-451 default)** for both B105 lanes | yes (existing key) | none | pack −20…40 MiB of page cache | none | **≈1.0 s snapshot [M]** + ≈3.3 s when `built` runs (2 clones) [M] | none, if collected, passed and skipped counts match | S: `assay.toml` both lanes, `test_self_lane.py:102`, comment, decision note, R0/R1 drift proof |
| `assay/`-only worktree (new `snapshot_selection` value), on top of shallow | needs code | none | tree 81 → 23 MiB | none with A-472; **amends A-161/A-269 §2** | **further ≈1.0 s [M]** (1.50 → 0.46) | a sibling read would fail loudly in R1; an existence-conditional read would be silent, so the pilot must compare per-test outcomes | M: config, verdict, `_verify` skip set, named extra path, decision |
| C1 refresh (P5) | yes | none | none | none (already A-472) | **0.1–0.2 s [M]** full; ≈0.03 s with `assay/`-only [C] | neutral; forces the C4 sweep and the config pins | M/L (P5) |
| C2 delta closure (P5) | yes | none | none | none | ≈0.33 s full [M proxy]; **≈0 with shallow** (walk already O(tree), 0.03 s) | neutral | M |
| reflink `TMPDIR` + template copy | **no** (ext4 / overlay2) | **host change**: new XFS/btrfs LV plus bind mount (production host); no container privileges | none extra | permitted by A-184; template never executed | 0.5–1.0 s full [A] + no 130 MB writeback | neutral | M (host ops + run-gate mount + Design B) |
| tmpfs `TMPDIR` (`--tmpfs`) | **no** (no run-gate key) | run-gate code change; no privileges | **+140–170 MiB per live snapshot (full), ≈60 MiB small**, swap-backed, in the 2 GiB cap | none | 0.3–0.8 s full, 0.1–0.2 s small [A] | neutral | S/M |
| eatmydata / `core.fsync=none` | eatmydata absent | lane env change (A-019) | none | none | **0 snapshot [M]**; ≤0.16 s test-side [M] | eatmydata alters the measured environment | S, but pointless |
| Base once plus a per-candidate plain copy (Design B, no reflink) | yes | none | none | allowed as C4 under 6 conditions | **≈0 or negative [M]** (cp −a 0.6–2.7 s vs 0.8–1.3 s write) | neutral | M |
| overlayfs per candidate | **no** | `CAP_SYS_ADMIN`/AppArmor or userns on the gate container (production host) | small | **violates "tree reuse"**; G1 must be re-specified; lower tamperable | ≈2 s [A] (to ≈0.5 s) | needs an A-472 amendment | L |
| fuse-overlayfs | **no** | `/dev/fuse` + `CAP_SYS_ADMIN` | small | same as overlay | likely a net loss [A] | same | L |
| alternates / `--shared` / `--reference` / hardlinks | refused by `_verify` | none | small | **forbidden** (A-184/185/194) | 0.01–0.28 s [M] | breaks isolation | ✗ |
| No git repository (plain directory) | – | none | – | breaks A-186/A-195 | – | 11 fail, 33 skip silently | ✗ |

---

## 4. Q4: Ranked recommendation, and what becomes of P5

1. **Shallow snapshot for `self-qualification` and `self-qualification-preflight`** once A-468(a) (`--ignore=tests/test_python_qualification.py`) is merged. This is plan §11 item 9, currently unowned.
   - **Needs:**
     - `snapshot_history = "shallow"` in both lanes. They must stay identical (`test_self_lane.py:241-246`).
     - Update the pin at `test_self_lane.py:102` and the comment at `assay.toml:73-74`.
     - A short decision record: "B105 no longer needs ancestry; A-451 default applies".
     - A **drift proof**: one R0/R1 preflight on the shallow shape showing the same collected, passed and skipped counts as full, with `test_distribution_build_release.py`'s 33 cases **run, not skipped**, and `test_b105_report_check.py`'s 11 passing.
     - No host change, no privileges, no A-472 conflict.
   - **Buys:**
     - ≈1.0 s per candidate snapshot [M];
     - ≈3.3 s per candidate that reaches the `built` fixture [M], which is every survivor;
     - about 20–40 MiB less pack I/O per candidate, ≈75–150 GiB per campaign [C].
   - **Risk:** setuptools-scm's shallow warning and version form inside the `built` clone are [I]. The drift proof covers them.
2. **An `assay/`-only worktree** as a new `snapshot_selection` value. A-269 names "a new `snapshot_selection` enum value" as the sanctioned escalation path.
   - **Needs:**
     - a decision amending A-161's "complete topology" and A-269 §2 ("no arbitrary exclusions");
     - schema and verdict recording;
     - `_verify`'s skip-set proof generalized from the three leaves to the declared subtree complement;
     - either materializing `run-gate-project/run-gate.py` or accepting the dangling tracked symlink `assay/run-gate.py`.
   - **Buys:** a further ≈1.0 s per candidate [M] and 62 MB less written per candidate.
   - **Claim risk:** only static evidence says no test reads siblings, so require a per-test outcome comparison against the full worktree in the pilot, not only counts.
3. **A reflink scratch filesystem**, only if the pilot shows I/O or writeback contention matters. It is a production-host change (new LV, not a loop file) plus the optional C4 template. A-184 already permits it.
4. **tmpfs**, only at the shallow + `assay/`-only footprint and with the P4 worker count checked against the 2 GiB cap.

   **Do not pursue:**
   - eatmydata (nothing to save);
   - overlay or fuse-overlay (privileges, and an A-472 amendment, for no gain over item 2);
   - alternates or hardlinks (forbidden);
   - plain-directory copies (breaks invariants).

**What P5 becomes:**
- **If items 1 and 2 are adopted:**
  - **C2 is moot for B105.** A shallow child's full closure walk is already O(tree), 0.03–0.04 s [M]. Keep it only if another full-history consumer needs it.
  - **C1 is reduced:** the hash pass over 23 MB costs ≈0.09 s, so the net saving is ≈0.03 s [C]. It is not worth its coupled cost:
    - the C4 ctime sweep;
    - the three `_FIXED_CONFIG` pins;
    - the coverage-exclusion fixture regeneration;
    - 13 oracles.

    Defer it.
  - **The C4 sweep's detection of assume-unchanged/skip-worktree blinding** is an existing hole ("That hole already exists today", `P5-snapshot-costs.md:103`). Consider it separately as an integrity fix, decoupled from C1.
  - **P0's G1–G5 guards stay necessary** under every option.
- **If only item 1 is adopted:** C2 is moot, and C1 is worth about 0.1 s. Deferring P5 entirely is reasonable until the pilot's snapshot share is known.

---

## 5. Corrections to earlier records

- **Container `/tmp`:** it is overlay2 on ext4, not plain ext4 (analysis §7.2, P5 environment item). The no-reflink conclusion stands. `TMPDIR` affects assay's scratch only, not the tests' `tmp_path`.
- **"62 MiB of packs":** this depends on the source repository's packing. From this clone the full closure packs to 43.4 MB. The shallow closure is 23.4 MB.
- **C1's saving:** the refresh is itself one full hash pass, so C1 removes one of two hash passes. Net saving measured here: 0.1–0.2 s, not 0.25–0.85 s.
- **The `assay.toml:73-74` rationale for `full`** becomes false after A-468(a). `test_self_lane.py:102` pins it.
- **"≈68 tests need the parent repository":** after the ignore, the real-repository users are 17 functions / 24 cases, none needing history. A missing repository makes 33 cases **skip silently** and 11 **fail**.

## 6. Caveats

- All timings come from one host at load 10–13, with n=2–3 and page-cache effects (the pack copy ranged 0.01–0.28 s). They are directional.
- The probe omits P22's Python validation, so P22's absolute cost is higher; the analysis estimates 2–4 s.
- tmpfs, reflink and overlay costs are estimates; no such filesystem was mounted.
- Every "PASS under shallow or `assay/`-only" in §1.2 is [I] from static reading plus the clone probe. The drift-proof preflight is the evidence that counts.
