---
kind: backlog-entry
schema_version: 1
id: NL-72
title: "`--redact-pattern` placeholder echoes the pattern into the extract it redacts, so a sensitive pattern leaks into the output"
status: fixed
type: "bugfix"
severity: "low"
component: "session-extract"
provenance: "controller review of the pi-readback-auto package, 2026-11-15 (CTX_READBACK_REDACT_PATTERNS added for hook-driven readbacks)"
filed_by: "controller"
filed_date: "2026-11-15"
closed_date: "2026-11-16"
closed_reason: "FIXED 2026-11-16 (pi-extract-pi-20261116): the placeholder now names the matched rule by its ORDINAL (#1, #2, ...) instead of echoing the regular expression, which could itself be the sensitive string the pattern was written to remove. Pinned by tests/test_session_extract_mangle.py (the ordinal placeholder and its monotonic counter) and tests/test_cli_extract.py's #1 assertion, so an extract can no longer reproduce any part of a pattern it redacted. NOTE: the entry was FILED on main (ac5c/1cbb627ae); the fix itself landed here, not on main."
---

## Problem

`session_extract/mangle.py::redact_paragraphs` replaces every paragraph that
matches a `--redact-pattern` with the one-line placeholder
`[redacted paragraph -- matched --redact-pattern '<pattern>']`, where
`<pattern>` is the regular expression itself (`!r`). The placeholder is meant
to tell the reader which rule fired. When the pattern is itself the sensitive
part (a token prefix, a host name, a customer name, a literal secret fragment
passed through `CTX_READBACK_REDACT_PATTERNS` by a hook), the redacted extract
re-publishes exactly the text the pattern was written to remove. The readback
hook feeds these patterns from an environment variable and writes the extract
into the next session's context, so the echo lands in model context and in
the stored readback file.

## Do

Name the rule, not the regex: number the patterns in the order given
(`matched --redact-pattern #2`), or accept an optional `name=regex` form and
print only the name, falling back to the ordinal. Keep the redacted-paragraph
count in the extract summary. Tests: a pattern containing a marker string
never appears in the extract output or in the summary; the ordinal is stable
when patterns are reordered only if names are given; existing placeholder
consumers (grep the readback tooling and the docs for `redacted paragraph`)
are updated in the same change.

## Acceptance

Extracting a fixture transcript with `--redact-pattern 'SECRETMARK-[0-9]+'`
yields placeholders that contain neither `SECRETMARK` nor the regex text, and
`CTX_READBACK_REDACT_PATTERNS` behaves the same way through the hook path.

**Priority.** Minor; no secret reaches a placeholder unless the pattern text is
itself secret, which is the case the readback hook makes easy.
