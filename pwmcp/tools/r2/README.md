# R2 campaign tooling

`select_changed_candidates.py` builds the candidate list for a release-scoped (changed-line)
mutation campaign.

```bash
# on the release checkout
assay plan r2 --json > plan.json
git diff -U0 <base>..<release> -- <source paths> > release.diff
python3 tools/r2/select_changed_candidates.py \
  --plan plan.json --diff release.diff \
  --out .run-gate/only-candidates-<scope>.txt
run-gate r2 --only-candidates .run-gate/only-candidates-<scope>.txt
```

It reads each hunk header's NEW-file range (`@@ -a,b +c,d @@` covers lines `c .. c+d-1`;
a bare `+c` is one line; `+c,0` covers none), strips the diff's `a/`/`b/` prefixes, writes
one candidate id per line in plan order, prints `selected N of M candidates` on stderr,
and exits 1 without writing a file when nothing is selected.

**Path form.** `assay plan r2 --json` carries REPOSITORY-ROOT-relative paths
(`pwmcp/client/src/...`), because assay anchors on the git repository top rather than on the
project directory. A `git diff` from the project directory produces the same form and works
directly; a PROJECT-relative diff (`git diff --relative`) needs `--project-prefix pwmcp/`.
Without it the tool derives the prefix only when the plan makes it unambiguous, and PRINTS
the derived prefix — it never strips one silently, and an ambiguous match is a refusal that
lists the forms it tried.

```bash
# project-relative diff (git diff --relative) — state the prefix:
python3 tools/r2/select_changed_candidates.py --plan plan.json --diff release.diff \
  --out .run-gate/only-candidates-<scope>.txt --project-prefix pwmcp/
```

A selected subset is PARTIAL by construction: the candidates left out are reported not
started and run-gate records the run as `selective`, so it is never a whole-target R2
PASS. The recipe and its traps: [docs/CONSUMERS.md](../../docs/CONSUMERS.md#release-scoped-r2-campaigns-changed-line-candidate-selection);
the reasoning: [docs/DESIGN-GUIDE.md](../../docs/DESIGN-GUIDE.md#release-scoped-r2-campaigns);
the tests: `tests/test_r2_candidate_recipe.py` (which runs on a VERBATIM slice of a real
plan plus two real diffs in `tests/fixtures/r2-plan/`, in both path forms).
