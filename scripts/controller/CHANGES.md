# Changelog

All notable changes to the controller kit (`scripts/controller`) are recorded
here, newest first. The kit ships with the repository's `scripts/` tree; it has
no separate version number, so entries are grouped by the work that landed.

## [Unreleased]

### Added
- `LONG-TOOL` / `HUNG-TOOL` (CTL-18): `job-status.sh` separates a heavy tool that
  is still working from one that stopped. `HUNG-TOOL` requires the ABSENCE of
  progress evidence, and the evidence set is the IN-FLIGHT TOOL CALL's own
  process subtree — never the wrapper's whole tree, which also holds the agent
  (a model loop) and the sidecar (its `/proc` scans and `git` calls), both of
  which grow every tick and once made a blocked tool read as working forever.
  Progress is a strictly positive delta in CPU ticks or IO bytes over the SAME
  processes (`pid` plus the kernel's own start ticks); a decrease, a changed set,
  a tool call's own START and an unreadable `/proc` are baselines, never
  evidence, and an unknown fact stays `null` (the row prints
  `progress=- progress-why=<reason>`). The row also prints the tool's own command
  (`cmd=`) and the exact child pids to steer (`steer=`), because the only process
  the earlier row could name was the wrapper — the one process that must not be
  killed. `pi-live.py` publishes `progress_reason`, `progress_baseline_epoch`
  (measured-but-never-moved, which is what licenses the stronger word) and
  `progress_tree_nodes`/`progress_tree_truncated` for the walk's documented cap
  (`PI_LIVE_TREE_NODE_LIMIT`, default 512).
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
- A settled `stop` whose final message carries TEXT **and** a trailing tool call
  is `SUCCESS` (pinned by a test): the text is the run's final message and the
  call is bookkeeping pi emits in the same message. `TOOL-CALL-NO-TEXT` is
  defined on the ABSENCE of text, so it never applies to that shape.
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
  never reports an older resume's exit while the new one works. Every launch path
  (new, resume, autoresume) closes the ownership descriptor before exec
  (`controller_close_ownership_fds`), so a SIGKILLed wrapper frees the name at
  once even while the agent it started — or a daemon the agent left behind — is
  still running; `CONTROLLER_RUN_FORCE=1` retires the previous records into
  `retired/<name>/<utc>/` and names what it retired. The owner record itself is
  written only AFTER the invocation's own preflight passes, so a REFUSED
  invocation never overwrites the identity of the owner it refused on.

### Fixed
- Progress is a surviving process's delta, not a change of the set (CTL-21):
  `pi-live.py` keeps each member of the in-flight tool's subtree separately and
  counts a SURVIVOR's strictly positive CPU/IO delta (the same pid AND the same
  kernel start ticks in both readings). A process-set change (`make -j` starting
  or reaping a worker, per-test subprocesses) is neither progress nor a reset of
  the quiet clock — its baseline is the first reading of the current tool call —
  so a silently churning tool still reads `HUNG-TOOL` at the threshold while a
  churning tool with a CPU-burning survivor reads `LONG-TOOL`. A recycled pid
  number is a different process and never a survivor.
- An unmeasurable progress epoch is no longer read as measured progress (CTL-20):
  `pi-live.py` publishes `progress_measured` beside `progress_epoch` — true only
  when the current tick read the in-flight tool's own subtree or evidence was
  produced on it, false when the tick could not re-read the subtree (the
  previous epoch is carried forward as a leftover) or when the epoch is older
  than the call (it is then published as `null`, never lifted to the tool's
  start). `job-status.sh` takes that flag as its only licence, so an unmeasured
  epoch prints `progress=- progress-why=unread` and the weaker word: the tool's
  age alone can no longer decide `HUNG-TOOL`, which the lift made it do again
  whenever the run's stream grew before a tool whose subtree could not be read.
- `job-status` liveness and the dead-wrapper/orphan case: `ACTIVE`/`WRAPPER-DEAD`
  is decided from the run name's owner record (`<run>.lock`), the same rule
  `job-check.sh` applies, so a recycled wrapper pid no longer reads `ACTIVE` and
  the row names the RECORDED pid (`recycled` is stated) instead of the stranger
  the snapshot carried. With no record the payload pid is the fallback and the
  row says `owner-record=none`. When the wrapper is dead and the agent it started
  still works, the row says `wrapper-dead-agent-alive` and names the agent
  (`agent=<pid>(<cmd>)`) and what that agent is running (`tool=<pid>`), found
  structurally (cwd = the run's worktree, argv[0] = `pi`/`codex`). The
  `last-event` age of a dead run comes from the run stream's mtime and says so
  (`last-event=42s(stream)`), because the dead sidecar can never be refreshed.
- `job-check` thread recovery: a `DIED` run whose `.thread` was never written now
  has its id READ from the run's own stream (pi's first `session` event, Codex's
  `thread.started`), printed as `thread: <id> (recovered …)`, with the exact
  `resume:` command; `thread: none` when the stream carries no id, and the file
  is never written by this read-only check.
- A final assistant message with `stopReason=stop`, a tool call and no text no
  longer classifies `SUCCESS` (CTL-13), and `stopReason=length` no longer
  classifies `INCOMPLETE` (CTL-17).
- A wrapper killed without writing `.done` no longer reads as a running job
  (CTL-19), and two wrappers can no longer share one run name's records (CTL-14).
- `HUNG-TOOL` is no longer decided on wall-clock alone (CTL-18), and
  `.live`/`job-status` no longer publish a placeholder `0` as the context size or
  an unqualified base-commit age as `since-commit` (CTL-15, CTL-16).
