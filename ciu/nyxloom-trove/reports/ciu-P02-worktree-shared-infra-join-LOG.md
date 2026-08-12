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
- **Judgment call, flagged for review rather than invented silently:**
  `run_shipped` has a pre-existing S8.7 legacy-fallback path where
  `compose_project_name` can raise (no `deploy.project_name`/
  `environment_tag` configured) and `shipped_project` becomes `None` — Compose
  then derives its own project from the cwd basename, a value CIU never
  learns. The packet's flow says to call `connect_shared_infra_after_up` with
  "the same exact `compose_project_name(...)` value passed to Compose", which
  in this fallback is `None` — but `None` cannot build a
  `label=com.docker.compose.project=<...>` filter (it would silently become
  the literal string `"None"`, a real correctness bug, not a graceful
  degradation). This exact combination (`--shipped` + no project config +
  `--shared-infra` declared) is not named in any oracle, fixture, or
  `escalate_if` line. Rather than either (a) silently skip the join on a
  DECLARED intent, which the module's whole design philosophy refuses to do
  elsewhere, or (b) pass a bogus value, I made it a loud `[S16.1]`
  `ComposeError` naming the missing config, proven by
  `test_unresolvable_compose_project_with_intent_refuses` in
  `test_ciu_engine_shared_infra.py`, and left the PRE-EXISTING no-intent
  fallback path completely unchanged (proven by the sibling
  `..._without_intent_is_unaffected` test). Flagging this here per the
  handoff's own instruction to report what was stopped on rather than invent
  past — this is the one place I made a call the packet didn't fully specify.

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
