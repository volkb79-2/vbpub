---
kind: backlog-entry
schema_version: 1
id: CP-51
title: "GPL-2.0 DAMON analysis code vendored in an otherwise Apache/MIT-licensed wheel: keep vendored, split into an optional cgprofile-damon extra, or rewrite"
status: open
type: "decision"
component: "release"
context_estimate: "small"
provenance: "Found in package 1 of cgprofile 2.0 (CP-46): vendoring scripts/damon-analysis/lib/damon_analysis.py into the wheel"
filed_by: "vbpub maintenance"
spec_owner: "operator"
priority: 1
filed_date: "2026-11-04"
---

## Problem

`src/cgprofile/_damon/damon_analysis.py` is a byte copy of `scripts/damon-analysis/lib/damon_analysis.py` and carries
`# SPDX-License-Identifier: GPL-2.0` (GPL-2.0-only). Package 1 ships it inside the `cgprofile` wheel, because the wheel cannot import across
projects and the 1.x daemon and `--damon` path need `SysfsInterface` and `Classifier`.

Facts from the repository, not a decision:

- cgprofile declares no licence of its own (`pyproject.toml` has no `license` field; there is no LICENSE file anywhere in the repository).
- Every other estate project that declares one declares `MIT` (cmru, run-gate-project and others).
- The wheel is published as a public release asset, so shipping GPL-2.0-only code in it is a redistribution.
- The module is pure Python, imported by `cgprofile.damon` only; `damon` already degrades to "DAMON unavailable" when the import fails.

Package 1 did not decide. It kept the SPDX header unchanged, added `NOTICE` naming the file and the open question, and pins the copy to its
source with `tests/test_damon_vendor.py`.

## Options

1. Keep vendored in the core wheel and declare the wheel's licence honestly as a combination (the wheel then carries GPL-2.0-only
   terms for that module; consumers that must stay permissive cannot use the core wheel).
2. Split it out: a separate `cgprofile-damon` distribution (or optional extra) holding `_damon`, imported lazily by `cgprofile.damon`, so the
   core wheel carries no GPL code and the DAMON feature is an opt-in install. Cost: a second wheel, release stream and version coupling.
3. Rewrite the needed surface (`SysfsInterface`, `Classifier`) clean-room under the project's own licence, then delete the vendored copy and
   the sync test. Cost: the most work and a risk of behavioural drift in a measured subsystem.
4. Relicense upstream: if the damon-analysis author (the same maintainer) owns the copyright, relicense that file and drop the question.

## Acceptance criteria

- The operator picks one option and the choice is recorded here.
- The chosen state is reflected in `pyproject.toml` (licence field and `license-files`), `NOTICE`, and the wheel contents (a test in
  `tests/test_wheel_install.py` asserts the shipped files).
- If option 2 or 3: `tests/test_damon_vendor.py` is removed or retargeted and `cmru.toml` reflects the extra distribution.
