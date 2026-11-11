---
kind: backlog-entry
schema_version: 1
id: NL-64
title: "search needs a `--persisted-in PATHS` check that reports whether each hit cluster was written into repository or documentation artifacts"
status: open
type: "feature"
severity: "medium"
component: "session-extract"
provenance: "operator direction, 2026-11-10: answer 'did we persist this?' while comparing session findings with reports, docs, and the repository"
filed_by: "Codex deepseek-v4.1-flash"
spec_owner: "src/nyxloom/harness_search.py"
filed_date: "2026-11-11"
---

## Problem

Finding a term in session history does not answer whether the same durable fact
made it into a report, design document, changelog, backlog, or source tree. The
investigation currently needs a second grep shaped by knowledge of the hit
cluster, and that ad hoc pass can use different case, regex, and path rules from
the session search. There is no machine-readable persisted/unpersisted status
beside the hit itself.

## Evidence

The controller session record for 2026-11-10 records a manual pattern of
searching the session evidence and then separately searching the repository and
report directories to decide whether a finding had been persisted. The same
search had to cover parent records, excluded communication records, and
separate run logs, making a second shell command and walker easy to make
inconsistent with the first.

## Proposed minimal solution

Add a repeatable `--persisted-in PATH` option to search. It requires at least one
readable regular file or directory and fails closed if a supplied path cannot be
inspected.

For each hit cluster in the detailed `--view hits` record row, reuse the active
pattern grammar and case policy to scan the supplied artifacts, then emit:

- `persisted` when every query term or phrase is present in at least one artifact;
- `partial` when some but not all required terms are present;
- `missing` when none is present;
- `indeterminate` when a declared path could not be read sufficiently to answer.

Include the matching artifact path, byte offset or line when available, and a
bounded snippet for each matching artifact. Keep counts and output ordering
deterministic. `--json` exposes the same status as a stable value. A hit cluster
must be identified by its source family and record coordinates, not by a bare
query string, so two discussions with different persistence outcomes are not
collapsed together.

The pass must reuse one source reader and one path walker from search. It must
not invoke `grep` with caller-specific flags, shell-expand paths, or fall back to
the current home-owned transcript stores when `--persisted-in` is supplied.

## Oracles

- A fixture session has two distinct hit clusters; one exact fact appears in a
  report and one does not. The first reports `persisted`, the second `missing`,
  and both retain their own source coordinates.
- A cluster with one of two required terms in the repository reports `partial`
  with the present term and the absent term named.
- Regex, literal, case-insensitive, and case-sensitive behavior is identical to
  the search match that produced the cluster.
- An unreadable supplied path reports `indeterminate` and a non-zero command
  disposition according to the documented fail-closed policy.
- Supplying `--persisted-in` never scans an unlisted document directory.
- A controlled implementation that takes the first repository text match as
  proof for every cluster fails the two-distinct-cluster oracle.

## Where

- `nyxloom/src/nyxloom/harness_search.py`: shared pattern matcher and artifact
  scan/status classification.
- `nyxloom/src/nyxloom/cli.py`: repeatable option, validation, text/JSON render.
- `nyxloom/tests/test_harness_search.py` plus fixture reports and source files.
- `nyxloom/docs/CLI-REFERENCE.md` and `nyxloom/docs/CONSUMERS.md`: a pasteable
  "was this finding persisted?" example with the meaning of every status.

## Priority

Medium. It closes a common session-to-durable-record audit question and prevents
the criteria from drifting between the transcript search and repository search.

**Close when.** The persisted/partial/missing/indeterminate matrix, per-cluster
identity, shared matching semantics, matching paths and bounded snippets,
fail-closed path handling, deterministic output, and the unlisted-path negative
pass on a clean final commit; and the CLI and consumer docs define every status.
A recorded operator decision that this audit remains manual also closes the item
when it documents the exact commands, their source boundary, and a test for
the intended unsupported `--persisted-in` surface.

Related: [NL-62](NL-62-record-level-search-across-session-evidence.md) owns the
record-level clusters and source matrix that this option annotates.
