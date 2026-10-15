# Independent release-wave review — Codex Sol xhigh

- Reviewed commit: 135ecacf0144474a4747f1079693d2dc145f684c
- Base: main at review time (0244fe4e7428d5eb6ce1c37ecc02f2cba0b7719d)
- Route: gpt-6-sol, reasoning effort xhigh
- Context: CODEX_SQLITE_HOME=$HOME/.codex/sqlite-shared, CODEX_HOME=$HOME/.codex
- Review session id: 01a13b74-5a37-7b43-8551-5a55f518ee7e
- Read-only review; no tests or gates run.
- git rev-parse HEAD before and after matched the reviewed commit; status was clean both times.

- **P1 — Release gate can accept modified snapshot source.** [gate_snapshot.py:874](/workspaces/vbpub/.worktrees/tool-release-wave-20261014/cmru/src/cmru/gate_snapshot.py:874) relies on `git status` to detect tracked changes. A lane can set `--assume-unchanged` on a tracked file, alter it, and exit successfully; status hides the change while HEAD remains the requested commit, so CMRU accepts evidence from bytes outside that commit. Recheck snapshot index flags after each lane and compare tracked content with the commit independently of status.

- **P2 — Local mutation-audit image builds now fail.** [Dockerfile:104](/workspaces/vbpub/.worktrees/tool-release-wave-20261014/tester-unified/Dockerfile:104) requires `RUN_GATE_TESTER_VERSION`, but the supported local-image path in [remote-mutation-audit-host.sh:93](/workspaces/vbpub/.worktrees/tool-release-wave-20261014/nyxloom/tools/remote-mutation-audit-host.sh:93) supplies no build argument. An audit without `--tester-image` stops at this Dockerfile check. Derive the version from the exact Run-Gate wheel and pass it to this build; update the other published rebuild commands that omit it.

- **P2 — `--check-env` can report a clean scan after skipping source.** [run_gate.py:6907](/workspaces/vbpub/.worktrees/tool-release-wave-20261014/run-gate-project/run_gate.py:6907) uses `Path.rglob`, which silently suppresses directory traversal errors; the new `INCOMPLETE` counter only sees failures reading files that were found. An unreadable package directory can therefore contain uncovered environment reads while the command reports `0 uncovered reference(s)` with no incomplete marker. Walk with an error callback and report traversal failures as incomplete. The swallowed environment-resolution error at [run_gate.py:6901](/workspaces/vbpub/.worktrees/tool-release-wave-20261014/run-gate-project/run_gate.py:6901) needs the same treatment.

- **P2 — Pending release notes can be silently omitted.** [release_notes.py:167](/workspaces/vbpub/.worktrees/tool-release-wave-20261014/cmru/src/cmru/release_notes.py:167) recognizes H2 headings only at column zero. A Markdown changelog with `  ## [Unreleased]` and migration notes is treated as having no pending notes; [fold_unreleased_notes](/workspaces/vbpub/.worktrees/tool-release-wave-20261014/cmru/src/cmru/changelog.py:94) then leaves them out of the tagged version section and published body. Recognize valid indented H2 headings, or refuse this near miss explicitly, and cover it in the release-notes tests.
