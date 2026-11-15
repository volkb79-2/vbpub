# Controller toolkit design

The controller is operator tooling around the repo's existing worktree,
`run-gate`, CMRU, and remote-gate workflows. It keeps orchestration state local
and leaves lifecycle decisions to those project tools.

## Keep state outside the checkout

(Exception, operator-agreed 2026-11-12: the controller's own working state, a gitignored `plan.yaml` and `.controller-state/` at the repo root, lives in the checkout and has a single owner; see the root `AGENTS.md` section "Long-running controllers: plan, handoff and compaction recovery". Everything below still applies to run records, queue state, host facts, logs and evidence.)

Run streams and completion markers default to `$CONTROLLER_HOME/runs/`; set
`CONTROLLER_RUNS_DIR` to use another run-record directory. Queue state, local
host facts, logs, and release evidence archives live under `CONTROLLER_HOME`,
defaulting to `$HOME/controller`. Gate evidence can use the configured
`CONTROLLER_LOGS_DIR`. This keeps credentials
and machine-specific details out of Git and lets one checkout serve more than
one controller machine. The committed `*.example` files describe the schema;
the local copies carry real paths and provider identity commands.

## Record the child process result

Agent launchers save the event stream and a separate `EXIT=` completion
marker. A missing marker stays distinguishable from a successful run even if
the process or shell is interrupted. Codex markers also record the number of
bounded same-thread auto-resumes. The caller selects and records the route
because the child cannot attest reliable invocation metadata.

## Pi Open Design route

Pi's Open Design wrapper uses the shared account helper for account alternation,
blocking, and session-to-account mapping. It makes no quota probe before a
start. Pi's operator-owned provider definitions select API-key environment
variable names; the wrapper sources `$HOME/.env.ai` and exports only the
selected account variable to the pi process. Key values never go in argv or
controller run evidence.

Pi 1.5.0 can report a provider 402 in the final assistant `message_end` event
and still exit zero. The wrapper therefore classifies the final assistant
`stopReason`, records `OUTCOME=USAGE-LIMIT` for an `insufficient_balance`
error, and passes the stream to the shared blocker to store its `reset_at`.
When the reset is missing or malformed, the account helper applies its bounded
temporary block. This keeps success and quota exhaustion distinguishable even
when their process exit codes match.

New sessions are identified by the pi `session` event and stored in the same
thread map the Codex routes use. Resume looks up that session's account and
starts on it; if that account is blocked, the shared helper selects the other
available account or exits 3 when neither can run. The controller starts
`pi-impl.sh` detached and checks its `.done` marker, while the wrapper always
closes pi's stdin to prevent the non-interactive process from waiting for
input.

### Why the terminal outcome is classified, not just the exit code

An exit code cannot carry what went wrong with pi. Two measured store-crate
stalls ended with `stopReason: stop`, exit zero and NO tool call, while the work
was uncommitted and no report existed: one final assistant message had
`content: []` at 211k context, the other carried only a `thinking` block at
177k. pi treats a turn with no tool call as the end of the run, so the wrapper
must read the stream to tell those apart from a finished job.

The classification compares facts the run itself left behind, in this order: the final
assistant `stopReason` and 402 code (`USAGE-LIMIT`, `ERROR`), the pi exit status
(`ERROR`), the stream's settling and `stopReason` (`INCOMPLETE`), the final
message's text and tool calls (`EMPTY-COMPLETION`), the worktree's own porcelain
(`UNCOMMITTED`), and the expected report file (`NO-REPORT`). `SUCCESS` is what
survives all of them. Every check is a fact with an authoritative source
(the stream, `git`, the report path the caller named), never an inference: a
worktree that cannot answer `git rev-parse` is refused before pi starts rather
than read as clean, and an unset report path means the report check is
*skipped*, not passed.

The alternative - keep `SUCCESS` for exit zero and let `job-check.sh` explain
it - was rejected: the wrapper already holds the stream, `job-check.sh` runs on
demand and only after someone remembers, and a caller who reads only `.done`
gets the wrong answer. The classes are additive: the first `.done` line keeps
its historical `EXIT=<n> OUTCOME=<word>` shape, so `job-check.sh` and every
consumer that matches on `EXIT=0` keep working.

Continuation is bounded and class-specific on purpose. Only `EMPTY-COMPLETION`
is continued, because only that class is a provider/model anomaly: the session
is intact and the same conversation can finish the work. `UNCOMMITTED`,
`NO-REPORT`, `ERROR` and `INCOMPLETE` describe real states of the worktree or
the provider, and a blind resume would either loop or mask them. The limit is
two attempts (measured: two store-crate stalls needed one continuation, a
second was unused), it is set by `PI_EMPTY_RETRY_LIMIT`, and the prompt lives in
one file (`continue-empty-completion.md`) so a wrapper revision cannot drift
from the text a hand resume uses. A restart is never automatic: a fresh session
needed about an hour and over 100 tool calls to re-orient, which is a
controller decision, not a wrapper reflex.

### Job-check compatibility

`job-check.sh` keeps the prototype's `any` route as an explicit allowed-set
mode: each commit in the selected base range, including merge commits, must
contain at least one accepted Codex Luna/Sol/DeepSeek or Claude trailer. More than
one recognized trailer is accepted and reported as `INFO`, because one commit
can legitimately carry the trailers of multiple agents. Named routes still
require exactly their own Codex trailer, while `none` skips trailer auditing.
It also retains the prototype's
relative report-litter check for untracked files under `reports/` and
`scripts/*/reports/` and its final-message checks for empty, intent-only,
tool-call, and mid-wait endings. A mid-wait result requires the final sentence
to announce a gate, process, or task that remains pending. This includes
first-person forms such as “I am continuing to wait” and “still waiting on the
gate”; quoted or historical mentions do not count unless they form the final
pending announcement.

For a branch with commits, at least one log under `CONTROLLER_LOGS_DIR` must
contain `run-gate.commit=<full HEAD>` and a terminal Run-Gate lane verdict. The
last `run-gate: lane ... verdict` line must say `PASS`; subtest PASS lines
cannot override a later FAIL. Every supplied report must name the current HEAD
by its short or full hash. The event stream
distinguishes a completed reasoning-only turn (`REASONING-END`), a usage-limit
failure with its reset time (`USAGE-LIMIT`), and a run with no final
`turn.completed` (`KILLED`). A usage-limit message takes precedence over the
generic killed class and over text-ending detectors such as `MID-WAIT`, so the
actionable reset time remains visible. The shared parser in `lib.sh` accepts
the time-only form and the dated `Mon DDth, YYYY H:MM AM/PM` form. The report
prints the text after `try again at` verbatim; the message does not establish a
timezone, so the controller never appends one.

A wrapper killed after `run-gate` started can leave an exited container whose
later collection log has no `run-gate.commit` line. `job-check.sh` accepts that
shape only as a re-attach exception. The collection log must end in PASS and
name exactly one container containing the current head's eight-character
prefix. A different log must bind that exact container name on the same line as
the full current `run-gate.commit` label, and a supplied report must name the
container. All three facts are required; a container name alone, a report alone,
or a label for another commit never establishes gate evidence.

An absent `.done` marker prints `RUNNING (no .done)` and exits 0, even when
the event stream is missing. This preserves the prototype's active-status
behavior; it is not evidence that the child completed, and the command does
not check stream age or existence in this state. Read the wrapper's `.done`
marker to learn the child exit status.

## Bounded auto-continue

The observed DeepSeek stall ended with `turn.completed` immediately after a
reasoning item. `codex-impl.sh` resumes that same Codex thread only when this
is the last item in the completed turn. The route default is two attempts for
DeepSeek and zero for Luna; `CODEX_AUTO_CONTINUE` overrides it. Every attempt
is bounded by that count and recorded in `<run>.autoresume.log` and the `.done`
marker. This addresses the detected stream shape while leaving ordinary clean
turns alone. Stale `.done`, `.jsonl`, and `.err` evidence is refused for new
runs. Resume mode intentionally bypasses that new-run collision guard so a
crashed continuation can reuse or rewrite its own output; a completed resumed
run remains distinguishable by its `.done` marker.

## Recover and schedule Codex work

`resume-usage-limited.sh` scans recent completed streams for a `turn.failed`
usage-limit message, reads the saved thread id, route, and explicit `<run>.wt`
worktree map, then calls the existing implementer on that same thread. A
missing map is a refusal; the event stream is never used to guess a worktree.
The default `naf-` exclusion keeps another controller's runs out of this
helper; `RESUME_EXCLUDE_PREFIXES` configures the space-separated prefix list.
The helper skips successful runs, already-resumed names, and live resumed
wrappers. A per-resume `flock` claim is held until the wrapper exits, which
prevents two scans from launching the same run even if both pass their earlier
eligibility checks. For a dated retry, it compares the GNU `date -d` epoch,
after stripping the ordinal suffix, with the current epoch and skips a run that
is still in the future. The source message has no timezone fact, so no zone is
invented. Time-only retry messages retain immediate recovery. `--dry-run`
applies the same map, prefix, live-process and future-retry checks without
launching jobs. `start-after.sh` provides a separate UTC launch time when an
operator wants to schedule work.

## Two-slot gate scheduling

`gate-run.sh` requires two distinct absolute lock paths from local controller
configuration. Its refusal names `controller.local.env.example`,
`CONTROLLER_GATE_LOCK`, and `CONTROLLER_GATE2_LOCK`. It tries each slot without
waiting; when both are occupied it polls both locks with non-blocking attempts
every 5 seconds and takes whichever frees first. A progress line every 300
seconds makes a long wait visible. It does not invent a lock path or launch
outside the configured locks. The command runs from the caller's project
directory so Run-Gate reads that project's lane declaration.

## Bounded session read-back

The compaction recipe for Claude Code controllers is the `readback` preset of
`nyxloom-harness extract` (see root `AGENTS.md`); it keeps operator turns whole.
`build-readback.py` below is the older character-budget builder and its cut can
truncate operator material.

`build-readback.py` requires the session path, runs directory, marker and
output path as arguments. Its total `--budget-chars` cap covers all four
sections; when the output is too large, it shares the remaining space across
the sections and marks the result as trimmed. Missing markers and a failing
`nyxloom-harness extract` command fail instead of producing a partial
read-back.

## Compaction recovery hooks

`hooks/ctx-hooks.py` exists because a compaction is the one moment a long
controller session cannot recover from alone. The decisions below follow from
the documented harness facts (measured in `docs/design/ctlstat/`):

- **The hook never fails the session.** A hook that raises, hangs or exits
towards the harness does not merely lose the readback, it can block the
compaction itself. So every path exits 0, the extract runs under a 15 s
default / **20 s hard cap**, and a failure is made visible in two safe places
instead: one log line, and (on `session-start`) the exact command to build the
readback by hand. A failure is also recorded durably in
`readback-state.json`, because the next session reads that state, not the log.
- **The whole session is the goal anchor's source, but the anchor is opt-in.**
`pre-compact` runs the preset WITHOUT `--goal-prompt`: a controller session's
anchor is its `plan.yaml`, and its goal shifts (AGENTS.md). A SPAWNED session
(`--session-kind spawned`, or `--goal-prompt`) asks for its first prompt; a
`--goal-file` pin wins over both. The same flags are repeated in the manual
fallback command, so a hand-built readback keeps the same anchor. A PreCompact
fired inside a subagent builds nothing unless a kind is named (it would renumber
and re-mark the controller's state). `--redact-pattern` /
`CTX_READBACK_REDACT_PATTERNS` reach the extract, but a pattern can itself be a
secret, so the hook logs, stores and prints only a `<REGEX>` placeholder.
- **State, not a checkout path.** The state directory comes from a flag, then
`CTX_STATE_DIR`, then the hook input's own `cwd/.controller-state`. No
repository path, transcript path or 30000/10000 budget is compiled in; the
budgets are flags/env with those documented defaults.
- **The numbering is crash-safe.** `readback-N.md` takes one past the highest
number ON DISK, not one past a counter in a state file, so a state file lost to
a crash cannot make a later readback overwrite an earlier one. The previous
readback's marker is read from state and passed as `--since`, which is what
makes two consecutive readbacks never duplicate each other.
- **A subagent's own transcript is used.** Inside a subagent the hook input's
`transcript_path` is the PARENT's transcript (measured 2026-11-14); reporting
the parent's fill caused a false "past 200k" note that made a subagent stop
early. The hook resolves
`<parent dir>/<session_id>/subagents/agent-<agent_id>.jsonl` and reports nothing
when that file is absent, rather than substituting the parent's size.

None of this replaces the harness's own compaction: on a harness with no
PreCompact hook the session builds its own readback on resume with the same
command (see
[`docs/design/ctlstat/HARNESS-COMPACTION-CHECKLIST.md`](../../../docs/design/ctlstat/HARNESS-COMPACTION-CHECKLIST.md)).

## Queue lane validation

Each `queue_lanes` value is one non-empty `project|lane` pair. The shared
host-table reader rejects missing sides or extra separators before start or
status operations use the value. Rejecting malformed entries at load avoids
repeated queue retries against a lane that could never be dispatched.

## Gate each host action by identity

`hx.sh` and the queue use the same local host table. `hx.sh` checks provider
identity before each SSH call. The queue checks identity before each lane
launch and standalone `probe` command; its periodic resource probe runs the
configured host-side guard without a provider API check. This keeps addresses,
key paths, server IDs, and provider credential locations local while making
the queue's identity-check boundary explicit.

## Replace live queue code only after draining it

The checked-in queue is canonical, while an installed copy may be executing
for hours. The update procedure pauses new work, waits for the current remote
run to finish, confirms both that `.current` disappeared and that the final log
line records the loop observed the pause, then stops the loop and waits for its
PID marker to disappear before atomically replacing same-filesystem staged
files. A TERM during an in-flight lane exits the loop without clearing
`.current`; a stale marker needs evidence-based diagnosis. Fixed sleeps or
deleting the marker do not prove the remote run ended.

## Keep validation in the registered gate

The `suite` lane runs in `tester-unified` and covers fake-root behavior,
`bash -n`, available ShellCheck, and hand-mutated guard checks. It is not
registered for release. New behavior therefore gets reproducible test
evidence without adding a controller release lifecycle.

## Copy hand-written skills instead of giving them a `skills` verb

cli-extended's `skills` verb is the right owner for a tool whose skill is
package data: it stamps the installed copy and `skills check` exits 1 when the
source moved on, so staleness is a check that can fail. Two of this estate's
skills have no such owner — assay and run-gate carry hand-written, repo-level
`SKILL.md` trees and ship no `skills` verb — and a consumer that copies one by
hand reads it forever, which is exactly how the 2026-11 refresh found a
run-gate skill describing "rev 49, unreleased" for a released 25.6.0. (fleetd
was the third; it adopted the verb while this refresh was in flight, which is
the intended way for an entry to leave this list.)

The alternative, giving each of those tools a `skills` verb, is the better
long-run answer and is filed as backlog entries against each tool. This script
exists because that answer is not available today and an agent that installs
skills by hand today still deserves a check that can go red. It is deliberately
narrow:

- **Ownership, not adoption.** It writes only the directories named in its
  `SOURCES` list, and only into a directory carrying its own
  `.controller-skills-stamp.json`. A directory without that stamp is somebody
  else's (`~/.claude/skills/synced`, a tool-installed skill, a hand copy) and is
  refused rather than replaced. A directory with the stamp whose bytes no longer
  match the stamp is a *local edit*, refused unless `--overwrite-modified`; a
  directory whose bytes match but whose source digest moved is *stale*, which
  install does repair. Conflating those two would either clobber an operator's
  edit or leave a stale copy in place, and both are failures this estate has
  already paid for.
- **Digest of the tree, not the file.** A skill directory may grow a reference
  file; the stamp covers relative path plus bytes, so adding one is a source
  change and not an invisible no-op.
- **Two destinations, same as cli-extended.** `$CLAUDE_CONFIG_DIR` (default
  `~/.claude`) and `~/.agents`, because an agent harness may read either.
- **No lifecycle, no release.** The controller kit is validation tooling, not a
  release lane (see "Keep validation in the registered gate"), so the script is
  a script with a shell test and no packaging.
