# Real coverage artifacts — carver-owned evidence, never hand-authored

Eight artifacts. Six are three formats × (branch tracking ON / OFF) over the
two-file program in `probe/`; two more (`*.exitarc.*`) come from `probe-exit/`
and exist only to witness the exit-arc spellings described at the bottom of this
file. All were produced by real `coverage.py` runs. They exist because the
branch-capability rules in the wave-1 specification are claims about what these
tools **actually emit**, and this project's own record says a fixture invented
to match a design is the shape of failure that survives a green suite
(A-124, A-131, MEASUREMENTS.md).

**The implementer must not edit any of these six files.** A branch parser that
needs one of them changed is a parser that does not read the real format.

## How they were produced

Inside `probe/`, with `coverage 7.15.3` / `pytest 8.4.2` / CPython in this
devcontainer, on 2026-08-16, driven by `probe/.coveragerc`
(`source = .`, `relative_files = True`):

```sh
# branch tracking ON
coverage run  --rcfile=.coveragerc --branch -m pytest check_sample.py -q
coverage json --rcfile=.coveragerc -o ../coverage-py-json.branch.json --pretty-print
coverage lcov --rcfile=.coveragerc -o ../lcov.branch.info
coverage xml  --rcfile=.coveragerc -o ../cobertura.branch.xml

# branch tracking OFF — same program, same tests, same commands minus --branch
coverage run  --rcfile=.coveragerc -m pytest check_sample.py -q
coverage json --rcfile=.coveragerc -o ../coverage-py-json.nobranch.json --pretty-print
coverage lcov --rcfile=.coveragerc -o ../lcov.nobranch.info
coverage xml  --rcfile=.coveragerc -o ../cobertura.nobranch.xml
```

**No file was edited afterwards** — including the Cobertura documents.
`relative_files = True` is why: without it, `coverage xml` writes the producing
machine's absolute directory into `<sources><source>`, and a fixture carrying
this worktree's path would break for every other reader. With it, the two
emitted `<source>` elements are the empty string and `.`.

**The driver files are named `check_*.py`, not `test_*.py`, and must stay that
way.** `pyproject.toml` sets `testpaths = ["tests"]` with no `norecursedirs`
override, so a `test_*.py` anywhere under `tests/fixtures/` is collected into
the project's REAL suite — measured here: `pytest tests/fixtures/coverage
--collect-only -q` reported `4 tests collected` under the original names and
`no tests collected` under these. pytest still runs an explicitly named file
regardless of the pattern, which is why the generating commands work unchanged.
Renaming these back would quietly add fixture programs to the gate's own suite.

`probe/sample.py` is deliberately shaped so every branch state appears at once:
`classify` has one taken and one untaken arc, `first_two` has a loop with both
arcs of one branch taken, and `never_called` is never entered at all — which is
the case `lcov` spells `-` rather than `0`.

## What each artifact proves — the facts the specification depends on

| fact | witness |
|---|---|
| coverage.py JSON states branch capability **explicitly** | `meta.branch_coverage` is `true` in `coverage-py-json.branch.json` and `false` in `coverage-py-json.nobranch.json` |
| coverage.py JSON carries per-arc detail, not only totals | `files["sample.py"].executed_branches` = `[[5,6],[11,12],[12,11],[12,13]]`, `missing_branches` = `[[5,7],[11,14],[18,19],[18,20]]` |
| its own totals can be cross-checked against that detail | `summary.num_branches` 8 = `covered_branches` 4 + `missing_branches` 4, and 4 = `len(executed_branches)` |
| the combined line+branch percentage IS coverage.py's `percent_covered` | `(covered_lines 8 + covered_branches 4) / (num_statements 13 + num_branches 8)` = 57.14…, exactly `summary.percent_covered` — the metric `--cov-fail-under` compares against |
| lcov states branch capability **nowhere** | `lcov.branch.info` and `lcov.nobranch.info` differ only by the presence of `BRDA`/`BRF`/`BRH` records |
| an lcov record for a branch-free file omits those records **even when tracking is on** | in `lcov.branch.info`, `check_sample.py` carries no `BRF`/`BRH` at all while `sample.py` carries `BRF:8`/`BRH:4` — so a *per-file* capability rule would call this single real artifact "mixed" and refuse it |
| lcov distinguishes "arc not taken" from "block never entered" | `BRDA:18,0,jump to line 19,-` uses `-`, not `0`, because line 18 never ran |
| Cobertura states capability only as a **document-level count** | root `branches-valid="8"` with tracking on, `branches-valid="0"` with it off |
| Cobertura's per-line branch detail is a ratio, not an arc list | `<line number="5" hits="1" branch="true" condition-coverage="50% (1/2)" missing-branches="7"/>` |
| a never-entered branch line reports zero covered arcs in every format | line 18: coverage.py `[18,19]`/`[18,20]` both missing; lcov two `-`; Cobertura `condition-coverage="0% (0/2)"` |

The last row is the invariant `FileCoverage` enforces once, in the model, for
all formats: a line in `missing` can never carry a covered arc.

## The exit-arc fixtures — two shapes that reject a "reasonable" parser

`probe-exit/exits.py` has one branch whose untaken arc **leaves the function**
instead of reaching another line. Generated the same way, `--branch` only:

```sh
coverage run  --rcfile=.coveragerc --branch -m pytest check_exits.py -q
coverage json --rcfile=.coveragerc -o ../coverage-py-json.exitarc.json --pretty-print
coverage lcov --rcfile=.coveragerc -o ../lcov.exitarc.info
```

| fact | witness |
|---|---|
| a coverage.py branch **destination can be negative** | `missing_branches` = `[[5,7],[11,-10]]` — `-10` encodes "exit the function that starts at line 10" |
| so `src` is a line number and `dst` is an opaque identity | a parser validating both members as positive line numbers rejects this real artifact. assay attributes a branch to its SOURCE line and never needs `dst` |
| an lcov branch id is **free text**, not a number | `BRDA:11,0,return from function 'falls_off_the_end',0` — spaces and an apostrophe inside the third field |
| so `BRDA` cannot be parsed by a bare `split(",")` count | split the line into `line`, `block`, and a remainder on the first two commas, then take `taken` off the remainder's RIGHT with `rsplit(",", 1)`. The branch id is whatever is left, opaque |

Both files carry the ordinary shapes too (`[5,6]`/`[5,7]`, `BRDA:5,0,jump to
line 6,1`), so a test that reads them is not exercising only the exotic case.
