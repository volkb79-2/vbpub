# Changelog

All notable changes to the controller kit (`scripts/controller`) are recorded
here, newest first. The kit ships with the repository's `scripts/` tree; it has
no separate version number, so entries are grouped by the work that landed.

## [Unreleased]

### Added
- `LONG-TOOL` / `HUNG-TOOL` (CTL-18): `job-status.sh` now separates a heavy tool
  that is still working from one that stopped. `HUNG-TOOL` requires the ABSENCE
  of progress evidence (`progress_epoch` from the new sidecar: bytes appended to
  the run's stream, or growth of the CPU-time and IO counters of the wrapper's
  process subtree), so a slow `grep` or a long `pytest` no longer reads as hung
  and the controller no longer kills working runs. The row also prints the tool's
  own command (`cmd=`) and the exact child pids to steer (`steer=`), because the
  only process the earlier row could name was the wrapper — the one process that
  must not be killed. `pi-live.py` reads `/proc` counters and
  `tool_execution_update` (a tool's own partial output) for that evidence; an
  unreadable `/proc` leaves the fact `null` and licenses the weaker word.
- `DIED` (CTL-19): `job-check.sh` reports a run whose `.done` is missing from the
  run name's own owner record (`<run>.lock`: pid, start epoch, kernel start
  ticks, boot id) instead of assuming `RUNNING`. A live owner stays `RUNNING (no
  .done)`, a gone owner (or a recycled pid, or one recorded under a previous
  boot) is `DIED (wrapper pid <pid> gone, no .done)` and a red flag, and a name
  nobody ever owned keeps the prototype line plus `owner record: none`.
- One live wrapper per run name (CTL-14): `lib.sh` `controller_run_claim` takes an
  exclusive `flock` on `<runs-dir>/<name>.lock` before any name-owned record is
  written, so a second invocation on the same name is refused with exit 2 and the
  live owner's pid. `CONTROLLER_RUN_FORCE=1` never bypasses a live owner; for a
  dead one it retires the whole record set into `retired/<name>/<utc-stamp>/`
  (nothing is deleted) and names the previous owner.
- Run outcome vocabulary (CTL-13, CTL-17): `pi-impl.sh` now classifies
  `CONTEXT-FULL` (a settled `stopReason=length`: the context window filled; the
  work is intact and the controller starts a FRESH successor from the run's
  forward-oriented state file — the wrapper never continues it) and
  `TOOL-CALL-NO-TEXT` (a settled `stop` whose final message is a tool call with no
  text: the run ended mid-work). Neither is `SUCCESS` and neither is the continued
  `EMPTY-COMPLETION` class; `job-check.sh` names both so a controller cannot miss
  them.
- `.live` token semantics (CTL-15): `latest_total_tokens` is the last NON-ZERO
  measurement (or `null` when none was ever read) and
  `latest_total_tokens_last_report` is the most recent usage report as its own
  fact, so a `0` from a streaming delta or an error turn never masquerades as an
  empty context. `job-status.sh` prints both.
- `since-commit` (CTL-16) names whose commit the age belongs to:
  `since-commit=no-commits-yet` when the run has no commit of its own,
  `since-commit=123s(ahead=2)` when it does, and `since-commit=null` when
  `commits_ahead` cannot be read — an unreadable qualifier never licenses the
  number, and a run started on an existing branch no longer reports the base
  commit's age as its own.
- `codex-impl.sh` claims its run name (the same `lib.sh` helper as `pi-impl.sh`),
  releases it on every exit path, and writes `<run>.done` on every exit path after
  the child launches as `EXIT=<n> OUTCOME=CODE-EXIT AUTORESUME=<k>`. A resume
  removes the previous invocation's marker before its child starts, so a reader
  never reports an older resume's exit while the new one works.

### Fixed
- A final assistant message with `stopReason=stop`, a tool call and no text no
  longer classifies `SUCCESS` (CTL-13), and `stopReason=length` no longer
  classifies `INCOMPLETE` (CTL-17).
- A wrapper killed without writing `.done` no longer reads as a running job
  (CTL-19), and two wrappers can no longer share one run name's records (CTL-14).
- `HUNG-TOOL` is no longer decided on wall-clock alone (CTL-18), and
  `.live`/`job-status` no longer publish a placeholder `0` as the context size or
  an unqualified base-commit age as `since-commit` (CTL-15, CTL-16).
