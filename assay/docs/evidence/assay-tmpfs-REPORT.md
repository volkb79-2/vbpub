<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-02; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/assay-tmpfs-REPORT.md -->
<!-- relation: tmpfs worker evidence; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# assay-tmpfs (B223) report, 2026-11-04

Branch `assay-tmpfs-20261105` (worktree `<worktree>`).
Commits: `14930488a` (feature), `4611221b3` (fix: `_tmpfs_worker.py` added to the three whole-source target lists in `assay/assay.toml`).

## Item 1: full unit validation through remote-gate: FIXED / PASS
- Control, main `e7327da2f`, <host>, `assay-base-1`, lane tester-unified: `verdict PASS; exit_code 0` (so the
  `fail-1` quarantine test / ASSAY_B110_ASSAY_STATE_DEVICE issue is NOT present on main at <host>; no env backlog item filed).
- Branch `14930488a`, <host>, `assay-tmpfs-1`: `verdict FAIL`, 85 failures, ALL one root cause: `test_b105_whole_source_target_lists_cover_every_production_module`
  and the B105 report-check / self-lane tests (`judgment.r1.targets ... missing ['src/assay/_tmpfs_worker.py']`). Real branch defect, no remote-env
  failure.
- Fix `4611221b3` (assay.toml, 3 lists). Local re-run of gate/tests/test_self_lane.py, gate/tests/test_b105_report_check.py,
  tests/core/test_b105_runner_boundaries.py: 278 passed. dataclass-contract fixture needs no change (no new dataclass; IsolationConfig fixture lists only special fields; 38 passed locally).
- Branch `4611221b3`, <host>, `assay-tmpfs-2`, lane tester-unified: `verdict PASS; exit_code 0` (the lane enforces the 100% line+branch coverage of src/assay incl. isolation.py and _tmpfs_worker.py).

## Item 2: measured cow vs tmpfs-worker comparison: OPEN (not measured)
No numbers. Findings that bound the next attempt:
- run-gate has no `--shard`; `assay run --candidates-file` is pilot-only and requires `--pilot-jobs 1`, `--state-dir`, no verdict; `max_mutants` is a ceiling (exceeding it fails), not a truncation, so a lane cannot be shrunk by config.
- The only committed bounded-subset path is the `analysis-r2-pilot` run-gate lane (`assay/run-gate.toml`, `assay/tools/self-qualification-container.sh`, selector `assay/tools/analysis_r2_pilot_select.py`, 140m budget, bare-host). Its own `docker run` (no `--shm-size`, so /dev/shm is 64 MiB) is where an RG-163-style shm key / bind-mount would have to be applied; that script is assay's, so the experiment needs a temporary local edit of the throwaway ON arm only.
- The earlier scratch ON-arm worktree `assay-tmpfs-exp-on` no longer exists; the scratch wrapper `rg_shm.py` (RUN_GATE_EXTRA_MOUNTS) is not applicable to the pilot lane's own docker run.
- Suggested next step: two committed throwaway branches (OFF = branch as is; ON = `[lanes.analysis-r2.isolation] materialize="tmpfs-worker", tmpfs_root=<bind-mounted host tmpfs>, tmpfs_max_mib=400` plus a `-v /dev/shm/<dir>:<path>` in the container script), run `analysis-r2-pilot` back to back on idle <host> (PSI cpu avg10 was 0.08 at 17:56Z), compare `progress-analysis-r2-pilot.jsonl` phase seconds, io.stat rbytes, and killed/survived buckets; confirm no "tmpfs-worker' is unavailable" advisory.
- <host> and <host> `/dev/shm/assay-tmpfs-exp` no longer exist (verified absent; nothing to remove).

## RG-163 (shm/tmpfs size lane key): left filed (not small: touches run-gate container creation and the self-qualification script).

## Deviations / notes
- Early editing of config.py used a python rewrite (earlier session); scratch wrapper outside the repo only.
- No new backlog ids. No push/merge/tag.

## Measured comparison (2026-11-05, <host>) — Item 2 now MEASURED

Setup. Throwaway branch `assay-tmpfs-exp-20261105` (worktree + branch deleted by exact name; never merged) = `4611221b3` + libraries/worktree lane `worktree` with a fixed `base` (changed-line plan, 76 candidates) and `assay run worktree --shard 1/3` = the SAME 28 candidates in every arm (no run-gate: a direct `docker run --name exp-assay-tmpfs-<arm> --memory 2g --cpus 3 --cgroup-parent dev-gates.slice --tmpfs /dev/shm:size=1024m tester-unified:r25-e92d43b28b00`, checkout bind-mounted, `MDT_COW_ROOT=/srv/cow/mdt-cow` bind for CoW arms, cgprofile observer socket + `--group-add 986`, DOCKER_CONFIG=$HOME/.docker-nocreds; assay run from the branch source via PYTHONPATH). OFF = `isolation.materialize` default (cow); ON = `tmpfs-worker`, `tmpfs_root=/dev/shm`, `tmpfs_max_mib=900`. Whole vbpub repository snapshot, tests = libraries/worktree pytest (about 4.5-7 s per candidate). Per-arm container cgroup sampled every 2 s (memory.peak, io.stat, cpu.stat; values at last sample, within 2 s of the end). Data/scripts: `reports/assay-tmpfs-exp-data/` (progress NDJSON, cg-last, arm.sh, analyze.py).

Caveat: <host> was NOT idle (3 other run-gate lanes, PSI cpu some avg10 8-28, load 3.6 on 4 vCPU); the first j1 pair (214.7 s vs 176.6 s) shows how big the noise is, so only the repeated pairs and the phase seconds are used for conclusions. A truly idle host was not available.

| Arm | cow area | jobs | exit | total wall s | cand wall sum s | mean/cand s | materialize mean (median) s | rbytes MB | wbytes MB | peak mem MB | CPU s | buckets |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| off-j1 | yes | 1 | 0 | 214.7 | 180.6 | 6.45 | 0.121 (0.119) | 162 | 510 | 606 | 140.5 | 28 killed |
| on-j1 | yes | 1 | 0 | 176.6 | 141.9 | 5.07 | 0.100 (0.058) | 170 | 418 | 886 | 134.7 | 28 killed |
| off-j1b (repeat) | yes | 1 | 0 | 174.5 | 144.1 | 5.15 | 0.091 (0.085) | 165 | 513 | 567 | 127.4 | 28 killed |
| on-j1b (repeat) | yes | 1 | 0 | 174.7 | 142.3 | 5.08 | 0.101 (0.060) | 237 | 451 | 845 | 131.3 | 28 killed |
| off-j3 | yes | 3 | 0 | 100.5 | 215.3 | 7.69 | 0.136 (0.124) | 175 | 503 | 643 | 128.4 | 28 killed |
| on-j3 | yes | 3 | 0 | 108.6 | 222.1 | 7.93 | 0.413 (0.109) | 123 | 382 | 1316 | 136.3 | 28 killed |
| off-j1-nocow | NO | 1 | 0 | 223.7 | 191.4 | 6.84 | 1.591 (1.641) | 0 | 5614 | 585 | 188.0 | 28 killed |
| on-j1-nocow | NO | 1 | 0 | 206.7 | 176.9 | 6.32 | 1.397 (1.379) | 0 | 5857 | 575 | 183.8 | 28 killed |
| off/on-j3-nocow | NO | 3 | 2 | refused | - | - | - | - | - | - | - | BAD_LANE_CONFIG |

(CPU s = container cgroup usage_usec; the "cow area yes" runs read the 36 MB-class pack from page cache so rbytes is small and noisy, +/- 50 MB between identical arms.)

Bucket identity: all 28 candidate ids and verdict buckets (all killed) are identical across ALL completed arms (OFF/ON, jobs 1/3, with/without cow). Honest limit: the set contains only killed candidates, so it proves no survivor-versus-killed identity beyond that.

Findings.
1. tmpfs-worker engaged (probe: `/dev/shm` held one `w-*` tree per worker, 531 MB for 3 workers = about 177 MB each) and no "unavailable" advisory appeared.
2. No wall-clock gain on <host>: with a btrfs CoW area the clone path already costs about 0.1 s per candidate (primed index, B220/B222), so the best case saving is about 0.05 s of a 5-7 s candidate (below 1 percent); the steady-state tmpfs materialize median is 0.06 vs 0.09-0.12 s. Totals: j1 repeat 174.7 vs 174.5 s, j3 108.6 vs 100.5 s (ON slower: each worker pays a first-tree copy of 1.0 s (j1) to 2.6-2.9 s (j3, three concurrent copies) before its first candidate). The earlier "6.0 s to 3.2 s per candidate" figure is from the synthetic harness before the clone path was shrunk and is not reproduced in the real lane; the B223 status text is corrected.
3. Disk traffic: ON writes 18-25 percent fewer bytes (418/451 vs 510/513 MB at j1, 382 vs 503 MB at j3), read bytes are not reliably lower (123 vs 175 MB at j3, but 237 vs 165 MB in the j1 repeat). CPU seconds equal within noise.
4. Memory: peak memory +280 MB (j1) to +670 MB (j3) because tmpfs pages are charged to the container cgroup; with the 2 GiB cap and a 1 GiB /dev/shm this ran without OOM (j3 peak 1.32 GiB).
5. Dependency on the btrfs cheap-clone: tmpfs-worker is NOT independent of it. `_acquire_worker` returns None unless the workspace is a cheap-clone one, so on a host/container WITHOUT `/srv/cow` the ON arm is a silent no-op (j1: materialize 1.4 s vs 1.6 s, the plain-snapshot copy, 5.6 GB written per run; the small difference is noise) and no advisory is printed (the design says one advisory). jobs > 1 is refused regardless of materialize mode: `native R2 --jobs greater than 1 requires a cheap_clone snapshot workspace; configure the supported btrfs CoW workspace or pass --jobs 1` (BAD_LANE_CONFIG, exit 2, both arms). So tmpfs-worker cannot replace the CoW area for parallel lanes.
6. Lane config note: `jobs` and `jobs_max` are aliases (declaring both is an error); `--jobs N` needs `jobs_max >= N`.

Recommendation.
- Default stays `cow` (OFF). Do not enable tmpfs-worker on btrfs hosts with the current clone path: no measurable wall gain, extra memory, first-tree copy cost.
- Where it could still help: a host with a CoW area but slow or contended disk where clone materialize is not about 0.1 s, or when disk write volume must drop (about 20 percent fewer bytes written). Memory sizing if used: `tmpfs_max_mib` per worker tree (this repo: about 177 MB, set 300-400) times jobs, plus container `/dev/shm` size (run-gate has no key: RG-163) and the container memory limit must cover workers x tree + normal peak (j3: 1.3 GiB peak, so >= 2 GiB cap and /dev/shm >= 700 MB).
- Fall back to cow (automatic): not a tmpfs, tree above `tmpfs_max_mib`, low free space, nested workspace; the no-cheap-clone case falls back silently (see follow-up).
- Follow-up (filed in B223 text, not new id): emit the advisory when `materialize = "tmpfs-worker"` is requested but the workspace is not cheap-clone; and if a non-btrfs host ever needs parallel lanes, the real lever is allowing jobs > 1 with tmpfs-worker trees as the clone substitute (currently refused), which would be a separate item (design change in the `cheap_clone` gate).

Cleanup: throwaway worktree and branch `assay-tmpfs-exp-20261105` removed; <host> `<home>/exp-tmpfs-20261105` and three `incoming/tmpfs-exp*-20261105.bundle` removed; all `exp-assay-tmpfs-*` containers removed; `/dev/shm` empty. <host> untouched this session.
