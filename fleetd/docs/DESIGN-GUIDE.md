# fleetd design guide

## Why this package starts with facts and a planner

The compute-host design's first week is WP0, WP1(a), and WP2. That gives
operators useful per-host lists before a daemon exists. Fleetd owns no host
keys, transport, campaign execution, or verdicts. The next lifecycle package
must supply those through the accepted CIU, Run-Gate, and Assay contracts.

## RAM is the placement constraint

Lane caps bound runaway containers; they are not expected worker charges.
Expected charges may sum against the configured 1.5 overcommit budget, but a
single candidate is compared to physical lane RAM after the control-plane
reserve. A candidate that cannot fit physically is unplaceable. Low and high
RAM work is mixed to keep the average expected charge near each host's budget.

## Geekbench is only a time estimate

The planner normalizes serial candidate time with the Geekbench single-core
score and the declared reference score. It multiplies by one stored factor per
host, defaulting to `1.0`. It never divides by multi-core score and never fits a
factor per lane or hardware flavor. ETA is informational and cannot reject or
time out work.

## Facts are read from their owner

The collector accepts a root directory so tests use synthetic `/proc` and
`/sys` trees. Production collection reads the host-owned Geekbench record; it
does not run the benchmark. A missing fact stays `null` with a reason, and a
planner that needs that fact refuses rather than inventing it.

## Determinism and manual command delivery

Candidate, host, batch, and command ordering use explicit stable keys. JSON
contains no generated timestamp. `--commands` emits IDs into checkout-local
candidate files before invoking Run-Gate's existing `--only-candidates FILE`
interface. That lets the current runner execute the plan without adding a
fleet-specific Run-Gate command.

## Deliberate limits

The guard rules are parsed only; no process or cgroup is changed. There is no
daemon, socket, SSH, Docker, transport, remote collection, retry supervision,
or strict merge implementation. Guard configuration accepts the design's
`report` and `enforce` modes, but this package does not act on either mode. See
[FD-001](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-001-fleet-level-campaign-lifecycle-is-not-implemented).
