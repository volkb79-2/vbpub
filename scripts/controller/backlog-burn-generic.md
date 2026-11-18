# Generic backlog burn-down package (read together with the project-specific wrapper brief that points here)

## Intent
Close a coherent batch of OPEN items of ONE project's backlog with real fixes, tests and honest closure, in small independently reviewable commits, so a reviewer can merge the whole batch or the good part of it. Do the backlog and fold in bugs.

## How to choose the batch
1. Read the project's backlog file (named in the wrapper) fully; list every OPEN item with id, severity and a one-line verdict "easy / average / hard / needs-operator / design".
2. Select 6-12 items that are EASY or AVERAGE (clear acceptance, local change, testable offline), prefer bugs with a reproducible wrong behavior and items that unblock other work; skip hard/design/needs-operator items but record why in the report (a short "skipped" table). Items the code already fixes: verify with a failing-then-passing test or a code reference, close with evidence ("known issue / by design" is a legitimate close with a documented reason; do not build machinery for it).
3. Check each chosen item against CHANGES.md and git log first (it may already be fixed), and check that the item's claim is still true on the current code before changing anything.

## Work rules
- Per item: failing test first (or an observable before/after), minimal fix, update the backlog entry (status, evidence, closed date) and CHANGES.md in the same commit; one commit per item (`git commit --only -- <paths>`), exactly the trailer your route requires, never amend others' commits.
- Hand-mutants: for every non-trivial fix, a mutant of the fix that the new test kills (record in the report or the project's mutation-check file if one exists).
- Never weaken a test, floor, lint or gate to make something pass. Never change another project's code or lane declarations: if an item needs it, file the finding in THAT project's backlog (ids unique across origin AND all worktrees) and skip. A package that adds `cmru` calls, estate files or edits lane declarations in other projects needs those projects' lanes, which is a known gate-coverage gap: prefer not to.
- File every new finding in the project's backlog (ids unique across origin AND all worktrees: grep origin/main and `git worktree list` trees before choosing the id).
- No hosts, no docker commands beyond the gate wrapper, no network, no secrets or controller-local paths or host addresses in repo files. File changes with the Edit/Write tools only (no sed or script rewrites of repo files). Every Bash call carries a `description` that states its intent.
- Living STATE FILE (so a fresh session can continue without re-orienting): `$CONTROLLER_RUNS_DIR/<run-name>.state.md`, updated at EVERY commit: items done, decisions and why, next item with the exact file paths and test commands, open questions. If your session ends before the report, the controller resumes or restarts from this file.
- Gates on the FINAL head via `scripts/controller/gate-run.sh` (cwd = the project directory of the worktree; profiling and the tester image follow the gate wrapper's defaults), one gate container at a time: run EVERY lane of the project that exercises the changed files (read the project's `run-gate.toml` lane declarations; the wrapper names the minimum), logs `$CONTROLLER_LOGS_DIR/<run-name>-<lane>.log` with `run-gate.commit` equal to the head and the new tests visible in the PASS output. Read each verdict in a separate step.
- Report `<run-name>-REPORT.md` at the absolute path the wrapper names: head, per-item table (id, verdict, commit, test, mutant), skipped table with reasons, new findings filed, gates with heads, NOT done, route experience. End with a short final message ONLY after the report exists.

## Wrong solutions
Closing items without a test or code evidence; fixing symptoms in the test instead of the code; broad refactors; touching files outside the project; mixing unrelated items in one commit; leaving work uncommitted at the end.
