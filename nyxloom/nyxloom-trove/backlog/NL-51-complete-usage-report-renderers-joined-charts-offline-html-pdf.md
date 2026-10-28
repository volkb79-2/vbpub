---
kind: backlog-entry
schema_version: 1
id: NL-51
title: "Complete usage report renderers: joined charts, offline HTML, PDF, and optional TUI"
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

`nyxloom usage report` currently emits the frozen `usage-report-v1` ledger as a tree table, JSON, or a flat call-ledger CSV. It has no joined chart datasets, offline interactive HTML renderer, or PDF export. The implementation and adoption guide explicitly defer these renderers after the weights, pricing, reset-window, and anchor-calibration work in the 2026-10-28 usage-analysis iteration.

## Why Nyxloom owns it

The report's calls, source provenance, lineage, typed events, and self/subtree totals are built by `session_extract.usage`. A future renderer must consume that shared contract and preserve its exact numeric totals and privacy rules; a separate transcript parser would create competing attribution and de-duplication behavior.

## Proposed contract

Add chart data as a derived view over `usage-report-v1`. Stacked chart buckets partition deduplicated call self-cost; subtree totals remain display-only. Top-N selection and `other` membership are deterministic, and every plotted point retains its call or event identity. Brushing and filtering use the exact ledger for table arithmetic; visual decimation cannot change totals.

Render the report as one offline HTML artifact. `--no-labels` removes free-text labels before serialization, embedded JSON is escaped, tooltip labels use text nodes, and a browser test verifies that the page makes no network requests. Treat labeled reports as sensitive local files and create outputs with private permissions.

Before adding `--format pdf`, test the existing pwmcp Python `BrowserLease` route with generated HTML, a render-ready marker, print media, and `page.pdf()`. The PDF must paginate arbitrary trees without dropping rows. If that route fails, choose and document a supported offline renderer before implementation. Keep the Textual TUI as a later optional renderer over the same data contract.

## Oracles

- Synthetic ledger fixtures prove every grouped bucket sums exactly to its input self-cost, while parent subtree rows never overlap stacked descendants.
- Stable top-N and `other` membership, point identities, context peaks, and explicit event markers match golden chart data.
- Browser interaction tests prove exact brushed subtotals, hover identity, label suppression, and zero network requests.
- PDF checks prove signature, searchable report title/window/totals, complete table rows across pages, and sentinel-content absence.

## SPEC ownership

`nyxloom/docs/DESIGN-GUIDE.md` owns the chart-data and rendering rationale. `nyxloom/docs/CONSUMERS.md` owns the copyable HTML/PDF usage examples. The report renderer contract remains in `nyxloom/src/nyxloom/session_extract/README.md`; CLI grammar belongs in `nyxloom/docs/CLI-REFERENCE.md` after the verbs and options are selected.

## Updates
