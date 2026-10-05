---
kind: backlog-entry
schema_version: 1
id: NL-31
title: "assay.toml session-extract lane runs pytest --maxfail=1 with --cov: a failing run writes no coverage report and assay reports NO_MEASUREMENT instead of naming the test"
status: open
type: "bugfix"
severity: "medium"
provenance: "cmru program 2026-10 W0-GATE (cmru finding BG-03), 2026-10-05"
filed_date: "2026-10-05"
---

## Observed mechanism

`nyxloom/assay.toml` (~:145) declares a lane whose argv is
`pytest ... -n 4 --maxfail=1 -q --cov=src/nyxloom --cov-branch --cov-report=json:coverage-session-extract.json`.

pytest-cov writes its report after the `yield` in its `pytest_runtestloop`
hookwrapper. Under `--maxfail`, `session.Failed` is raised before that code
runs, so a failing run writes no coverage JSON. Assay then reports
`NO_MEASUREMENT/EMPTY_COVERAGE` and its summary ranks that above the failed
R0, so the headline never names the failing test. Reproduced for cmru with
pytest 9.1.1 / coverage 7.16.2 / pytest-cov 7.1.0, where this hid 25
consecutive release failures.

## Proposed fix

Remove `--maxfail=1` from lanes that also produce a coverage artifact (keep it
only on mutation-candidate runs), and consider declaring `--junitxml` as a lane
artifact. Not changed here: the cmru W0-GATE package only files this entry.

## Related

cmru BG-03 (fixed in cmru's own lanes by W0-GATE), assay B146 (verdict summary
should name an R0 FAIL and the first failure instead of ranking
NO_MEASUREMENT higher), ciu CIU-129 (same pattern in `ciu/assay.toml`).
