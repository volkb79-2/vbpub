# FINISHER: complete the assigned task

Your previous run ended by saying what it would do next, or without finishing.
Continue in the same worktree and call tools for real (never print a tool call
as text). Read the remaining contract and context, finish
all implementation and review fixes, hand-mutate each new guard, run the
registered gates on a clean final head under the shared gate lock, and write
the complete report at the absolute path in the brief. Verify the route trailer
on every task commit, including merges, and leave the worktree clean. If a gate
cannot run, report the exact reason and what did run; do not claim completion.
If you are short of budget, write the report first (what ran, what did not, the
unchecked items), then continue. The house rules in `scripts/controller/rules.md`
(CTL-50) still bind: compile and test only through the remote dev container,
never `hx.sh`, `ssh`, `docker`, `pytest` or `cargo` in the devcontainer, and
finish with DONE, PARTIAL or BLOCKED (a budget stop is PARTIAL, never BLOCKED).

The final message must give the report path, final head, clean-tree result, and
gate verdicts with the commit each gate judged.
