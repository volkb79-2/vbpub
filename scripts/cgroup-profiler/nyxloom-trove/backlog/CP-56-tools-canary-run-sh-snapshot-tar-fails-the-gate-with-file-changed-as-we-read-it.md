---
kind: backlog-entry
schema_version: 1
id: CP-56
title: "tools/canary-run.sh: the disposable-copy tar exits 1 with 'file changed as we read it' on a live tree and fails the gate"
status: fixed
type: "bugfix"
severity: "low"
component: "tools"
priority: 3
provenance: "cgprofile r3 lane on v1001, 2026-11-04 (campaign cgprofile-r3-20261104, log run.log)"
filed_by: "rg-154"
filed_date: "2026-11-04"
---

## Observed mechanism

`canary-run.sh` copies the project into a temp dir with `tar -C "$project_dir" ... -cf - . | tar -xf -` under `set -o pipefail`. The lane ran in a
main-checkout-opt-in container whose tree other processes may write to; GNU tar then reports `tar: .: file changed as we read it` and exits 1, and the
whole r3 lane ended `exit 1` before the first canary ran.

## Fix

The creating tar gets `--warning=no-file-changed` (verified: the same tree/writer race gives rc 1 without it). A torn source file would show up as a failing
canary baseline, never as a silent pass. Fixed in the rg-154 branch (minimal change to the one line group in `tools/canary-run.sh`).
