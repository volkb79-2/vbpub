---
kind: backlog-entry
schema_version: 1
id: NL-67
title: "Decide whether tester-base should pin or float pytest"
status: open
type: "decision"
severity: "medium"
component: "toolchain"
provenance: "2026-11-12 pytest 9.2 process-wide filesystem-mock audit"
filed_by: "Codex deepseek-v4.1-flash"
spec_owner: "operator-owned tester-base policy"
filed_date: "2026-11-12"
---

## Observed mechanism and reproduction

`tester-unified:prev-20261112` carries Pytest 9.1.1 and
`tester-unified:run-gate-2550` carries Pytest 9.2.0 on the same Python 3.14.7.
Pytest 9.2 began calling `os.fstat` in capture and teardown paths, exposing
repository tests that replaced the shared `os` functions process-wide with
incomplete or raising fakes.

## Why nyxloom owns the filed question

This is an estate-level tool-chain policy question rather than a defect owned
by any one project. Nyxloom's managed backlog can hold it until the operator
chooses a policy; no `tester-base` file is changed by this filing.

## Proposed contract

Choose one supported policy:

1. Pin Pytest in `tester-base` and change it only by a reviewed image update
   with recorded compatibility evidence; or
2. Float Pytest within a declared compatibility range and treat the resulting
   runner-version changes as part of the tester image identity.

The decision must state which artifacts record the effective version, how an
unexpected Pytest upgrade is detected before project gates run, and whether
projects may declare a narrower test-runner floor independently.

## Oracles

- The selected policy is visible from the tester image metadata and the
  project gate documentation.
- A policy-compliance test distinguishes an allowed version from a rejected
  one without relying on host coincidence.
- Existing image and gate evidence identifies the actual Pytest version used.

## Updates

- 2026-11-12: open for the operator; do not change `tester-base` before the
  policy decision.
