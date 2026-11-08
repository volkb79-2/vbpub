# RG-55 cgroup-profiler R2 cap: final adversarial review

**Code-review verdict: ACCEPT.** Reviewed branch `rg55-cgprofile-cap-20261011` at exact HEAD `34417c42ef129c184ed37938c4deaa7bb3673af2` (tree `99dbeba948a2a6feb39880c4328c1591d42d724f`) against code base `05e7b6cd5140add21a08a57c84f309423b78b802`. The R2 comparison base is the distinct, explicit pre-wave commit `eb3af770eb16362cff4c39388193c0180c2d370c`.

## Findings, by severity

- **No blocking finding.** The entire code-base diff is one file, `scripts/cgroup-profiler/assay.toml`: `max_mutants` changes from 1,500 to 2,000, with the associated comment. Both commits (`27ddf251a754f68ac23b20fa72a0c3a1074e5583`, `34417c42ef129c184ed37938c4deaa7bb3673af2`) were inspected. The first commit's message/comment treated the old 1,501 result as a full count; the second commit correctly calls it the cap-plus-one sentinel and records the full count.
- **Low — provenance in the inline comment is incomplete.** It pins the pre-wave base exactly but says “current-tree” without naming the source HEAD/tree. Its 1,593 count is true for the reviewed HEAD/tree, as reproduced below. Future source growth below 2,001 could change that count without a cap refusal, so the comment must be read as a historical measurement. This report supplies the missing immutable tree identity. Growth above 2,000 fails closed through the 2,001 sentinel; it does not silently truncate a qualifying run.

## Verification

Commands were run in the target checkout. Exit statuses below are those of the named jobs, not a trailing display command.

| Check / command | Actual exit | Result |
|---|---:|---|
| `git rev-parse HEAD`; `git status --porcelain=v1 --untracked-files=all`; `git log -5 --oneline`; `git diff --find-renames 05e7b6cd5140add21a08a57c84f309423b78b802..HEAD --` | 0 each | Expected HEAD, initially clean; complete diff only the cap and comment in `assay.toml`. |
| `git diff --check 05e7b6cd5140add21a08a57c84f309423b78b802..HEAD` | 0 | No whitespace error. |
| `git merge-base HEAD eb3af770eb16362cff4c39388193c0180c2d370c`; `git rev-list --parents -n 1 HEAD` | 0 each | Merge-base is exactly the supplied pre-wave base; reviewed HEAD has one parent, so Assay's merge-base path applies. |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<this-worktree>/assay/src /home/vscode/.venv/bin/python -m assay.cli plan r2 --file scripts/cgroup-profiler/assay.toml --request-base eb3af770eb16362cff4c39388193c0180c2d370c` (JSON summarized through a Python pipe) | Assay **0**; summary **0**, captured separately via `PIPESTATUS` | `status=ok`, commit `34417c42ef129c184ed37938c4deaa7bb3673af2`, tree `99dbeba948a2a6feb39880c4328c1591d42d724f`, `candidate_count=1593`, 1,593 rows and 1,593 distinct IDs, `max_mutants=2000`, `jobs=1`, `shard=null`, `worktree_integrity=null`. Operator counts: compare-swap 728, boolop-swap 420, bool-const-flip 300, falsy-swap 145 (sum 1,593); 14 files. |
| Python `tomllib` parse of both lane files | 0 | Assay schema 2 and run-gate schema 1; R2 uses `base_source="request"`, `jobs=1`, cap 2,000; run-gate R2 points at assay `r2`, uses `tester-unified`, and requires a clean tree. |
| Source-backed `run-gate.py` `load_config(<lexical cgroup-profiler directory>)` | 0 | Project and inherited root configs validate; R2 linkage is accepted. An initial inspection mistakenly used the launcher symlink's *resolved* parent (`run-gate-project`) and exited **1** with `KeyError: 'r2'`; using the actual cgroup-profiler project directory corrected that reviewer invocation. |

The selected worktree's `assay/src/assay/cli.py` plans with `limit=max_mutants+1`; `assay/src/assay/mutation.py` uses the same bound for execution and returns a pre-submission limit sentinel when the count exceeds the cap. Since this plan returned 1,593, below 2,000, its rows are the complete selected set rather than a truncated 2,001 sentinel. At the former 1,500 cap, this same candidate set would have reached the 1,501 sentinel and the run would refuse before candidate submission. `assay plan` itself reports planning data, not a mutation verdict. No retained old plan artifact was found under this checkout's `.assay/` directories; the old-cap conclusion is derived from the reproduced full plan and the selected source's limit logic, not certified as an independently observed historical run.

## Merge readiness

**Not ready to merge on this review alone.** The exact registered R2 mutation lane has not run at this cap branch HEAD; its full mutation verdict and any required full gate remain pending. The supplied r0-r1, r3 and doctor results are controller-reported context, not gates rerun by this review. A dry-run or this `assay plan` proves configuration/planning only. Run R2 with the explicit pre-wave base against this branch before treating it as mutation evidence. A later no-ff merge commit is a different tree: Assay's `resolve_base` uses a merge HEAD's first parent, so a post-merge run alone would not reproduce this branch's pre-wave comparison.

No container, gate, mutation lane, CIU command, daemon, host operation, test, or commit was run for this review. Only this report was written in the checkout.
