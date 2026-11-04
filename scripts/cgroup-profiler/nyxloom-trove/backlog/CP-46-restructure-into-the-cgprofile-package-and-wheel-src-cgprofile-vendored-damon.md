---
kind: backlog-entry
schema_version: 1
id: CP-46
title: "Restructure into the cgprofile package and wheel: src/cgprofile, distribution cgprofile, scm version, vendored DAMON, install lane (plan package 1)"
status: open
type: "feature"
component: "release"
context_estimate: "medium"
provenance: "Operator acceptance 2026-11-04 of the cgpctl plan defaults (CGPCTL-PLAN-2026-11-04 sections 1-4, Q1-Q11); contract in docs/CGPCTL-SPEC.md, docs/PROTOCOL.md section 10, docs/NATIVE-DEPLOYMENT.md (package 0)"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler release"
priority: 2
filed_date: "2026-11-04"
---

## Problem

The project is a flat checkout: `lib/` (not a valid top-level package name), `cgprofile.py`, a bash shim, `py-modules = []`, the distribution
`cgroup-profiler` with a literal version, and a DAMON library loaded from the sibling `scripts/damon-analysis` by relative path
(`lib/damon.py:45-48`). None of that can be a wheel.

## Design

Contract: `docs/NATIVE-DEPLOYMENT.md` section 2 and `docs/CGPCTL-SPEC.md` (names). Branch `cgp2-1-package-20261104`, mechanical but it touches
every import.

- Move `lib/` and `cgprofile.py` into `src/cgprofile/` (`collect`, `daemon`, `client`, `report`, `cli`, `_damon`, `skills/`, `data/`); keep behaviour
  identical; no new features in this package.
- `pyproject.toml`: distribution `cgprofile`; scripts `cgpctl` and `cgpd` pointing at thin `main` stubs that the later packages fill; extra `report`
  with `>=` floors and a reason per floor; dependency `cli-extended>=0.5.1` with its reason; scm-derived version, no literal, no `CGPROFILE_VERSION`
  input; package data for the unit templates and the skill.
- Vendor the DAMON module(s) as `cgprofile._damon` with a sync test against `scripts/damon-analysis` (recorded decision Q7).
- `cmru.toml`: add the `wheel` artifact (sha256 sidecar, `latest.json`) beside `oci-image`; this package completes CP-41.
- Delete the relative-path DAMON import and the `<project>/venv/bin/python` report subprocess path only where the move forces it; removal of the
  report op is package 3.

## Acceptance criteria

- Full existing suite green on the new layout with the same coverage (100% as today).
- `pip install --no-index` of the built wheel into a clean venv: `cgpctl --version`, `cgpd --version` print the scm version; metadata test pins the
  dependency closure (cli-extended only); the wheel contains no checkout path.
- The DAMON sync test fails when the source and the copy differ.

## Dependencies and order

After package 0 (this contract). Blocks packages 2 and 3, which run in parallel afterwards. Covers CP-41 acceptance.
