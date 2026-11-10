# Controller toolkit design

The controller is operator tooling around the repo's existing worktree,
`run-gate`, CMRU, and remote-gate workflows. It keeps orchestration state local
and leaves lifecycle decisions to those project tools.

## Keep state outside the checkout

Run streams, completion markers, queue state, local host facts, logs, and
release evidence archives live under `CONTROLLER_HOME`, defaulting to
`$HOME/controller`. This keeps credentials and machine-specific details out
of Git and lets one checkout serve more than one controller machine. The
committed `*.example` files describe the schema; the local copies carry real
paths and provider identity commands.

## Record the child process result

Agent launchers save the event stream and a separate `EXIT=` completion
marker. A missing marker stays distinguishable from a successful run even if
the process or shell is interrupted. `job-check.sh` checks the final event,
report paths, worktree state, and route trailer on every commit, including
merges. The caller selects and records the route because the child cannot
attest reliable invocation metadata.

## Gate each host action by identity

`hx.sh` and the queue use the same local host table. Each remote command first
runs the configured provider identity check and then a host-side guard. This
keeps addresses, key paths, server IDs, and provider credential locations
local while preserving an explicit identity check on every call.

## Replace live queue code only after draining it

The checked-in queue is canonical, while an installed copy may be executing
for hours. The update procedure pauses new work, waits for the current remote
run to finish, stops the loop and waits for its PID marker to disappear, then
atomically replaces same-filesystem staged files. A stale current marker needs
evidence-based diagnosis; fixed sleeps or deleting the marker do not prove the
remote run ended.

## Keep validation in the registered gate

The `suite` lane runs in `tester-unified` and covers fake-root behavior,
`bash -n`, available ShellCheck, and hand-mutated guard checks. It is not
registered for release. New behavior therefore gets reproducible test
evidence without adding a controller release lifecycle.
