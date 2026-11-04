---
kind: backlog-entry
schema_version: 1
id: CP-53
title: "tests/test_serve.py::test_accept_loop_survives_a_handler_bug_and_keeps_serving is flaky: its crash injection races between two connections"
status: fixed
type: "bugfix"
severity: "low"
component: "tests"
priority: 3
provenance: "cgp2 package 1 and 2 gate runs, 2026-11-04 (failed under load, passed on rerun)"
filed_by: "cgp2-pkg2"
filed_date: "2026-11-04"
---

## Observed mechanism

Since CP-33 each verb runs on its own thread. The test injects a handler crash with a shared `calls["n"] == 1` counter and then expects the
second connection to be served; when both connections reach the handler close together the counter is hit by the "wrong" one, so the crash
lands on the connection the test expects to succeed. The outcome depends on thread scheduling, so it differs between runs under load.

## Fix

Key the injection to the connection (for example the request payload or the peer socket), not to a call counter, so the test does not
depend on which thread reaches the handler first. Test-only change; no daemon change.

## Oracles

The test passes 200 consecutive runs under `taskset -c 0` and with a competing CPU burner.

## Updates

**2026-11-04** FIXED in a6b354d4a (cgp2 package 3, test-only): the crash is keyed on the request and the test waits for both handler threads.
