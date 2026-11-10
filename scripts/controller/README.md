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

   Use `CODEX_ROUTE=deepseek` for the configured DeepSeek route, or
   `scripts/controller/opencode-impl.sh` for OpenCode. The wrappers save
   `.jsonl`, `.err`, `.last.txt` where supported, and `.done` under
   `$CONTROLLER_HOME/runs/`. Run long jobs detached and read the `.done` marker
   to learn the child process exit status.
3. **Check the result.** Run `scripts/controller/job-check.sh` with the run
   name, exact worktree path, route, and absolute report paths. It reports runs
   without `.done` as running; for completed runs it checks intent-only
   endings, tool-call text, report completeness, worktree cleanliness, and the
   route trailer on every commit, including merges. The prototype's `any`
   route remains supported for the accepted Codex and Claude trailers; it also
   reports untracked files under `reports/` and `scripts/*/reports/`. Without
   `.done`, it prints `RUNNING (no .done)` and exits 0 even if the event stream
   is absent. That status does not mean the child completed. See the
   [job-check design](docs/DESIGN-GUIDE.md#job-check-compatibility) for the
   accepted forms and completion rules.
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
: "${CONTROLLER_GATE_LOCK:?set the shared gate lock from host policy}"
flock "$CONTROLLER_GATE_LOCK" nice -n 10 run-gate suite
```

The lane runs the deterministic fake-root shell suite in `tests/` and the
queue tests in `scripts/netcup/runner/tests/`. It runs `bash -n` on the kit and
uses ShellCheck when the tester image provides it.

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
