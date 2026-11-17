---
kind: backlog-entry
schema_version: 1
id: NL-83
title: "subprocess readers wait for pipe EOF and can hang after the child exited (audit, from cmru KI-178)"
status: open
type: "bugfix"
severity: "high"
provenance: "vbpub mdt-cockpit release 2026-11-17, cmru KI-178"
filed_date: "2026-11-17"
---

**Observed.** On 2026-11-17 a release stalled for 11 minutes because the child exited while an orphaned grandchild kept the inherited stdout pipe open, so a read-to-EOF loop never ended (cmru KI-178 has the full mechanism; killing only the pipe holder made everything exit at once). The same shape (`Popen(stdout=PIPE)` read until EOF, or `communicate()` without a timeout) exists in nyxloom at `src/nyxloom/harness_search.py:982` and `:1272`, `src/nyxloom/session_extract/follow.py:826` and `src/nyxloom/doctor.py:769`. None is reproduced here, so this is an audit entry, not a confirmed defect.

**Why nyxloom owns it.** These are nyxloom's own subprocess reads. The controller-side stall detection (`codex/job-check.sh`, HUNG-TOOL) watches agents, not nyxloom's own reads. `harness_search` runs per query, so a hang there blocks the session finder.

**Proposed contract.** Every subprocess read in nyxloom is bounded: a timeout, or the bounded post-exit drain plus process-group start from cmru KI-178 once that helper exists. A reader that cannot hang says why in a one-line code comment (closed-pipe builtin, file read only).

**Oracles.** One test per listed site where the helper command exits while a background `sleep` holds stdout: the call returns within the bound. A controlled wrong implementation (read to EOF) must make the test time out.

**SPEC owner.** The subprocess-handling section of the nyxloom SPEC, or a new short section if none exists.
