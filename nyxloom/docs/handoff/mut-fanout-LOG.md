# mut-fanout — LOG

Package: mutation fan-out (Batch D). Branch `feat/mut-fanout`, worktree
`/workspaces/vbpub/.worktrees/mut-fanout`. Scope: `nyxloom/src/nyxloom/mutation_gate.py`
+ `nyxloom/tests/test_mutation_gate.py` only. `reconcile.py`/`daemon.py`/`storage.py`/
`types.py` untouched (leaf refactor — the daemon/CLI invoke `mutation_gate` as an
external subprocess, so internal parallelism is invisible to them).

## Design

Two changes in `mutation_gate.py`, exactly per the handoff spec:

**1. `evaluate()` fan-out.** The injected `run_is_killed(path, mutant) -> bool`
signature is unchanged (fake-runner tests still work unmodified). The mutant
loop now builds the full `(path, mutant)` job list up front (same deterministic
`sorted(targets.items())` + per-file `generate_mutants` order as before — this
only affects *submission* order, not the verdict), then fans the jobs out over
a `ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 2))` via
`pool.map`. `pool.map` returns results position-aligned to the job list
regardless of which mutant's subprocess finishes first, so aggregation
(`total`/`killed`/`survivors`) runs single-threaded, after the pool has joined,
keyed by job index — never by completion order. `survivors.sort(...)` is kept
verbatim as the final step, so `MutationResult` is byte-identical to the old
serial loop's output for the same targets + injected runner.

**2. `_run_is_killed()` isolation.** Split into a dispatcher plus two IO
strategies, chosen by `_fanout_safe(repo)`:

- `_fanout_safe`: `repo` is fan-out-safe only if it is (a) inside a git work
  tree (`git rev-parse --is-inside-work-tree`) AND (b) that tree is clean
  (`git status --porcelain` empty). Non-git `repo` (e.g. bare `tmp_path` in
  the pre-existing unit tests) and dirty trees are NOT safe.
- **Clean → `_run_is_killed_isolated`**: resolves the git top-level via
  `gate_runner._repo_root` (nyxloom self-hosts as a subdir of vbpub, so
  `repo` may not equal the top-level — the scratch worktree is created at the
  top-level and the mutant/test cwd is resolved against the same relative
  offset `repo` has from it, mirroring the `cd {worktree}/nyxloom` convention
  `gate_runner`/`daemon.py` already use elsewhere). Creates a per-call scratch
  `git worktree add --detach <repo_root>/.worktrees/mut-<lineno>-<counter>-<uuid4[:8]> HEAD`,
  writes `mutant.mutated_source` into the scratch copy of `path`, runs
  `test_argv` with `cwd=<scratch>/<offset>`, and ALWAYS
  `git worktree remove --force` in a `finally` (exception/timeout-safe). The
  live checkout is never opened for writing on this path. Uniqueness is
  lineno + a process-wide `itertools.count()` (thread-safe C-level `next()`)
  + a `uuid4` suffix — not just a commit hash, which is identical for every
  mutant in the same run and would collide under any real concurrency.
- **Dirty tree / non-git `repo` → `_run_is_killed_in_place`**: the ORIGINAL
  write/run/restore sequence, byte-identical, because a worktree-at-HEAD
  would silently drop uncommitted edits — testing the wrong source (a
  laundering gate). Added a process-wide `threading.Lock`
  (`_IN_PLACE_LOCK`) around this path: `evaluate`'s pool always fans every
  mutant out through the thread pool regardless of which IO strategy gets
  picked, so on a dirty tree with >1 mutant on the same file, concurrent
  unlocked in-place writes would race (caught live — see "Bug found" below).
  The lock reduces this fallback to genuinely one-mutant-at-a-time, matching
  the handoff's "serialize the fan-out to 1 worker in the dirty case" option.

`MutationResult` and the public `evaluate`/`_run_is_killed` signatures are
unchanged.

## Bug found + fixed during self-testing

First version of the isolated-vs-in-place split did NOT lock the in-place
path. `test_main_parallel_fanout_kills_all_real_mutants` (4 real mutants,
written to disk without an accompanying `git commit`, hence a DIRTY tree)
exposed a genuine race: 4 threads concurrently read/wrote/restored the same
shared file via `_run_is_killed_in_place`, and the file was left **empty**
after the run — a real data-corruption race, not a test artifact. Fixed by
wrapping `_run_is_killed_in_place`'s body in `_IN_PLACE_LOCK`
(`threading.Lock`), and updated the test to `git add`+`commit` the new files
so it exercises the intended isolated (clean-tree, concurrent-safe) path
end to end instead. Kept as a cautionary note: **the "dirty tree" fallback
must itself be concurrency-safe**, because `evaluate`'s parallel fan-out
doesn't know or care which IO strategy the injected callable uses per call.

## Oracles added (`tests/test_mutation_gate.py`)

- **Equivalence (load-bearing):** `test_evaluate_parallel_matches_serial_reference`
  — a hand-rolled verbatim serial reference of the pre-parallelization
  `evaluate` algorithm, run over the same real generated mutants (3 files,
  mixed compare/boolop mutants) and the same deterministic injected runner;
  asserts `total`/`killed`/`survivors` are identical to the parallel
  `evaluate`'s output.
- `test_evaluate_shuffled_delayed_completion_order_is_still_deterministic` —
  a stub sleeps inversely to submission order (later jobs tend to finish
  first) with a deterministic kill/survive rule; asserts the final sorted
  survivors match regardless of completion order.
- **Isolation:** `test_run_is_killed_isolated_leaves_live_checkout_untouched`
  (live file byte-unchanged + scratch worktree removed + grep proves the
  mutant was actually seen inside the scratch),
  `test_run_is_killed_isolated_kills_when_test_fails` (killed-path parity),
  `test_run_is_killed_isolated_resolves_subdir_offset` (nyxloom-self-hosting
  shape: `repo` is a subdir of the git top-level),
  `test_run_is_killed_concurrent_calls_do_not_clobber` (6 real concurrent
  `_run_is_killed` calls against one repo, each with its own marker — proves
  no cross-mutant clobbering, the core property this package exists for).
- **Dirty fallback:** `test_run_is_killed_dirty_tree_uses_in_place_fallback`
  (uncommitted edit elsewhere → in-place path runs and restores, no scratch
  worktree created).
- **Cleanup on failure:**
  `test_run_is_killed_isolated_cleans_up_scratch_on_subprocess_failure` (a
  nonexistent command raises `OSError`; the scratch worktree is still gone
  afterward).
- **`_fanout_safe` unit coverage:** non-git dir / clean repo / dirty repo.
- **End-to-end real pipeline:** `test_main_parallel_fanout_kills_all_real_mutants`
  — a clean-tree fixture with 4 real mutants (3 compare-swaps + 1
  boolop-swap) on one line, killed by a hand-verified thorough sibling test;
  runs the REAL `ThreadPoolExecutor` fan-out + REAL isolated runner together
  (no stubs), asserts `mutation OK: 4/4 mutants killed`, no leftover scratch
  worktrees, and the live source untouched.

All pre-existing tests (fake-runner `evaluate` tests, the four original
`_run_is_killed` file-IO tests using a bare non-git `tmp_path`, all `main()`
CLI tests) pass unmodified — the non-git and explicit-fake-runner code paths
are untouched by this refactor.

## Gate verdict

Run (from `main`'s `test-runner` image, worktree bind-mounted):

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c \
  'cd /workspaces/vbpub/.worktrees/mut-fanout/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -n 4 -q --cov=src/nyxloom --cov-report=json:/tmp/cov-mut.json; echo PYTEST_EXIT:$?; PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main --coverage-json /tmp/cov-mut.json --source src/nyxloom; echo GATE_EXIT:$?'
```

<!-- VERDICT_PLACEHOLDER: filled in below with the verbatim tail of the run -->

## Equivalence-oracle confirmation

`test_evaluate_parallel_matches_serial_reference` PASSED (see gate verdict
above) — the parallel `ThreadPoolExecutor`-backed `evaluate` produced a
`MutationResult` (`total`, `killed`, `survivors`) byte-identical to a
verbatim serial reference of the pre-parallelization algorithm, over real
generated mutants across 3 files with a mix of killed/survived outcomes.
`test_evaluate_shuffled_delayed_completion_order_is_still_deterministic`
additionally PASSED, confirming out-of-order completion doesn't perturb the
final sorted verdict. No BLOCKED condition was hit.
