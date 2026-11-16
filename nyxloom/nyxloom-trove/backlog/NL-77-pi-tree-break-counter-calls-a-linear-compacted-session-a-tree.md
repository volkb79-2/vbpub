---
kind: backlog-entry
schema_version: 1
id: NL-77
title: "the pi adapter's tree-break counter compares parentId with the previous record and therefore calls a linear compacted session a tree"
status: fixed
type: "bugfix"
severity: "medium"
component: "session-extract"
provenance: "pi-extract-pi fix round 2 (review finding a), 2026-11-16: measured on the 26 real stored pi sessions on this machine, every one with a compaction fired the false positive"
filed_by: "Codex deepseek-v4.1-flash"
filed_date: "2026-11-16"
closed_date: "2026-11-16"
closed_reason: "Fixed on pi-extract-pi-20261116 in fix round 2: count_tree_breaks() replaces the previous-record comparison. A file is now reported only when it really diverges from one line -- a fork (one parent with two or more children, counted once at the parent, because one fork produces two children) or a parentId naming an entry the file does not contain. Evidence: tests/test_session_extract_pi.py::test_a_LINEAR_pi_session_is_not_reported_as_a_tree (a linear file interleaving compaction/context_edit entries reports 0) and ::test_a_tree_break_is_counted_not_implied (a real fork plus a dangling parent reports 2), plus tests/test_session_extract_r2_survivors.py's equivalent assertions which also kill the compare-swap mutant on that comparison."
---

## Problem

The pi adapter counts `parentId` "tree breaks" and prints them into the
primer's footer:

```
[pi: N record(s) whose parentId breaks the linear chain -- this extract
 follows FILE ORDER, not the parent tree]
```

The counter compared a message record's `parentId` with the PREVIOUS message
record's `id`. pi's own session files interleave non-message entries
(`compaction`, `context_edit`, `model_change`) into that same chain: the
compaction's `parentId` is the last message's id, and the NEXT message's
`parentId` is the COMPACTION's own id. Under the old comparison every one of
those transitions counted, so a perfectly LINEAR session printed "breaks the
linear chain" into a successor primer whose file was one straight line.

Measured before the fix, on the 26 real stored sessions in
`~/.pi/agent/sessions/` on this machine: every session containing a `compaction`
record (16 of them, several with two or three) reported 1-3 false breaks, while
the true tree signal -- a parent with more than one child -- was ZERO in all 26.
The line was therefore not merely noisy: it was wrong every time it appeared.

## Why it matters beyond the wording

A primer is read by a fresh agent as instructions. A false "this file is a
tree, so the extract flattened it" line tells that agent its own history is
possibly incomplete or reordered, which is exactly the kind of doubt that
makes a successor re-derive work it already had. A statement that is printed
into a document must be true or absent; silence about a linear file is the
correct answer.

## Fix

`count_tree_breaks()` counts the two shapes that actually make a session a
tree, and nothing else:

- a **fork**: one parent id with more than one child, counted ONCE (counting
  each child would report a single branch as two breaks);
- a **dangling parent**: a `parentId` naming an entry the file does not
  contain (the parent lives in another session file).

It no longer compares anything with the "previous" record, because file order
and tree order are different questions and the file is already read in file
order by design.
