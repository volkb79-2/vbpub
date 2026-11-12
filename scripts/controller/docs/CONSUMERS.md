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
masked by pending-wait text.

When a dead wrapper leaves a Run-Gate container for a later invocation to
collect, pass the report path normally. `job-check.sh` can bind the collection
log only when it names one container containing the current head's
eight-character prefix, an earlier log has the full `run-gate.commit` label on
the same line as that container name, and the report names the container. A
missing report, missing label, wrong commit label, or container without the
head prefix leaves the gate evidence red. A
missing `.done` marker prints `RUNNING (no .done)` and exits 0 even if the event
stream is absent; this does not mean the child completed. Read the wrapper's
`.done` marker for its exit status. See the
[design guide](DESIGN-GUIDE.md#job-check-compatibility) for the checks.

## Resume a usage-limited run or schedule a launch

Preview recent usage-limit failures, then resume eligible runs on their saved
thread, worktree, and route. `RESUME_SKIP` is a space-separated list of run names;
`--skip` can exclude a run for one invocation. The helper does not sleep until
the reset time:

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
`controller.local.env.example` and export both. Once both slots are busy the
wrapper polls both every 5 seconds and prints progress every 300 seconds, so a
gate uses the first freed slot instead of waiting only for the first path. The
suite is a validation lane, not a release lane.
