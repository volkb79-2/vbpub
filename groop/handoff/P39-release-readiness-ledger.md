# P39 - Release Readiness Ledger

## Goal

Create the final v1/v1.5 release-candidate readiness surface: one canonical
document/checklist that tells an operator which evidence is complete, which
manual live-host evidence is still required, and which commands to run before a
release claim.

This package should be mostly documentation plus small glue only if it removes
duplication. It depends on P38 if it references the `tui-smoke` acceptance
command.

## Workflow

Follow `groop/README.md` "Workflow protocol" exactly.

- Branch: `feat/groop-p39-release-readiness-ledger`
- Worktree: `.worktrees/-groop-p39-release-readiness-ledger`
- Branch from local `main` after P38 merges
- Touch only `groop/**`
- Keep `groop/handoff/reports/P39-LOG.md` updated while working
- Finish with `groop/handoff/reports/P39-REPORT.md` and a focused commit

## Required Context

Read before editing:

- `groop/README.md`
- `groop/TUI-SPEC.md` section 9 and release-cut section 0.1
- `groop/docs/STATUS.md`
- `groop/docs/ROADMAP.md`
- `groop/docs/OPERATIONS.md`
- `groop/MEASUREMENTS.md`
- reports for P33, P35, P36, P37, and P38

## Functional Requirements

Add a canonical release-readiness document, suggested path:

- `groop/docs/RELEASE-READINESS.md`

The document should include:

- Current release-cut scope: what can be claimed for v1/v1.5 today, and what
  remains explicitly outside the claim.
- A table mapping `TUI-SPEC.md` section 9 acceptance items to evidence sources:
  tests, acceptance commands, measurements ledger entries, and remaining manual
  gates.
- Exact commands for the rootless automated checks:
  - full tests;
  - `py_compile`;
  - `python -m groop.acceptance smoke`;
  - `python -m groop.acceptance steady`;
  - P38 `python -m groop.acceptance tui-smoke`;
  - replay UI smoke;
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

- `groop/README.md` canonical documents list.
- `groop/docs/OPERATIONS.md` release checklist section to point to the new
  document.
- `groop/docs/STATUS.md` only if the readiness document changes wording of the
  current release claim.
- `groop/docs/ROADMAP.md` to mark P39 done when the package is complete.

## Tests / Validation

This is documentation-heavy, but still run:

- full test suite;
- `py_compile` over any touched Python files, if any;
- `python -m groop.acceptance smoke` fixture command;
- P38 `python -m groop.acceptance tui-smoke` fixture command, if P38 has
  landed.

If no Python files are touched, say so in the report.

## Out Of Scope

- Running live-root DAMON on the developer host.
- Performing package publication.
- Implementing missing v2 features.
