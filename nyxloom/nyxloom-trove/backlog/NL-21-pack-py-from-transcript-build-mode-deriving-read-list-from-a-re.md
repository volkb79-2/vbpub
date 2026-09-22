---
kind: backlog-entry
schema_version: 1
id: NL-21
title: "pack.py: --from-transcript build mode deriving read-list from a recon-agent's real readset"
status: open
type: "feature"
severity: "medium"
provenance: "dstdns controller session, 2026-09-22, operator brainstorm; see design-context-lifecycle-experiments.md E-018"
filed_date: "2026-09-22"
---

## Observed mechanism

`pack.py`'s read-list today is derived from a fixed set of mechanical rules
against handoff structure (scope.touch -> full file, "Context to read first"
-> slice, decisions.md D-refs -> D-section slice, gate-adjacent artifacts ->
always-include). Documented cost problem: this rule set has needed five prior
extensions (E-002 addenda 4-7) and still has no size ceiling -- one real
package's protected core measured ~660k tokens before any --extra additions,
substantially from decisions.md's unbounded growth and scope.touch-full-file
having no cap.

## Proposed contract

A new `pack.py build --from-transcript <jsonl> --role implementer` mode: run
(a Grep/Glob-extended) `jsonl-metrics.py compute_readset()` against a REAL
session transcript instead of deriving the read-list from handoff-structure
heuristics, and assemble the pack from that readset's files. Intended input
is a cheap "recon" agent (e.g. Haiku) dispatched with the handoff and told to
explore-only (no edits) until "ready to implement," then stop -- its own
dispatched-subagent transcript already persists independently (E-015) and is
directly readable with no new capture mechanism.

## Why nyxloom should own this decision

Same reasoning as NL-19: this is a generic curation-mechanism question for
any nyxloom-registered project doing carve->dispatch, not a dstdns-specific
one, and `pack.py` already lives here.

## Prerequisite

`jsonl-metrics.py compute_readset()` needs Grep/Glob tool_use visibility
first (currently blind to native Grep/Glob, only sees Read + a Bash-command
allowlist) -- named in NL-19, doubly motivated by this entry.

## Behavioral oracles this would need

- A controlled comparison: on a matched pair of similar-complexity packages,
  compare a heuristic-derived pack against a recon-transcript-derived pack
  via `pack.py score` (documented limitations apply, non-authoritative
  alone) PLUS an outcome measure -- does the real implementer's own
  subsequent Read/Grep footprint on already-packed files shrink, per
  `jsonl-metrics.py curve`/`cmd_boundaries`.
- A genuine negative result (recon trace dominated by cheap-model noise, or
  missing files a heuristic reliably catches like scope.touch) must be
  recorded as such, not silently dropped.

## Spec section that owns this behavior

None yet -- see E-018's own "Status" for the current blocker: dstdns pack
orientation is presently PAUSED for live dispatches pending exactly this
kind of improved mechanism (or a decision not to pursue one).

## Full writeup

`vbpub/nyxloom/docs/design-context-lifecycle-experiments.md` E-018 (protocol,
hypothesis, risks, oracle in full). This entry is the feature-tracking
pointer; the experiment-log entry is the design record.
