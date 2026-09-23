# Assay B096 adversarial review — round 2 — 2026-09-14

Date: 2026-09-14
Reviewed worktree: `/workspaces/vbpub/.worktrees/assay-b096`
Reviewed HEAD: `2d80012ab32f656b30bbdd90331b4ac751211114`
Reviewer: Luna xhigh

## Verdict

**ACCEPT**

No P0, P1, or P2 findings. The B096 implementation remains limited to
parser-help construction and its regression test. The B092+B098 implementation
diff is present because this branch is based on that combined tip; it was
reviewed as current context, while B092/B098 decisions were not reopened.

## Review evidence

### Current tip and complete diff

| command | result |
|---|---|
| `git rev-parse HEAD` | `2d80012ab32f656b30bbdd90331b4ac751211114` |
| `git status --short --branch` before this report | clean, `## assay-b096` |
| `git diff --stat main...HEAD` | 22 files changed, 981 insertions, 38 deletions |
| `git diff --name-status main...HEAD` | reviewed all 22 paths: B092/B098/B096 source, tests, README, DESIGN-GUIDE, CONSUMERS, CHANGES, backlog, and reports |
| `git diff --check main...HEAD` | PASS; no output |

The B096 implementation delta was checked separately with
`git diff --unified=5 6f76e471^ 6f76e471`. It adds only
`_rejudge_outcome_help()` in `assay/src/assay/cli.py`, its test, and the
document/backlog entries. The exact command
`git diff --exit-code 6f76e471^ 6f76e471 -- assay/src/assay/runner.py assay/src/assay/mutation.py`
returned exit 0. Thus the pre-existing runtime parser/validator path was not
edited by B096; the current main-to-tip runner/mutation changes are the
accepted B092/B098 base changes.

### Owner derivation, alias, and import graph

`assay run --help` was invoked live with:

```text
nice -n 19 ionice -c 3 env PYTHONPATH=src assay run --help
```

It exited 0 and rendered the canonical clause as:

```text
One of killed, survived, crashed, budget_exceeded, equivalent, hung; the
CLI-only convenience alias 'error' is accepted for the canonical 'crashed'
bucket; a union with --rejudge when both are given. Requires --resume.
```

The independent temporary-owner probe imported `assay.verdict` and
`assay.mutation`, asserted `error not in MUTATION_BUCKETS`, appended
`future_bucket` only to `verdict.MUTATION_BUCKETS`, and rebuilt the real help.
Results:

```text
owner_tuple ('killed', 'survived', 'crashed', 'budget_exceeded', 'equivalent', 'hung')
error_in_owner_tuple False
temporary_owner_extension_changes_help True
alias_remains_cli_input_only True
```

The generated `One of ...` clause included the exact temporary tuple, and the
alias remained separately described. An AST probe over `src/assay/cli.py`
found no list/tuple/set literal containing canonical bucket members; the only
canonical source reference is `from .verdict import MUTATION_BUCKETS`.

The fresh-process import probe:

```text
nice -n 19 ionice -c 3 env PYTHONPATH=src python - <<'PY'  # import cli, mutation, verdict
```

returned `fresh_import_graph PASS` and confirmed
`mutation.MUTATION_BUCKETS is verdict.MUTATION_BUCKETS`. No import cycle or
stale-list path was observed.

### Runtime behavior and help regression

Focused serial commands, all run with `nice -n 19 ionice -c 3` and
`PYTHONPATH=src`, returned:

| command | result |
|---|---|
| `pytest -q tests/test_cli_run.py -k rejudge_outcome_help_derives` | PASS — 1 passed, 43 deselected in 0.38s |
| `pytest -q tests/test_runner_run_lane.py -k rejudge` | PASS — 5 passed, 28 deselected in 9.58s |
| `pytest -q tests/test_cli_run.py` | PASS — 44 passed in 79.39s |
| `git diff --check 6f76e471^ 6f76e471` | PASS; no output |

The runner tests cover malformed/empty outcome input, an unknown bucket,
missing `--resume`, and the `error` alias's successful mapping to `crashed`.
No R2 mutation lane or mutation campaign was launched.

### Documentation, schema, and anchors

`pytest -q tests/test_docs_examples_and_vocabulary.py` returned **PASS — 42
passed in 0.59s**. This exercised the shipped loader against the live TOML
examples and the current lane schema. The B096 documentation adds no config
schema key and the examples continue to declare `schema_version = 2`.

An independent local-link probe checked 23 anchored links across README,
DESIGN-GUIDE, and CONSUMERS and returned:

```text
all_local_doc_anchors 23 PASS
b096_design_anchor PASS
b096_consumer_anchor PASS
```

The README B096 paragraph links to both new anchors; DESIGN-GUIDE explains
the owner/alias rationale; CONSUMERS gives the accepted spellings and a
pasteable `--resume --rejudge-outcome` command. `CHANGES.md` records B096
under Unreleased, and the detailed B096 backlog row is marked fixed on
2026-09-13 with the same source, alias, test, and three-document claims.

## Ranked findings

None (P0–P2).

## Scope and residuals

No product implementation file was modified. No merge, release, R2 mutation,
mutation campaign, or tester-unified gate was launched. The required final
tester-unified gate remains a controller responsibility on the combined tip.
