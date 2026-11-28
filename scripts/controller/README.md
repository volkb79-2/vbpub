# Controller toolkit

This is the reusable controller kit for starting implementation and review
jobs, checking their evidence, coordinating releases, and operating the
host-satellite Run-Gate queue. Runtime files and local machine facts live under
`$CONTROLLER_HOME` (default `$HOME/controller`); see
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

Every implementer brief carries the house rules in [`rules.md`](rules.md)
(CTL-50): remote dev container before each commit, no direct `hx.sh`/`ssh`/
`docker`, no tests in the devcontainer, contract plus size, at most 2 review
rounds, explicit effort, unique run names, DONE/PARTIAL/BLOCKED.

**Remote dev tool.** `dev -p <project> <verb>` compiles, tests and lints a project in
its run-gate environment's capped, network-less container on a gate host, before
the commit (house rule 1). Never gate evidence. Guide: [`docs/DEV.md`](docs/DEV.md);
profiles: `dev-profiles.toml`; contract: `docs/design/agent-dev/DESIGN.md`.

**Bash is frozen** (CTL-40): no new bash beyond ~50-line entry points; new
logic goes into the `agctl` Python package. The one exception is the generic
dev tool (`dev`, about 530 lines including the moved host-side scripts; the
profile resolution is already Python, `dev_profile.py`), whose full Python port
follows with the fleetd host agent.

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

## Preflight (`ctl-doctor.sh`)

`scripts/controller/ctl-doctor.sh [--json] [--scan <dir>]...` checks a controller
machine without changing it and exits non-zero on any failure. Each finding is one
`OK|WARN|FAIL|INFO <check>: <message>` line, with an indented `fix:` line under
every WARN and FAIL. It checks: `$CONTROLLER_HOME` and `controller.local.env`
(the env file is shell-sourced by `lib.sh` like for every controller tool and must
hold assignments only: the doctor flags any other line but cannot make a value
inert, `A=$(cmd)` runs when sourced); every
host-table alias (`ssh`, `api_check`, `guard`, `queue_lanes`, the SSH key file with
mode 600, the pinned `known_hosts` file, `StrictHostKeyChecking=yes`, the identity
command is executable); that `CONTROLLER_REMOTE_GATE_HOSTS` names only aliases of the
host table; `flock`, `git`, `python3`, `jq` (failures) and `docker`, `shellcheck`
(warnings) on `PATH`; the default tester image of `run-gate.root.toml` by exact name
(`docker image inspect`; an unreachable daemon only warns); any file under
`$CONTROLLER_HOME` (and each `--scan` directory) that still names the removed
`run-gate-2560` image tag; the effective `RUN_GATE_PROFILE`; writable runs and logs
directories; the effective number of local gate slots (`CONTROLLER_GATE_SLOTS`, default 2)
and that every slot lock (`CONTROLLER_GATE_LOCK`, `CONTROLLER_GATE2_LOCK` from two
slots on, and the derived `gate<N>.lock` files from three slots on) is set, absolute,
distinct and in a writable directory (a failure otherwise); whether the keep-alive watcher's pid
is running (read from `/proc/<pid>/cmdline`, never probed with a lock or signal; a WARN prints
the detached start command); and where `$CONTROLLER_HOME` and `CONTROLLER_RUNS_DIR` really live (a
symlink target inside a git work tree is a warning: `git clean -fdx` would delete it,
see [`docs/STATE-VS-WORK.md`](docs/STATE-VS-WORK.md)). It prints names and paths only,
never a value from the env file, the host table or a key. Limit: the env file is
scanned line by line, so a multi-line assignment reads as a stray line.

## Hygiene notifier (`ctl-hygiene.sh`)

`scripts/controller/ctl-hygiene.sh` is read-only and always exits 0: it prints one `HYGIENE <rule> <path>: <value> (threshold <t>)` line per finding (nothing when all is well) for an oversized or stale `MEMORY.md` (M1-M3), handoff/state files with too many blocks or bytes (H1, H2), stale or foreign `handovers/` files (S1) and tracked backlogs whose closed entries exceed 30% of the file (B1). `ctl-doctor.sh` appends its lines to the report (a `hygiene` array in `--json`) without changing the status; thresholds and test seams are in the script header and `docs/design/agctl/CONTEXT-ECONOMY.md` section 5.

## State backup (`ctl-state-backup.sh`)

`scripts/controller/ctl-state-backup.sh [--list] [--include-large] [--max-file-bytes <n>]`
archives the non-rebuildable controller state into `$CONTROLLER_BACKUP_DIR` (required,
absolute, never inside a git work tree): `.controller-state/` and `plan.yaml` of the
repository root, `$CONTROLLER_HOME/*.local.*`, `reports/`, the remote-gate
`result.json`/`meta.json` summaries, and the runs' `*.thread`, `*.state.md`, `*.done`.
Never `*.jsonl` session streams, `.git`, worktrees, logs, artifacts, or a file over the
cap (5 MiB). Symlinks are stored as links, never followed (link targets are kept
exactly as they were). `--list` writes nothing; the archive is mode 0600 (zstd if
installed, else gzip) with a `.sha256` sidecar, and `ctl-state-backup.sh --verify
<archive>` checks it (exit 1 for an archive with 0 members or without its sidecar,
not just for a bad checksum). A backup directory the run creates gets mode 700; an
existing `CONTROLLER_BACKUP_DIR` keeps its mode. When `CONTROLLER_REPO` is a linked
worktree that lacks `.controller-state` or `plan.yaml`, the run prints a WARNING
naming them: the real state lives in the main checkout. What is state and what is work:
[`docs/STATE-VS-WORK.md`](docs/STATE-VS-WORK.md).

## Restart read (`resume-read.sh`)

`resume-read.sh <main|platform|core|assay|hostobs|edge|rollback>`: ONE read-only call that prints exactly the state-file slices a restarted session needs (each under `== <path> [<slice>] (<bytes> B, cap <cap> B) ==`, cut at its cap with a visible marker, `MISSING` for an absent file, exit 2 for an unknown role). The output has a budget (`RESUME_READ_TOTAL`, default 24000 B) that the per-slice caps (`RESUME_READ_CAP_<SLICE>`) are scaled to fit, because the Bash tool turns anything above about 30,000 characters into a persisted file; a STATE.md over its share prints one `WARN STATE.md is N B, cap C: shorten it` line. The SessionStart hook names it as the first command; slices and settings: the script header and AGENTS.md "Long-running controllers"; test: `tests/test-resume-read.sh`.

## Successor restart (`compact-resume.sh`, `clear-resume.sh`)

`compact-resume.sh <tmux-pane-id> <guidance-file> <prompt-file> <log-file>` (default: `/compact`, background work survives) and `clear-resume.sh <tmux-pane-id> <prompt-file> <log-file>` (clean slate: `/clear`) restart an interactive Claude Code session in a tmux pane and type the prompt file; both share `resume-lib.sh`; launch detached with `setsid nohup`; procedure, exit codes and limits: AGENTS.md "Successor restart of an interactive session".

`ctl-keepalive.sh [--once]` is the prompt-cache keep-alive watcher: one detached process (`setsid nohup`, singleton lock, log `~/controller/logs/keepalive.log`) that types a short ping into the idle, empty prompt box of the named controller panes 55-59 min after their last API request, at most `KEEPALIVE_MAX` (3) in a row; spec `docs/design/agctl/CONTEXT-ECONOMY.md` section 2. It takes the same per-pane lock as the two resume helpers.

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

   Every pi launch runs under a **run mode**, `implementer` (the default) or
   `review` (`PI_RUN_MODE`). An implementer launch keeps pi's tool set, but its
   built-in bash tool and user `!`/`!!` commands go through the tracked
   `pi-pidns-extension.js` and `pi-pidns-bash`. With the tracked guard files
   unchanged, each command starts in a fresh user and PID namespace.
   The Python bootstrap starts as `/usr/bin/python3 -IS`: its interpreter
   startup ignores `PYTHON*` settings and skips site initialization before
   bwrap, while the original environment remains available to Bash commands.
   `pi-impl.sh` passes the extension explicitly with
   `--no-extensions -e`, so worktree and user extension settings cannot replace
   it; `PI_EXTRA_ARGS` cannot load or disable extensions in implementer mode.
   Before it claims the run name, the launcher probes the wrapper with
   `-c true`; a missing extension or wrapper, missing bwrap, or failed
   namespace setup refuses the launch with exit 2. Every prompt also carries
   `implementer-prompt-rules.md` — signal and TERM-trap tests run only inside a
   capped container against exact PIDs the test recorded itself; no shell
   experiments in the shared devcontainer. A review launch carries the read-only
   tool set from `pi-review-argv` in the wrapper's OWN argv (first run, resume
   and every automatic continuation), every prompt carries
   `review-prompt-rules.md` (execute nothing), and `PI_EXTRA_ARGS` that would
   widen it is refused. The mode is recorded as `<runs-dir>/<run>.mode` and
   `<runs-dir>/session-modes/<session-id>`; a resume records its known ID before
   invoking pi, and a first launch records it after pi returns with the ID.
   A resume also checks the session event in the bounded head of each run stream,
   so a running or stopped first review is found before its `.thread` and
   session-mode records are written.
   It is **sticky per run name and session**: a review resumed under a fresh name
   is still a review, and asking for implementer mode on it is refused. A
   missing rule file refuses the launch (exit 2).

   Use `CODEX_ROUTE=deepseek` to select an Open Design account automatically,
   or pin one with `CODEX_ROUTE=deepseek-volker` or
   `CODEX_ROUTE=deepseek-github2`. OpenCode is available through
   `scripts/controller/opencode-impl.sh`. Codex stores its run
   stream and completion marker in `$CONTROLLER_RUNS_DIR` (default
   `$CONTROLLER_HOME/runs/`); OpenCode uses `$CONTROLLER_HOME/runs/`. Every
   Codex exec path — new, manual resume, and automatic continuation — derives
   `<run>.last.txt` from the last completed `agent_message` item in that
   invocation's JSON stream. A stream with no final assistant text leaves no
   `.last.txt`; an older file is removed. Run long jobs detached and read the
   `.done` marker to learn the child process exit status. Codex resumes a run on the same
   thread up to `CODEX_AUTO_CONTINUE` times after a reasoning-only completed
   turn; the default is two for DeepSeek and zero for Luna. The `.done` marker
   records `EXIT=<n> OUTCOME=CODE-EXIT AUTORESUME=<k> EFFORT=<value>`, and the
   `EFFORT` field is Codex-only; Pi records no thinking level in its `.done`
   marker. `CODEX_EFFORT` accepts only `minimal`, `low`, `medium`, `high`,
   `xhigh` or `max`. The attempt log is `<run>.autoresume.log`. A new run name with an existing `<run>.done`,
   `<run>.jsonl`, or `<run>.err` record is refused before the child starts and
   all existing paths are printed. `CONTROLLER_RUN_FORCE=1` permits replacing
   evidence for a new run that was intentionally abandoned or failed. Resume
   mode does not take that new-run guard: it continues the same thread over an
   existing stream and writes a fresh completion marker when it finishes.

3. **Check the result.** Run `scripts/controller/job-check.sh` with the run
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
   boot) prints `DIED (wrapper pid <pid> gone, no .done)` as a red flag, a name
   that was claimed but never started (an empty lock file, no stream, the lock
   free: a launch refused after the claim, CTL-23) prints `NOT-STARTED` as a red
   flag (a held empty lock is a wrapper still preflighting and prints `RUNNING`),
   and a run that no wrapper ever owned prints `RUNNING (no .done)` plus
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
   review profile. An OpenCode implementation is reviewed by Luna or by a
   separate review session. A DeepSeek implementation is reviewed by
   `pi-review.sh`, which runs the reviewer with a shell-less, writer-less tool
   set on the OTHER Open Design account, under a time-and-output budget, and
   compares the reviewed worktree before and after the launch; the pi-review
   paragraph below lists exactly what that comparison covers and what it
   does not. Keep the
   review report at an absolute path outside the worktree; an existing report
   file is refused, so a retried review uses a fresh path or deletes the old
   file.
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
| `sol` | `codex-impl.sh`, `codex-review.sh` | Independent Sol review route; `codex-review.sh` selects it. |
| `deepseek-pi-<other>` | `pi-review.sh` | Pi review on the Open Design account the implementer did NOT use; `pi-review.sh` derives it from the implementer run. |

The existing `luna` route remains available for side-by-side implementation
runs while the favored Pi route gathers operating experience.

The `deepseek` route uses the two locally configured Codex homes:
`$HOME/.codex-opendesign-volker` and `$HOME/.codex-opendesign-github2`. Set
`CODEX_HOME_DEEPSEEK_VOLKER` or `CODEX_HOME_DEEPSEEK_GITHUB2` to override either
home. Both homes must have their own catalog with a null `tool_mode`; the
selected home's catalog is checked before Codex starts. The model defaults to
`deepseek-v4.1-flash-eco` at `max` effort. Set `CODEX_EFFORT` to override it.

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

`CODEX_HOME_OVERRIDE` wins on Luna and an explicit `CODEX_ROUTE=sol`
`codex-impl.sh` run. `codex-review.sh` deliberately chooses
`CODEX_HOME_REVIEW` (default `$HOME/.codex`), which wins for reviews and keeps
a caller's override from redirecting them. Luna defaults to `$HOME/.codex2`
(or `CODEX_HOME_LUNA`); an explicit Sol `codex-impl.sh` run defaults to
`$HOME/.codex` (or `CODEX_HOME_REVIEW`).

`pi-review.sh` is the pi counterpart of `codex-review.sh`:

```bash
scripts/controller/pi-review.sh <worktree> <absolute-prompt-file> <run-name> \
  --report <absolute-review-path> --implementer-run <implementer-run>
```

It wraps `pi-impl.sh` and gives the reviewer a **tool set without a shell and
without a writer**: `--tools read,grep,find,ls --exclude-tools 'mcp__*'
--no-extensions --no-mcp`. That list has **one definition**, the tracked file
`scripts/controller/pi-review-argv`, which `pi-impl.sh` reads in its own **review
mode** and puts in its own argv; this launcher sets the mode
(`PI_RUN_MODE=review`) instead of exporting a second copy of the list. Because
the set lives in the wrapper's argv, it is carried by the first launch, by every
automatic continuation and by a manual `resume` alike, and a review launched
**directly** through `pi-impl.sh` with `PI_RUN_MODE=review` gets it too. In
review mode `PI_EXTRA_ARGS` is refused (exit 2) when it would add or widen
tools, `--exclude-tools`, builtin-tool switches, extensions, MCP, skills, prompt
templates or project trust — and refused for **any** element at all, so a
spelling the wrapper has not classified takes the same exit and the reason is
printed. `pi`'s documented default tools are `read`, `bash`, `edit` and `write`
(`pi --help`, `docs/cli.md`); `--tools` REPLACES that selection, so the set is
named in full. This is an enforcement, not a detection: there is no tool in
that set that can run a program, so a reviewer cannot loop in a shell or push a
multi-gigabyte stream through one. What it needs instead is rendered before the
launch into `<runs-dir>/<run>.review/`, OUTSIDE the reviewed worktree:
`commits.txt`, `diff.txt`, `files.txt` (`--name-status`), `uncommitted.diff`,
`status.txt` and `before-state.txt`; the reviewer's copy of the prompt names
those files. The reviewer does not write the report either — its FINAL
assistant message is the report, and the launcher saves it at `--report`,
**but only for a settled success class**: `SUCCESS` or `UNCOMMITTED`. A run that
pi-impl.sh recorded as anything else writes no report, because having text in the
final message is not the same as having a review (`stopReason=length` text, the
`CONTEXT-FULL` class, is a cut-off fragment). `CONTEXT-FULL`,
`TOOL-CALL-NO-TEXT`, `NO-REPORT`, `INCOMPLETE`, `ERROR` and `USAGE-LIMIT` are
`REVIEW-NOT-SETTLED` (exit 6); the empty/thinking-only classes are
`REVIEW-NO-REPORT` (exit 2).
A report path that already exists is refused (exit 2, and a dangling symlink
counts as existing): a retried review that says nothing must not leave the
previous file certified as this run's. So is a `<run>.review/before-state.txt`
from an earlier review of the same NAME — the material is rendered before
`pi-impl.sh` refuses the reused name, so a retry must use a fresh run name.
Text in the final message is not the only thing checked; the outcome class is.

**What this does NOT prevent, and cannot:** the `read` tool is not confined to
the worktree. The reviewer can read anything the wrapper's user can, including
credentials under `$HOME`, `/proc`, and the implementer's own run records. The
review prompt and the rendered material are the only inputs the launcher
controls; enforcing a filesystem boundary would need an OS-level mechanism, not
this tool selection. A worktree whose `AGENTS.md` steers the reviewer is a real
risk, and it is not closed here. **A review mode is tool-set enforcement, not
a sandbox:** the reviewer cannot execute anything because no tool in its argv
can, while `read` can still read anything the wrapper's user can. Implementer
bash has a per-command OS boundary for PID signals while the tracked launch
guard files are unchanged: a shell in its new PID namespace cannot address
outer processes, even though it can see their PIDs in the host `/proc`.
`/proc` is intentionally not remounted: `--proc /proc` fails in the supported
container. PID 1 inside is bwrap, so `kill -0 1` succeeds as expected; `sudo`
fails inside, and background jobs end when their command ends.

The namespace does not confine file writes, network access, or other Unix
sockets. The wrapper hides the tmux socket directory and `/run/docker-api`,
unsets `TMUX`, `TMUX_PANE`, and `DOCKER_HOST`, and covers an existing
`/var/run/docker.sock` or an existing `DOCKER_HOST=unix://` socket outside
`/run/docker-api`. Other Unix sockets remain available. The `pi-shims` PATH
guard still refuses ordinary `pkill`, `killall`, and signalling `pgrep`
spellings; it is not itself a security boundary. The implementer prompt still
forbids experiments in the shared devcontainer, whose own shell does not run
under this guard.

**Guard limitation.** This stops accidental signal commands made through the
current guarded path. It is not tamper-resistant: a same-user implementer can
edit the tracked extension, Python bootstrap, worker, or launcher files and
change how a later command starts. The bootstrap's Python interpreter startup
uses `-I -S` before bwrap; the environment is still passed through to the
commands. This limitation is tracked as CTL-65 in
[`KNOWN_ISSUES_TODO_BACKLOG.md`](KNOWN_ISSUES_TODO_BACKLOG.md).

**Review mode is sticky per run name and pi session.** Every launch records it as
`<runs-dir>/<run>.mode` (`review` or `implementer`). A resume records its known
session ID in `<runs-dir>/session-modes/<session-id>` before invoking pi; a first
launch records it after pi returns with the ID. On resume, the wrapper requires an exact
session id in a session-mode record, a `.thread` file, or a session event in a
run stream. It scans only the first 64 KiB of each `.jsonl` stream, so large
streams stay bounded and a running or stopped first review is found without a
`.thread` or session-mode record. It reads each matching run name's mode or
review material. Review from any of these sources forces review, even when the
caller uses a fresh name. A caller that ASKS for implementer mode on that
session is refused (exit 2, before the new name is claimed when the review
record is already visible); the name is re-read after claim to catch a review
record written during preflight. No mode takes the recorded review. A resume id
must match a recorded id exactly; prefixes and ids ending `.jsonl` are refused.
Forced name reuse first persists the old session's mode, using
the same locked monotonic record, before it retires the `.thread` and `.jsonl`
files. The session record can become `review`, but no later implementer launch
can replace it. A forced reuse is refused when the old name has a session id but
no recorded mode and no review evidence (for example a run made before mode
records existed, or a Codex run that left only a `.thread`). Use a new run name,
or write the true mode of that session (the one word `review` or `implementer`;
`review` if unsure) to `<runs-dir>/<run>.mode` and force the reuse again.

A record that exists but is unreadable or neither word is refused rather than
guessed. A name that carries review material (`<run>.review/before-state.txt`)
but no mode record is resumed as a review. A pre-existing implementer session
with no review evidence or matching review record resumes unchanged. A
`PI_RUN_MODE` value that only the host's `controller.local.env` supplied is a
default for launches that name none; it cannot flip a review, and a request
naming the wrong mode is not obeyed either. An implementer run may be resumed
STRICTER (as a review); the name and session records then become `review` and
the session stays a review for the rest of its life.

It also carries a **review budget**, enforced while the run is going:
`PI_REVIEW_MAX_SECONDS` (wall clock, default **7200** — a max-effort review of a
~1200-line diff did not finish inside 3600 s, so 30 minutes paced real reviews),
`PI_REVIEW_MAX_STREAM_BYTES` (size of `<run>.jsonl`, default 268435456),
`PI_REVIEW_POLL_SECONDS` (default 15) and `PI_REVIEW_KILL_WAIT_SECONDS`
(default 10), printed at launch so a stopped run's limits are in its own log.
The launch runs in its own process group, and that ownership is VERIFIED before
it is used: the launcher reads the launch's pgid and signals the group only when
the launch leads it, otherwise it signals the launch pid alone and says so —
a stop that cannot reach the pi the launcher's child execs is not the same
guarantee as one that does. Over budget the launch is TERMed, KILLed if it
outlasts the wait, the run is marked `REVIEW-BUDGET` (exit 5), and a launch that
survives both is named with its pid instead of being waited on: the budget can
never block the wrapper. **The marker says which stop it was**:
`REVIEW-BUDGET-STOP=group` means a group signal was sent, so the pi the launch
execs was signalled too; `REVIEW-BUDGET-STOP=pid` means the launch was not its
own group leader and only the wrapper's child got the signal, so **a pid stop
does not confirm that the reviewer stopped** — its own `pi` may still be running,
consuming the review account and appending to `<run>.jsonl`. Read the form from
the marker before starting a successor review of the same account. The line is
a qualifier of the budget verdict, never a verdict of its own: it is not one of
the four class words, so no reader treats `REVIEW-BUDGET-STOP=*` as a class and
none can turn it into success.
A non-integer value is a refusal, never a default.

**The other account** (the second guarantee): the implementer's account comes
from `<run>.route` when that pins one (`deepseek-pi-volker|github2`,
`deepseek-volker|github2`) and otherwise from `<run>.thread` through
`opendesign-accounts.py find-thread-account`; the review route is then
`deepseek-pi-<the other account>`. An account that cannot be found is a refusal
(exit 2) that prints the record paths it read — the wrapper never falls back to
the implementer's own account. `--account volker|github2` pins the review
account directly, for an implementer run that names none; it prints one line
saying that the caller is asserting the reviewer is not the implementer.
`--implementer-run` names the run whose COMMITS are under review, not the whole
task history: a resumed run has its own name (`<run>-resumed`), so a series that
used both accounts cannot be described by one record — name the run that owns
the commits and say the rest in the review prompt.

**The changed worktree is DETECTED, not prevented.** Before and after the
launch the wrapper records, and compares, exactly these worktree-scoped values:
`git rev-parse HEAD`; the symbolic HEAD (`DETACHED` when detached); `git status
--porcelain=v1 -uall`; a hash of `git ls-files -s -v -z` (index entry flags,
which is what `--assume-unchanged`/`--skip-worktree` hides behind); the hash of
`$(git rev-parse --git-path info/exclude)`, or `ABSENT` (this file belongs to
the repository: in a linked worktree it resolves to the common dir's copy, so
another session editing it flags a review); the hashes of the pre-existing
untracked files' content; and a hash of `git diff --no-ext-diff
--no-textconv --no-color HEAD --binary`. Any difference appends `REVIEW-
MODIFIED-TREE` to `<run>.done`, prints it and exits 4, leaving the changes in
place for the controller to judge. A dirty worktree is allowed (it is compared
with its own before-state). A worktree whose before-state cannot be recorded is
refused (exit 2) and one whose after-state cannot be read takes the same flag.
This is a comparison, so it does NOT cover: ignored paths (except `info/exclude`
itself), `.git` metadata beyond the values above (hooks, the common config,
other refs), the shared main checkout, other worktrees, and anything outside
the worktree. A review that is killed loses its comparison; the before-state is
persisted in `<run>.review/before-state.txt` so a later reader can still see
what the tree looked like. Stop a review through the owner pid in `<run>.lock`
(pi-impl), never through the `pi-review.sh` pid: the wrapper waits on its child
and, if killed, orphans the guarded launch.

The outcome vocabulary and the run records are `pi-impl.sh`'s; `PI_MODEL` and
`PI_THINKING` keep working, and the defaults are the eco model at `max`
thinking. Exit codes: 2 for the refusals (usage, unreadable worktree, report
inside the worktree, an existing report, an unresolvable report path, an earlier
review of this name's own before-state, unknown account, account records that
name none, an invalid budget value) and for `REVIEW-NO-REPORT` or a report that
cannot be written; 4 `REVIEW-MODIFIED-TREE`; 5 `REVIEW-BUDGET`; 6
`REVIEW-NOT-SETTLED`. `0` means exactly one thing for a review: it settled in
`SUCCESS`/`UNCOMMITTED` AND its report was saved. Otherwise pi-impl's own code is
returned unchanged (`1`, `2` refused, `3` both Open Design accounts blocked, and
any other pi status). `3` ("both Open Design accounts blocked") is pi-impl's own
code but UNREACHABLE for a review: the pinned `start-pi <account>` never fails
over, so a blocked review account launches and ends `USAGE-LIMIT` with pi's exit
status. `4`, `5` and `6` are the launcher's, not pi-impl's — pi itself can exit
5, so a bare `5` is not the budget verdict: the `.done` marker is the authority,
not the code. `job-check.sh`, `job-status.sh` and `ctl-status.sh` read any
`REVIEW-MODIFIED-TREE`, `REVIEW-BUDGET`, `REVIEW-NO-REPORT` or
`REVIEW-NOT-SETTLED` line of the marker, so a flagged, stopped or unsucceeded
review is never shown as green. One scan, all three readers: any line of the
marker that CONTAINS one of those four words names the class, and
`job-status.sh` scans the WHOLE marker the same way — including the launcher's
no-marker fallback, which writes the verdict INTO line 1
(`EXIT=5 OUTCOME=REVIEW-BUDGET`), so the class word leads the row there too.
Lines that are none of those four words (pi-impl's outcome line, `RETRIES=`,
`REVIEW-BUDGET-STOP=`) change nothing. A report the launcher could not save —
the path taken by another writer after the checks, or a `cp` that fails — exits 2
with `REVIEW-NO-REPORT` in the marker, because line 1 there is still pi-impl's
`EXIT=0 OUTCOME=SUCCESS`. Two concurrent reviews of one worktree
are not serialised; each sees the other's writes and both flag.

### Pi on Open Design

`PI_ROUTE=deepseek-pi` selects between the same two Open Design accounts using
the shared account state and alternation logic. Use
`PI_ROUTE=deepseek-pi-volker` or `PI_ROUTE=deepseek-pi-github2` to pin an
account. The default model is `deepseek-v4.1-flash-eco` at `max` thinking;
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

**Signal guard.** The wrapper hands the agent `pi-shims/` FIRST on its `PATH`
(the first run, a resume and every automatic continuation alike). pi then
prepends its OWN managed-bin directory to that `PATH` for the agent's bash
tool, so the shims are the first directory that may provide these names only
because the launch is refused (exit 2) while that directory holds a `pkill`,
`killall` or `pgrep`. `pkill`,
`killall` and a `pgrep` carrying a signal argument refuse there with one
`pi-shim: refused <name>` line and exit 126, while a plain `pgrep` passes
through. A pattern kill in the shared devcontainer hits other controller
sessions, and a brief's written ban did not hold. The launch is refused (exit
2) when any shim is missing or not executable, or when pi's own managed-bin
directory (`$PI_CODING_AGENT_DIR/bin`, default `$HOME/.pi/agent/bin`) holds a
`pkill`, `killall` or `pgrep`, because pi puts that directory ahead of the
agent's `PATH`; that value is resolved as pi resolves it (`~` and `~/x`
against `$HOME`) and a value that is still relative is refused, not guessed.
No variable switches the PATH shims or namespace off. The shims are not a
security boundary: absolute command paths, alternate tools, and a script that
resets `PATH` can bypass their refusal. With the tracked guard assets unchanged,
the current implementer bash command runs in its own PID namespace and cannot
address outer processes by PID. The
namespace does not restrict file writes, network access, or other Unix sockets;
the exact mounts and measured namespace limits are in the
[design guide](docs/DESIGN-GUIDE.md#why-implementer-bash-runs-in-a-user-and-pid-namespace).

See the [design guide](docs/DESIGN-GUIDE.md#pi-open-design-route) for the
reason Pi runs are classified from the event stream and how its session id
joins the shared account map, and
[why the signal guard is mechanical](docs/DESIGN-GUIDE.md#why-the-pi-signal-guard-is-mechanical-not-advisory)
for the decisions behind it. The
[consumer guide](docs/CONSUMERS.md#run-pi-on-open-design) has a copyable launch,
the two launch refusals and how to fix each.

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
not progressing. The growth is a SURVIVOR's: a process present in both readings
with the same pid and the same kernel start ticks that grew. A tool that merely
churns its process set (`make -j` starting and reaping workers, per-test
subprocesses) is not progress, and the churn does not reset the quiet clock, so
such a tool reads `HUNG-TOOL` at the threshold; a survivor that burns CPU is
progress, and a recycled pid number is a different process. The sidecar also publishes whether that fact was MEASURED
(`progress_measured`): a tick that could not read the tool's subtree carries a
previous epoch forward as unmeasured, and an epoch from before the call started
is published as `null`, so the row prints `progress=- progress-why=unread` with
the weaker word — an unread fact never licenses `HUNG-TOOL`.
To steer a stuck tool, kill the exact child pid the row
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
finisher prompts live beside them, together with two reusable prompt blocks:
[`sonnet-continuation-preamble.md`](sonnet-continuation-preamble.md) (overrides
for a Sonnet implementer that takes over a task whose Codex run hit the usage
limit) and [`backlog-burn-generic.md`](backlog-burn-generic.md) (the generic
backlog burn-down package a per-project wrapper brief points at). Read the [design guide](docs/DESIGN-GUIDE.md)
for the reasons behind the boundaries, and the [consumer guide](docs/CONSUMERS.md)
for copyable setup and run examples.

### Pi sub-controllers (`pi-ctl-launch.sh`)

`scripts/controller/pi-ctl-launch.sh <role>` starts a persistent pi session in
tmux named `vbpub-<role>`. It resumes the last session id after the extension
signals a successful `session_start`; `--fresh <role>` selects a new id but
keeps the previous id if the new pane fails to become ready, and
`--print-command <role>` displays the resolved command without starting tmux or
reading credentials. The state root defaults to `$HOME/controller/pi-ctl` and
must stay outside the repository. Role names use letters, digits, `.`, `_`, and
`-`, starting with a letter or digit and at most 64 characters.

Every component of the configured `PI_CTL_ROOT` path is checked as written,
before path normalization; any symlink component is refused, including a final
symlink followed by `/` or a symlink before `..`. This applies to system-owned
components as well. If a system path component is a symlink, choose an absolute
physical path with no symlink components. The parent-directory swap race after
this check is BY DESIGN: it requires a hostile process with the same user
identity, which already has read and write access to the state root. This check
is not a tamper boundary against that user.

The pane requires util-linux `flock`, opens the persistent
`locks/<role>.lock` file and keeps its kernel lock for the full pi process
lifetime. The file is never unlinked or replaced; the kernel releases the lock
when pi exits or crashes. A symlink or non-regular lock path is refused. The
extension checks that its inherited descriptor names this role's regular lock
file and that `/proc/self/fdinfo/<fd>` reports a held exclusive flock. Starting
pi by hand without the pane descriptor is refused. `--print-command` shows the
lock path and flock requirement without starting a process. The gate suite
checks that tester-unified provides `flock`.

```bash
scripts/controller/pi-ctl-launch.sh hostobs
PI_CTL_PROVIDER=opendesign_github2 scripts/controller/pi-ctl-launch.sh hostobs
scripts/controller/pi-ctl-launch.sh --fresh hostobs
scripts/controller/pi-ctl-launch.sh --print-command hostobs
```

Defaults are provider `opendesign_volker`, model
`deepseek-v4.1-flash-eco`, thinking `max`, and the current repository root as
the working directory. Set `PI_CTL_PROVIDER=opendesign_github2` to switch to
the other Open Design account. The tmux pane alone sources `$HOME/.env.ai`;
it clears both provider keys first, then exports only
`OPENDESIGN_API_KEY_VOLKER` or `OPENDESIGN_API_KEY_GITHUB2` to pi according to
the selected provider. Override the launch with `PI_CTL_MODEL`,
`PI_CTL_THINKING`, `PI_CTL_CWD`, `PI_CTL_ROOT`, `PI_CTL_SESSION_DIR`, or
`PI_CTL_PRIME`. Keep credentials in `$HOME/.env.ai`, never in argv, tmux
arguments, command output, or logs.

The launcher selects `pi-pidns-extension.js` and `pi-ctl/extension.mjs`
explicitly with `--no-extensions`; it also uses `--no-approve` so an unattended
launch ignores trust-gated project-local resources without a trust dialog. It
does not create project `.pi/settings.json`. Pi's configured global model
catalogue remains the operator-owned `$HOME/.pi/agent/models.json`.

The `pi-ctl/` extension provides `send_message`, persisted wake-up
schedule/list/cancel tools, bounded monitor start/list/stop tools, inbox polling,
an event log and context recovery. Main receives peer messages through the
tested bridge, which prints a fixed untrusted-peer header and id, prints each
claim once per watch process, replays unacknowledged claims after a restart, and
removes a claim only after an explicit acknowledgment:

```bash
# Start this in the main controller's monitor, from the repository root.
node scripts/controller/pi-ctl/bridge.mjs watch
# Later, from a main shell after recording the message id:
node scripts/controller/pi-ctl/bridge.mjs ack "$message_id"
```

Any local shell can queue a peer message without pi's `send_message` tool: the
script writes the same envelope the extension writes, atomically, and prints the
message id the receiving session will confirm:

```bash
# One line of text, sender `main`:
scripts/controller/pi-ctl-send.sh hostobs 'please check the 03:00 run'
# A body from stdin, from a named sender:
printf 'two findings\n' | scripts/controller/pi-ctl-send.sh --from platform hostobs -
```

It refuses an invalid role or sender name, an empty body, a body over
`PI_CTL_MAX_SEND_BYTES` (same default and bounds as the extension), a state root
that is relative, inside the checkout, or has a symlink in any path component
from the root through `inbox/<role>`, for the same reasons
the extension refuses those: the receiver scans `*.msg` entries, so a malformed
or misplaced file would only be quarantined later, away from the caller. The
sender defaults to `main` (`PI_CTL_SEND_FROM` or `--from` changes it), and the
temporary file never ends in `.msg`, so an interrupted write leaves nothing a
session could claim. The component check runs before directory creation and
again immediately before publication; a filesystem swap between check and use
remains a race this shell implementation cannot eliminate.

After recording a message id in main's own state, set `message_id` from the
header and run the `ack` command. A successful print is the delivery: the
watching process records which claim it handed to main and never prints it
again, however long the ack takes. A restarted `watch` replays every
unacknowledged claim once (its predecessor's print died with it) and reports a
claim once if it was printed but still not acknowledged after a real timeout --
30 minutes by default, `PI_CTL_BRIDGE_ACK_TIMEOUT_SECONDS` (1 to 604800) to
change it. The timeout is measured from the first print of any process, so
restarting the watcher cannot postpone it, and `ack` is the only thing that ends
a claim. The extension inbox deduplicates at the queue:
a claim keeps its id and, once `pi.sendMessage()` has accepted that id, the
message is never injected again into that session runtime. pi holds the copy in
its agent-level follow-up queue and persists it only when the turn consumes it,
so the durable claim stays on disk until pi's persisted custom-message entry
shows the type and id, or until a fresh session runtime (`startup`, `resume`,
`new`, `fork`) proves the old queue is gone and re-delivers it exactly once. A
`reload` keeps the live agent, so it re-delivers nothing. Only when pi refuses
the call synchronously is the claim retried, with bounded attempts and a report
to main on a real failure. If a queued id has no persisted entry while pi reports
idle with no pending messages, two polls at least 30 seconds apart mark it
accepted-but-unconfirmed. The extension logs and reports it once, sends the same
one-line notice without starting a turn, and keeps the claim for a fresh runtime.
Wake-ups follow the same rule: their persisted id
survives until pi's entry arrives or a fresh runtime re-fires them once. Peer
messages are untrusted and never auto-replied to; send and receive limits default to 10
per peer per hour; inbox bodies default to 16 KiB and sends to 64 KiB; wake-ups
are 10 seconds to 24 hours with 32 pending. Monitors use the private-PID-namespace
wrapper, wait for its readiness marker, and end within one hour. Compaction
starts only from `agent_end` at 150,000 tokens by default and tells the session
to run `scripts/controller/resume-read.sh <role>` afterward. The launcher uses
`--no-approve` to ignore trust-gated project-local resources without a prompt;
explicit extensions load from argv and no project settings file is written.
Tests run in the registered `scripts/controller` suite with a fake pi API, not
against a live provider.

At each `agent_end`, the extension logs the count and queues one line when any
inbox claims have not been persisted by pi. The notice does not start a turn, and
the claim is never re-injected into the same live runtime: an abort can drop
pi's accepted follow-up copy before it is saved. Polling also detects a likely
asynchronous send rejection after two idle/no-pending observations at least 30
seconds apart; that idle-poll path records its notice as sent once and retains
the claim for a fresh runtime. Separately, each later `agent_end` with an
unconfirmed claim logs the count and queues the same one-line notice; these
notices can repeat and do not start a turn.
Unobservable abort loss while a session remains busy is still tracked as CTL-71.

At `session_start`, the extension arms a one-shot role context block. Before the
session's first model turn, it adds a persistent context message with the bounded
`PI_CTL_PRIME` excerpt and the first prompt, without starting a turn itself. The
first prompt can be typed input, a peer message or a wake-up. The goal is saved
in the session log for compaction and restart; a persisted marker prevents
repeating the role context on resume.

Pi 1.6.0's built-in bash tool starts its shell with only standard input, output
and error in its `stdio` array (`dist/core/tools/bash.js:75-79`). Node 26 treats
unspecified child descriptors 3 and above as `ignore` ([child-process stdio
documentation](https://nodejs.org/api/child_process.html#optionsstdio)). The
monitor spawn explicitly sets the lock descriptor to `ignore` in an extended
stdio array, so neither a tool shell nor a monitor started through
`pi-pidns-bash` keeps the lock after pi exits.

The same wrapper is the one place that keeps the route's API keys away from the
model: every command it starts -- the model-facing bash tool, `!`/`!!` user
commands routed through `pi-pidns-extension.js`, and monitors started by the
extension -- runs behind it, so it unsets every `OPENDESIGN_API_KEY*` variable
before `bwrap` starts (by prefix, not by the two account names the launcher
exports today; the extension's own monitor environment drops the same names).
Pi itself keeps its key: the launcher exports it and pi reads the provider
profile, but a tool shell no longer inherits it. Implementer runs launched by
`pi-impl.sh` use the same wrapper and extension, so they get the same strip.
Known limit: the strip removes what the process environment holds, not what a
file holds -- a shell command that reads `$HOME/.env.ai` itself can still recover
a key, so that file keeps its `0600` mode and stays out of argv, logs and
transcripts. Repository code that needs the variable inside a tool shell would
have to source the file explicitly; a grep of this repository finds no such
consumer.

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
uses ShellCheck when the tester image provides it. The slot lock paths
come from `CONTROLLER_GATE_LOCK` and `CONTROLLER_GATE2_LOCK`; see
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md). Copy those exact names from
`controller.local.env.example` and export both before invoking the wrapper.

`tests/test-mutations.sh` keeps its `expect_killed` checks and per-check logs,
and runs up to three mutation checks at once. The `tester-unified` environment
enforces 3 CPUs; the suite lane caps memory at 4 GiB. The default width is
`nproc`, capped at 3. Set `CONTROLLER_MUTATION_JOBS=1` for serial mutation
checks or choose 2 or 3 explicitly. Empty, non-integer, zero or above-cap
values refuse with exit 2, and the effective width is checked again at
dispatch.

The dispatcher keeps blocked commands pending and starts the next eligible
check from another suite while a serialized suite is active. Checks in the
same serialized suite never overlap. `test-ctl-status.sh`, `test-ctx-hooks.sh`,
`test-pi-review.sh` and `test_remote_gate_cache.sh` remain exclusive: each waits
for the pool to drain and runs alone. The parent reaps whichever recorded
worker finishes first. Every registered check is either started or reported as
not started after a survivor; the final counts still require one result for
each started check.

Each worker command has a 600-second wall limit. GNU `timeout` sends TERM at
the limit and KILL after a two-second grace period if needed. Each worker also
gets a 1 GiB virtual-address-space limit through `ulimit -v`; the lane's 4 GiB
memory cap contains aggregate use across the three workers and the suite
parent. A wall timeout, a memory/resource-limit death, a timeout utility error,
or an unresolved command fails the lane and is never counted as KILLED. So does a
check whose pi override variable (`PI_IMPL_SCRIPT`, `PI_PIDNS_WRAPPER_SCRIPT` or
`PI_PIDNS_WORKER_SCRIPT`) names a file the test reports as unreadable. Any other
started command returning nonzero—including 126 or 127—remains KILLED; zero is
SURVIVED. The longest single event or hang guard in these suites is 300
seconds (`test-pi-impl.sh` and a held-wrapper marker in `test-wrappers.sh`); the
600-second worker limit leaves a twofold margin. Passing marker-driven checks
release on observed state and should finish well before those guards.
`test_dev.py` uses a 180-second subprocess limit. Short 10- and 20-second
readiness polls in `test-wrappers.sh` and `test-pi-impl.sh` now allow 60 seconds
under pool load. Actual per-check durations remain unmeasured until the
controller runs the gate.

Mutation workers are asynchronous commands in non-interactive Bash. They
inherit ignored SIGINT/SIGQUIT dispositions and stdin from `/dev/null`; a test
that needs a signal restores the intended disposition in its own child.
Ordinary `tests/run-suite.sh` test files remain sequential and unchanged.

The number of local slots is `CONTROLLER_GATE_SLOTS` (an integer from 1 to 8; unset
or empty means 2, so nothing changes for a controller that sets nothing). The default
stays 2 on purpose: the local slots guard the controller's own devcontainer, which has
an 8 GiB memory cap and was OOM-killed once when too much ran at the same time. A
controller on a host with more headroom can raise it; the remote gate worker
(`remote-gate.sh`) has its own weight-based slots, and a later RAM-first admission
will replace static counts. The lock file of each slot, exactly:

- slot 1 is `CONTROLLER_GATE_LOCK` and slot 2 is `CONTROLLER_GATE2_LOCK` (the names
  are unchanged, so existing env files keep working);
- slot N from 3 to 8 is `gate<N>.lock` (for example `gate3.lock`) in the directory
  `CONTROLLER_GATE_LOCK_DIR` when that is set (an absolute path, checked from three
  slots on), otherwise in the directory that holds `CONTROLLER_GATE_LOCK`;
- with `CONTROLLER_GATE_SLOTS=1` only `CONTROLLER_GATE_LOCK` is required and
  `CONTROLLER_GATE2_LOCK` is not read; from two slots on both are required.

Every slot lock path must differ (compared as written; a collision between a named
and a derived slot is refused with the two slot numbers). A value that is not an
integer from 1 to 8 (0, 9, `x`, `2.5`) is refused with exit 2 before any lock is
opened.

`gate-run.sh` first tries the slots in order (slot 1, then slot 2, ...) without
waiting. If all are busy, it polls all locks with non-blocking attempts every 5
seconds, takes whichever slot is freed first, and prints which slot it took. It
prints a progress line every 300 seconds while all remain busy (with two slots
the wording is `both slots busy` / `are both busy`; with one `the only slot`, with
three or more `all N slots busy`). The gate command's exit code is propagated
unchanged, and the configured-path and distinct-path refusals still run before
the gate command can start. Gates run with `RUN_GATE_PROFILE=off` unless the
caller sets it: the resource observer needs the rebuilt container, and a lane
that declares one goes to a remote gate worker instead.

A held slot is not an admission (CTL-57): after it holds a slot and before it starts
`run-gate`, `gate-run.sh` waits while the host's memory PSI `full` avg60
(`/proc/pressure/memory`) is at or above 10, the programme's stop signal (fixed, no
knob). It prints `gate-run: waiting for host admission: <value>, <n>s of <bound>s`
once a minute (the wait is counted in its own 10 s sleeps, never read from a clock)
and gives up after `CONTROLLER_GATE_PSI_WAIT_S` seconds (default 1800) with exit 75 and
`gate-run: not admitted (memory PSI full avg60=X >= 10 for Ns); nothing ran`. A value that
cannot be read is not waited on: exit 75 at once, `not admitted (memory PSI unreadable:
<path>); nothing ran`. Exit 75 from this step is "nothing ran", never a lane verdict.
`avg10` spikes are ignored. `dev` host-admission/1 and
`scripts/netcup/runner/remote-gate.py` use other rules.
`CONTROLLER_GATE_PSI_FILE` replaces the PSI path (a test seam).

## Run one lane on a remote gate worker

```bash
scripts/controller/remote-gate.sh <host-alias> <project-dir> <lane> <commit-or-branch> [--base <ref>] [-- extra run-gate args]
```

`remote-gate.sh` is the identity-checked, commit-pinned, slot-admitted wrapper
around `scripts/netcup/runner/remote-gate.py`. It resolves the commit in the
controller checkout, runs the provider identity check from the host table,
admits the lane by weight into k of N flock slots (declared
`resources.memory`, measured peak from `remote-gate-weights.tsv`, else one
slot), builds a throwaway detached worktree under `CONTROLLER_HOME`, runs the
lane and writes `$CONTROLLER_LOGS_DIR/<run>.log` with a binding line
`run-gate.commit=<sha>` plus the copied verdict JSON files. Exit codes: the
lane's own, 2 refusal, 3 identity or transport failure, 75 slots or PSI busy,
76 `INCONCLUSIVE-OOM` (an OOM kill in the host's gate slice during the run,
measured by the slice oom_kill delta; not a lane FAIL; a run-gate
`BUDGET_EXCEEDED` verdict, read only from run-gate's own verdict line for the
lane, is never 76 and keeps run-gate's exit 4; words in the lane's own output
never decide either). Every setting is a `CONTROLLER_REMOTE_*` variable in
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md); `CONTROLLER_REMOTE_GATE_HOSTS`
is required. The weights sample is `remote-gate-weights.tsv.example`.
`-- --base <ref>` (or `--base=<ref>`) after the commit is accepted as a spelling
of `--base <ref>`: either way the base commit travels to the host with the
bundle and `--base` is forwarded to the host's `run-gate` once.

Differences from the program-directory `remote-gate.sh` this replaces. One rule
first: stop using the old program-dir tool before using this one; the controller
replaces the old script with a shim (the slot locks live in different places, so old
and new callers would not exclude each other).

- The old environment names (`REMOTE_GATE_V1001_SLOTS`, `REMOTE_GATE_V1001_BUDGET_MB`,
  `REMOTE_GATE_WEIGHT_MB`, `REMOTE_GATE_SLOT_WAIT_S`, `REMOTE_GATE_WAIT_PSI`,
  `REMOTE_GATE_DRYRUN`, `REMOTE_GATE_DRY_HOLD`, `REMOTE_GATE_PY`) are NOT mapped: each
  one that is set prints a one-line stderr warning naming its `CONTROLLER_REMOTE_*`
  replacement. Note that `REMOTE_GATE_PY` was the Python INTERPRETER of the old tool and is
  replaced by `CONTROLLER_REMOTE_PYTHON`, not by `CONTROLLER_REMOTE_GATE_PY` (that one is
  the path of `remote-gate.py`).
- The weights table lives at `$CONTROLLER_HOME/remote-gate-weights.tsv` (copy the
  old one there). A missing table is the documented fallback (declared memory,
  else one slot); a table that exists but cannot be read prints a warning.
- The runner repository name on the host is `vbpub` unless
  `CONTROLLER_REMOTE_REPO_NAME` is set; it is no longer derived from the
  checkout directory name.
- The throwaway detached worktree is `$CONTROLLER_REMOTE_GATE_WORKROOT/rgw-<time>-<pid>` (default `$CONTROLLER_HOME/remote-gate-work`; set the variable to a path outside every IDE workspace folder, for example `/tmp/ctl-remote-gate-work`, so the editor's Git extension does not adopt each scratch worktree as a repository; after a hard stop remove a leftover directory with `git -C <repo> worktree remove --force <path>`; after a reboot or `/tmp` clear run `git -C <repo> worktree prune`)
  (the old tool used `<repo>/.worktrees/rgw-*`); it is removed when the run ends.

## Run several lanes in a row (`chain-run.sh`)

```bash
(setsid nohup scripts/controller/chain-run.sh [--head <sha>] [--remote-host <alias>] [--stop-on-fail] [--unset-env <NAME>]... \
  <worktree> <project-subdir> <log-prefix> <lane>... >/dev/null 2>&1 </dev/null & disown)
```

One generic chain runner replaces the per-package `chain-*.sh` wrappers. A plain
`<lane>` runs through `gate-run.sh` from `<worktree>/<project-subdir>` and logs
to `$CONTROLLER_LOGS_DIR/<log-prefix>-<lane>.log`; `remote:<lane>` or
`remote:<lane>:--base=<sha>` runs through `remote-gate.sh` (host from
`--remote-host`, or the only alias in `CONTROLLER_REMOTE_GATE_HOSTS`; commit =
the worktree HEAD) and logs to `<log-prefix>-remote-<lane>.log`. Lanes run one
after another; the marker `$CONTROLLER_LOGS_DIR/<log-prefix>.chain` is removed
at start and written at the end: `CHAIN-DONE` once every lane ended, or the
reason (`HEAD moved: ...`, `tree dirty`, `lane launcher missing: ...`,
`STOPPED-ON-FAIL: ...`) when it refused or stopped. No marker means the chain is
still running or was killed.

- The chain NEVER reads a lane's verdict: a failed lane does not stop the next
  one (the controller reads the logs in a separate step) unless `--stop-on-fail`
  is given, which stops on a non-zero lane exit code only.
- `--head <sha>` (full or abbreviated) pins the commit: the chain refuses with
  `HEAD moved: ...` when the worktree HEAD differs and with `tree dirty` when
  the tree has any change, before the first lane starts.
- The environment is passed on untouched, except the names given with
  `--unset-env NAME` (repeatable; for example `--unset-env RUN_GATE_TESTER_UNIFIED_IMAGE`
  for an old chain that began with `unset RUN_GATE_TESTER_UNIFIED_IMAGE`).
  A name that `controller.local.env` itself assigns is re-exported by `gate-run.sh`
  and `remote-gate.sh` (they load that file again through `lib.sh`), so `--unset-env`
  cannot remove it: remove the assignment from `controller.local.env` instead. The
  chain exports no image tag and no profiler socket; profiling is `gate-run.sh`'s
  default (off unless the caller sets `RUN_GATE_PROFILE`), and the repository's
  default tester image is used.
- `gate-run.sh` REQUIRES `CONTROLLER_GATE_LOCK` and (from two slots on, the default)
  `CONTROLLER_GATE2_LOCK` (distinct absolute paths, normally set in
  `controller.local.env`; `CONTROLLER_GATE_SLOTS` sets the slot count, see above):
  when one is unset it exits 2 for every local lane, and because the chain never
  reads a verdict it still ends with `CHAIN-DONE`, by design. Read the lane logs,
  and run `ctl-doctor.sh`, which FAILs when a slot lock is unset, relative, equal to
  another one or in an unwritable directory.
- Tests inject stub launchers through `CONTROLLER_GATE_RUN_SCRIPT` and
  `CONTROLLER_REMOTE_GATE_SCRIPT` (default: the siblings of `chain-run.sh`).

### Migrating an old `chain-*.sh`

An old wrapper maps onto the arguments like this: the worktree variable becomes
`<worktree>`; the `cd` into a project directory becomes `<project-subdir>`; the
file stem of the logs (`<prefix>-<lane>.log`) and of the marker
(`<prefix>.chain`) becomes `<log-prefix>`; the `for` list of lanes becomes the
lane arguments; a `HEAD_WANT` check plus the `status --short` check becomes
`--head`; a `remote-gate.sh <host> <project> <lane> <sha>` call becomes
`remote:<lane>` (host in `--remote-host`; a pass-through `-- --base <sha>`
becomes `:--base=<sha>`). Drop every `export RUN_GATE_TESTER_UNIFIED_IMAGE=...`
(that image tag no longer exists), `export CGPROFILE_CONTROL_SOCKET=...` and
`RUN_GATE_PROFILE` line; an `unset NAME` line becomes `--unset-env NAME`.

Log and marker names change for remote lanes, so monitors keyed on the old names
must be updated: a remote lane's log `<prefix>-<lane>-<host>.out` (for example
`assayburn-final3-tester-unified-<host>.out`) is now `<prefix>-remote-<lane>.log`
(`assayburn-final3-remote-tester-unified.log`), and a separate `<prefix>-remote.chain`
marker of an old remote chain is now `<prefix>.chain` (local lanes keep
`<prefix>-<lane>.log`). The new name carries no host alias. Two real examples:

```bash
# local lanes with a head pin (old: three lanes from run-gate-project, HEAD_WANT + clean-tree check,
# logs judgeprov-final5-<lane>.log, marker judgeprov-final5.chain)
scripts/controller/chain-run.sh --head b3c431a04e4c1fb29f39018dcb2756868d751cc6 \
  .worktrees/pi-rg-judge-provider-20261116 run-gate-project judgeprov-final5 selftest assay-r1 assay-r3

# remote lanes (old: remote-gate.sh <host> assay <lane> 204ed6147 in a for loop, logs
# assayburn-final3-<lane>-<host>.out, marker assayburn-final3-remote.chain); new logs are
# assayburn-final3-remote-<lane>.log and the marker assayburn-final3.chain. The worktree
# must be at that commit, --head proves it
scripts/controller/chain-run.sh --head 204ed6147 --remote-host <host-alias> \
  .worktrees/pi-assay-burn-20261116 assay assayburn-final3 remote:tester-unified remote:self-qualification-provisional
```

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
`stop` (the same five words as the retired untracked `.controller-state/hooks/ctx.py`) and
`pre-tool-use` (the forced yield at 300k: 5 grace calls, then a deny that still allows the
handover writes (under the state directory, `plan.yaml`, the hook's `scratchpad_dir`), the
reads those edits need (the state directory, `plan.yaml`, `scripts/controller/` -- the harness
refuses an Edit of a file this conversation did not Read), `Skill controller-handover`, and ONE
bare launch of this checkout's own `compact-resume.sh`/`clear-resume.sh`; the deny reason
carries the launch template). Wiring this script is a path change in
`.claude/settings.local.json` (`python3 <repo>/scripts/controller/hooks/ctx-hooks.py <mode>`
per mode) plus one new `PreToolUse` entry. Controller thresholds: 200k INFORM, 250k urge,
300k forced (`CTX_INFORM_AT`, `CTX_URGE_AT`, `CTX_FORCE_AT`, `CTX_FORCE_GRACE`).
Configuration is flags or environment (`CTX_STATE_DIR`,
`CTX_EXTRACT_CMD`,
`CTX_READBACK_BUDGET_TOKENS` = 30000, `CTX_READBACK_TAIL_PROSE_TOKENS` =
10000, `CTX_READBACK_GOAL_FILE`, `CTX_READBACK_GOAL_PROMPT`,
`CTX_READBACK_REDACT_PATTERNS`, `CTX_SESSION_KIND`, `CTX_HOOK_ROLE`,
`CTX_REPO_ROOT`, `CTX_HANDOFF_FILE`, `CTX_READBACK_TIMEOUT`, `CTX_HOOK_LOG`); the
state directory defaults to the nearest `.controller-state` at or above the hook
input's `cwd`, so no checkout path is hard-coded.

The pressure ladder (`post-tool-use`) notes every 50k from 100k on; the growth line
counts "calls to the 300k forced yield" (no ceiling, no `autoCompactWindow`). A
controller below 200k is told to persist and plan a voluntary stop; at 200k
(`CTX_INFORM_AT`) and 250k (`CTX_URGE_AT`) to write the handover and launch
`compact-resume.sh` detached; from 300k (`CTX_FORCE_AT`) `pre-tool-use` denies every
call after `CTX_FORCE_GRACE` (5) tool calls except the handover writes and that
launch. An agent (`CTX_SOFT_AGENT`, default 200000)
gets the marker only until its threshold, then finish-or-PARTIAL. A controller
past 200k with a HANDOFF.md older than 15 minutes also gets a stale-state
warning, and `session-start` prints a role rule (main controller, sub-controller
or any other session reads its own list) followed by the READ FIRST list of the
newest `RESUME HERE #N` block of HANDOFF.md instead of a hard-coded order. The
readback files and state are per session (`readback-<session_id>-N.md`,
`readback-state-<session_id>.json`), `session-start` names a readback only when
that session's own last build succeeded, and the pressure state files live in
`<state>/hooks/`. With no `.controller-state` above the hook's `cwd`, the one of
the repo holding the script is used, else nothing is written. Which harness/situation rows are tested is in
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
