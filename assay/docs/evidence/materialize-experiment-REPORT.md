<!-- origin: 2026-11-02; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/materialize-experiment-REPORT.md -->
<!-- relation: materialisation experiment; verbatim source text follows -->

# Materialization-backend experiment (v1001, 2026-11-04/05)

Status: DONE. Raw data and harness: `reports/materialize-experiment-data/` (`results-main.jsonl`, `results-d2fix.jsonl`, `bin/matexp.py`, `bin/analyze.py`, `bin/micro.py`). The scratch dir `/srv/cow/exp-materialize-20261105` and every mount, subvolume and container it made are removed (checked: no mounts, no subvolumes, no `matexp-*` containers). Campaigns were not touched; the experiment ran at CPU PSI some avg10 <= 20 (gate in the harness), container `--cpus 1.5 --memory 1g --cgroup-parent dev-gates.slice`, host phases in transient scopes under `dev-gates.slice` at nice 10.

Evidence tags: [M] measured here, [F] read from code, [I] inferred, [U] unknown / not measured.

## 1. Answer to the question

The 15-30 GB (now 132 GB) of campaign container reads come from **Assay's own per-candidate git verification, not from tests**:

1. The sealed template is built with `git read-tree` and the worktree is written by Python (`_build`, `_write_worktree`). Its index therefore carries **no stat data**. [F, `isolation.py` `_build`]
2. Every git child runs with `GIT_OPTIONAL_LOCKS=0` / `--no-optional-locks`, so `git status` never writes a refreshed index back. [F, `git.py` `_REPLACEMENT_ENV`, `_p22_argv`]
3. Hence in every candidate `git status` (in `_verify` before the command and in `_snapshot_left_dirt` after it) compares each of ~7.6k files by **hashing its content** (127 MB worktree). [M: 0.45-0.49 s CPU on tmpfs, vs 0.04 s with a refreshed index]
4. Each clone is a new inode set, so its page cache starts **cold** (btrfs snapshots and reflinks share extents, not cache pages): the first `status` reads ~138 MB of file data from disk, plus the 36 MB shallow pack read by the closure walk (`rev-list --objects`, `cat-file --batch-check`). The second status (integrity) hits the cache just filled. [M]
5. Measured steady state of the current backend: **173.6 MB read per candidate** in materialize, 0 in command and integrity. [M]

Cross-check against the live campaign [M/D]: cli-extended r2 (job 3762) had 740 candidate rows and `rbytes=132.1 GB` when I read it: 178 MB per candidate, within 3 % of my 173.6 MB. Its `phase_seconds` were materialize 2.06-2.21 s, integrity 0.48-0.54 s, against my 2.31 s and 0.48 s for the same backend. The model reproduces the campaign. Average read size ~31 KB (4.2M IOs for 132 GB) is the file-by-file read pattern of `status`.

The tests do not matter for reads: the pytest subset read 0.0-0.8 MB in every variant.

## 2. Setup (what is and is not representative)

- Tree: vbpub commit `ba601c9e5`, **whole repository** (7,644 tracked files, 127 MB worktree; what `snapshot_selection = "repository-minus-unsafe-symlinks"` snapshots for cli-extended), shallow single-commit `.git` (36 MB pack), three unsafe symlinks omitted with skip-worktree, fixed mtimes. Built like `_build`: `read-tree`, worktree write, no index refresh. Verified: status clean. [M]
- Candidate loop (mirrors `_materialize_clone`, `_verify`, `_snapshot_left_dirt`): clone, `hash-object -w`, `update-index --cacheinfo`, `write-tree`, `commit-tree`, closure walk (2x `rev-list --objects`, `cat-file --batch-check`), rewrite file + utime, `rev-parse`, shallow read, `rev-list --count`, `status`, `write-tree`, `rev-parse HEAD^{tree}`, `ls-files -v`, manifest `lstat` walk; then pytest (`tests/test_config.py tests/test_audit.py`, 3 s) in `tester-unified:local` (user 1003, `--memory 1g`, `--cgroup-parent dev-gates.slice`, exact names); integrity (`status`, `ls-files --others`, `rev-parse HEAD`); teardown. Same git flags and environment as Assay (`_FIXED_CONFIG`, `_REPLACEMENT_ENV`).
- Metric: `rbytes` from the transient scope's cgroup `io.stat` (host phases) and from the container's own `io.stat` (command), cross-checked with `RUSAGE_CHILDREN.ru_inblock` (identical in all rows). Wall time secondary. No host-wide cache drop: each rep used a fresh pristine template with `fsync` + per-file `posix_fadvise(DONTNEED)`; candidate 0 is the cold case, candidates 1.. are steady state (reported). Variants were interleaved candidate by candidate in random order (seed 7), 9 variants.
- Not Assay itself: Assay's btrfs-only workspace cannot run the other backends, so the loop is a faithful re-implementation of its git/filesystem operations, not the product. Python overhead of Assay (GIL, ledger) is absent. [I: effect on absolute times small, reads exact]
- N: 20 candidates x 9 variants for rep 0 (complete); rep 1 stopped after 8-9 candidates per variant (I stopped the run: reads are deterministic and further reps add nothing). 27-29 steady rows per variant after pooling; 0 harness errors; all pytest runs rc 0. Variant (d2) was initially invalid (index primed before the copy to tmpfs, which changes inode numbers); re-run alone in `results-d2fix.jsonl` (8 candidates) with the fix. Times for (d)/(d2) in the table are from the d2fix run for d2 and the main run for d.
- Limits [U]: btrfs metadata (extent tree) reads are not dropped between candidates and are not separated in `rbytes`; only 3 s of tests; one project.

## 3. Results (medians, steady state, per candidate)

Bytes are read from the block device (cgroup `io.stat`). "total s" = materialize + pytest + integrity + teardown.

| Variant | materialize read MB | command read MB | integrity read MB | materialize s | integrity s | teardown s | total s | page cache charged in materialize MB | tmpfs MB held | privilege / complexity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| (a) btrfs snapshot, current Assay [M] | **173.6** | 0.0 | 0.0 | 2.31 | 0.48 | 0.08 | 6.03 | 182 | 0 | in use; root-free via cowtree ioctl |
| (a2) btrfs snapshot + template index refreshed once [M] | **37.2** | 0.8 | 0.0 | 0.33 | 0.07 | 0.03 | 3.53 | 37 | 0 | one `update-index --refresh` before seal |
| (b) plain copy (`cp -a`) [M] | 0.0 (source cached) | 0.0 | 0.0 | 1.99 | 0.47 | 0.34 | 6.35 | 183 | 0 | none; **writes 120 MB per candidate** |
| (c) `cp --reflink=always` [M] | 173.6 | 0.0 | 0.0 | 2.68 | 0.47 | 0.37 | 6.77 | 182 | 0 | none (btrfs only) |
| (d) tmpfs per worker, restore the changed file, index as built [M] | **0.0** | 0.0 | 0.0 | 0.59 | 0.48 | 0.00 | 4.33 | 1 | 175 per worker | needs a mount (root) once per worker |
| (d2) same, index refreshed inside the tmpfs [M] | **0.0** | 0.0 | 0.0 | 0.16 | 0.06 | 0.00 | **3.22** | 1 | 175 per worker | as (d) |
| (e) overlay, lower = template, upper+work on tmpfs [M] | **0.0** (174 on cold candidate 0) | 0.0 | 0.0 | 0.81 | 0.53 | 0.03 | 4.69 | 37 shmem | 37 per live candidate | mount per candidate: root on host |
| (e2) overlay on tmpfs, template index refreshed [M] | **0.0** (37 on cold candidate 0) | 0.0 | 0.0 | 0.29 | 0.10 | 0.03 | 3.63 | 37 shmem | 37 per live candidate | as (e) |
| (f) overlay, upper+work on btrfs [M] | 35.9 | 0.0 | 0.0 | 0.84 | 0.52 | 0.06 | 4.56 | 37 | 0 | as (e) |

Command phase: 3.0-3.3 s and ~67 MiB peak in every variant (no effect). IQR of total s per variant is within about +-0.5 s; PSI CPU max 20 in rows, so times are indicative, bytes are exact.

Extra [M]: refreshing the index cuts the template-cost of `git status` from 0.45-0.49 s to 0.04 s (tmpfs microbenchmark), `git diff --name-only` 0.02 s, a Python stat-walk of the 8,653 files 0.05-0.06 s.

Notes on the table:
- Why (a) and (c) read 173.6 MB and (e)/(b) do not: after the first candidate, the overlay lower files and the plain-copy source are in the page cache and are shared/reused; a snapshot or reflink clone is a different inode set whose cache is empty. [M]
- Why (e) shows 37 MB shmem: it appears to be the pack being copied up to the tmpfs upper layer. Cause not verified [U]; (f) does not show it in written bytes. Treat 37 MB per live overlay candidate as a measured cost.
- (b) is the only variant writing 120 MB per candidate (disk writes, flushed later) and the slowest teardown.
- Memory per worker: tmpfs variants hold 175 MB (whole repo + `.git`) resident; 3 workers 0.5 GB of 8 GB. A worktree-only tmpfs of just `libraries/cli-extended` is 3 MB and ran the pytest subset (rc 0) with no `.git`; the full `tests` suite fails one test (`test_docs.py::test_library_document_markdown_links_and_anchors_resolve`) because it reads repo-root documents. So for tests alone the project directory plus the documents it links is enough; Assay's verification is what needs `.git` and the full tree. [M]

## 4. Operator questions

### 4.1 Pure tmpfs, swap and zswap

- A tmpfs holding the worktree and `.git` works (variants d, d2): 0 disk reads, 3.2-4.3 s per candidate. [M]
- zswap on v1001: `enabled=Y`, compressor zstd, `max_pool_percent=25`, 4 x 4 GiB swap partitions (16 GiB), `vm.swappiness=10`; counters live (zswpout 168k, zswpin 98k, `Zswapped` 179 MB at the time). So tmpfs pages are swappable and compressible. [M]
- Accounting [M for charging, I for the lane case]: tmpfs pages are charged to the cgroup of the process that first wrote them (my scope's `memory.stat` showed `shmem` 37 MB for the overlay upper). In a lane the writer would be Assay inside the lane container, so tmpfs counts in that container's `memory.current` and is limited by `--memory`; swapped pages count in `memory.swap.current` against `memory.swap.max`. The slice has `memory.high` 4.3 GB, `memory.max` 5.9 GB, `memory.swap.max` 13 GB. [I: not tested with a live lane]
- Sizing: per worker ~175 MB (whole-repo snapshots) plus the candidate's own anon (~270-380 MB RSS measured earlier): 3 workers ~1.6-1.7 GB, fits the 5 GiB lane limit used by iobench. If the tmpfs is mounted outside the container (host mount bind-mounted in), pages are charged to the mounter, not the lane limit; decide which is wanted. Swap-out under pressure is a benefit only while memory PSI is low; it adds a decompression stall per touched page. [I]

### 4.2 One pristine tree per worker, restore the changed file, cheap safety check

Variants (d)/(d2) are exactly this: candidate = replace one file (+ new loose objects, index, HEAD), run tests, restore the file, the index and HEAD from pristine copies, and delete the new loose objects. Cost: teardown ~0.00 s. [M]

Safety check options, cost measured on the 8,653-file tree [M]:

| Check | Cost | Catches | Misses |
|---|---:|---|---|
| `git status` with the template index as built | 0.45-0.49 s | any content change of tracked files, untracked non-ignored files | ignored files (pyc, caches, coverage if ignored) |
| `git status` with a refreshed index | 0.04 s | same | same; a changed file whose size+mtime+ctime+inode are all unchanged (not possible after a rewrite: ctime moves) |
| `git diff --name-only` (refreshed index) | 0.02 s | tracked modifications | untracked, ignored |
| `git ls-files --others --exclude-per-directory=.gitignore` | 0.03 s | untracked files | ignored |
| Python stat-walk of the tree vs a stored map (size, mtime_ns, inode, mode, dir entry counts) | 0.05-0.06 s | everything incl. ignored files and new/removed files | content changes that keep size, mtime and inode (a test that rewrites a file in place and resets mtime) |

When the check can be wrong: tests that write ignored or generated files (`__pycache__`, `.pytest_cache`, `.coverage`, `coverage.json`, `.hypothesis`, tool caches) pass `git status` but are residue for the next candidate and are caught only by the stat-walk or by redirecting them: run with `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `COVERAGE_FILE` and any report path outside the tree (the lanes already set the first). With in-place reuse a leaked file does not just fail the check, it **contaminates the next candidate** (a survivor can pass because of a stale artifact). A refreshed index is also wrong if priming happens before the copy that defines its inodes (my d2 mistake): prime inside the final location. In my tests the default-environment pytest left `git status` clean (ignored files) [M]; that ignored files were in fact created is [I].

### 4.3 When the verification can fail, and what is needed per candidate

Code read: `isolation.py` `_materialize_clone` / `_verify` (before the command) and `mutation.py` `_snapshot_left_dirt` (after) with `git.dirty_paths` and `git.head_rev`.

Checks and the failure class each guards [F]:

| Check | Guards against | Per candidate needed? |
|---|---|---|
| `rev-parse HEAD == commit`, `HEAD^{tree} == write-tree`, expected blob in tree | wrong or failed replacement commit (the bytes under test are not the intended mutant) | yes (it is the identity of the candidate), but only needs the touched path: ~3 git calls on a few objects |
| shallow boundaries, `rev-list --count`, no alternates, no hooks, config does not reference source | bad or tampered **template** (copied from the seed) | once per template (sealed read-only), not per clone |
| `status` clean + `write-tree` == HEAD tree + skip-worktree set == omissions + manifest `lstat` walk | bad clone (partial snapshot, corrupted copy, wrong mode), template mutated by an earlier worker | once per template at seal time; per clone only if the clone is not a CoW snapshot of the verified sealed template (plain copy, overlay setup error). A btrfs snapshot of a read-only sealed subvolume cannot differ from it. |
| `_enforce_child_closure` (2x `rev-list --objects`, `cat-file --batch-check`) | the replacement pushing the object count/size past declared limits | only the **new** blob, trees and commit change; base closure is the seed's, checked once. This is also the 36 MB pack read per candidate. |
| after command: `dirty_paths` (status + untracked scan) and `head_rev` | a test or mutant that edits tracked files, adds files, or commits inside the snapshot, i.e. the result no longer represents its commit (A-195, `DIRTY_TREE` / `HEAD_CHANGED`) | **yes every candidate**: this is the only check against the code under test itself, and the only one that cannot be amortised. But it can be cheap (see below). |

Can the pre-command verification fail in practice? For a btrfs snapshot of a verified sealed template: only through a harness bug or a replacement bug; I found no failure in about 250 candidates here and the campaign progress files give no `DIRTY_TREE`/`GIT_FAILED` signal I could attribute (not checked exhaustively [U]). The post-command check fails when a test dirties the tree; that is real and must stay.

Proposal (correctness argument in each line):

1. **Prime the template index once** (`git update-index --refresh` before `seal`, with locks allowed, in the template only). Clones that preserve inode numbers and timestamps (btrfs snapshot, measured a2; overlay with the template as lower, measured e2) then validate by stat. A clone whose stat data does not match (copy, reflink) falls back to hashing, which is slower but still correct, so the change is safe by construction. Effect: 173.6 -> 37.2 MB read and 2.3 -> 0.33 s materialize, 0.48 -> 0.07 s integrity. [M]
2. **Verify the template fully once per campaign** (at seal), then per candidate only the replacement identity (HEAD, tree, expected blob oid) and closure limits of the new objects. Argument: the clone is a CoW snapshot of the sealed read-only template, so its untouched content is the template's content; the replaced path is covered by the expected blob oid. Add an opt-in `verify_clone = "full"` (default today) so a new backend that is not CoW keeps the full proof.
3. **Keep the post-command dirt check per candidate but make it a touched-path check**: stat-walk against the template map (0.06 s) or `git diff --name-only` + `ls-files --others` on a refreshed index (0.05 s), full `status` every N candidates and after any candidate that crashed, timed out, was killed (leaked processes may still write) or hit a resource limit.
4. For in-place reuse backends (tmpfs per worker): the stat-walk is mandatory per candidate (ignored files matter), plus restore of file/index/HEAD/loose objects, plus a full verify at worker start and every N candidates.

Tests needed (assay): (a) primed-index template: status in a snapshot clone reads no file bodies (count `read` of worktree files via a fake or compare hash invocations) and still reports a modified file; (b) a test that writes a tracked file, an untracked file, an ignored file, commits, and changes mode: each is detected by the cheap post-check in the documented modes (ignored file only by the stat-walk); (c) crashed/timeout candidate triggers the full check; (d) replacement identity check fails when the wrong blob is written; (e) `verify_clone=full` preserves today's behaviour byte for byte; (f) in-place restore leaves tree, index, HEAD and loose objects equal to the pristine state (compare tree hashes); (g) a candidate that leaves a stale artifact does not influence the next candidate.

## 5. ZFS (documented from knowledge, NOT measured, not installed)

- OpenZFS clones of a snapshot share blocks; the ARC caches by block (DVA), so a clone's first read of a file whose blocks were cached through the origin dataset is a **cache hit**: this removes the cold-clone read amplification that btrfs snapshots show here. Rollback of a dataset to a snapshot is O(changed blocks), keeps inode numbers and typically stat data, so a refreshed index would stay valid. A per-worker "one dataset, rollback after each candidate" would give the restore semantics of (d) without tmpfs memory. [I from OpenZFS design]
- Cost of introducing it [I]: a kernel module (DKMS) for kernel 7.2 which may lag the kernel (a compatibility range is declared per OpenZFS release), CDDL licence, ARC competes with the page cache and needs sizing on an 8 GB host, a pool or zvol on a dedicated partition (btrfs on vda8 is the CoW root today), new root-privileged operations (`zfs clone/destroy/rollback`) and a new cowtree backend, new failure modes on host kernel upgrades. Not recommended: the measured fix (a2/e2) needs no new filesystem.

## 6. Recommendation

1. **Immediately (assay, no new backend): prime the template index (B219).** Removes 136 MB of 174 MB reads and 1.9 s of 2.3 s materialize on the current btrfs backend. Smallest change, safe by construction.
2. **Avoid the per-candidate pack read (B220):** closure limits only for new objects; the remaining 37 MB is the pack read by the closure walk.
3. **Cheaper, tiered verification (B222):** template verified once, per-candidate identity + touched-path check, full check every N and after anomalies. With 1+2+3 the per-candidate git work approaches the (d2) line: ~0.3 s materialize, 0.06 s integrity.
4. **New cowtree/assay backend modes (B221):** `overlay` (lower = sealed template, upper on tmpfs or btrfs) reached 0 steady reads even without index priming and 0.29 s with it; it needs a privileged mount, so it must be set up by the host side (run-gate/cowtree helper) and bind-mounted into the lane; unprivileged overlay in the lane container is **not** possible with docker's default seccomp and AppArmor (see 7) and works only with both disabled, which is not acceptable for lanes. `tmpfs-worker` (in-place reuse with restore, B223) is the fastest (3.2 s total) but needs the per-candidate stat-walk and contamination guards; use it only after tiered verification exists.
5. Do not use plain copy (b): same time as today and 120 MB of writes per candidate. ZFS: no.
6. dstdns/estate: expect candidate wall for cli-extended-sized trees to fall from ~6.0 s to ~3.5 s (a2) or ~3.2 s (d2), most of it the 3 s test command; the read load on the 300 IOPS-capped device from ~178 MB to ~37 MB or 0 per candidate.

## 7. Unprivileged overlay mount inside the lane container [M]

| Setting | Result |
|---|---|
| docker default (seccomp builtin + apparmor default), user 1003, `unshare -Urm` then `mount -t overlay` | `unshare failed: Operation not permitted` |
| `--security-opt seccomp=unconfined` | `cannot change root filesystem propagation: Permission denied` |
| seccomp unconfined + apparmor unconfined | overlay mount succeeds, file readable |
| host, user 1003, `unshare -Urm` + overlay | succeeds (user namespaces enabled, max_user_namespaces 31454) |

So a host-side (or setuid helper) mount is required for lanes.

## 8. Backlog filed (assay, next free ids after B218)

B219 (prime the template index), B220 (closure walk reads the full pack per candidate), B221 (overlay backend), B222 (tiered verification, `verify_clone`), B223 (tmpfs-worker in-place backend). Branch `backlog-materialize-20261105` in vbpub; dstdns doc branch `docs-materialize-20261105`. Commits (no push): vbpub `5678f4f6c` (worktree `/workspaces/vbpub/.worktrees/backlog-materialize-20261105`), dstdns `f1f041e5` (worktree `/workspaces/dstdns/.worktrees/docs-materialize-20261105`, section 3.5 of PARALLELISM-AND-PERFORMANCE.md).

## 9. Deviations

- Rep 1 was stopped after 8-9 candidates per variant (cleaned by exact path); pooled steady rows used. Not 3 reps.
- (d2) first run invalid, corrected and re-run alone.
- Not Assay itself running each backend (see section 2).
- Operator follow-up items 1-3 were addressed in 4.1-4.3 and the table; the "tmpfs holds only the git tree" question measured with a microbenchmark and a project-only tmpfs, not a full candidate loop.
