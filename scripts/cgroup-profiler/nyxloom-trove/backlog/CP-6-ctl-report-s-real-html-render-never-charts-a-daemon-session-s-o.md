---
kind: backlog-entry
schema_version: 1
id: CP-6
title: "ctl report's real HTML render never charts a daemon session's own DAMON hot/warm/cold/idle series -- analyze.py has no damon.jsonl reader at all"
status: open
type: "feature"
severity: "low"
provenance: "RG-55 wave, cgprofile-P1-DAEMON C5 (RW-14), 2026-09-12"
filed_date: "2026-09-12"
---

## Observed mechanism and reproduction

`ctl report`'s render (RW-14, C5) now produces the REAL interactive HTML
report (`lib.analyze.build` + `lib.report_html.render`) for a daemon
session directory — verified live:
`tests/test_serve.py::TestHandleReportRealRender::test_renders_a_real_interactive_html_report`
asserts a genuine plotly figure (`cgp-figure` div, `Plotly.newPlot` call)
rather than the old hand-rolled stub. But `lib.analyze.build` reads only
`samples.jsonl`, `marks.jsonl` and `events.jsonl` from the run directory —
it has no code path that touches `damon.jsonl` at all (confirmed: no
reference to "damon" anywhere in `lib/analyze.py`). The daemon's own
DAMON hot/warm/cold/idle classified-byte series (C3, `lib/damon.py`
`KdamondPool`/`DamonSession`, threaded into `SummaryAccumulator` in C4) is
fully present in the Summary's own `damon` block (contract §3) and in
`damon.jsonl` on disk, but never appears as a chartable series in the
rendered report — a session run with `--damon on` gets a report
indistinguishable, chart-wise, from one run with `--damon off`.

## Why cgroup-profiler owns it

`lib/analyze.py`/`lib/report_html.py` are this project's own pre-existing
report tier (predates RG-55 entirely); DAMON classification is this
project's own C3 addition. No other project has any stake in whether the
interactive report charts it.

## Proposed contract

Not designed here. Sketch worth considering: `analyze.build` would need a
fourth reader (`damon.jsonl`) alongside the three it already has, folded
into `Analysis` as either a new field or four additional `Series` entries
(hot/warm/cold/idle bytes) keyed to the same target the container's own
memory series already uses — `_GAUGES`/`_RATES` in `analyze.py` is the
existing registry pattern a DAMON entry would extend. `report_html.py`'s
panel-group/colour-assignment machinery (`build_figure`) would then need
to know about a "damon" panel group. This is real work, not a small patch
— worth scoping as its own package/checkpoint rather than folded into
whatever picks this entry up casually.

## Oracles

A fix should be verified against a session built from the same
`tests/fixtures/contract/frames/` fixture set this project already uses
for golden reproduction (`damon.json` per frame is already part of that
fixture — see `RG55-INTERFACE-CONTRACT.md` fixtures/rg55/README.md's own
DAMON table), asserting the rendered HTML's embedded plotly figure JSON
contains a trace for at least the "hot" class with the exact byte values
that table documents — not just that DAMON data was "read" internally.

## SPEC ownership

`scripts/cgroup-profiler/lib/analyze.py` (`to_frame`, `build`),
`scripts/cgroup-profiler/lib/report_html.py` (`build_figure`), DESIGN.md
§4.9/§4.11 (the existing report-tier module contracts this would extend).

## Provenance

RG-55 wave, `cgprofile-P1-DAEMON` C5 (RW-14: `ctl report` renders the real
report tier) — discovered while verifying the real render actually charts
what a `--damon on` session collected; not part of RW-14's own scope
(which was strictly "make the daemon write a compatible run directory and
invoke the existing report tier," not "extend the report tier to a new
data source"). Filed in C9, 2026-09-12.
