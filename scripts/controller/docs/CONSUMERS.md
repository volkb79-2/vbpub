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
report, and audits the route trailer across every commit in the worktree's
configured base range, including merges. Use `any` when the run intentionally
accepts the prototype's supported Codex or Claude trailers. A missing `.done`
marker prints `RUNNING (no .done)` and exits 0 even if the event stream is
absent; this does not mean the child completed. Read the wrapper's `.done`
marker for its exit status. See the
[design guide](DESIGN-GUIDE.md#job-check-compatibility) for the checks.

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
: "${CONTROLLER_GATE_LOCK:?set the shared gate lock from host policy}"
flock "$CONTROLLER_GATE_LOCK" nice -n 10 run-gate suite
```

The suite is a validation lane, not a release lane.
