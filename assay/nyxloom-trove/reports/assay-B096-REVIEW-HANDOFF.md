# B096 final adversarial review handoff

Review the assay B096 implementation on branch tip `de1ef79967c92b1eabc5cfae0d06010e04f7fe69`
in `/workspaces/vbpub/.worktrees/assay-b096` before merge.

Read `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`, repository
`AGENTS.md`, and `assay/nyxloom-trove/reports/assay-B096-BRIEF-1.md` first.

This is a fresh adversarial review, not an implementation continuation. Check
the complete B096 diff and its docs/tests. Falsify these claims with live
probes and focused serial tests:

1. `assay run --help` derives the canonical `--rejudge-outcome` bucket names
   from `assay.verdict.MUTATION_BUCKETS`, so adding a temporary owner bucket
   changes help without a second hard-coded edit.
2. The CLI-only `error` alias remains documented and accepted for `crashed`
   but is not added to the canonical vocabulary; runtime parser behavior is
   unchanged.
3. The help grammar remains clear, deterministic, and complete, with no
   import-cycle or stale-list path.
4. B096's README/DESIGN-GUIDE/CONSUMERS claims, CHANGES/backlog status,
   schema/version statements, and cross-document anchors are synchronized.

Do not start a mutation campaign, modify product files, merge, or release.
The controller will schedule the required tester-unified gate on the final
combined tip. Write a dated report under `assay/nyxloom-trove/reports/` with
ACCEPT or REJECT, exact commands/results, and ranked findings. Cap at three
rounds; reuse this reviewer for any fix-verification round while alive. Use
Luna xhigh only; Sol is reserved for a genuinely unsolvable Luna issue.
