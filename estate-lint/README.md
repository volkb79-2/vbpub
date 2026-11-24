# estate-lint

The estate's own lint project: checks that judge **every project at once** rather
than one project's behaviour. It exists so a change to one project cannot turn
another project's gate red (P-core-12, `cmru/KNOWN_ISSUES_TODO_BACKLOG.md`
KI-194).

## What it judges

Two things, both estate-wide:

1. **The real release graph** (`tests/test_estate_dependency_levels.py`, moved
   here from cmru). `cmru.orchestration.toml` declares each project's
   `depends_on`; every project's `pyproject.toml` declares its real
   requirements. `cmru dependencies` must find those two statements in
   agreement for the real estate, and `docs/BOOTSTRAP-FROM-ZERO.md`'s fenced
   `project-order` block must equal the graph's level table (and be a valid
   release order for every declared edge). A project added, moved or removed
   without updating the doc fails here.
2. **Run-Gate lane budgets** (`run-gate lanes --check --all --strict`, the
   second step of the `suite` lane). It walks every tracked `run-gate.toml`,
   and with `--strict` the "outer budget >= inner budget + 5m" margin is a
   failure rather than a warning. The rules it no longer needs restating in a
   project's own tests.

## How to run it

```bash
# from the repository root, through the registered lane:
run-gate --worktree . estate-lint/suite     # or `scripts/controller/gate-run.sh estate-lint/suite`

# the lane's first step alone, in the tester-unified environment:
cd estate-lint && PYTHONPATH=../cmru/src:../libraries/worktree/src \
  /opt/tester-venv/bin/python3 -m pytest tests -q
```

The lane runs in `tester-unified`: the image bakes a copy of cmru, so the lane
puts the **worktree's** `cmru/src` and `libraries/worktree/src` on
`PYTHONPATH` and judges the checkout, not the image.

## What it does not own

Release-order facts for a specific project (its own `depends_on`, its own
pyproject) stay where the project is. Only the agreement of all of them, and
the doc that states them, is judged here.
