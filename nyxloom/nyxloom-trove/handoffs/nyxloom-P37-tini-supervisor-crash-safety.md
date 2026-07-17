---
schema_version: 1
id: nyxloom-P37-tini-supervisor-crash-safety
project: nyxloom
title: "nyxloomd runs under tini + a supervisor loop (crash/restart without agent loss)"
tier: sonnet5-high
input_revision: "fe45c7c"
depends_on: []
session: fresh
source: {kind: backlog, ref: nyxloom-trove/4-backlog.md}
scope:
  touch:
    - "nyxloomd/ciu.compose.yml.j2"
    - "nyxloomd/docker-compose.yml"
    - "tests/test_daemon.py"
  forbid:
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/wrapper.py"
oracles:
  - id: O1
    observable: "The nyxloomd container no longer runs the daemon as PID 1. In BOTH `nyxloomd/ciu.compose.yml.j2` and the pre-rendered `nyxloomd/docker-compose.yml`: `init: true` is set (docker runs tini as PID 1), and the `command` is a SUPERVISOR loop that respawns the daemon on exit — `bash -c 'rm -f <pidfile>; while true; do /opt/nyxloom-venv/bin/python -m nyxloom.cli daemon; sleep 2; done'` — with NO `exec` (bash stays as the daemon's parent). A test asserts both files contain `init: true` and a `while` loop rather than `exec ... daemon`."
    negative: "the daemon remains container PID 1 (current `exec python -m nyxloom.cli daemon`), so a daemon crash or restart tears down the container and kills every in-flight agent (2026-07-16 finding: PID-1 crash == total agent loss)."
    gate: tester-unified
  - id: O2
    observable: "On startup, the daemon RE-ADOPTS still-live wrapper processes it did not spawn (orphans reparented to tini after a prior daemon died) rather than marking their attempts INTERRUPTED. A unit test in tests/test_daemon.py builds a non-terminal attempt whose recorded pid (and/or attempt_dir/wrapper.pid) is a LIVE process not descended from this daemon, runs a reconcile pass, and asserts the attempt is treated as alive (no MarkInterrupted / no InterruptAttempt) — the P14 belt-and-braces liveness path, now exercised under the tini parentage that makes it reachable."
    negative: "a fresh daemon (post-crash respawn) treats orphaned-but-live wrappers as dead and interrupts them, defeating the whole point — the agents survived the crash only to be killed by the respawn."
    gate: tester-unified
  - id: O3
    observable: "The compose header comment that currently claims `the daemon is always pid 1 ... ciu/docker is the singleton` is updated to describe the tini+supervisor model and WHY (crash/restart must not kill agents). Documented in the REPORT: a manual verification that killing the daemon process INSIDE the container (not the container) leaves the agent wrappers running and the supervisor respawns the daemon, which re-adopts them (integration check, not unit-testable here)."
    negative: "docs still assert the daemon is PID 1, so the next operator reasons from a false model."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "the daemon's orphan re-adoption provably cannot work under tini without a change to wrapper.py or reconcile.py (both forbidden) — then BLOCKED, do not weaken the liveness contract"
  - "the `restart: unless-stopped` policy must change to avoid double-supervision (docker restarting the container AND the loop respawning the daemon) — if that needs a product call, file a D-decision"
---

# P37 — nyxloomd under tini + supervisor (crash/restart without agent loss)

**Highest-priority hardening.** Today the daemon is container **PID 1** (the
entrypoint `exec`s it). A daemon **crash** OR a code-update **restart** therefore
tears down the container and kills every in-flight agent — an unrelated daemon
fault takes the whole fleet down. Fix: make the daemon a **non-PID-1 child** so
its death never ends the container. See `docs/runtime-process-model.md` §2 for
the full analysis; this handoff implements the recommendation.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P37-tini-supervisor-crash-safety`
from `main`); commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these, in order)

- `nyxloomd/ciu.compose.yml.j2` — the stack template. Line ~31 `network_mode:
  host` (leave as-is — network change is out of scope, a separate decision),
  line ~40-45 the `command:` (`bash -c "rm -f <pidfile>; exec /opt/nyxloom-venv/
  bin/python -m nyxloom.cli daemon"`). The `exec` is what makes the daemon PID 1.
- `nyxloomd/docker-compose.yml` — the pre-rendered plain-compose copy; must be
  kept in sync with the .j2 (same `init:`/`command` change).
- `docs/runtime-process-model.md` §1-§2 — the process tree + the tini+supervisor
  design (why execv-self-reload is insufficient for crashes; why the daemon
  must not be PID 1; how orphan re-adoption closes the loop).
- `src/nyxloom/daemon.py` (READ only, forbidden to edit) — the run_pass
  liveness/adoption path: it rebuilds from disk every pass and checks
  attempt.pid / attempt_dir/wrapper.pid for liveness (P14 belt-and-braces).
  This is the code that makes O2 work; confirm it does not assume a wrapper is
  its own child.
- `tests/test_daemon.py` — mirror an existing liveness/adoption test for O2.

## Work

1. `nyxloomd/ciu.compose.yml.j2` + `nyxloomd/docker-compose.yml`: set
   `init: true` on the nyxloomd service; change `command` to the supervisor
   loop (remove `exec`; `while true; do <daemon>; sleep 2; done`). Keep the
   stale-pidfile `rm -f` before the loop.
2. Update the header comment (O3) to the tini+supervisor model.
3. `tests/test_daemon.py`: add the O2 orphan-re-adoption regression test.
4. REPORT: document the manual in-container `kill <daemon-pid>` verification
   (agents survive, supervisor respawns, re-adoption occurs).

## Scope / forbid

Touch ONLY the three files in `scope.touch`. Do NOT change `reconcile.py` /
`wrapper.py`. Do NOT change `network_mode` (separate decision). Do NOT remove
`restart: unless-stopped` unless O2's escalate_if applies (then D-decision).

## BLOCKED rule

If orphan re-adoption cannot work under tini without a forbidden-file change,
STOP — write `BLOCKED: <reason>` to the LOG, commit, exit. Do NOT weaken the
liveness contract to make a test pass.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
