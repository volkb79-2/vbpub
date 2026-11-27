# Controller toolkit consumers

## Set up local configuration

From a vbpub checkout, copy the examples and set permissions:

```bash
mkdir -p "$HOME/controller"
cp scripts/controller/controller.local.env.example "$HOME/controller/controller.local.env"
cp scripts/controller/hosts.local.json.example "$HOME/controller/hosts.local.json"
chmod 600 "$HOME/controller/controller.local.env" "$HOME/controller/hosts.local.json"
```

Edit the local files. Replace every host-table placeholder with an accepted
remote-gate alias, SSH argv, provider identity command, remote guard, and
`project|lane` assignment. A lane value must have exactly one `|`, with both
sides non-empty. For example:

```json
{"queue_lanes":["pwmcp|r1","scripts/netcup|suite"]}
```

Set `CONTROLLER_REPO` if you install the queue outside the checkout. Keep keys
and provider tokens in their supported local credential stores. See
[configuration details](CONFIGURATION.md) and the
[queue validation design](DESIGN-GUIDE.md#queue-lane-validation).

## Run and check an implementation job

Use an absolute prompt and report path, then check the completed run against
the exact worktree:

```bash
CONTROLLER_HOME="$HOME/controller" CODEX_ROUTE=luna \
  scripts/controller/codex-impl.sh \
  /path/to/worktree /absolute/path/to/brief.md example-run

scripts/controller/job-check.sh \
  example-run /path/to/worktree luna /absolute/path/to/report.md
```

`job-check.sh` reads `CONTROLLER_HOME`, verifies the child exit marker and
report HEAD, final-head gate log and route trailer across every commit in the
worktree's configured base range, including merges. Gate logs are read from
`CONTROLLER_LOGS_DIR` (default `$CONTROLLER_HOME/logs`); the last lane verdict
in a bound log must be `PASS`. It reports a trailing pending wait as MID-WAIT,
including “I am continuing to wait,” while quoted or historical wait mentions
in a completed close are allowed. Use `any` when the run
intentionally accepts the prototype's supported Codex or Claude trailers;
multiple recognized trailers on one commit are allowed and reported as `INFO`.
A usage-limit failure is reported with its parsed retry time instead of being
masked by pending-wait text. Both `3:46 PM` and
`Nov 17th, 2026 12:59 AM` retry forms are accepted; the latter is printed
verbatim because no timezone is supplied in the message.

When a dead wrapper leaves a Run-Gate container for a later invocation to
collect, pass the report path normally. `job-check.sh` can bind the collection
log only when it names one container containing the current head's
eight-character prefix, an earlier log has the full `run-gate.commit` label on
the same line as that container name, and the report names the container. A
missing report, missing label, wrong commit label, or container without the
head prefix leaves the gate evidence red. A
missing `.done` marker is read from the run name's owner record rather than
assumed: a live owner prints `RUNNING (no .done)` with its pid, a dead one prints
`DIED (wrapper pid <pid> gone, no .done)` and exits nonzero so the controller can
relaunch, a name that was claimed but never started (a launch refused after the
claim) prints `NOT-STARTED` and exits nonzero, and a name no wrapper ever owned
says `owner record: none`. `RUNNING`
never means the child completed; read the wrapper's `.done` marker for its exit
status.

A `DIED` line also prints what a relaunch needs when the wrapper died early:

```text
DIED (wrapper pid 8451 gone, no .done)
thread: 8f2c1d04 (recovered from my-run.jsonl; the wrapper died before writing .thread)
resume: .../codex-impl.sh /path/to/worktree <prompt-file> my-run resume 8f2c1d04
```

`thread: none` means the stream carried no id — start a fresh run instead of
inventing one. If a run still has a `.live`, `job-status.sh` says
`wrapper-dead-agent-alive agent=<pid> tool=<pid>` when the wrapper died but the
agent it started is still working: that agent (or its tool) is what to stop, not
the dead wrapper's pid. See the
[design guide](DESIGN-GUIDE.md#job-check-compatibility) for the checks.

A Codex run derives `.last.txt` from the last completed `agent_message` item in
that invocation's stream, on every exec path. No final assistant text means no
`.last.txt`, rather than an older file. The run states its own outcome in
`.done` as `EXIT=<n> OUTCOME=CODE-EXIT AUTORESUME=<k> EFFORT=<value>`; the
Codex stream carries no pi stop reasons, so the child's exit status is the one
fact the wrapper can state. `EFFORT` is Codex-only; Pi records no thinking level in its `.done` marker.
A resume of an existing run name removes the
previous invocation's marker before its child starts, so a reader never reports
an older resume's exit while the new one works, and a wrapper refused before
the child launches writes no marker.

## Run Pi on Open Design

`deepseek-pi` is the favored Pi route and alternates the two available Open
Design accounts. Pin with `deepseek-pi-volker` or `deepseek-pi-github2`. The
wrapper loads the selected key environment variable by sourcing
`$HOME/.env.ai`; Pi provider definitions stay in the operator-owned
`$HOME/.pi/agent/models.json`. Set `PI_MODEL`, `PI_THINKING`, or
`PI_SESSION_DIR` in the local controller environment to override the defaults.

Start the wrapper detached with an absolute prompt path:

```bash
nohup env CONTROLLER_HOME="$HOME/controller" PI_ROUTE=deepseek-pi \
  scripts/controller/pi-impl.sh \
  /path/to/worktree /absolute/path/to/brief.md pi-run \
  --report /absolute/path/to/report.md \
  > "$HOME/controller/logs/pi-run.launch.log" 2>&1 < /dev/null &
```

The `--report` path (or `PI_REPORT`) is the brief's report file. Naming it lets
the wrapper tell `OUTCOME=NO-REPORT` from a real `OUTCOME=SUCCESS`; leaving it
out skips that one check.

A run name carries one run: the wrapper takes an exclusive lock on
`<runs-dir>/<name>.lock` before writing anything and refuses a second
invocation of the same name (or a `resume` of it) with exit 2, naming the live
owner's pid:

```text
controller: run name 'pi-run' is already owned by pid 12345 (started 2026-11-16T10:00:00Z)
  one run name carries one run: wait for that wrapper, use a different run name, or retire the owner's records explicitly (CONTROLLER_RUN_FORCE=1, refused while an owner is alive)
```

`CONTROLLER_RUN_FORCE=1` only works for a DEAD owner: it moves the whole record
set to `retired/<run>/<utc-stamp>/` (with `PREVIOUS-OWNER.txt`) and starts
fresh, so a deliberate restart never destroys the previous run's evidence.

Every pi launch also carries a **signal guard** (see the
[design rationale](DESIGN-GUIDE.md#why-the-pi-signal-guard-is-mechanical-not-advisory)):
`pi-impl.sh` puts `pi-shims/` first on the agent's `PATH`, so the agent's
`pkill` and `killall` refuse with `pi-shim: refused <name>` and exit 126, and a
`pgrep` carrying a signal argument refuses the same way while a plain `pgrep`
still works. Nothing to configure — but a launch can therefore be **refused
with exit 2 before pi starts**, naming the reason, and both are facts you can
fix rather than retry:

```text
pi signal guard is incomplete: …/scripts/controller/pi-shims/pkill is missing or not executable
pi signal guard cannot be enforced: …/bin/pkill shadows the shim
pi signal guard cannot be enforced: PI_CODING_AGENT_DIR must be an absolute path or start with ~/ (got 'agents/pi')
```

The first means the kit is incomplete (a shim does not resolve or is not
executable): reinstall or restore `scripts/controller/pi-shims/` (all three
files, mode 100755). The second means a `pkill`/`killall`/`pgrep` sits in pi's
**own** managed-bin directory (`$PI_CODING_AGENT_DIR/bin`; a leading `~/` in
that value is expanded against `$HOME` the way pi expands it, and a value that
is relative is refused rather than guessed at, because pi would join it to its
own working directory), which pi puts ahead of the guard: remove that file, or
move it out of the way. Both refusals happen before the run name is claimed, so
a refused launch leaves no `.done`, no `.lock` owner record and no evidence
that would refuse the retry.

An agent that meets the refusal is not to work around it: signal only the exact
pid you started, and never `pkill`/`killall` by pattern in a shared devcontainer.
The guard does not cover the bash builtin `kill` on a negative or foreign pid,
an absolute `/usr/bin/pkill`, a shell loop over `/proc`, or a script that resets
`PATH`; a brief still has to say so.

Read the `.done` marker's outcome word before deciding what to do next:

| `.done` outcome | What to do |
|---|---|
| `SUCCESS` | Read the report, then review on another route. |
| `EMPTY-COMPLETION` | An intermediate word: pi stopped with no text and no tool call, so the wrapper continues the same session itself and records the next outcome. It stays in the marker only when `PI_EMPTY_RETRY_LIMIT=0`. |
| `EMPTY-COMPLETION-RETRIES-EXHAUSTED` | Two continuations of the same session also ended empty. Read `<run>.events`, then decide: resume again with a concrete instruction, or restart deliberately (a fresh session costs about an hour of re-orientation). |
| `UNCOMMITTED` | The work is real but not committed. Inspect the worktree and commit, or resume for the finish. |
| `NO-REPORT` | The work may be done but the report is missing. Resume and ask for the report, or write it from the review. |
| `USAGE-LIMIT` | Wait for the reset recorded in the account state, then `resume-usage-limited.sh`. |
| `TOOL-CALL-NO-TEXT` | The run ended on a tool call and said nothing: inspect the worktree and the last tool output, then resume with a concrete instruction or restart. A message with text **and** a trailing tool call is `SUCCESS` instead (see the row above), so this row is never the one to use for it. |
| `CONTEXT-FULL` | The context window filled; the work is intact. Start a FRESH successor session seeded from the run's forward-oriented state file: continuing a full context cannot succeed, and a fresh session costs about an hour of re-orientation, so this is the controller's decision. |
| `ERROR` / `INCOMPLETE` | Read `<run>.err` and the stream's last events before deciding. |

Check the stream, exit code, and account outcome with the route that was
selected, then inspect current run/stall status:

```bash
scripts/controller/job-check.sh pi-run /path/to/worktree deepseek-pi
scripts/controller/job-status.sh pi-run
scripts/controller/ctl-status.sh
```

`job-status.sh` is the liveness read for an ACTIVE run (the controller's
Monitor polls it instead of watching for `.done`). One line per run, with
`STALLED` when the wrapper is alive but no pi event arrived within
`JOB_STATUS_STALL_SECONDS` (default 600), `LONG-TOOL` when one tool call has been
in flight past `JOB_STATUS_HUNG_TOOL_SECONDS` (default 1800) while the run still
shows progress evidence, and `HUNG-TOOL` for the same age with no progress
evidence. Long tool calls are reported, never killed; the thresholds only
choose the word. Progress is read from SURVIVING processes (the same pid and
kernel start ticks in both readings), so a tool that only churns its process set
(`make -j` workers, per-test subprocesses) is not progress and does not reset
the quiet clock — it reads `HUNG-TOOL` at the threshold, while a survivor that
burns CPU keeps the row at `LONG-TOOL`. Evidence the sidecar could not read is
not evidence: a tick that cannot see the tool's subtree carries the previous
epoch forward as UNMEASURED, and an epoch from before the call started is
published as null, so that row prints `progress=- progress-why=unread` and the
weaker word (`LONG-TOOL`).

A `HUNG-TOOL` row carries the tool's command and the exact child pid to steer:

```
pi-run: HUNG-TOOL pid=8123 last-event=1900s tool=bash/1900s cmd=git show :0
        steer=8451(git show :0) turns=12 calls=9 tokens=204118 since-commit=...
```

Kill `8451`, not `8123`. Killing the wrapper throws away the run's
classification, its `.done` marker and its continuation history, and the tool it
was waiting on is what actually needs to stop. When the tool is waiting on
something the agent itself created (a `git` command on a FIFO the agent opened),
the row shows no progress either, so read the command and decide.

The usage and commit columns are qualified facts, not bare numbers:
`tokens=204118 tokens-last-report=0` means the last measured context was 204k
and the most recent usage report said 0 (a streaming delta or an error turn,
never an empty context), and `since-commit=no-commits-yet` means the age would
have belonged to the base commit, not to this run.

An `EMPTY-COMPLETION` run is continued by the wrapper without the controller
doing anything: it resumes the same pi session with the canonical prompt in
`continue-empty-completion.md`, appends each continuation to `<run>.jsonl`, and
records one JSON line per attempt in `<run>.events`. The default is two
attempts; set `PI_EMPTY_RETRY_LIMIT` to change it, or to `0` to see the raw
class without a continuation. No other outcome is continued automatically.

A pi 402 may exit zero; the `.done` record uses `OUTCOME=USAGE-LIMIT` and the
shared account state stores the reset. Resume by the saved pi session id; the
wrapper uses the recorded account and applies the shared blocked-account
fallback:

```bash
PI_ROUTE=deepseek-pi scripts/controller/pi-impl.sh \
  /path/to/worktree /absolute/path/to/brief.md pi-resume resume <session-id>
```

## Resume a usage-limited run or schedule a launch

Preview recent usage-limit failures, then resume eligible runs on their saved
thread, worktree, and route. `RESUME_SKIP` is a space-separated list of run names;
`--skip` can exclude a run for one invocation. A dated retry still in the
future is skipped without sleeping, with this observable form:

```text
SKIP <run> (limit until Nov 17th, 2026 12:59 AM)
```

Time-only retries remain immediately eligible because they carry no date:

```bash
scripts/controller/resume-usage-limited.sh --dry-run --skip release-review
scripts/controller/resume-usage-limited.sh --skip release-review
```

`codex-impl.sh` writes the required `$CONTROLLER_RUNS_DIR/<run>.wt` map when
it starts. The recovery helper refuses a candidate without that explicit map;
it never guesses a checkout from the JSONL stream. It skips live
`<run>-resumed` wrappers and holds a per-run claim while resuming. The default
foreign-controller prefix is `naf-`; set a space-separated list in
`controller.local.env` to customize it:

```bash
RESUME_EXCLUDE_PREFIXES="naf- external-"
```

To scan a different state directory or wait for a UTC start time on the current
day, paste:

```bash
scripts/controller/resume-usage-limited.sh --runs-dir /path/to/runs --dry-run
scripts/controller/start-after.sh 1430 /path/to/worktree /absolute/path/to/brief.md scheduled-run
```

Codex automatically resumes a reasoning-only completed DeepSeek turn up to
two times by default; Luna defaults to zero. Set `CODEX_AUTO_CONTINUE` to
change the count. Each run records its attempts in `.autoresume.log` and
`AUTORESUME=<k>` in `.done`.

## Build a session read-back

For compaction recovery of a Claude Code controller, use the operator-mandated
recipe in the root `AGENTS.md` (`nyxloom-harness extract --preset readback
--budget-tokens N --since <marker>`). It never drops operator turns. The older
character-budget builder below can truncate operator material; use it only for
non-Claude sessions or bounded run summaries.

Provide the session and run directories and a total output character budget:

```bash
scripts/controller/build-readback.py \
  --since-marker SESSION-MARKER \
  --session /path/to/session.jsonl \
  --runs-dir "$HOME/controller/runs" \
  --out /absolute/path/to/readback.md \
  --budget-chars 12000
```

The builder fails on an unknown marker or a failing `nyxloom-harness extract`
command. Set `--harness` when the executable is not named `nyxloom-harness` on
`PATH`.

## Install the compaction recovery hooks

The controller's Claude Code settings point at the repository's hook script; the
repository ships the script and its documentation, never the controller's own
`settings.local.json` or `.controller-state/` contents. A hook block looks like
this (`PreCompact` builds the readback, `SessionStart compact` republishes the
read order, `PostToolUse` delivers the pressure note):

```bash
printf '%s' '<hook JSON>' | scripts/controller/hooks/ctx-hooks.py pre-compact \
  --state-dir "$PWD/.controller-state" --extract nyxloom-harness
printf '%s' '<hook JSON>' | scripts/controller/hooks/ctx-hooks.py session-start \
  --state-dir "$PWD/.controller-state"
printf '%s' '<hook JSON>' | scripts/controller/hooks/ctx-hooks.py post-tool-use \
  --state-dir "$PWD/.controller-state"
```

Everything is a flag or an environment variable (`CTX_STATE_DIR`,
`CTX_EXTRACT_CMD`, `CTX_READBACK_BUDGET_TOKENS` (30000), `CTX_READBACK_TAIL_PROSE_TOKENS`
(10000), `CTX_READBACK_GOAL_FILE`, `CTX_READBACK_GOAL_PROMPT`,
`CTX_READBACK_REDACT_PATTERNS` (one regex per line, forwarded as `--redact-pattern`),
`CTX_READBACK_TIMEOUT`, `CTX_HOOK_LOG`,
`CTX_SESSION_KIND`, `CTX_HOOK_ROLE`, `CTX_INFORM_AT`, `CTX_URGE_AT`, `CTX_FORCE_AT`,
`CTX_FORCE_GRACE`, `CTX_SOFT_AGENT`,
`CTX_REPO_ROOT`, `CTX_HANDOFF_FILE`, `CTX_MAIN_CONTROLLER`). Without
`--state-dir` the state directory is the nearest `.controller-state` at or above
the hook input's `cwd` (else the repo holding the script, else nothing is written),
so nothing depends on a fixed checkout path. Readback files and state are per
session id, so sessions sharing one state directory never read each other's. The pressure
ladder is a note every 50000 tokens from 100000 on. The wording follows the
session kind (`--session-kind` / `CTX_SESSION_KIND` / `CTX_HOOK_ROLE`; default:
the main session is a `controller`, a hook fired inside an Agent-tool subagent is
`spawned`). A controller below 200000 persists at the next semantic boundary and
continues; from 200000 (and again, more urgently, from 250000) it is asked to write the
handover (controller-handover skill) and launch `compact-resume.sh` detached; from 300000
the `pre-tool-use` mode denies every tool call after 5 more, except the handover
writes (state dir, `plan.yaml`, `scratchpad_dir`), the reads of the state dir, `plan.yaml`
and `scripts/controller/` that an Edit needs first, `Skill controller-handover`, and one bare
launch of this checkout's own `compact-resume.sh`/`clear-resume.sh` (the deny reason carries
the template). An interactive top-level session that is NOT a controller must export
`CTX_HOOK_ROLE=agent`, or it is forced at 300000 too. A spawned session gets the context marker until its threshold
(default 200000), then finish-or-return-PARTIAL. The marker counts calls to the 300k
forced yield; no ceiling or `autoCompactWindow` is read.

To replace a hand-written hook with this script, change only the path in each
`.claude/settings.local.json` command: the five commands are
`python3 <repo>/scripts/controller/hooks/ctx-hooks.py statusline`, `post-tool-use`,
`stop`, `pre-compact` and `session-start` (matcher `compact`); the forced yield adds a
sixth, a `PreToolUse` entry (no matcher) running the same script with `pre-tool-use`.
A failed build never fails the
session: `session-start` then prints the exact manual command, and
`readback-state-<session_id>.json` carries the failure so the next session sees it. Which
harness/situation rows are tested is in
[the compaction checklist](../../../docs/design/ctlstat/HARNESS-COMPACTION-CHECKLIST.md);
the reasoning is in the
[hook design](../DESIGN-GUIDE.md#compaction-recovery-hooks).

## Operate the host queue

After configuring `hosts.local.json`, these commands use only configured
aliases and lanes:

```bash
scripts/netcup/runner/host-sat-queue.sh status
scripts/netcup/runner/host-sat-queue.sh start
scripts/netcup/runner/host-sat-queue.sh pause runner-alias
scripts/netcup/runner/host-sat-queue.sh resume runner-alias
```

Use the identity-checked `scripts/controller/hx.sh runner-alias 'command'`
for a bounded host operation. For queue updates, follow the pause, drain,
stop, wait, atomic replacement, and verify procedure in the
[controller README](../README.md#installing-a-queue-update).

## Run the suite

From a vbpub checkout, enter `scripts/controller/` so Run-Gate reads this
directory's lane declaration, then use the shared gate lock:

```bash
cd scripts/controller
CONTROLLER_HOME=${CONTROLLER_HOME:-"$HOME/controller"}
. "$CONTROLLER_HOME/controller.local.env"
./gate-run.sh suite
```

`CONTROLLER_GATE_LOCK` and `CONTROLLER_GATE2_LOCK` must be distinct absolute
paths from controller configuration; copy their names from
`controller.local.env.example` and export both (`CONTROLLER_GATE_SLOTS`, 1 to 8,
default 2, changes the slot count; see the README). Once all slots are busy the
wrapper polls all of them every 5 seconds and prints progress every 300 seconds, so a
gate uses the first freed slot instead of waiting only for the first path. The
suite is a validation lane, not a release lane.

## Install the tool skills an agent will read

Skills a tool packages install themselves:
`cmru skills install`, `ciu skills install`, `pwmcp skills install`,
`fleet skills install` (each stamps its own directory and answers
`skills check`). assay and run-gate ship hand-written, repo-level skills with no
`skills` verb; install those from this kit:

```bash
scripts/controller/skills-sync.sh check     # exit 1 unless every owned skill is current
scripts/controller/skills-sync.sh install   # ~/.claude/skills and ~/.agents/skills
```

Run `install` after pulling, and after editing either source: a stale
installed copy is what the next agent reads, and `check` is the only thing that
notices. For a single destination (a test tree, a container home), use
`--dest DIR`; `--dry-run` prints the actions without taking them. An edited copy
is refused by design — `--overwrite-modified` is a deliberate discard of those
edits, not a routine step.
