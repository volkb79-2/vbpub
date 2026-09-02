# Fix round 1 — continuation BRIEF 1 (E-008 checkpoint)

Written 2026-09-02 by the fix implementer (fresh session, dispatched after
adversarial review round 1 returned NOT ACCEPT). The session is cut here on
the controller's instruction, at a coherent boundary: the gate is GREEN on
the committed tip and there are no uncommitted product edits.

Branch `feature/run-gate-wave-resumable`, worktree
`/workspaces/vbpub/.worktrees/run-gate-wave-resumable`, project dir
`run-gate-project/`. Target run-gate 23.4.0, `__revision__ = 34` (unchanged;
extend its rev-34 note where a ruling changes what it claims).

## Gate on the tip (`450b3e22`), verdict read separately from the log

```
505 passed, 2 skipped, 2 warnings in 75.21s (0:01:15)
diff-coverage OK: 342/342 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: lane 'selftest' exit 0
```

(`scratchpad/selftest-fix-rw16b.log`, untracked. RG-39 still applies: gate on
a COMMITTED tip only, or the diff-coverage line numbers are offset.)

## Landed (four commits, on top of the review report `be7d94b3`)

| commit | ruling | one line |
|---|---|---|
| `73f2bb14` | **RW-14** (B2) | owner-liveness in the record; an ALIVE owner is FOLLOWED, never hijacked (`R-39e`) |
| `074ae074` | **RW-13** (B1) | a misplaced LANE key says "move it, do not delete it"; RG-32's impact is the parsed 13 of 29, two rounds |
| `3f157a3e` | **RW-16** (S2+S3) | `{{.Id}}` in the inspect format; "gone" is only `No such object`/`No such container`, every other failure is exit 3 with the record untouched |
| `450b3e22` | (gate) | five tests for the eight changed lines the diff-coverage floor caught |

Detail worth carrying:

- **RW-14.** Record gains `owner_pid`, `owner_start` (field 22 of
  `/proc/<pid>/stat`, read after the last `)`) and `boot_id`; liveness is the
  conjunction, so a recycled or post-reboot pid reads as DEAD. `resolve_inflight`
  asks it FIRST. Alive → `follow_container` (`run-gate.py:3072`): `docker wait`
  started BEFORE and concurrently with `docker logs -f` (the owner removes the
  container within ms of its exit; a wait issued after that answers "No such
  container"), no `rm`, no cleared record, no history — `disown_run_record`
  claims the flush sentinel. Alive + `--fresh` → exit 2 by pid. Alive + container
  gone → exit 2 by pid, record untouched.
  Red-first PROVEN: `TestTwoClientsOneLane` fails against `be7d94b3` exactly as
  the review's probe did (one client re-attaches, the other reports `could not
  read the container's exit status` and exit 3).
- **RW-13.** `LANE_KEYS`/`PIN_KEYS` are module constants; the pin loop checks
  `(set(pin) & LANE_KEYS) - PIN_KEYS - {"budget"}` BEFORE the generic
  `_check_keys`. `budget` keeps its own delete-this message. Impact re-measured
  in-session with the rev-34 loader over all 29 dstdns lanes: 13 refuse; after
  deleting `budget`, four still refuse on `clean_tree` (`assay-dlq`, `assay`,
  `sql-mutation`, `assay-p129-enumeration-cursor`), whose `clean_tree = false`
  has been inert all along. Corrected notification is appended to the REPORT;
  the old section is annotated SUPERSEDED (records are append-only).
- **RW-16.** `container_state` now returns a dict
  (`status`/`exit_code`/`finished_at`/`id`) or None. The stateful fake docker's
  `inspect` answers with an id, and its state file takes an optional THIRD field
  so `plant_inflight(..., state_id=...)` can give the live container an id that
  is not the recorded one.

## Remaining, in order — RW-15 → RW-17 → RW-18 → RW-19

Line numbers are on tip `450b3e22`.

1. **RW-15 (S1) — drop `--since` outright.** `await_container`'s `since`
   parameter (`run-gate.py:2988`), its use at `run-gate.py:3001`, the false
   docstring above it, and the `since=started` argument at the re-attach call
   (`run-gate.py:3230`-ish, inside `resolve_inflight`'s tail). `started_at`
   STAYS in the record (display + duration). The argv-asserting test in
   `test_a_running_container_is_re_attached_not_re_run` (search
   `["logs", "-f", "--since"`) is REPLACED by one asserting the replayed FIRST
   line (hollow test 1); the stateful shim prints `FAKE-LOGS-LINE`, so give it
   a first line to assert. Also fix the rev-34 note, which still says
   `docker logs -f --since`, and CHANGES' RG-35 entry, which says the same.
2. **RW-17 (S4) — collected duration is `FinishedAt − StartedAt`.** Add
   `{{.State.StartedAt}}` to the inspect format (`container_state`, ~`:1290`),
   parse both stamps (docker emits RFC3339 with NANOSECOND fractions and the
   zero stamp `0001-01-01T00:00:00Z`; `datetime.fromisoformat` will not do it —
   write a small `_TS_RE` parser, stdlib only) and put the fixed duration on the
   run record. `finish_run_record` (`run-gate.py:970`) currently does
   `time.time() - _started_epoch`; add a `_duration_seconds` channel it prefers,
   set only on the COLLECT path. Re-attach keeps `now − started`. Record's
   `started_at` is the fallback when inspect lacks either stamp.
3. **RW-18 (S5+S6).** (a) `--dry-run` computes the commit comparison FIRST and
   names the refusal: the dry-run branch is at `run-gate.py:3155`, the commit
   check ~40 lines below it — hoist `head_commit(worktree)` above the dry-run
   branch and let the message say "a live run would REFUSE (exit 2): the record
   judges <sha>, this tree is at <sha>". Hollow test 5 is the new dry-run ×
   mismatch case. (b) Extract the `budget` / `stall_timeout` disclosure prints
   (`run-gate.py:3301` and `3307`) into one helper and call it on the re-attach
   AND follow paths too, before `await_container`/`follow_container`.
   (c) RG-40's acceptance criteria in `KNOWN_ISSUES_TODO_BACKLOG.md` gain "the
   source-of-signal line is printed on re-attach and follow too".
4. **RW-19.**
   - **S7:** RG-34's dstdns consumer is `scale-admission`, not `schema`
     (fixed at `dstdns@65582354`). Already corrected in the REPORT's appended
     notification; STILL to fix: the RG-34 backlog section's transcript
     (`KNOWN_ISSUES_TODO_BACKLOG.md:~2202`) and CHANGES' RG-34 entry.
   - **N1:** `SPEC.md` R-40a "costs a four-hour lane 480 `stat()`s" and the
     same claim in the `PROGRESS_POLL_SECONDS` comment (`run-gate.py:71-72`).
     It is 480 × (stat + full `read_text` + `json.loads` per line). Say that,
     or read incrementally from a saved offset — the sentence must be true.
   - **N2:** `load_inflight_record` (`run-gate.py:1168`) requires only
     `container`; check `data.get("schema") != INFLIGHT_SCHEMA`, DISCLOSE the
     mismatch and treat it as no record.
   - **N3:** the record's `verdict` and `progress` (written at
     `run-gate.py:3357`) are never read. Read them on re-attach AND follow —
     that run's declared artifacts, not the live config. Seams:
     `make_progress_watch` (`run-gate.py:2974`) needs a recorded-progress
     override, and `print_lane_artifacts` needs the recorded verdict. Use "is
     the key PRESENT in the record" as the authority test, so a pre-rev-34
     record still falls back to the config.
   - **N4:** `ProgressWatch.poll` (`run-gate.py:2938`): a file that VANISHES
     mid-run currently prints the `no candidate events (not an R2 lane…)`
     line — untrue — and stall detection then stops forever. Give the vanish
     its own sentence and keep counting silence toward `stall_timeout`
     (distinguish "never saw an event" from "had a token, now unreadable";
     store the last (index, total) for the stall message).
   - **N5:** `CONSUMERS.md:~489` `--fresh` transcript is not verbatim —
     it prints `judging commit deaddead… , but`, the code emits `commit <sha>,
     but` with no space before the comma. Also state the wave-prompt path as
     the MAIN checkout's
     (`/workspaces/vbpub/run-gate-project/nyxloom-trove/WAVE-PROMPT-2026-09-02-resumable-gate.md`).
   - **Hollow tests 2–6**, exactly as the review lists them. 2 is largely
     answered (the id IS read now, and the name-reuse behaviour test exists);
     3 needs `len(lane_slot(proj)["history"]) == 1` on the collect test; 4
     needs the `aborted` write asserted through its WIRING (wrap
     `run_gate.record_invocation` and assert the ordered pair
     aborted-then-fresh); 5 is RW-18(a)'s new test; 6 is a watch constructed,
     a file that never moves, stalling at `stall_seconds` from construction.
5. **Records + return.** Append a fix-round section to
   `run-gate-WAVE-RESUMABLE-LOG.md` and "as ruled" lines for RW-13..RW-19 to the
   REPORT's decision-ask section. NO push, NO merge, NO release.

## Not done, and owed

- **The live two-client probe has NOT been run.** It is the one live probe this
  package owes: client A owns a real `tester-unified:local` container, client B
  follows, A exits 0 with its true result, ONE history entry, one `docker run`,
  one `rm`. Host rule first: `docker ps --format '{{.Image}} {{.Names}}'` shows
  no `tester-unified:local` and no `run-gate-*`, AND `pgrep -af
  tester-unified-gate.sh` is empty (assay Wave D holds the gate container ~25
  min at a time); poll every 60 s inside one Bash call and WAIT, never two.
  Cap yours with `docker update --cpus=3 <id>` within seconds; remove the
  container and any scratch repo in a trap/finally.

## Decision asks (numbered; none decided on silence)

1. **`--fresh` while the owner is ALIVE but the container is already GONE**
   now refuses (exit 2) naming the pid, and so does a plain invocation in that
   state. This is a race window of milliseconds (the owner is between its
   `docker rm -f` and its `clear_inflight_record`). Ruled acceptable here
   because the alternative writes a second outcome for one run — but it means a
   client can, very rarely, be told to "wait for pid N" for a run that is
   already over. Confirm, or ask for a bounded re-poll instead.
2. **The follower's `docker wait` is issued BEFORE the log stream.** This is
   what makes the follower's exit code survive the owner's `rm -f`, and it is
   an extra concurrent docker client for the duration of the lane. Confirm it
   is wanted (the alternative loses the code on every clean finish).
3. **The impostor container (id mismatch) is left running.** run-gate clears its
   own record and runs fresh beside it. Deliberate — "run-gate will not touch
   it" — but it means a stranger wearing the lane's name keeps the name. No
   `doctor` check names it. Confirm, or file a follow-up.

## Retention prompt for the fresh successor

> KEEP: branch `feature/run-gate-wave-resumable`, worktree
> `/workspaces/vbpub/.worktrees/run-gate-wave-resumable`, tip `450b3e22`,
> gate GREEN (505 passed / diff-coverage 342/342 / `lane 'selftest' exit 0`).
> Landed: RW-14 `73f2bb14`, RW-13 `074ae074`, RW-16 `3f157a3e`, coverage
> `450b3e22`. Remaining in order: RW-15 → RW-17 → RW-18 → RW-19, with the
> file:line seams and the hollow-test list in
> `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RESUMABLE-BRIEF-1.md`
> (read it FIRST, then the review report and the controller log's RW-13..RW-19).
> The live two-client probe is still owed. Rules: Edit tool only, every git
> command `git -C <worktree> …`, commits `--only -- <paths>` with both
> trailers, `__revision__` stays 34, gate on a COMMITTED tip only, pytest
> serial under `nice -n 19 ionice -c 3`, ONE container at a time on this host.
> DROP: the RW-14/RW-13/RW-16 implementation transcripts — they are commits now.
