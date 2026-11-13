---
kind: backlog-entry
schema_version: 1
id: NL-57
title: "`session-extract --since` accepts only a record marker; add ISO timestamps and relative durations such as `5h`"
status: fixed
type: "feature"
severity: "medium"
component: "session-extract"
provenance: "controller session record 2026-11-10; backlog filing for the operator"
filed_date: "2026-11-10"
closed_date: "2026-11-11"
closed_reason: "Delivered on nyx-readback-20261111 in commits 7f8dfcd01 and dc65ad159 with ISO/relative time grammar, strict/inclusive boundary behavior, malformed/overflow refusal, resolved-cutoff disclosure, docs, and focused tests; final-head authoritative gate receipts are in nyxloom/nyxloom-trove/reports/nyx-extract-readback-REPORT.md."
---

## Problem

`extract --since` is an opaque session marker: Claude event/record ids, Codex ordinals, OpenCode message ids, or Reasonix line markers. It cannot answer the ordinary controller question "show me the last five hours" without first extracting a marker from another command or knowing each adapter's internal record vocabulary. `--max-time-minutes` bounds a backward walk from the newest event; it is not the same as an absolute or relative lower bound and cannot express "since 2026-11-10T08:00:00Z".

## Evidence

The controller session record 2026-11-10 records repeated use of timestamp-based reasoning for controller readback: a 5-hour interval contained 31 KB / 7.7k tokens of agent notifications and a 5.5-hour controller session was about 200 KB / 50k tokens with everything retained. Finding the start of those intervals required record-specific marker discovery rather than a user-facing time selector.

## Proposed minimal solution

Extend the existing `--since` value grammar without changing the marker path:

1. an ISO 8601 timestamp with an explicit offset (for example `2026-11-10T08:00:00Z`) means "events with timestamp strictly after this instant";
2. a relative duration in the existing compact duration style (at least `5h`, plus `s`, `m`, `d`) means "events with timestamp at or after invocation time minus that duration";
3. every existing adapter marker remains accepted byte-for-byte.

Resolve a relative value once, at command start, to one UTC instant and disclose the resolved instant in the output footer. Apply the cutoff uniformly to normalized events; do not reinterpret adapter ids as timestamps. Keep `--until` marker-only in this item unless the same time resolver can be shared without enlarging the command contract. An activity window that contains no timestamped events is an empty result, not a fallback to the newest epoch.

## Where

- `nyxloom/src/nyxloom/cli.py`: `_resolve_since_marker`, `_extract_config_from_args`, and `cmd_extract`.
- `nyxloom/src/nyxloom/session_extract/config.py`: the `since_marker` input contract.
- `nyxloom/src/nyxloom/session_extract/select.py`: lower-bound filtering and span selection.
- Adapter parsers under `nyxloom/src/nyxloom/session_extract/adapters/` consume normalized timestamps.
- `nyxloom/tests/test_cli_extract.py` and adapter-specific extract tests.

## Acceptance test idea

Build synthetic Claude, Codex, and OpenCode logs with fixed timestamps around a boundary. Inject a fixed "now" and assert: an ISO boundary includes only later events, `5h` resolves to exactly five hours before the injected now, and a legacy record marker still selects its old span. A malformed timestamp/duration refuses before extraction and names the accepted grammar. Re-applying the old marker-only resolver makes the ISO and relative cases fail.

**Close when.** The ISO, relative-duration, legacy-marker, malformed-input, and empty-window cases in the acceptance contract pass on a clean final commit, and the user docs name the accepted grammar and the disclosed resolved cutoff. A recorded operator decision that rejects the expanded time grammar and documents marker-only selection as the intended contract also closes the item, provided the refusal/help text and tests encode that decision.

Related: [NL-48](NL-48-nyxloom-harness-search-results-do-not-group-subagents-show-snip.md) already records that extract windows cannot be selected by calendar date. That item owns the search-result-to-extract handoff; NL-57 owns the general `--since` time grammar.

## Priority

Medium. It removes a repeated controller-side discovery step across every adapter and is additive to the existing marker contract.

## Updates

**2026-11-11** — 2026-11-11: Implemented in commits 7f8dfcd01 and dc65ad159: --since now accepts timezone-qualified ISO-8601 and positive s/m/h/d durations while retaining adapter markers; relative cutoffs resolve once and are disclosed, ISO uses strict-after, relative is inclusive, malformed values refuse, and undated events cannot pass a time cutoff. Docs and focused tests are in the feature change. Authoritative final-head gates are recorded in the external delivery report.
