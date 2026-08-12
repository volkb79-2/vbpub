# LOG — ciu-P02-worktree-shared-infra-join

Implementer: Claude Sonnet 5. Handoff:
`nyxloom-trove/handoffs/ciu-P02-worktree-shared-infra-join.md`.
`input_revision`: `4756b6085b1d90dce0c04bcaf0325a7f349c0bd0`; this worktree's
actual starting HEAD is `4bf271ec55b6bf606acbdef1291fd7675bffa9e` (a later
commit on the same branch that only re-pinned the handoff's `input_revision`
pointer and refreshed `add()`'s signature to match ciu-P01's landed
`data_isolation`/`provisioner` parameters — no other `src/` delta between the
two). Verified with `git rev-parse HEAD`.

## Baseline (before any code change)

`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT PYTHONPATH=src python3 run-ciu-tests.py`
(the project's own gate helper, `[gates.tester-unified]`'s second half without
the docker wrapper — no docker socket reachable from this sandbox either; the
`-u` unset avoids an ambient contaminated `REPO_ROOT`/`PHYSICAL_REPO_ROOT` in
this devcontainer that is unrelated to this package, same practice as
ciu-P01's LOG):

```
Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/cli.py                                     396     27    128      4    94%   350-373, 586-591, 805, 808, 811
src/ciu/composefile.py                             344     14    168      2    96%   809-833, 924
src/ciu/deploy.py                                 1027      3    422      1    99%   725, 745-746
src/ciu/governance.py                              382      1    158      2    99%   189, 197->201
src/ciu/ksm.py                                     180     56     64      6    68%   86, 118-120, 133-134, 140, 151, 159, 165-166, 214, 260-261, 292-307, 327-385, 405-406, 414-415
src/ciu/worktree.py                                166     24     52      4    86%   199, 213, 238-248, 263-290, 345, 534
--------------------------------------------------------------------------------------------
TOTAL                                             6022    125   2368     19    98%
Coverage JSON written to file coverage.json
FAIL Required test coverage of 100% not reached. Total coverage: 98.00%
====================== 1827 passed, 8 warnings in 13.66s =======================
```
(every other module not listed above is 100%; `run-ciu-tests.py` then exits
non-zero because `--cov-fail-under=100` fails on the blanket total — this is
the SAME pre-existing shortfall ciu-P01's LOG documented, not something this
package introduces or must eliminate outside its own touched lines.)

`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT PYTHONPATH=../nyxloom/src python3 -m
nyxloom.coverage_gate --repo . --base main --coverage-json coverage.json
--source src/ciu` (the changed-line gate):

```
diff-coverage NO MEASUREMENT: resolved base (4bf271ec55b6) IS HEAD -- there is
no delta to measure.
```

i.e. HEAD == `main` at start, exactly as the task brief said to verify. The
blanket-100% shortfall above is 100% pre-existing (`worktree.py`'s partial
coverage on `_generate_env_in`/`_clean_in`'s subprocess bodies and
`remove`'s tail, `ksm.py` 68%, and scattered gaps in `cli.py`/`composefile.py`/
`deploy.py`/`governance.py` this package never touches) — not this package's
problem per the handoff's own note that this measurement is about the
CHANGED-line delta, not the blanket total.

## Requirement-to-oracle traceability table (pre-implementation)

| Oracle | Requirement | Where it will be proven |
|---|---|---|
| O1 | `worktree add --shared-infra REF --shared-infra-services ... --shared-infra-ref-projects ...` validates REF (registered worktree, non-empty `DOCKER_NETWORK_INTERNAL`, every declared reference project has a running container on that network, AND-combined) BEFORE `git worktree add`; only then creates the checkout and writes the 4 intent fields to the child's own `ciu.env`. All-or-nothing group with `--profile`. | `worktree.add()` + `parse_shared_infra_intent()`; `test_ciu_worktree_shared_infra.py` |
| O2 | After a successful non-dry-run compose up, `connect_shared_infra_after_up` re-validates the reference, discovers only THIS compose project's declared-service containers via label filters, and connects only those absent from the reference network. Docker-STATE (re-inspect after non-zero connect), not Docker-TEXT, decides concurrent-no-op vs genuine failure. | `worktree.connect_shared_infra_after_up()`; `engine.main_execution`/`run_shipped` call sites; `test_ciu_worktree_shared_infra.py`, `test_ciu_engine_shared_infra.py` |
| O3 | Every precondition (ref registered/live, network unchanged, every declared target service has a running container) is checked BEFORE any connect. A genuine connect failure triggers reverse-order disconnect of ONLY this invocation's zero-return connects — never a pre-existing member, never a non-zero-but-now-present no-op. No `docker compose down` on failure. Dry runs never touch Docker. | `worktree.connect_shared_infra_after_up()`; `test_ciu_worktree_shared_infra.py` |

No oracle requires inventing an externally-visible interface, default, or
bound beyond what the Implementation packet already pins (constants, dataclass
shape, `add()` signature, env-var grammar, Docker invocations, decision
table). Proceeding to implementation.

## What was built

### `src/ciu/worktree.py` — the complete S16.1 protocol (owner, per the packet)

- `SHARED_INFRA_REF_PATH` / `_NETWORK` / `_SERVICES` / `_REF_PROJECTS`
  constants and the frozen `SharedInfraIntent` dataclass, exactly as specified.
- `parse_shared_infra_intent(values: Mapping[str, str]) -> SharedInfraIntent |
  None` — the sole reader. All four absent returns `None`; any partial,
  empty, malformed, or duplicate-containing group raises `WorktreeError`.
- `_split_unique_list` — shared blank/duplicate/order-preserving validation,
  used by both add-time CLI parsing and stored-intent parsing (one rule, one
  error vocabulary, matching the packet's grammar note).
- `_check_reference_network_and_projects(network, ref_projects)` — the two
  reference-live Docker checks (network exists; every declared project has a
  running container on it, AND-combined), factored out because O1's add-time
  preflight and O2's post-up revalidation both need the IDENTICAL check.
- `_preflight_shared_infra_for_add` — resolves REF via `find_worktree`'s own
  grammar, reads its `ciu.env` by explicit path (never `find_workspace_env`),
  validates the lists, and runs the liveness check — all BEFORE any side
  effect. Wired into `add()` right after the `target.exists()` check and
  before `_git(["worktree", "add", ...])`, gated on the all-or-nothing group
  (`shared_infra` + both lists + non-empty `profile`).
- `add()` writes the four intent fields into the child's own `ciu.env` after
  `_generate_env_in` succeeds (and after the existing profile/data-isolation
  blocks), using the exact `export KEY="value"` shape the packet specified —
  verified byte-for-byte round-trippable through `parse_workspace_env` +
  `parse_shared_infra_intent` in a dedicated test.
- `connect_shared_infra_after_up(repo_root, compose_project, intent)` — the
  O2/O3 transaction: re-resolves REF via `find_worktree(repo_root,
  str(intent.ref_path))` (the same grammar, applied to the recorded absolute
  path), re-reads its `ciu.env`, requires the network unchanged, refuses a
  declared reference project equal to `compose_project`, re-runs the shared
  liveness check, gathers every declared service's running containers in
  THIS compose project (label filters, fail before any Docker network
  mutation if any service has zero), sorts by name for determinism, snapshots
  reference-network membership, and connects only the absent targets.
- **Docker-STATE, not Docker-TEXT** (`_network_container_ids` +
  the re-inspect-on-non-zero branch): on every non-zero connect result, CIU
  re-inspects the network for that SAME container ID — present is a
  concurrent no-op (never rolled back), absent is genuine. No code path
  anywhere reads `result.stderr`/`stdout` to decide the verdict, only to
  build a human-readable message after the verdict is already known.
- **A real correctness trap I caught and fixed:** `docker ps --format
  {{.ID}}` returns a TRUNCATED 12-char ID by default, while `docker network
  inspect`'s `.Containers` map is keyed by the FULL 64-char ID. Comparing
  them directly would never match, silently treating every already-connected
  target as absent (spurious reconnect attempts) or — worse — every genuinely
  absent target as a truncation-collision false match. Fixed by adding
  `--no-trunc` to the service-discovery `docker ps` call so both sides
  compare full IDs. Documented in `_network_container_ids`'s docstring and
  exercised by every O2/O3 test (all fixtures use synthetic 64-char IDs).
- `_disconnect_rollback` — reverse-order disconnect of only THIS call's own
  successful connects, collecting (never swallowing) any disconnect failure
  for the final error message; `_connect_failure_message` composes the
  original failure with any rollback failures appended.
- Module-level `from . import procutil` added (previously only local,
  function-scoped imports existed) — required so the handoff's own named test
  seam, `monkeypatch.setattr(worktree.procutil, "docker", fake)`, is a valid
  attribute access at all. Verified this resolves to the identical `ciu.procutil`
  module object regardless of call site.

### `src/ciu/cli.py` — three new flags, zero new validation

`--shared-infra REF`, `--shared-infra-services S1,S2`,
`--shared-infra-ref-projects R1,R2` added to `worktree add`'s subparser and
forwarded verbatim to `worktree.add`. The CLI owns no parallel validation or
fallback, per the packet.

### `src/ciu/engine.py` — post-up wiring in both up paths

- `main_execution`: after `execute_docker_compose_with_logs` returns and
  `docker_result["status"] == "success"` (never on `"error"` — that already
  raises — and never on `"interrupted"`), parses the intent from
  `os.environ` and, if present, calls `connect_shared_infra_after_up` with
  the SAME `project` value already passed to Compose. A `WorktreeError` is
  translated to `ComposeError` (retaining the full `[S16.1]` message) — this
  is load-bearing, not cosmetic: `deploy._run_stack` catches
  `engine.ComposeError` specifically to mark one stack failed and continue
  the multi-stack run; an unwrapped `WorktreeError` would escape that catch
  and crash the whole `ciu deploy` run instead of failing just this stack.
- `run_shipped`: identical wiring after its own compose call, using
  `shipped_project`.
- **Judgment call, flagged for review rather than invented silently
  (corrected after independent review — see "Round 2" below for what was
  wrong in the first framing):** `run_shipped` has a pre-existing S8.7
  legacy-fallback path where `compose_project_name` can raise (no
  `deploy.project_name`/`environment_tag` configured) and `shipped_project`
  becomes `None` — Compose then derives its own project from the cwd
  basename, a value CIU never learns. The packet's flow says to call
  `connect_shared_infra_after_up` with "the same exact
  `compose_project_name(...)` value passed to Compose", which in this
  fallback is `None`. This combination is arguably covered by the handoff's
  own `escalate_if` #1 — "the post-up join cannot enumerate and connect only
  containers carrying... the current compose-project label... through
  `procutil.docker`" is literally true here, since there is no known
  compose-project value to filter by at all. Rather than either (a) silently
  skip the join on a DECLARED intent, which the module's whole design
  philosophy refuses to do elsewhere, or (b) pass `None` through anyway
  (which stringifies into `label=com.docker.compose.project=None`, matching
  no real container and simply failing the existing "no running container"
  check — a loud, harmless failure, not silent corruption), I made it a
  separate, more specific `[S16.1]` `ComposeError` naming the missing config
  directly, proven by `test_unresolvable_compose_project_with_intent_refuses`
  in `test_ciu_engine_shared_infra.py`, and left the PRE-EXISTING no-intent
  fallback path completely unchanged (proven by the sibling
  `..._without_intent_is_unaffected` test). The actual case for refusing
  here is that CIU cannot know the value Compose itself will choose to scope
  the join to — not that a wrong value would corrupt anything.

### Tests

- `tests/tests/test_ciu_worktree_shared_infra.py` (38 tests) — O1 (add-time
  preflight: success + full round-trip through `parse_shared_infra_intent`,
  unresolved REF, missing partner flag, missing profile, ordinary add
  unaffected, absent/unreadable/network-less reference env, the REQUIRED
  masquerader fixture, the REQUIRED all-R AND-combine fixture, blank/duplicate
  list items, `FileNotFoundError`/`OSError` on every Docker call site) and
  O2/O3 (`connect_shared_infra_after_up`: the REQUIRED two-selected-one-
  unrequested-service fixture, idempotent rerun, the REQUIRED concurrent-join
  fixture, the REQUIRED genuine-failure fixture, the REQUIRED three-target
  rollback discriminator proving the disconnect set is exactly `[B]`, ref
  no-longer-registered, network-changed, self-project rejection, absent
  target service, masquerader/AND-combine at post-up, membership-inspect
  failure, and a combined rollback fixture exercising BOTH a raising AND a
  non-zero disconnect in one reverse-order pass).
- `tests/tests/test_ciu_engine_shared_infra.py` (11 tests) — proves the
  ENGINE call sites: success wiring with the exact project value, no-intent
  unaffected, dry-run never touches the seam, interrupted-compose never
  joins, `WorktreeError`→`ComposeError` translation, and the `run_shipped`
  fallback judgment call above (both branches).
- `tests/tests/test_ciu_cli_worktree.py` — extended `fake_add` signatures for
  the three new kwargs (existing tests), plus a new
  `TestWorktreeAddSharedInfraDispatch` class for flag forwarding/defaults/
  error-mapping.
- All Docker branches use `monkeypatch.setattr(worktree.procutil, "docker",
  fake)` — a strict, ordered `ScriptedDocker` fake that raises
  `AssertionError` on any unscripted call, so a regression that issues an
  extra/different Docker command fails loudly. No wall-clock waits, no real
  git/docker/network/filesystem outside each test's own `tmp_path`.
- **One real test bug caught and fixed during development:**
  `bootstrap_workspace_env(define_root=...)` writes every parsed `ciu.env`
  key straight into the REAL `os.environ` (not through `monkeypatch`), so a
  `with_intent=True` engine test running before a `with_intent=False` one
  leaked the four `CIU_SHARED_INFRA_*` keys across tests regardless of file
  order — exactly AUTHORING.md §3b-B's "process-global state without
  restoring it" trap. Fixed by pre-registering all four keys with
  `monkeypatch.delenv(..., raising=False)` before bootstrap runs in every
  test, so monkeypatch owns their teardown regardless of what bootstrap later
  writes. Confirmed with repeated full-suite `-n auto` runs.

## Final gate (after committing `1664b4d5`)

`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT PYTHONPATH=src python3 run-ciu-tests.py`:

```
Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/__init__.py                                  3      0      0      0   100%
src/ciu/__main__.py                                  3      0      2      0   100%
src/ciu/activate.py                                119      0     46      0   100%
src/ciu/cli.py                                     399     27    128      4    94%   350-373, 595-600, 814, 817, 820
src/ciu/cli_utils.py                                11      0      0      0   100%
src/ciu/composefile.py                             344     14    168      2    96%   809-833, 924
src/ciu/config_constants.py                         28      0      4      0   100%
src/ciu/config_model.py                            237      0    106      0   100%
src/ciu/deploy.py                                 1027      3    422      1    99%   725, 745-746
src/ciu/deploy_pkg/__init__.py                       7      0      0      0   100%
src/ciu/deploy_pkg/health.py                       192      0     98      0   100%
src/ciu/deploy_pkg/http_util.py                     24      0      2      0   100%
src/ciu/deploy_pkg/phases.py                        69      0     40      0   100%
src/ciu/deploy_pkg/profiles.py                     123      0     60      0   100%
src/ciu/deploy_pkg/registry.py                      38      0     20      0   100%
src/ciu/dev.py                                     194      0     74      0   100%
src/ciu/diagnose.py                                 79      0     34      0   100%
src/ciu/engine.py                                  844      0    278      0   100%
src/ciu/governance.py                              382      1    158      2    99%   189, 197->201
src/ciu/hooks/__init__.py                            0      0      0      0   100%
src/ciu/hooks/examples/__init__.py                   0      0      0      0   100%
src/ciu/hooks/examples/post_compose_example.py       5      0      0      0   100%
src/ciu/hooks/examples/pre_compose_example.py        4      0      0      0   100%
src/ciu/hooks_runner.py                            115      0     52      0   100%
src/ciu/hosts.py                                    35      0     16      0   100%
src/ciu/ksm.py                                     180     56     64      6    68%   86, 118-120, 133-134, 140, 151, 159, 165-166, 214, 260-261, 292-307, 327-385, 405-406, 414-415
src/ciu/paths.py                                    30      0     12      0   100%
src/ciu/procutil.py                                 17      0      2      0   100%
src/ciu/provisioning.py                            256      0    120      0   100%
src/ciu/secrets/__init__.py                          3      0      0      0   100%
src/ciu/secrets/directives.py                      131      0     72      0   100%
src/ciu/secrets/materialize.py                     212      0     60      0   100%
src/ciu/secrets/providers.py                       111      0     38      0   100%
src/ciu/transport_ssh.py                           219      0     70      0   100%
src/ciu/warn_policy.py                               9      0      2      0   100%
src/ciu/workspace_env.py                           428      0    178      0   100%
src/ciu/worktree.py                                311     24    110      4    93%   210, 224, 416-426, 441-468, 560, 764
--------------------------------------------------------------------------------------------
TOTAL                                             6189    125   2436     19    98%
Coverage JSON written to file coverage.json
FAIL Required test coverage of 100% not reached. Total coverage: 98.05%
====================== 1879 passed, 8 warnings in 14.03s =======================
```

`engine.py` is at **100%** and `cli.py`'s missing lines are byte-identical to
the pre-existing baseline set (shifted only by line-number offset from the
new code inserted above them: `350-373`/`595-600` (was `586-591`)/`814, 817,
820` (was `805, 808, 811`) — same statements, none of them mine).
`worktree.py`'s 24 missing lines are its OWN pre-existing baseline set
(`199, 213, 238-248, 263-290, 345, 534` before my edits), also only shifted
by line-number offset (`210, 224, 416-426, 441-468, 560, 764` after) — every
one of them inside `list_worktrees`, `_generate_env_in`, `_clean_in`, and the
pre-existing tails of `add()`/`remove()`'s `git` failure branches, none of
which this package touches. The blanket `--cov-fail-under=100` failure is
therefore the SAME pre-existing shortfall the baseline measured (52 statements
became 145 new ones, all 145 covered; 0 new misses), confirmed by the
changed-line gate below.

`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT PYTHONPATH=../nyxloom/src python3 -m
nyxloom.coverage_gate --repo . --base main --coverage-json coverage.json
--source src/ciu`:

```
diff-coverage OK: 167/167 changed executable lines covered (100.0% ≥ 100.0% floor)
```

**100% of this package's own changed executable lines are covered.** No
`--allow-excluded` used; no `pragma: no cover` on any changed line.

Every REQUIRED fixture named in the handoff's traceability table is present
and passing (satisfying `canary-verified`'s intent — there is no separate
canary CLI in this repo; verified by grep):

- O1: masquerader fixture (`test_masquerader_fixture_refuses_before_git_add`,
  and its post-up mirror), all-R AND-combine fixture (`test_all_r_and_combined_fixture_refuses_before_git_add`,
  and its post-up mirror).
- O2: two-selected-one-unrequested-service fixture
  (`test_success_connects_only_absent_selected_targets`), concurrent-join
  fixture (`test_concurrent_join_fixture_non_zero_then_present_is_success_no_rollback`),
  genuine-failure fixture (`test_genuine_failure_fixture_non_zero_then_absent_raises_and_no_rollback_needed`).
- O3: three-target rollback discriminator
  (`test_three_target_rollback_discriminator`), asserting the disconnect set
  is exactly `[B]`.

## Summary

- `ciu-P02-worktree-shared-infra-join` is landed at commit `1664b4d5` on this
  branch (was `4bf271ec`).
- CIU-22 marked FIXED in `KNOWN_ISSUES_TODO_BACKLOG.md` with the code/test/
  SPEC evidence pointers, per that file's own house rule.
- SPEC.md gained S16.1; CONFIG.md gained the four `CIU_SHARED_INFRA_*` env
  keys and a worked CLI example.
- One judgment call made and flagged (not silently invented): `run_shipped`'s
  pre-existing legacy-fallback path (no `deploy.project_name`/
  `environment_tag`) now REFUSES with a `[S16.1]` `ComposeError` if a
  shared-infra join is declared, since the fallback provides no compose
  project string to scope the join to — arguably covered by `escalate_if`
  #1 (the join genuinely cannot enumerate/connect by compose-project label
  without a known project value), decided inline as a fail-loud refusal
  rather than escalated, per review. CIU refuses because it cannot know the
  value Compose itself would choose, not because a wrong value would corrupt
  anything (a `None`-derived filter just matches nothing and fails the
  existing no-running-container check harmlessly). Both branches (with and
  without a declared intent) are tested. Now also documented in SPEC.md
  S16.1 itself (added in review round 2).
- Nothing else required inventing an externally-visible interface, default,
  or bound. No BLOCKED condition was hit.

## Round 2 — independent review findings and fixes (post `b5d1bd5a`)

Independent review verdict: REJECT, five concrete defects, all narrowly
scoped; core O1/O2/O3 design confirmed correct by mutation testing (verified
the OR-instead-of-AND, snapshot-absence-rollback, and truncated-ID attacks
are all genuinely caught). Fixed exactly the five defects named, re-verified
each by mutation testing myself, touched nothing else.

1. **O2's "Docker STATE, not TEXT" discipline had zero test defense.** The
   concurrent-join fixture's `stderr="endpoint already exists"` happened to
   be exactly what a naive text-matcher would also key on, so a
   state-check-to-text-match regression passed every test undetected. Fixed
   by changing the fixture's `stderr` to `"context deadline exceeded"` (an
   unrelated message) in `test_ciu_worktree_shared_infra.py`. Re-verified by
   temporarily REPLACING the real state-based check with
   `if "already exists" in detail: continue` in `worktree.py` — confirmed
   the mutant now fails
   `test_concurrent_join_fixture_non_zero_then_present_is_success_no_rollback`,
   then reverted the mutation (clean `git diff` on `worktree.py` for this
   change alone) and confirmed the real implementation still passes.
2. **`_network_container_ids` didn't wrap `OSError`/`FileNotFoundError`.**
   Every other Docker call site in this feature wraps it into
   `WorktreeError`; this one didn't, so an `OSError` here would escape
   `connect_shared_infra_after_up` raw, miss `engine`'s `except
   worktree.WorktreeError`, and crash the whole `ciu deploy` run instead of
   failing one stack. Fixed with the same `try/except (FileNotFoundError,
   OSError)` pattern used everywhere else in the module. New test:
   `test_membership_inspect_filenotfound_raises_worktree_error`.
3. **`parse_shared_infra_intent(os.environ)` sat OUTSIDE the
   `WorktreeError`→`ComposeError` translation** in both `main_execution` and
   `run_shipped`, so a partial/malformed stored intent (a state the S16.1
   decision table itself names) would also crash the whole run via the same
   untranslated path. Fixed by moving both the parse call and the join call
   inside one shared `try/except worktree.WorktreeError` block in each
   function. New engine-level tests:
   `test_malformed_stored_intent_translates_to_compose_error_not_whole_run_crash`
   in both `TestMainExecutionSharedInfraWiring` and
   `TestRunShippedSharedInfraWiring`, asserting `engine.ComposeError` (not a
   bare `WorktreeError` or an uncaught exception) and that
   `connect_shared_infra_after_up` is never reached. Re-verified by
   temporarily moving `main_execution`'s parse call back outside the `try`
   — confirmed the mutant's new malformed-intent test failed (a raw
   `WorktreeError` escaped instead of `ComposeError`), then reverted and
   confirmed a clean `git diff` restored the exact fixed form.
4. **A failed re-inspect after a failed connect skipped rollback entirely.**
   If target B connected successfully (a REAL membership created) and
   target C's connect then failed AND the re-inspect call used to classify
   that failure ALSO failed, the re-inspect's own (now correctly wrapped,
   per #2) `WorktreeError` propagated straight past the rollback logic,
   leaving B's membership stranded — a direct O3 violation ("a partial-connect
   failure that leaves CIU-added memberships behind"). Fixed by wrapping
   that specific re-inspect call in its own `try/except WorktreeError`,
   running `_disconnect_rollback` on the accumulated `connected` list before
   re-raising, and appending any rollback failure to the original message
   (never swallowing either). New tests:
   `test_reinspect_failure_after_failed_connect_still_rolls_back` and
   `test_reinspect_failure_and_rollback_disconnect_failure_both_surface`
   (the latter exercising a rollback disconnect that ALSO fails, so both
   failure shapes surface in one message). Re-verified by temporarily
   removing the wrapping try/except — confirmed both new tests failed, then
   reverted and confirmed the real fix passes all 41 tests in the file.
5. **The claimed test-pollution fix did not actually isolate.**
   `monkeypatch.delenv(key, raising=False)` only records an undo action when
   the key is ALREADY present at call time; on a clean worker it is a true
   no-op with nothing registered, so `bootstrap_workspace_env`'s later
   direct `os.environ[key] = value` write for the four
   `CIU_SHARED_INFRA_*` keys was never cleaned up by `monkeypatch`'s
   teardown. Fixed by replacing the per-test `_base_env` delenv loop with a
   module-level `@pytest.fixture(autouse=True) def
   _isolate_shared_infra_env()` that snapshots the real `os.environ` state
   for these four keys before every test, clears them, yields, then
   forcibly restores (or deletes) them after — independent of whatever
   `monkeypatch` did or didn't track. Re-verified with the reviewer's own
   exact attack: a standalone two-test probe where test 1 runs an ordinary
   `with_intent=True` `main_execution` (leaking the four keys via
   `bootstrap_workspace_env` under the OLD buggy fixture) and test 2 is a
   plain no-intent `main_execution` that never mentions shared-infra at
   all (mirroring a real, unrelated sibling test elsewhere in the repo).
   Under the OLD (reintroduced) buggy fixture this reproduced the reviewer's
   EXACT symptom verbatim: `ciu.engine.ComposeError: [S16] \`git worktree
   list\` failed ... fatal: not a git repository`. Under the FIXED
   `_isolate_shared_infra_env` autouse fixture, running the real
   `test_success_calls_connect_with_project_and_intent` (the actual leaking
   test) immediately before the same unrelated victim test: both pass.
6. **Documentation gap:** SPEC.md's S16.1 section did not name the S8.7
   legacy-fallback refusal as an intentional terminal state. Added one
   paragraph (see above) naming it explicitly and correcting the "why" to
   match the reviewer's finding: CIU cannot safely guess the compose project
   Compose actually chose, not that a wrong value would silently corrupt
   anything.

All five defects were fixed with the SMALLEST change that closes them —
`worktree.py`: two `try/except` additions (defects 2 and 4, ~15 lines each,
no behavioral change to any already-passing path); `engine.py`: two
`try/except` block re-scopes (defect 3, moving existing lines, not adding
new logic); test files: fixture/fixture-data changes plus new tests, no
production logic touched beyond the two files above. Nothing outside the
five defects and the documentation gap was modified.
