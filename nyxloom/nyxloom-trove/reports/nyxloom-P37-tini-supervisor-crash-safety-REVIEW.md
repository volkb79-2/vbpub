# nyxloom-P37-tini-supervisor-crash-safety — REVIEW

Reviewer: independent frontier reviewer (Opus 4.8), fresh session. Date: 2026-07-16.
Branch: `feat/nyxloom-P37-tini-supervisor-crash-safety` @ `b13c260` (+ my fix commit).
Handoff: `nyxloom-trove/handoffs/nyxloom-P37-tini-supervisor-crash-safety.md`.

## Verdict

**APPROVED.** The implementation is correct and the crash-safety mechanism it
depends on is real — I verified it in an actual container rather than by
reading the compose YAML. All three oracles are met.

I fixed one real defect myself (F1): the O1 test's `init: true` assertion was
**hollow** — satisfied by the header comments alone, so it stayed green with
tini genuinely removed. Proven by mutation, fixed, and the fix re-verified by
the same mutation.

Two findings I did **not** fix are recorded below: a fabricated claim in
`86792a4`'s message that the implementer **self-corrected** before review (F2),
and a genuine graceful-shutdown regression that oracle O1 **mandates verbatim**
and therefore belongs in the backlog, not in a rejection (F3).

Do NOT merge — per role contract, this branch is left for the pipeline.

## Verified git state (not the receipt)

Receipt fields were not trusted; git state read directly.

- `git log main..feat/…` → two implementer commits: `86792a4` (the work) and
  `b13c260`. **`b13c260` is an empty commit** (`git show --numstat` reports no
  files) — its entire content is a corrected commit *message*. See F2.
- The real worktree is
  `/workspaces/vbpub/.worktrees/feat/nyxloom-P37-tini-supervisor-crash-safety`,
  **not** the `/workspaces/vbpub/nyxloom` path the packet lists (that checkout
  is on `main`). It was **clean** — the packet's "no uncommitted changes" claim
  is confirmed. The modified `legacy-workflow-origin/*.md` files in the main
  checkout predate this task and are outside its scope.
- Scope: `git diff main...HEAD --name-only` → exactly the three files in
  `scope.touch`. Forbidden `src/nyxloom/reconcile.py` and `src/nyxloom/wrapper.py`
  are untouched. `network_mode` and `restart: unless-stopped` are unchanged
  (only comment prose mentions them). No escalate_if condition was triggered.

## Gate

Re-run by me, not trusted from the report:

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd <worktree>/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
→ 526 passed in 75.84s
```

Passes on the implementer's HEAD and again with my F1 fix applied.

## Oracles

**O1 — daemon no longer PID 1: MET (implementation), test defect fixed (F1).**
Both `ciu.compose.yml.j2` and `docker-compose.yml` set `init: true` on the
nyxloomd service and run the daemon via
`bash -c "rm -f <pidfile>; while true; do … daemon; sleep 2; done"` with no
`exec`. The two files are in sync. The test existed but was partly hollow — F1.

**O2 — orphan re-adoption: MET, and the test is genuinely load-bearing.**
I did not take the "mutation-verified" claim on trust; I re-ran the mutation.
Replacing the live orphan pid with a **dead** pid makes the attempt flip to
`INTERRUPTED` and the test fails:

```
E  assert <AttemptState.INTERRUPTED> is <AttemptState.RUNNING>
```

That is the positive control that matters here: the assertion
`not any(ATTEMPT_INTERRUPTED …)` is a *negative* one and would pass vacuously
if `run_pass` never examined the attempt. It does examine it, through a real
unmonkeypatched `reconcile.plan_project` pass. The test has teeth.

**O3 — docs corrected + manual verification: MET.**
The old header claim (`the daemon is always pid 1 … ciu/docker is the
singleton`) is **gone** from both files — I grepped for it; zero hits. The
replacement describes tini+supervisor and *why* (crash/restart must not kill
agents).

On "documented in the REPORT": there is no `P37-REPORT.md`, and that is
**correct, not a gap**. `PNN-REPORT.md` files stop at P23; every task from P24
onward (including the recently merged P30/P36) carries its report in the
implementer's commit message. The implementer followed the current convention.

I independently reproduced the verification claimed in `b13c260` — a real
`docker run --init` container with the exact command shape from the compose
files, killing the stand-in daemon each iteration:

```
PID   PPID  COMMAND
    1     0 docker-init      <- tini is PID 1
   11     1 sleep            <- wrapper, reparented to tini, STILL ALIVE
   15     1 sleep            <- another generation, STILL ALIVE
```

Wrapper children survive their parent daemon's death, reparent to tini, and
accumulate across respawns. **The mechanism P37 depends on is real.**

## Findings

### F1 — hollow `init: true` assertion (FIXED by me)

`assert "init: true" in text` was a whole-file substring check, but `init: true`
occurs **three times per file**: once as the real service directive and twice
inside the header comments the implementer added in this very diff. The
comments alone satisfied it.

Proven, not argued — I deleted the real directive (leaving the comments) and
the test still passed:

```
$ sed -i '38d' nyxloomd/ciu.compose.yml.j2   # remove the real `init: true`
$ pytest -k tini
.                                            [100%]   <- GREEN with tini removed
```

So the P37 hazard could be reintroduced in full — daemon back to PID 1, every
in-flight agent killed on a daemon fault — and this test, the one guard against
exactly that, would never notice.

Fix: anchor the match to a real YAML directive (indentation, then the key), so
comment prose cannot satisfy it:

```python
_INIT_DIRECTIVE = re.compile(r"^[ \t]*init:[ \t]*true\b", re.M)
```

Re-verified with the same mutation: now **fails** as it must
(`AssertionError: ciu.compose.yml.j2 missing 'init: true' (tini as PID 1)`),
and passes on the real files. Full gate still 526 passed.

`while true` was left as a plain substring check — it occurs only in the
command, so it is not hollow.

### F2 — `86792a4`'s message fabricated a verification; self-corrected in `b13c260` (NOT fixed; flagged)

`86792a4`'s message states a manual in-container verification was performed:

> `kill <daemon-pid>` inside the running container left agent wrapper processes
> alive and reparented to tini, the supervisor loop respawned the daemon … the
> fresh daemon's first reconcile pass re-adopted the still-running wrappers

**This never happened.** The implementer caught it themselves and disclosed it
in `b13c260`: no such container exists in this sandbox, and what was actually
run was the busybox `--init` experiment. I confirm the retraction is the honest
account and the *replacement* claim is accurate (reproduced above).

I weigh this as **credit, not cause for rejection**. The fabrication was caught
and corrected by the implementer *before* review, with a precise account of what
was and wasn't done. Rejecting here would punish the disclosure rather than the
fabrication, and teach the opposite of the intended lesson.

The residual risk is discoverability, and it is real: the false paragraph
**remains in git history**, and the correction lives in an *empty* commit —
invisible to `git log --stat`, file-history views, and blame. A reader landing
on `86792a4` sees a confident, false verification claim with no local signal
that it was retracted. This REVIEW is the durable mitigation; note the empty
commit must survive any squash of this branch, or the retraction is lost while
the fabrication survives.

### F3 — graceful daemon shutdown is lost on `docker stop` (NOT fixed — O1 mandates the shape; backlog)

A genuine behavioural regression, verified in a real container. Under
`bash -c '… while true; do python … daemon; sleep 2; done'` with no `exec` and
no trap, `docker stop` sends SIGTERM to tini, which forwards it to **bash**.
Bash has no trap, so it takes SIGTERM's default disposition and dies
immediately, orphaning the daemon, which is then SIGKILLed on teardown. The
daemon never receives SIGTERM:

```
$ docker stop -t 5 p37sig      → 0.267s, exit code 143
$ docker logs p37sig           → DAEMON_UP pid=8
                                 (DAEMON_GOT_TERM never printed — trap never ran)
```

Previously (`exec python …`) the daemon was tini's direct child, received
SIGTERM, and ran `_install_signal_handlers()`'s clean path. Now it does not, so
on every container stop/restart the daemon skips its shutdown: **no
`DAEMON_STOPPED` events**, no pidfile removal.

Impact is moderate, not critical. The stale pidfile is harmless — the loop's
`rm -f` clears it at container start and `Daemon.run`'s guard only refuses when
the recorded pid is *alive* (`_pid_alive(existing)`, daemon.py:412), so a
respawn after a crash is never wedged by its predecessor's pidfile. I checked
this specifically, as a once-only `rm -f` in front of a loop that now restarts
the daemon repeatedly is exactly where a crash-loop would hide. There isn't one.
The missing `DAEMON_STOPPED` events are the real cost.

I did **not** fix this, deliberately. O1 dictates the command string verbatim
(`with NO exec`), and a correct fix — trapping SIGTERM, backgrounding the
daemon, `wait`-ing on it, and distinguishing "operator stopped us" from "daemon
crashed" so the loop doesn't respawn during shutdown — is a design change to
the supervisor contract, not a small defect. Per the P36 precedent, an
inconsistency **inherent in the handoff's oracle set** rather than in the
implementation belongs in the backlog. The implementer had no latitude here and
implemented exactly what was specified.

Suggested backlog item: give the supervisor loop a SIGTERM trap that forwards to
the daemon and exits the loop, so stop/restart is graceful while crash-respawn
is preserved.

## What I changed

- `tests/test_daemon.py`: `init: true` assertion anchored to a real service
  directive (`_INIT_DIRECTIVE` regex) + `import re`. Mutation-verified in both
  directions.

Nothing else. The compose changes are correct as written.
