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
   records `EXIT=<n> AUTORESUME=<k>`, and the attempt log is
   `<run>.autoresume.log`. A new run name with an existing `<run>.done`,
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
   merges. The last lane verdict in a bound gate log must be
   `run-gate: lane ... verdict PASS`; an earlier PASS line cannot override a
   final FAIL. The prototype's `any`
   route remains supported for the accepted Codex and Claude trailers;
   multiple recognized agent trailers on one commit are reported as `INFO`
   instead of a failure. It also reports untracked files under `reports/` and
   `scripts/*/reports/`. A collected Run-Gate container can bind evidence only
   when its name contains the current eight-character head prefix, an earlier
   log has the matching full `run-gate.commit` label on a line naming that
   container, and a supplied report names the same container. Without
   `.done`, it prints `RUNNING (no .done)` and exits 0 even if the event stream
   is absent. That status does not mean the child completed. See the
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
`window_end_epoch` per account. A 402 `insufficient_balance` response with a
`reset_at` value records that reset for the account used by the run. Automatic
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
  "github2": {"last_used_epoch": 0, "blocked_until_epoch": 0}
}
```

Add a numeric `window_end_epoch` field to an account when recording its
current weekly window end. The state schema rejects unknown fields so account
keys cannot be stored there by mistake.

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
