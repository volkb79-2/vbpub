---
schema_version: 1
id: ciu-P02-worktree-shared-infra-join
project: ciu
component: worktree
title: "Join only a worktree's declared diverging services to live shared infrastructure"
tier: implement-2
input_revision: "202d292501fd11f440125900e981a4483e139e80"
source: {kind: backlog, ref: "nyxloom-trove/backlog.md#CIU-22"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/worktree.py"
    - "src/ciu/engine.py"
    - "src/ciu/cli.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "tests/tests/test_ciu_worktree_shared_infra.py"
    - "tests/tests/test_ciu_engine_shared_infra.py"
    - "tests/tests/test_ciu_cli_worktree.py"
    - "nyxloom-trove/reports/ciu-P02-worktree-shared-infra-join-LOG.md"
  forbid:
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
    observable: "CIU-22/S16.1. `ciu worktree add NAME --profile P1[,P2] --shared-infra REF --shared-infra-services S1[,S2] --shared-infra-ref-projects R1[,R2]` validates REF before `git worktree add`: REF is an existing registered worktree resolved by the same basename-or-absolute-path rule as `find_worktree`; its explicit `ciu.env` has a non-empty `DOCKER_NETWORK_INTERNAL`; and every declared reference Compose project has a running container on that network. Only then does add create the new checkout, generate its OWN `ciu.env`, and append the four complete intent fields `CIU_SHARED_INFRA_REF_PATH`, `CIU_SHARED_INFRA_NETWORK`, `CIU_SHARED_INFRA_SERVICES`, and `CIU_SHARED_INFRA_REF_PROJECTS`. The stored path is the resolved reference path, the network is the value read from the reference's env, and both comma-split lists are non-empty, duplicate-free names in supplied order. `--shared-infra`, both list flags, and a non-empty `--profile` are an all-or-nothing group; no mode may infer either tier from a compose file. An unresolved REF, absent/malformed reference env, absent/non-running declared reference project, a missing partner flag, an empty list item, or a duplicate item is exit 2 before checkout creation."
    negative: "Appending only a network name without a consumer, resolving REF after a checkout has already been created, accepting `--shared-infra` without explicit diverging services/reference projects/profile, treating a previously joined child as proof that the reference is deployed, or replacing the new instance's own `DOCKER_NETWORK_INTERNAL` with the reference network each fail this oracle. The gate fakes `worktree.procutil.docker` exactly at the Docker boundary; it must prove the invalid-reference paths make no git-add call and write no child `ciu.env` intent."
    gate: tester-unified
  - id: O2
    observable: "CIU-22/S16.1. After a successful non-dry-run `docker compose up`, both native `engine.main_execution` and `engine.run_shipped` parse the complete intent from the new worktree's already-loaded environment and call `worktree.connect_shared_infra_after_up(repo_root, compose_project, intent)`. It re-resolves the recorded absolute REF against `git worktree list`, re-reads that worktree's explicit `ciu.env`, and refuses if the registered path disappeared or its network no longer equals the recorded network. It proves the reference is still deployed by requiring a running container for EVERY recorded reference Compose project on that network (and refusing a recorded reference project equal to the current project), obtains every *running* container of each declared diverging service from THIS compose project with Docker's `com.docker.compose.project=<compose_project>` and `com.docker.compose.service=<service>` label filters, and fails before any connect if any declared service has zero running containers. It then inspects the reference network membership and calls `docker network connect <recorded-network> <container-id>` only for declared-service containers absent from that network. Thus a second `ciu up` with all memberships already present succeeds without a connect call. Neither `DOCKER_NETWORK_INTERNAL` nor the base compose/overlay network declarations change; the declared diverging services gain a SECOND membership and all other new-instance containers remain only on their own instance network."
    negative: "Putting the join in a compose `networks:` overlay, attaching every container in the compose project, attaching a reference-tier container, changing `DOCKER_NETWORK_INTERNAL`, joining before Compose succeeds, or treating an already-connected container as a Docker error each fail this oracle. The no-socket gate monkeypatches `worktree.procutil.docker` and the compose executor: a combined-axis test with two requested services, one already attached, and a third unrequested service proves exactly one connect is issued and the unrequested service is never named."
    gate: tester-unified
  - id: O3
    observable: "CIU-22/S16.1 failure and terminal-state contract. A reference that was live at `worktree add` but has no Compose-labelled container at post-up revalidation, a recorded reference path no longer registered, a changed reference network, an absent declared target service, or a non-zero Docker inspect/connect result makes `ciu up` fail with an `[S16.1]` error naming the reference path/network or target service. No connect is attempted before all reference and target preconditions pass. If a later connect fails after earlier new memberships were made, CIU disconnects only those memberships it added in reverse connection order; previously present memberships are never removed. CIU does not run `docker compose down` on this failure: its own stack remains up on its OWN network, clearly not joined, so the operator may restore the reference and retry or explicitly `ciu down`. Dry runs never inspect or connect Docker networks."
    negative: "A ref-down race that silently leaves a running but unjoined instance, a missing second target that still lets the first target connect, a partial-connect failure that leaves CIU-added memberships behind, rollback that removes a pre-existing membership, or dry-run Docker traffic each fail this oracle. Tests use ordered fake Docker replies to assert preconditions precede every side effect, to witness reverse-order rollback, and to distinguish a pre-existing membership from one created by this invocation."
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "The post-up join cannot enumerate and connect only containers carrying both the current compose-project label and each declared service label through `procutil.docker`, without changing a forbidden compose/network declaration."
  - "A correct implementation needs to alter the new instance's `DOCKER_NETWORK_INTERNAL`, or to attach a container not named by `--shared-infra-services`, in order to reach the reference infrastructure."
  - "The Docker seam cannot distinguish reference-not-running, target-service-absent, already-connected, and connect-failure/rollback paths in `tester-unified` without a Docker socket."
mutexes: [merge-lane]
review_focus:
  - "Reject any route that mutates compose network declarations or replaces the worktree's own network identity."
  - "Attack reference liveness between add and up, idempotent rerun, and partial-connect rollback."
---

# ciu-P02-worktree-shared-infra-join — make the two-verb join real

## Context to read first

1. `nyxloom-trove/backlog.md` — **CIU-22** in full; it rules out a declarative
   overlay network list as inert.
2. `src/ciu/worktree.py` in full — especially `find_worktree`, explicit
   `ciu.env` handling in `_clean_in`, and `add`'s pre-side-effect ordering.
3. `src/ciu/engine.py` — `main_execution` steps 1–17 and `run_shipped`'s
   compose-up path; the join is after their successful compose invocation.
4. `src/ciu/workspace_env.py` — `parse_workspace_env` and
   `_connect_devcontainer_to_network`; the latter is imperative-connect
   precedent only, not the ownership model for this feature.
5. `src/ciu/cli.py` — `_worktree`; and `docs/SPEC.md` **S16**.
6. `tests/tests/test_ciu_deploy_actions.py:1348-1379` — the established
   `monkeypatch.setattr(module.procutil, "docker", fake)` seam.
7. `nyxloom-trove/nyxloom.toml` `[gates.tester-unified]` — it has no Docker
   socket. Do not use a live daemon in a test.

## Dispatch contract

- Contract class: **2d.** All CLI grammar, persisted fields, call ordering,
  failure vocabulary, and terminal states are fixed below. Private helper names
  and equivalent local decomposition are the only degrees of freedom.
- Required roles: **implement-2 implementer -> fresh independent reviewer.**
- Baseline: run the declared `tester-unified` gate at `input_revision`, paste
  its real output into the LOG before changing source, then run it again after.

## Implementation packet (normative)

### Owned interface and persisted grammar

`src/ciu/worktree.py` owns the complete protocol. Add these exact constants and
value type (private helper names around them are free):

```python
SHARED_INFRA_REF_PATH = "CIU_SHARED_INFRA_REF_PATH"
SHARED_INFRA_NETWORK = "CIU_SHARED_INFRA_NETWORK"
SHARED_INFRA_SERVICES = "CIU_SHARED_INFRA_SERVICES"
SHARED_INFRA_REF_PROJECTS = "CIU_SHARED_INFRA_REF_PROJECTS"

@dataclass(frozen=True)
class SharedInfraIntent:
    ref_path: Path
    network: str
    services: tuple[str, ...]
    ref_projects: tuple[str, ...]
```

Extend the public function exactly as follows; `shared_infra_services` is the
raw comma-separated CLI value so the owner, not argparse, owns validation:

```python
def add(
    repo_root: Path,
    name: str,
    *,
    base: str = "main",
    profile: str | None = None,
    worktree_dir: str = DEFAULT_WORKTREE_DIR,
    shared_infra: str | None = None,
    shared_infra_services: str | None = None,
    shared_infra_ref_projects: str | None = None,
) -> Path: ...
```

`parse_shared_infra_intent(values: Mapping[str, str]) -> SharedInfraIntent |
None` is the sole reader for engine use. All four absent returns `None`. Any
partial, empty, malformed, or duplicate-containing group raises
`WorktreeError` with `[S16.1]`; no consumer may silently ignore a partial
intent. The writer appends these values to the child env after its successful
`_generate_env_in` call, using shell-safe quoting so `parse_workspace_env` reads
them back verbatim:

```sh
export CIU_SHARED_INFRA_REF_PATH="/logical/path/to/reference-worktree"
export CIU_SHARED_INFRA_NETWORK="repo-ab12cd-network"
export CIU_SHARED_INFRA_SERVICES="api,worker"
export CIU_SHARED_INFRA_REF_PROJECTS="idp-dev-idp,vault-dev-vault"
```

`REF` accepts only the existing `find_worktree` grammar: a registered
worktree's basename or an absolute registered path. Persist the resolved
absolute `WorktreeInfo.path`, not the caller spelling. Services are split at
commas, stripped, rejected when blank or duplicated, and retained in the
provided order; the reference-project list follows the same grammar. The
complete `--shared-infra` mode requires both lists and a non-empty `--profile`;
ordinary `worktree add` retains its current behaviour unchanged.

`src/ciu/cli.py` adds exactly `--shared-infra REF` and
`--shared-infra-services S1,S2` and `--shared-infra-ref-projects R1,R2` to
`worktree add` and forwards all raw values. The CLI owns no duplicate parser or
fallback.

### Required flow and namespace map

```
worktree add (PRIMARY process)
  resolve REF in git worktree registry
  read REF/ciu.env explicitly -> ref own network
  Docker-check every declared REF Compose project on ref network is running
  git worktree add -> child ciu env generate -> append immutable intent

ciu up (CHILD process)
  bootstrap loads CHILD/ciu.env -> CHILD keeps own DOCKER_NETWORK_INTERNAL
  docker compose up succeeds for CHILD project
  re-resolve recorded REF -> re-read REF/ciu.env -> re-prove it is live
  list only CHILD project + declared service containers
  docker network connect REF network to each absent CHILD target container
```

At add time, resolve and validate all shared-infra input **before** `_git`
creates the checkout. Read `ref.path / "ciu.env"` by explicit path with
`parse_workspace_env`; never use `find_workspace_env`, which may consult the
primary's ambient `REPO_ROOT`. Use these Docker invocations through
`worktree.procutil.docker(..., capture=True, check=False)`:

1. `docker network inspect <ref-network>` must return zero.
2. For every declared reference project R, `docker ps --filter
   network=<ref-network> --filter label=com.docker.compose.project=R --format
   {{.ID}}` must return zero and at least one nonblank ID.

`FileNotFoundError`, `OSError`, or any non-zero result is a loud `WorktreeError`
with `[S16.1]`; a configured shared join never treats unavailable Docker as an
empty reference. A network containing only the devcontainer fails the second
check because it lacks the Compose project label.

Engine must call `parse_shared_infra_intent(os.environ)` only after bootstrap.
For a non-`None` intent, call `connect_shared_infra_after_up` only after the
existing compose executor reports success; `dry_run=True` does neither Docker
preflight nor join. The helper receives `repo_root`, the same exact
`compose_project_name(...)` value passed to Compose, and the intent. It:

1. Finds the persisted absolute reference again in `list_worktrees(repo_root)`;
   absence is an `[S16.1]` failure. Re-read its explicit env, require its
   current network equal `intent.network`, reject any reference project equal
   to the current project, then perform the two reference-live Docker checks
   above for every declared reference project. This catches ref removal, a
   stale recording, a previously joined child masquerading as a live ref, and a
   ref stopped between verbs.
2. For every declared service, query running containers with both filters
   `label=com.docker.compose.project=<current-project>` and
   `label=com.docker.compose.service=<service>`, formatted as ID plus name.
   Require at least one result for every service before inspecting or changing
   reference membership. Sort the gathered containers by name for deterministic
   effects.
3. Inspect `intent.network` once and derive its current container names. For
   each gathered target absent from that membership, run `docker network connect
   <intent.network> <container-id>`. Existing membership is success/no-op.
4. If any connect fails, disconnect only IDs connected in this call, in reverse
   order, with `docker network disconnect <intent.network> <container-id>`;
   retain the original failure and append any rollback failure. Never disconnect
   a member discovered before this invocation.

Translate a `WorktreeError` from this post-up helper to the engine's normal
up-error surface while retaining its complete `[S16.1]` message. Do not
automatically bring the child's Compose project down: Compose's existing
failure model leaves its started containers observable and the child has never
been moved off its own network.

### Decision table

| State | Result | Docker side effect |
|---|---|---|
| No four intent fields | Current up unchanged | None from this feature |
| Partial/malformed stored fields | `[S16.1]` error | None |
| Add: REF unresolved, env invalid, network absent, or no Compose container | Error before `git worktree add` | Read-only checks only |
| Add: valid REF + profile + both service/project lists | Child env records all four fields | Read-only checks, then normal git/env work |
| Up: ref no longer registered/live or network differs | Error after successful compose up | No connect; child remains own-network-only |
| Up: any named target has no running container | `[S16.1]` error | No connect |
| Up: target already in ref network | Success | No connect for that target |
| Up: all connects succeed | Success | Each absent declared target gains second membership |
| Up: later connect fails | Error | Reverse-disconnect only memberships added by this call |
| Dry run | Current dry-run result | No reference Docker query or join |

### Proof material and traceability

| Work | Owner | Oracle | Required proof / controlled break |
|---|---|---|---|
| CLI and add recording | `worktree.py`, `cli.py` | O1 | Fake docker marks a ref live; parse child env and compare every field. Break: missing profile/service/ref must make no git-add call. |
| Ref liveness and target selection | `worktree.py`, `engine.py` | O2 | Fake Compose succeeds; fake Docker provides two selected services, one already attached, and one unselected service. Assert exactly the absent selected ID is connected. |
| Failure cleanup | `worktree.py`, `engine.py` | O3 | Ordered fake replies cover ref-down-after-add, absent second target, failing second connect, and failing rollback; assert no premature or over-broad disconnect. |
| Docs/status/LOG | docs + tracker + LOG | O1–O3 | Add S16.1 and configuration/CLI examples; mark CIU-22 FIXED with code/test/SPEC evidence. |

Test the real `worktree.add` and env-file parser flow in a temporary git repo;
only the git/Docker process edges may be faked. The gate has no Docker socket,
so every Docker branch uses `monkeypatch.setattr(worktree.procutil, "docker",
fake)`. For engine ordering, fake the compose executor and the same Docker
namespace; do not invent a second network API.

### Test rules (mandatory)

- Do not use wall-clock pass/fail assertions, short sleeps, or deadlines as an
  oracle; use direct calls and deterministic ordered fake results. A timeout is
  allowed only as a generous suite-hang failsafe.
- Do not leak `os.environ`, module globals, or mocked lazy-object attributes
  between xdist workers. Use `monkeypatch` and fresh `tmp_path` state.
- Assert the stated contract, not a call count or private helper. A test that
  only asserts no exception, weakens an assertion, or mocks the helper under
  test is hollow.
- Do not add a no-cover exclusion on changed code. Cover failures and cleanup
  paths directly.
- Do not use a real Docker daemon, registry, network, clock, or filesystem
  outside the test's temporary repository. The Docker CLI boundary is the
  controllable input.

## Work

1. Implement the exact CLI, intent grammar, add-time preflight, and explicit
   env write in `worktree.py` / `cli.py`.
2. Implement post-success engine wiring for native and shipped up paths and the
   transaction-like membership helper. Do not edit `composefile.py`.
3. Add direct, deterministic tests named in the traceability table.
4. Document S16.1 and the configuration/CLI contract; mark CIU-22 FIXED with
   real evidence. Write the baseline and final gate evidence to the LOG.

## Environment setup

Run the declared `tester-unified` gate from the dispatched worktree. It mounts
no Docker socket; tests must use the specified fake `procutil.docker` seam.
Do not prepare a live reference stack at carve or dispatch time.

## BLOCKED rule

If the exact protocol cannot be implemented without a forbidden file, without
the `procutil.docker` seam distinguishing every O3 state, or without preserving
the child's own network identity, write `BLOCKED: <mechanical reason>` to the
LOG, commit, and exit. Do not replace it with a compose overlay, a guessed tier,
or a best-effort silent no-op.
