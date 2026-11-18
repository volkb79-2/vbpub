# FINISHER: complete the report and evidence audit

Continue in the same session and worktree and call tools for real (never print
a tool call as text). Read the full source chunk named in the brief in several
reads (for example 400 lines at a time). Update the absolute report
incrementally, appending the rows you have established about every 100 lines
and checking with grep that they persisted, with file dispositions, findings,
tests, hand mutations, gate evidence, and commit audit. Fill the summary last. Before
ending, verify the report is not a skeleton, the worktree is clean, every
commit including merges has exactly one route trailer, and each gate log names
the final head. If context or budget is short, list unread ranges and
unchecked evidence explicitly; do not claim completion.

The final message must state the report path, final head, and test/gate verdicts.
