---
name: cli-extended-review
description: Use when reviewing or adopting the surface of a cli-extended CLI (help text, flags, semantic review cases, findings); drives sync, pack, judge, edit and check.
---

# Review a cli-extended CLI surface

You judge the CLI; the `cli-extended` tool collects the evidence and checks your
work. The library never rewrites the review catalog or the findings file. You
edit both by hand.

## Procedure

1. Locate the project configuration: `cli-extended.toml`, or `[tool.cli-extended]`
   in `pyproject.toml`, found by walking up from the current directory (or pass
   `--config PATH`; pass `--cli ID` when several CLIs are configured). Read it to
   learn the review catalog, manifest, spec and findings paths.
2. Run `cli-extended surface sync` to bring the generated manifest and spec region
   up to date.
3. Run `cli-extended surface pack --output <tmp>` (a temporary file outside the
   repository) and read the whole bundle: the rubric, the help of every route, and
   every case awaiting review.
4. Judge each case and each help block with the rubric. For every case decide
   `accept` or `refuse` and why.
5. Edit the rows of the CLI's configured `review` catalog by hand: `state`, `decision`, `rationale`,
   `reviewed_signature` (copy the signature from the bundle), `invocation`,
   `expected_exit_status`, `effects` and `test_ids`. Edit the findings file by hand
   for every problem the rubric turned up.
6. Run `cli-extended surface sync`, then `cli-extended surface check`, then
   `cli-extended surface report`. Fix what `check` reports and repeat until it
   passes; the report lists whatever still needs a decision.

## Rules

- Never invent a test ID. Write the behavioural test first, run it, then link its
  real node ID in `test_ids`.
- Do not mark a case `active` without a linked, passing behavioural test.
- Every finding needs a concrete remedy: say what to change, not that something
  is wrong. An open `blocker` or `major` finding fails `check`.
- A `wontfix` finding needs a `rationale` that explains why the behaviour stays.
- Close a finding by changing its status to `fixed` only after the fix is in the
  code and `check` agrees; never delete a finding to make `check` pass.
- A finding whose `route` no longer exists is stale; update or remove its route.
- Edit only the catalog and the findings file. Generated files change only through
  `cli-extended surface sync`.
