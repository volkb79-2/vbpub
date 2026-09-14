# Assay B096 implementation report

Implementation commit: `6f76e471d024fe2d12b8f673d6d642521c2347bf`
(`fix(assay): B096 derive rejudge outcome help from vocabulary`). The commit
includes the CLI helper, regression test, README/DESIGN-GUIDE/CONSUMERS sync,
CHANGES entry, and B096 backlog close-out. It carries the required Luna
trailer.

## Contract evidence

`assay.cli._rejudge_outcome_help()` reads
`assay.verdict.MUTATION_BUCKETS` while constructing parser help. The canonical
names are joined into the existing help shape; `error` is described separately
as the CLI-only alias for canonical `crashed` and is not added to the owner
tuple. The regression temporarily adds `future_bucket` to the owner tuple and
checks the real `assay run --help` output for the complete generated list and
alias.

## Verification

All test commands were run serially under `nice -n 19 ionice -c 3` with
`PYTHONPATH=src` from `assay/`:

| command | result |
|---|---|
| `pytest -q tests/test_cli_run.py` | **PASS** — 44 passed in 85.78s |
| `pytest -q tests/test_runner_run_lane.py -k rejudge` | **PASS** — 5 passed, 28 deselected in 0.95s |
| `pytest -q tests/test_docs_examples_and_vocabulary.py` | **PASS** — 42 passed in 0.81s |
| red-first B096 regression before implementation | **EXPECTED RED** — 1 failed, 43 deselected; `future_bucket` was absent from help |
| `git diff --check` | **PASS** |

Registered `tester-unified` gate: **NOT RUN in this worktree**. The requested
focused PSI-safe checks are green; no R2 lane or mutation campaign was
launched. The operator-supplied `assay-B096-BRIEF-1.md` remains untracked and
unchanged.

No merge or release was performed.
