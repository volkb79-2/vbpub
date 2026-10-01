# Nyxloom harness search report

Date: 2026-10-01
Result: **DONE** — local session search is integrated into main.

## User behavior

The command accepts a search phrase and returns matching local session IDs:

    nyxloom-harness search 'cli-extended gate backlog' [--sort-by <best|date>] [--client <codex|claude|opencode>]

It searches the locally available Claude Code, Codex, and OpenCode session
stores. The default ordering ranks relevance; date ordering sorts by activity.
The client option limits discovery to one client. Results include session
identity and match metadata, without returning transcript bodies. Search
errors that make a store indeterminate are reported rather than silently
treated as an empty result.

## CLI option grouping

The requested grouped help is supported by cli-extended's OptionSpec.group
field and generated parser help. Nyxloom assigns groups to extract and search
options. Extract help groups its arguments under SESSION SOURCE, WINDOW
SELECTION, CONTENT, OUTPUT, REDACTION AND TASK, and FOLLOWING. Search groups
its options under RESULT ORDER and SESSION SOURCE. No backlog item is needed
for the grouping capability.

## Coverage and documentation

The declared tester-unified gate passed at integration commit c1d0fbe2:
R0 PASS and R1 PASS with 655/655 changed executable lines and 166/166
branches. The product source, tests, and user documentation at that commit
are identical to the Nyxloom tree merged at 2ba90c10. Search-specific
behavioral and failure-boundary cases are in test_harness_search.py.

README, DESIGN-GUIDE, CONSUMERS, USAGE, and CLI-REFERENCE describe the search
command and its choices. The implementation is in harness_search.py and is
registered by cli.py and cli_registry.py.

## Disposition

Search and its documentation are complete and integrated. The original
nyxloom-harness-search worktree's tip is included in the integration history.
