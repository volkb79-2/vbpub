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

### Why a run name is owned by one live wrapper

Everything a run owns is keyed by its NAME: `<run>.done`, `.jsonl`, `.err`,
`.events`, `.live`, `.last.txt`, `.wt`, `.route` and `.thread`. Two wrappers on
one name would therefore interleave on those files with no error: the second
truncates `.events`, both append to one stream, the pid in `.live` is whichever
wrote last, and each writes its own `.done`. A reader then sees one job's
completion as the other job's result — the strongest form of the false
certification the rest of this kit exists to prevent.

The guard is a kernel `flock` on `<runs-dir>/<name>.lock`, taken before any
record is written and released on every exit path. A kernel lock, not a pid
test, because the release must not depend on the dying process running: a
wrapper that is TERMed, KILLed or OOM-killed leaves the name claimable, and a
recycled pid cannot inherit a stale claim. The lock file also records who holds
it (pid, start epoch, the pid's own kernel start ticks, boot id), so the refusal
can name the live owner and `job-check.sh` can tell a dead owner from a live
one. The agent and the liveness sidecar close their inherited copy of the
descriptor, so a child that outlives its wrapper never holds the name hostage.

Force is deliberately not a bypass: `CONTROLLER_RUN_FORCE=1` is refused while an
owner is alive, and for a dead owner it RETIRES the whole record set into
`retired/<run>/<utc-stamp>/` with the previous owner's identity written beside
it, instead of deleting or overwriting evidence. `resume` of the same session
takes the same lock rather than a second ownership dimension — it is another
invocation of the same owned run.

### Why the terminal outcome is classified, not just the exit code

An exit code cannot carry what went wrong with pi. Two measured store-crate
stalls ended with `stopReason: stop`, exit zero and NO tool call, while the work
was uncommitted and no report existed: one final assistant message had
`content: []` at 211k context, the other carried only a `thinking` block at
177k. pi treats a turn with no tool call as the end of the run, so the wrapper
must read the stream to tell those apart from a finished job.

The classification compares facts the run itself left behind, in this order: the final
assistant `stopReason` and 402 code (`USAGE-LIMIT`, `ERROR`), the pi exit status
(`ERROR`), the stream's settling (`INCOMPLETE`), a `length` stop
(`CONTEXT-FULL`), any other non-`stop` reason (`INCOMPLETE`), the final message's
text and tool calls (`TOOL-CALL-NO-TEXT`, then `EMPTY-COMPLETION`), the
worktree's own porcelain (`UNCOMMITTED`), and the expected report file
(`NO-REPORT`). `SUCCESS` is what survives all of them.

Three of those words exist because their neighbours have a different remedy,
not because the states differ in detail. `CONTEXT-FULL` is the real end state of
a long run: the harness stopped the model because the window filled. The work is
intact, so the action is a FRESH successor seeded from the run's
forward-oriented state file. The wrapper must not continue it, because
continuing a full context is the one continuation that cannot succeed, and a
fresh session pays about an hour of re-orientation that the controller, not the
wrapper, must decide to spend. `INCOMPLETE` is reserved for a stream the
classifier cannot attribute (unsettled, aborted, a `stop` reason it does not
know) and for an unreadable worktree status, where blind continuation or restart
would mask the real state. `TOOL-CALL-NO-TEXT` is a settled `stop` whose final
message is a tool call with no text: the run ended mid-work. It is deliberately
not `EMPTY-COMPLETION`, because that word is bound to the wrapper's one
automatic continuation and a tool call is not the anomaly that continuation
repairs. Text **plus** a trailing tool call is `SUCCESS`, by contrast: the class
is defined on the ABSENCE of text, the text is what the run was supposed to
produce, and the trailing call is bookkeeping pi emits in the same message (a
test pins this so it is never re-argued as the opposite).

Every check is a fact with an authoritative source
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

### Why liveness is a file, not a poll of the process

The controller needs to see a stalled run within seconds, not when a `.done`
marker happens to appear, and it must not learn that by killing or probing a
running agent. While a run is active the wrapper starts one small sidecar
(`pi-live.py`) as a child and kills it on exit; the sidecar appends nothing to
the run's evidence but rewrites `<run>.live` every `PI_LIVE_INTERVAL_SECONDS`
(30 s default) by reading only the bytes its stream gained since the previous
tick. That keeps a 400 MB stream at one incremental parse per tick.

The file holds facts only, as absolute epochs and counts, and is replaced by
temp-file + rename, so a reader never sees a half-written object; every age
(seconds since the last event, tool-call age, since-last-commit) is derived by
the reader, which is why a stale snapshot still describes exactly what it last
saw. A value that cannot be read - git unreachable, a stream not yet created -
is `null` rather than an invented zero.

`job-status.sh` is the read side: one line per run, `STALLED` when the wrapper
is alive and no pi event arrived within `JOB_STATUS_STALL_SECONDS` (600 s),
`LONG-TOOL` when a single tool call has been in flight past
`JOB_STATUS_HUNG_TOOL_SECONDS` (1800 s) while the run still shows progress
evidence, `HUNG-TOOL` for that age with no progress evidence, and
`NO-LIVE`/`WRAPPER-DEAD` kept distinct from `ACTIVE` so a missing snapshot
cannot read as health. It is read-only by construction: `kill -0` on the
recorded pid and file reads, nothing else. A long tool call is therefore
*reported* long before it is judged a failure, and no threshold on this path
stops an agent: the controller decides.

**Why `LONG-TOOL` exists as its own word.** The prototype decided `HUNG-TOOL`
from one quantity: the tool's age. The first weeks of real use showed what that
comparison actually collapses: a `grep -r` over a large tree, a `pytest` run and
an agent's own blocking experiment all exceeded 1800 s while three of the four
were working, and the controller killed runs that were making progress. Age is
not progress, so the sidecar now publishes an observable progress fact
(`progress_epoch`): the last tick at which bytes were appended to the run's
stream, or the CPU-time/IO counters of the in-flight tool call's own subtree
grew on the same set of processes. The summed set matters as much as the growth:
an earlier version summed the wrapper's whole tree, which also holds the agent
(always working) and the snapshotter (scanning `/proc` every tick), so a tool
that had stopped was still certified as progressing and `HUNG-TOOL` became
unreachable — the check was narrower than the word it printed. The set is
located structurally (`os.getpid()` is the snapshotter, the wrapper's other
direct child is the agent, the agent's descendants are the tool's processes),
never by matching a command name. `HUNG-TOOL` requires the absence of that
evidence within the same threshold, and an unreadable `/proc` leaves the fact
`null`, which licenses the weaker word (`LONG-TOOL`) rather than the stronger one. The diagnostic columns exist for the
same reason: `cmd=` and `steer=` are the tool's command and the exact child pid,
because the earlier row named neither and the only actionable process it could
describe was the wrapper — the one process that must not be killed.

**Why progress is a SURVIVOR's delta, not a change of the set.** The first
version compared rounded sums over an exactly-equal set, so ANY churn reset the
comparison: a `make -j` that forked and reaped a worker between two ticks, or a
`pytest` whose per-test subprocesses come and go, changed the set and the tool
looked freshly measured — a silent churn could read `LONG-TOOL` for as long as
it kept churning, and `HUNG-TOOL` was unreachable again. The rule is defined
over members, not over the set: a SURVIVOR (same pid, same kernel start ticks
in both readings) that shows a strictly positive CPU/IO delta is progress; a
set change alone is neither progress NOR a reset of the quiet clock, whose
baseline is the first reading of the current tool call. So churn with idle
survivors reads quiet (and `HUNG-TOOL` at the threshold), churn with a survivor
that burns CPU reads progress, and a pid RECYCLED between two ticks (same
number, different start ticks) is not a survivor at all — its counters say
nothing about the process measured before.

**Why the sidecar publishes `progress_measured`, and not just an epoch.** The
first version of the progress fact was one absolute epoch, and a reader could
not tell three different situations apart: evidence measured on this tick,
evidence from an EARLIER tick carried forward because the tool's subtree could
not be read this time, and evidence from BEFORE the tool call started (the run's
own session bytes). The third was the damaging one: when the tool's subtree
could not be located, the epoch was lifted up to the tool's start, and the tool's
age then stood in for progress — `HUNG-TOOL` decided on wall-clock alone, which
is exactly what the `LONG-TOOL` split exists to stop. So the epoch and its
provenance are two facts: `progress_measured` is false when the tick did not
read the CURRENT tool (no `/proc` subtree, or an epoch older than the call), and
the reader takes an unmeasured epoch as unknown, prints `progress=-` and uses
the weaker word. A payload without the flag is unknown for the same reason: a
missing measurement is not a measurement, and the producer writes the fact now,
so absence can only mean an older snapshot or a broken one.

Continuation is bounded and class-specific on purpose. Only `EMPTY-COMPLETION`
is continued, because only that class is a provider/model anomaly: the session
is intact and the same conversation can finish the work. `UNCOMMITTED`,
`NO-REPORT`, `ERROR`, `TOOL-CALL-NO-TEXT`, `CONTEXT-FULL` and `INCOMPLETE`
describe real states of the worktree, the provider or the context window, and a
blind resume would either loop or mask them. The limit is
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

An absent `.done` marker is not read as `RUNNING` by default any more. The
prototype's line asserted a process it had never looked for, so a wrapper killed
by `TERM`/`KILL`, an OOM kill or a container restart read as a live job forever
and the controller never relaunched it. The reader now takes the same owner
record the wrapper writes on a claim (`<run>.lock`: pid, start epoch, the pid's
kernel start ticks, boot id) and separates three real states that used to
collapse into one: a live owner is `RUNNING (no .done)`, an owner that is gone —
or whose kernel start ticks no longer match a recycled pid, or that was recorded
under a previous boot — is `DIED (wrapper pid <pid> gone, no .done)` and a red
flag, and a name nobody ever owned keeps the prototype's `RUNNING (no .done)`
with the explicit `owner record: none`. The comparison is now as strong as the
message: `RUNNING` is only printed when a recorded owner is alive, and every
other case either names why the owner is dead or says that no record exists.
The recovery the reader owes a controller does not stop there: a killed wrapper
usually dies before writing `<run>.thread`, and a controller without a thread id
can only restart (about an hour of re-orientation). So the id is READ from the
run's own stream — pi's first `session` event, Codex's `thread.started` — printed
as `thread: <id> (recovered …)`, and the exact `resume:` command printed beside
it. A stream with no id says `thread: none`; the file is never written by this
read-only check and no id is invented.

`job-status.sh` applies the same rule to a run that still has a `.live`: an
`ACTIVE`/`WRAPPER-DEAD` verdict comes from the owner record, and the payload's
own pid is only the fallback when no record exists (the row then says
`owner-record=none`). A dead wrapper with a live agent is its own state — the
row says `wrapper-dead-agent-alive` — named with the agent's pid and the pid of
what that agent is running:
the wrapper's death does not stop the work, and the only actionable process
left is the agent's tool.

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
