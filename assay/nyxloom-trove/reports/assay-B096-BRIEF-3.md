# Assay B096 P25 repair checkpoint — blocked before authoritative reproduction

**Worktree:** `/workspaces/vbpub/.worktrees/assay-b096`  
**Branch/HEAD:** `assay-b096` / `84baffb4` (`fix(assay): carry resolved bases through P22 snapshots`)  
**Scope:** only the B096 P25 declared-base-as-tag scenario; no dstdns,
operator-owned files, Sol, mutation lane, or full gate was touched or run.

## Exact checkpoint state

The worktree was clean before this brief. No product or test file was changed.
The exact functions inspected were
`assay/gate/python/qualify_topos.py::_check_resolved_base_is_the_resolution_not_the_declaration`
and `assay/src/assay/runner.py::evaluate_r1`, plus the higher-rigor call path
that passes `resolved_base` through the P22 snapshot.

## Reproduction evidence obtained

The single disposable P25 scenario was materialized using
`_materialize_negative(..., base_override="p33-declared-base",
tag_base_as="p33-declared-base")`. It produced distinct commits:

```text
base=12ac3a4abba87522e95cae3233d06d10f39650c5
head=ecd1dfc541a3f5dc61bca0a30a4006ae3b51e2a4
```

The attempt was made from the cockpit with the worktree source on
`PYTHONPATH`. This environment does not contain the tester-unified runtime:
`/opt/tester-venv/bin/python` is absent. The exact resulting artifact summary
was:

```text
process_returncode=2
artifact.outcome=ERROR
artifact.reason_code=EXEC_FAILED
artifact.exit_code=2
artifact.claims[0]=ERROR/EXEC_FAILED
artifact.claims[1]=NO_MEASUREMENT/EMPTY_COVERAGE
```

The exact assay stderr was:

```text
assay: no judge_provenance recorded -- this process imported 'assay' from '/workspaces/vbpub/.worktrees/assay-b096/assay/src/assay/__init__.py', which is outside the installed distribution at '/home/vscode/.venv/lib/python3.14/site-packages' -- the running code is not the code that artifact contains, so its digest would name the wrong build; pass --require-judge-provenance to refuse instead of proceeding
assay: NO_MEASUREMENT/EMPTY_COVERAGE: no coverage artifact was produced by this run -- the lane's command exited without writing the declared artifact at all. This is distinct from an unreadable/malformed artifact: there is nothing here to have failed to read.
```

The declared command therefore never started, and the expected
`ASSAY_P25_LOG` file was absent. **Pytest stderr: unavailable; no pytest
process ran and no pytest log was created.** This is an environment blocker,
not evidence about the reported tester-unified `FAIL/COMMAND_FAILED` result.

## Blocker and required continuation

The cockpit cannot distinguish the P25 product failure from a missing child
runtime. A successor/controller must run this same one scenario inside
`tester-unified:local` (with the project cgroup parent, `CGROUP_PARENT_DEV_BACKGROUND`,
and both physical/devcontainer repository mounts), then capture the artifact's
`outcome`/`reason_code` and the actual `pytest.log`. Only after that evidence
identifies the failing runner branch should code be edited. Preserve the
existing `base-is-head` contract and do not run the full gate or any mutation
lane.

No repair is claimed. No full gate was run. No mutation process is running.
