# LOG — ciu-P06-worktree-container-targets

- Package: `ciu-P06-worktree-container-targets`
- Branch: `docs/ciu-worktree-automation-backlog`
- Implementer: opencode (DeepSeek V4 Flash) — **its session ended after the implementation but
  before this LOG**; this LOG is CONTROLLER-AUTHORED (dstdns Fable session) from the
  implementer's final state report + the controller's own review, and includes one
  controller-applied fix (below). Status: COMPLETE (no BLOCKED).

## What shipped (S16.7 — declared exact container targets)

- `worktree.py`: `ExecTarget` (frozen dataclass, closed four-key grammar:
  stack/service/workdir/requires_worktree_mount default true), `parse_exec_targets` (unknown
  keys/aliases/empty strings/malformed booleans refuse loudly, `[S16.7]`),
  `resolve_exec_targets_config` ([ciu.worktree.exec_targets] from the rendered global chain),
  `_resolve_target_container` (exactly ONE running container by compose project+service labels
  + the instance's own `DOCKER_NETWORK_INTERNAL` — never substring, never an implicit `up`),
  `_workdir_within`, `_verify_worktree_mount` (docker inspect Mounts is the ONLY namespace
  authority; host Source compared against `to_physical_path(record.git_worktree_path,
  repo_root=env[REPO_ROOT], physical_root=env[PHYSICAL_REPO_ROOT])` — no local filesystem
  predicate on the other namespace's path), `exec_target_instance` (all validation before any
  Docker execution; exact `docker exec` exit code returned).
  `WORKTREE_CAPABILITIES` += `worktree.exec-target.v1`.
- `cli.py`: `_worktree_exec` manual parser (argparse REMAINDER cannot both consume `--target`
  and keep `--` byte-identical), early dispatch, exec subparser removed; `_USAGE`/`_VERB_HELP`.
- Tests: `TestExecTargets` (grammar, exact selection, mount proof, argv construction,
  real-subprocess integration via PATH-shim docker), CLI dispatch/error tests, capabilities
  list at 6 items, doc-contract `CLOSED_PUBLIC_VALUES` extended; `_fake_docker` gained
  `inspect_error`. Four coverage arcs closed with dedicated tests (cli.py:596;
  worktree.py inspect-OSError; non-string mount field skip; `_workdir_within` False final
  raise).
- Docs: CONSUMERS §8, DESIGN-GUIDE, README, SPEC S16.7 + S16.5 identifiers, FEATURES,
  ARCHITECTURE, CHANGES (CIU-29/D-007 entry).

## Controller review fix (would have been a total functional failure)

The implementer built `docker exec -w WORKDIR CID -- ARGV...`. `docker exec` stops
option-parsing at the CONTAINER positional, so the `--` is executed AS the in-container
command. **Measured live at review**: `docker exec -w /tmp <ctr> -- echo hi` → exit 127,
`exec: "--": executable file not found`; without `--` → `hi`, exit 0. Every `exec --target`
invocation would have failed. Fixed: the `--` is consumed at the CLI layer only; the docker
argv carries `child_argv` verbatim (`worktree.py`, with an explanatory comment), and the
argv-pinning test updated. **Lesson: an argv pinned against a fake docker proves construction,
not acceptance — any new docker argv SHAPE needs one live acceptance probe at review.**

## Venv iteration signal (controller-run, scrubbed env — NOT the gate)

```
env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u CIU_GOV_READ_IOPS .venv/bin/python run-ciu-tests.py
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2173 passed in 14.31s =============================
VENV_EXIT=0
```

(P05 checkpoint was 2139; P06 adds 34 tests. The implementer's own last full run predated its
coverage-arc fixes and was discarded as stale — this run supersedes it.)

## Scope

All changes within P06 scope.touch (`worktree.py`, `cli.py`, three test files,
doc-contract test, 7 docs, CHANGES, this LOG). `config_model.py` was permission-only and is
untouched. Forbid list (engine.py, deploy.py, governance.py, decisions.md) untouched.
`_last-summary.txt` (pre-existing untracked) and the operator's local `cmru/` note edit are
NOT part of this commit.
