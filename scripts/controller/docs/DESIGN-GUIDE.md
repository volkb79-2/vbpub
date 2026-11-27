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

### Why the pi signal guard is mechanical, not advisory

A pi agent on this estate killed other controller sessions' monitors with
`pkill -f 'sleep 30'` **although its brief banned pattern kills**. The ban was
text, and text did not hold, so the guard is a refusal in the command path: a
rule the agent can only obey by reading it is not a rule at all. Only a check
that can go red without anyone remembering it protects anything.

The guard is a `PATH` entry, not a wrapper flag, because that is the surface a
mechanical refusal needs. `pi-impl.sh` prepends `scripts/controller/pi-shims/`
to the **agent's** `PATH` for the first run, for a resume and for every
automatic continuation alike — all three go through one `invoke_pi`, and the
wrapper's own commands keep the caller's `PATH`. `pkill` and `killall` always
refuse (`pi-shim: refused <name>`, exit 126, the shell convention for a command
that exists and is refused); `pgrep` refuses only when an argument would make
it signal (`--signal`, `--signal=<sig>`, `--kill`, a `-<SIGNAME>` or
`-<number>` spelling), because listing processes is not a kill and a shim that
over-refused would be switched off.

Three decisions, each with the alternative it rejects:

- **Refuse rather than guess, on `pgrep`'s own parse.** pgrep's value-taking
  options are read with their value, so a pattern or a value that begins with
  `-` is never misread as an option (that is why there is a pass-through case
  with `-d -`), and an argument that is not one of pgrep's own options is
  refused. The cost is a wrong refusal on a long-option abbreviation; the cost
  of the alternative — passing an unclassified argument to the real pgrep — is
  signalling a stranger's process.
- **Fail closed at launch, before the name is claimed.** The shims must exist
  and be executable, and pi's own managed-bin directory must hold none of the
  three names, or the launch exits 2 with a named message. That check runs
  before `controller_run_claim`, so a refusal leaves no run record that would
  refuse the retry. The alternative — start the agent and hope the shims are
  there — makes a missing file look like a guarded run.
- **The managed-bin directory is resolved the way pi resolves it.** pi
  prepends `getBinDir()` — `join(getAgentDir(), "bin")` — to the `PATH` of
  every command its bash tool runs (`dist/utils/shell.js:147-157`, passed at
  `dist/core/tools/bash.js:78` and `:160/:287`), so a `pkill` downloaded there
  would be found **before** the shim. `getAgentDir()` is
  `$PI_CODING_AGENT_DIR` (`dist/config.js:510-515`) run through
  `normalizePath` (`dist/utils/paths.js:100-124`), which expands a leading
  `~/` against the home directory; bash does not tilde-expand a variable's
  value, so a literal test of the variable would stat nothing, launch, and let
  pi put the real expanded directory ahead of the shims — silently defeated
  with nothing red. The value is therefore expanded against `$HOME`, and a
  value that is still not absolute is refused: pi joins it to **its own**
  working directory (the worktree), which this wrapper cannot stat as one path,
  so no reading of it can decide the launch.

The inherited `PATH` is not pasted on blind: `PATH=$pi_shims_dir${PATH:+:$PATH}`
and not `${PATH-}`, because an unset or empty `PATH` would otherwise hand the
child a trailing empty field, and an empty `PATH` field means the **current
directory** — the agent's own worktree — both to the shim's lookup and to every
command its bash tool runs.

The shims themselves are mechanical refusals for ordinary `PATH` spellings,
**not a security boundary**. The current tracked launcher imposes the user+PID
namespace described below. With those guard assets unchanged, it blocks that
command's signal attempts to outer PIDs. The checkout is
writable by the same user as the agent, so this is an accidental-command guard,
not tamper-resistant protection against an agent that edits the guard files.
The shims still refuse `pkill`, `killall`, and signalling `pgrep`, and their
managed-bin shadow check still fails at launch rather than hoping a download
does not take precedence.

How each of those can go red is in `tests/test-pi-impl.sh`: the fake pi
asserts with `command -v` that each spelling resolves **inside** the shim
directory (else `GUARD-MISSING`, exit 90) before it calls one, and it calls them
only on a pattern it made random and that matches nothing, so no real
`pkill`/`killall` is ever reached. `check_signal_guard_agent_dir_forms` proves
the `~/` expansion and the relative-value refusal, `check_signal_guard_fail_closed`
the missing/non-executable shim and the managed-bin shadow (all with no pi
invocation and no claimed run name), and the continuation case requires every
guard fact **twice**, because the continuation is a launch too.

### Why implementer bash runs in a user and PID namespace

The PATH shims cannot catch Bash builtins such as `kill`, and a written ban did
not stop a pi agent from running signal probes in the shared devcontainer. The
current launcher therefore starts each implementer Bash execution in a user
and PID namespace. While its tracked guard files are unchanged, this prevents
accidental signal commands issued through that launch path from reaching outer
processes. It is not tamper-resistant: a same-user agent can edit the extension,
wrapper, worker, or launcher and change how a later command starts (CTL-65).

`pi-impl.sh` passes `--no-extensions -e <absolute pi-pidns-extension.js>` on
every implementer invocation. The explicit extension replaces the built-in
`bash` tool with `createBashTool(..., {operations})`, where the operations use
`createLocalBashOperations({shellPath})` with `pi-pidns-bash`. Its `user_bash`
handler returns those same operations for `!` and `!!`. Pi 1.6.0 documents that
`user_bash` fires for those commands and that returned operations handle them
(`docs/extensions.md:1078-1095`); same-name extension tools replace built-ins
(`docs/extensions.md:2358-2360`). With `--no-extensions`, the resource loader
selects only CLI extension paths (`dist/core/resource-loader.js:426-428`), so a
worktree's `.pi/settings.json` cannot load a later extension; the explicit
extension installs its own `shellPath` via the local operations factory
(`dist/core/tools/bash.js:156-157`). Implementer `PI_EXTRA_ARGS` cannot add or
disable extension flags. No user or worktree settings file is edited. Review
mode is unchanged and has no bash tool.

Before the run name is claimed, `pi-impl.sh` calls the wrapper with `-c true`.
A missing wrapper or extension, a missing bwrap executable, or a namespace
setup failure refuses with exit 2 and leaves the name unclaimed. Each actual
command uses these bubblewrap namespace and root-bind options, plus private
tmpfs mounts over `${TMUX_TMPDIR:-/tmp}/tmux-<uid>` and `/run/docker-api`:

```text
bwrap --unshare-user --unshare-pid --die-with-parent --dev-bind / /
```

The wrapper unsets `TMUX`, `TMUX_PANE`, and `DOCKER_HOST`, and covers an
existing default or `DOCKER_HOST=unix://` Docker socket outside
`/run/docker-api` with `/dev/null`. A `--json-status-fd` child-start record lets
the wrapper distinguish a setup failure from a command's own exit code.

With the tracked guard assets unchanged, the namespace blocks PID-based signals
from that pi bash process to processes outside its PID namespace. The same-user
edit limitation above means this does not guarantee that future commands will
continue to use the guard. The mount namespace does not confine file writes,
network traffic, or other Unix sockets. `/proc` stays the outer procfs because
mounting `--proc /proc` fails with `Operation not permitted` in the supported
container;
process listings therefore show outer PIDs and `/proc/$$` does not match the
namespace shell. PID 1 inside is bwrap, so `kill -0 1` succeeds by design.
`sudo` fails inside, and background jobs end with their command. These are
documented limits, not an expanded filesystem sandbox.

### Why a review mode is the wrapper's own switch, and sticky

A review and an implementation run the SAME wrapper, and the difference between
them is what the agent may DO. It used to be a caller convention: `pi-review.sh`
exported a read-only tool list through `pi-impl.sh`'s `PI_EXTRA_ARGS` seam, and
every other path — a review launched straight through `pi-impl.sh`, or a review
RESUMED through it — got the full implementer tool set unless the caller
remembered. That is the same shape as the signal guard's defect: a rule that
holds only while someone remembers it, and a pi reviewer already spent two hours
running kill/trap scripts in the shared devcontainer when it did not.

The mode is therefore `pi-impl.sh`'s own switch:

- **The tool set is the wrapper's argv, not a caller's variable.** Review mode
  reads `pi-review-argv` once and appends it to `base_pi_args`, so the first run,
  a manual `resume` and every automatic continuation carry the same element.
  The list has ONE definition, in that file; `pi-review.sh` sets the mode and
  carries no copy of it. Rejected: a second copy of the literal list in the
  launcher (two definitions drift, and the drift is invisible), and a
  `--review` flag on `pi-impl.sh` (a new reserved argv element that a
  `PI_EXTRA_ARGS` value or a stale wrapper invocation would have to keep
  in step).
- **The seam is refused, not classified.** In review mode any non-empty
  `PI_EXTRA_ARGS` is an error (exit 2), and every spelling pi accepts for a tool
  selection, an extension, an MCP server, a skill, a prompt template or project
  trust (`--tools`, `-t`, `--exclude-tools`, `-xt`, `--no-tools`, `-nt`,
  `--no-builtin-tools`, `-nbt`, `--extension`, `-e`, `--no-extensions`, `-ne`,
  `--no-mcp`, `--skill`, `--no-skills`, `-ns`, `--prompt-template`,
  `--no-prompt-templates`, `-np`, `--approve`, `-a`, `--no-approve`, `-na`) is
  named in the refusal first, so the message says which one. The general refusal
  behind it exists because the alternative — a whitelist of "harmless" additions
  — is a vocabulary that must be kept in step with pi, and a spelling it misses
  fails silently. Rejected: allowing an "equal or narrower" seam, which cannot
  be decided without re-implementing pi's own tool merge.
- **The mode follows both the run name and the pi session.** Every accepted
  launch writes `<run>.mode`; a resume records its known ID in
  `<runs-dir>/session-modes/<session-id>` before invoking pi, while a first
  launch records it after pi returns with the ID. A resume requires the exact id
  in a session-mode record, `.thread`, or a session event in the first 64 KiB
  of a run's `.jsonl`. The bounded head scan covers a still-running or killed
  first review before `.thread` or session-mode state exists. It then reads the
  matching names' mode records and review material. Forced reuse persists the
  old session mode before retiring the name's `.thread` and `.jsonl`. This
  covers a review resumed under a fresh name and sessions created before
  session records existed. If any source says `review`,
  the launch is a review; a caller that ASKS for implementer mode is refused
  (exit 2, before the new name is claimed when its review record is already
  visible); the name is re-read after claim to catch a record written during
  preflight. Resume ids match full recorded ids exactly: prefixes and ids ending
  `.jsonl` are refused. A resume with no named mode takes review. Session writes
  use a lock and move only toward review, so a later
  implementer launch cannot overwrite it. An implementer name or session may be
  resumed STRICTER; its records then become `review`.
- **Missing or unreadable: the safe direction only where the fact exists.** An
  unreadable or invalid name, session, or matching-name mode record is refused —
  no correct writer produces it, and guessing which word was meant is a fallback
  that cannot fail loudly. A missing name record is not the same fact: a name
  carrying `<run>.review/before-state.txt` (written by `pi-review.sh` before its
  launch) is known to be a review and is resumed as one. A pre-existing
  implementer session without review evidence or any matching review record
  resumes unchanged rather than being turned into a review by a refusal it
  never asked for.
- **A host file is a default, not a request.** `controller.local.env` is sourced
  with `set -a`, so a `PI_RUN_MODE=implementer` line in it would otherwise decide
  every launch. The caller's own value is saved before that file is sourced and
  re-applied after, so a caller's review is never flipped; a value that only the
  file supplied is treated as the default for launches that name no mode, and on
  a resume against a record it is printed and NOT obeyed. Rejected: refusing any
  local value (it breaks hosts that put their default there) and letting it
  override a record (a silent widening of a review).

### Why every pi prompt carries a rule paragraph, and where enforcement applies

Both modes append a fixed paragraph to every prompt: the implementer one names
the pattern-kill ban and confines signal/TERM-trap tests to a capped container
with a private PID namespace, against PIDs the test recorded itself; the review
one says the run executes nothing at all. The paragraph is a tracked file beside
the script (`implementer-prompt-rules.md`, `review-prompt-rules.md`), appended to
every launch and to the automatic continuation, and a launch whose file is
missing is refused: a prompt that lost the rule is not the contract the wrapper
promises, so the absence must be loud rather than silent.

The prompt's ban on shell experiments in the shared devcontainer remains an
INSTRUCTION. With the tracked guard assets unchanged, each implementer bash
command, including a builtin `kill` or a direct signal syscall, runs in a fresh
user and PID namespace and cannot address outer processes by PID. A same-user
agent can edit those assets and change a later command's launch path (CTL-65),
so this prevents accidental signals through the current guard without claiming
tamper resistance. The `pi-shims` PATH guard still refuses ordinary `pkill`,
`killall`, and signalling `pgrep` spellings. Review mode is
tool-set enforcement (no tool that can execute or write), not a sandbox, and
even it leaves `read` unconfined. The namespace also leaves filesystem writes,
network traffic, and other Unix sockets available; only the tmux socket
directory, `/run/docker-api`, and the identified Docker socket paths are hidden.

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

**Every exec site, not just the first one.** A `codex-impl.sh` run has THREE
places where it execs a child — the new launch, a manual `resume`, and the
automatic continuation loop — and only the first was covered by a SIGKILL
check for a while. The resume path is the ROUTINE one: every follow-up of a
long job runs it, and a killed resume whose child inherited the descriptor
strands the run name exactly as a killed new launch does. So the check kills a
wrapper at EACH site, with the agent and the setsid daemon alive, and asserts
the name is free at once and a forced relaunch is accepted — and a mutant
deletes the close from ONE line at a time, so a fix that covered only the
obvious site fails.

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

**Scope of the tool's subtree (by design, CTL-24).** The processes whose
counters count as the tool's progress are ALL descendants of the agent, not only
the ones the tool call started: the sidecar locates the set structurally (the
agent is the wrapper's other direct child) and cannot tell which descendant
belongs to which call. A long-lived helper the agent keeps as a child — a
language server, a watcher, a dev server — therefore sits in the set and, as a
survivor that does work, reads as progress. That errs toward `LONG-TOOL`, the
weaker word, so the controller waits longer on a tool that is really stuck; it
never kills a tool that is working, which is the safe direction for a word that
decides whether an agent is stopped. Narrowing the set to the single call's
processes would need the agent's own call-to-pid mapping, which the stream does
not carry, so it is documented rather than built.

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

The owner record belongs to a launch that is REAL (CTL-23). The claim takes the
name's lock and creates the lock file empty; the wrapper writes its identity
into it only after every refusal that can still happen (a bad route, a bad
continuation count, blocked accounts, a missing credential file or key), and
`pi-impl.sh` touches the name's own `.wt`, `.route`, `.thread` and `.done`
records only after the same point. A launch refused earlier therefore leaves the
previous owner's identity and the previous run's records byte-identical, so a
name that never started cannot read as `DIED`, and a refused first launch leaves
no evidence that would refuse its retry. What it does leave is the empty lock
file, which `job-check.sh` reads as its own state: an empty lock, no stream and
a lock nobody holds is `NOT-STARTED` (red, exit 1); the same empty lock while a
wrapper holds it is that wrapper still preflighting and stays `RUNNING`; a lock
with unparsable content, or an empty one beside a stream, keeps the
no-owner-record wording because nothing says the name was only claimed. The same
kernel-lock probe is applied whenever the recorded owner is dead: a resume of a
`DIED` name holds the lock while its record still names the previous owner, and
that reads `RUNNING (preflighting, previous owner gone)`, not `DIED`; the same
record with a free lock is `DIED`. `CONTROLLER_RUN_FORCE=1` follows the same
rule: the evidence preflight only decides that a retirement is wanted, and the
retirement into `retired/<run>/<utc-stamp>/` runs together with the owner
record, once the launch is real, so a forced launch that is refused moves
nothing. Known limit (CTL-25): the lock probe itself takes `flock -n` for about a
millisecond, so a claim landing in that window is refused as `already owned by
pid <unknown>`.
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

## Gate slot scheduling (two slots by default)

`gate-run.sh` requires two distinct absolute lock paths from local controller
configuration (`CONTROLLER_GATE_SLOTS`, 1 to 8, changes the count; the default
stays 2 because the controller devcontainer is memory capped and was OOM-killed once;
the slot lock rule is in the README). Its refusal names `controller.local.env.example`,
`CONTROLLER_GATE_LOCK`, and `CONTROLLER_GATE2_LOCK`. It tries each slot without
waiting; when all are occupied it polls all locks with non-blocking attempts
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
`readback-state-<session_id>.json` (state is per session), because the next
session reads that state, not the log.
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
- **One hook, one threshold per kind, no number compiled in.** The tracked hook
replaced an untracked 203-line copy that told the controller "auto-compaction is
the only limit: never stop" (it contradicts the operator's 2026-11-18 handover
decision: auto-compaction is not a safe handover) and hard-coded a 500k ceiling.
Since 2026-11-24 (operator) no ceiling and no `autoCompactWindow` enter the policy:
the named thresholds are the one place the wording changes. Below 200k the controller
persists at boundaries, from 200k (urgent from 250k) it writes the handover and launches
`compact-resume.sh` itself, and from 300k a `PreToolUse` deny forces that after a
5-call grace; an agent finishes or returns PARTIAL from its own threshold. The read
order after a compaction is the READ FIRST line of the newest handover block,
so a handover edit needs no hook edit.
- **State, not a checkout path.** The state directory comes from a flag, then
`CTX_STATE_DIR`, then the nearest `.controller-state` at or above the hook
input's `cwd` (a controller that `cd`s into a worktree keeps its state). No
repository path, transcript path or 30000/10000 budget is compiled in; the
budgets are flags/env with those documented defaults.
- **The numbering is crash-safe.** `readback-<session_id>-N.md` takes one past
the session's highest number ON DISK, not one past a counter in a state file, so a state file lost to
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
