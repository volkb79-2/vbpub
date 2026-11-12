---
kind: backlog-entry
schema_version: 1
id: NL-66
title: "orphaned controller gate-run wrappers leave only indirect re-attach evidence"
status: open
type: "bugfix"
severity: "medium"
component: "controller"
provenance: "ctl-hardening2 operator finding, 2026-11-12: a dead wrapper left a run-gate container and its collection log lacked the controller's run-gate.commit label"
filed_by: "Codex deepseek-v4.1-flash"
spec_owner: "scripts/controller/job-check.sh"
filed_date: "2026-11-12"
---

## Problem

The controller kit requires a gate log to contain the full current
`run-gate.commit` label. When a controller wrapper dies after starting
`run-gate`, the container can survive and a later invocation collects it under
the line `run-gate: collected <container>`. That later log does not repeat the
container-start label, so the ordinary evidence check rejects a PASS unless it
reconstructs provenance from another log.

The kit now accepts that reconstruction conservatively: the collected
container must contain the current eight-character head prefix, an earlier log
must carry the full current commit label on a line naming the same container,
and a supplied report must name the container. This closes the immediate
evidence gap, but it is indirect evidence and still requires the operator to
retain an earlier log.

## Evidence

The finding was observed twice before the ctl-hardening2 change. A re-run
printed `re-attach — no new container was started` and a later collection
printed the container name, but the run log lacked the `run-gate.commit` label
that `scripts/controller/job-check.sh` requires. The new bounded fallback and
its positive and negative fixtures are in
`scripts/controller/tests/test-job-check.sh`.

## Proposed minimal solution

Make the controller's gate wrapper append its own stable evidence preamble to
the destination log before starting `run-gate`: the full intended commit, cwd,
argv, wrapper PID, and a durable invocation identifier. Have the wrapper
record the container name after run-gate selects it, or tee the run-gate
output to that same log so the creation and collection records cannot live in
different files. Add cleanup of a wrapper-owned container when the wrapper is
terminated before collection.

The exact mechanism must respect Run-Gate's ownership rules: a live owner is
followed, never removed, and `--fresh` remains the explicit escape for an
unowned inflight record. The wrapper and `job-check.sh` should share one
mechanical evidence format rather than parse free-form text twice.

## Oracles

- A wrapper killed after container creation leaves one wrapper log naming the
  full commit and the exact container, and a later re-attach uses that same
  direct evidence.
- A wrapper log for a different commit or a different container is rejected.
- A live owner is never removed by the dead-wrapper cleanup path.
- A clean wrapper termination exits `run-gate` collection or removal and
  leaves no inflight container.
- The fallback introduced by ctl-hardening2 remains available for historical
  logs, but new wrapper runs are bound by the direct preamble.

## Where

- `scripts/controller/gate-run.sh`: stable log preamble and wrapper identity.
- `scripts/controller/job-check.sh`: one shared evidence reader for direct and
  historical formats.
- `scripts/controller/tests/test-hardening-tools.sh` and
  `scripts/controller/tests/test-job-check.sh`: killed-wrapper, re-attach,
  wrong-container and live-owner fixtures.
- `scripts/controller/README.md`, `docs/DESIGN-GUIDE.md`, and
  `docs/CONSUMERS.md`: direct evidence format and recovery procedure.

## Priority

Medium. The conservative fallback prevents a false PASS today, while the
direct wrapper evidence would remove the extra-log dependency and make orphan
cleanup mechanical.

**Close when.** New controller wrapper runs carry direct full-head and
container identity in one log, their killed-wrapper cleanup is tested without
removing a live owner's container, and the historical fallback remains
regression-tested on a clean final commit.
