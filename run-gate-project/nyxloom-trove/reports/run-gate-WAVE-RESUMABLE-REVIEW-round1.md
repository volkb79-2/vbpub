# run-gate "resumable, observable gate" wave — adversarial review, ROUND 1

Reviewer: fresh Opus session, never a fork. Tip reviewed: `73e6b061`
(six commits since `main`). Branch `feature/run-gate-wave-resumable`,
worktree `/workspaces/vbpub/.worktrees/run-gate-wave-resumable`.
Yardstick: RW-1..RW-8 (wave prompt) + RW-9..RW-12 (controller log).
Method: blind diff pass first, then the records, then probes.

## VERDICT — **NOT ACCEPT**

The four items are real, well-shaped and genuinely work: the red-first
claim reproduces, the selftest is green on the committed tip, and the live
re-attach probe passes end to end (transcripts below). What blocks the merge
is not the mechanism — it is two things that are **untrue as written**:

1. the BREAKING change's consumer impact was measured wrong by a factor of
   six, and the documented migration is actively harmful on four dstdns
   lanes; and
2. the concurrency claim in `SPEC.md R-39a` / RW-2 does not hold — a second
   concurrent client on the same lane *hijacks* the first client's live
   container, and the client that actually started the run reports **exit 3
   on a green lane** and writes `outcome: "error"` into `latest`. That is a
   new failure mode this wave introduces; rev 33 did not have it.

Counts: **2 BLOCKER, 7 SHOULD-FIX, 5 NIT.**

---

## BLOCKER

### B1 — RG-32's breaking-change impact is measured wrong (13 lanes, not 2), and the documented migration is WRONG for four of them

`CHANGES.md:33` ("**Known affected consumer:** dstdns (`sql-mutation`,
`assay-p129-enumeration-cursor`)"), `CHANGES.md:22` ("**Migration (one
deletion per lane):** delete the `budget` line"), and the same claim in
`nyxloom-trove/reports/run-gate-WAVE-RESUMABLE-REPORT.md:192` and
`-LOG.md:160`.

The LOG says the sweep was `grep -rn budget --include=run-gate.toml` — that
is a text grep, and it cannot tell a lane-level `budget` from one that TOML
puts inside the pin table. The pin table is exactly the thing whose nesting
nobody reads correctly; that is RG-32's own thesis. Parsed properly:

```
$ python3 - # branch loader, _validate_lane over every dstdns lane
13 of 29 dstdns lanes now REFUSE at load
- assay-dlq, assay, sql-mutation, assay-p129-enumeration-cursor,
  worker-execution-admission, worker-execution-admission-r2-{compare,boolop,flips,falsy},
  assay-p169-op-override-projection, assay-p169-op-override-projection-r2-{compare,boolop,falsy}
```

Worse — four of those pin tables also carry **`clean_tree = false`** in the
same misplaced position (`/workspaces/dstdns/run-gate.toml:34-38`, `92-96`,
`114-118`, `145-149` — the four `clean_tree = false` lines at 37, 95, 117
and 148 sit under a `[lanes.<n>.pins.assay]` header, not under the lane). Applying the migration CHANGES documents, verbatim:

```
AFTER the documented migration ("delete the budget line"), 4 lanes STILL refuse:
- assay-dlq -> [lanes.assay-dlq].pins.assay: unknown key(s) clean_tree (allowed: sha256, version)
- assay, sql-mutation, assay-p129-enumeration-cursor: the same
clean_tree actually in effect for those lanes today (lane table):
   assay-dlq                      ABSENT -> default True
   assay                          ABSENT -> default True
   sql-mutation                   ABSENT -> default True
   assay-p129-enumeration-cursor  ABSENT -> default True
```

So: (a) the consumer needs **two** migration rounds, not one, because the
`budget` refusal fires first and masks the `clean_tree` one; (b) for those
four lanes the correct remedy is to **MOVE `clean_tree` up to the lane
table**, not to delete it — deleting it silently flips them from the intended
`clean_tree = false` to the default `true`; and (c) run-gate has just
discovered a live, latent dstdns defect (four lanes' `clean_tree = false` has
been inert all along) and says nothing about it, while the generic message
`unknown key(s) clean_tree (allowed: sha256, version)` is the one shape
RG-32's whole rationale says is not good enough — a key one nesting level
below a real one that reads identically.

**Fix I would accept:** (i) correct `CHANGES.md`, the REPORT and the LOG to
the measured 13 lanes and describe the two-round shape; (ii) state
explicitly that a lane key found under a pin table must be MOVED, never
deleted, and name `clean_tree` as the case that occurs today; (iii) in
`_validate_lane`'s pin `_check_keys` call, when an unrecognized pin key is
itself a legal LANE key, say so in one clause — "`clean_tree` is a lane key;
it belongs one level up in `[lanes.<n>]`, where it is load-bearing — move it,
do not delete it". That is four words of code and it is the difference
between RG-32 preventing its own defect class and merely renaming it.

### B2 — a second concurrent client on the same lane hijacks the first client's container: the true owner reports exit 3 on a green lane

`run-gate.py:2951` (`resolve_inflight`) reads the record and decides with no
lock held; the sibling `inflight.lock` (`run-gate.py:1150-1181`) is taken
only across the ~1 ms of the record WRITE. Nothing serialises the
read-decide-attach window, and nothing marks a record as *owned by a live
client*. `SPEC.md:895` claims the opposite:

> The residual case (two clients, one lane, one tree) is arbitrated the same
> way: a sibling `inflight.lock` … plus write-temp-then-`os.replace`.

Demonstrated (`tests/test_review_probe.py::TestTwoClientsOneLane`, scratch
worktree, stateful fake docker, client A started, client B started after A's
record appeared, both through the shipped entrypoint):

```
=== client A exit 3
run-gate: docker argv: … run -d --name run-gate-repo-suite-2168043-1788389319 …
FAKE-LOGS-LINE
run-gate: could not read the container's exit status (docker wait failed) — refusing to guess

=== client B exit 0
run-gate: re-attached to run-gate-repo-suite-2168043-1788389319 (started 2026-09-02T22:48:39Z, running for 0m 00s)
run-gate: rev 34 | lane suite | re-attach — no new container was started
run-gate: lane 'suite' exit 0

=== docker runs: 1
=== history: "history": [ … "outcome": "pass", "exit_code": 0 … ]
             "latest":  { "outcome": "error", "exit_code": null,
                          "excluded_reason": "error: GateInfraError — the lane did not
                          report its own status, so its duration measures a partial run" }
```

B re-attaches to A's live container, and B's `finally` (`run-gate.py:2924-2930`)
`docker rm -f`s it and clears the record while A is still attached. A — the
client that started the run and is the one a human is watching — then gets
`docker wait` on a container that no longer exists and **exits 3 on a lane
that passed**. `latest`, the slot RG-27 defines as "the last outcome, any
outcome", ends up holding `error` for a green run; the operator's most
likely next action is to re-run the whole gate.

This is strictly worse than rev 33 for the concurrent case: at rev 33 the
second client started a duplicate container (the RG-35 defect) but the first
client still reported its own true result. The wave trades a visible,
expensive fault for a silent, misleading one.

It is not hypothetical on this host: the operating rule is one gate container
at a time *across agents*, which is a rule precisely because more than one
agent addresses the same tree.

**Fix I would accept** — any one of:
- hold a per-(lane × tree) lock for the LIFETIME of the run (the shared-infra
  lock discipline already in `acquire_shared_locks`), so a second concurrent
  client refuses by name: `lane 'x' is already running in this tree (pid N,
  container C) — wait, or --fresh`; or
- record the owning `pid` (+ boot id) in the inflight record and re-attach
  only when that owner is **no longer alive**, refusing by name otherwise —
  which is what "after its client DIES" means and is cheap to check; or
- at minimum, if the controller rules this out of scope: delete the
  arbitration sentence from `SPEC.md:895` and RW-2's claim, and say plainly
  that two concurrent clients on one lane are undefined. Shipping the claim
  while the behaviour contradicts it is the part I will not accept.

---

## SHOULD-FIX

### S1 — `docker logs -f --since <started_at>` is lossy by construction, and the rationale written beside it is false

`run-gate.py:2890` and its docstring at `2886`: "`--since` on a re-attach:
the container's own start, so a client that reconnects sees the run from the
beginning rather than only what happened after it arrived."

Plain `docker logs -f` **already** replays from the beginning and then
follows; `--since` can therefore only ever REMOVE lines. Falsified live
against a real `tester-unified:local` container mid-run:

```
=== FALSIFICATION CHECK: plain 'docker logs' (NO --since) replays from the start ===
FIRST-LINE-AT-START
LIVE-PROBE tick 1
LIVE-PROBE tick 2
```

And `started_at` is not the container's start: it is `time.strftime(...)` at
`run-gate.py:3124`, taken AFTER `docker run -d` returned, truncated to whole
seconds. Any line the container emitted before that second is dropped from
every future re-attach. My live probe did not lose a line (the entrypoint's
first line landed inside the same second), and neither did the implementer's
— which is why the REPORT's claim 2 ("`--since` proven") proves acceptance by
real docker, not absence of loss.

**Fix:** drop `--since` from the re-attach `docker logs` invocation (keep
`started_at` in the record for display and duration). If it is kept, it must
come from `docker inspect -f '{{.State.StartedAt}}'`, not from the client's
own later clock — and the test at `tests/test_run_gate.py:6245` must stop
asserting the argv and start asserting the replayed FIRST line.

### S2 — `container_id` is recorded and never verified; a name-reused container is re-attached and `rm -f`ed

`run-gate.py:3123` writes `container_id`; nothing ever reads it.
`container_state` (`run-gate.py:1200`) inspects by NAME only. Container names
are deterministic per (env, repo, lane), so a record that outlives its
container can point at a *different* container that later took the same name.

Probe `test_F1_container_id_is_never_checked`: a record whose
`container_id` is `sha256:A-COMPLETELY-DIFFERENT-CONTAINER` re-attaches
without a murmur and `rm -f`s the container:

```
PROBE-F1: re-attached and rm -f'd a container whose recorded id does not
match; docker calls = ['inspect', 'logs', 'wait', 'rm']
```

**Fix:** add `{{.Id}}` to the existing `docker inspect -f` format (zero extra
calls) and treat an id mismatch as GONE, disclosed by name.

### S3 — an unreachable docker daemon is read as GONE: the record is destroyed and a live container is orphaned permanently

`run-gate.py:1200-1220` treats ANY non-zero `docker inspect` as the gone
signal, while its own docstring claims "`docker inspect`'s own non-zero exit
on `No such object` IS the gone signal, and the only one that is safe to read
as gone". `docker inspect` also exits non-zero when the daemon is
unreachable. Probe `test_F3_a_dead_daemon_reads_as_gone`:

```
PROBE-F3 exit: 3
run-gate: inflight record names run-gate-planted (started …) but no such
container exists — the daemon or the host lost it; recording that run as
aborted, clearing the record and running fresh
PROBE-F3 history latest: error
(assert not record.exists()  ->  the record was DESTROYED on a daemon outage)
```

A docker restart on this shared host therefore (a) writes a false `aborted`
history entry, and (b) deletes the only thing on disk that can find the
still-running container once the daemon returns — the exact loss RG-35 was
filed to end.

**Fix:** match `No such object` / `No such container` on stderr (or use
`docker ps -a --filter name= --format '{{.Names}}'`, whose empty output is
unambiguous) and treat every other failure as infra (exit 3, record
untouched). Decision ask 4 below if the controller prefers to keep it.

### S4 — a collected run's duration counts the idle time since the container exited; `FinishedAt` is read and thrown away

`run-gate.py:938` computes `duration_seconds = time.time() - started_epoch`.
`container_state` already returns `FinishedAt` (`run-gate.py:1220`), and
`resolve_inflight` prints it, then discards it. Probe
`test_F4_collected_duration_includes_idle_time`, container exited at
`2026-09-02T12:00:00Z`, collected now, started 3 h ago:

```
PROBE-F4 recorded duration_seconds: 10800.028
```

RG-27's whole purpose is a trend series with median/min/max. One overnight
collect poisons it. **Fix:** on the COLLECT path use `FinishedAt − started_epoch`;
keep `now − started_epoch` for the re-attach path, where it is correct.

### S5 — `--dry-run` misreports what a live run would do when the record names a different commit

`run-gate.py:2971`: the dry-run branch is taken before the commit comparison,
so it announces "a live run would re-attach to it or collect it" for a record
whose commit does not match — where the live run refuses with exit 2. Probe
`test_F6_dry_run_misreports_a_commit_mismatch`:

```
PROBE-F6 dry-run said: ['run-gate: DRY RUN: an inflight record names container
  run-gate-planted (started 2026-09-02T11:00:00Z, state running) — a live run
  would re-attach to it or collect it']
PROBE-F6 the live run actually: run-gate: lane 'suite' has an inflight container
  run-gate-planted … judging commit deaddeaddead… [exit 2]
```

RW-1 asks the dry run to DISCLOSE the record; a disclosure that names the
wrong outcome is worse than none. **Fix:** compute the commit comparison
first and let the dry-run message name the refusal.

### S6 — a re-attached lane never discloses the `budget` / `stall_timeout` it is armed with, and the stall can then kill it

The `budget` and `stall_timeout` disclosure prints live at
`run-gate.py:3086` and `3092`, on the fresh-start path only; `resolve_inflight`
returns before them (`run-gate.py:3043`) while still arming the watch
(`run-gate.py:3021`). Probe
`test_F7_a_reattached_lane_never_discloses_its_stall_timeout`:

```
PROBE-F7 exit: 3
run-gate: re-attached to run-gate-planted (started 2026-09-02T11:00:00Z, running for 12m 34s)
run-gate: rev 34 | lane mutation | re-attach — no new container was started
run-gate: progress mutation: candidate 37/172
PROBE-F7 stderr tail: run-gate: lane 'mutation' STALLED: the container is still
RUNNING but progress-cw2b_schema.jsonl has not advanced for 1s (stall_timeout 1s) …
```

The operator is told the lane stalled against a `stall_timeout` this
invocation never mentioned. R-05 says mechanics are visible before execution.
**Fix:** print both lines on the re-attach path too (they are facts about the
lane, not about the mounts this client did not choose — the reason given for
suppressing the `rev | lane | env | slice` header does not extend to them).

### S7 — RG-34's "known affected consumer" is stale: dstdns's `schema` lane is already fixed; `scale-admission` is the one that still trips

`nyxloom-trove/reports/run-gate-WAVE-RESUMABLE-REPORT.md:194` recommends
fixing `[lanes.schema] argv = ["scripts/schema-gate.sh", "{worktree}"]`, and
`KNOWN_ISSUES_TODO_BACKLOG.md:2202` carries that lane in its transcript.
dstdns `main` today (`/workspaces/dstdns/run-gate.toml:49`) reads
`argv = ["{worktree}/scripts/schema-gate.sh", "{worktree}"]`, with an RG-34
comment — fixed in the P152 merge `dstdns@65582354`. The lane that still
trips the new WARN is `scale-admission`
(`/workspaces/dstdns/run-gate.toml:81`):

```
RG34-FLAG scale-admission scripts/schema-gate.sh
```

(One hit; parsed with `tomllib` over every lane. The estate sweep is
confirmed clean: zero hits across all eleven vbpub `run-gate.toml` files,
for both the pin-key and the argv[0] checks — the CHANGES claim "No
vbpub-estate lane trips it" is TRUE.)

**Fix:** correct the dstdns notification to name `scale-admission`; RG-34
lands with a live consumer hit, which is a better story than the fixed one.

---

## NIT

- **N1** `SPEC.md:965` — "costs a four-hour lane 480 `stat()`s". It costs 480
  `stat()`s **plus 480 full `read_text()`s and a `json.loads` of every line**
  (`run-gate.py:2775-2793`). On a 4 000-candidate mutation lane that is ~2 M
  line-parses. Harmless in practice, but this is the same class of
  quantitatively-false cost claim rev 29 already had to correct once for the
  RG-25 probe. Either say what it does or read incrementally from a saved
  offset.
- **N2** `INFLIGHT_SCHEMA = 1` (`run-gate.py:68`) is written and never checked
  on read (`load_inflight_record`, `run-gate.py:1138`, requires only
  `container`). A future schema 2 record is silently misread by an old
  client. One `if data.get("schema") != INFLIGHT_SCHEMA: return None`.
- **N3** The record's `verdict` and `progress` fields (`run-gate.py:3126-3127`)
  are written and never read — the re-attach path recomputes both from the
  live config via `make_progress_watch`. Two dead fields, in the wave whose
  BREAKING change exists to refuse a dead field. Either read them on
  re-attach (they are the artifacts THAT run declared) or drop them.
- **N4** A progress file that DISAPPEARS mid-run is disclosed as
  `no candidate events (not an R2 lane, or the judge writes none)` — untrue in
  that case — and stall detection then silently stops forever. Probe
  `test_F2_deleting_the_progress_file_disables_the_stall`: five polls, 500 000
  simulated seconds of silence, `poll()` returns `None` every time. Low
  severity (the container is still streaming logs), but "the file I was
  watching vanished" deserves its own sentence.
- **N5** The CONSUMERS `--fresh` transcript (`CONSUMERS.md:489`) is not
  verbatim: it prints `judging commit deaddead… , but` where the code
  (`run-gate.py:3002`) emits `commit <sha>, but` with no space before the
  comma. Also: the wave prompt the dispatch calls "on the branch" is only in
  the main checkout (`/workspaces/vbpub/run-gate-project/nyxloom-trove/
  WAVE-PROMPT-2026-09-02-resumable-gate.md`); the branch's `nyxloom-trove/`
  holds `reports/` only.

---

## Red-first reproduction (independent)

Scratch worktree `git -C /workspaces/vbpub worktree add --detach
.worktrees/rg-review-r1-redfirst main` (HEAD `850a45fe`, run-gate rev **33**);
the branch's `tests/test_run_gate.py` copied in; nothing else changed.

```
$ nice -n 19 ionice -c 3 python3 -m pytest -q -p no:randomly \
      -k TestReattachAcrossADeadClient
E  AssertionError: a SECOND container was started for a lane that already had
   one running: [[…'--name','run-gate-repo-suite-2159373-1788389219'…],
                 […'--name','run-gate-repo-suite-2160199-1788389222'…]]
E  assert 2 == 1
1 failed, 489 deselected, 1 warning in 4.99s
```

**Confirmed**, exactly as the LOG (E2) and the controller log state: rev 33
starts two `docker run -d` for one lane, one worktree, one commit. The
red-first proof is real and the test is not hollow for that claim.

## Selftest on the committed tip `73e6b061`

`nice -n 19 ionice -c 3 ./run-gate.py selftest --allow-dirty` from the
worktree's `run-gate-project/`, tree clean at launch, verdict read in a
separate step from `scratchpad/selftest-review-r1.log`:

```
488 passed, 2 skipped, 2 warnings in 82.58s (0:01:22)
diff-coverage OK: 269/269 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: lane 'selftest' exit 0
```

**Green**, matching the controller log's `selftest-rw910.log` figures.

## Live re-attach probe (real `tester-unified:local`)

Host rule observed: `docker ps` showed no `tester-unified:local` and no
`run-gate-*` container and `pgrep -af tester-unified-gate.sh` was empty
(assay's Wave D generation 9 had just finished); one container, capped with
`docker update --cpus=3`, removed in an EXIT trap; scratch repo deleted.

```
=== HEAD: 6f1c5cd22047ff23072ea6516285b9ca03ea38e7
=== INVOCATION 1 (fully detached with setsid, then SIGKILLed) ===
--- inflight record:
{ "commit": "6f1c5cd2…", "container": "run-gate-rg35-review-probe-probe-2181837-1788389469",
  "container_id": "a431b43dec47…", "lane": "probe", "progress": null,
  "project_dir": "/tmp/rg35-review-probe", "revision": 34, "schema": 1,
  "started_at": "2026-09-02T22:51:10Z", "started_epoch": 1788389470.0992832,
  "verdict": null, "worktree": "/tmp/rg35-review-probe" }
--- capped … at 3 CPUs
--- killing client pids: 2181807 2181837 at 22:51:14.763
--- client 1 output: … FIRST-LINE-AT-START / LIVE-PROBE tick 1 / LIVE-PROBE tick 2
--- t+2s after kill: Up 7 seconds
--- t+4s after kill: Up 10 seconds
--- t+6s after kill: Up 12 seconds
--- t+8s after kill: Up 14 seconds
--- t+10s after kill: Up 16 seconds
=== INVOCATION 2 (must re-attach, must NOT start a second container) ===
=== exit code of invocation 2: 0
run-gate: re-attached to run-gate-rg35-review-probe-probe-2181837-1788389469 (started 2026-09-02T22:51:10Z, running for 0m 16s)
run-gate: rev 34 | lane probe | re-attach — no new container was started
FIRST-LINE-AT-START
LIVE-PROBE tick 1 … LIVE-PROBE tick 10
LIVE-PROBE-DONE
run-gate: lane 'probe' exit 0
=== containers named run-gate-rg35-review-probe-* still present: 0
=== inflight record still present: NO
=== history latest: {"duration_seconds": 30.597, "exit_code": 0, "outcome": "pass",
    "history_eligible": true, "started_at": "2026-09-02T22:51:10Z", …}
=== history series entries: 1
```

**PASS on every clause of the acceptance shape:** the container outlived the
SIGKILL by ≥16 s; invocation 2 printed `re-attached to …`; **no** second
container was started; the log replay began at the container's FIRST line;
the exit code is the lane's own; the container was removed; the record was
cleared; **exactly one** history entry, `duration_seconds: 30.597` measured
from the container's start (this client was attached for ~14 s) — RW-3 proven
live, independently of the implementer's probe.

(Housekeeping: my first probe attempt died at 4 s with the container at
`exit 137`. Cause found and it is mine, not run-gate's — an earlier aborted
attempt's `trap cleanup EXIT` fired late and `docker rm -f`'d every container
matching its own name prefix, including the new one. Reported for honesty;
not a finding.)

## Docs-truth table

| claim | where | true? | evidence |
|---|---|---|---|
| rev 33 starts a duplicate container for one lane/commit | LOG E2, controller log | **TRUE** | reproduced, `assert 2 == 1` above |
| `--fresh` removes the recorded container by name first | `R-39b`, CONSUMERS | TRUE | `test_fresh_removes_the_recorded_container_and_runs_anew`; disclosure verbatim |
| `--dry-run` discloses a record and starts nothing | `R-39d`, RW-1 | TRUE for the action, **FALSE for the description** on a commit mismatch | S5 |
| record cleared in the same `finally` that removes the container | `R-39c` | TRUE | `run-gate.py:2929-2935`; live probe: record gone, container gone |
| an un-ignored `.run-gate/` warns and the lane still runs | `R-39a` | TRUE | `test_an_unignored_store_disables_re_attach_but_not_the_lane` |
| "the residual case (two clients, one lane, one tree) is arbitrated the same way" | `SPEC.md:895` | **FALSE** | B2 |
| `docker inspect` non-zero "is the only [signal] safe to read as gone" | `run-gate.py:1206` | **FALSE** | S3 (daemon outage reads as gone) |
| `--since` lets a reconnecting client "see the run from the beginning rather than only what happened after it arrived" | `run-gate.py:2886` | **FALSE** (plain `logs -f` already does; `--since` only subtracts) | S1 |
| history records a re-attached/collected run ONCE, duration from the container | `R-39c`, RW-3 | TRUE for the single-client case | live probe: 1 entry, 30.597 s. FALSE under B2 (two entries, one of them `error`) |
| a gone container's run is `aborted`, never a pass | `R-39c` | TRUE | `test_a_lost_run_is_recorded_as_aborted_never_as_a_pass`; superseded in `latest` by design (REPORT decision 4) |
| stall stops the lane only while RUNNING and only on silence | `R-40c` | TRUE, structurally (the poll only runs while `docker logs -f` has not returned) | `TestStallEndToEnd`; probe F7 |
| a lane whose file never appears cannot stall | `R-40b` | TRUE | `test_no_file_is_disclosed_once_and_is_never_a_fault` |
| `budget` print unchanged | `R-40c` | TRUE on the fresh path; **absent** on re-attach | S6 |
| `PROGRESS_POLL_SECONDS = 30` judges a 15 m stall to 3% | `R-40a` | TRUE (30/900 = 3.3%) | arithmetic |
| "costs a four-hour lane 480 `stat()`s" | `R-40a` | misleading | N1 |
| `stall_timeout` refused on a command lane, by name | `R-40c`, RW-9 | TRUE | `test_a_command_lane_refuses_it_by_name` |
| `pins.*.budget` refused by name, naming the assay.toml owner | `R-08a` | TRUE | 13 real dstdns refusals quoted above |
| pin tables "validate their keys at last" | CHANGES BREAKING | TRUE for `kind = "assay"` lanes only; a `kind = "command"` lane's `pins` table is still never validated at all (`run-gate.py:281-282`: the pin loop lives inside the `kind == "assay"` `else`) | read |
| known affected consumer = dstdns `sql-mutation` + `assay-p129-enumeration-cursor` | CHANGES:33, REPORT:192, LOG:160 | **FALSE — 13 lanes** | B1 |
| migration is one deletion per lane | CHANGES:22 | **FALSE for 4 lanes**, and harmful there | B1 |
| no vbpub-estate `run-gate.toml` declares the key / trips RG-34 | CHANGES | TRUE | parsed all 11 estate configs, zero hits |
| dstdns `[lanes.schema]` still needs the argv fix | REPORT:194 | **FALSE** (fixed at `dstdns@65582354`); `scale-admission` is the live hit | S7 |
| RG-35/36/32/34 rows FIXED with evidence; RG-39/RG-40 OPEN with acceptance criteria | backlog:52-59 | TRUE | read |
| `[Unreleased]` complete for 23.4.0, nothing that did not land | CHANGES:12-124 | TRUE | every entry maps to shipped code |

## Hollow tests (would pass against a wrong implementation)

1. `test_a_running_container_is_re_attached_not_re_run`
   (`tests/test_run_gate.py:6232`, argv assertion at `:6245`) asserts the literal argv
   `["logs","-f","--since","2026-09-02T11:00:00Z","run-gate-planted"]`. That is
   an implementation detail, and it *locks in* S1's defect: an implementation
   that drops the container's first lines passes it. Assert the replayed FIRST
   line instead.
2. `TestInflightRecordStore::test_the_record_names_the_container_commit_and_tree`
   asserts `container_id == "sha256:fakeid-…"` — a field no product code reads
   (S2). The test proves the construction of a dead value; that is the exact
   shape RG-32 refuses one file over.
3. `test_an_exited_container_is_collected_with_its_real_exit_code` checks
   `latest["exit_code"] == 7` but never that the run appears **once**
   (`len(lane_slot(proj)["history"])`, no second `latest` write). RW-3's
   "ONCE" is only unit-tested through `adopt_inflight_start`; the end-to-end
   guarantee is untested, which is why B2 slipped through.
4. `test_a_gone_container_is_reported_cleared_and_the_lane_runs_fresh` never
   asserts the `aborted` record `record_lost_run` writes on that path (it is
   superseded in `latest` by the fresh run). `record_lost_run` is proven only
   by a direct call; its WIRING into `resolve_inflight` is unasserted.
5. `test_dry_run_discloses_a_live_record_and_touches_nothing` passes
   identically against a commit-mismatched record, where the disclosure is
   wrong (S5). No dry-run × mismatch case exists.
6. `TestProgressWatch::test_a_frozen_file_trips_the_stall_…` drives `poll()`
   directly; an implementation that started the silence clock at the first
   OBSERVED event rather than at construction passes it unchanged. Add one
   case: a watch constructed, then a file that never moves, must stall at
   `stall_seconds` measured from construction.

Everything else I checked asserts behaviour (exit codes, printed lines,
files on disk, `docker` argv where argv IS the behaviour) — the suite is in
good shape; these six are the exceptions.

## Decision asks for the controller (numbered)

1. **B1's code half.** Do you want the pin-key refusal to name a misplaced
   LANE key as "move it up, do not delete it" (four words of code, closes
   RG-32's own defect class), or only the CHANGES/REPORT/notify corrections?
2. **B2.** Lifetime lock, or owner-liveness in the record, or delete the
   arbitration claim from `SPEC.md:895` + RW-2? I recommend owner-liveness:
   it is the cheapest and it makes "after its client dies" literally true.
3. **S1.** Drop `--since` outright (my recommendation), or re-source it from
   `{{.State.StartedAt}}`?
4. **S3.** Distinguish "no such object" from "daemon unreachable", or accept
   the current conflation and document it in `R-39b` as a known loss?
5. **S4.** Use `FinishedAt` for a collected run's duration, or accept
   `now − started_epoch` and note it in `R-39c`?
6. **RG-40 scope.** RG-40's filing assumes `stall_timeout` becomes legal on
   command lanes with the source disclosed. Note that S6 (no disclosure on a
   re-attach) will apply to that source line too — worth fixing here rather
   than inheriting it.

## What I did NOT change

No product file was touched on this branch. The scratch worktree
`.worktrees/rg-review-r1-redfirst` (probe code, copies of `run-gate.py` and
the test file) and the scratch repo `/tmp/rg35-review-probe` and its
container are removed. This report is the only file this review commits.
