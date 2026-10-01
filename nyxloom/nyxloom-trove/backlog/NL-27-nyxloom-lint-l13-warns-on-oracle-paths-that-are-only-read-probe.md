---
kind: backlog-entry
schema_version: 1
id: NL-27
title: "nyxloom lint L13 warns on oracle paths that are only read, probed, forbidden or untracked decoys (no way to declare read-only intent)"
status: open
type: "feature"
severity: "low"
component: "nyxloom-lint"
provenance: "dstdns 2026-09-30 P224 carve (earlier: P143)"
filed_date: "2026-09-30"
---

## Observed mechanism and reproduction

`_check_l13` (`src/nyxloom/lint.py` l.~1167-1232) extracts every path-shaped token from each oracle's `observable`, `negative` and `gate` text (`_extract_candidate_paths`) and warns when one is not matched by a `scope.touch` glob. It cannot tell a path the implementer must EDIT from a path the oracle only reads (a fixture or source file asserted against), probes (a `test -e` / `git diff --exit-code` check), or names as forbidden ("the diff must not touch X"), nor an intentionally untracked decoy path used as a negative control. All of those are correct NOT to be in scope.touch (a forbidden path in scope.touch would be self-contradictory).

Observed: dstdns P224's carve produced 7 L13 warnings, all false positives. Earlier instance: `dstdns/nyxloom-trove/reviews/dstdns-P143-carve-review-r1.md` l.27 records two L13 "read-only-reference" warnings ("correct, they are references not edits"). Because `effects_carver.py` (l.~584-592) treats L13 as BLOCKING for carver proposal admission, the noise is not merely cosmetic there.

## Why nyxloom owns it

The heuristic is nyxloom's; only the rule can know the author's intent, and only an annotation convention owned by the handoff schema can carry it. Consumers either learn to ignore L13 (defeating its real purpose, B22: catching an oracle unsatisfiable within scope.touch) or contort the oracle text.

## Proposed contract

Let the author mark the intent of a path reference so L13 checks only touch-intent ones. Options: (a) an inline annotation in oracle text such as `path (read)`, `path (probe)` or a backtick form like `` `ro:path` ``; (b) an optional oracle frontmatter field `reads: [globs]` whose paths are exempt from L13; (c) auto-exempt any path that matches a `scope.forbid` glob (a forbidden path can never need to be in touch). Do (c) unconditionally; it removes the forbidden-path class with zero authoring cost. Keep the warning for unannotated, unforbidden paths.

## Oracles

- An oracle naming a path that matches `scope.forbid` produces no L13 warning.
- An oracle path annotated read-only produces none; the same path unannotated still warns.
- A path outside touch with no annotation and no forbid match still warns (B22 preserved).
- A controlled wrong implementation that suppresses L13 for every path must fail the last oracle.

**Found in:** dstdns 2026-09-30, P224 carve (7 false-positive L13 warnings); earlier P143.
