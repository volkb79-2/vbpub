---
kind: backlog-entry
schema_version: 1
id: NL-51
title: "Complete usage report renderers: PDF export and optional TUI"
status: open
type: "feature"
severity: "medium"
component: "session-extract"
context_estimate: "large"
provenance: "nyxloom usage-analysis plan, 2026-10-28; renderer work deferred after U3-U6"
spec_owner: "session_extract.usage renderers"
filed_date: "2026-10-28"
---

## Observed mechanism and reproduction

`nyxloom usage report` emits the frozen `usage-report-v1` ledger as a tree table, JSON, a flat call-ledger CSV, a private offline interactive HTML file, or a paginated PDF. C1 adds the versioned `usage-charts-v1` data extension and `nyxloom usage charts`. The optional TUI remains open. Chart data and interactive totals derive from the same exact call ledger and use self-cost partitions rather than overlapping subtree totals.

## Why Nyxloom owns it

The report's calls, source provenance, lineage, typed events, and self/subtree totals are built by `session_extract.usage`. A future renderer must consume that shared contract and preserve its exact numeric totals and privacy rules; a separate transcript parser would create competing attribution and de-duplication behavior.

## Proposed contract

The C1 chart-data contract is shipped as `usage-charts-v1`, derived from `usage-report-v1`. Stacked chart buckets partition deduplicated call self-cost; subtree totals remain display-only. Top-N selection and `other` membership are deterministic, and every plotted point retains its call or event identity. Future renderers must use the exact ledger for table arithmetic; visual decimation cannot change totals.

The offline HTML artifact is shipped. `--no-labels` removes free-text labels before serialization, embedded JSON is escaped, tooltip labels use text nodes, and a BrowserLease acceptance probe verifies interaction behavior and zero network requests. Treat labeled reports as sensitive local files and create outputs with private permissions.

`--format pdf` uses PWMCP's existing Python `BrowserLease` with generated HTML, a render-ready marker, print media, and `page.pdf()`. The optional `nyxloom[report]` extra supplies the PWMCP client; PDF output flows across arbitrary page counts, repeats the table header, preserves rows, and stays vector-text. Keep the Textual TUI as a later optional renderer over the same data contract.

## Oracles

- Synthetic ledger fixtures prove every grouped bucket sums exactly to its input self-cost, while parent subtree rows never overlap stacked descendants.
- Stable top-N and `other` membership, point identities, context peaks, and explicit event markers match golden chart data.
- The BrowserLease interaction probe proves exact brushed subtotals, hover identity, label suppression, synchronized isolation, and zero network requests.
- PDF checks prove signature, page count, searchable report title/window/totals, complete table rows across pages, chart titles, and sentinel-content absence.

## SPEC ownership

`nyxloom/docs/DESIGN-GUIDE.md` owns the chart-data and rendering rationale. `nyxloom/docs/CONSUMERS.md` owns the copyable HTML/PDF usage examples. The report renderer contract remains in `nyxloom/src/nyxloom/session_extract/README.md`; CLI grammar belongs in `nyxloom/docs/CLI-REFERENCE.md` after the verbs and options are selected.

## Updates

- 2026-10-28: C1 implements four chart datasets, deterministic top-N/other selection, self-cost conservation, packed swim lanes, stable call/node chart IDs, model/effort style keys, and context decimation that retains peaks and event neighbors. `usage-report-v1` continues to carry the exact call ledger and reserves chart output under `extensions.chart_data`. Synthetic tests and consumer/design docs were added. The controller owns the registered gate and final CLI semantic-case activation; no gate result is claimed here.
- 2026-10-28: H1+H2 add the private offline HTML report, exact filtered table totals, synchronized charts, browser acceptance probe, and print CSS. The user-facing README, design guide, consumer guide, session-extract guide, and CLI reference describe HTML use. The HTML format has a reviewed CLI-surface case; the controller owns registered gate execution, which has not run here.
- 2026-10-28: P1's BrowserLease/page.pdf spike generated a valid 24,085-byte, five-page PDF from a synthetic 120-row table. Nyxloom now has the optional `nyxloom[report]` renderer, `--format pdf`, repeated table headers, row-preserving page breaks, private binary output, and PDF contract tests using a pinned pure-Python reader. No gate was run; the controller owns registered test and coverage receipts.
- Remaining work: optional TUI ideas T0 Rich `--watch`, T1 Textual tree table, T2 context sparklines/event list, and T3 anchor editor/calibration. Re-evaluate them now that H2 is complete; they remain deferred and are not part of P1.
