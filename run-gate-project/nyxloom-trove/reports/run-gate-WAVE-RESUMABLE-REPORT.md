# run-gate wave "resumable, observable gate" — implementer REPORT

Branch `feature/run-gate-wave-resumable` (forked from `main` at `f6d3a858`),
target run-gate 23.4.0 / `__revision__ = 34`. Wave prompt:
`/workspaces/vbpub/run-gate-project/nyxloom-trove/WAVE-PROMPT-2026-09-02-resumable-gate.md`
— **in the MAIN checkout, not on this branch**, where
`run-gate-project/nyxloom-trove/` holds `reports/` alone (RW-31; the
unqualified path here is what review round 2's G5 found the LOG claiming was
already corrected). Rulings RW-1..RW-8. Blow-by-blow: `-LOG.md` beside this
file.

## What landed

| item | commit | SPEC | one line |
|---|---|---|---|
| RG-35 re-attach | `6fe633f5` | `R-39` | an inflight record per (lane × worktree × project); re-attach / collect / report-lost / refuse-on-commit-mismatch / `--fresh` |
| RG-32 inert pin key | `8db781e6` | `R-08a` | **BREAKING**: `pins.*.budget` refused at load by name; pin tables validate their keys at all |
| RG-34 unprefixed argv[0] | `1e41069f` | `R-30b` | `doctor` names it, with the fix and the mechanism; a warning, never a refusal |
| RG-36 progress-judged liveness | `10aa59e2` | `R-40` | 30 s rate/ETA disclosure; optional `stall_timeout` bounding SILENCE, never elapsed |

All four are `__revision__ = 34`, one `[Unreleased]` block, one SPEC.

**Final gate** (`nice -n 19 ionice -c 3 ./run-gate.py selftest --allow-dirty`
from the worktree's `run-gate-project/`, clean tree, verdict read in a
separate step from `scratchpad/selftest-final.log`):

```
488 passed, 2 skipped, 2 warnings in 69.52s (0:01:09)
diff-coverage OK: 269/269 changed executable lines covered (100.0% >= 100.0% floor)
run-gate: lane 'selftest' exit 0
```

Per-item gate runs, all read separately from their own logs: RG-35
`455 passed / 156-156 / exit 0`; RG-32 `459 passed / 153-153 / exit 0`;
RG-34 `465 passed / 163-163 / exit 0`; RG-36 (final, above)
`488 passed / 269-269 / exit 0`. The wave added **60 tests** (428 -> 488).

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

6. **`stall_timeout` on a `kind = "command"` lane is REFUSED at load**
   (`R-40c`). RW-5's "a lane without a progress file cannot stall by this
   rule" is about an ASSAY lane whose judge writes no events (an R0/R1
   lane) — that case is disclosed once and stays healthy, exactly as ruled.
   A COMMAND lane can never have the file at all, so the key there would be
   inert config indistinguishable from a real setting: RG-32's defect, one
   key over, landing in the same wave. See the decision ask below.
7. **The stall exits 3, and `finish_run_record` therefore records the run as
   `error`** (a `GateInfraError` is an `Exception`). Exit 3 is RW-5's;
   `aborted` stays reserved for a `BaseException` (Ctrl-C) and for RG-35's
   lost-container case. Either way it is never a pass and never joins the
   trend series.

## Decision asks for the controller — ALL THREE RULED 2026-09-02

**RW-9** — ask 1: the refusal STANDS; the gap is filed as **RG-41**
(log-stream silence for container command lanes, source disclosed), E-3
candidate, not implemented here. **RW-10** — ask 2: no propagation, no
token; the shape is documented in CONSUMERS "Gate-conjunction lanes" and
SPEC `R-39d`, and the refusal-through-the-chain claim was verified
empirically first (transcript in the LOG, entry E10, and in CONSUMERS).
**RW-11** — ask 3: RG-40 stands as filed, not fixed in this wave.
**RW-12**: the E-008 deviation is accepted as recorded.

The asks as originally written follow, for the record.

## Decision asks for the controller

1. **`stall_timeout` on a command lane: refuse (shipped) or accept-and-inert?**
   I refused it, for the reason in decision 6 above. The cost of the refusal
   is that a container COMMAND lane — the shape most likely to hang, and the
   one this estate runs most — gets nothing from RG-36 at all: run-gate has
   no liveness signal for it, because only assay writes a progress file. If
   the controller wants the key accepted there as a forward declaration
   (inert until some future command-lane progress source exists), that is a
   one-line change plus a disclosure line, and it should be decided
   deliberately rather than by my silence. **Nothing in the rulings settles
   it.**
2. **`--fresh` is not offered on the conjunction path.** RW-2 says a
   conjunction lane carries the behaviour to each SUB-lane. It does, because
   each sub-lane is a separate `run-gate <lane>` invocation with its own
   record — but the conjunction's own argv is a consumer-authored string, so
   a caller who wants `--fresh` to reach the sub-lanes has to put it there
   (exactly as `--worktree` and `{base}` already work, R-25/R-35). Flagging
   it in case the controller wants `--fresh` added to the documented
   conjunction argv recipe in CONSUMERS.md; I did not change consumer
   recipes.
3. **New backlog filing: RG-40** — `tools/coverage_gate.py` reports
   misleading uncovered LINE NUMBERS when the `selftest` lane runs with
   `--allow-dirty` (its diff comes from `base..HEAD`, its coverage from the
   file on disk). Measured twice in this wave, `98.9%` dirty vs `100.0%`
   committed on identical code, each costing a gate round. Filed with the
   transcript and two proposed fixes; NOT fixed here (out of the wave's
   scope, and it touches the gate every other item was measured with).

## For the controller's dstdns notification (RW-7's, plus two more)

> **SUPERSEDED 2026-09-02 by "Fix round 1 — corrected dstdns notification
> (RW-13)" at the end of this file.** The sweep behind this section was a
> text `grep`, which cannot tell a lane-level `budget` from one inside a pin
> table; parsed with the loader it is 13 lanes, not 2, the migration takes
> two rounds, and `schema` was fixed at `dstdns@65582354`. Kept verbatim
> because the record is append-only.

- **Blocking:** `pins.assay.budget` must be deleted from dstdns's
  `run-gate.toml` (`sql-mutation`, `assay-p129-enumeration-cursor`) BEFORE
  upgrading to 23.4.0 — the key now refuses at load.
- **Recommended:** dstdns's `[lanes.schema] argv = ["scripts/schema-gate.sh",
  "{worktree}"]` is RG-34's own evidence case; `run-gate doctor` will now
  name it on every run until the script path is `{worktree}`-prefixed. The
  edit is dstdns's — run-gate deliberately does not rewrite argv.
- **Offered:** `sql-mutation` is the lane RG-36 was built for. The shape is a
  generous assay `budget` + `judge.mutation.budget_per_candidate` +
  `stall_timeout = "15m"` in run-gate.toml; the lane then stops on SILENCE
  instead of on a guessed total, and a killed client re-attaches instead of
  starting a second 120-minute container.

---

# Fix round 1 (after adversarial review round 1), 2026-09-02

Fresh implementer session; the original's is closed. Orders: controller-log
rulings **RW-13..RW-19**, applied in the order RW-14 → RW-13 → RW-16 →
RW-15 → RW-17 → RW-18 → RW-19. Every ruling is a behaviour change with a
behaviour test; the review's probes are the red-first tests where the
pre-fix tip expresses the wrong behaviour.

## Corrected dstdns notification (RW-13, RW-19/S7)

> **SUPERSEDED 2026-09-03 by "dstdns notification, re-measured" at the
> end of this file (RW-30).** The counts below were true on 2026-09-02 and
> are not any more: dstdns merged the `assay-p166-result-dedup` family in
> between. The migration's SHAPE (two rounds; move `clean_tree`, do not
> delete it) is unchanged and still correct.

Everything below is PARSED with the rev-34 loader over every lane of
`/workspaces/dstdns/run-gate.toml` (29 lanes), not text-grepped. Re-measured
independently in this session.

- **Blocking, and it is TWO rounds, not one deletion.** **13 of 29** lanes
  refuse at load once run-gate is at 23.4.0:
  `assay-dlq`, `assay`, `sql-mutation`, `assay-p129-enumeration-cursor`,
  `worker-execution-admission`,
  `worker-execution-admission-r2-{compare,boolop,flips,falsy}`,
  `assay-p169-op-override-projection`,
  `assay-p169-op-override-projection-r2-{compare,boolop,falsy}`.
  - **Round 1:** delete `budget` from every `[lanes.<n>.pins.assay]` table.
  - **Round 2:** re-load. Four of those lanes — `assay-dlq`, `assay`,
    `sql-mutation`, `assay-p129-enumeration-cursor` — then refuse again on
    `clean_tree`, which sits in the same misplaced position
    (`/workspaces/dstdns/run-gate.toml:34-38, 92-96, 114-118, 145-149`; the
    `clean_tree = false` lines are 37, 95, 117 and 148). The `budget`
    refusal fires first and masks it, which is why one round is not enough.
  - **`clean_tree` is MOVED one level up into `[lanes.<n>]`, never deleted.**
    23.4.0's refusal says so by name. Deleting it would leave the lane on
    the default `clean_tree = true`.
- **A live dstdns defect run-gate found, for dstdns to file:** those four
  lanes' `clean_tree = false` has been **inert since it was written** — the
  key is under `[lanes.<n>.pins.assay]`, where run-gate never read it, so
  all four have been running with `clean_tree = true`. Whether they SHOULD
  run dirty-tolerant is dstdns's call; the point is that the config has been
  saying one thing and the gate doing another. Filing it is dstdns's.
- **Recommended (RG-34), corrected:** `[lanes.schema]` is **already fixed**
  (`argv = ["{worktree}/scripts/schema-gate.sh", "{worktree}"]`, with an
  RG-34 comment, since `dstdns@65582354`). The lane that trips the new
  `doctor` WARN today is **`scale-admission`**
  (`/workspaces/dstdns/run-gate.toml:81`, `argv[0] =
  "scripts/schema-gate.sh"`) — one hit, parsed over every lane. RG-34 lands
  with a live consumer hit.
- **Offered (RG-36), unchanged:** `sql-mutation` is the lane RG-36 was built
  for — a generous assay `budget` + `judge.mutation.budget_per_candidate` +
  `stall_timeout = "15m"`, so the lane stops on SILENCE instead of on a
  guessed total, and a killed client re-attaches instead of starting a
  second 120-minute container.

---

# Fix round 1, part 2 (successor session), 2026-09-03

Rulings **RW-15, RW-17, RW-18, RW-19** plus **RW-20** (which the controller
ruled in answer to fix round 1's decision ask 1), and the live two-client
probe this package owed. RW-21 and RW-22 are answered "as ruled" below.

| commit | ruling | one line |
|---|---|---|
| `a8dc5ebc` | **RW-15** (S1) | `--since` dropped; plain `docker logs -f` already replays from the first line |
| `cb6104a4` | **RW-20** (ask 1) | a live owner whose container is already gone is re-polled, bounded, before any refusal |
| `86f7f7b4` | **RW-17** (S4) | a COLLECTED run's duration is the container's own `FinishedAt − StartedAt` |
| `ffc64903` | **RW-18** (S5+S6) | the dry run names the real outcome; every path discloses the lane's bounds |
| `d7e280ce` | **RW-19** | S7 + N1..N5 + hollow tests 2/4/6, and one real flake in the wave's own fixture |

Gate on the committed tip `d7e280ce`, verdict read in a separate step:

```
538 passed, 2 skipped, 2 warnings in 70.00s (0:01:09)
diff-coverage OK: 452/452 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: lane 'selftest' exit 0
```

## "As ruled" — RW-20, RW-21, RW-22

- **RW-20 (fix round 1's ask 1) — as ruled.** The bounded re-poll is
  implemented in `repoll_owner_race`: `OWNER_RACE_REPOLLS = 3` reads in all
  (the caller's is the first) at `OWNER_RACE_PAUSE_SECONDS = 0.5`, ~1 s. The
  record vanishing → disclose and run fresh; the owner dying → the ordinary
  lost-container path; the owner still alive after the window → the refusal
  by pid, unchanged. Bounded, never a wait loop, and the refusal test asserts
  the bound.
- **RW-21 (ask 2) — as ruled, confirmed.** The follower's `docker wait` stays
  issued BEFORE and concurrently with the log stream. `SPEC.md R-39e` now
  states the reason and the price in the same breath: one extra docker client
  held open for the lane's duration, accepted because the alternative loses
  the follower's exit code on every clean finish.
- **RW-22 (ask 3) — as ruled, confirmed as shipped.** An impostor container
  (a stranger wearing the lane's name, id mismatch) is disclosed, left
  RUNNING, and never touched: run-gate does not remove what it did not
  start. RG-44 (`doctor` names a container wearing a run-gate lane name that
  no inflight record owns) is the CONTROLLER's filing after the 23.4.0 merge
  and is deliberately not filed here.

## Live two-client acceptance probe (real `tester-unified:local`)

Host rule observed: `docker ps --format '{{.Image}} {{.Names}}'` clear of
`tester-unified:local` and of any `run-gate-*` container (assay's Wave D
generation 11 had just released the slot), one container at a time, capped
with `docker update --cpus=3` within seconds of launch, container and scratch
repo removed in an EXIT trap. Scratch repo `/tmp/rg-live-probe-succ`, a
`kind = "command"` container lane printing 40 one-second ticks.

**Honest note on the first attempt.** The probe ran twice. In the first run
scenario 1 passed exactly as below, but scenario 2 was INVALID: the `pgrep`
pattern matched only the detached launcher, so the run-gate client itself
survived the `kill -9` and invocation 2 correctly printed `following … (owner
pid 2653111)` instead of re-attaching. That is a genuine extra RW-14 data
point — a live owner really is followed, not hijacked — but it is not the
scenario the acceptance asks for. The run below is the corrected one, with
the pattern fixed and the client logs moved OUT of the judged tree (in the
first run they dirtied it, which correctly made the history entry
`history_eligible: false`).

### Scenario 1 — two clients, one lane: the second FOLLOWS

```
=== HEAD: e99cb5d30e2398d89222a34ad186bd5bb5214305
--- client A inflight record:
{
  "boot_id": "119fbdb1-be66-4467-9a18-e04e1977ca03",
  "commit": "e99cb5d30e2398d89222a34ad186bd5bb5214305",
  "container": "run-gate-rg-live-probe-succ-probe-2662208-1788405791",
  "container_id": "9144b2a4aef8288555edcb61ba41b647445c28c80648684ad2eced8f786d8cc7",
  "lane": "probe",
  "owner_pid": 2662208,
  "owner_start": 7543605,
  "progress": null,
  "project_dir": "/tmp/rg-live-probe-succ",
  "revision": 34,
  "schema": 1,
  "started_at": "2026-09-03T03:23:11Z",
  "started_epoch": 1788405791.7470937,
  "verdict": null,
  "worktree": "/tmp/rg-live-probe-succ"
}
--- capped run-gate-rg-live-probe-succ-probe-2662208-1788405791 at 3 CPUs
--- containers while BOTH are attached: run-gate-rg-live-probe-succ-probe-2662208-1788405791

=== CLIENT A (the OWNER) output:
run-gate: rev 34 | lane probe | env [environments.tester-unified] in /tmp/rg-live-probe-succ/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-rg-live-probe-succ-probe-2662208-1788405791 --cgroup-parent dev-background.slice … tester-unified:local bash -c '…'
FIRST-LINE-AT-START
LIVE-PROBE tick 1 … LIVE-PROBE tick 40
LIVE-PROBE-DONE
run-gate: lane 'probe' exit 0
EXIT=0
=== CLIENT B (the FOLLOWER) output:
run-gate: following run-gate-rg-live-probe-succ-probe-2662208-1788405791 (owner pid 2662208, started 2026-09-03T03:23:11Z)
run-gate: rev 34 | lane probe | follow — no new container was started, and this client will not remove run-gate-rg-live-probe-succ-probe-2662208-1788405791, clear its record or record its history: pid 2662208 owns all three
FIRST-LINE-AT-START
LIVE-PROBE tick 1 … LIVE-PROBE tick 40
LIVE-PROBE-DONE
run-gate: lane 'probe' exit 0
EXIT=0
=== containers named run-gate-rg-live-probe-succ-* still present: 0
=== distinct containers this lane EVER had: run-gate-rg-live-probe-succ-probe-2662208-1788405791  (one docker run)
=== inflight record present: NO
=== history:
  series entries: 1
  latest: {"outcome": "pass", "exit_code": 0, "duration_seconds": 41.007, "history_eligible": true}
```

(The `LIVE-PROBE tick 2..39` lines are elided from both clients' output for
length; both printed all forty, in order. The only other elision is the
`docker run` argv's inner command.)

**PASS on every clause of the acceptance shape.** A — the client that started
the run and the one a human is watching — exits **0** with its own true
result, all forty ticks. B exits **0**, prints `following … (owner pid …)`,
and removes nothing: the container, the record and the history entry all stay
the owner's, and B's header says so by name. **ONE** `docker run` (one
container name ever existed). **ONE** `rm` (zero containers remain, and only
the owner had a `finally` to run one). **ONE** history entry, `pass`,
`history_eligible: true`, `duration_seconds: 41.007` measured from the
CONTAINER's start. This is review B2's exact probe, and at `73e6b061` it
produced A exit 3 on a green lane with `outcome: "error"` in `latest`.

### Scenario 2 — kill -9 the owner mid-lane; the next invocation RE-ATTACHES

```
--- capped run-gate-rg-live-probe-succ-probe-2671294-1788405832 at 3 CPUs
--- SIGKILLing client pids: 2671288 2671288 2671294  at 03:23:59
--- container after the kill: run-gate-rg-live-probe-succ-probe-2671294-1788405832 Up 8 seconds
--- inflight record survives the kill: YES
--- INVOCATION 2 (must re-attach, must NOT start a second container):
run-gate: re-attached to run-gate-rg-live-probe-succ-probe-2671294-1788405832 (started 2026-09-03T03:23:53Z, running for 0m 08s)
run-gate: rev 34 | lane probe | re-attach — no new container was started
FIRST-LINE-AT-START
  …
LIVE-PROBE tick 40
LIVE-PROBE-DONE
run-gate: lane 'probe' exit 0
EXIT=0
=== invocation 2 built/started NO container: 'docker argv' lines = 0; re-attach header lines = 1
=== containers named run-gate-rg-live-probe-succ-* still present: 0
=== inflight record present: NO
=== history after scenario 2:
  series entries: 1
    {"outcome": "pass", "exit_code": 0, "duration_seconds": 40.897, "started_at": "2026-09-03T03:23:53Z"}
  latest: {"outcome": "pass", "exit_code": 0, "duration_seconds": 40.897}
```

**PASS.** The container outlived the SIGKILL; the record survived it;
invocation 2 printed the re-attach line with the container's true age
(`running for 0m 08s`), emitted **zero** `docker argv` lines — it never built
a plan, let alone started a second container — replayed from the container's
**FIRST** line (`FIRST-LINE-AT-START`, which is RW-15's plain `docker logs
-f` proven live: this client attached ~33 s after that line was printed),
exited with the lane's own code, removed the container, cleared the record,
and recorded the run ONCE with `duration_seconds: 40.897` from the
CONTAINER's clock rather than the ~33 s it was attached. `series entries: 1`
rather than 2 across the two scenarios is RG-27's own rev-30 rule, not a
loss: the trend series is keyed by (lane, commit) and a re-run of the same
commit REPLACES its entry (`_apply_record`), so "the last N commits" stays
true.

Nothing was left behind: `docker ps -a` clear of `run-gate-rg-live-probe-*`,
scratch repo deleted by the EXIT trap.

## Decision asks (numbered; none decided on silence)

1. **RW-18 was applied to two branches beyond the one S5 named.** The ruling
   asks the dry run to compute the commit comparison first and name that
   refusal. The same structural defect sat one branch over in two more
   places, both introduced by THIS wave, and both are fixed here: a record
   whose owner is ALIVE would be FOLLOWED (RW-14's branch), and `--fresh
   --dry-run` announced a re-attach for a container the live run removes.
   Flagged rather than done silently. Confirm, or say which should be
   reverted to the narrower reading.
2. **Hollow test 6 exposed a real behaviour change, not just a test.**
   `ProgressWatch` measured silence from the first OBSERVED event, so a
   container that was ALREADY frozen when a client re-attached received a
   fresh, full `stall_timeout` window. Silence now runs from the watch's
   CONSTRUCTION until the file first moves under it. This makes a re-attached
   lane stoppable sooner than rev 34's first cut would have stopped it —
   deliberate, and the case the wave exists for — but it is a behaviour
   change the review asked for only as a test. Confirm.
3. **N2's disclosure has a cost worth naming.** A record of an unknown schema
   is ignored, and this client then starts its own container and writes its
   OWN record over the unreadable one. That is the only coherent thing an
   owner-of-a-new-run can do, and the warning says what is lost — but it does
   mean a NEWER client's record can be destroyed by an OLDER one. If that is
   not wanted, the alternative is refusing (exit 2) on an unknown schema
   instead of degrading, which turns a forward-compatibility hint into a hard
   stop. Ruled here as: degrade and disclose. Confirm.
4. **`RG-34`'s backlog acceptance box changed shape.** The dstdns-side box is
   now ticked (`schema` was fixed at `dstdns@65582354`) and a NEW unticked
   box names `scale-admission` as the lane that still trips the WARN. The
   section therefore stays OPEN on its consumer half, with a different lane
   than the one it was filed from. Confirm that is the record you want,
   rather than closing RG-34 outright and filing the `scale-admission` hit
   as a note in the dstdns notification only.

---

# Fix round 2 (after adversarial review round 2), 2026-09-03

Fresh implementer session. Rulings **RW-27 (G1, the blocker), RW-29 (G3),
RW-28 (G2), RW-25, RW-30 (G4), RW-31 (G5), RW-26**, plus both round-2 nits.
Round-2 tip reviewed was `21e6bbea`; this package starts at `4a7a490b` (the
review report on top of it).

| commit | ruling | one line |
|---|---|---|
| `43d66ba8` | **RW-27** (G1) | silence is measured from the FILE's mtime on the first observation, not from the client's construction |
| `d16d9380` | **RW-29** (G3) | the PID-namespace inode joins the owner conjunction; another namespace = liveness UNKNOWN = ALIVE |
| `7fd45793` | **RW-28** (G2) | a follower that outlives its owner is promoted and finishes the three duties |
| `3af55353` | **RW-25** | an inflight record of another schema is REFUSED (exit 2), never disclosed-and-overwritten |
| `713887fc` | nits **N-a**, **N-b** | a `null` recorded artifact falls back to the config; `repoll_owner_race` seeds `owner` from the caller's pid |

## "As ruled" — RW-25 … RW-31

- **RW-27 (G1) — as ruled.** Seeded from the FILE. On the first observation
  the age is `wall_now − mtime` (the mtime `_newest()` already read and threw
  away on that path), so a re-attached frozen file stalls at once — RW-24's
  case, intact — and a lane whose first candidate arrives after a long
  startup gets its FULL window from that event. `R-40c`'s "where the silence
  is measured from" bullet is rewritten with both halves and with why the
  first cut was wrong. The reviewer's driven-clock probe is the red-first
  test; the round-1 "already frozen at construction" test could not express
  its own premise (it wrote the file at t=0 and advanced only the DRIVEN
  clock, so the "already frozen" file had an mtime of now) and now ages the
  file with `os.utime`.
- **RW-28 (G2) — as ruled, plus one thing the ruling did not name (ask 1).**
  Promotion after `docker wait`, on a fresh read of the record and of the
  owner's liveness; `rm -f`, clear, ONE history entry with the exit code the
  follower holds and the CONTAINER's start; disclosed by name. The addition:
  a failing container's evidence is saved BEFORE the `rm -f`, and the
  follower's own "the owning client preserves the evidence" line is replaced
  by the owner's "full container logs preserved at …" when it promotes,
  because that sentence stops being true the instant this client becomes the
  owner.
- **RW-29 (G3) — as ruled.** `pid_ns` (the inode of `/proc/self/ns/pid`) in
  the record and in `live_owner_pid`. A record from another namespace returns
  the recorded pid — liveness UNKNOWN, treated as ALIVE — and the boundary is
  disclosed by name at the decision point, on the live path and in
  `--dry-run` alike, because an assumption of life is not a reading of one. A
  record that names no inode cannot be compared, so the question is not asked
  and the boot + start-time conjunction answers alone.
- **RW-25 — as ruled.** Exit 2 naming both schema numbers, `--fresh`, and the
  record path. The `--fresh` escape had to be made to WORK, so
  `load_inflight_record` takes a `fresh` flag under which exactly two fields
  are read out of a grammar this client does not know — the container name
  and `started_at` (display only). A record carrying no `schema` key at all
  is treated as corrupt, not as "another schema", and still falls under the
  existing no-container rule.
- **RW-26 — as ruled.** RG-34 closed as FIXED in the backlog (index row and
  section). The unticked `scale-admission` box is gone; the hit lives in the
  dstdns notification below and in the section's close note, re-measured
  today (still exactly one hit, still `scale-admission`).
- **RW-30 (G4) — as ruled.** Every place that states the impact now says
  "N of M dstdns lanes as measured on <date>" and carries the command that
  re-takes it: `CHANGES.md` (full recipe), `SPEC.md` `R-08a` (full recipe),
  `KNOWN_ISSUES_TODO_BACKLOG.md` (row + section, pointing at CHANGES), and
  the notification below.
- **RW-31 (G5) — as ruled.** Both citations of the wave prompt now say
  "in the MAIN checkout, not on this branch": `REPORT:5` and `LOG:4`. The
  round-1 LOG entry that claimed the correction is left as written and a
  correction entry is APPENDED to the end of the LOG.
- **Nits — both taken, both one-liners.** N-a: a record whose `progress` (or
  `verdict`) is present-but-`null` falls back to the config, so a `null`
  cannot silently disable the stall watch of a lane whose `kind` changed
  between two invocations. N-b: `repoll_owner_race` seeds `owner` from the
  caller's known-live pid.

## dstdns notification, re-measured (RW-30) — supersedes the RW-13 version above

Parsed with THIS branch's loader over every lane of
`/workspaces/dstdns/run-gate.toml`, **2026-09-03**. Re-take the number rather
than trusting it — from `run-gate-project/`:

```sh
python3 -c 'import importlib.util as I, tomllib, pathlib
s = I.spec_from_file_location("rg", "run-gate.py"); rg = I.module_from_spec(s); s.loader.exec_module(rg)
L = tomllib.loads(pathlib.Path("/workspaces/dstdns/run-gate.toml").read_text())["lanes"]
def refuses(n, t):
 try: rg._validate_lane(n, t, "run-gate.toml"); return False
 except rg.GateError: return True
r = [n for n, t in L.items() if refuses(n, t)]
print(len(r), "of", len(L), "dstdns lanes refuse at load:", ", ".join(r))'
```

- **Blocking, and it is TWO rounds, not one deletion.** **18 of 35 dstdns
  lanes as measured on 2026-09-03** refuse at load once run-gate is at
  23.4.0 (13 of 29 on 2026-09-02; the `assay-p166-result-dedup` family
  merged in between carries the same key): `assay-dlq`, `assay`,
  `sql-mutation`, `assay-p129-enumeration-cursor`,
  `worker-execution-admission`,
  `worker-execution-admission-r2-{compare,boolop,flips,falsy}`,
  `assay-p169-op-override-projection`,
  `assay-p169-op-override-projection-r2-{compare,boolop,falsy}`,
  `assay-p166-result-dedup`,
  `assay-p166-result-dedup-r2-{compare,boolop,flips,falsy}`.
  - **Round 1:** delete `budget` from every `[lanes.<n>.pins.assay]` table.
  - **Round 2:** re-load. Re-measured 2026-09-03 by deleting every
    `pins.*.budget` and re-loading: **four** lanes still refuse, the same
    four as yesterday — `assay-dlq`, `assay`, `sql-mutation`,
    `assay-p129-enumeration-cursor` — on `clean_tree`, which sits in the
    same misplaced position. The `budget` refusal fires first and masks it,
    which is why one round is not enough.
  - **`clean_tree` is MOVED one level up into `[lanes.<n>]`, never deleted.**
- **A live dstdns defect run-gate found, for dstdns to file:** those four
  lanes' `clean_tree = false` has been inert since it was written, so all
  four have been running with `clean_tree = true`.
- **Recommended (RG-34), re-measured 2026-09-03:** exactly ONE dstdns lane
  trips the new `doctor` WARN, and it is **`scale-admission`**
  (`/workspaces/dstdns/run-gate.toml:81`, `argv[0] =
  "scripts/schema-gate.sh"`), not the already-fixed `[lanes.schema]`. This
  is the notification's business, not an open run-gate backlog box (RW-26).
- **Offered (RG-36):** `sql-mutation` is the lane RG-36 was built for — a
  generous assay `budget` + `judge.mutation.budget_per_candidate` +
  `stall_timeout = "15m"`. **This recommendation is safe as of RW-27 and was
  NOT safe before it:** rev 34's first cut measured silence from the watch's
  construction, so `stall_timeout = "15m"` on a lane that takes twenty
  minutes to reach candidate #1 destroyed it on its first live run, with the
  failure looking like a stall rather than a bug.

## Decision asks (numbered; none decided on silence)

1. **RW-28's promotion preserves a failing container's evidence.** The
   ruling names three duties (`rm -f`, clear, one history entry); this adds
   `save_container_logs` before the `rm -f` when the exit code is non-zero,
   and swaps the follower's "the owning client preserves the evidence" line
   for the owner's "full container logs preserved at …". Rationale: `rm -f`
   destroys the logs and the client that would have saved them is gone, so a
   promotion without `R-26` would be WORSE than the next-invocation
   self-heal it replaces (a collect does save them). Confirm, or say the
   promotion should stay to the letter of the three duties.
2. **RW-29 returns the recorded pid for a foreign-namespace record.**
   `live_owner_pid` answers with the pid it cannot verify, so every existing
   disclosure ("owner pid N", "--fresh would remove … pid N") names a number
   that is meaningless in THIS namespace. The boundary is disclosed by name
   on its own line, immediately before those. The alternative — a second
   return channel so each message can say "an unverifiable pid N" — touches
   six message sites for a case that should be rare. Confirm the one-line
   disclosure is enough.
3. **RW-25's `fresh` degrade reads two fields from an unknown grammar.**
   `container` (what to remove) and `started_at` (display). Any other field
   under a schema this client does not know could mean something else. If
   even those two are too many, the alternative is refusing under `--fresh`
   as well and making the ONLY remedy "delete the record path" — but then
   the refusal must stop naming `--fresh`.
4. **A record whose `schema` key is ABSENT is corrupt, not foreign.** It
   returns `None` (no record) under the pre-existing no-container rule
   rather than refusing. Rev 34 is the first revision that writes the file
   at all, so an absent `schema` cannot be a pre-rev-34 record — it is
   damage. Confirm; refusing there instead would turn a truncated file into
   a hard stop.
5. **Commit trailers changed mid-package.** The first four commits carry
   `Co-Authored-By: Claude Fable 5.1`, as this package's prompt specifies;
   the harness then re-issued its attribution instruction as
   `Co-Authored-By: Claude Sonnet 5`, and the commits from `713887fc` on
   carry that. Flagged rather than silently normalised either way — say
   which the wave should end with and the tail can be rewritten before the
   merge.

## Fix round 2 — gates

Both read in a separate step from their own logs, on committed tips, with
nothing edited while a gate ran.

First (after RW-27, tip `43d66ba8`, `scratchpad/selftest-pkg2-1.log`):

```
539 passed, 2 skipped, 2 warnings in 71.55s (0:01:11)
diff-coverage OK: 453/453 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: lane 'selftest' exit 0
```

Final (tip `f4a46459`, `scratchpad/selftest-pkg2-3.log`):

```
553 passed, 2 skipped, 2 warnings in 77.83s (0:01:17)
diff-coverage OK: 494/494 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: lane 'selftest' exit 0
```

The gate on the docs tip `2cb91b4e` was RED on the diff-coverage half alone
(`492/494`, naming `pid_ns_inode`'s `except OSError` pair) and is fixed by
`f4a46459`, which covers that branch with a test rather than a pragma. No
live docker probe: this package introduces no new docker argv shape, and
RW-28's promotion is fully expressible against the stateful fake docker.
