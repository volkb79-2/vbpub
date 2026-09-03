# run-gate "resumable, observable gate" wave — adversarial review, ROUND 2

Reviewer: the round-1 reviewer's session (cap 3). Tip reviewed: `21e6bbea`
(eleven commits since round 1's `be7d94b3`). Yardstick: RW-1..RW-26.
Method: every round-1 finding re-reproduced with my own probes, then a
new-defect hunt on the surfaces the fixes created.

## VERDICT — **NOT ACCEPT** (one blocker, narrowly)

The fix package is good work. **Every one of round 1's fourteen findings is
genuinely fixed** — I re-ran my own probes against the new tip and each one
now behaves as ruled, including both blockers. B2's fix in particular is
better than what I asked for: `follow` is the right answer where a lifetime
lock would have been the blunt one, and the two-client probe now shows A
exit 0 with its true result, B exit 0 as follower, one `docker run`, one
`rm`, one history entry.

What blocks the merge is a **new regression the fix package introduced**:
RW-24's "silence is measured from the watch's construction" kills a healthy
lane whose first candidate event simply arrives later than `stall_timeout`
— which is the dstdns `sql-mutation` shape this whole item exists to serve,
and the shape the wave's own notification recommends `stall_timeout = "15m"`
for. It is a one-line seam with a fix already sitting in the code.

Counts: **1 BLOCKER, 4 SHOULD-FIX, 2 NIT.** Round-1 regressions: **none**.

---

## BLOCKER

### G1 — RW-24 kills a lane on the very poll that proves it is alive

`run-gate.py:3152-3167` (`ProgressWatch.poll`, the `if not first_observation:`
guard) and the `R-40` sentence it implements.

RW-24 is right about the case it was ruled on: a container already frozen
when a client re-attaches must not get a fresh full stall window. But the
implementation buys that by **never** restarting the clock on the first
observation, and `_token_at` is seeded at construction — which on a FRESH
run is the moment `docker run -d` returned. So a lane that spends longer
than `stall_timeout` getting to its first candidate is stalled at the
instant it produces one.

Reproduced (`tests/test_review_r2.py::TestG1SlowStartupIsKilled`, driven
clock, fresh watch, `stall_timeout = 900`, first event at t+20 min):

```
PROBE-G1 poll() returned: 'the container is still RUNNING but
  progress-cw2b.jsonl has not advanced for 1200s (stall_timeout 900s);
  last event seen: candidate 1/172'
PROBE-G1 printed: 'run-gate: progress sql-mutation: no candidate events
  (not an R2 lane, or the judge writes none)\n
  run-gate: progress sql-mutation: candidate 1/172\n'
```

The two lines are printed one after the other by the same `poll()`: run-gate
announces `candidate 1/172` — movement, this instant — and then stops the
container for not having advanced for 1200 s. The message is self-refuting,
and `docker rm -f` has already run by the time an operator reads it.

This is not a corner case. An assay mutation lane's startup is: image entry,
`safe.directory`, a Postgres provision, collection, the baseline suite, then
mutant generation — all before candidate #1. dstdns raised `sql-mutation`
from 90m to 120m because that lane cannot finish a window; the REPORT's own
"Offered" bullet tells dstdns to add `stall_timeout = "15m"` to it. With G1
in place that recommendation destroys the lane on its first live run, and
the failure looks like a stall rather than a bug.

Note the interaction with `R-40b`: while no file exists, `poll()` returns
`None` unconditionally — correct — so the clock runs invisibly during exactly
the startup window nothing can be measured in, and the debt is collected in
full the moment the first event lands.

**Fix I would accept** (the data is already in hand — `_newest()` returns the
file's mtime and throws it away on this path): seed the silence clock from
the FILE, not from the client. On the first observation set
`self._token_at = now - max(0.0, wall_now - mtime)`, i.e. measure age as
`wall_now - mtime`. That is exact for both cases RW-24 weighs: a re-attached
frozen file has an old mtime and stalls immediately as ruled, while a fresh
lane's first event has an mtime of ~now and gets its full window. If the
controller prefers to keep construction as the origin, the minimum is to
exempt the first observation from the stall check on that poll (movement is
movement) — but the mtime version is strictly better and no larger.
Red-first: the probe above.

---

## SHOULD-FIX

### G2 — a follower that outlives its owner leaves the orphan RG-35 exists to prevent

`run-gate.py:3318-3352` (`follow_container`). The follower is told, correctly,
that the owner does all three duties. Nothing re-checks that the owner is
still there when the run ends.

Reproduced (`TestG4FollowerOutlivesItsOwner`: A owns, B follows, A is
SIGKILLed mid-follow, the container then finishes):

```
=== follower B exit 0
=== containers still present: ['run-gate-repo-suite-2762388-1788406838']
=== inflight record still present: True
=== history: none ( [Errno 2] No such file or directory: … /.run-gate/history.json )
```

B reports the true exit code and then leaves a finished container squatting,
a record pointing at it, and **no history entry at all** for a run that
completed. On a host with a one-gate-container rule that container holds the
slot until somebody notices. It does self-heal — the next invocation of the
lane collects the exited container and records it — which is why this is not
a blocker; but the self-heal is the *next* run, and `history` shows the run
as never having happened until then.

**Fix:** after `docker wait` returns in `follow_container`, re-check
`live_owner_pid` on a fresh read of the record. If the owner is gone,
promote: `rm -f`, clear, and write the one history entry (the follower
already holds the exit code). Disclose the promotion by name — "the owning
client (pid N) is gone; this client is finishing its cleanup".

### G3 — the record's owner identity is not namespace-safe: `boot_id` is host-global, `owner_pid` is not

`run-gate.py:3657-3659` records `owner_pid = os.getpid()` and
`boot_id()` from `/proc/sys/kernel/random/boot_id`. That boot id is
**identical in every container on the host**, while the pid is meaningful
only inside one PID namespace. Two clients in different namespaces sharing
one worktree (this host runs several devcontainers and bind-mounts
`/workspaces/vbpub` into containers) therefore both pass the boot check and
then look the recorded pid up in their own namespace — where it is absent,
or is a stranger with a different `owner_start`. Either way `live_owner_pid`
returns `None`: **a live owner reads as dead, and B2's hijack returns**,
across the namespace boundary.

The conjunction is otherwise exactly right, and `process_start_ticks` is
correct — I verified the parse against a synthetic `/proc` line whose comm
contains both spaces and a `)` (`PROBE-G2 parsed start ticks: 987654321`)
and against this process live (`PROBE-G2b`), so the `rpartition(")")`
treatment is sound.

**Fix:** add one conjunct — the PID namespace's inode,
`os.stat("/proc/self/ns/pid").st_ino` — to the record and to
`live_owner_pid`. A record from another namespace then reads as "not my
namespace: owner liveness unknown", which must be treated as ALIVE (refuse
or follow), not dead. Cheap, and it makes the boot-id conjunct honest.

### G4 — "13 of 29 dstdns lanes" is already stale; a hard-coded count of a peer repo's config cannot stay true

`CHANGES.md:45`, `SPEC.md:190`, `REPORT:227`. RW-13 asked for the parsed
number and got it — correctly measured yesterday. Re-measured today against
the branch's own loader:

```
B1 round 2: 18 of 35 dstdns lanes refuse at load
after deleting pins.*.budget: 4 still refuse
```

dstdns merged the `assay-p166-result-dedup` family (five more lanes, same
`pins.assay.budget`) in the meantime. The `clean_tree` count of 4 is still
exactly right (`assay-dlq`, `assay`, `sql-mutation`,
`assay-p129-enumeration-cursor`).

**Fix:** state the number as measured-on-a-date, and give dstdns the
one-liner that re-measures it rather than a list that ages between the
review and the merge. The migration's SHAPE (two rounds; move, do not
delete) is the durable part and it is correct.

### G5 — the LOG records an N5 fix that did not land

`nyxloom-trove/reports/run-gate-WAVE-RESUMABLE-LOG.md:386` lists
"wave-prompt path stated as the main checkout's" among the N5 corrections.
It is not: `REPORT:5` and `LOG:4` still give
`run-gate-project/nyxloom-trove/WAVE-PROMPT-2026-09-02-resumable-gate.md`
with no qualifier, and that file does not exist on this branch —
`ls run-gate-project/nyxloom-trove/` returns `reports` alone. The other half
of N5 IS done: the CONSUMERS transcript at `CONSUMERS.md:497` is now
verbatim (real sha, real path, no stray space before the comma).

A record claiming a correction that is not there is worse than the missing
qualifier. **Fix:** add "(in the main checkout, not on this branch)" to both
lines, or correct the LOG entry.

---

## NIT

- **N-a** `make_progress_watch` (`run-gate.py:3205-3226`) makes the PRESENCE
  of the record's `progress` key the authority. A record whose `progress` is
  present-but-`null` therefore silently disables the stall watch on
  re-attach and follow:

  ```
  PROBE-G5 watch from live config:            <ProgressWatch object>
  PROBE-G5 watch from record progress=null:   None
  PROBE-G5 watch from record progress=path:   <ProgressWatch object>
  ```

  Today rev 34 only writes `null` there for a command lane, which cannot
  declare `stall_timeout`, so it is unreachable in production — but it is
  reachable the moment a lane's `kind` changes between two invocations, and
  it fails silent. One clause: treat a null `progress` on an assay lane as
  "the record predates this artifact" and fall back to the config, or say so.
- **N-b** `repoll_owner_race` (`run-gate.py:1332-1355`) returns
  `(pending, None)` — "the owner exited without clearing" — when
  `OWNER_RACE_REPOLLS <= 1`, because the loop body never runs and `owner`
  keeps its initial `None`. Correct at the shipped value of 3; a trap for
  whoever tunes the constant. Initialise `owner` from the caller's known-live
  pid instead of `None`.

---

## Round-1 findings: every one verified fixed

| # | round-1 finding | status | my evidence on `21e6bbea` |
|---|---|---|---|
| B1 | RG-32 impact wrong; migration harmful | **FIXED** | loader run: 18/35 today (13/29 as measured); after deleting `budget`, 4 still refuse with `'clean_tree' is a lane key; it belongs one level up in [lanes.assay-dlq], where it is load-bearing — move it, do not delete it (under a pin table it has never done anything, so the lane has been running with the default instead)` |
| B2 | second client hijacks the owner's container | **FIXED** | two-client probe: A exit 0, B `following … (owner pid 2764728, …)` exit 0, `docker run count: 1`, `docker rm count: 1`, one history entry `"outcome": "pass"`, record cleared |
| S1 | lossy `--since` + false rationale | **FIXED** | `PROBE-S1 docker logs calls: [['logs', '-f', 'run-gate-planted']]`; the replayed-first-line test replaces the argv assertion |
| S2 | `container_id` never verified | **FIXED** | `a different container now wears this name; run-gate will not touch it`; `PROBE-S2 rm calls` contain only the fresh run's own container — the impostor is never removed |
| S3 | dead daemon read as GONE | **FIXED** | `PROBE-S3 exit: 3`, `the inflight record is untouched and nothing was recorded`, record still on disk, no `aborted` written |
| S4 | collected duration counts idle time | **FIXED** | `PROBE-S4 duration_seconds: 3600.0` = the fixture container's own `FinishedAt − StartedAt` (11:00→12:00), not the 10800 s round 1 measured |
| S5 | `--dry-run` misreports a commit mismatch | **FIXED** | `a live run would REFUSE (exit 2): the record judges commit deaddead…`; the live run then exits 2 |
| S6 | re-attach never disclosed its bounds | **FIXED** | re-attach now prints `budget 120m (advisory)` and `stall_timeout 1s — … never on total elapsed time` before the watch arms |
| S7 | stale dstdns consumer for RG-34 | **FIXED** | `CHANGES.md:120` and the backlog both name `scale-admission`, with the `tomllib` re-run recorded |
| N1 | false `stat()` cost claim | **FIXED** | `SPEC.md:1106` now says stat + full read + per-line parse |
| N2 | `INFLIGHT_SCHEMA` never checked | **FIXED** (as far as ruled) | `PROBE-N2 returned: None`, warning `declares schema 99` — RW-25's *refusal* is correctly still absent |
| N3 | record's `verdict`/`progress` never read | **FIXED** | `make_progress_watch(recorded=…)` reads them; see N-a for the null edge |
| N4 | vanished progress file disabled the stall | **FIXED** | `PROBE-N4`: its own sentence (`is no longer readable — it was, at candidate 37/172 … SILENCE, not 'no events'`) and the stall still fires |
| N5 | non-verbatim transcript; wave-prompt path | **HALF** | transcript verbatim ✓; the path qualifier ✗ — see G5 |
| hollow 1–6 | argv/dead-value/ONCE/wiring/dry-run/first-observation | **REPLACED** | hollow 6 turned out to hide the real defect the implementer fixed (silence-from-construction) — and that fix is G1 |

## Rulings not yet on the branch (verified absent, not counted as findings)

- **RW-25** — an unknown-schema record still WARNS and is treated as no
  record (`load_inflight_record`, `run-gate.py:1207-1220`); the exit-2
  refusal naming both schemas is not implemented. As the controller stated.
- **RW-26** — RG-34's index row already reads `FIXED 2026-09-02`, but the
  section still carries the `scale-admission` box rather than closing on
  run-gate's half. As the controller stated.

No other gap between RW-13..RW-24 and the branch: I checked each ruling
against the code, not against the records.

## Selftest on the committed tip `21e6bbea`

`nice -n 19 ionice -c 3 ./run-gate.py selftest --allow-dirty` from the
worktree's `run-gate-project/`, tree clean at launch, verdict read in a
separate step from `scratchpad/selftest-r2.log`:

```
538 passed, 2 skipped, 2 warnings in 62.80s (0:01:02)
diff-coverage OK: 452/452 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: lane 'selftest' exit 0
```

**Green**, matching the controller log's `selftest-succ-3.log` exactly.

## Live probe

Not re-run. The recorded transcripts for both scenarios are consistent with
what I reproduced in-process, and my round-1 live probe already established
the two facts only a real container can (a container outlives a SIGKILLed
client; a re-attach replays and exits with the container's own code). The
new surface — follow vs hijack — is fully expressed by the two-client probe
above, which drives the shipped entrypoint end to end. Spending the host's
one container slot again would buy nothing this review is missing. Host was
checked and free (`docker ps` clear of `tester-unified`,
`pgrep -af tester-unified-gate.sh` empty, load 4.16) but not used.

## Decision asks for the controller (numbered)

1. **G1's fix shape.** Seed the silence clock from the progress file's mtime
   on the first observation (my recommendation — exact for both the
   re-attach and the slow-start case, and the mtime is already read), or the
   minimal "movement exempts this poll" guard? Either way RW-24's sentence in
   `R-40` needs the second half: silence is measured from the last time the
   FILE moved, which for a re-attach is before this client existed.
2. **G2 (follower promotion).** Promote the follower to owner when the owner
   dies mid-follow, or accept the next-invocation self-heal and say so in
   `R-39e`? I recommend promotion: it is ~six lines and it keeps
   "one run, one rm, one history entry" true in the one case that currently
   breaks it.
3. **G3 (PID namespace).** Add the namespace inode conjunct now, or rule
   run-gate single-namespace and state it in `R-39e`? If the latter, the
   record should still refuse rather than assume-dead when it cannot tell.
4. **G4 (the dstdns count).** Replace the fixed number with a
   measured-on-date figure plus the re-measure one-liner, or re-measure at
   merge and accept that it ages again?
5. **Round 3.** With G1 fixed and G2/G3 ruled, I do not need a third full
   round: the fixes are small and each has a probe here that expresses it.
   If the controller wants the cap spent, round 3 should be verification of
   G1–G5 only, on the committed tip, not another full pass.

## What I did NOT change

No product file was touched. The scratch worktree
`.worktrees/rg-review-r2` (probe module plus copies of `run-gate.py` and the
test file) is removed. This report is the only file this review commits.
