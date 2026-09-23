# Assay B096 adversarial review — round 1

Date: 2026-09-13
Reviewed worktree: `/workspaces/vbpub/.worktrees/assay-b096`
Reviewed tip: `ee41553da966bd33b376d466c8026feebac0429b`
Implementation: `6f76e471d024fe2d12b8f673d6d642521c2347bf`
Verification: `de1ef79967c92b1eabc5cfae0d06010e04f7fe69`

## Verdict

**ACCEPT**

No ranked defects found. B096's implementation is limited to parser-help
construction and its regression test; the runtime rejudge path is unchanged.
The canonical owner tuple remains free of `error`, and the alias is translated
to `crashed` only at the CLI runner boundary.

## Review evidence

The complete diff from `6f76e471^` through the reviewed tip contains 10 files:
the B096 implementation/test, CHANGES, README, DESIGN-GUIDE, CONSUMERS,
backlog, and three existing/added reports. `git diff --check` passed. The
implementation diff has no changes to `assay/src/assay/runner.py` or
`assay/src/assay/mutation.py`:

| command | result |
|---|---|
| `git status --short --branch` and `git rev-parse HEAD` | clean at `ee41553d` before this report; expected branch `assay-b096` |
| `git diff --stat 6f76e471^ HEAD` | 10 files, 204 insertions, 10 deletions |
| `git diff --exit-code 6f76e471^ 6f76e471 -- src/assay/runner.py src/assay/mutation.py` | PASS; no runtime-file diff |
| `nice -n 19 ionice -c 3 env PYTHONPATH=src assay run --help` | PASS; help lists `killed`, `survived`, `crashed`, `budget_exceeded`, `equivalent`, `hung`, then separately describes the CLI-only `error` alias for `crashed` |
| temporary-owner probe using `main(['run', '--help'])`, with `verdict.MUTATION_BUCKETS + ('future_bucket',)` | PASS; `future_bucket` and the exact generated `One of ...` clause appeared; alias remained distinguished; `error_in_owner_tuple = False` |
| `nice -n 19 ionice -c 3 env PYTHONPATH=src pytest -q tests/test_cli_run.py -k rejudge_outcome_help_derives` | PASS — 1 passed, 43 deselected |
| `nice -n 19 ionice -c 3 env PYTHONPATH=src pytest -q tests/test_runner_run_lane.py -k rejudge` | PASS — 5 passed, 28 deselected; includes malformed/unknown/refusal and `error` alias behavior |
| `nice -n 19 ionice -c 3 env PYTHONPATH=src pytest -q tests/test_docs_examples_and_vocabulary.py` | PASS — 42 passed; live loader/schema, closed vocabulary, and README→DESIGN-GUIDE anchor checks |
| direct B096 anchor/schema probe | PASS; README B096 design link present and resolves, CONSUMERS B096 section present, contract terms present, and all three docs retain current `schema_version = 2` examples |
| `nice -n 19 ionice -c 3 git diff --check 6f76e471^ HEAD` | PASS |

The temporary-owner probe was in-process only and restored the tuple in a
`finally` block; it made no source edit. The runner/mutation source comparison
and the focused rejudge tests together falsify a help-only change that would
silently alter runtime parsing or make `error` canonical.

## Ranked findings

None (P0–P2).

## Scope and residuals

No product files, merge, release, mutation campaign, or R2 lane was started.
The tester-unified gate was not run in this review worktree, as the handoff
assigns that gate to the controller on the final combined tip.
