# Controller toolkit

This is the reusable controller kit for starting implementation and review
jobs, checking their evidence, coordinating releases, and operating the
host-satellite Run-Gate queue. Runtime files and local machine facts live under
`$CONTROLLER_HOME` (default `$HOME/controller`); see
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

## Set up once

```bash
mkdir -p "$HOME/controller"
cp scripts/controller/controller.local.env.example "$HOME/controller/controller.local.env"
cp scripts/controller/hosts.local.json.example "$HOME/controller/hosts.local.json"
chmod 600 "$HOME/controller/controller.local.env" "$HOME/controller/hosts.local.json"
```

Fill in the controller environment, host SSH argv, provider identity command,
remote guard, and queue lane assignments. These local files are ignored by
Git. Never put a credential value in a command line, report, or tracked file.
Set `CONTROLLER_REPO` in the local environment file before installing the
queue under `CONTROLLER_HOME/bin`.
Each `queue_lanes` value must contain exactly one `|` with a non-empty project
and lane on each side; malformed entries are rejected before a queue starts.
For the host-table schema and the additional `queue_lanes` field, see
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) and the
[queue validation design](docs/DESIGN-GUIDE.md#queue-lane-validation).

## Controller loop

Use this order for each implementation task.

1. **Read status.** Run `scripts/controller/ctl-status.sh`. It reports the
   checkout, worktrees, recent controller runs and release logs, host queue
   state, pause reasons, and recent results. It does not fetch, SSH, start
   processes, or change state.
2. **Launch implementation.** Put the brief at an absolute path. Select the
   route in the caller's environment, then launch the matching wrapper. For a
   Luna Codex task:

   ```bash
   CONTROLLER_HOME="$HOME/controller" CODEX_ROUTE=luna \
     scripts/controller/codex-impl.sh /path/to/worktree /absolute/path/brief.md task-run
   ```

   Pi is the favored Open Design DeepSeek route. Start the wrapper detached so
   the controller can return immediately; it writes run evidence under
   `$CONTROLLER_RUNS_DIR`:

   ```bash
   nohup env CONTROLLER_HOME="$HOME/controller" PI_ROUTE=deepseek-pi \
     scripts/controller/pi-impl.sh /path/to/worktree /absolute/path/brief.md pi-run \
     --report /absolute/path/to/report.md \
     > "$HOME/controller/logs/pi-run.launch.log" 2>&1 < /dev/null &
   ```

   Pi may exit zero after a provider 402. The wrapper classifies the final
   assistant event and records `OUTCOME=USAGE-LIMIT` in `.done` while blocking
   the selected account until its reset. Pass the brief's report path with
   `--report` (or `PI_REPORT`): the wrapper can then tell `OUTCOME=NO-REPORT`
   from `OUTCOME=SUCCESS`. The full vocabulary is in the
   [Pi section](#pi-on-open-design).

   Use `CODEX_ROUTE=deepseek` to select an Open Design account automatically,
   or pin one with `CODEX_ROUTE=deepseek-volker` or
   `CODEX_ROUTE=deepseek-github2`. OpenCode is available through
   `scripts/controller/opencode-impl.sh`. Codex stores its run
   stream and completion marker in `$CONTROLLER_RUNS_DIR` (default
   `$CONTROLLER_HOME/runs/`); OpenCode uses `$CONTROLLER_HOME/runs/`. Run long
   jobs detached and read the `.done` marker to learn the child process exit
   status. Codex resumes a run on the same
   thread up to `CODEX_AUTO_CONTINUE` times after a reasoning-only completed
   turn; the default is two for DeepSeek and zero for Luna. The `.done` marker
   records `EXIT=<n> OUTCOME=CODE-EXIT AUTORESUME=<k>`, and the attempt log is
   `<run>.autoresume.log`. A new run name with an existing `<run>.done`,
   `<run>.jsonl`, or `<run>.err` record is refused before the child starts and
   all existing paths are printed. `CONTROLLER_RUN_FORCE=1` permits replacing
   evidence for a new run that was intentionally abandoned or failed. Resume
   mode does not take that new-run guard: it continues the same thread over an
   existing stream and writes a fresh completion marker when it finishes.3. **Check the result.** Run `scripts/controller/job-check.sh` with the run
   name, exact worktree path, route, and absolute report paths. It reports runs
   without `.done` as running; for completed runs it checks intent-only
   endings, reasoning-end, usage-limit retry text, missing turn completion,
   tool-call text, report completeness and HEAD binding, worktree cleanliness,
   final-head gate evidence, and the route trailer on every commit, including
   merges. For a pi stream it also names `CONTEXT-FULL` (assistant
   `stopReason=length`) and `TOOL-CALL-NO-TEXT` (a settled stop ending on a tool
   call with no text), so neither reads as an ordinary running or finished
   job. The last lane verdict in a bound gate log must be
   `run-gate: lane ... verdict PASS`; an earlier PASS line cannot override a
   final FAIL. The prototype's `any`
   route remains supported for the accepted Codex and Claude trailers;
   multiple recognized agent trailers on one commit are reported as `INFO`
   instead of a failure. It also reports untracked files under `reports/` and
   `scripts/*/reports/`. A collected Run-Gate container can bind evidence only
   when its name contains the current eight-character head prefix, an earlier
   log has the matching full `run-gate.commit` label on a line naming that
   container, and a supplied report names the same container. Without
   `.done`, it reads the run name's own owner record (`<run>.lock`, written by
   every wrapper when it claims the name, CTL-14): a live owner prints
   `RUNNING (no .done)` with its pid, a recorded owner that is gone (or whose
   kernel start time no longer matches, or that was recorded under a previous
   boot) prints `DIED (wrapper pid <pid> gone, no .done)` as a red flag, and a
   run that no wrapper ever owned prints `RUNNING (no .done)` plus
   `owner record: none`, stating that a live and a dead wrapper cannot be told
   apart there. A `DIED` row is never evidence that the child completed — it is
   evidence that the wrapper is gone, so the controller can relaunch. A killed
   wrapper often leaves no `<run>.thread`; the reader recovers the session/thread
   id from the run's own stream (pi's first `session` event, Codex's
   `thread.started`), prints `thread: <id> (recovered …)` as a fact, and prints
   the exact `resume:` command it enables — or `thread: none` when the stream
   carries no id, rather than an invented one. See the
   [job-check design](docs/DESIGN-GUIDE.md#job-check-compatibility) for the
   accepted forms and completion rules. Usage-limit detection accepts a
   gateway `insufficient_balance` reset timestamp, a time-only retry, and a
   month-name date with an ordinal day and 12-hour time; month-name text is
   printed verbatim because its message carries no timezone.
4. **Review on another route.** Give an independent reviewer the exact commit
   and a read-only review prompt. Do not have an implementer review its own
   change. For a Luna implementation, `codex-review.sh` defaults to the Sol
   review profile; for a DeepSeek or OpenCode implementation, select Luna or a
   separate review session. Keep the review report at an absolute path.
5. **Update the work branch.** Commit only the task's paths, with exactly one
   route trailer on each commit. After implementation and review fixes, fetch
   fresh `origin/main` and merge it into the task branch as the final content
   change when the task contract calls for that update. Resolve conflicts on
   the task branch and put the same route trailer on the merge commit. Do not
   merge the task branch into the shared main checkout from this toolkit.
6. **Run gates last.** With the final head and a clean tree, run the registered
   `run-gate` lane through the project workflow. Use the shared gate lock. The
   gate log must identify the same commit as `git rev-parse HEAD`. If code or
   documentation changes after the gate, rerun the lane.
7. **Release through the project.** Use the project's supported release
   workflow, normally `cmru release` via `release-from-wt.sh`; do not emulate
   release, cleanup, tag, or publication operations with raw shell or Git
   commands. Select the project engine venv in `CMRU_VENV` or with `--venv`.
   On a `rel-*` branch, the wrapper archives existing project `logs/`,
   `artifacts/`, and `evidence/` under `$CONTROLLER_HOME/releases-archive/`
   before invoking release.
8. **Do host work last.** Use `hx.sh` for identity-checked SSH. For an
   exclusive host operation, pause the matching queue, wait for its
   `<host>.current` marker to clear, then do the host operation and resume.
   Follow the runner guide for queue details and the atomic queue update steps
   below.

## Recover or schedule Codex work

After a usage-limit failure, the resume helper scans recent completed runs in
`$CONTROLLER_HOME/runs/`. It resumes eligible runs on their recorded thread,
explicit `<run>.wt` worktree map, and route; it does not infer a worktree from
the event stream. A dated retry that is still in the future is skipped with
`SKIP <run> (limit until <text>)`; the helper uses GNU `date -d` after removing
the ordinal suffix and never guesses a timezone. A time-only retry retains the
immediate-resume behavior because it has no date to compare. Missing or invalid
maps are skipped. By default it excludes `naf-*` runs owned by another controller;
configure the space-separated `RESUME_EXCLUDE_PREFIXES` list to change that.
It skips a live `<run>-resumed` wrapper and holds a per-run lock through the
resume to prevent concurrent launches. Use `--dry-run` to inspect the
candidates and `--skip RUN` to exclude one:

```bash
scripts/controller/resume-usage-limited.sh --dry-run --skip example-run
scripts/controller/resume-usage-limited.sh --skip example-run
```

Set `CONTROLLER_RUNS_DIR` or pass `--runs-dir DIR` to scan another runs
directory. `RESUME_SKIP` accepts a space-separated list, and
`RESUME_MAX_AGE_MINUTES` controls the recent-run window. To schedule one launch
for a UTC time on the current day, use:

```bash
scripts/controller/start-after.sh 1430 /path/to/worktree /absolute/path/to/brief.md scheduled-run
```

See the [recovery and scheduling design](docs/DESIGN-GUIDE.md#recover-and-schedule-codex-work)
for the safety checks.

## Open Design account selection

| Route | Launcher | Account behavior |
|---|---|---|
| `deepseek-pi` | `pi-impl.sh` | Favored Open Design route; alternates available accounts. |
| `deepseek-pi-volker`, `deepseek-pi-github2` | `pi-impl.sh` | Pi pinned to the named account. |
| `deepseek` | `codex-impl.sh` | Codex automatically selects an Open Design account. |
| `deepseek-volker`, `deepseek-github2` | `codex-impl.sh` | Codex pinned to the named account. |
| `opencode` | `opencode-impl.sh` | OpenCode using its configured Open Design provider. |
| `luna` | `codex-impl.sh` | Luna Codex route. |

The existing `luna` route remains available for side-by-side implementation
runs while the favored Pi route gathers operating experience.

The `deepseek` route uses the two locally configured Codex homes:
`$HOME/.codex-opendesign-volker` and `$HOME/.codex-opendesign-github2`. Set
`CODEX_HOME_DEEPSEEK_VOLKER` or `CODEX_HOME_DEEPSEEK_GITHUB2` to override either
home. Both homes must have their own catalog with a null `tool_mode`; the
selected home's catalog is checked before Codex starts. The model defaults to
`deepseek-v4.1-flash-eco` at `high` effort.

The gateway has no quota endpoint, so the wrapper never probes it. It reads
only its local state and chooses the available account with the oldest
`last_used_epoch`; the start is serialized with `flock` so automatic starts
alternate safely. `deepseek-volker` and `deepseek-github2` always pin the
named account. A pin does not fail over. Codex reads the key through the
selected home and its `env_key` configuration; the wrapper does not read
`.env` or `auth.json`, and does not place key values in command arguments,
run evidence, reports, or account state.

The state file defaults to
`$CONTROLLER_RUNS_DIR/opendesign-accounts.json`. Set `CONTROLLER_OD_STATE` to
an absolute path to move it. It contains account names and numeric
`last_used_epoch` and `blocked_until_epoch` values, plus an optional
`window_end_epoch` per account and a `threads` map from Codex thread ids or Pi
session ids to their Open Design account. Thread mappings let a manual
`CODEX_ROUTE=deepseek ... resume <thread>` or `PI_ROUTE=deepseek-pi ... resume
<session-id>` resume on the account that started that conversation. If no run
or state mapping exists, the wrapper says so and picks an account
automatically. A later resume updates the mapping to the account it uses.

A 402 `insufficient_balance` response with a parseable `reset_at` value records
that reset for the account used by the run. If the gateway reports 402 but the
reset time is missing or malformed, the wrapper logs that the reset time is
unknown and records a temporary block for six hours. Set the positive integer
`CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS` to change the default `21600` second
block. A later block is never shortened by a new response with an earlier
reset time. Invalid overrides log a warning and use the six-hour default. Codex
prints the real 402 as a final plain-text `error`/`turn.failed` message
(`unexpected status 402 Payment Required: [code=insufficient_balance] ... resets
at <UTC time>`), which is recognized too; `Reconnecting...` retries and text
inside agent or tool output are not. Automatic
selection skips an account until that time; if both are blocked, the wrapper
exits 3 and reports the earliest reset. Usage-limit recovery resumes on the
account in the run's `.route` sidecar. If that account is still blocked, it
uses the other available account and logs the fallback.

To use credits that would otherwise expire, fill in each account's
`window_end_epoch` in the state file using the dashboard or an observed
`reset_at` plus whole 7-day windows, then set
`CONTROLLER_OD_PREFER_EXPIRING=1`. Selection prefers the available account
whose recorded window ends soonest, with `last_used_epoch` breaking ties. The
preference is off by default because the wrapper cannot see remaining quota.
The editable state shape is:

```json
{
  "volker": {"last_used_epoch": 0, "blocked_until_epoch": 0},
  "github2": {"last_used_epoch": 0, "blocked_until_epoch": 0},
  "threads": {"codex-thread-id": "volker"}
}
```

Add a numeric `window_end_epoch` field to an account when recording its
current weekly window end. The state schema rejects unknown fields so account
keys cannot be stored there by mistake.

`CODEX_HOME_OVERRIDE` is honoured only on the luna route.

### Pi on Open Design

`PI_ROUTE=deepseek-pi` selects between the same two Open Design accounts using
the shared account state and alternation logic. Use
`PI_ROUTE=deepseek-pi-volker` or `PI_ROUTE=deepseek-pi-github2` to pin an
account. The default model is `deepseek-v4.1-flash-eco` at `high` thinking;
override these with `PI_MODEL` and `PI_THINKING`. Pi receives the selected
provider/model pair (`opendesign_volker/<model>` or
`opendesign_github2/<model>`).

Pi reads its provider definitions from the operator-owned
`$HOME/.pi/agent/models.json`. The wrapper sources `$HOME/.env.ai` in its own
shell, exports only the selected `OPENDESIGN_API_KEY_*` variable to pi, and
does not print or record key values. Do not put credentials in the controller
environment example or run files. The session directory defaults to
`$HOME/.pi/agent/sessions`; set `PI_SESSION_DIR` to override it.

The pi event stream is saved as `<run>.jsonl`, stderr as `<run>.err`, the last
assistant text as `<run>.last.txt`, and the worktree, selected route, and pi
session id as `<run>.wt`, `<run>.route`, and `<run>.thread`. A run NAME is
owned by one live wrapper at a time: `<run>.lock` is an exclusive `flock` the
wrapper takes before it writes any record and releases on every exit path
(the kernel releases it when the owner dies, so a killed wrapper cannot strand
it). A second invocation — a relaunch while the first is alive, or a `resume`
of the same run — is refused with exit 2 and names the live owner pid. A
`CODEOWNER`-style force is not offered: `CONTROLLER_RUN_FORCE=1` only retires a
DEAD owner's records, moving the whole record set into
`retired/<run>/<utc-stamp>/` (never deleting it) and reporting the retired
owner, so the name can be reused deliberately. The first `.done`
line keeps its historical shape `EXIT=<n> OUTCOME=<word>`, where the word is:

| Outcome | Meaning |
|---|---|
| `SUCCESS` | settled stream, session id, final `stopReason` of `stop`, assistant text present, worktree clean, and the expected report present when one was named. |
| `EMPTY-COMPLETION` | exit zero and the final assistant message carried no text (empty or thinking-only) and no tool call: a provider/model anomaly, not a limit, and not a success. The wrapper continues the same session first (see below), so this word is only the intermediate state. |
| `EMPTY-COMPLETION-RETRIES-EXHAUSTED` | still empty after two recorded continuations; the controller decides whether to restart or abandon. |
| `UNCOMMITTED` | exit zero with uncommitted changes in the worktree. |
| `NO-REPORT` | exit zero but the expected report file is missing. |
| `USAGE-LIMIT` | a provider 402 `insufficient_balance` stop; the account is blocked until its reset. |
| `TOOL-CALL-NO-TEXT` | a settled `stop` whose final assistant message is a tool call with no text: the run ended mid-work. Not a success, and not the continued `EMPTY-COMPLETION` class. A message carrying TEXT **and** a trailing tool call is `SUCCESS` instead: the text is the run's final message and the call is bookkeeping pi emits in the same message. |
| `CONTEXT-FULL` | a settled `stopReason` of `length`: the context window filled. The work is intact; start a FRESH successor from the run's forward-oriented state file. The wrapper never continues it. |
| `ERROR` | a provider error stop, or a nonzero pi exit. |
| `INCOMPLETE` | a stream the classifier cannot attribute (unsettled, aborted, a `stopReason` it does not know), or a finish whose worktree status cannot be read (never read as clean). |

Name the expected report with `--report <absolute-path>` or `PI_REPORT`;
leaving it unset skips the report check rather than passing it. Every other
class is a fact read from the stream, `git`, or that path, and a worktree that
cannot answer `git rev-parse` is refused before pi starts. Non-interactive pi
requires closed stdin; the wrapper always redirects it from `/dev/null`.

An `EMPTY-COMPLETION` is the one class the wrapper continues by itself: it
resumes the same pi session with the canonical continuation prompt in
`continue-empty-completion.md`, appends the continuation stream to `<run>.jsonl`
and records one JSON line per attempt in `<run>.events`. The count defaults to
two and is set by `PI_EMPTY_RETRY_LIMIT`; the `.done` marker then adds a
`RETRIES=<n>` line. No other class is ever continued, and nothing is retried on
a tool-call, turn or token count.

Resume with the recorded session id and automatic route:

```bash
PI_ROUTE=deepseek-pi scripts/controller/pi-impl.sh \
  /path/to/worktree /absolute/path/brief.md pi-resume resume <session-id>
```

The wrapper resumes on the account recorded for that session. If it has become
blocked, the shared helper selects the other available account and logs the
fallback; if both accounts are blocked, it exits 3 before starting pi. A pi
402 is detected from the final assistant `message_end` event even though pi
exits 0. Its `errorMessage` reset is recorded by the account helper; an
unparseable reset uses the existing bounded unknown-reset default. No quota
probe is made before a start.

See the [design guide](docs/DESIGN-GUIDE.md#pi-open-design-route) for the
reason Pi runs are classified from the event stream and how its session id
joins the shared account map. The [consumer guide](docs/CONSUMERS.md#run-pi-on-open-design)
has a copyable launch and resume example.

Check a completed Pi run with its route name, and watch an active one from
its liveness sidecar while it runs:

```bash
scripts/controller/job-check.sh pi-run /path/to/worktree deepseek-pi
scripts/controller/job-status.sh pi-run
scripts/controller/ctl-status.sh
```

While a run is active the wrapper also keeps `<run>.live` current (default
every 30 s, `PI_LIVE_INTERVAL_SECONDS`): pid liveness, seconds since the last
pi event, the tool call in flight with its age, command and the child pid to
steer, assistant turns, tool calls, the last non-zero `usage.totalTokens`
measurement (with the last usage report beside it as its own fact), progress
evidence, commits ahead of the base ref, uncommitted files, and the age of the
run's own last commit. `job-status.sh` reads those files and prints one line per
run with `ACTIVE`, `STALLED` (no pi event for longer than
`JOB_STATUS_STALL_SECONDS`, default 600, while the pid is alive), `LONG-TOOL`
(one tool call in flight longer than `JOB_STATUS_HUNG_TOOL_SECONDS`, default
1800, while the run still shows progress evidence), `HUNG-TOOL` (the same age
with no progress evidence), `WRAPPER-DEAD`, `FINISHED`, or `NO-LIVE`. It never
writes.

`WRAPPER-DEAD` is decided from the run name's owner record — the same
`<run>.lock` `job-check.sh` reads — never from `kill -0` on the pid a snapshot
happened to carry: a recycled pid answers "alive" for a stranger, and a record
written before a reboot cannot name a live process at all. When the wrapper is
gone the row also says whether the AGENT it started is still working
(`wrapper-dead-agent-alive agent=<pid>(<command>) tool=<pid>`): that is a
different state from "nothing is running", and it is the only one where the
controller can still name a process to stop. The reader finds it structurally
(the process whose cwd IS the run's worktree and whose argv[0] is `pi` or
`codex`), never by guessing a name. `last-event` then comes from the run
stream's own mtime and says so (`last-event=42s(stream)`), because a dead
wrapper's sidecar is stale by definition.

**`LONG-TOOL` and `HUNG-TOOL` are different words on purpose**: age is not
progress. Progress evidence is read, not inferred: bytes appended to the run's
event stream, or a strictly positive growth of the CPU-time and IO counters over
the same processes of the IN-FLIGHT TOOL CALL's own subtree — never the
wrapper's whole tree, which also holds the agent (a model loop) and the
snapshotter (its own `/proc` scans and git calls) and would therefore keep any
tool looking busy, so a slow `grep` or a long `pytest` reads as a working job
instead of a hung one while a tool blocked on the agent's FIFO still reads as
not progressing. To steer a stuck tool, kill the exact child pid the row
prints as `steer=` — never the wrapper, which would discard the run's
classification, its `.done` marker and its continuation history. A tool blocked
by the agent's own experiment (a `git` command waiting on a FIFO the agent
created) also shows no progress, and its command and child pids are on the row
so the controller can decide which one to kill.

`since-commit` names whose commit the age belongs to: a run with no commit of
its own prints `since-commit=no-commits-yet`, a run with `ahead=` commits prints
`since-commit=123s(ahead=2)`, and an unreadable `commits_ahead` prints
`since-commit=null` rather than someone else's commit age.

OpenCode defaults to `opendesign_github2/deepseek-v4.1-flash-eco`. Set
`OPENCODE_PROVIDER=opendesign_volker` to choose the other provider, or set
`OPENCODE_MODEL` to a full provider/model identifier. On the command line,
choose a provider and model with `opencode run -m <provider>/<model>`.

The reusable brief format is [`BRIEF-TEMPLATE.md`](BRIEF-TEMPLATE.md), and
implementation-job constraints are in [`rules.md`](rules.md). The two
finisher prompts live beside them. Read the [design guide](docs/DESIGN-GUIDE.md)
for the reasons behind the boundaries, and the [consumer guide](docs/CONSUMERS.md)
for copyable setup and run examples.

## Running the registered gate

The toolkit's `suite` lane is declared in `run-gate.toml`. Run it from this
directory under the estate gate lock:

```bash
cd scripts/controller
CONTROLLER_HOME=${CONTROLLER_HOME:-"$HOME/controller"}
. "$CONTROLLER_HOME/controller.local.env"
./gate-run.sh suite
```

The lane runs the deterministic fake-root shell suite in `tests/` and the
queue tests in `scripts/netcup/runner/tests/`. It runs `bash -n` on the kit and
uses ShellCheck when the tester image provides it. The two slot lock paths
come from `CONTROLLER_GATE_LOCK` and `CONTROLLER_GATE2_LOCK`; see
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md). Copy those exact names from
`controller.local.env.example` and export both before invoking the wrapper.

`gate-run.sh` first tries both slots without waiting. If both are busy, it polls
both locks with non-blocking attempts every 5 seconds, takes whichever slot is
freed first, and prints which slot it took. It prints a progress line every
300 seconds while both remain busy. The gate command's exit code is propagated
unchanged, and the configured-path and distinct-path refusals still run before
the gate command can start.

## Build a bounded session read-back

For the controller's own compaction recovery use the operator-mandated recipe in the root `AGENTS.md` (`nyxloom-harness extract --preset readback --budget-tokens N`, which never drops operator turns or interview answers). `build-readback.py` below is the older character-budget builder; it also bundles Codex run finals, but its character cut can truncate operator material, so do not use it alone for compaction recovery.

Pass the session file and runs directory explicitly. `--budget-chars` limits
the complete output across the session extract, returned results, sent
subagent prompts, and Codex run finals:

```bash
scripts/controller/build-readback.py \
  --since-marker SESSION-MARKER \
  --session /path/to/session.jsonl \
  --runs-dir "$CONTROLLER_HOME/runs" \
  --out /absolute/path/to/readback.md \
  --budget-chars 12000
```

The builder invokes `nyxloom-harness extract` from `PATH` and fails if the
marker or harness command is unavailable. See the
[read-back design](docs/DESIGN-GUIDE.md#bounded-session-read-back).
## Compaction recovery hooks

`hooks/ctx-hooks.py` is the controller's Claude Code hook script: it delivers the
context-pressure note, builds the readback from the session transcript before a
compaction, and republishes the read order after one. It never fails the
session: every error, timeout (20 s hard cap) or missing transcript exits 0,
logs one line, and (on SessionStart) prints the exact manual build command
instead.

```bash
# the controller points its own settings at the script; nothing is baked in
printf '%s' '<hook JSON>' | scripts/controller/hooks/ctx-hooks.py pre-compact \
  --state-dir /path/to/.controller-state
```

Subcommands: `statusline`, `post-tool-use`, `pre-compact`, `session-start`,
`stop`. Configuration is flags or environment (`CTX_STATE_DIR`,
`CTX_EXTRACT_CMD`,
`CTX_READBACK_BUDGET_TOKENS` = 30000, `CTX_READBACK_TAIL_PROSE_TOKENS` =
10000, `CTX_READBACK_GOAL_FILE`, `CTX_READBACK_GOAL_PROMPT`,
`CTX_READBACK_REDACT_PATTERNS`, `CTX_SESSION_KIND`, `CTX_READBACK_TIMEOUT`, `CTX_HOOK_LOG`); the
state directory defaults to the hook input's own `cwd/.controller-state`, so no
checkout path is hard-coded. Which harness/situation rows are tested is in
[`docs/design/ctlstat/HARNESS-COMPACTION-CHECKLIST.md`](../../../docs/design/ctlstat/HARNESS-COMPACTION-CHECKLIST.md);
the reason each choice was made is in the
[read-back design](docs/DESIGN-GUIDE.md#compaction-recovery-hooks).

## Installing a queue update

The checked-in `scripts/netcup/runner/host-sat-queue.sh` is canonical. The
controller's installed copy is runtime state and must be updated only after
the repository copy has passed its gate. Never edit the live copy in place:
the running Bash loop can still be reading the script file.

1. **Pause by flag.** Set the per-host pause flag for every affected host:

   ```bash
   queue=${CONTROLLER_QUEUE:-$CONTROLLER_HOME/bin/host-sat-queue.sh}
   bash "$queue" pause host-alias
   ```

   For an update affecting all loops, pause each configured queue host.
2. **Drain before stop.** Keep the pause flag in place. For every host, wait
   until both its `.current` marker is gone and the last queue log line records
   that the loop observed the pause flag. A TERM trap exits the loop immediately
   and can leave a stale `.current` marker, so never stop it with a lane in
   flight. Use this direct predicate in the polling loop:

   ```bash
   current="$CONTROLLER_HOME/logs/host-sat-queue/$host.current"
   log="$CONTROLLER_HOME/logs/host-sat-queue/$host.log"
   while :; do
     last=$(tail -n 1 "$log" 2>/dev/null || true)
     [[ ! -e $current && $last == *'disabled flag present: not launching'* ]] && break
     sleep 2
   done
   ```

   If either fact does not arrive, do not stop the loop. If the marker is
   stale, check the queue PID and log, then query the exact remote-gate run
   through its identity-checked status command. Clear a stale marker only
   after evidence shows there is no active remote run; preserve the result and
   log evidence.
3. **Stop and wait.** Only after the drain predicate succeeds, run
   `stop <host>` and wait until its PID file disappears. If it does not,
   inspect the PID and log; do not replace a script under a live process.
4. **Stage beside the target and rename.** Copy the checked-in file to a
   temporary file in the target directory, validate the staged copy, then use
   `mv` to replace the target atomically. For example:

   ```bash
   . "$CONTROLLER_HOME/controller.local.env"
   target=${CONTROLLER_QUEUE:-$CONTROLLER_HOME/bin/host-sat-queue.sh}
   lib_target="$(dirname -- "$target")/lib.sh"
   candidate=/path/to/vbpub/scripts/netcup/runner/host-sat-queue.sh
   lib_candidate=/path/to/vbpub/scripts/controller/lib.sh
   stamp=$(date -u +%Y%m%dT%H%M%SZ)
   tmp="$target.install-$stamp"
   tmp_lib="$lib_target.install-$stamp"
   mkdir -p "$(dirname -- "$target")"
   install -m 0755 "$candidate" "$tmp"
   install -m 0644 "$lib_candidate" "$tmp_lib"
   bash -n "$tmp"
   bash -n "$tmp_lib"
   mv -f "$tmp_lib" "$lib_target"
   mv -f "$tmp" "$target"
   sha256sum "$target"
   ```

   Set `CONTROLLER_REPO` in `controller.local.env` to the checkout that holds
   `scripts/netcup/runner/remote-gate.py`. Stage every related file before
   replacing any of them. Each rename must be on the target filesystem. Do
   not use `cp` directly over a live script.
5. **Start once and verify.** Start each loop only after all files are
   replaced. Poll for up to 60 seconds for a PID file naming a live process,
   then run `status` and require exit 0 before removing the per-host pause flag.
   Do not start a second loop while a first one may still own the lock.

Use direct file-existence and PID checks in drain loops. Do not build a wait
condition from a pipeline whose status can be changed by `pipefail`, and do
not use a fixed number of sleeps as proof that a marker or process is gone.

## Install the tool skills into the harness skill directories

Each tool carries its agent skill next to itself, so the text versions with the
code: cmru, ciu, pwmcp and fleetd PACKAGE theirs and install with
`cmru skills install` / `ciu skills install` / `pwmcp skills install` /
`fleet skills install` (they stamp their own directory — `.cli-extended-stamp.json`
— and answer `skills check`). assay and run-gate have no `skills` verb, so their
repo-level sources — `run-gate-cli` and `assay-cli`
(`<tool>/.claude/skills/<name>/SKILL.md`) — are copied by this kit instead:

```bash
scripts/controller/skills-sync.sh check      # exit 1 unless every owned skill is current
scripts/controller/skills-sync.sh install    # copy them into ~/.agents/skills and ~/.claude/skills
scripts/controller/skills-sync.sh list       # report state only
```

It is idempotent, prints what it changed, and refuses to overwrite a directory
that does not carry its own `.controller-skills-stamp.json` (an edited copy needs
`--overwrite-modified`, and a source change is reported as `stale`, not as a
local edit). It never touches `~/.claude/skills/synced` or any skill owned by
another tool. `--dest DIR` installs into one directory instead of both, and
`--dry-run` says what it would do. The copy script's own tests are
`tests/test-skills-sync.sh`, part of the controller `suite` lane.

After changing either of those two skill sources, re-run `install` (a stale copy
is what an agent will otherwise read) and run `cmru skills check` and
`ciu skills check` if you also touched their packaged texts.

Why the kit owns a copy step instead of each tool owning a `skills` verb is in
[the design guide](docs/DESIGN-GUIDE.md#copy-hand-written-skills-instead-of-giving-them-a-skills-verb);
the paste-ready procedure is in
[the consumer guide](docs/CONSUMERS.md#install-the-tool-skills-an-agent-will-read).
