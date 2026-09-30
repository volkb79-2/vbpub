---
kind: backlog-entry
schema_version: 1
id: CP-8
title: "the daemon judges no liveness at all -- a session is sampled but never watched, so a stalled or runaway lane runs until someone notices, and there is no streaming `ctl watch` for a consumer to attach to"
status: fixed
type: "feature"
severity: "medium"
provenance: "RG-55 wave, RW-30 (design A1/D-27) + RW-34 (contract v1.1 §8.2/§8.4), 2026-09-12"
filed_date: "2026-09-12"
closed_date: "2026-09-29"
closed_reason: "watch role and exact-cgroup enforcement landed; RW-379/RW-380 prohibit numeric-PID signaling and require an already-requested verified placement leaf for shared-scope kill"
---

## Observed mechanism and reproduction

`lib/serve.py`'s session server samples a target cgroup on a fixed cadence
and accumulates a Summary, and that is all it does with what it reads. The
numbers that would answer "is this lane still alive, and is it still making
progress?" are all already in hand on every tick — `cpu.stat usage_usec`
(`_on_session_sample` computes `live_cpu_cores_recent` from it and throws
the history away), `io.stat` bytes, the target's `memory.pressure`, the
host PSI in `metrics.sample_host`'s `psi` block — and nothing correlates
them over time or acts on them. Concretely, today:

* a lane that wedges (no CPU, no I/O, no output) is sampled forever, its
  session stays `live`, and `ctl status` answers with a perfectly healthy
  looking `live` block: memory numbers, a sample count that keeps rising
  because the SAMPLER is alive, and `cpu_cores_recent: 0.0` — which no
  consumer is told to interpret;
* a lane that burns CPU in a loop while producing no progress events is
  indistinguishable from one doing real work;
* a lane whose own progress stream has already said "done" while its pid
  subtree is still alive leaves no trace anywhere;
* there is no verb a consumer can hold open to be TOLD about any of this —
  `ctl status` is a poll, and every response the daemon writes today is one
  object on a connection that then closes (`_handle_connection`).

The consequence for the estate is the one RG-55 exists to fix: the stall
judgement lives in each consumer (run-gate's in-process `ProgressWatch`),
which means it dies with the consumer's own process, it is duplicated per
consumer, and it cannot reach the lane's cgroup to enforce anything.

## Why cgroup-profiler owns it

Design A1/D-27 (RW-30): *the watcher is a singleton and it is the daemon*.
The daemon already reads the lane's cgroup on a fixed cadence and outlives
individual consumer processes (D-28: "the run-gate client is disposable").
It keeps private PID/cgroup namespaces and uses the explicit host-proc view
for observation; it never treats a host PID number as a signal handle.
run-gate authors the POLICY at `ctl start`; the daemon judges and uses only
the exact cgroup enforcement boundaries authorized by RW-379/RW-380.
Contract v1.1 (RW-34) specifies the result as §8.2 (`ctl watch`, streaming)
and §8.4 (the `liveness` block, the policy options, and the state
vocabulary); §8.7 adds the resulting blocks to the Summary and §8.8 the
`bad-policy` error code.

## Resolved contract

1. `start` gains four policy options (§8.4), all optional, all refused as
   `bad-policy` (exit 2, session NOT started) when unparsable:
   `--progress-stream <path as the lane sees it>` (read through
   `/proc/<pid>/root/<path>`, bounded tail reads — no extra mount),
   `--idle-bound auto|<s>` (`auto` = `max(300, 3 x cadence hint)`, D-22),
   `--ceiling auto|<s>` (`auto` = `3 x meta.expected.duration_s` when
   known), `--on-stall kill|report` (default `report`).
2. Per session the sampler maintains the §8.4 `liveness` block:
   `last_activity_at`, `idle_for_seconds`, `cpu_seconds`,
   `cpu_seconds_recent`, `io_bytes`, the `stream` sub-block (path, last
   event, cadence hint) and `paused_for_seconds`/`pause_reason` — the idle
   clock PAUSES while gates-slice or host memory `full avg10 > 5`, so
   host-wide memory pressure can never be read as the lane stalling.
3. The state machine is exactly §8.4's vocabulary — `ok`, `stalled`,
   `hung`, `runaway`, `throttled`, `over_ceiling` — and the verdict
   (`killed`/`reported`/`none`) plus its readings are recorded on the
   session, returned by `status` and `stop`, and copied into the Summary
   (§8.7 `liveness`/`watch`).
4. `--on-stall kill` never signals numeric PIDs. For `scope=container`, it
   writes only `cgroup.kill` on the exact runtime cgroup whose full container
   ID is proven beneath the verified, bounded gates slice. For
   `scope=container-shared`, it requires the caller's token and an explicitly
   requested, verified placement leaf. It never invents placement solely to
   enable kill. Missing/unverified boundaries are refused before the session
   starts; enforcement failure after acceptance is `reported`, never
   `killed` (RW-379/RW-380).
5. `ctl watch <session> --json` streams §8.2's `reading` / `verdict` /
   `end` lines on BOTH carriers until the session ends. This is the first
   verb that writes more than one object per connection, so it is also the
   documented exception to §8.1's "one request per connection, then close".

Consumer side (run-gate, P5) is §8.9 obligation 2: one `watch` per lane,
`killed` -> the lane's stall exit path, `reported` -> a warning line.
