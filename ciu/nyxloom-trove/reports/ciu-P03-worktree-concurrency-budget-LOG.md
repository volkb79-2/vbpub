# LOG — ciu-P03-worktree-concurrency-budget

Implementer: Claude Sonnet 5. Handoff:
`nyxloom-trove/handoffs/ciu-P03-worktree-concurrency-budget.md`.
`input_revision`: `5cb4a9a8e710095c902dadcad0c9504cd84f616e` (the P02 merge
commit); this worktree's actual starting HEAD is
`2fa57f20c25285ad1e3d6a55548f70ae133c956a` (one later commit on the same
branch that only re-pins the handoff's `input_revision` pointer — no other
content changed, per that commit's own message). Verified with
`git rev-parse HEAD`.

## Baseline (before any code change)

Docker IS reachable from this sandbox (unlike ciu-P01/P02's environment), so
the DECLARED gate (`[gates.tester-unified]` in `nyxloom-trove/nyxloom.toml`)
was run literally, via the same bind mount the gate config specifies
(`-v /home/vb/volkb79-2/vbpub:/workspaces/vbpub`, confirmed to match this
host's own mountinfo for `/workspaces/vbpub`), with `{worktree}` substituted
for this session's actual worktree path.

**Literal declared argv, byte-for-byte** (no added `-e` flags):

```
docker run --rm --cgroup-parent=nyxloom-gates.slice \
  -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd /workspaces/vbpub/.worktrees/ciu-P03-worktree-concurrency-budget/ciu && \
    export PYTHONPATH=src && /opt/tester-venv/bin/python run-ciu-tests.py && \
    PYTHONPATH=../nyxloom/src /opt/tester-venv/bin/python -m nyxloom.coverage_gate \
    --repo . --base main --coverage-json coverage.json --source src/ciu'
```

This fails on **2 pre-existing, unrelated** tests — a real environment gap in
the declared gate command itself, not something this package introduces:

```
FAILED tests/tests/test_spec_contracts.py::TestVaultBackedFlows::test_gen_to_vault_generates_once_across_reruns
FAILED tests/tests/test_spec_contracts.py::TestVaultBackedFlows::test_gen_to_vault_refreshes_store_when_vault_changes
...
E ValueError: [S15.2] governance is enabled but no cgroup_parent is resolvable —
  set [<root>.governance].cgroup_parent explicitly, or ensure
  $CGROUP_PARENT_DEV_BACKGROUND is present in the environment
======================= 2 failed, 1882 passed in 14.44s =======================
```

The declared `docker run` invocation in `nyxloom.toml` never passes
`$CGROUP_PARENT_DEV_BACKGROUND` (an AGENTS.md-documented host convention,
`ciu/src/ciu/governance.py`'s `resolve_cgroup_parent`) INTO the spawned
`tester-unified:local` container, so any test whose fixture enables
governance without an explicit `cgroup_parent` fails inside the gate
container even though this host's cockpit devcontainer has the variable set
(`dev-background.slice`). Confirmed this is purely an env-passthrough gap by
re-running with `-e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice`
added — **all 1884 tests pass, matching ciu-P02's own landed final-gate
count exactly**:

```
Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
(all modules 100% except:)
src/ciu/cli.py                                     399     27    128      4    94%   350-373, 595-600, 814, 817, 820
src/ciu/composefile.py                             344     14    168      2    96%   809-833, 924
src/ciu/deploy.py                                 1027      3    422      1    99%   725, 745-746
src/ciu/governance.py                              382      1    158      2    99%   189, 197->201
src/ciu/ksm.py                                     180     56     64      6    68%   ...
src/ciu/worktree.py                                321     24    112      4    93%   210, 224, 416-426, 441-468, 560, 764
--------------------------------------------------------------------------------------------
TOTAL                                             6199    125   2438     19    98%
FAIL Required test coverage of 100% not reached. Total coverage: 98.05%
============================ 1884 passed in 13.20s =============================
```

Changed-line gate against that `coverage.json` (HEAD == `main`, no delta to
measure yet — expected, confirming this worktree starts clean):

```
diff-coverage NO MEASUREMENT: resolved base (2fa57f20c252) IS HEAD -- there is
no delta to measure. --base should resolve to an ANCESTOR of HEAD ...
```

**Both numbers, reported honestly, same as ciu-P01/P02's LOGs:** the blanket
`--cov-fail-under=100` shortfall (98.05%) is the identical, pre-existing,
unrelated-to-this-package baseline every prior package in this series
measured (`worktree.py`'s untouched `list_worktrees`/`_generate_env_in`/
`_clean_in` tails, `ksm.py` 68%, scattered `cli.py`/`composefile.py`/
`deploy.py`/`governance.py` gaps this package never touches) — not something
fixable from inside this package regardless, since `run-ciu-tests.py`'s own
blanket check runs before the changed-line half ever does. The literal
declared gate command additionally cannot pass end-to-end on THIS host
without the `$CGROUP_PARENT_DEV_BACKGROUND` passthrough gap closed — noted
here for the controller, not worked around inside this package (out of
scope: this package's `forbid` list does not include
`nyxloom-trove/nyxloom.toml`, but silently patching the gate's own argv
felt like exactly the kind of invented fix that belongs in a report, not a
unilateral edit).

## Requirement-to-oracle traceability table (pre-implementation)

| Oracle | Requirement | Where it would be proven |
|---|---|---|
| O1 | Sole file policy source is the PRIMARY *Git* worktree's own CIU root's `[ciu.worktree]` table, reached via the exact git-root-to-CIU-root offset; `CIU_MAX_CONCURRENT_WORKTREES` ambient override; narrow no-global-configuration catch only; no CIU-13 participation; every invalid-value refusal | `worktree.primary_worktree_root`/`git_toplevel`/`primary_ciu_root`/`resolve_max_concurrent_instances`/`resolve_worktree_cap`; `config_model.render_global_chain`'s new `write_rendered`/`environ` kwargs; `test_ciu_worktree_budget.py`, `test_ciu_config_model.py` |
| O2 | Pre-lock candidate translation (offset-preserving), registered-and-deployed classifier via the EXACT per-instance compose-project label (never bare label presence), sibling-missing-stack skip, P02-composition non-confusion, malformed-env/duplicate-network/render-failure refusals | `worktree._resolve_budget_candidates`, `worktree._candidate_deployed`, `worktree.worktree_budget_slot`; `test_ciu_worktree_budget.py` |
| O3 | The locked critical section itself (candidates resolved before the flock, Docker/count/executor held under it, released on every return/raise) plus both engine call sites (`main_execution`/`run_shipped`) wired around only the real Compose start, dry-run/render-only exempt, P02's join strictly after the budget context exits | `worktree.worktree_budget_slot`'s lock discipline (instrumented `fcntl.flock`, real two-thread contention); `engine.main_execution`/`run_shipped`; `test_ciu_worktree_budget.py`, `test_ciu_engine_worktree_budget.py` |

No oracle required inventing an externally-visible interface, default, or
bound beyond what the Implementation packet already pinned. One genuine gap
was found and resolved with the smallest, most-precedented answer rather
than escalated — see "Judgment call" below, flagged rather than silently
invented. Proceeding to implementation.

## What was built

### `src/ciu/config_model.py` — `render_global_chain` extension (owner per packet)

- `render_global_chain` gains keyword-only `write_rendered: bool = True` and
  `environ: Mapping[str, str] | None = None`, threaded through
  `_make_render_context` and `render_toml_template` down to
  `expand_env_vars_or_fail`'s new `environ=` parameter. Both default to the
  prior behaviour exactly (write `ciu.global.toml`; consult `os.environ`), so
  every existing caller (`engine.py`, `deploy.py`, `dev.py`, and every
  existing test) is unaffected — confirmed by running the FULL pre-existing
  suite unmodified after this change alone: 1884/1884 still pass.
- `write_rendered=False` is what lets S16.3's policy probe AND every
  candidate's render happen without writing/racing the real rendered
  `ciu.global.toml` output another process depends on.
- `environ=<mapping>` replaces `os.environ` for BOTH the Jinja `env.*`
  context and `$VAR` expansion at every template in the chain — this is what
  lets a candidate worktree render against its OWN explicit `ciu.env`
  without ever touching the calling process's ambient environment.

### `src/ciu/worktree.py` — the S16.3 policy, classifier, and lock (owner per packet)

- `primary_worktree_root(repo_root)` — the registered primary GIT worktree
  (via `list_worktrees`); zero/multiple primaries is a loud `[S16.3]` error.
- `git_toplevel(repo_root)` — `git rev-parse --show-toplevel`, validated to
  return one absolute existing directory.
- `_ciu_root_offset(repo_root)` — the shared offset derivation
  (`repo_root.resolve().relative_to(git_toplevel(repo_root))`), computed
  once and reused verbatim by both `primary_ciu_root` and the candidate
  translation in `worktree_budget_slot` — the single namespace translation
  this feature uses, never re-derived differently in two places (this is
  exactly the trap the handoff's escalate_if #1 named).
- `primary_ciu_root(repo_root)` — `primary_worktree_root(repo_root) /
  _ciu_root_offset(repo_root)`; an absent derived root is a loud `[S16.3]`
  failure, never a silent fall-back to the git root.
- `resolve_max_concurrent_instances(raw, *, environ=None)` — validates the
  file table (unknown key, non-positive-int, `bool`-is-`int` trap) even when
  an ambient override is present; the ambient `CIU_MAX_CONCURRENT_WORKTREES`
  must match `^[1-9][0-9]*$` exactly (no sign/leading-zero/decimal/
  whitespace); no `0 == unlimited` sentinel — absence at BOTH sources is the
  only "no cap".
- `resolve_worktree_cap(repo_root)` — the engine-facing convenience wrapper:
  derives `primary_ciu_root`, renders its global chain with
  `write_rendered=False`, catches ONLY the literal `"[ERROR] No global
  configuration found."` prefix as "no file policy" (any other render
  `ValueError` still aborts), then calls `resolve_max_concurrent_instances`.
- `_BudgetCandidate` / `_candidate_project` (lazy `from . import engine`,
  avoiding the `worktree` ↔ `engine` import cycle P03's own wiring
  introduces) / `_resolve_budget_candidates` — the pre-lock classifier: for
  every git-registered worktree, applies the offset to get
  `candidate_ciu_root`/`candidate_stack`; a genuinely absent stack is
  skipped with one `[INFO] [S16.3]` note (no Docker query); an eligible
  worktree's OWN `ciu.env` (read at the GIT WORKTREE root — the same
  location `worktree.add`/S16.1 already write to and read from, not an
  offset-translated path) supplies `DOCKER_NETWORK_INTERNAL` and the
  `environ=` mapping used to render that candidate's OWN global config and
  derive its OWN exact compose project; a present-but-unrenderable candidate
  is a loud failure, never "inactive".
- `_candidate_deployed(candidate)` — `docker ps --filter
  label=com.docker.compose.project=<exact-project> --format {{.Networks}}`,
  deployed only when the candidate's OWN network appears in that project's
  own container output. Proven against the REQUIRED P02-composition fixture
  (a container labelled with instance A's project that ALSO lists instance
  B's network, from S16.1's shared-infra join) — querying B's own exact
  project returns nothing, so B is correctly NOT counted deployed.
- `_git_common_dir` / `worktree_budget_slot` — the locked critical section.
  `cap=None` yields immediately (no candidate resolution, no Docker, no
  lock). Otherwise: `stack_rel` absolute-path guard, candidate resolution,
  the distinct-network + current-network-registered checks — ALL before
  `fcntl.flock(LOCK_EX)` on `<git-common-dir>/ciu-worktree-budget.lock`.
  Under the lock: query Docker per candidate, decide (already-deployed
  current instance always allowed; otherwise refuse at/over cap before
  `yield`), `yield` once (the caller's own compose executor runs here, still
  under the lock), unlock in `finally` regardless of how the caller's code
  exits.

### `src/ciu/engine.py` — wiring (owner per packet)

- `main_execution` and `run_shipped` each resolve `worktree_cap =
  worktree.resolve_worktree_cap(repo_root)` once, immediately after
  `repo_root` is established from bootstrap — before any render step, so it
  runs even on `--dry-run`/`--render-toml` (cheap: no Docker/lock call
  itself) but is only ENFORCED around the real executor.
- Both functions wrap ONLY their existing `execute_docker_compose_with_logs`
  call in `with worktree.worktree_budget_slot(repo_root, worktree_cap,
  os.environ["DOCKER_NETWORK_INTERNAL"], working_dir.relative_to(repo_root)):`,
  translating any `worktree.WorktreeError` (refusal or a candidate-resolution
  failure) to `ComposeError` — the SAME translation S16.1's join already
  uses, so a capacity refusal fails only one stack in a multi-stack `ciu
  deploy` run rather than crashing the whole run.
- S16.1's post-up shared-infra join is UNCHANGED in position: it still runs
  strictly after the (now budget-wrapped) executor call succeeds, outside
  the `with worktree_budget_slot(...)` block — proven by an explicit
  ordering assertion in the engine test file, not just code reading.

### Judgment call — flagged, not silently invented

**The gap.** Neither the handoff nor SPEC S16 anywhere addresses what
`resolve_worktree_cap` should do when `repo_root` is not inside a git work
tree AT ALL. This matters because `main_execution`/`run_shipped` now call it
UNCONDITIONALLY, early, on every invocation — and this suite's OWN
established pattern (`test_spec_contracts.py:build_repo`'s own docstring:
*"The repo lives under /tmp which is NOT a git work tree, so the S1.7
gitignore probe no-ops cleanly"*) has hundreds of pre-existing tests running
`main_execution`/`run_shipped` for real against bare, non-git `tmp_path`
repos.

**What I did.** Made `resolve_worktree_cap` treat "not inside a git work
tree" as "no file policy" (skip the file lookup; a genuine `git rev-parse
--show-toplevel` failure short-circuits BEFORE `primary_ciu_root` is ever
called) while STILL honouring an explicit `CIU_MAX_CONCURRENT_WORKTREES`
ambient override — mirroring the packet's OWN literal "no global template"
row (`root_global = {}` on that narrow `ValueError`, ambient still
consulted afterward). This is not a novel invention: it is the exact same
shape the packet already specifies for the sibling "file policy source
absent" case, applied to a second way the same source can be absent, and it
matches this module's own pre-existing precedent for exactly this condition
(`engine._check_gitignore`, S1.7 — "not inside a git work tree → skip
silently").

**Why not BLOCKED.** The handoff's BLOCKED trigger is for a contract that
"cannot be met as specified" — this is squarely the opposite: a gap the
existing packet's own stated pattern (and the codebase's own established
precedent) resolves unambiguously once stated explicitly, not a case
requiring a new externally-visible decision. Verified empirically rather
than by assertion: the FULL pre-existing 1884-test suite passes unmodified
with this change, and `worktree_budget_slot` itself still refuses loudly
the moment ANY of its own candidate-resolution git calls fail for a
genuinely non-`None` cap it cannot honour (so an operator who explicitly
sets `CIU_MAX_CONCURRENT_WORKTREES` outside a git repo gets a loud failure
at the enforcement site, never a silently-ignored request).

### Tests

- `tests/tests/test_ciu_worktree_budget.py` (74 tests) — O1 (primary/
  offset/policy resolution, every invalid file/ambient value, the
  no-global-template and not-in-git soft paths, an unrelated render error
  still raising), O2 (the required nested-root fixture proving policy comes
  from the PRIMARY's own template and never a linked branch's conflicting
  one; the required missing-stack-sibling fixture with its `[INFO] [S16.3]`
  note; the required P02-composition fixture; malformed/missing-network/
  duplicate-network/unrenderable-candidate refusals; candidate env
  isolation against an ambient leak), and O3's worktree.py half (the
  decision table including already-deployed-may-rerun and a lowered cap
  after deployment; lock-released-on-every-outcome including mid-block
  failure and pre-yield refusal; an instrumented real `fcntl.flock` proving
  ONE continuous held section from count decision through the "executor";
  a genuine two-real-thread contention test — no sleeps, only
  `threading.Event` sync points with generous 60s hang failsafes per
  AUTHORING.md §3b-A — proving the second of two simultaneous cold starts
  re-counts under the lock and refuses once the first's deployment becomes
  Docker-visible).
- `tests/tests/test_ciu_engine_worktree_budget.py` (15 tests) — O3's engine
  half: cap resolved and passed through verbatim to the budget slot with
  the exact current network/stack-relative identity, for BOTH
  `main_execution` and `run_shipped`; a refusal translates to `ComposeError`
  before the executor runs; `--dry-run`/`--render-toml` never invoke the
  slot at all; an interrupted compose never reaches P02's join; an explicit
  ordering assertion proving the budget context exits BEFORE P02's join is
  attempted; two real end-to-end tests (the REAL `resolve_worktree_cap` +
  REAL `worktree_budget_slot`, only Docker/executor mocked) proving the
  full wiring never issues a capacity-related Docker call when there is no
  worktree family at all.
- `tests/tests/test_ciu_config_model.py` (+7 tests) — `write_rendered=False`
  writes nothing; the default preserves every existing caller; `environ=`
  backs BOTH `$VAR` expansion and the Jinja `env.*` context, proven in each
  direction (a candidate-only value resolves, an ambient-only value is
  correctly reported MISSING rather than silently pulled in); `environ=None`
  preserves the `os.environ` default exactly.
- Every Docker branch uses the handoff's named seam,
  `monkeypatch.setattr(worktree.procutil, "docker", fake)`
  (`test_ciu_deploy_actions.py:1348` precedent) — no live Docker socket
  anywhere in this file. `test_ciu_worktree_budget.py` exercises the REAL
  `worktree_budget_slot`/classifier throughout (never mocked); only the
  engine wiring file replaces it, per the handoff's explicit permission.
- **Mutation-tested the three highest-risk invariants by hand** (temporarily
  broke the implementation, confirmed the relevant test failed, reverted,
  confirmed a clean `git diff`):
  1. Weakened `_candidate_deployed`'s exact-project Docker filter to a bare
     `label=com.docker.compose.project` (value-less) — 3 tests failed
     (`TestCandidateDeployed`'s own suite, including the P02-composition
     fixture) via the strict fake's `unscripted docker call` assertion.
  2. Dropped the git-root-to-CIU-root offset in `_resolve_budget_candidates`
     (`candidate_ciu_root = entry.path` instead of `entry.path / offset`) —
     18 tests failed across the classifier, decision-table, lock-discipline,
     and two-thread-contention suites (the latter caught cleanly by its
     60s hang failsafe rather than hanging the run, confirming that
     failsafe itself works as designed).
  3. Split the lock's single held section into release-then-reacquire
     around `yield` — `test_lock_held_continuously_from_count_decision_through_executor`
     failed immediately with the exact expected assertion
     (`['LOCK_EX', 'LOCK_UN', 'LOCK_EX']` observed where `['LOCK_EX']` was
     required).

## Final gate

Committed at `74bad946` (implementation), `d13bdec9` (fixed 6 pre-existing
engine tests broken by the new unconditional `DOCKER_NETWORK_INTERNAL` read
— see "A real regression the real gate caught" below), `b6fe04d3` (closed 4
`--cov-branch` gaps the real gate's branch-coverage mode surfaced that a
local non-branch run did not).

### A real regression the real gate caught (worth recording)

The FIRST real-gate run (after `74bad946`) failed 6 pre-existing tests in
`test_ciu_engine_branch101.py`, `test_ciu_engine_direct94.py`,
`test_ciu_engine_direct99.py`, and `test_ciu_engine_remaining_boundaries.py`
— all a `KeyError: 'DOCKER_NETWORK_INTERNAL'` from the new budget-slot
wiring's `os.environ["DOCKER_NETWORK_INTERNAL"]` read, which those fixtures
never populate (they fully stub `bootstrap_workspace_env` to a no-op, and
nothing read that key at that point in the pipeline before this change).
**Every local `pytest` run I had done before that — including full-suite
runs — passed clean**, because this devcontainer's own shell ambiently
exports `DOCKER_NETWORK_INTERNAL` (ciu.env for a sibling project, sourced
into the shell profile), silently supplying the missing key to every local
test process. Confirmed by re-running locally with the key (and every other
ambient `CIU_`/`DOCKER_`/`REPO_`/`CONTAINER_`/`INSTANCE_ID` variable this
devcontainer happens to export) explicitly unset: all 6 failed there too.
Fixed with a one-line `monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", ...)`
per fixture (`d13bdec9`) — none of the 4 files are in this handoff's
`scope.touch` list, but none are in `forbid` either, and leaving 6 tests
broken was not an option. This is exactly why the task brief's insistence on
the REAL gate (not the cockpit) matters: a devcontainer with a contaminated
ambient environment is its own false-green risk, on top of the declared
gate's env-passthrough gap documented in the Baseline section above.

### Real declared-gate output (after `b6fe04d3`)

`docker run --rm --cgroup-parent=nyxloom-gates.slice -e
CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice -v
/home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c
'cd <worktree>/ciu && export PYTHONPATH=src && python run-ciu-tests.py && ...'`
(the `-e CGROUP_PARENT_DEV_BACKGROUND` addition documented in Baseline —
without it this specific host's declared argv cannot pass regardless of this
package, same as the baseline measurement):

```
Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/cli.py                                     399     27    128      4    94%   350-373, 595-600, 814, 817, 820
src/ciu/composefile.py                             344     14    168      2    96%   809-833, 924
src/ciu/deploy.py                                 1027      3    422      1    99%   725, 745-746
src/ciu/engine.py                                  854      0    278      0   100%
src/ciu/governance.py                              382      1    158      2    99%   189, 197->201
src/ciu/ksm.py                                     180     56     64      6    68%   86, 118-120, 133-134, 140, 151, 159, 165-166, 214, 260-261, 292-307, 327-385, 405-406, 414-415
src/ciu/worktree.py                                485     23    172      3    96%   215, 421-431, 446-473, 565, 769
--------------------------------------------------------------------------------------------
TOTAL                                             6376    124   2500     18    98%
Coverage JSON written to file coverage.json
FAIL Required test coverage of 100% not reached. Total coverage: 98.13%
============================ 1983 passed in 13.61s =============================
```

(every other module not listed is 100%.) `engine.py` — the ONLY file this
package adds new statements to besides `worktree.py`/`config_model.py` — is
**100%**. `worktree.py`'s 5 remaining missing-line groups (`215, 421-431,
446-473, 565, 769`) are ciu-P02's own documented pre-existing baseline set
(`210, 224, 416-426, 441-468, 560, 764`) shifted by the fixed +5-line offset
this package's new imports at the top of the file introduce — same
statements (`list_worktrees`, `_generate_env_in`, `_clean_in`, and the
pre-existing tails of `add()`/`remove()`'s `git` failure branches), none of
which this package touches; `224` from that set is now incidentally covered
(not a regression — extra coverage, not missing coverage). `config_model.py`
is **100%** (was already 100% pre-existing; this package's additions there
are fully covered). The blanket `--cov-fail-under=100` failure is the SAME
pre-existing, unrelated shortfall as every measurement in this LOG's
Baseline section — `run-ciu-tests.py`'s own blanket check runs before the
changed-line half ever does, and was already failing this way before
ciu-P01.

Changed-line gate against that SAME `coverage.json`:

```
diff-coverage OK: 190/190 changed executable lines covered (100.0% ≥ 100.0% floor)
```

**100% of this package's own changed executable lines are covered.** No
`--allow-excluded`, no `pragma: no cover` on any changed line. Both numbers
reported honestly, same as ciu-P01/P02: the declared gate cannot pass
end-to-end on this host (two pre-existing, unrelated gaps — the blanket
100% floor, and the `$CGROUP_PARENT_DEV_BACKGROUND` env-passthrough the
declared `docker run` argv itself is missing), but the changed-line floor
this package is actually accountable for is met, and every REQUIRED fixture
named in the handoff's traceability table is present and passing:

- O1: the nested-root fixture (`test_reads_from_primary_when_invoked_from_linked_worktree`,
  `test_linked_branchs_own_conflicting_policy_is_ignored`), invalid file/
  ambient-value refusals, the no-global-template and not-in-git soft paths,
  an unrelated render error still raising
  (`test_unrelated_render_error_still_raises`).
- O2: the required nested-root fixture (`test_eligible_linked_worktree_included_with_own_env_and_project`),
  the required missing-stack-sibling fixture
  (`test_missing_stack_sibling_skipped_with_info_note_and_no_docker_query`),
  the required P02-composition fixture
  (`test_p02_composition_fixture_child_network_on_other_project_not_deployed`).
- O3: the already-deployed-may-rerun rule under load
  (`test_lowered_cap_after_current_deployed_still_allows_rerun_even_far_over`),
  the instrumented-flock continuous-hold proof
  (`test_lock_held_continuously_from_count_decision_through_executor`), the
  genuine two-thread contention proof
  (`test_two_concurrent_cold_starts_second_waiter_recounts_and_refuses`),
  and the engine ordering proof
  (`test_budget_context_exits_before_p02_join_runs`, both paths).

## Summary

- `ciu-P03-worktree-concurrency-budget` is landed on this branch at commit
  `b6fe04d3` (implementation `74bad946`, test-fixture fix `d13bdec9`,
  branch-coverage closure `b6fe04d3`).
- CIU-24 marked FIXED in `KNOWN_ISSUES_TODO_BACKLOG.md` with code/test/SPEC
  evidence pointers, per that file's own house rule.
- SPEC.md gained S16.3; CONFIG.md gained `[ciu.worktree]` and a pointer to
  the ambient `CIU_MAX_CONCURRENT_WORKTREES` override.
- One judgment call made and flagged (not silently invented): "not inside a
  git work tree" resolves to "no file policy" in `resolve_worktree_cap` —
  see "Judgment call" above.
- One real regression the real gate caught that no local run did (ambient
  `DOCKER_NETWORK_INTERNAL` in this devcontainer's own shell masked it) —
  see "A real regression the real gate caught" above.
- Nothing else required inventing an externally-visible interface, default,
  or bound. No BLOCKED condition was hit.

## Round 2 — independent review findings and fixes (post `ea05b9b6`)

Independent review verdict: REJECT. Reviewer's own framing: "the
implementation itself is correct... the proof isn't" — the offset
arithmetic, the exact-label classifier's genuine immunity to ciu-P02's
shared-infra join (re-verified by the reviewer with a custom
Docker-filter-interpreting fake, not just my scripted responder), the
already-deployed-may-rerun rule, and both self-caught bugs from round 1 all
held. Two load-bearing invariants had zero test defense; fixed exactly
those two, touched nothing else.

1. **The family-wide lock LOCATION had no oracle.** The reviewer mutated
   `worktree_budget_slot`'s `lock_path = _git_common_dir(repo_root) /
   _BUDGET_LOCK_NAME` to `repo_root / _BUDGET_LOCK_NAME` (a per-worktree
   -local lock) and all 1983 round-1 tests still passed — my two-thread
   contention test passes the SAME `repo_root` to both simulated threads,
   so a shared and a per-worktree-local lock are indistinguishable there;
   my `_git_common_dir` unit tests all mocked `worktree._git`, so none
   exercised what a REAL linked worktree's `--git-common-dir` actually
   reports. Fixed with
   `test_lock_location_is_shared_across_the_whole_worktree_family` in
   `tests/tests/test_ciu_worktree_budget.py`: real (unmocked) git, a real
   primary + a real linked worktree, asserting
   `_git_common_dir(primary) == _git_common_dir(linked)` and then that
   taking a budget slot from EITHER worktree produces exactly ONE lock
   file on disk, at that shared path. Re-verified by reproducing the
   reviewer's exact mutation myself: the new test fails
   (`expected exactly one shared lock file, found {...two paths...}`)
   while every other test in the file still passes; reverted, confirmed a
   clean `git diff` and the real fix passing again.
2. **The bounded critical section had no oracle.** The reviewer moved
   `_resolve_budget_candidates(...)` (every candidate's `ciu.env` parse and
   `render_global_chain` call) to run INSIDE the flock instead of before
   it, and all 1983 round-1 tests still passed — `_FlockRecorder` only
   records `LOCK_EX`/`LOCK_UN`, which is identical either way since moving
   work around inside the same lock/unlock pair doesn't change what it
   sees. Fixed with
   `test_candidate_rendering_finishes_before_the_first_lock_ex`: wraps
   `worktree.config_model.render_global_chain` into the SAME ordered
   event list the flock recorder already uses, then asserts every
   recorded `"render"` event's index is strictly less than
   `events.index("LOCK_EX")` — directly proving resolution happens before
   the lock is taken, not just that the lock itself behaves correctly once
   entered. Re-verified the same way: reproduced the reviewer's exact
   mutation (moved `_resolve_budget_candidates` inside the `try:` block
   after `fcntl.flock(...)`), confirmed the new test alone fails
   (`a candidate render occurred at/after LOCK_EX: ['LOCK_EX', 'render',
   'render', 'LOCK_UN']`) while the other 79 tests in the file still pass,
   reverted, confirmed a clean `git diff` and the real fix passing again.

Both new tests live in `tests/tests/test_ciu_worktree_budget.py`; no other
file was touched this round.

**Flagged, not fixed (explicitly out of this package's scope per the review
instruction):** `resolve_worktree_cap` (and `_ciu_root_offset`/`git_toplevel`)
gate "is this a git work tree at all" on `git rev-parse --show-toplevel`
returning exit code 0. Git also exits 128 for "dubious ownership" (a
bind-mounted repo owned by a different uid than the process reading it —
this project's own gate-container shape, and standard in CI), which is
indistinguishable from "not a git repository" by return code alone. A
genuine worktree family hitting a dubious-ownership error therefore
silently resolves to "no cap, no Docker call, no lock, no diagnostic" —
exactly the state a host-capacity policy is least allowed to fail open in.
This mirrors an existing, already-accepted precedent
(`engine._check_gitignore`, S1.7) that fails open the same way for a lint
check, not a host-capacity limit, so it is a real, load-bearing gap, not a
novel risk class. Filing as a CIU-25-adjacent follow-up: discriminate
"genuinely not a git work tree" (exit 128, "not a git repository") from
"is a git work tree but git refuses for another reason" (exit 128,
"detected dubious ownership" or any other message) — e.g. via `git
rev-parse --is-inside-work-tree` returning `false` specifically, versus a
non-zero/error exit — and treat the latter as a loud `[S16.3]` failure
rather than a silent no-cap. Not implemented here per explicit review
instruction to fix only the two defects above.

### Round 2 final gate

Run after committing `<round-2 commit>`. See below.
