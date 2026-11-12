---
kind: backlog-entry
schema_version: 1
id: NL-68
title: "Tests must scope process-wide os.* syscall fakes to the descriptor or path under test"
status: fixed
type: "bugfix"
severity: "medium"
component: "test-hygiene"
provenance: "2026-11-12 pytest 9.2 process-wide filesystem-mock audit"
filed_by: "Codex deepseek-v4.1-flash"
spec_owner: "tests/"
filed_date: "2026-11-12"
closed_date: "2026-11-12"
closed_reason: "The observed Nyxloom filesystem fake now delegates unrelated descriptors; the deliberate break was rejected by the oracle and the focused file passed."
---

## Observed mechanism and reproduction

Pytest 9.2.0 calls `os.fstat` during capture and teardown. The usage-loader
coverage test replaced the shared `os.fstat` with a fake that changed its
answer after the first call; unrelated descriptors then received that altered
answer, so the test depended on Pytest's private filesystem usage. It now
delegates every descriptor except the scanner's target snapshot.

## Why nyxloom owns it

The usage loader owns the snapshot-change and read-error oracle. The test fake
belongs to that contract and must not observe or constrain Pytest internals.

## Proposed contract

A process-wide patch of an `os.*` function is permitted only as a dispatcher
that proves the fd/path is the one under test, applies the fake only on that
match, and delegates every other call to the real function. The same rule
applies to `builtins.open` and `io.open`. Tests must remain valid when the
runner opens, stats, reads, or closes unrelated files during capture and
teardown.

## Oracles

- An unrelated fd passed through the fake reaches the real `fstat`.
- The target snapshot still produces the truncate/read-error oracle.
- A deliberate fake that alters the first unrelated descriptor causes the
  focused or registered test to fail.

## Updates

- 2026-11-12: the observed usage-loader hazard is fixed in the audit branch;
  no production file changed.
