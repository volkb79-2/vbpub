---
kind: backlog-entry
schema_version: 1
id: CP-63
title: "Tests must scope process-wide os.* syscall fakes to the descriptor or path under test"
status: open
type: "bugfix"
severity: "medium"
component: "test-hygiene"
provenance: "2026-11-12 pytest 9.2 process-wide filesystem-mock audit"
filed_by: "Codex deepseek-v4.1-flash"
spec_owner: "tests/"
filed_date: "2026-11-12"
---

## Observed mechanism and reproduction

Pytest 9.2.0 calls `os.fstat` while managing capture and teardown. A test that
replaced the shared `os` module's `fstat`, `stat`, `lstat`, `open`, `fdopen`,
`read`, `write`, `close`, or `fsync` with an incomplete or raising fake for
every caller could therefore fail outside the code under test. The audit fixed
the cgprofile instances by matching the descriptor or path under test and
delegating every other call to the saved real function.

## Why cgprofile owns it

The affected tests exercise cgprofile's namespace inspection, service doctor,
DAMON identity checks, and resource-observer socket handling. The product code
is correct; the ownership is the project's test contract and its future
addition of global syscall fakes.

## Proposed contract

A process-wide patch of an `os.*` function is permitted only as a dispatcher
that (1) proves the fd/path is the one under test, (2) applies the fake only on
that match, and (3) delegates every other call to the real function. A
`builtins.open` or `io.open` patch follows the same rule. Tests must not rely
on a test runner's capture, teardown, logging, or temporary-directory
implementation avoiding the patched symbol.

## Oracles

- Every touched fake delegates an unrelated descriptor/path to the real call.
- The original refusal or short-I/O oracle still triggers on the target.
- A deliberate break that delegates only some unrelated calls fails in the
registered R0/R1 lane or a focused owning test.

## Updates

- 2026-11-12: the current cgprofile hazards were scoped to the tested
  descriptor/path in the audit branch; no production file changed.
