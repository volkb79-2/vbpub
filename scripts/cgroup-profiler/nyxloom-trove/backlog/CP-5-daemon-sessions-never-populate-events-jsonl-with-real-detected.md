---
kind: backlog-entry
schema_version: 1
id: CP-5
title: "daemon sessions never populate events.jsonl with real detected events (limit drift, memory.high breaches, OOM kills, refault bursts) -- only the contract's own events counters are computed"
status: open
type: "bugfix"
severity: "low"
provenance: "RG-55 wave, cgprofile-P1-DAEMON C4/C5, 2026-09-12"
filed_date: "2026-09-12"
---

## Observed mechanism and reproduction

`lib/serve.py`'s `_create_session_locked` touches an empty `events.jsonl`
for every session (`with open(rundir.stream_path("events"), "a", ...): pass`)
and never appends to it again for the life of the session. The contract's
own `events` block in the Summary (§3: `oom_kill`, `limit_drift`,
`memory_high_breach`) IS computed correctly and incrementally — that part
is real, done in `lib/summary.py`'s `_count_limit_drift`/`_events_block`
from raw `memory.events.local`/`memory.max`/`memory.high` deltas across
samples, and covered by the golden fixture reproduction
(`summary-v1.json`'s `events` block). What is NOT done is a per-event
*record* on the same cadence `lib.events.Detector` (the `cgprofile run`/
`attach` collector's own detector, `lib/events.py`) produces for a
`cgprofile run` session: individual timestamped rows naming exactly which
sample tick crossed which threshold, the kind of thing a human skimming
`events.jsonl` or a future `ctl report` render would want to see plotted
as event markers (`lib/report_html.py` already knows how to draw event
markers from `events.jsonl` — see `TestFullLifecycleGoldenReproduction`'s
own module docstring and `lib/model.py`'s `Event`).

`stop`'s response already names `events.jsonl` in its `series` map
(RG55-INTERFACE-CONTRACT.md §2.5), so a consumer reading that file today
gets zero lines, always, for a daemon session — never wrong, but never
useful either.

## Why cgroup-profiler owns it

Entirely internal: `lib/serve.py`'s session server, `lib/events.py`'s
existing `Detector`/`DetectorConfig` (already used by `cmd_collect` for
`cgprofile run`/`attach`), no other project or consumer has any stake in
this. Deferred during RG-55 P1 C4 (`lib/serve.py`) rather than blocking
the C4 deliverable — the contract's OWN computed `events` counts (the part
run-gate actually reads) were never at risk; only the human-readable
per-event log was skipped.

## Proposed contract

Wire `lib.events.Detector` (or a session-scoped instance of it) into
`SessionServer._on_session_sample`, fed the same `prev`/`cur`/`dt` shape
`cmd_collect`'s own `on_sample` closure already builds it from (see
`cgprofile.py`'s `cmd_collect`), and append each returned `Event.to_dict()`
to `sess.rundir.append("events", ...)`. The detector's own config
(`DetectorConfig`, PSI/memory thresholds) would need either the daemon's
own sane defaults or a way to inherit whatever `serve`'s CLI already
exposes — worth deciding explicitly rather than silently reusing
`cmd_collect`'s own defaults verbatim, since a long-lived daemon session
has different tolerance for noise than a single gate run.

## Oracles

A fix should reproduce a controlled-wrong-implementation check: a session
whose target's `memory.high` changes mid-run (the same golden-fixture
frame sequence `tests/fixtures/contract/frames/` already encodes —
`RG55-INTERFACE-CONTRACT.md`'s own README documents a `memory.high`
change at frame 2→3) must produce AT LEAST one `events.jsonl` row naming
that transition, not just the contract summary's `limit_drift: 1` counter
— a wrong implementation that only increments the counter without ever
writing a row is exactly the bug this entry describes, and should be
red-first before the fix lands.

## SPEC ownership

`scripts/cgroup-profiler/lib/serve.py` (`_create_session_locked`,
`_on_session_sample`), `scripts/cgroup-profiler/lib/events.py` (the
existing `Detector`), `RG55-INTERFACE-CONTRACT.md` §2.5/§3 (the `events`
field this does NOT change — only the per-event log file does).

## Provenance

RG-55 wave, `cgprofile-P1-DAEMON` C4 (`lib/serve.py`) — deferred
explicitly by the controller's C5 dispatch: "events.jsonl populated with
real detected events... is deferred: file CP-5 via the backlog skill in
C9 (the summary's events counts are the contractual part and are
computed)." Filed in C9, 2026-09-12.
