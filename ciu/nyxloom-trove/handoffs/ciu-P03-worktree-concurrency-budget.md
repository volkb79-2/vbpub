---
schema_version: 1
id: ciu-P03-worktree-concurrency-budget
project: ciu
component: worktree
title: "Enforce a repository worktree-instance capacity before Compose starts"
tier: implement-2
input_revision: "202d292501fd11f440125900e981a4483e139e80"
source: {kind: backlog, ref: "nyxloom-trove/backlog.md#CIU-24"}
stack: none
depends_on: []
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
    observable: "CIU-24/S16.3. The sole file configuration is the PRIMARY worktree's repository-root global table `[ciu.worktree] max_concurrent_instances = N`, where N is a positive TOML integer. `worktree.primary_worktree_root(repo_root)` derives that one registered primary root from `git worktree list`; CIU renders only that root (never an intermediate/stack global layer) with `config_model.render_global_chain(primary_root, primary_root, write_rendered=False)`. This prevents linked worktrees on different branches from silently applying different capacity policy. It is deliberately NOT a `[governance]` or `[<root>.governance]` value and does not participate in CIU-13's global-over-stack governance merge: capacity is one policy for the git-worktree family, not a property of one stack. `CIU_MAX_CONCURRENT_WORKTREES`, when present, is a positive decimal integer and overrides the validated file value for that process. Both sources absent means no cap and makes no Docker or lock call. A blank, zero, negative, non-decimal, boolean/non-integer TOML value, unknown `[ciu.worktree]` key, or invalid ambient override fails `[S16.3]` loudly; an ambient override does not mask an invalid file table."
    negative: "A per-stack governance key, a default cap, a leaf/global-chain override that lets one stack raise its own host budget, silently treating `0`/empty/typo as unlimited, or consulting Docker when both sources are absent each fail this oracle. Tests load root and leaf global templates with conflicting values and prove only the root policy is seen; separate invalid file and invalid ambient cases prove no unsafe fallback."
    gate: tester-unified
  - id: O2
    observable: "CIU-24/S16.3. With a configured cap, `worktree.worktree_budget_slot(repo_root, cap, current_network)` obtains an exclusive advisory lock at `<git-common-dir>/ciu-worktree-budget.lock` before reading deployment state and holds it through the caller's single `docker compose up` execution. It derives candidate instances exclusively from `git worktree list --porcelain`: the primary is included, and a non-primary entry counts only when its explicit `<worktree>/ciu.env` exists, parses, and supplies a distinct non-empty `DOCKER_NETWORK_INTERNAL`. One `procutil.docker(['ps', '--filter', 'label=com.docker.compose.project', '--format', '{{.Networks}}'])` query supplies the running Compose networks; an eligible registered instance is deployed exactly when its own network appears in that output. Docker unavailable/non-zero, malformed eligible env, or duplicate registered network is an `[S16.3]` error, never an empty count. If the current instance is already deployed it may rerun even at or above the cap; otherwise count >= cap refuses before Compose starts and names the observed count and cap. Containers on an unregistered/deleted worktree do not count (CIU-25 owns stale-leak handling)."
    negative: "Counting every git checkout regardless of deployment, excluding the primary, counting a deleted/unregistered container, counting a network merely because Docker created it, letting a Docker-query error read as zero, or refusing an already-running current instance when a later cap was lowered each fail this oracle. The no-socket gate patches `worktree.procutil.docker` and `worktree._git`/temporary porcelain state to distinguish primary-only, registered-but-not-deployed, registered-and-deployed, and stale-unregistered containers."
    gate: tester-unified
  - id: O3
    observable: "CIU-24/S16.3. Both `engine.main_execution` and `engine.run_shipped` resolve the primary-root-only cap after bootstrap has established `repo_root`, but enforce it only for a real compose-up, immediately around their existing compose executor. They pass the same current `DOCKER_NETWORK_INTERNAL` that bootstrap loaded, hold `worktree_budget_slot` while `execute_docker_compose_with_logs` runs, and release it on every return/raise. `--dry-run` and render-only paths make no budget Docker/lock call. The exclusive lock serializes two cold starts in the same git worktree family: after the first Compose start makes its own network visible, the second waiter re-counts under the lock and is refused if it would exceed the cap. A Compose failure releases the lock and consumes no lasting reservation; normal Compose idempotence remains unchanged for an already deployed current instance."
    negative: "Checking before an unlocked Compose start, releasing the lock before Compose returns, enforcing only native (not `--shipped`) up, reserving a slot at `worktree add`, or making dry-run require Docker each fail this oracle. Engine tests replace the compose executor with a deterministic callable and use a real temporary lock path plus controlled fake deployment state to prove the budget check encloses the executor and that refusal occurs before it is called."
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "The primary-root-only capacity policy cannot be rendered without changing a forbidden governance table or without `render_global_chain(..., write_rendered=False)` preserving every existing rendered-output behaviour when omitted."
  - "A correct count cannot distinguish a Git-registered CIU instance with a running Compose container from a raw/unregistered checkout or a stale orphan through the named `procutil.docker` seam."
  - "The count-and-start critical section cannot share `<git-common-dir>/ciu-worktree-budget.lock` across the repository's worktrees while releasing it on every Compose outcome."
mutexes: [merge-lane]
review_focus:
  - "Attack CIU-13-style per-stack shadowing, no-cap Docker calls, and invalid-value fallbacks."
  - "Attack primary counting, already-deployed reruns, orphan exclusion, and two simultaneous cold starts."
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
5. `src/ciu/config_model.py:367-424` — `render_global_chain`; and
   `docs/CONFIG.md` **Three-Layer Configuration Model** / **[ciu]**.
6. `nyxloom-trove/nyxloom.toml` `[gates.tester-unified]`: no Docker socket.
   Read `tests/tests/test_ciu_deploy_actions.py:1348-1379` for the fake Docker
   seam to use.

## Dispatch contract

- Contract class: **2d.** The policy namespace, precedence, parser, deployment
  predicate, lock location and lifetime, and all enforcement sites are fixed.
  Private helper names and equivalent internal decomposition are free.
- Required roles: **implement-2 implementer -> fresh independent reviewer.**
- Baseline: run the declared gate at `input_revision` and paste its actual
  output into the LOG before source changes; record the final gate likewise.

## Implementation packet (normative)

### Policy interface, authoritative source, and validation

Add these public functions in `src/ciu/worktree.py` (helper decomposition is
free, but their input/output semantics are not):

```python
def primary_worktree_root(repo_root: Path) -> Path: ...

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
) -> Iterator[None]: ...
```

`primary_worktree_root` calls the existing `list_worktrees(repo_root)` and
returns the one entry whose `is_primary` is true. Zero or multiple primary
entries are an `[S16.3]` `WorktreeError`; do not choose a path by iteration
order. It is the one authoritative policy root for all linked worktrees.

The file source is *only* this root global declaration:

```toml
[ciu.worktree]
max_concurrent_instances = 3
```

At every up path, obtain the sole policy root and `raw` with:

```python
primary_root = worktree.primary_worktree_root(repo_root)
root_global = config_model.render_global_chain(
    primary_root, primary_root, write_rendered=False
)
raw = root_global.get("ciu", {}).get("worktree")
```

Extend `render_global_chain` exactly with the keyword-only
`write_rendered: bool = True`; its default retains every current caller's
output write, while `False` returns the same rendered/merged mapping without
writing `ciu.global.toml`. Primary-root-only is intentional: do not substitute
the normal `global_config` made for a nested stack, whose chain can contain a
more-local configuration file, or the current linked worktree's root, whose
branch may carry a conflicting policy.

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

1. Obtain the common Git directory with `git rev-parse --git-common-dir` from
   `repo_root`; resolve a relative result against `repo_root`. Open
   `<common-dir>/ciu-worktree-budget.lock` and acquire `fcntl.flock(...,
   LOCK_EX)`. This logical Git path is shared by all linked worktrees and is
   local to the processes taking the lock; do not translate it into the Docker
   daemon namespace.
2. Call `list_worktrees(repo_root)`. For every entry, read
   `<entry.path>/ciu.env` by explicit path with `parse_workspace_env`. An entry
   without that file is a raw Git worktree, not a registered CIU instance, and
   is excluded. An existing env that fails to parse or lacks/empties
   `DOCKER_NETWORK_INTERNAL` is an `[S16.3]` refusal: it is a purported CIU
   instance whose deployment cannot be truthfully counted. Require every
   eligible network name to be distinct, and require `current_network` to be
   one of them; a duplicate or an unregistered current network is a loud
   isolation/count failure, not one slot silently shared or silently omitted.
3. Once, through `worktree.procutil.docker`, run exactly
   `docker ps --filter label=com.docker.compose.project --format {{.Networks}}`.
   `FileNotFoundError`, `OSError`, or non-zero is `[S16.3]` rather than zero.
   Split the output's comma-separated network lists and mark an eligible entry
   deployed when its own network occurs. Docker-created-but-empty networks do
   not count; a stale container whose worktree is absent from Git does not
   count; the primary is included because `list_worktrees` includes it.
4. If `current_network` is deployed, yield even when the observed count is at
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
`run_shipped` resolve the primary-root-only cap once with the packet's exact
`primary_worktree_root` and `render_global_chain` calls. They retain it as data.
For real Compose starts, immediately around their existing
`execute_docker_compose_with_logs` call:

```python
with worktree.worktree_budget_slot(
    repo_root,
    cap,
    os.environ["DOCKER_NETWORK_INTERNAL"],
):
    docker_result = execute_docker_compose_with_logs(...)
```

No budget check occurs for `dry_run`, `render_toml`, or any path that does not
call the executor. The lock begins after all normal render/preflight work and
ends directly after the executor; this protects the count -> start transition
without serialising the rest of CIU's pipeline. A Compose error/interruption
uses existing engine behaviour, but the context manager must release the lock
on it; there is no separate reservation artifact to leak.

### Decision table

| Root file value | ambient value | current deployment state | outcome |
|---|---|---|---|
| absent | absent | any | no cap; no Docker/lock call |
| valid N | absent | current already deployed | allow rerun, even if total >= N |
| valid N | absent | current absent, total < N | lock then allow Compose start |
| valid N | absent | current absent, total >= N | refuse before executor |
| valid N | valid M | any | use M after validating both sources |
| invalid file | any | any | `[S16.3]` refuse |
| any valid/absent file | invalid present ambient | any | `[S16.3]` refuse |
| cap configured + Docker/count ambiguity or unregistered current network | any | any | `[S16.3]` refuse |
| dry-run/render-only | any | any | no budget Docker/lock call |

### Proof material and traceability

| Work | Owner | Oracle | Required proof / controlled break |
|---|---|---|---|
| Primary-root-only policy resolution | `config_model.py`, `worktree.py` | O1 | Primary, linked-worktree root, and nested templates disagree; `write_rendered=False` returns the primary value without writing an artifact. Break invalid type/key/environment and assert refusal. |
| Instance classifier | `worktree.py` | O2 | Temporary Git porcelain lists primary, eligible children, raw child without env, and an absent stale path. Fake one Docker network list and assert only primary + deployed eligible child count. |
| Lock and capacity | `worktree.py` | O2/O3 | Patch Docker state and use a temporary common Git dir. Assert flock acquisition surrounds the count and executor boundary; lower cap after current deployment and assert rerun is allowed. |
| Native and shipped wiring | `engine.py` | O3 | Fake executor plus `worktree_budget_slot`; assert cap refusal occurs before executor, both paths use the current network, and dry-run does not invoke Docker/slot. |
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

1. Add the primary-root-only no-write config render and exact policy parser.
2. Implement the registered-and-deployed classifier and common-Git-dir locking
   critical section in `worktree.py`.
3. Wire it around both real Compose up paths, with dry-run/render exemptions.
4. Add the required deterministic tests; document S16.3/configuration and mark
   CIU-24 FIXED with evidence in the tracker and LOG.

## Environment setup

Run the declared `tester-unified` gate from the dispatched worktree. It has no
Docker socket; all Docker state is supplied through the named fake seam. Do not
start a live stack to test this package.

## BLOCKED rule

If the exact primary-root-only policy, Git-common-dir lock, registered-and-deployed
classifier, or no-Docker gate seam cannot be met without a forbidden file,
write `BLOCKED: <mechanical reason>` to the LOG, commit, and exit. Do not move
the cap into governance, invent an unlimited default, reserve capacity in
`worktree add`, or turn a Docker/count failure into zero.
