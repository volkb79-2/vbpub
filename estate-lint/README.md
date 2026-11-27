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
   failure rather than a warning. A project's own tests no longer restate those
   rules.
3. **Command-lane liveness** (`tests/test_command_liveness_inventory.py`) checks every tracked lane against `stall-progress-inventory.toml` for missing or stale progress evidence and wrapper bounds.

## How to run it

```bash
# through the registered lane (run-gate reads the run-gate.toml of the current directory):
cd estate-lint && run-gate --worktree <tree> suite    # or `scripts/controller/gate-run.sh --worktree <tree> suite`

# the lane's first step alone, in the tester-unified environment:
cd estate-lint && PYTHONPATH=../cmru/src:../libraries/worktree/src \
  /opt/tester-venv/bin/python3 -m pytest tests -q
```

The lane runs in `tester-unified`: the image bakes a copy of cmru, so the lane
puts the **worktree's** `cmru/src` and `libraries/worktree/src` on
`PYTHONPATH` and judges the checkout, not the image.

Two limits to know. The lane runs the image's installed `run-gate`, not
`{worktree}/run-gate-project/run_gate.py`, so a change to the budget checker
itself is judged by the previous checker. And `cmru r0-r1` no longer checks the
budget rules, so a merge that edits only a `run-gate.toml` or `assay.toml`
should also run `estate-lint/suite`.

## What it does not own

Release-order facts for a specific project (its own `depends_on`, its own
pyproject) stay where the project is. Only the agreement of all of them, and
the doc that states them, is judged here.
