# Nyxloom usage analysis — iteration 4 report

Date: 2026-10-28

Result: **IMPLEMENTATION COMPLETE; full suite and browser acceptance pass; controller gate pending.**

## Outcome

`nyxloom usage report --format html --out FILE` now renders the frozen
`usage-report-v1` and `usage-charts-v1` contracts as one offline HTML file.
The interactive table uses the exact call ledger; joined charts use the
versioned bucket and point identities. Chart data is built from the complete
windowed tree before presentation folding, so `--depth` and `--min-share` do
not change chart ownership or HTML call totals. `--no-labels` removes topics
before serialization. HTML output remains private (mode `0600`). The existing
price provenance, account-limit diagnostics, and local-share meanings remain
separate.

## Evidence

| Oracle | Result |
| --- | --- |
| `git diff --check` | PASS |
| Full Nyxloom pytest suite | PASS — 5,756 passed; 2 named opt-in skips |
| Usage package line and branch coverage | PASS — 2,614/2,614 statements and 1,016/1,016 branches (100%) |
| Added `cli.py` lines and branch arcs | PASS — 22/22 added lines and 2/2 added-source branch arcs covered |
| Managed BrowserLease interaction probe | PASS — full and folded tables reconcile all 3 calls; 0 network requests |
| Registered Nyxloom tester-unified gate | Pending; controller-owned per assignment |

The full suite skipped the opt-in real-transcript test (`NYXLOOM_REAL_TRANSCRIPT`)
and the collected browser test (`NYXLOOM_RUN_BROWSER_ACCEPTANCE`). The browser
acceptance was run separately against the reachable PWMCP service and passed.
The controller still owns the registered tester-unified gate.

## Content-free 24-hour sample

Generated at `2026-10-28T16:45:23.143096Z` with `--no-labels`:

- 3,642 calls across 838 scanned transcript files
- Raw token units: 775,714,996
- Experimental weighted units: 151,131,944.0
- API-equivalent USD: 415.18516565
- Input 27,094; cache read 724,297,325; cache write 5m 46,046,002; cache write 1h 1,868,420; unclassified cache write 0; output 3,476,155
- HTML size: 11,455,042 bytes

This sample reports aggregate numeric metadata only; no labels or transcript
content were printed or saved.

## Complete and deferred

HTML, responsive/offline rendering, filters, tree sorting and collapse,
time controls, synchronized row/chart selection, accessible hover details,
anonymize display mode, `--no-labels`, provenance footer, and landscape print
CSS are implemented. Browser interaction is verified against the managed
PWMCP browser; no requests left the page. The BrowserLease `page.pdf`
feasibility spike and a PDF CLI route remain deferred, along with the optional
TUI. No screenshots were committed.

The HTML anonymize switch only changes visible labels; the report data retains
labels unless `--no-labels` is used.
