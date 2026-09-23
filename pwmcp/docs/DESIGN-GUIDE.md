# PWMCP design guide

The release gate uses source-backed Assay through `run-gate.py`. Run-gate owns
the tester-unified container, mounts, cgroup placement, and resumable progress
plumbing; Assay owns the test command, branch coverage, and mutation judgment.
Keeping those responsibilities separate lets the same lane run in a release or
as an operator-selected R2 investigation.

The R1 lane uses Hypothesis for the pure upstream-version intersection rule.
PWMCP has no OpenAPI surface, so Schemathesis would add no evidence and is not
part of the lane.
