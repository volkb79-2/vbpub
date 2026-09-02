# run-gate wave "resumable, observable gate" — implementer REPORT

Branch `feature/run-gate-wave-resumable` (forked from `main` at `f6d3a858`),
target run-gate 23.4.0 / `__revision__ = 34`. Wave prompt:
`run-gate-project/nyxloom-trove/WAVE-PROMPT-2026-09-02-resumable-gate.md`
(rulings RW-1..RW-8). Blow-by-blow: `-LOG.md` beside this file.

## What landed

| item | commit | SPEC | one line |
|---|---|---|---|
| RG-35 re-attach | `cee805ce` | `R-39` | an inflight record per (lane × worktree × project); re-attach / collect / report-lost / refuse-on-commit-mismatch / `--fresh` |

(Table filled in as the remaining items land: RG-32 → RG-34 → RG-36.)

## RG-35 — live acceptance probe (verbatim)

A fake-docker argv proves construction, not acceptance (AGENTS.md, "Manual
tester-unified gate runs"). One live probe, run 2026-09-02 21:54–21:55 UTC
under the host rule (waited for assay Wave D's gate container to finish;
`docker ps` clear before start; `docker update --cpus=3` right after start;
removed in a trap).

Fixture: a real repo at `/tmp/rg35-live-probe`, a `tester-unified:local`
container lane running `for i in $(seq 1 14); do echo LIVE-PROBE tick $i;
sleep 5; done`.

```
=== HEAD: 8cd72e649c24106920d0285a459f56a22af80055
=== INVOCATION 1 (will be killed) ===
--- inflight record:
{
  "commit": "8cd72e649c24106920d0285a459f56a22af80055",
  "container": "run-gate-rg35-live-probe-probe-1840747-1788386064",
  "container_id": "856caf99afc47de932a6148eca0c18ddf70e5276b601040727c103b8d59a0cb3",
  "lane": "probe",
  "progress": null,
  "project_dir": "/tmp/rg35-live-probe",
  "revision": 34,
  "schema": 1,
  "started_at": "2026-09-02T21:54:24Z",
  "started_epoch": 1788386064.9848084,
  "verdict": null,
  "worktree": "/tmp/rg35-live-probe"
}
--- capped run-gate-rg35-live-probe-probe-1840747-1788386064 at 3 CPUs
--- client 1 killed (SIGKILL); its output:
run-gate: rev 34 | lane probe | env [environments.tester-unified] in /tmp/rg35-live-probe/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-rg35-live-probe-probe-1840747-1788386064 --cgroup-parent dev-background.slice -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice -v /home/vb/mdt--mounted-folders/tmp/rg35-live-probe:/home/vb/mdt--mounted-folders/tmp/rg35-live-probe -v /home/vb/mdt--mounted-folders/tmp/rg35-live-probe:/tmp/rg35-live-probe tester-unified:local bash -c 'set -euo pipefail && ...'
LIVE-PROBE tick 1
LIVE-PROBE tick 2
--- container after the client's death:
run-gate-rg35-live-probe-probe-1840747-1788386064 | Up 8 seconds
=== INVOCATION 2 (must re-attach, must NOT start a second container) ===
21:54:33
21:55:35
run-gate: re-attached to run-gate-rg35-live-probe-probe-1840747-1788386064 (started 2026-09-02T21:54:24Z, running for 0m 08s)
LIVE-PROBE tick 1
LIVE-PROBE tick 2
... (ticks 3..13) ...
LIVE-PROBE tick 14
LIVE-PROBE-DONE
run-gate: lane 'probe' exit 0
=== exit code of invocation 2: 0
=== containers named run-gate-rg35-live-probe-* still present: 0
=== inflight record still present: NO
=== history latest:
{'outcome': 'pass', 'exit_code': 0, 'started_at': '2026-09-02T21:54:24Z', 'duration_seconds': 70.858}
```

Four things this proves that a fake docker cannot:

1. a real container genuinely outlives a SIGKILLed client (`Up 8 seconds`
   after the kill), which is the whole premise of RG-35;
2. `docker logs -f --since <started_at>` is accepted by real docker AND
   replays from the container's start — invocation 2 saw `tick 1`, not the
   ticks after it arrived;
3. `docker wait` on a re-attached container yields the container's own exit
   status (0) to the second client;
4. RW-3's duration is the CONTAINER's: `70.858 s` from 21:54:24, not the
   ~62 s invocation 2 was attached for.

## Decisions taken where the rulings did not reach

Recorded here because the wave prompt asks for them, and because a reviewer
should see them as decisions rather than accidents.

1. **An un-ignored `.run-gate/` warns; it does not fail the lane** (`R-39a`).
   RW-1 says "refuse to write it … with the same remedy `history.json`
   gives", and history's remedy is a warning that leaves the lane's exit
   status untouched (`R-36h`). So the record is not written, the run says
   exactly what is lost ("if this client dies … the next invocation will
   start a second one"), and the lane runs. The alternative reading — a hard
   refusal — would be a second BREAKING change in a wave whose only declared
   one is RG-32's.
2. **A `docker inspect` answer that parses as neither a state nor a gone
   signal refuses (exit 3)** rather than being read as gone (`R-39b`).
   `docker inspect`'s non-zero exit on `No such object` is the only safe
   gone signal; guessing gone on a garbage answer would start the duplicate
   container the feature exists to prevent.
3. **A commit that cannot be determined refuses too.** `head_commit()`
   returning `None` is treated as a mismatch, not as a match — "could not
   determine" resolves toward refusal, the same way it does for history
   eligibility (`R-36b`).
4. **`record_lost_run`'s entry is superseded in `latest`** by the fresh
   run's own record within the same invocation (aborted entries never join
   the trend series, `R-36b`). It is still written, per RW-3: a lost run
   that was never recorded at all is indistinguishable from a run that never
   happened.
5. **The `rev | lane | env | slice` header is not reprinted on a re-attach.**
   That header describes a run this client STARTED — its mounts, its slice.
   A re-attaching client chose none of them. It prints
   `run-gate: rev 34 | lane <n> | re-attach — no new container was started`
   instead.

## Decision asks for the controller

(None outstanding for RG-35. Any that arise in the remaining items are added
here.)
