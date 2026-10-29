# Nyxloom usage analysis — iteration 4 report

Date: 2026-10-29

Result: **Usage fixes complete; focused coverage and browser acceptance pass;
registered lane mutation campaign incomplete.**

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
