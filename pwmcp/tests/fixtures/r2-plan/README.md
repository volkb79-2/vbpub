# `r2-plan` fixtures — a VERBATIM plan slice and two real diffs

These pin the ONE fact `tools/r2/select_changed_candidates.py` depends on and that prose
kept getting wrong: **a real `assay plan r2 --json` carries REPOSITORY-ROOT-relative
paths** (`pwmcp/client/src/pwmcp_client/page_content.py`), not project-relative ones,
because assay anchors every path on the git repository top rather than on the project
directory it was invoked from.

| file | what it is | how it was produced |
|---|---|---|
| `real-plan-slice.json` | 261 candidate rows for ONE target of the r2 plan — verbatim, no field renamed or dropped — plus the plan-level fields the selector reads and a `_provenance` block | `assay plan r2 --json` run with cwd `pwmcp/` (assay.toml `[lanes.r2.judge] source_roots = ["."]`) at commit `2cd57f3c77e0abe20c4236148983170b66f24fa1`; the full plan had 1293 candidates, of which every path's first component is `pwmcp` |
| `diff-repo-root.diff` | the changed lines of `page_content.py` in the REPOSITORY-ROOT form the documented recipe produces | `git diff -U0 3ef8b2642^..3ef8b2642 -- client/src/pwmcp_client/page_content.py`, cwd `pwmcp/` |
| `diff-project-relative.diff` | the SAME commit pair in the PROJECT-RELATIVE form | the same command with `--relative` |

The two diffs differ in nothing but their path prefix:

```
a/pwmcp/client/src/pwmcp_client/page_content.py   (112 lines of diff, 51 selected of 261 candidates)
a/client/src/pwmcp_client/page_content.py         (only with --project-prefix pwmcp/, or after disclosure)
```

Refresh policy: this is a RECORDED measurement, not a snapshot of the current lane. It is
deliberately not regenerated per release (a plan changes with every line of judged source).
Regenerate only when the path-form claim itself is being re-checked, and keep the
`_provenance` block honest when you do — `tests/test_r2_candidate_recipe.py` asserts the
recorded plan shape, that both diffs select the same 51 candidates, and that a derivation
prints the prefix it used.

Why not a fresh full plan: 1293 candidate rows is ~640 KiB in the tree for the same fact one
target already proves.
