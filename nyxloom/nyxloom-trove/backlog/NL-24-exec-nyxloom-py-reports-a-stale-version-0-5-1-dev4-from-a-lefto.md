---
kind: backlog-entry
schema_version: 1
id: NL-24
title: "exec-nyxloom.py reports a stale version (0.5.1.dev4) from a leftover src/nyxloom.egg-info shadowing the installed dist metadata"
status: open
type: "bugfix"
severity: "low"
provenance: "dstdns 2026-09-30, exec-nyxloom.py version vs nyxloom --version 0.8.1.dev519"
filed_date: "2026-09-30"
---

## Observed
`./exec-nyxloom.py version` printed `nyxloom 0.5.1.dev4+g184e670e.d20260911` while the installed `nyxloom --version` printed `0.8.1.dev519+g9a0d247c`; the verb surface is the same. Minor, but it misreports which build an operator is running.

## Mechanism (confirmed in source)
`exec-nyxloom.py` inserts `nyxloom/src` at the front of `sys.path` (host-fallback leg). `src/nyxloom/__init__.py:_resolve_version()` reads `importlib.metadata.version("nyxloom")`, which now resolves the untracked, gitignored `nyxloom/src/nyxloom.egg-info` (PKG-INFO `Version: 0.5.1.dev4+g184e670e.d20260911`, left from a September 11 editable install) ahead of the installed dist-info. So the script reports stale egg-info metadata, not the code it runs.

## Why nyxloom owns it
A version string that can silently come from a leftover build directory is a provenance hazard for every evidence line that quotes it.

## Proposed fix
Derive the version in the host-fallback path from the same source the wheel stamps (setuptools-scm from git), or have `exec-nyxloom.py` refuse/warn when `src/nyxloom.egg-info` is older than the checkout HEAD; `nyxloom doctor` could flag a stale egg-info. Deleting the egg-info locally is only a workaround.

## Oracle
With a stale egg-info present, `exec-nyxloom.py version` must not print the stale version (either correct or a named refusal); a controlled wrong implementation that keeps reading `importlib.metadata` must fail.
