# B110-P5: Cheaper per-candidate snapshots (C1 index refresh with a ctime sweep, C2 incremental child-closure bound)

*Revised 2026-09-28 after round-1 review (see REVIEW-2026-09-28-round1.md).* This revision applies round-1 findings P5-1..P5-6 and carver decisions **C4** and **C15**.

The C4 addition: after C1 the post-command dirt check becomes stat-based. A same-size, in-place edit made in the same wall-clock second, with its mtime restored, could then pass unseen (P5-1). So C1 now ships together with a **ctime-nanosecond sweep**, and the sweep is gated by a probe (Work step 0).

| Field | Value |
|---|---|
| Backlog | **B116** (split from B110) |
| Branch | `assay-b110-p5-snapshot` from the integration line (the plan §11.1 / C18 reconciliation branch `assay-b110-integration`) |
| Depends on | **P0** (B111): its snapshot guard tests **G1–G5** must be merged and green on your base. Read their definitions in `b110/P0-measurement-hygiene.md`. |
| Contract class | **2b**: public behavior, refusal pairs and invariants are fixed; the private construction is yours |
| Implementer | Opus, fresh session |
| Decisions | **A-472 (plan D8)**: C1 plus C2; keep one fresh private repository per candidate; hardlinks, tree reuse and in-place mutation stay forbidden. Constraining: A-120, A-161, A-184, A-186, A-193, A-194, A-195. Round-1 carver decisions: **C4** (ctime watermark sweep, probe-gated) and **C15** (dataclass-contract fixture). |
| Size | M/L: `isolation.py` (three functions, the constructor, one new frozen dataclass, one sweep function), one `git.py` constant, a one-line call-site change in each of `mutation.py` and `runner.py`, one new test file, docs |

**What this package buys, as an estimate only:** about 0.6–1.4 s less git work per candidate (analysis report §7.2), times 3,760 candidates. The C4 sweep costs about 5.3k `lstat` calls plus, normally, one small `hash-object` process for the few suspects. That is expected to be well under the saved `status` hash pass, but it is **measured in the pilot, never asserted**. **No oracle asserts time.**

---

## Context to read first

Paths are relative to `assay/`. Line numbers were checked at HEAD `db85f747`.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`, §0, §3 D8, §10.
2. `nyxloom-trove/reports/assay-B110-RUNTIME-ANALYSIS-2026-09-28.md`, §7.2 (the snapshot internals: step inventory, measured costs, invariants table, stale-pyc probe).
3. `src/assay/isolation.py`:
   - `:67` `_FIXED_MTIME`;
   - `:229-256` the frozen `Snapshot` dataclass, which gains one field;
   - `:569-590` `SnapshotRepository.__init__`;
   - `:670-727` `materialize`, `materialize_replacement`, `_materialize`;
   - **`:762-863` `_build`** (the call order you change: `_write_worktree` at `:852`, the HEAD write at `:860`, `_verify` at `:861`);
   - **`:970-1009` `_enforce_child_closure`** (you replace its body);
   - **`:1011-1085` `_verify`** (unchanged; read the `status` proof at `:1040-1044`);
   - `:1241-1293` `_closure_oids` (you add `exclude=`);
   - `:1296-1326` `_object_metadata`;
   - `:1329-1371` `_enforce_object_limits` and `_enforce_tree_blob_limits` (their messages are reused verbatim);
   - `:1961-2132` `prepare_snapshot` (the closures at `:1993-2012` and `:2048-2077`, the judged-tree walk at `:2088-2102`, construction at `:2103`).
4. `src/assay/git.py`:
   - `:143-164` the `_FIXED_CONFIG` comment and `:165-178` the tuple;
   - `:826-905` `dirty_paths` (unchanged; it stays generic because it is also used on real checkouts);
   - `:1222-1243` `_p22_argv`;
   - `:1244-1268` `_p22_spawn`;
   - `:1352-1400` `_p22_git`, whose non-zero exit becomes `GIT_FAILED`.
4b. The two **post-command snapshot dirt checks** that gain the sweep. Each is a one-line union:
   - `src/assay/mutation.py:1843-1874` `_snapshot_left_dirt` (the `git.dirty_paths(snapshot.root, …)` at `:1859`);
   - `src/assay/runner.py:3104-3115` (the `post_dirty = git.dirty_paths(snapshot.root, …)` at `:3109`).
5. `nyxloom-trove/decisions.md`, rows A-120 (`:373`), A-161 (`:454`), A-184 (`:502`), A-186 (`:504`), A-193 (`:516`), A-194 (`:517`), A-195 (`:518`).
6. The tests that pin the invariants. These must stay green unchanged, except the constructor call sites in Work step 4:
   - `tests/test_isolation.py`: fixtures `_root_repo` `:104`, `_scratch` `:146`, `_spec` `:152`, `LITERALS` `:65`; tests `:309`, `:420`, `:450`, `:1324`, `:1598`, and **`:1760` `test_reviewer_the_child_closure_is_bounded_not_only_the_base`**;
   - `tests/test_mutation_isolation.py:261`;
   - `tests/test_b105_isolation_proof_boundaries.py` (constructs `SnapshotRepository` directly at `:68`; `_closure_oids` usage `:232`, monkeypatches `:395-486`);
   - `tests/test_b105_isolation_manifest_boundaries.py` (direct constructions at `:126`, `:158`, `:177`, `:198`);
   - `tests/test_git_boundary.py` (the only test file that references `_FIXED_CONFIG`/`preloadIndex`);
   - `tests/test_b105_git_process_boundaries.py:581, :717` (`_p22_spawn` monkeypatches).
7. P0's G1–G5 tests, wherever P0 placed them. Find them with `rg -n "G[1-5]" tests/` on your base.

## Implementation packet (normative)

### Interfaces and grammar

- **C1.** In `SnapshotRepository._build`, between the `HEAD` write (`:860`) and `self._verify(...)` (`:861`), run exactly one call:

  ```python
  try:
      run("update-index", "--refresh")          # NO -q: a mismatch must exit non-zero
  except AssayError as exc:
      if exc.reason_code is not ReasonCode.GIT_FAILED:
          raise                                  # a deadline expiry stays BUDGET_EXCEEDED/LANE_TIMEOUT
      raise _git_failed(
          f"the materialized worktree at {root} does not match the index "
          f"after update-index --refresh"
      ) from exc
  ```

  - This applies to **both** `materialize()` and `materialize_replacement()`, because both go through `_build`.
  - `_verify` is unchanged. Its `status` stays the authoritative clean-tree proof at build time; after C1 it is stat-only.
- **C1 hardening.** Add three entries at the end of `git._FIXED_CONFIG` (`:165-178`):
  - `"-c", "core.checkStat=default"`
  - `"-c", "core.trustctime=true"`
  - `"-c", "core.ignoreStat=false"`

  Put a comment above them: "after B116's index refresh the dirty check is stat-based; a consumer-written `.git/config` must not be able to weaken stat comparison". **Never** set `checkStat=minimal`, `trustctime=false`, or `ignoreStat=true` anywhere.

  **What the `-c` pins do and do not cover (P5-5).**
  - They defeat **config-level** weakening only.
  - `core.ignoreStat=false` has no effect on assay's own `status --no-optional-locks`. It is kept only as a pin.
  - A consumer command can also weaken the **child's index**, by writing assume-unchanged or skip-worktree bits with its own `git update-index`. The `-c` pins cannot stop that. The C4 sweep does, because it compares against a baseline assay recorded itself and ignores index bits.
- **C4: the ctime-nanosecond sweep.** It runs in addition to `git.dirty_paths` on every post-command snapshot dirt check. New code in `isolation.py`:

  ```python
  @dataclass(frozen=True, kw_only=True)
  class _StatBaseline:
      watermark_ns: int                               # max st_ctime_ns over `regular`, read right after the refresh
      regular: Mapping[str, tuple[int, int, str]]     # repo-top-relative POSIX path -> (st_ino, st_size, blob oid);
                                                      # every tracked, non-skip-worktree REGULAR file of the manifest
  ```

  - `Snapshot` (`:229`) gains `stat_baseline: _StatBaseline | None = None`. `_build` always sets it after the refresh. `None` only occurs for snapshots that tests construct directly.
  - `def snapshot_content_drift(snapshot: Snapshot, *, remaining: git.Remaining | None) -> tuple[str, ...]` is a module-level function. It returns the sorted repo-top-relative paths whose **content** differs from the baseline OID:
    1. If `snapshot.stat_baseline is None`, return `()`.
    2. Call `os.lstat(root / p)` for each `p` in `regular`. A path that no longer exists, or is no longer a regular file, is **not** reported here, because `dirty_paths` reports deletions and type changes.
    3. `suspects` are the paths with `st.st_ctime_ns >= watermark_ns` **or** `(st.st_ino, st.st_size) != baseline[:2]`. Use `>=`, not `>`: files written in the same coarse clock tick as the watermark are always re-hashed. There are only a handful.
    4. If `suspects` is non-empty, hash them in **one** bounded `git hash-object --no-filters --stdin-paths` call, run with `cwd=root` and the snapshot's `.git`, under the P22/`_FIXED_CONFIG` hardening.
       - Never use `-w`.
       - Never allow filters. Without `--no-filters`, a `.gitattributes` in repo content could invoke a filter driver defined in a consumer-written `.git/config` (A-186).
    5. Return the suspects whose computed OID differs from `baseline[2]`.
  - **Call sites (one line each).** `mutation._snapshot_left_dirt` (`:1859`) and `runner.py:3109` use `git.dirty_paths(snapshot.root, remaining=…)` **or** `isolation.snapshot_content_drift(snapshot, remaining=…)`, and report `DIRTY_TREE` if either is non-empty.
    - The drift paths are added to the reported path set. Keep the existing message and reason pair.
    - Nothing else in `mutation.py` or `runner.py` changes.
  - **The residual,** recorded in A-472 by the controller: the realtime clock stepping **backwards** during a candidate, between the watermark read and a same-size in-place edit that keeps the inode. Rename-replacement is still caught by the inode comparison, and by `status` under `checkStat=default`.
- **C2.** Add a frozen dataclass in `isolation.py`:

  ```python
  @dataclass(frozen=True)
  class _BaseClosure:
      oids: frozenset[str]                                   # closure(spec.commit) in the SEED, no resolved_base
      object_count: int                                      # == len(oids)
      object_bytes: int                                      # sum of sizes over oids
      metadata: Mapping[str, tuple[str, int]]                # MappingProxyType over exactly `oids`
  ```

  - `SnapshotRepository.__init__` gains a **required** keyword-only parameter `base_closure: _BaseClosure`.
  - `prepare_snapshot` computes the value once, via `_compute_base_closure(...)`.
- **`_closure_oids(..., exclude: str | None = None)`.** When `exclude` is given, append `"--not", exclude` after the positive commit (and after `resolved_base`, if both are given). Everything else is unchanged, including the non-empty and `max_objects` guards.

### Required flow (C1 + C4, inside `_build`)

1. Write the worktree and `HEAD`, as today.
2. Run `update-index --refresh` (C1). A non-zero exit gives the `GIT_FAILED` above.
3. Build the `_StatBaseline`:
   - `regular` comes from the manifest's regular-file entries minus skip-worktree omissions, with `(st_ino, st_size)` from `os.lstat` and the entry's blob OID.
   - `watermark_ns = max(st_ctime_ns)` over them.
   - This is a Python-only pass, with no git process.
4. `_verify(...)`, unchanged.
5. Construct the `Snapshot` with `stat_baseline=…`.

### Required flow (C2)

1. **`_compute_base_closure`** (called in `prepare_snapshot` after the seed checks at `:2048-2078`, before `_build_manifest`):
   - full history (`not shallow`): `oids = frozenset(seed_oids)`. That set is exactly `closure(spec.commit)`, because `resolved_base` is `None` there.
   - shallow: `oids = frozenset(_closure_oids(executable, git_dir=seed_git_dir, work_tree=None, cwd=seed_git_dir, commit=spec.commit, deadline=deadline, max_objects=spec.limits.max_objects))`, with no `resolved_base` and no `no_walk`. This matches what today's per-child walk sees below the child commit.
   - `metadata = MappingProxyType({o: seed_metadata[o] for o in oids})`. A `KeyError` there is `_git_failed("base closure object missing from the seed inventory")`.
   - `object_bytes = sum(size for _kind, size in metadata.values())`.
2. **`_enforce_child_closure(git_dir, commit, deadline)`**, the new body. Precedence is fixed and each check raises the existing `_limit_exceeded` pair with the message shape shown:
   1. `delta = _closure_oids(..., git_dir=git_dir, commit=commit, exclude=self._spec.commit, max_objects=limits.max_objects)`.
   2. `new = tuple(o for o in delta if o not in base.oids)`. It is never empty: it always contains the child commit.
   3. `new_meta = _object_metadata(..., oids=new)`, a batch-check over the delta only.
   4. `count = base.object_count + len(new)`. If `count > limits.max_objects`, raise `f"the closure holds {count} objects, exceeding max_objects ({limits.max_objects})"`.
   5. For each new blob with `size > limits.max_blob_bytes`, raise the existing blob message.
   6. `total = base.object_bytes + sum(new sizes)`. If `total > limits.max_total_object_bytes`, raise the existing total message.
   7. `tree_oids = _closure_oids(..., commit=commit, no_walk=True)`, unchanged. Then call `_enforce_tree_blob_limits(ChainMap(new_meta, base.metadata), tree_oids, limits)`. A `KeyError` there is a real disagreement and must stay loud: wrap it as `_git_failed("child tree names an object outside base ∪ delta")`.

   **Why this is exact.** Everything reachable from the child is either reachable from `spec.commit`, and so in `base.oids`, or it is reported by `rev-list child --not spec.commit`. Git's negative walk marks only the base's tree walk as uninteresting, so a historical blob that equals the mutant bytes can appear in `delta`. The subtraction in step 2 is what prevents counting it twice. R3 proves this exactness.

### Topology and bounds

- **Inside `materialize_replacement`,** the P22 git subcommands are the same as today except:
  - one `rev-list --objects --not` (delta) replaces the full `rev-list --objects` child walk;
  - the `cat-file --batch-check` stdin carries only the delta;
  - one `update-index --refresh` is added.
- The `--no-walk` child tree walk stays.
- **After the command** (the caller's dirt check), the sweep adds one `lstat` per tracked regular file (about 5.3k for B105, in Python) and **at most one** `hash-object --no-filters --stdin-paths` process, only when there are suspects.
- The per-candidate hash pass (the refresh) is the **only** per-candidate proof that the written bytes match the OIDs. It also catches a tampered seed or template. Never remove it.
- Limits: unchanged (`SnapshotLimits`). Their values come from the lane or the defaults, exactly as today.

### Decision table

| Situation | Outcome / reason | Side effects |
|---|---|---|
| Materialized bytes equal the index | refresh exit 0; `_verify` passes; yield | index stat data recorded (mtime 946684800) |
| A file differs from its blob after `_write_worktree` (G5) | `ERROR` / `GIT_FAILED` ("does not match the index after update-index --refresh") | tree discarded (existing `_materialize` except-path) |
| The deadline expires during the refresh | `BUDGET_EXCEEDED` / `LANE_TIMEOUT` (not rewrapped) | tree discarded |
| Child closure count == `max_objects` | yield | none |
| Child closure count == `max_objects + 1` | `BUDGET_EXCEEDED` / `SNAPSHOT_LIMIT_EXCEEDED` | no child yielded; scratch holds only the seed |
| Same, for `max_total_object_bytes` or `max_total_tree_blob_bytes` | same pair | same |
| A consumer command writes `core.checkStat=minimal` / `trustctime=false` / `ignoreStat=true` into the snapshot's `.git/config`, then **rename-replaces** a tracked file with same-size different bytes and the mtime restored (new inode) | `DIRTY_TREE`: `status` under the `-c` pins sees the inode change, and the sweep sees `(ino, size)` changed and a different OID | existing A-195 path |
| Same hostile config; in-place same-size edit with mtime restored, **after** the wall-clock second has passed the file's ctime second | `DIRTY_TREE`: `trustctime=true` makes `status` see the ctime change; the sweep also sees `ctime_ns >= watermark` | same |
| **No** config change; in-place same-size edit with mtime restored **in the same wall-clock second** as materialization (P5-1) | `DIRTY_TREE` from the **sweep** (`ctime_ns >= watermark`, OID differs), even if `status` reports clean | same |
| The consumer sets assume-unchanged / skip-worktree on a tracked file in the child index, then edits it | `DIRTY_TREE` from the sweep, which ignores index bits | same |
| The suspect's content is unchanged (for example a `touch`) | not dirty (OID equal) | none |

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| C1 refresh | `isolation._build` | R2 | `_root_repo` | drop the refresh → R2 sees `mtime: 0:0` |
| C1 no `-q` | same | **R6** (the message and `__cause__`) | same-size overwrite after `_write_worktree` | add `-q` → the refresh passes, `_verify`'s different message fires, R6 is red. G5 alone cannot tell, because it only checks the pair (P5-2). |
| C1 hardening | `git._FIXED_CONFIG` | R4 (rename-replacement), R4b (trustctime) | hostile `.git/config` | remove `checkStat=default` → R4's `status` half is clean; remove `trustctime=true` → R4b's `status` half is clean. Each half is asserted **separately** from the sweep, by calling `git.dirty_paths` directly. |
| C4 sweep | `isolation.snapshot_content_drift` plus the two call sites | R5 (same second), R5b (assume-unchanged) | in-place edit right after yield | drop the sweep → R5's `snapshot_content_drift` returns `()`, and the caller-level check reports clean |
| C4 no filters | same | R5c | `.gitattributes` naming a filter driver whose command would create a marker file | drop `--no-filters` → the marker file exists |
| C2 delta walk | `isolation._enforce_child_closure` | R1 | `_root_repo` + second commit | keep the full walk → R1 finds a `rev-list --objects` without `--no-walk`/`--not`, and the batch-check OID count ≠ |delta − base| |
| C2 exactness | same | R3 (+ shallow variant) | X/Y history fixture, |X| > |Y| | drop the subtraction → R3 refuses at the reference limit |
| Existing invariants | — | `test_isolation.py:1760`, G1–G4 | existing | hardlink `_copy_objects` → G1 red |

### Degrees of freedom

You may choose private helper names, whether `_compute_base_closure` is a function or a classmethod, and how you ChainMap or otherwise look up metadata, provided a missing key still fails loudly.

You may **not**:
- change a refusal pair or a limit value;
- skip the tree-blob check;
- move the refresh after `_verify`;
- add a code path that runs without a `_BaseClosure`;
- make the sweep compare against index stat data or index bits (it must use its own `_StatBaseline`);
- hash suspects with filters or with `-w`;
- use `>` instead of `>=` against the watermark;
- skip the sweep at either call site.

## Work

0. **Probe, before writing any production code (C4).** Record every command and its output in `assay-B116-REPORT.md` under "C4 probe".
   - **(a) Git stat granularity.** Use `git version --build-options`, which does **not** reveal USE_NSEC, so this probe is empirical. Run it once with the devcontainer git, and once in the gate image through one short-lived container, only when no gate is running:
     ```
     docker run --rm --name b116-probe --cpus=1 --memory=256m -v <host path of a scratch dir>:/p -w /p tester-unified:local bash /p/probe.sh
     ```
     Remember the host-path bind rule: the docker daemon is the host's.
     - `probe.sh` creates a repo with one committed file; sets its mtime to 946684800 with `touch -d @946684800`; runs `git update-index --refresh`.
     - Then, **within the same second,** it rewrites the file in place with same-size different bytes (`printf … | dd conv=notrunc`), restores the mtime, and runs `git status --porcelain`.
     - Record whether `status` reports the file. Either answer is acceptable: "clean" proves the P5-1 hole, and "dirty" means the build compares nanoseconds. The sweep ships either way.
   - **(b) ctime ordering (kernel-level; containers share the host kernel, so the devcontainer suffices).** In a scratch directory, write 200 files, take `watermark = max(st_ctime_ns)`, then do 1,000 immediate in-place writes to one of them and check `st_ctime_ns >= watermark` after each. The test is purely `>=` against a stored value, with no elapsed-time assertion.
   - **BLOCKED** if (b) ever observes `st_ctime_ns < watermark` after a write. That would contradict the sweep's premise; write `BLOCKED: C4 premise contradicted` with the numbers.
1. **Red first.** Create `tests/test_snapshot_costs.py`, importing the fixtures from `tests/test_isolation.py` via `from test_isolation import _root_repo, _scratch, _spec, LITERALS, TIMEOUT`. If that `sys.modules` import misbehaves, copy the helpers. Write R1–R6 (see Oracles).
   - Run them against the unchanged code and record in `assay-B116-REPORT.md`:
     - which ones fail;
     - R1's **observed "before" subcommand multiset**, obtained by running the recorder on the unchanged code and pinned as a literal in the test, with a comment naming the REPORT section. Do not predict it.
   - R2, R3, R5 and R6, and R1's "no full walk", "exactly one refresh" and "batch-check count" parts, must fail before the change.
   - For R4 and R4b, record their state at three points: before C1, after C1 without the hardening, and after the hardening.
2. Implement **C1** (`isolation.py:860-861`), the **C4** baseline in `_build`, and the **C1 hardening** (`git.py:165-178`).
2b. Implement **C4**: `_StatBaseline`, the `Snapshot.stat_baseline` field, `snapshot_content_drift`, and the two one-line call-site unions (`mutation.py:1859`, `runner.py:3109`).
3. Implement **C2**: `_BaseClosure`, `_compute_base_closure`, the `_closure_oids(exclude=)` parameter, the new `_enforce_child_closure` body.
3b. **C15.** `_BaseClosure`, `_StatBaseline` and the new `Snapshot` field change the dataclass inventory. Regenerate `tests/fixtures/dataclass-contract.json` with the command P1 (B112) documents in DESIGN-GUIDE, review the diff (exactly the new classes and field), and commit it in the same change.
4. Update the **five direct `SnapshotRepository(...)` constructions** in tests (Context 6), passing `base_closure=`:
   - if the test reaches `materialize_replacement`, build the closure with `_compute_base_closure` over its seed;
   - otherwise pass `_BaseClosure(oids=frozenset(), object_count=0, object_bytes=0, metadata=MappingProxyType({}))`, and add a comment "never replaced in this test".
5. If `tests/test_git_boundary.py` pins the `_FIXED_CONFIG` tuple literally, extend the pin with the three new entries. Do not weaken it.
6. Run these focused files serially:
   - `tests/test_snapshot_costs.py`
   - `tests/test_isolation.py`
   - `tests/test_runner_p23_cleanup_and_budget.py`, `tests/test_b105_mutation_boundaries.py` and `tests/test_b105_runner_boundaries.py` (the two call sites)
   - `tests/test_b105_isolation_proof_boundaries.py`
   - `tests/test_b105_isolation_manifest_boundaries.py`
   - `tests/test_b105_git_process_boundaries.py`
   - `tests/test_git_boundary.py`
   - `tests/test_mutation_isolation.py`
   - `tests/test_runner_p23_combined_axis_review.py`
   - `tests/test_isolation_unsafe_symlink_omissions.py`
   - P0's guard test file
7. Update **CHANGES.md** under `## [Unreleased]` → `### Changed`: "Snapshots record real index stat data with one `update-index --refresh`, re-hash any tracked file touched after materialization in the post-command dirt check, and bound each mutant child's closure as base ∪ git-reported delta; the fixed Git config pins stat comparison (B116, A-472)."
8. Write `assay-B116-REPORT.md` (the C4 probe, traceability with actual names, the R1 before/after multisets, red/green counts), then commit.

**Optional, not in this package's acceptance** (file separately if the pilot shows a need):
- C3: a per-seed `.git` template;
- C4: copying the worktree from a manifest-copied template;
- micro-changes: cache `by_path()`, combine the two `rev-parse` calls, a Python blob-OID precheck.

**Environment item for the controller, not code:** put the tester-unified `TMPDIR` (`tools/self-qualification-gate.sh:45`) on a reflink-capable filesystem. `/tmp` in the gate is ext4, which has no reflink. A-184 already permits reflinks. Record it in the plan's §11 list only if the pilot shows the ~130 MB of copying per candidate matters.

## Oracles

- **R1: operation-count oracle (structural, not timing).**
  - **Setup:** wrap `git._p22_spawn` with a delegating recorder via `monkeypatch.setattr(git, "_p22_spawn", recorder)`, and wrap `isolation._object_metadata` with a delegating recorder of the `oids` argument length.
  - **Recording window:** from immediately **before** `prepared.materialize_replacement(...)` is called (the build runs in `__enter__`) until immediately **after** its `with` block exits (teardown included). Record nothing else. The fixture has at least 2 commits.
  - **Normalization grammar** (P5-3), applied per argv, in this order:
    1. drop the executable;
    2. drop the global options `--no-pager`, `--no-optional-locks`, `--literal-pathspecs`, `--git-dir=…`, `--work-tree=…`, `--template=…`, and every `-c k=v` pair;
    3. replace every token matching `^[0-9a-f]{40}$` or `^[0-9a-f]{64}$` with `<oid>`;
    4. replace every token that is an absolute path (starts with `/`) with `<path>`.

    The normalized argv is a tuple of tokens. The multiset is a `collections.Counter` of those tuples.
  - **Pinned "before":** the Counter observed on the **unchanged** code (Work step 1), written into the test as a literal.
  - *Observable:*
    - `after == before - Counter({<the full child `rev-list --objects …` tuple>: 1}) + Counter({<the delta `rev-list --objects … <oid> --not <oid>` tuple>: 1, ("update-index", "--refresh"): 1})`. The "after" is **derived**, never separately observed and pinned.
    - Every recorded tuple containing `rev-list` and `--objects` also contains `--no-walk` or `--not`.
    - The single post-delta `_object_metadata` call receives exactly `|delta − base|` OIDs. Compute that independently: plain `git rev-list --objects --no-object-names <child> --not <spec.commit>` in the child's `.git`, minus plain `git rev-list --objects --no-object-names <spec.commit>` in the seed.
  - *Negative:* the current code has a full `rev-list --objects` and no refresh; an implementation that still batch-checks base ∪ delta passes the argv shape but fails the OID count.
  - *Gate:* tester-unified.
- **R2: index stat oracle.**
  - *Observable:* at yield, the real `git ls-files --debug` in the child (plain `subprocess` git, the test's `_git` helper) shows `mtime: 946684800:0` and `size: <true size>` for every non-skip-worktree regular entry.
  - *Negative:* today it shows `mtime: 0:0` and `size: 0`.
- **R3: closure exactness (P5-4).**
  - **Fixture:** commit 1 holds `lib/data.bin = X` and commit 2 (HEAD, judged) holds `lib/data.bin = Y`, with **|X| > |Y|** (for example 64 vs 32 bytes). Only `lib/data.bin` differs between the two commits, so the child tree equals commit 1's tree, the most adversarial case.
  - Replace Y with X in the child. X's blob, and the child's root and `lib` trees, already exist in history.
  - **Reference values:** yield once with default limits, read `child.commit`, and inside that yielded child run plain `git rev-list --objects --no-object-names <child.commit>` in the **child's own `.git`**. An independent clone cannot contain the child commit. From that list compute the object count, the byte total (via plain `git cat-file --batch-check` in the child), and the tree-blob total of `<child.commit>^{tree}`.
  - Because |X| > |Y|, the seed's own closure, bytes and tree-blob total are each strictly below the reference values. So `prepare_snapshot` passes at `reference - 1`, and any refusal must come from the child.
  - *Observable:*
    - with `max_objects = reference` it yields;
    - with `reference - 1`, the **`materialize_replacement` call** raises `BUDGET_EXCEEDED`/`SNAPSHOT_LIMIT_EXCEEDED` (assert that `prepare_snapshot` returned first);
    - the same holds for `max_total_object_bytes` and `max_total_tree_blob_bytes`, each varied alone.
  - **Shallow variant:** repeat the object-count case with `snapshot_history = "shallow"` and a seed in which X exists only in an ancestor outside the shallow boundary. X is then genuinely new in the delta.
  - *Negative:* without the subtraction, the snapshot refuses at `reference`. Do not predict by how much (§3b F).
  - `test_isolation.py:1760` must also still pass.
- **R4: hostile config plus rename-replacement (P5-1, P5-5).**
  - Inside a yielded child, append `[core]\n\tcheckStat = minimal\n\ttrustctime = false\n\tignoreStat = true\n` to `.git/config`.
  - Write same-size different bytes to `f.tmp`, `os.utime` it to `_FIXED_MTIME`, then `os.replace("f.tmp", f)` for a tracked `f`. That gives a new inode with the same size and mtime.
  - *Observable:* `git.dirty_paths(child.root)` reports `f`, because the `-c core.checkStat=default` pin makes the inode compare, **and** `isolation.snapshot_content_drift(child)` reports `f`. Assert each **separately**.
  - *Negative:* without the `checkStat` pin, `dirty_paths` is clean. The sweep half stays red-free independently.
- **R4b: hostile config plus in-place edit after the second boundary.**
  - The same hostile config.
  - Wait until `int(time.time()) > os.stat(f).st_ctime_ns // 10**9`. This is a precondition, not an assertion: poll with `time.sleep(0.05)` and a 5 s failsafe that fails the test with a message.
  - Rewrite `f` **in place** (`open(f, "r+b")`, same size, different bytes) and restore the mtime.
  - *Observable:* `git.dirty_paths` reports `f` (the `trustctime=true` pin), and so does the sweep.
  - *Negative:* without the `trustctime` pin, `dirty_paths` is clean.
- **R5: same-second in-place edit, no hostile config (the P5-1 hole; C4).**
  - Immediately after yield, with no waiting, rewrite a tracked `f` in place with same-size different bytes and restore the mtime.
  - *Observable:* `isolation.snapshot_content_drift(child)` returns `(f,)`. The caller-level check reports `DIRTY_TREE`: call `mutation._snapshot_left_dirt(job, child, remaining=None)` with a minimal job, and assert the reason pair.
  - Whether `git.dirty_paths` alone sees it depends on the git build (Work step 0) and on timing. **Do not assert on it.**
  - **Deterministic `>=` sub-case (R5-eq).** After the edit, build a `_StatBaseline` from the child's real baseline, but with `watermark_ns` set to `f`'s **post-edit** `st_ctime_ns`, so the edit lands exactly in the watermark tick. Attach it with `dataclasses.replace(child, stat_baseline=…)`, then assert that `snapshot_content_drift` reports `f`.
  - *Negative:* dropping the sweep turns R5 red. Using `>` instead of `>=` turns R5-eq red. Comparing against index stat data turns R5b red.
- **R5b: assume-unchanged in the child index.** Run `git update-index --assume-unchanged f` in the child (plain subprocess git), then edit `f` in place with the mtime restored.
  - *Observable:* the sweep reports `f`.
  - *Negative:* a sweep that consults index bits.
- **R5c: no filters executed.** Commit a `.gitattributes` in the fixture: `data.bin filter=evil`. The child's `.git/config` gets `[filter "evil"] clean = touch <tmp>/FILTER_RAN`. Edit `data.bin` in place.
  - *Observable:* the sweep reports it, and `<tmp>/FILTER_RAN` does not exist.
  - *Negative:* hashing without `--no-filters` creates the marker.
- **R5d: the runner call site (`runner.py:3109`, inside `_execute_snapshot_unit` at `:2800`).** Follow the existing direct-call pattern in `tests/test_b105_runner_boundaries.py:463-620`. Give `runner._execute_snapshot_unit` a fake `process_runner` that rewrites a tracked file in place, with the same size and the mtime restored, inside its `cwd`. For determinism, pre-attach a `stat_baseline` whose watermark equals that file's post-edit ctime (the R5-eq technique), then return exit 0.
  - *Observable:* the unit's post-command reason is `DIRTY_TREE`, and the path is named.
  - *Negative:* forgetting the runner call site turns R5d red.
- **R6: the refresh, not `_verify`, catches a materialization mismatch (P5-2).** Use the same tamper as G5: wrap `_write_worktree` to overwrite one file with same-size different bytes and `_FIXED_MTIME` after it writes.
  - *Observable:* `GIT_FAILED`, whose message contains `"does not match the index after update-index --refresh"`, and whose `__cause__` is the refresh's own `AssayError`.
  - *Negative:* with `-q`, the refresh exits 0, `_verify`'s status raises a different message, and R6 is red.
- **G1–G5 (from P0)** stay green: disjoint inodes, no residue across candidates, the stale-pyc sequence, fixed mtime/mode, and same-size overwrite gives `GIT_FAILED`.

### Anti-pattern list (verbatim from `nyxloom/reference/AUTHORING.md` §3b)

#### 3b. What an oracle must NOT contain — paste this into any handoff that asks for tests

Every rule below is the residue of a real incident; the `L`/`PL` refs are the
write-ups in `reference/LESSONS.md`. **If a handoff asks an agent to write
tests, copy this list into it** — an implementation agent has no access to our
incident history and will otherwise reproduce these by default.

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous — 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" — pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real —
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line — including in
  a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too —
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**F. No predicted measurements.** (distilled 2026-09-17 from an incident in a
consuming project's own decision ledger — the specific entry isn't cited here
since a canonical doc shouldn't hard-reference a consumer's private,
renumberable ledger; see that project's own decisions.md around the same
date for the full incident writeup if useful.)
- ✗ A carve or oracle asserting a specific coverage/mutation number, a "missing
  lines" list, or a "this branch is permanently uncoverable" claim computed by
  reasoning about a tool's rendered report instead of running the tool.
- ✗ Trusting `coverage.py`'s rendered "Missing" column as a complete branch-arc
  list — it silently suppresses an arc whose destination line is already
  reported missing elsewhere, so a hand-derived read of the report undercounts
  by exactly that arc. This exact mistake recurred three times independently
  in one wave before being traced to this display artifact.
- ✓ Assert the POLICY requirement instead — the project's coverage target, its
  R0-R3 (or equivalent) testing tier, the design decision — as the oracle.
  Never a predicted number; the number does not exist until the implementer's
  own gate run produces it.
- ✓ If a carve must justify "this is achievable" or "this line is
  unreachable" before dispatch, PROVE it by executing the tool
  (`coverage.py`/`runpy.run_module(mod, run_name="__main__")`, or the
  project's own judge) against real or synthetic stand-in code — never by
  reading a report and reasoning about what it would show.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.

## Docs sync

- **DESIGN-GUIDE:** find the P22 section with `rg -n -e "status --porcelain" -e "hash pass" -e "child closure" -e "A-186" docs/DESIGN-GUIDE.md`. In ripgrep `\|` is a literal pipe, so use `-e` (P5-6). Add one paragraph covering:
  - the index is refreshed once per materialization, and that refresh is the content proof;
  - the post-command sweep re-hashes files touched after the watermark, and ignores index bits;
  - the residual (a backwards realtime clock step);
  - the child closure is bounded as base ∪ delta, and why that is exact (the subtraction);
  - the pinned stat-comparison config and its scope (config only, not index bits).

  Cite A-472.
- **README / CONSUMERS:** no change. There is no user-visible behavior, the refusals are identical, and no key is added. Confirm with `rg -n -e "update-index" -e "closure" README.md docs/CONSUMERS.md`, and edit only if a sentence describes the per-child full walk.
- **CHANGES:** Work step 7.

## Scope / forbid

- **Touch:**
  - `src/assay/isolation.py` (`_build`, `_enforce_child_closure`, `_closure_oids`, `SnapshotRepository.__init__`, `prepare_snapshot`, `Snapshot` (one new field), and the new `_BaseClosure`, `_compute_base_closure`, `_StatBaseline` and `snapshot_content_drift`);
  - `src/assay/git.py` (`_FIXED_CONFIG` only);
  - `src/assay/mutation.py`: **only** the `git.dirty_paths` line in `_snapshot_left_dirt` (`:1859`), turned into the union with the sweep. P4 and P6 edit other regions of this file; rebase on whichever merged first;
  - `src/assay/runner.py`: **only** the `post_dirty = …` line at `:3109`, turned into the union;
  - `tests/test_snapshot_costs.py` (new);
  - the five constructor sites in `tests/test_b105_isolation_proof_boundaries.py` and `tests/test_b105_isolation_manifest_boundaries.py`;
  - `tests/test_git_boundary.py` (a pin only, if present);
  - `tests/fixtures/dataclass-contract.json` (C15, regenerated, Work step 3b);
  - `docs/DESIGN-GUIDE.md`, `CHANGES.md`, `nyxloom-trove/reports/assay-B116-REPORT.md`.
- **Forbid:**
  - `_copy_objects` (no hardlinks, no shared packs);
  - `_write_worktree` (the optional template copy, formerly labelled "C4" in the research record, is out of scope; do not confuse it with carver decision C4);
  - `_verify`'s proof set;
  - `mutation.py` and `runner.py` beyond the two single lines named above;
  - `config.py` and any lane key;
  - `assay.toml`, `run-gate.toml`, the gate scripts;
  - `decisions.md` (the controller records A-472).

  Needing any of them is a BLOCKED trigger. Keep the B105 coverage floor: every new line and branch in `isolation.py`/`git.py` must be exercised by tests, and no `pragma: no cover` may be added.

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Run the focused tests from Work step 6 serially: `nice -n 19 ionice -c3 python -m pytest <files> -q -p no:cacheprovider`.
2. Run `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b116-gate.log 2>&1; echo "exit=$?"`. In a **separate** step, run `grep -E "ASSAY_GATE_CONTAINER_EXIT|ASSAY_REGISTERED_GATE_COMPLETE" /tmp/b116-gate.log`.
3. `isolation.py` is inside B105's measured target, and its coverage floor is 100%. So also run `python ./run-gate.py self-qualification-preflight > /tmp/b116-preflight.log 2>&1`. That is R0/R1 only, roughly 10 minutes; start it only when no other gate container is running. Read `ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1` in a separate step.

## BLOCKED rule

If a named contract cannot be met as specified, or the scope requires a forbidden file, STOP. Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B116-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

Specific triggers:
- the Work step 0 probe contradicts the sweep's premise (`st_ctime_ns < watermark` after a write);
- R3 cannot be made exact with base ∪ delta;
- a G-test from P0 goes red;
- the refresh needs `-q` to pass any existing test. That would mean that test was relying on an unproven tree, so report it instead of patching around it;
- an existing test legitimately rewrites a tracked file inside a snapshot, so the sweep would newly report it dirty. Report it; do not exempt paths.

## Report

`assay-B116-REPORT.md` must contain:
- the traceability table with actual names and counts;
- R1's before/after multisets;
- the gate and preflight log paths plus their marker lines;
- the files touched;
- any residuals, such as C3/C4 not done.

Commit with a precise message and the trailer:

```
Co-Authored-By: Claude Sonnet <noreply@anthropic.com>
```

Do not merge; the controller merges after an independent adversarial review.
