# Implementation map for cheaper B105 per-candidate snapshots

Read-only research on worktree `assay-b105-evidence-integrity` @ `db85f747`. Paths are relative to `assay/` unless marked `vbpub:`. I changed no files. I ran only cheap git commands under `nice -n 19 ionice -c3` on a host at load ≈7.5 of 8 cores, plus two throwaway probes in the scratchpad. All timings are indicative only.

## Summary

- **Cost today:** 18 P22 git processes per replacement snapshot plus 6 more in the post-command dirt check, 24 in total. B110 requires an average R2 cost under 4.8 s per candidate with one worker (`nyxloom-trove/4-backlog.md:11066`), and snapshots use most of that.
- **Biggest hidden cost:** both `git status` calls hash every tracked file's content. `read-tree` (`isolation.py:797`) leaves the index with zeroed stat data, and neither `_verify` nor the post-command status can write the index back (`GIT_OPTIONAL_LOCKS=0`, `git.py:139`). Measured: 0.28–0.89 s per status. After one `update-index --refresh` (0.29 s) the same status takes 0.04 s.
- **Second cost:** `_enforce_child_closure` walks the whole history for every candidate: 54,974 objects, 0.23 s for the walk plus 0.28 s for the size check. The same answer can be computed from the base closure plus a git-reported delta walk, measured at 0.023 s.
- **Test gap:** the disjoint-inode check (O2) exists only in `nyxloom-trove/carve-assets/P22/test_acceptance.py:266-309`. Neither the B105 lane (`testpaths=["tests"]`, `pyproject.toml:100`) nor the tester-unified gate script runs it; the script runs only the P26 and W1 acceptance suites (`tools/tester-unified-gate.sh:444, 577`). A hardlink "optimization" of `_copy_objects` would pass every collected test (`tests/test_b105_isolation_proof_boundaries.py:243` checks names and bytes only).
- **Stale bytecode is real:** on Python 3.14.7 I reproduced a stale `.pyc` being used for an `==`→`!=` mutant with the fixed mtime. Reusing any tree a command has run in is unsafe. The current fresh-per-candidate design has no reuse path.
- **Recommendation:** keep "fresh private repository per candidate". Add one index refresh (C1) and an incremental child-closure bound (C2) first; C3/C4 are optional after measurement. These two changes are local to `isolation.py` and remove about 0.6–1.4 s per candidate with every invariant intact. Anything larger (narrower worktree, shallow history plus pinned commits, reflink scratch) needs an operator decision or an environment change.

---

## 1. What each snapshot does per candidate (B105: full history, 3 omissions, no `link_paths`)

**Repository facts** (vbpub HEAD, measured):
- 5,298 tracked entries (26 symlinks, 3 omitted), 780 directories, maximum depth 13.
- 70.7 MB of blob bytes across 4,713 unique blobs.
- `assay/` alone: 960 files, 116 directories, 20.2 MB.
- 6,338 commits; the full closure is 54,974 objects.

### Per-candidate step inventory

| # | Step | file:line | git procs | Per-candidate need |
|---|---|---|---|---|
| 1 | Stale-site check: `_read_blob` runs `cat-file --batch` for 1 blob, compares bytes | `isolation.py:703-704, 729-760, 655-668` | 1 | Needed. Could compare `sha1(b"blob %d\0"+expected)` to `entry.oid` in Python, which is exact because A-185 refuses non-SHA-1 repositories (`test_isolation.py:973` "sha256"). |
| 2 | `mkdtemp` in `scratch_root`, `_track` | `:705-708` | 0 | Needed |
| 3 | `git init --template=<empty>` | `:770-776`, `git.py:1565-1621` | 1 | Once per seed (template `.git`, C3) |
| 4 | `_copy_objects`: `copyfile` of every seed pack file (≈62 MiB) | `:778, 1154-1166` | 0 | **Needed**: O2 plus A-184/A-194. A reflink would be allowed; a hardlink is not. |
| 5 | `_write_shallow`; full history means unlink | `:783, 1179-1189` | 0 | Trivial |
| 6 | `read-tree <commit>`: 5,298-entry index, 0.02 s | `:797` | 1 | Once per seed |
| 7 | `update-index --skip-worktree -z --stdin` (3 paths) | `:798-817` | 1 | Once per seed |
| 8–11 | `hash-object -w`, `update-index --cacheinfo`, `write-tree`, `commit-tree` (fixed identity) | `:820-839` | 4 | Needed: this is the child's identity (A-186, A-188) |
| 12 | Rebuild the 5.3k `_Entry` tuple | `:840-849` | 0 | Needed, cheap |
| 13 | `_enforce_child_closure`: full `rev-list --objects` (54,974), `cat-file --batch-check` over all of them, `rev-list --objects --no-walk` (5,411) | `:850, 970-1009` | 3 | The **bound** is needed; the full walk is not (see below) |
| 14 | `_write_worktree`: `cat-file --batch` of about 4.7k blobs (69.4 MB, 0.48 s); `mkdir(parents=True)` per entry; duplicate blobs via `copyfile`; `chmod` and `utime(_FIXED_MTIME)` per file and per directory | `:852-859, 1870-1957` (mkdir `:1883`, utime `:1887-1891, 1942, 1957`) | 1 | Output needed; the work could be a copy from a template (C4) |
| 15 | Write `HEAD` | `:860` | 0 | Needed |
| 16 | `_verify`: `rev-parse HEAD`; `_read_shallow`; `rev-list --count HEAD` (walks 6,338 commits, 0.06 s); `status` (**zeroed stat, so every file is hashed**); `write-tree`; `rev-parse HEAD^{tree}`; `ls-files -v -z`; manifest proof; alternates, hooks, config and project-root checks | `:861, 1011-1085` | 6 | Needed as proof; the status cost can be cut (C1) |
| 17 | `_plant_link_paths` | `:862, 912-914` | 0 | No-op for B105 |
| post | `_snapshot_left_dirt`: `dirty_paths` (status plus `ls-files --others`) and `head_rev`; each `git.run` adds a bootstrap `rev-parse` | `mutation.py:1843-1874` (called `:2758`), `git.py:826-905, 770, 563-590, 663-700` | 6 | Needed (A-195). Its status **hashes everything again**. |
| end | `_remove_owned_tree` (rmtree) | `:726-727, 1098-1113` | 0 | Needed |

### Measured costs

| Operation | Time |
|---|---|
| full `rev-list --objects` | 0.228 s |
| same plus `batch-check` | 0.282 s |
| `--no-walk` walk | 0.014 s |
| `HEAD --not HEAD~1` delta walk | 0.023 s |
| `cat-file --batch` of all HEAD blobs | 0.484 s |
| status on a zeroed-stat index | 0.887 s cold, 0.275 s warm |
| `update-index --refresh` | 0.291 s |
| status after refresh | 0.041 s |

A scratch probe confirmed three things:
- After `read-tree`, `ls-files --debug` shows `mtime: 0:0 size: 0`.
- `update-index --refresh` exits 1 with "needs update" when content differs (exit 0 with `-q`). Stat data is recorded **only for entries whose content matches**.
- A mismatched entry stays unrefreshed, and status still reports it `M`.

### Can the child-closure limit be computed incrementally?

Yes, exactly. The child's only parent is the base, and the seed's reachable set was proven equal to the source inventory at prepare time (`isolation.py:2048-2077`). So:

- closure(child) = `base_oids` ∪ (`rev-list --objects <child> --not <base>` − `base_oids`).
- The delta walk marks the base tree as uninteresting, so it costs O(tree), not O(history). It returns the child commit, 4–5 new trees for `assay/src/assay/...`, and the new blob.
- Subtracting `base_oids` handles a mutant blob that equals a historical blob. Git's negative walk only excludes the base **tree**, so without the subtraction that blob would be counted twice.
- Run `cat-file --batch-check` on the delta only, with fewer than 10 objects.
- The object-count and byte limits then become sums over the base plus the new objects.
- The tree-blob limit keeps the cheap `--no-walk` walk of the child (0.014 s) and looks up sizes in a mapping chain of delta sizes over the base judged-tree sizes. Store the base judged-tree sizes at prepare time from `judged_tree_oids` (`:2088-2102`).

The existing refusal test `test_isolation.py:1760` (child over `max_objects` = base count) still fires.

---

## 2. Isolation invariants and the tests that pin them

| Invariant | Enforced at | Pinned by (collected in B105?) |
|---|---|---|
| O2: disjoint inodes (source, seed, siblings) | `_copy_objects` copyfile `:1157-1166` | **Only** `carve-assets/P22/test_acceptance.py:266-309` (`_object_inodes` `:170-175`). **Not collected.** |
| Sibling writes don't cross; a later base never contains an earlier mutant | fresh `mkdtemp` per call `:705` | `test_isolation.py:1324`; acceptance `:305-306` (not collected); `test_mutation_isolation.py:261` (scratch empty afterwards) |
| No alternates | `:1068-1071`; seed `:2078` | `test_isolation.py:1043[alternates]`, `:973`, `:1014`; `test_b105_isolation_proof_boundaries.py:460`; `test_b105_git_process_boundaries.py:827, 888` |
| No hooks | empty template `git.py:1573-1578`; check `:1072-1074` | `test_isolation.py:1043[hooks]` |
| Config does not reference the source | `:1075-1079` | `test_isolation.py:1043[config]` |
| Exact shallow boundaries (none for full history) | `:1022-1039`, `:2040-2042` | `test_isolation.py:536, 565, 587`; `test_b105_isolation_proof_boundaries.py:107, 121, 132, 156, 162, 389` |
| Skip-worktree set = declared omissions; index tree = HEAD tree | `:1050-1066` | `test_isolation_unsafe_symlink_omissions.py:181, 533, 567, 620, 640, 670` |
| Manifest proof (leaf kinds; omissions absent) | `_prove_manifest_materialized` `:431-474` | `test_isolation.py:1104-1177` |
| Fixed mtime and mode on files and directories (`_FIXED_MTIME` `:67`) | `:1887-1891, 1942, 1957` | `test_isolation.py:1598`; `_assert_combined_worktree` `:1427-1441` (used by `:1444`) |
| Untracked `__pycache__` in the source is never materialized | manifest-only writes | `test_isolation.py:309` |
| Deterministic child OID | `git.py:1147-1159` | `test_isolation.py:450` |
| Child closure bounded, not only the base | `:970-1009` | `test_isolation.py:1760` |
| Dirt and HEAD-change after a mutant | `mutation.py:1843-1874` | `test_runner_p23_combined_axis_review.py:625, 1074` |
| Budget per P22 call | `_P22Deadline` `git.py:1184-1211` | `test_isolation.py:420` |

### Decision constraints (`nyxloom-trove/decisions.md`)

- **A-119** (`:372`): "Both the baseline run and every per-mutant run call `runner.execute_command` UNMODIFIED … Only `cwd` varies … (`cwd=<its own scratch copy>`, A-120)."
- **A-145** (`:428`): "A repo-relative path and a project-relative path are two different spellings, and every boundary that crosses between them says which one it speaks."
- **A-160** (`:453`): "`lane.budget` bounds the whole Assay lane invocation, not each subprocess … Every baseline, mutant, control, transform, snapshot … consumes one shared budget."
- **A-186** (`:504`): "P22 parses raw tree objects … writes one deterministic `commit-tree` child … no checkout, archive, `git commit`, hook, filter, ambient identity, source write, or hardlink is permitted."
- **A-193** (`:516`): "The deadline … supplies a newly observed positive remainder immediately before every P22/process boundary."
- **A-195** (`:518`): "A disposable snapshot prevents consumer writes but does not excuse a unit that changes its named Git state."
- Related:
  - A-120 (`:373`): "fresh scratch directory per mutant … discard the copy afterward — never in-place-with-a-lock".
  - A-161 (`:454`): "ignored/untracked files are not implicit inputs".
  - A-184 (`:502`): "independent pack copies/reflinks with distinct inodes keep hostile consumer commands from affecting siblings".
  - A-194 (`:517`): "Hardlinks remain forbidden".

### Design A: reuse one materialization per worker, swap one file, restore, re-verify

This **violates**:

- **A-120 / A-161 / A-184 / A-195 (disposable, independent state):**
  - Ignored residue from the previous candidate survives: `__pycache__`, `.pytest_cache`, the lane's own `.assay/coverage-self-qualification.json`.
  - The dirt check cannot see it, because it deliberately consults only committed `.gitignore` plus status (`git.py:826-905`, A-177).
  - Re-running `_verify` proves nothing about freshness: `_prove_manifest_materialized` checks that manifest leaves exist, not that extra paths are absent.
- **`.git` carry-over from a consumer:**
  - `.git/info/exclude` hides later dirt. `git.py` states it is repository content "deliberately NOT worked around"; that is safe today only because each `.git` is fresh.
  - A `[filter]` clean driver added to `.git/config` would be executed by the **next** `_verify` status, because status runs clean filters while hashing. That breaks A-186's "only git plumbing assay chose".
  - `_verify` checks only hooks, alternates and source paths in config (`:1068-1079`).
- **Stale bytecode** (section 4). This yields false kills, i.e. an inflated score and a false PASS.
- **Pinning tests:** `test_isolation.py:1324`; `test_mutation_isolation.py:261`; the acceptance O2 test, if it were collected.

### Design B: copy a verified pristine tree (copy, not hardlink), swap one blob, rebuild the index

This **keeps** every invariant: distinct inodes (copy, or a reflink allowed by A-184), no alternates, hooks or config drift (re-checked per candidate), shallow file, skip-worktree set, manifest proof, fixed mtime, fresh-per-mutant, raw-object provenance (the template was written by `_write_worktree`, never checked out), and the deterministic child. It does so only under five conditions:

1. **Copy by explicit manifest and file list, not `copytree`.** A same-uid command can reach and write the template: `scratch_root/assay-p22-seed-*` sits beside the snapshot root, so the seed is "unexposed" by name only. `copytree` would spread a planted `__pycache__/*.pyc`, `.git/info/exclude` or `hooks/*` into every later candidate, and `_verify` would not notice ignored extras.
2. Symlinks must be copied as symlinks (`symlinks=True` semantics), otherwise `:450-454` fails.
3. The swapped file must be re-`utime`d to `_FIXED_MTIME`.
4. **Do not copy a refreshed index.** Inode and ctime change on copy, so run the per-candidate hash pass (refresh or status). That pass is the **only** per-candidate proof that the written bytes match the OIDs, and it is also what detects a tampered seed or template.
5. The template is never yielded and never has a command run in it.

---

## 3. What the B105 tests need from outside `assay/`

**Vocabulary:** `SNAPSHOT_SELECTIONS = {"repository", "repository-minus-unsafe-symlinks"}` (`config.py:226-233`, "There is no third value"). `SNAPSHOT_HISTORIES = {"shallow", "full"}` (`:238`). No selection narrower than the whole repository exists, and A-269 §2 forbids "arbitrary file or directory exclusions" (quoted at `isolation.py:1826-1828`).

### Tests that depend on the repository

| Module (test defs) | Needs | Evidence |
|---|---|---|
| `test_python_qualification.py` (32; module-level `requires_parent_repository` `:46`) | `REPO_ROOT` must be the git top level, **and historical commit `9f522a72…` plus its `topos` tree**. This is the reason for `snapshot_history="full"`. | `gate/python/qualify_topos.py:31-32, 346 (git archive INPUT_REVISION .gitignore topos), 367, 655-662`; tests `:241-326` |
| `test_distribution_build_release.py` (32; `:49`) | `REPO_ROOT` repository; `git clone --no-local` of it plus a sparse checkout of `assay` | `gate/distribution/build_release.py:168-176, 191, 247`; fixture `:368-382` clones **twice per candidate**. With full history, each clone packs the whole snapshot history, a test cost that scales with snapshot scope. |
| `test_b105_report_check.py` (4) | `rev-parse HEAD`, `HEAD^{tree}` at `REPO_ROOT` | `:23-31, 112-114`; `tools/b105_report_check.py:47` |
| `test_runner_snapshot_selection.py` (2) | tags plus `c56a13ea` | `:809, 863, 906`; **deselected** by `assay.toml:85-86` |
| `tests/qualification/*` (9) | skipped unless enabled by environment | `test_go_r1_real.py:54-79` |

`requires_parent_repository` is defined at `tests/conftest.py:326-334`; `REPO_ROOT = PROJECT_ROOT.parent` at `:215`.

**Numbers:** the lane runs 5,831 tests per candidate (`4-backlog.md:11032`); `tests/` has 3,795 test defs, 7 of them in the ignored `test_self_hosting.py`. About 68 defs need the parent repository, and only the 32 in `test_python_qualification.py` need history beyond HEAD.

**None reads another project's worktree files from the snapshot.** Every mention of `cmru/`, `topos/`, `nyxloom/` or `ciu/` in `tests/` is a string in a synthetic temporary repository or fixture (for example `test_gate_qualify_cmru_b006a.py:102-131`, `test_runner_snapshot_selection.py:135-222`). The real-repository reads go through git objects (`archive`, `ls-tree`, `rev-parse`, `clone`). This is static grep evidence only; a pilot run would confirm it.

A hypothetical "project-subtree" worktree that keeps every index entry but marks non-`assay/` paths skip-worktree, as the omissions mechanism already does, would keep commit and tree identity and satisfy these tests, while cutting the worktree from 5,298 files / 70.7 MB to 960 files / 20.2 MB. That requires a new lane-schema value and a decision against A-161 and A-269 §2, and B110 forbids silently running less (`4-backlog.md:11076-11077`).

---

## 4. Stale bytecode

**No reuse path today:**
- The child environment is exactly `lane.env ∪ infrastructure ∪ passthrough` (`runner.py:880-907`). For B105 that is `{PATH}` (`assay.toml:91-92`) plus the liveness `PYTHONPATH` (`liveness.py:469-476`).
- Neither `PYTHONDONTWRITEBYTECODE` nor `PYTHONPYCACHEPREFIX` is set anywhere in `src/`, so bytecode is written under the snapshot's `src/assay/__pycache__`.
- Each candidate is a new `mkdtemp` containing only manifest files (`test_isolation.py:309`), and it is deleted afterwards (`:726-727`).
- The one directory shared across candidates is the liveness plugin directory, which holds assay's own module and is never mutated.

**Why reuse would fail:**
- CPython checks a default timestamp `.pyc` only against the source's `int(st_mtime)` and `st_size`.
- `_write_worktree` gives every file the same `_FIXED_MTIME` (`isolation.py:1942`), and compare-swap `Eq↔NotEq` keeps the byte count (`adapters/python.py:454-463`).
- So a `.pyc` compiled from the original, or from any earlier mutant of the file with the same size change (every `<`→`<=` is +1 byte), passes the check, and the new source is never compiled.
- The worst case is **false kills**, i.e. a false PASS. Candidate A mutates `==`→`!=` in file F and a `.pyc` is written. Restoring F brings back the same size and mtime, so A's bytecode stays valid and every later candidate runs with A's mutant still active.

**Probe on Python 3.14.7** (scratch directory; `m.py` is `a = 1 == 1`, then `a = 1 != 1`, fixed mtime):

| Setup | Output |
|---|---|
| Original | `True` |
| Mutant, same tree | `True` (stale bytecode) |
| `--check-hash-based-pycs always` | `True` (only affects hash-based pycs) |
| `PYTHONDONTWRITEBYTECODE=1` with a stale pyc present | `True` (prevents writing, not reading) |
| Fresh `PYTHONPYCACHEPREFIX` | `False` (correct) |
| `__pycache__` deleted | `False` (correct) |

**How the mitigations fit Assay's rules:**
- A per-candidate `PYTHONPYCACHEPREFIX` would be an Assay-injected variable. That is allowed only as infrastructure recorded in `env_effective`, like the liveness precedent (RW-36). It needs a decision record because A-019 (`decisions.md:31`) makes lane env declared-only.
- `--check-hash-based-pycs` is an argv change; `allow_argv_append=false` (`assay.toml:97`) and A-036 rule it out, and it does not work anyway.
- Deleting `__pycache__` addresses bytecode but not other ignored residue or `.git` drift.
- **Fit:** keep fresh trees built only from manifest content. Then no mitigation or environment change is needed.

---

## 5. Seed preparation, `_BatchReader`, manifest proof

**`prepare_snapshot`** (`isolation.py:1961-2132`) runs once per lane:
1. Open the source: `rev-parse --git-common-dir`, object format, `config --list` (`git.py:1713-1738, 1640-1712`).
2. `rev-parse --verify` (`:1976-1990`).
3. Source closure plus metadata (`:1993-2012`, about 0.5 s).
4. `init --bare`, then `pack-objects --stdout` → `index-pack --stdin` with byte counting (`:2020-2036`). This is the dominant one-time cost, of order seconds.
5. Shallow write and check (`:2040-2042`).
6. Seed re-walk plus metadata equality (`:2048-2077`).
7. Alternates check (`:2078`).
8. `_build_manifest`: one `cat-file --batch` per tree depth, 13 levels, plus one for symlink targets (`:1621-1867`).
9. Judged-tree walk (`:2088-2102`).

Over 3,760 candidates this is negligible. The data C2 needs (`oids`, `metadata`, `judged_tree_oids`) already exists here and is simply not kept on `SnapshotRepository` (`:572-588`).

**`_BatchReader`** (`:477-566`) is fed by `_batch_objects` (`:1205-1238`, deduplicated):
- Per candidate: `_read_blob` (1 object) and `_write_worktree` (all unique regular blobs).
- Per seed: `_resolve_declared_omission_modes` (`:1589`) and `_build_manifest` (`:1692, 1806`).
- `_object_metadata` uses `--batch-check`, buffered (`:1296-1326`).

**`_prove_manifest_materialized`** calls `lstat` only: `os.lstat` per regular leaf, `is_symlink` per symlink, `lexists` per omission (`:439-474`). It never reads or hashes content. **Content is proven only by the status hash pass** in `_verify` (`:1040-1044`), which is why that pass must stay per candidate.

---

## 6. Recommended implementation map

### C1: refresh the index once, per candidate (largest, safest win)
- **`_build`:** after `_write_worktree` (`:852-859`) and before `_verify` (`:861`), add `run("update-index", "--refresh")` **without `-q`**. A non-zero exit maps to `GIT_FAILED` through `_p22_git`; call it "materialized bytes differ from the index".
- **Unchanged:** `_verify`'s status stays the authoritative proof. Entries that don't match stay unrefreshed and are still reported.
- **Effect:** the `_verify` status and `mutation._snapshot_left_dirt`'s status (`git.py:873`) become stat-only.
- **Why dirt detection is not weakened:** the default `core.checkStat` includes inode and ctime (`trustctime` is not overridden in `_FIXED_CONFIG`, `git.py:157-170`), so a same-size edit whose mtime is restored is still caught. **Never** set `checkStat=minimal` or `trustctime=false`.
- **Saving:** one full hash pass, about 0.25–0.85 s.

### C2: incremental child-closure bound
- **`prepare_snapshot`** (`:2081-2109`): after the existing checks, build a frozen `_BaseClosure` with `oids`, `object_count`, `object_bytes` and `judged_tree_meta` (the 5.4k judged-tree entries), and pass it to `SnapshotRepository.__init__` (`:572-588`).
- **`_closure_oids`** (`:1241-1293`): add `exclude: str | None`, which appends `--not <exclude>`.
- **`_enforce_child_closure`** (`:970-1009`): walk the delta against `spec.commit`, run `batch-check` on the delta, then count and sum over `new = delta − base.oids`. Raise the same `_limit_exceeded` messages. Keep the `--no-walk` child-tree walk and run `_enforce_tree_blob_limits` over a mapping chain of delta sizes over `judged_tree_meta`.
- **Saving:** about 0.3–0.5 s.
- **Needs** a new decision record: "the child closure is base ∪ the git-reported delta, not a re-enumeration".

### C3 (optional): per-seed base `.git` template, copying no objects
- Once per seed: `init` + pack copy + `read-tree` + skip-worktree; then **delete the template's pack** so A-194's space accounting stays unchanged.
- Per candidate: copy `HEAD`, `config`, `index` and `refs/` by an explicit list, and the pack from the seed as today.
- **Removes** 3 processes from `_build:770-817`.

### C4 (optional, measure first): copy the worktree from a template by manifest
- A template written by `_write_worktree` once, never yielded.
- Per entry: `copyfile`, `chmod` and `utime`; re-`utime` the swapped file.
- **Saves** the zlib inflate (≈0.48 s). The per-file syscall count stays the same. C1's hash pass still proves content.

### Micro-changes (optional)
- Cache `by_path()`, currently rebuilt for 5.3k entries at `:742` and `:822`.
- Combine `rev-parse HEAD` and `HEAD^{tree}` (`:1019`, `:1051`) into one call.
- Check the Python blob-OID at step 1.
- Pre-create only the ancestor directories of entries (not empty subtrees; `test_isolation.py:1800`) instead of `mkdir` per entry (`:1883`).

### Environment and decisions (outside code)
- Put `TMPDIR` for the tester-unified lane (`tools/self-qualification-gate.sh:45`) on a reflink-capable filesystem. Python 3.14's `shutil.copyfile` uses `copy_file_range`; `/tmp` here is ext4, which cannot reflink. That would turn about 130 MB of copies per candidate (≈490 GB over the campaign, charged against the 2 GiB cap, `run-gate.toml:45`) into metadata operations, which A-184 already permits.
- Shallow history plus declared pinned commits (a new key; A-451 `:901`) would shrink the pack and the two `build_release` clones.
- A project-subtree worktree (section 3).

### Tests, in order

**Guards first.** These pass today and pin what the optimizations could break.
- **G1:** port the O2 check into `tests/`. Two live snapshots plus the seed plus the source, `(st_dev, st_ino)` pairwise disjoint over `.git/objects/**` **and** worktree files. Goes red on any hardlink.
- **G2:** candidate A writes `pkg/__pycache__/x.pyc`, `.pytest_cache/v`, `.git/info/exclude` and a `.git/config [filter]` entry. After A's context closes, snapshot B contains none of them.
- **G3:** the real stale-bytecode sequence with `sys.executable` and env `{PATH}`: mutant `==`→`!=` imports and prints `False`, then the base materialization prints `True`. Red for Design A.
- **G4:** the replaced file in the child has mode and `_FIXED_MTIME`, and symlink leaves stay symlinks.
- **G5:** after `_write_worktree`, overwrite one file with same-size bytes and `_FIXED_MTIME` via a monkeypatch wrapper. The result must be `GIT_FAILED`, pinning that neither refresh nor stat caching can hide it.

**Red first.** These fail today and pass after the change.
- **R1, operation-count oracle** (in `tests/test_isolation.py`, tiny fixture with ≥2 commits):
  - Wrap `git._p22_spawn` (`git.py:1244`), delegating to the real function and recording `argv`.
  - Normalize each call to its subcommand by stripping `--no-pager`, `--no-optional-locks`, `--literal-pathspecs`, `--git-dir=`, `--work-tree=` and `-c k=v` pairs.
  - Assert the **exact** multiset of subcommands for one `materialize_replacement`: today it is 18.
  - Assert **no** `rev-list --objects` in that window lacks `--no-walk` or `--not` (C2).
  - Assert exactly one `update-index --refresh` (C1), and for C3 that `init` and `read-tree` appear 0 times per materialization.
  - Also count `shutil.copyfile` calls and bytes inside `isolation` (pack plus duplicates only).
  - It asserts nothing about time. Optionally `record_property` the duration for humans.
- **R2, index-stat oracle (C1):** at yield, real `git ls-files --debug` shows `mtime: 946684800:0` and the true `size:` for every non-skip entry. Today it shows `0:0 / 0`.
- **R3, closure exactness (C2):** in a fixture where HEAD's file is Y and an older commit had X, replace Y with X. With `max_objects` equal to the independently computed reference (a real `rev-list --objects <child>` in the child repository), the snapshot must yield; at reference minus 1 it must refuse with `SNAPSHOT_LIMIT_EXCEEDED`. Repeat for the byte and tree-blob limits. `test_isolation.py:1760` must still pass.

### Caveats

- **Seed reachable by commands:** the seed (and any template) is reachable by a same-uid command through `..` from the snapshot root. The per-candidate hash pass is what catches tampering; do not remove it for speed.
- **Resulting cost is an estimate:** C1+C2 bring the per-candidate snapshot to roughly 1–2 s on this host. The remaining floor is the pack plus worktree writes (about 130 MB) and the rmtree, so more than that needs the environment and decision items above.
- **Open question:** does an executed-in-place test cost of `build_release`'s two full-history clones per candidate fit the 4.8 s budget at all? That needs the B110 pilot.