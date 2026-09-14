# RG-26 `gate-full --base` proof repair — implementation brief

**Controller:** RG-55 controller. **Worker:** fresh Luna xhigh.
**Worktree:** `/workspaces/vbpub/.worktrees/rg55-gate-full-base-propagation`.

## Required context

Read `/workspaces/vbpub/AGENTS.md` and
`/workspaces/vbpub/nyxloom/reference/AUTHORING.md`, then read this branch's
`run-gate-project/nyxloom-trove/reports/run-gate-RG26-gate-full-base-propagation-REVIEW-round1.md`
in full. Also inspect `run-gate-project/SPEC.md`, `run-gate.toml`,
`tests/test_run_gate.py`, README, CHANGES, and the existing RG-26/RG-52
reports. Do not touch `/workspaces/dstdns` or operator-owned dirty files.

## Objective and blocker

Commit `84fab73e` correctly changed the shipped `run-gate-project/run-gate.toml`
`gate-full` conjunction to pass `{base}` to the nested `assay-r1`, and its live
acceptance probe passed. The fresh Luna xhigh reviewer rejected it only because
no committed test reads the real shipped `run-gate.toml`; synthetic conjunction
tests stay green if the production declaration is reverted. Close exactly that
proof gap.

Add a behavioral/construction test that loads the real project
`run-gate.toml` and asserts the exact `gate-full` command shape and ordering:
`selftest`, then `./run-gate.py --base {base} assay-r1`, then `assay-r3`, while
protecting the other lane declarations as appropriate. The test must go red if
the production `{base}` token is removed or moved. Keep the existing live
acceptance evidence and run a fresh real dry-run/acceptance probe if needed.
Do not change the runtime implementation unless the test proves a separate
defect; preserve base safety, default behavior, and non-gate-full lanes.

## Quality and handoff

Use serial `nice -n 19 ionice -c 3` tests and PSI gating. No mutation lane may
be launched while the two RG-55 mutation/recovery lanes are active. Run the
targeted tests, the package's real non-mutation gate, and `git diff --check`.
Commit only the focused test/necessary docs with
`Co-Authored-By: GPT-5 Codex <noreply@openai.com>`. Do not modify the existing
round-1 report. Return exact paths, commit, commands, exit statuses, and any
remaining blocker. If approaching the dispatch context/call ceiling,
checkpoint coherently with a successor BRIEF and retention prompt before
returning. Do not use Sol.
