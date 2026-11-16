---
kind: backlog-entry
schema_version: 1
id: NL-76
title: "scripts/controller/pi-impl.sh's classify_run() can drift from the extractor's pi stop-state classifier, and nothing on the shell side would notice"
status: open
type: "bugfix"
severity: "medium"
component: "session-extract"
provenance: "pi-extract-pi fix round 1 (review finding 5), 2026-11-16: the parity fixture and its Python-side test were added, but the shell consumer is still untested"
filed_by: "Codex deepseek-v4.1-flash"
filed_date: "2026-11-16"
---

## Problem

Two independent consumers decide how a pi run ENDED, from the same records:

- `nyxloom/src/nyxloom/session_extract/stopstate.py` (`read_pi_facts` +
  `_pi_stop_state`), used by `nyxloom-harness extract --stop-state` and by the
  readback/successor presets' ledgers.
- `scripts/controller/pi-impl.sh` (`inspect_run()` and `classify_run()`), used
  by the controller to decide whether a dispatched pi run succeeded, hit the
  context limit, or returned nothing and must be retried.

`pi-impl.sh`'s predicate was pinned as DATA in
`nyxloom/tests/fixtures/pi_stop_parity.json` and
`tests/test_session_extract_pi.py::test_the_python_and_shell_consumers_agree_on_how_a_pi_run_ended`
feeds the same synthetic records through the Python classifier and asserts the
two agree, case by case. That test is a snapshot of the shell predicate, not a
check OF it: it cannot go red when `pi-impl.sh` changes, because the shell file
is never executed. If the controller's predicate later gains a case (say a new
`stopReason`, or a different reading of a thinking-only ending), the fixture
will keep asserting the OLD predicate and both sides will look correct while
disagreeing.

The two divergences that already existed and were fixed on the Python side
(`EMPTY-COMPLETION` meaning "no text and no tool call, thinking-only counts as
empty", and `length` meaning `CONTEXT-FULL`) show the drift is real, not
theoretical.

## Wanted

A test that actually EXECUTES the shell side. `classify_run()` reads its facts
on stdin as a tab-separated line (`session_id, stop_reason, usage_limited,
settled, aborted, thread_id, has_text, has_tool_call`) and prints the outcome
class, so it is callable from a test with a synthetic line and no job: drive
the fixture's cases through BOTH sides in one test, and let the shell
classification fail the test when it disagrees. State the boundary explicitly
in the fixture (`predicate_source` already names
`scripts/controller/pi-impl.sh:classify_run()`).

If sourcing `pi-impl.sh` from a test turns out to be impossible (it is a
`set -euo pipefail` script with a `main()` that runs jobs), the acceptable
alternative is a separate `--classify-only` entry point in that script,
reviewed as a controller change, so the predicate has ONE implementation that
a test can call.

## Priority

Medium: the Python side is pinned and the drift would be caught by a human
reading both files only after a retry loop misbehaved, which is exactly the
class of silent disagreement this estate treats as a defect.
