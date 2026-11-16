---
kind: backlog-entry
schema_version: 1
id: NL-78
title: "a pi `custom` message whose content carries no text renders nothing and is not counted, so extension context can disappear from an extract"
status: fixed
type: "bugfix"
severity: "medium"
component: "session-extract"
provenance: "pi-extract-pi fix round 2 (review finding c): found while writing the R2 survivor assertions for the same function"
filed_by: "Codex deepseek-v4.1-flash"
filed_date: "2026-11-16"
closed_date: "2026-11-16"
closed_reason: "Fixed on pi-extract-pi-20261116 in fix round 2: the `custom` message role's empty-text branch now bumps report.blocks as `custom (no text)`, exactly as the `custom_message` RECORD's empty branch already did, so extension context pi really injected is counted instead of silently missing. Evidence: tests/test_session_extract_pi.py::test_a_custom_message_carries_a_STRING_content_and_an_unlisted_source (asserts the count) and tests/test_session_extract_r2_survivors.py::test_pi_a_custom_message_with_no_text_is_counted."
---

## Problem

pi converts a `custom` MESSAGE (and a `custom_message` RECORD) into a user
message (`dist/core/messages.js`), so both are content the MODEL SAW. The
adapter renders the text of each, and for the RECORD's empty case it counted
the skip as `custom_message (no text)`.

The MESSAGE role's empty case returned `[]` with no count at all. An
extension-injected message with no text (an image-only note, or a message
whose content is a shape this text extractor cannot read) therefore
disappeared from both the event stream AND the `[pi skipped ...]` disclosure,
which is precisely the rule fix round 1 established for every other shape:
nothing the file contains is dropped silently.

The two shapes had drifted apart by accident, not by design -- the record
branch documented its count and the message branch simply did not have one.

## Fix

The empty branch bumps `report.blocks` as `custom (no text)`. The spelling is
deliberately different from the record's `custom_message (no text)` so a reader
of the footer can tell WHICH shape was skipped; both are deliberate skips
(a text extract cannot carry them) and both are now visible.
