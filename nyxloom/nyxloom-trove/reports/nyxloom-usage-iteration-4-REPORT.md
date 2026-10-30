# Nyxloom usage analysis — iteration 4 report

Date: 2026-10-29

Iteration-4 result: **Usage fixes complete; focused coverage and browser
acceptance pass; registered lane mutation campaign incomplete.** The later
`nyx-b3fix` follow-up below is the current status for this branch.

## Outcome

`nyxloom usage report --format html --out FILE` renders the frozen
`usage-report-v1` and `usage-charts-v1` contracts as one offline HTML file.
The interactive table uses the exact call ledger; joined charts use the
versioned bucket and point identities. Chart data comes from the complete
windowed tree before presentation folding, so `--depth` and `--min-share` do
not change chart totals. `usage windows` applies its UTC half-open range to
calls, reset evidence, reset timestamps, threshold events and interval
metrics. Conflicted representatives are visible in CSV, table, HTML and PDF.

The review follow-ups also make corrupt anchor state explicit, reject
ambiguous custom fast/over-threshold pricing, create private report parent
directories, and remove the duplicate project-key helper.

## Evidence

| Oracle | Result |
| --- | --- |
| `git diff --check main...HEAD` | PASS after the final commit |
| Registered `session-extract` lane selection, R0 | PASS |
| Registered `session-extract` lane selection, R1 | PASS — changed-line coverage 100.0%; 7,295/7,295 statements and 1,580/1,580 branches across 28 considered files |
| Usage HTML BrowserLease acceptance in tester-unified | PASS — full and folded ledger totals reconcile with joined chart points; 0 network requests |
| Registered `session-extract` lane overall | INCOMPLETE — exit 4, `BUDGET_EXCEEDED/LANE_TIMEOUT`; R2 completed 1,199/1,201 candidates, with 2 per-candidate timeouts; R3 did not start |

The usage tests are part of the registered lane selection, including
`test_usage_report.py`, `test_usage_pricing.py`, `test_usage_documentation.py`,
`test_usage_charts.py`, `test_usage_html.py`, `test_usage_html_browser.py`,
`test_usage_pdf.py`, `test_usage_loader_coverage.py`, and
`test_usage_coverage.py`. The lane's R0 and R1 results were recorded at
implementation commit `1e9cf22d162c08a81a192cbb292fa1edb68ad3e3`; the remaining
change before the browser acceptance was a test locator update. The final R2
timeouts were `presets.py:91` (`True -> False`) and `usage/loader.py:397`
(`NotEq -> Eq`). They were killed at the configured per-candidate limit with
clean process-tree evidence. Since R2 did not complete, the lane did not reach
R3 and must not be reported as a green full gate.

The browser acceptance was run as a detached tester-unified container using
the registered browser helper. Its assertions cover both the complete and
folded views, including joined context/swim chart points, and assert that the
page makes no network requests.

## Content-free 24-hour sample

Generated at `2026-10-28T16:45:23.143096Z` with `--no-labels`:

- 3,642 calls across 838 scanned transcript files
- Raw token units: 775,714,996
- Experimental weighted units: 151,131,944.0
- API-equivalent USD: 415.18516565
- Input 27,094; cache read 724,297,325; cache write 5m 46,046,002; cache write 1h 1,868,420; unclassified cache write 0; output 3,476,155
- HTML size: 11,455,042 bytes

This sample reports aggregate numeric metadata only; no labels or transcript
content were printed or saved. It is a usage example, not gate evidence.

## Complete and deferred

HTML rendering, responsive/offline behavior, filters, hierarchy sorting and
collapse, time controls, synchronized row/chart selection, accessible hover
details, anonymize display mode, `--no-labels`, provenance footer, and
landscape print CSS are implemented. PDF output is implemented through the
optional PWMCP `BrowserLease` client. The optional TUI remains deferred. No
screenshots were committed.

The HTML anonymize switch only changes visible labels; the report data retains
labels unless `--no-labels` is used.

## Independent review round nyx-b3fix — 2026-10-29

Initial 2026-10-29 verdict (superseded by the continuation below):
**FIXES-NEEDED — the full registered lane did not complete.**
The explicit missing-anchor behavior is implemented, and the review's P2 finding
is covered by new direct-reader and CLI regression assertions in
`tests/test_usage_pricing.py`. The first post-fix lane invocation was blocked at
test collection by a stale `tester-unified:local` image missing the already
declared `pypdf==6.19.0` test dependency. The image was rebuilt with the
supported `tester-unified/build-image.sh` workflow under `gate.lock`; its build
log confirms `pypdf==6.19.0` and Run-Gate `24.1.1.dev53+gc2448ef59`, image ID
`sha256:fca35f461d8a4098ae229c0883fc029f13b6bc9ad97bd285943e23673a547fe0`.

| `session-extract` attempt | Run-Gate result | Phase evidence |
| --- | --- | --- |
| 1 of 2, target `c2448ef59a24c51822484e6a05a16afda44e03f7` | exit 3, `NO_MEASUREMENT/EMPTY_COVERAGE` | R0 failed while collecting `tests/test_usage_pdf.py` (`ModuleNotFoundError: pypdf`); R1 had no artifact; R2 started 0 candidates; R3 was inconclusive. |
| 2 of 2, same target | exit 1, `FAIL/COMMAND_FAILED` | R0 had 2 failing tests; R1 reported 99.98874254193403%, all 1,580/1,580 branches covered, with only `anchors.py:156` uncovered; R2 started 0 candidates; R3 was inconclusive because R0 did not pass. |

The two R0 failures in attempt 2 were
`test_anchor_store_rejects_malformed_entries_duplicate_ids_and_parent_errors`
(the parent security check was reached after the read, changing its refusal to
“cannot read” and leaving its `cannot secure` branch uncovered) and
`test_usage_anchor_docs_distinguish_missing_default_and_explicit_stores` (the
README had a Markdown line break inside a phrase checked as a string). Commit
`98dd2bbde` now secures the parent before reading and keeps the same private
directory check before writing; the docs assertion now normalizes layout
whitespace without relaxing its wording. `git diff --check` and
`python3 -m py_compile` passed after those corrections; the registered lane was
not rerun afterward.

At the time of this initial report, neither invocation reached Assay's R2
candidate plan, so both candidate counts were 0. The earlier 1,199/1,201
mutation result in the iteration-4 evidence table was historical evidence from
an earlier tree, not a green result for that follow-up. Those conclusions are
superseded by the 2026-10-30 continuation below.

Attempt 2 final Run-Gate output tail:

```text
run-gate: footprint session-extract: peak 612 MiB, p90 470 MiB, 1.05 cores avg, 0.1 s stalled on memory (full), hot-set p90 0 MiB; history median peak - (0 runs)
run-gate: lane 'session-extract' failed with exit 1; full container logs preserved at /tmp/run-gate/run-gate-vbpub-session-extract-2114880-1793289035.log
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/nyxloom-usage-20261028/nyxloom/.assay/verdict-session-extract.json
run-gate: state directory: /workspaces/vbpub/.run-gate/assay-state/.worktrees/nyxloom-usage-20261028/nyxloom
run-gate: assay failure digest: verdict=FAIL
run-gate: WARNING: assay closeout evidence_error: None (lane verdict FAIL (exit 1) is unchanged); retained json /tmp/run-gate/lanes/session-extract/1c2d7882de6b20ccad0d6df51e9b0244.campaign.json, text /tmp/run-gate/lanes/session-extract/1c2d7882de6b20ccad0d6df51e9b0244.campaign.txt
run-gate: lane 'session-extract' verdict FAIL; exit_code 1; reason COMMAND_FAILED; log /tmp/run-gate/lanes/session-extract/1c2d7882de6b20ccad0d6df51e9b0244.log
```

## nyx-b3fix continuation — 2026-10-30

Current verdict: **R0, R1, and R3 pass; R2 remains `BUDGET_EXCEEDED`; P1 is
still open.** The usage anchor regression now covers `anchors.py:156` on commit
`a43c0fcf97d8ef14b592b8eb0c6b377ab0e1251c`. Its registered R1 result is 100.0%
(7,306/7,306 statements; 1,580/1,580 branches; 28 considered files).

| Continuation evidence | Result |
| --- | --- |
| Registered `session-extract` R0 on the corrected tree | PASS; no failures |
| Registered `session-extract` R1 on the corrected tree | PASS; 100.0%, 7,306/7,306 statements and 1,580/1,580 branches |
| R2 campaign | 1,203/1,203 terminal; 889 killed, 312 survived, 2 `budget_exceeded`; 0 pending |
| R3 uncovered-line canary | PASS |
| Overall registered lane | exit 4, `BUDGET_EXCEEDED/LANE_TIMEOUT` |

The two remaining budget candidates were rejudged through Run-Gate with the
existing 120-second per-candidate cap and both reached the same terminal
outcome: `presets.py:91` (`True -> False`, ID prefix `ebe086…`) and
`usage/loader.py:397` (`NotEq -> Eq`, ID prefix `558916…`). The R2 campaign is
fully enumerated, but Assay still reports the lane as budget-exceeded; the
terminal counts do not make that a green R2 result. No candidate limit, time
budget, or mutation scope was relaxed.

The first selective rejudge request was refused before the lane started:
Run-Gate's version floor parser did not recognize the worktree-sourced Assay
version `9.0.1.dev1083+ga43c0fcf9`. Run-Gate now accepts the numeric release
part of its own setuptools_scm `.devN+g...` source builds while continuing to
refuse opaque versions and a development build exactly at the stable feature
floor. The fix and its docs/tests are commit `829f30db7e219f3a6fcaf1e493cb7508ccbe18a0`;
registered `run-gate selftest` passed with 2,435 passed, 1 skipped, 9/9 changed
lines covered, and 6/6 branches taken.

An initial selective invocation using that new CLI while HEAD was `829f30d`
correctly treated the different full commit as a different Assay tree and
started a full rejudge. It was interrupted through Run-Gate after five
candidates. I then temporarily detached to the exact Nyxloom campaign commit
`a43c0fcf` and ran the registered lane using a task-scoped wheel of the tested
Run-Gate source. Assay resumed 1,196 prior results, rejudged the two requested
budget candidates, and replayed the five records from the interrupted
different-commit run. All seven reached terminal outcomes; R0/R1 and R3 passed.
The original branch was restored afterwards. The tester image was not rebuilt;
the already-built image contained `pypdf==6.19.0`;
the existing `tester-unified:local` image ID remains
`sha256:fca35f461d8a4098ae229c0883fc029f13b6bc9ad97bd285943e23673a547fe0`.

The final exact-tree Run-Gate log is
`/tmp/run-gate/lanes/session-extract/d1b8b0eadd04fb3c014f386fd8d3b1ed.log`;
its campaign analysis is
`/tmp/run-gate/lanes/session-extract/d1b8b0eadd04fb3c014f386fd8d3b1ed.campaign.json`.

The 312 surviving candidates and two budget-exceeded candidates are grouped
by source file, line range, operator, and provisional triage classification in
the [R2 survivor inventory](nyxloom-usage-r2-survivor-inventory-2026-10-30.md).
Its adjacent JSON sidecar lists every candidate ID and outcome. The inventory
does not change the waiver or report R2 as green; candidate-level triage and
any killing tests remain deferred.
