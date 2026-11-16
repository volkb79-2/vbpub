---
kind: backlog-entry
schema_version: 1
id: CP-65
title: "Live cgprofile docs still describe the removed `cgprofile ctl version` verb, and the CP-49 removed-name guard greps the two-word form so a bare `ctl version` escapes it"
status: open
type: "bugfix"
severity: "low"
component: "docs"
context_estimate: "small"
provenance: "remote gate worker report 2026-11-16 (sonnet-v1001-gateworker-REPORT.md, 'Findings for tool backlogs': 'r1002 RUNBOOK and task briefs still cite `cgprofile ctl version --json`; with 3.0.0 the verbs are cgprofilectl status|host|doctor. Verify the repo guide text is updated'); re-checked against origin/main on 2026-11-16"
filed_by: "vbpub maintenance"
spec_owner: "docs/CGPCTL-SPEC.md, docs/PROTOCOL.md section 10"
priority: 3
filed_date: "2026-11-16"
---

## Problem

cgprofile 2.0.0 replaced the `cgprofile` command with `cgprofilectl` (client)
and `cgprofiled` (daemon), and CP-49 swept the estate documents for the removed
1.x names. One live document still instructs an operator to read daemon state
through the removed verb, and the guard that is supposed to prevent exactly
this cannot see the shape it appears in.

1. **A live document still names the removed verb.**
   `cgprofile/docs/DESIGN-GUIDE.md:516` and `:548` both explain DAMON
   availability in terms of "the `damon` field in `ctl version`" and
   "neither `ctl version` reporting `available` nor a request ...". With the
   shipped 3.0.0 CLI there is no `ctl` subcommand at all: the verbs are
   `cgprofilectl status|host|doctor|start|stop|watch|prune|config|sessions|show`,
   and the daemon's version/capability facts are read from
   `cgprofilectl status --json` (contract 2). A reader who follows the
   DESIGN-GUIDE text gets "unknown command" rather than the DAMON answer,
   which is the same class of wrong instruction the estate rule about
   prescribed remedies names ("an error message that prescribes a fix must
   prescribe a correct one").
2. **The removal guard cannot catch the bare form.**
   `cgprofile/tests/test_docs_sync.py`'s `REMOVED` tuple (around line 325) lists
   the two-word forms — `"cgprofile ctl"` and
   `"docker exec cgprofile-host-daemon"` — and `removed_name_hits()` does a
   plain substring search. `DESIGN-GUIDE.md` writes the verb as "`ctl version`"
   with no `cgprofile` prefix, so the file is in `LIVE_DOCS` (line 335) and
   still passes the no-removed-name test. The guard therefore certifies
   "no live document names a removed command" while a live document names one:
   the check's message is stronger than its comparison (a check is only as
   strong as what it actually compares).

Both are disclosure/documentation facts, not behaviour: the daemon, the client
and Run-Gate are unaffected, which is why this is low severity rather than a
release blocker.

## Why cgprofile owns it

`docs/DESIGN-GUIDE.md` is this project's own rationale document, and
`tests/test_docs_sync.py` is this project's own docs-drift guard. The estate's
other copies of the removed verb (`run-gate-project/run_gate.py`'s warning
strings and its `KNOWN_ISSUES_TODO_BACKLOG.md`/`CHANGES.md` history,
`cgprofile/docs/CONSUMERS.md`'s 1.x→2.0 migration TABLE, and the dated
reports under `cgprofile/nyxloom-trove/reports/`) are either historical
records or deliberate migration rows and are out of scope here; only the
unfrozen live prose is.

One document is explicitly OUT of scope: `docs/RG55-INTERFACE-CONTRACT.md` is
FROZEN 2026-09-12 by a controller ruling ("nobody edits this file unilaterally")
and a byte-identical copy lives in `run-gate-project/nyxloom-trove/`. Its
section headings `2.1 cgprofile ctl version --json`, `2.3 cgprofile ctl status`,
`2.4 cgprofile ctl host` are the frozen contract-1 verb names, and changing them
is a numbered controller ruling, not a docs sweep. Any fix here must state that,
so a future sweep does not "discover" the frozen file and edit it.

## Proposed fix

- Rewrite the two `DESIGN-GUIDE.md` passages in the shipped vocabulary: the
  capability question is answered by `cgprofilectl status --json` (the field is
  the daemon's own `daemon.damon` value), and the client/daemon contract facts
  are `cgprofilectl status`'s `contracts`/`skew` fields. Keep the substantive
  point (that `damon: available` describes interface availability, not a
  committed kdamond) unchanged; only the command name changes.
- Extend the guard so the bare form is detectable WITHOUT re-admitting the
  migration table: either add the `"ctl version"`/`"ctl start"`/`"ctl host"`
  family to `REMOVED` and exempt the `## Migration table: 1.x command to
  cgprofilectl verb` section the same way `_consumers_without_migration_table()`
  already exempts it for CONSUMERS.md, or add a separate rule that a live doc
  outside the migration table must not contain "`ctl `" followed by a
  contract-1 verb name. Whichever is chosen, the guard must go RED on the
  current DESIGN-GUIDE text BEFORE the rewording (a red-first proof), and must
  stay green on the frozen contract copy and on the migration table.
- Do not touch the frozen `RG55-INTERFACE-CONTRACT.md` or its byte-identical
  copy; if those headings should change, that is a controller ruling.

## Where

- `cgprofile/docs/DESIGN-GUIDE.md` (the two "DAMON availability is not session
  readiness" passages, around lines 516 and 548).
- `cgprofile/tests/test_docs_sync.py` (`REMOVED`, `LIVE_DOCS`,
  `_live_texts`/`removed_name_hits`, and the mirror test).
- Cross-reference: `cgprofile/docs/CGPROFILECTL-SPEC.md` (the shipped verb
  surface) and `cgprofile/docs/PROTOCOL.md` section 10 (ctl contract 2), so the
  replacement text names a verb that actually produces the fact.

## Acceptance test idea

`removed_name_hits()` returns a hit for the pre-fix DESIGN-GUIDE text
(`ctl version`), and the test is proven red before the rewording. After the
rewording the same test is green, the migration table's 1.x rows are still
exempt, the frozen interface contract still contains its contract-1 headings
and is never flagged, and `cgprofilectl status --json` is the command named in
the guide. A hand mutation that removes the new detection rule from the guard
makes its focused case fail.

## Priority

3 — documentation and a guard gap; no verdict, no measurement and no daemon
behaviour changes. Raise it if an operator-facing procedure (a host runbook or
a task brief) keeps being written from the DESIGN-GUIDE text: the observed
instance was a host agent repeatedly trying `cgprofile ctl version --json`
against a 3.0.0 daemon before switching to `cgprofilectl status --json`.

## Close when

The live DESIGN-GUIDE names only shipped verbs, the docs guard detects the bare
`ctl <verb>` form in a live document (proven red-first and green after),
the frozen interface contract and the migration table are untouched and
explicitly documented as out of scope, and the docs-sync test mirrors the
reasoning in its own comments so a future sweep does not re-open it.
