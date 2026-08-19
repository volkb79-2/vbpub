---
schema_version: 1
id: ciu-P03-worktree-concurrency-budget
project: ciu
component: worktree
title: "Enforce a repository worktree-instance capacity before Compose starts"
tier: implement-2
input_revision: "5cb4a9a8e710095c902dadcad0c9504cd84f616e"
source: {kind: backlog, ref: "nyxloom-trove/backlog.md#CIU-24"}
stack: none
depends_on: [ciu-P02-worktree-shared-infra-join]
session: fresh
scope:
  touch:
    - "src/ciu/worktree.py"
    - "src/ciu/engine.py"
    - "src/ciu/config_model.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "tests/tests/test_ciu_worktree_budget.py"
    - "tests/tests/test_ciu_engine_worktree_budget.py"
    - "tests/tests/test_ciu_config_model.py"
    - "nyxloom-trove/reports/ciu-P03-worktree-concurrency-budget-LOG.md"
  forbid:
    - "src/ciu/governance.py"
    - "src/ciu/composefile.py"
    - "src/ciu/workspace_env.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
    - "../assay"
    - "../srdm"
    - "../dstdns"
    - "../nyxloom"
oracles:
  - id: O1
    observable: "CIU-24/S16.3. The sole file configuration is the PRIMARY CIU root's global table `[ciu.worktree] max_concurrent_instances = N`, where N is a positive TOML integer. `worktree.primary_worktree_root(repo_root)` identifies the registered PRIMARY *Git* worktree; `worktree.primary_ciu_root(repo_root)` then appends the exact relative offset `repo_root.relative_to(git rev-parse --show-toplevel)` to it. CIU renders only that derived primary CIU root (never the Git root, an intermediate/stack global layer, or a linked worktree's branch) with `config_model.render_global_chain(primary_ciu_root, primary_ciu_root, write_rendered=False)`. This makes a CIU project below a monorepo Git checkout reach its real file policy rather than silently seeing `{}`. Catch only that function's specific `ValueError` whose message begins `[ERROR] No global configuration found.` and treat it as an absent file-level policy, then continue to the ambient override; all other `ValueError`s still fail `[S16.3]`. The narrow catch remains required when even the derived CIU root has no template. This prevents linked worktrees on different branches from silently applying different capacity policy. It is deliberately NOT a `[governance]` or `[<root>.governance]` value and does not participate in CIU-13's global-over-stack governance merge: capacity is one policy for the git-worktree family, not a property of one stack. `CIU_MAX_CONCURRENT_WORKTREES`, when present, is a positive decimal integer and overrides the validated file value for that process. Both sources absent means no cap and makes no Docker or lock call. A blank, zero, negative, non-decimal, boolean/non-integer TOML value, unknown `[ciu.worktree]` key, or invalid ambient override fails `[S16.3]` loudly; an ambient override does not mask an invalid file table."
    negative: "A per-stack governance key, a default cap, treating the Git worktree root as the CIU root, dropping any component of the Git-root-to-CIU-root relative offset for a linked worktree, a leaf/global-chain override that lets one stack raise its own host budget, treating the normal no-global-configuration `ValueError` as a policy error, swallowing any other render error, silently treating `0`/empty/typo as unlimited, or consulting Docker when both sources are absent each fail this oracle. Tests use a CIU root genuinely below a Git root with one linked worktree and prove the policy comes from the primary CIU-root template; a no-template derived-CIU-root test proves absent file policy reaches the no-Docker/no-lock outcome, while separate invalid file and invalid ambient cases prove no unsafe fallback."
    gate: tester-unified
  - id: O2
    observable: "CIU-24/S16.3. With a configured cap, `worktree.worktree_budget_slot(repo_root, cap, current_network, stack_rel)` first resolves candidate identity OUTSIDE its exclusive advisory lock, then obtains `<git-common-dir>/ciu-worktree-budget.lock` for only the exact-project Docker queries, count decision, and caller's single `docker compose up` execution. Candidates come exclusively from `git worktree list --porcelain`: the primary is included, and a non-primary entry counts only when its explicit `<git-worktree>/ciu.env` exists, parses, and supplies a distinct non-empty `DOCKER_NETWORK_INTERNAL`. Let `ciu_root_offset = repo_root.relative_to(git_toplevel)`. For each eligible Git entry, its CIU root is exactly `entry.path / ciu_root_offset` and its stack is exactly that root plus the caller's relative `stack_rel`; no path component may be discarded. Before acquiring the flock, read that entry's own `ciu.env` explicitly and render the candidate stack's global config without output using that mapping as BOTH the Jinja `env` context and `$VAR` expansion environment, never the current process's ambient environment. Derive the exact project with the existing `engine.compose_project_name(config, candidate_stack)`. A candidate stack absent from its sibling branch is not deployed: skip it, emit an `[INFO] [S16.3]` note naming the candidate/stack, and make no Docker query for it. Query Docker with `docker ps --filter label=com.docker.compose.project=<that-exact-candidate-project> --format {{.Networks}}`; never use the bare existence filter. An eligible registered instance is deployed only when a container selected by ITS OWN exact project label lists ITS OWN network. This means a P02 child container carrying the reference network, but labelled with the child's project, cannot make the reference count as deployed. Docker unavailable/non-zero, a present candidate stack whose config/project identity cannot be rendered, malformed eligible env, or duplicate registered network is an `[S16.3]` error, never an empty count. If the current instance is already deployed it may rerun even at or above the cap; otherwise count >= cap refuses before Compose starts and names the observed count and cap. Containers on an unregistered/deleted worktree do not count (CIU-25 owns stale-leak handling)."
    negative: "Counting every git checkout regardless of deployment, excluding the primary, filtering only for the existence of `com.docker.compose.project`, counting a candidate's network from a container whose exact Compose project label belongs to another instance, composing a candidate path from the Git root without its CIU-root offset, rendering a candidate with the caller's ambient environment, rendering under the family flock, treating a genuinely absent sibling stack as an `[S16.3]` error, counting a network merely because Docker created it, letting a Docker-query/present-stack identity-resolution error read as zero, or refusing an already-running current instance when a later cap was lowered each fail this oracle. The no-socket gate patches `worktree.procutil.docker` and `worktree._git`/temporary porcelain state to distinguish primary-only, registered-but-not-deployed, registered-and-deployed, and stale-unregistered containers. A required P02-composition fixture has one A-labelled container list both A's and B's networks; querying B's exact project label is empty, so B MUST NOT be counted deployed."
    gate: tester-unified
  - id: O3
    observable: "CIU-24/S16.3. Both `engine.main_execution` and `engine.run_shipped` resolve the primary-CIU-root-only cap after bootstrap has established `repo_root`, but enforce it only for a real compose-up, immediately around their existing compose executor. They pass the same current `DOCKER_NETWORK_INTERNAL` and `working_dir.relative_to(repo_root)` that bootstrap established. `worktree_budget_slot` resolves candidate roots/envs/project labels before acquiring its flock, then holds that flock while `execute_docker_compose_with_logs` runs and releases it on every return/raise. In the combined P02/P03 call path, the release happens immediately after Compose returns successfully; only then does P02's `connect_shared_infra_after_up` run. The capacity slot protects count -> Compose start, whereas the post-up join does not create another instance and can make multiple Docker calls, so holding the family-wide flock across it would serialize unrelated joins without protecting the budget. `--dry-run` and render-only paths make no budget Docker/lock call. The exclusive lock serializes two cold starts in the same git worktree family: after the first Compose start makes its own network visible, the second waiter re-counts under the lock and is refused if it would exceed the cap. A Compose failure releases the lock and consumes no lasting reservation; normal Compose idempotence remains unchanged for an already deployed current instance."
    negative: "Checking before an unlocked Compose start, resolving candidate config/env identity under the flock, releasing the lock before Compose returns, holding the budget flock across P02's post-up join, enforcing only native (not `--shipped`) up, reserving a slot at `worktree add`, or making dry-run require Docker each fail this oracle. Engine tests replace the compose executor with a deterministic callable and use a real temporary lock path plus controlled fake deployment state to prove one continuous held section from the count decision through the executor return: an instrumented `fcntl.flock` wrapper delegates to the real lock while recording transitions, and the executor asserts no `LOCK_UN` occurred after the count decision. A deliberately broken acquire -> count -> release -> re-acquire -> run arrangement therefore fails even though it is locked at both sampled endpoints. Refusal is before the executor is called."
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "The primary CIU root cannot be derived by applying the exact Git-root-to-CIU-root relative offset to the registered primary worktree, without changing a forbidden governance table or without `render_global_chain(..., write_rendered=False, environ=...)` preserving every existing caller's behaviour when omitted."
  - "A correct count cannot distinguish a Git-registered CIU instance with a running Compose container from a raw/unregistered checkout or a stale orphan through the named `procutil.docker` seam, after resolving candidate identity outside the flock under that candidate's explicit `ciu.env`."
  - "The count-and-start critical section cannot share `<git-common-dir>/ciu-worktree-budget.lock` across the repository's worktrees while releasing it on every Compose outcome."
mutexes: [merge-lane]
review_focus:
  - "Attack CIU-13-style per-stack shadowing, no-cap Docker calls, and invalid-value fallbacks."
  - "Attack Git-root/CIU-root offset loss, sibling-branch missing stacks, candidate-environment leakage, primary counting, already-deployed reruns, orphan exclusion, and two simultaneous cold starts."
---

# ciu-P03-worktree-concurrency-budget — make worktree capacity a real policy

## Context to read first

1. `nyxloom-trove/backlog.md` — **CIU-24** in full; its previous per-stack
   proposal is intentionally not an implementation contract.
2. `KNOWN_ISSUES_TODO_BACKLOG.md` — **CIU-13** resolution, especially the
   global `[governance]` + stack table shallow merge that this feature must not
   accidentally reuse.
3. `src/ciu/worktree.py` in full — `list_worktrees`, `WorktreeInfo.is_primary`,
   and the explicit worktree-env pattern in `_clean_in`.
4. `src/ciu/engine.py` — `main_execution` and `run_shipped` from bootstrap to
   their existing Compose calls; `src/ciu/deploy.py:_running_containers` only as
   the `procutil.docker` precedent, not as a host-wide count implementation.
   `ciu-P01-worktree-isolation-primitives` is the already-READY sibling that
   also changes `worktree.py` and `engine.py`; it lands before P02/P03, so
   re-orient to its implementation before dispatching either package.
5. `src/ciu/config_model.py:367-424` — `render_global_chain`; and
   `docs/CONFIG.md` **Three-Layer Configuration Model** / **[ciu]**.
6. `src/ciu/dev.py:32-48` — `resolve_repo_root` finds a CIU marker, not a Git
   root; `src/ciu/workspace_env.py:parse_workspace_env` is the explicit-env
   reader. The CIU root may sit below `git rev-parse --show-toplevel`.
7. `nyxloom-trove/nyxloom.toml` `[gates.tester-unified]`: no Docker socket.
   Read `tests/tests/test_ciu_deploy_actions.py:1348-1379` for the fake Docker
   seam to use.

## Dispatch contract

- Contract class: **2d.** The policy namespace, precedence, parser, deployment
  predicate, lock location and lifetime, and all enforcement sites are fixed.
  Private helper names and equivalent internal decomposition are free.
- Required roles: **implement-2 implementer -> fresh independent reviewer.**
- Baseline: run the declared gate at `input_revision` and paste its actual
  output into the LOG before source changes; record the final gate likewise.
- Dispatch order is **P02, then P03**. `depends_on` deliberately pins this
  order because P03's classifier must account for P02's legitimate second
  network membership. Before P03 dispatch, replace this handoff's
  `input_revision` with the exact merge commit that implements P02; do not
  dispatch it against the pre-P02 source pinned above.

## Implementation packet (normative)

### Policy interface, authoritative source, and validation

Add these public functions in `src/ciu/worktree.py` (helper decomposition is
free, but their input/output semantics are not):

```python
def primary_worktree_root(repo_root: Path) -> Path: ...

def git_toplevel(repo_root: Path) -> Path: ...

def primary_ciu_root(repo_root: Path) -> Path: ...

def resolve_max_concurrent_instances(
    raw: Mapping[str, Any] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int | None: ...

@contextmanager
def worktree_budget_slot(
    repo_root: Path,
    cap: int | None,
    current_network: str,
    stack_rel: Path,
) -> Iterator[None]: ...
```

`primary_worktree_root` calls the existing `list_worktrees(repo_root)` and
returns the one entry whose `is_primary` is true. Zero or multiple primary
entries are an `[S16.3]` `WorktreeError`; do not choose a path by iteration
order. It is the **Git** primary, not a CIU configuration root.

`git_toplevel` runs `git rev-parse --show-toplevel` from `repo_root`, requiring
a zero result and one absolute existing directory. `primary_ciu_root` derives
`ciu_root_offset = repo_root.resolve().relative_to(git_toplevel(repo_root))`
(which is `Path(".")` only for a standalone CIU Git repository), then returns
`primary_worktree_root(repo_root) / ciu_root_offset`. Failure to derive that
relative offset or an absent derived primary CIU root is `[S16.3]`; do not
silently use the Git root. This offset is the sole namespace translation used
again for every linked candidate.

The file source is *only* this root global declaration:

```toml
[ciu.worktree]
max_concurrent_instances = 3
```

At every up path, obtain the sole policy root and `raw` with:

```python
primary_ciu_root = worktree.primary_ciu_root(repo_root)
try:
    root_global = config_model.render_global_chain(
        primary_ciu_root, primary_ciu_root, write_rendered=False
    )
except ValueError as exc:
    if not str(exc).startswith("[ERROR] No global configuration found."):
        raise
    root_global = {}
raw = root_global.get("ciu", {}).get("worktree")
```

Extend `render_global_chain` exactly with the keyword-only
`write_rendered: bool = True`; its default retains every current caller's
output write, while `False` returns the same rendered/merged mapping without
writing `ciu.global.toml`. Primary-CIU-root-only is intentional: do not substitute
the normal `global_config` made for a nested stack, whose chain can contain a
more-local configuration file, or the current linked worktree's CIU root,
whose branch may carry a conflicting policy. The narrow no-global-configuration
catch is also intentional, but it applies only after deriving the primary CIU
root: a Git primary lacking a template says nothing about the project below it.
Do not catch a different render `ValueError`: it is evidence of a real bad
template/override and must remain loud.

Extend `render_global_chain` with keyword-only
`environ: Mapping[str, str] | None = None` in addition to its established
`write_rendered` parameter. Pass that parameter explicitly through
`_make_render_context`, `render_toml_template`, and `expand_env_vars_or_fail`.
If `environ is None`, its default is exactly the current `os.environ`,
preserving every existing caller. Otherwise the supplied mapping is used for
**both** Jinja's `env` context and `$VAR` expansion at every template in the
chain; no template/render helper may consult ambient process environment in
that mode. Candidate rendering below uses this data path; the primary policy
render above retains the bootstrap process environment it has always used.

`resolve_max_concurrent_instances` validates the file table even if the
ambient override exists. `raw is None` is valid. Otherwise it must be a mapping
with exactly the optional key `max_concurrent_instances`; any unknown key is an
`[S16.3]` error. The value, when present, must be an `int` but not `bool`, and
must be `>= 1`. The environment mapping defaults to `os.environ` only when the
argument is `None`. If `CIU_MAX_CONCURRENT_WORKTREES` is absent, return the
validated file value (or `None`). If it is present, it must match decimal
`[1-9][0-9]*`; return that integer. Empty, whitespace-only, zero, signed,
floating, or other text is an `[S16.3]` error. There is no `0 == unlimited`
sentinel: **only absence at both sources means no cap.**

This policy is deliberately outside `governance.py`. CIU-13 shallow-merges
the global `[governance]` base with each stack's `[<root>.governance]` because
those values configure one stack. An instance cap controls all worktrees in a
repository's Git family and must never be changed by the stack being launched.

### Deployment predicate and critical section

`worktree_budget_slot(..., cap=None, ...)` immediately yields without opening
a lock or calling Docker. Otherwise:

1. **Before acquiring the flock**, require `stack_rel` to be the engine's exact
   relative, non-escaping `working_dir.relative_to(repo_root)`. Obtain
   `git_toplevel(repo_root)` and `ciu_root_offset` exactly as
   `primary_ciu_root` does, then call `list_worktrees(repo_root)`. For every
   Git entry calculate `candidate_ciu_root = entry.path / ciu_root_offset` and
   `candidate_stack = candidate_ciu_root / stack_rel`. This is a literal path
   append; it must retain every component of a nested CIU root (for example,
   `ciu/test-repo`), rather than treating `entry.path` as the CIU root.
2. If `candidate_stack` is absent or not a directory, emit one deterministic
   `[INFO] [S16.3]` message naming that Git worktree and absent stack and omit
   it from the candidate set. It is a sibling branch that does not deploy this
   stack, not an error in the caller's deploy and not a Docker query. Otherwise
   read `<entry.path>/ciu.env` by explicit path with `parse_workspace_env`. An
   entry without that file is a raw Git worktree, not a registered CIU instance,
   and is excluded. An existing env that fails to parse or lacks/empties
   `DOCKER_NETWORK_INTERNAL` is an `[S16.3]` refusal: it is a purported CIU
   instance whose deployment cannot be truthfully counted. Require every
   remaining eligible network name to be distinct, and require
   `current_network` to be one of them; a duplicate or an unregistered current
   network is a loud isolation/count failure, not one slot silently shared or
   silently omitted.
3. Still before acquiring the flock, render each remaining candidate's global
   config with `render_global_chain(candidate_stack, candidate_ciu_root,
   write_rendered=False, environ=candidate_env)`. `candidate_env` is exactly
   the mapping parsed from that candidate's explicit env file; do not mutate
   `os.environ` or merge the caller's ambient mapping into it. Derive the exact
   Compose project with `engine.compose_project_name(candidate_global,
   candidate_stack)`. To avoid the `engine` -> `worktree` import cycle introduced
   by P03 wiring, import `engine.compose_project_name` lazily inside this
   candidate-resolution helper, after `engine` has finished module import; do
   not add a module-level `worktree -> engine` import. A present candidate stack
   whose config/project identity cannot be rendered is `[S16.3]`, not evidence
   of an inactive instance. Store only `(entry.path, candidate_stack, network,
   exact_project)` descriptors for the locked phase.
4. Obtain the common Git directory with `git rev-parse --git-common-dir` from
   `repo_root`; resolve a relative result against `repo_root`. Open
   `<common-dir>/ciu-worktree-budget.lock` and acquire `fcntl.flock(...,
   LOCK_EX)`. This logical Git path is shared by all linked worktrees and is
   local to the processes taking the lock; do not translate it into the Docker
   daemon namespace. Through `worktree.procutil.docker`, for each descriptor's
   exact project run
   `docker ps --filter label=com.docker.compose.project=<candidate-project>
   --format {{.Networks}}`. `FileNotFoundError`, `OSError`, or non-zero is
   `[S16.3]` rather than zero. Split only that project's output into its
   comma-separated network lists and mark the eligible candidate deployed only
   when its **own** network occurs. The filter is deliberately value-qualified:
   a P02 child container may list the reference network, but it carries the
   child's project label and cannot satisfy the reference candidate's query.
   Docker-created-but-empty networks do not count; a stale container whose
   worktree is absent from Git does not count; the primary is included because
   `list_worktrees` includes it.
5. If `current_network` is deployed, yield even when the observed count is at
   or over cap — an already running instance may be reconciled after the
   policy is lowered. Otherwise, if the count is `>= cap`, raise `[S16.3]`
   naming count, cap, and the current network before Compose is called; else
   yield one start slot. Keep the flock until the caller's compose executor
   returns or raises, then unlock in `finally`.

The only intentionally excluded running state is CIU-25's stale/unregistered
orphan; this package must not claim to reap or count it. No other unknown
state gets invented as "not deployed."

### Engine construction and state flow

After bootstrap establishes `repo_root`, both `main_execution` and
`run_shipped` resolve the primary-CIU-root-only cap once with the packet's exact
`primary_ciu_root` and `render_global_chain` calls. They retain it as data.
For real Compose starts, immediately around their existing
`execute_docker_compose_with_logs` call:

```python
with worktree.worktree_budget_slot(
    repo_root,
    cap,
    os.environ["DOCKER_NETWORK_INTERNAL"],
    working_dir.relative_to(repo_root),
):
    docker_result = execute_docker_compose_with_logs(...)

# only after the context has released its flock:
if shared_infra_intent is not None:
    worktree.connect_shared_infra_after_up(...)
```

No budget check occurs for `dry_run`, `render_toml`, or any path that does not
call the executor. Candidate CIU-root/env/config identity is resolved before
the lock; the lock begins only for Docker/count work and ends directly after the
executor. This protects the count -> start transition without serialising config
rendering or the rest of CIU's pipeline. P02 is deliberately outside
this flock: it starts no instance, and holding a repository-wide capacity lock
across an arbitrary sequence of post-up network connects would create unrelated
contention without making the count/start decision safer. A Compose error/interruption
uses existing engine behaviour, but the context manager must release the lock
on it; there is no separate reservation artifact to leak.

### Decision table

| Root file value | ambient value | current deployment state | outcome |
|---|---|---|---|
| absent | absent | any | no cap; no Docker/lock call |
| no primary-CIU-root global template | absent | any | no file policy; no cap and no Docker/lock call |
| valid N | absent | current already deployed | allow rerun, even if total >= N |
| valid N | absent | current absent, total < N | lock then allow Compose start |
| valid N | absent | current absent, total >= N | refuse before executor |
| valid N | valid M | any | use M after validating both sources |
| invalid file | any | any | `[S16.3]` refuse |
| any valid/absent file | invalid present ambient | any | `[S16.3]` refuse |
| cap configured + Docker/count ambiguity, present-stack identity error, or unregistered current network | any | any | `[S16.3]` refuse |
| registered sibling lacks this stack on its branch | any | any | skip it as not deployed; log note; no Docker query for it |
| dry-run/render-only | any | any | no budget Docker/lock call |

### Proof material and traceability

| Work | Owner | Oracle | Required proof / controlled break |
|---|---|---|---|
| Primary-CIU-root policy resolution | `config_model.py`, `worktree.py` | O1 | Required nested-root fixture: a temporary Git root contains `project/ciu.global.defaults.toml.j2` with `[ciu.worktree]`, and a linked Git worktree contains the same `project/` offset. Invoke from the primary `project/`; assert policy is read from primary `project/`, never the Git root or child branch, with no rendered artifact. Break offset loss, invalid type/key/environment, and unrelated render errors; assert refusal. |
| Instance classifier | `worktree.py`, `config_model.py` | O2 | Temporary Git porcelain lists primary, eligible children, raw child without env, and an absent stale path. Fake exact-project Docker responses and assert only primary + deployed eligible child count. The nested-root fixture gives the child its own explicit `ciu.env` and a template that reads it; assert its project derives from that env, not the caller's. Required missing-stack sibling fixture removes the stack directory only in the child: assert it is skipped with the `[S16.3]` note and no Docker query. Required P02 composition fixture: container A has `com.docker.compose.project=A-project` and lists `A-network,B-network`; B's exact-project query is empty, so B is not deployed. |
| Lock and capacity | `worktree.py` | O2/O3 | Patch Docker state and use a temporary common Git dir. Assert all candidate env/config renders finish before the first `LOCK_EX`; then wrap real `fcntl.flock` to record every locked transition. The controlled executor asserts no `LOCK_UN` occurred after the count decision, and the test observes the first unlock only after executor return. A deliberately broken count -> unlock -> re-lock -> executor arrangement must fail. Lower cap after current deployment and assert rerun is allowed. |
| Native and shipped wiring | `engine.py` | O3 | Fake executor plus `worktree_budget_slot`; assert cap refusal occurs before executor, both paths use the current network and stack-relative identity, dry-run does not invoke Docker/slot, and P02 join runs only after the budget context exits. |
| Docs/status/LOG | docs + tracker + LOG | O1–O3 | Add S16.3 and configuration precedence; mark CIU-24 FIXED with code/test/SPEC evidence. |

The Docker test seam is only
`monkeypatch.setattr(worktree.procutil, "docker", fake)`. Do not use a real
Docker socket, and do not mock `worktree_budget_slot` in the tests that prove
its classifier/lock semantics; only engine wiring tests may replace it after
the direct helper tests cover the real helper.

### Test rules (mandatory)

- Do not use wall-clock pass/fail assertions, sleeps, or deadline races. Use
  direct deterministic helpers, a real temporary flock, and controlled fake
  state; timeouts are only generous suite-hang failsafes.
- Do not leak `os.environ`, module globals, or mocks between xdist workers.
  Use `monkeypatch`, fresh `tmp_path`, and restore all process state.
- Assert capacity behaviour and terminal outcomes, not a private call count.
  A test that only asserts no exception, weakens an assertion, or mocks the
  implementation helper under test is hollow.
- Do not add a no-cover exclusion on changed code. Exercise invalid config,
  Docker failure, refusal, current-deployed allowance, and unlock-on-error.
- Do not contact a live Docker daemon, registry, network, or clock. Docker
  output and Git porcelain are controlled test inputs.

## Work

1. Add the primary-CIU-root no-write config render, explicit-render-environment
   path, and exact policy parser.
2. Implement the Git-root-to-CIU-root candidate translation, pre-lock
   candidate-env/project resolution, registered-and-deployed classifier, and
   common-Git-dir locking critical section in `worktree.py`.
3. Wire it around both real Compose up paths, with dry-run/render exemptions.
4. Add the required deterministic tests; document S16.3/configuration and mark
   CIU-24 FIXED with evidence in the tracker and LOG.

## Environment setup

Run the declared `tester-unified` gate from the dispatched worktree. It has no
Docker socket; all Docker state is supplied through the named fake seam. Do not
start a live stack to test this package.

## BLOCKED rule

If the exact primary-CIU-root policy, Git-root-to-CIU-root candidate translation,
candidate-owned render environment, Git-common-dir lock, registered-and-deployed
classifier, or no-Docker gate seam cannot be met without a forbidden file,
write `BLOCKED: <mechanical reason>` to the LOG, commit, and exit. Do not move
the cap into governance, invent an unlimited default, reserve capacity in
`worktree add`, or turn a Docker/count failure into zero.
