# Nyxloom usage analysis — iteration 4 report

Date: 2026-10-28

Result: **IMPLEMENTATION COMPLETE; controller gate pending.**

## Outcome

`nyxloom usage report --format html --out FILE` now renders the frozen
`usage-report-v1` and `usage-charts-v1` contracts as one offline HTML file.
The interactive table uses the exact call ledger; joined charts use the
versioned bucket and point identities. `--no-labels` removes topics before
serialization. HTML output remains private (mode `0600`). The existing price
provenance, account-limit diagnostics, and local-share meanings remain
separate.

## Evidence

| Oracle | Result |
| --- | --- |
| Python AST parse of seven changed Python files | PASS |
| Inline JavaScript syntax (`node --check`) | PASS |
| Static scan of an in-memory 24-hour, no-label HTML report for external URLs, request APIs, `eval`, and external asset attributes | PASS |
| `git diff --check` | PASS |
| Synthetic HTML structure, contract, CLI privacy, and sentinel tests | Authored; not run |
| BrowserLease interaction probe | Authored; not run |
| Nyxloom tester-unified tests and branch coverage | Not run; controller-owned per assignment |

No test or coverage result is claimed. The declared gate must still establish
the project result.

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
CSS are implemented. The BrowserLease/page.pdf feasibility spike and a PDF CLI
route remain deferred, along with the optional TUI. No screenshots were
committed.

The HTML anonymize switch only changes visible labels; the report data retains
labels unless `--no-labels` is used. Browser interaction behavior remains
unverified until the controller runs the probe and registered gate.
