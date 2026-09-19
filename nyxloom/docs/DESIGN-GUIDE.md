# Nyxloom design guide

The README describes the shipped surface; [`CONSUMERS.md`](CONSUMERS.md) shows
how to adopt it. This document records the reasoning behind the release-facing
test contract.

## Why the broad CLI lane measures branch coverage

Nyxloom's primary lane covers the complete CLI package in `tester-unified` and
is judged by Assay. Line coverage alone can execute a decision while leaving
one outcome untested, so the lane now passes `--cov-branch` and requires the
same 100% whole-source floor for both lines and branches. The narrower
`session-extract` lane already carried branch, mutation, and canary evidence;
the broad lane now has a consistent minimum reach contract.

Hypothesis remains appropriate for pure CLI and configuration invariants where
input families are broad. Schemathesis is outside the CLI-only lane because
Nyxloom does not expose an owned HTTP/OpenAPI contract there.
