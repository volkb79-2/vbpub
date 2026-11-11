# Controller toolkit design

The controller is operator tooling around the repo's existing worktree,
`run-gate`, CMRU, and remote-gate workflows. It keeps orchestration state local
and leaves lifecycle decisions to those project tools.

## Keep state outside the checkout

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

### Job-check compatibility

`job-check.sh` keeps the prototype's `any` route: each commit in the selected
base range, including merge commits, must contain exactly one accepted Codex
Luna/DeepSeek or Claude trailer. Named routes require their exact Codex
trailer, while `none` skips trailer auditing. It also retains the prototype's
relative report-litter check for untracked files under `reports/` and
`scripts/*/reports/` and its final-message checks for empty, intent-only,
tool-call, and mid-wait endings. A mid-wait result requires the final sentence
to announce a gate, process, or task that remains pending; quoted or
historical mentions of a wait do not count.

For a branch with commits, at least one log under `CONTROLLER_LOGS_DIR` must
contain both `run-gate.commit=<full HEAD>` and a passing summary. Every supplied
report must name the current HEAD by its short or full hash. The event stream
distinguishes a completed reasoning-only turn (`REASONING-END`), a usage-limit
failure with its reset time (`USAGE-LIMIT`), and a run with no final
`turn.completed` (`KILLED`). A usage-limit message takes precedence over the
generic killed class so the actionable reset time remains visible.

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
turns alone.

## Recover and schedule Codex work

`resume-usage-limited.sh` scans recent completed streams for a `turn.failed`
usage-limit message, reads the saved thread id and worktree sidecar, and calls
the existing implementer on that same thread and saved route. It skips
successful runs, already-resumed run names, and operator-specified entries.
`--dry-run` reports
the same candidate list without launching jobs. It does not wait until the
reset time; `start-after.sh` provides a separate UTC launch time when an
operator wants to schedule work.

## Two-slot gate scheduling

`gate-run.sh` requires two distinct absolute lock paths from local controller
configuration. It tries each slot without waiting, then waits on the first
slot when both are occupied. It does not invent a lock path or launch outside
the configured locks. The command runs from the caller's project directory so
Run-Gate reads that project's lane declaration.

## Bounded session read-back

`build-readback.py` requires the session path, runs directory, marker and
output path as arguments. Its total `--budget-chars` cap covers all four
sections; when the output is too large, it shares the remaining space across
the sections and marks the result as trimmed. Missing markers and a failing
`nyxloom-harness extract` command fail instead of producing a partial
read-back.

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
