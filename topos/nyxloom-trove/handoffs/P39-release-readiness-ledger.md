# P39 - Release Readiness Ledger

## Goal

Create the final v1/v1.5 release-candidate readiness surface: one canonical
document/checklist that tells an operator which evidence is complete, which
manual live-host evidence is still required, and which commands to run before a
release claim.

This package should be mostly documentation plus small glue only if it removes
duplication. P38 has landed on `main`, so this package should include the
`tui-smoke` acceptance command in the release-readiness surface.

## Workflow

Follow `topos/README.md` "Workflow protocol" exactly.

- Branch: `feat/topos-p39-release-readiness-ledger`
- Worktree: `.worktrees/-topos-p39-release-readiness-ledger`
- Branch from local `main` at or after P38 merge `e6ab8f8`
- Touch only `topos/**`
- Keep `topos/handoff/reports/P39-LOG.md` updated while working
- Finish with `topos/handoff/reports/P39-REPORT.md` and a focused commit

## Required Context

Read before editing:

- `topos/README.md`
- `topos/TUI-SPEC.md` section 9 and release-cut section 0.1
- `topos/docs/STATUS.md`
- `topos/docs/ROADMAP.md`
- `topos/docs/OPERATIONS.md`
- `topos/MEASUREMENTS.md`
- reports for P33, P35, P36, P37, and P38

Baseline evidence available before P39 starts:

- P38 merge commit on `main`: `e6ab8f8`
- Full suite after P38 merge:
  `PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q`
  -> `382 passed in 41.48s`
- Focused acceptance tests after P38 merge:
  `PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests/test_acceptance.py -q`
  -> `40 passed in 7.05s`
- P38 fixture command after merge:
  `PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m topos.acceptance tui-smoke --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --json`
  -> exit `0`, `ok: true`, `frames: 1`, `view: tree`, `profile: auto`

## Functional Requirements

Add a canonical release-readiness document, suggested path:

- `topos/docs/RELEASE-READINESS.md`

The document should include:

- Current release-cut scope: what can be claimed for v1/v1.5 today, and what
  remains explicitly outside the claim.
- A table mapping `TUI-SPEC.md` section 9 acceptance items to evidence sources:
  tests, acceptance commands, measurements ledger entries, and remaining manual
  gates.
- Exact commands for the rootless automated checks:
  - full tests;
  - `py_compile`;
  - `python -m topos.acceptance smoke`;
  - `python -m topos.acceptance steady`;
  - P38 `python -m topos.acceptance tui-smoke` with explicit fixture replay;
  - replay UI smoke through `topos --replay ... --step --ui-smoke`;
  - packaging/wheel smoke.
- A live-host evidence template for:
  - 5-minute Textual TUI CPU/RSS;
  - live DAMON vaddr/paddr acceptance if claiming controlled DAMON;
  - daemon status if claiming non-root daemon mode.
- Explicit non-claims:
  - exact per-cgroup network loss without BPF;
  - live BPF lifecycle;
  - executable admin actions;
  - web UI;
  - GPU/ZFS plugins.
- A short "release blocker" section that points to `MEASUREMENTS.md` for
  evidence that must be pasted before tagging.

Update:

- `topos/README.md` canonical documents list.
- `topos/docs/OPERATIONS.md` release checklist section to point to the new
  document.
- `topos/docs/STATUS.md` only if the readiness document changes wording of the
  current release claim.
- `topos/docs/ROADMAP.md` to mark P39 done when the package is complete.

Keep `topos/MEASUREMENTS.md` as the evidence ledger, not the new document.
`RELEASE-READINESS.md` should point to the ledger and provide paste-in
templates for missing live-host evidence instead of duplicating every historical
measurement.

## Tests / Validation

This is documentation-heavy, but still run:

- full test suite;
- `py_compile` over any touched Python files, if any;
- `python -m topos.acceptance smoke` fixture command;
- P38 `python -m topos.acceptance tui-smoke` fixture command, if P38 has
  landed.

If no Python files are touched, say so in the report.

## Out Of Scope

- Running live-root DAMON on the developer host.
- Performing package publication.
- Implementing missing v2 features.
