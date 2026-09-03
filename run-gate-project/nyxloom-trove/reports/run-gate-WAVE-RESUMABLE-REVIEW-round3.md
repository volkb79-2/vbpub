# run-gate "resumable, observable gate" wave — adversarial review, ROUND 3 (verification-only)

Reviewer: a fresh session (never a fork), per the controller's binding
ruling that round 3 is verification-only — confirm fix package 2 landed
RW-25, RW-27..RW-31 and the two round-2 nits correctly, and introduced no
new defect. Not a rediscovery of round 1/round 2 territory. Tip reviewed:
`661fde05` (confirmed HEAD, tree clean).

## VERDICT — **NOT ACCEPT** (one narrow finding: RW-35's ruling is not
implemented, and the actual failure mode is worse than either alternative
that was discussed)

Everything else in fix package 2 is genuinely landed and independently
driven to a real observation, not just read in the diff. RW-35 is the one
exception: the controller's log records it as "confirmed as implemented"
(package-2-returned entry, ask 4), and the implementer's own REPORT decision
ask 4 says the same thing — but it is not what the shipped code does, and I
can make it crash.

## Selftest — read from the log file, in a separate step, not a pipe tail

Ran it myself on the committed tip (`nice -n 19 ionice -c 3 python3
run-gate.py selftest`, HOST-mode lane, no docker container), after the host
rule's docker-ps/pgrep wait (see below):

```
553 passed, 2 skipped, 2 warnings in 69.35s (0:01:09)
diff-coverage OK: 494/494 changed executable lines covered (100.0% ≥ 100.0% floor)
run-gate: lane 'selftest' exit 0
```

Matches the package-2-final numbers in both the LOG and REPORT exactly
(`553 passed, 2 skipped` / `494/494` / exit 0). Log at
`run-gate-project/scratchpad/selftest-round3.log` (git-ignored, not
committed — tree was clean before and after).

**Host rule observed.** A `tester-unified:local` container
(`jolly_ritchie`, assay Wave D generation 12) was running when I started;
`docker ps` and `pgrep -af tester-unified-gate.sh` both showed it. I waited
(polled, did not fight for the slot) until it exited and load dropped from
~7.8 to ~4.0 before launching the selftest lane. The selftest lane itself
is `environment = "host"` (declared in `run-gate.toml`, deliberately not
tester-unified — mountinfo self-reference reasons unrelated to this wave)
so it started no container of its own; nothing was edited while it ran.

## What I drove to a real observation vs. read in the diff

1. **RW-27 (G1, silence from the file's mtime) — DRIVEN.** Ran
   `test_a_file_already_frozen_when_the_watch_starts_stalls_from_its_mtime`
   and `test_a_slow_starting_lane_is_not_killed_by_the_poll_that_proves_it_alive`
   directly with a `FakeClock` (4 tests, all PASS): a re-attach to an
   already-frozen file stalls immediately from `wall_now − mtime`; a fresh
   lane whose first candidate lands after 20 minutes of simulated startup
   gets its full 900s window from that first event, not a remainder. Also
   read `ProgressWatch.poll()` (`run-gate.py:3190-3264`) — the mtime-to-clock
   translation (`self._token_at = now - max(0.0, time.time() - mtime)`) is
   exactly the described mechanism.

2. **RW-28 (G2, follower promotion) — DRIVEN.** Ran the `main()`-level
   probe `test_a_follower_that_outlives_its_owner_leaves_no_orphan` (owner
   alive-then-gone, driven by a `monkeypatch` answer sequence) plus the six
   `TestOwnerLivenessAndFollowEdges` promotion tests — 17 tests total, all
   PASS. Confirmed the disclosure text, the single `rm -f`, the cleared
   record, and the one history entry carrying the container's own start
   (754–900s window, not the few seconds this client was attached).
   **Beyond-the-letter behavior also driven**: `promote_follower`
   (`run-gate.py:3413`) calls `save_container_logs` before `rm -f` on a
   non-zero exit — confirmed by
   `test_a_promoted_follower_preserves_a_failing_containers_evidence`
   (PASS, asserts the saved-log path and its content). `follow_container`
   (`:3459-3502`) prints "full container logs preserved at …" when
   promoted-and-saved, or "could NOT be captured" when promoted-but-code-0
   (no save attempted), or the honest "the owning client preserves the
   evidence" only when NOT promoted — confirmed by
   `test_a_followed_failure_names_who_keeps_the_evidence` (PASS). This is
   correct, not just present.

3. **RW-29 (G3, PID-namespace conjunct) — DRIVEN.** Ran
   `test_a_record_from_another_namespace_is_followed_and_says_why`,
   `test_fresh_refuses_a_record_from_another_namespace`, and
   `test_a_record_from_another_pid_namespace_reads_as_ALIVE_not_dead` (all
   PASS): a foreign-namespace record is followed (never adopted/removed),
   `--fresh` refuses by pid, and the boundary is disclosed by name. Also
   drove the coverage-motivated
   `test_a_kernel_that_will_not_name_the_namespace_asks_no_question` (PASS):
   a kernel that cannot answer `/proc/self/ns/pid` leaves the boot+start-time
   conjunction to answer alone, not a false "foreign" reading.

4. **RW-25 (unknown-schema refusal) — DRIVEN; RW-35 (absent-schema =
   corrupt) — DRIVEN AND FOUND WRONG.**
   - RW-25's core refusal: ran
     `test_a_record_of_another_schema_is_REFUSED_not_overwritten`,
     `test_an_unreadable_schema_with_no_container_name_names_the_path`, and
     `test_fresh_reads_only_the_container_name_out_of_a_foreign_record` (all
     3 PASS) — exit 2, both schema numbers named, every remedy named,
     `--fresh` reads exactly `container` + `started_at` and nothing else.
     Genuinely correct.
   - RW-35 (ask 4 of package 2: *"A record whose `schema` key is ABSENT is
     corrupt, not foreign... returns `None` (no record) under the
     pre-existing no-container rule"*, ruled "confirmed as implemented"):
     **this is not what `load_inflight_record` does.** No test in the suite
     plants a record with the `schema` key entirely absent (grepped for
     "absent schema"/"missing schema"/"corrupt"+"schema" test names and for
     any `plant_inflight(... schema=None ...)` / deleted-key call — zero
     hits), so the claim was never exercised. I drove it directly:

     ```python
     record = {"container": "run-gate-foo",
               "started_at": "2026-09-03T00:00:00Z", "owner_pid": 12345}
     # no "schema" key at all
     run_gate.load_inflight_record(path_to(record))
     # => returns the dict UNCHANGED, not None
     ```

     `load_inflight_record` (`run-gate.py:1196-1249`) only refuses/degrades
     when `schema is not None and schema != INFLIGHT_SCHEMA`
     (`:1220`) — an absent key (`schema is None`) skips that branch
     entirely and falls straight to `if not data.get("container"): return
     None` (`:1247`), which does NOT fire when `container` is present. The
     record is returned as-is and treated as a normal live record. SPEC.md
     `:953` makes the same false claim ("A record carrying no `schema` key
     at all is not a versioned record but a corrupt one, and falls under
     the no-container rule below") — the SPEC and the ruling agree with
     each other, and both disagree with the code.

     **The actual failure mode is worse than either discussed alternative
     (refuse, or degrade to no-record): it crashes.** A schema-absent
     record with a `container` that names a real container and a `commit`
     that matches HEAD reaches the re-attach/collect path, which calls
     `adopt_inflight_start(run_record, pending)` (`run-gate.py:3688`). That
     function does `run_record["started_at"] = pending["started_at"]`
     (`:1556`, bracket access, not `.get()`) — if the corrupt record also
     lacks `started_at` (plausible: it's a corrupt record, there's no
     reason to assume it has some fields and not others), this raises an
     unhandled `KeyError: 'started_at'`. Reproduced directly by calling
     `resolve_inflight()` with a hand-built pending dict (`container`,
     `commit` only, docker/head/owner mocked to the collect branch):

     ```
     CRASHED with KeyError: 'started_at'
     ```

     (`--fresh` against the same corrupt record does NOT crash — the
     `:3641` fresh branch uses the pre-read `pending.get("started_at")`,
     safely — so this is specific to the default, non-`--fresh` path.)

     This is a genuine, reproducible gap between what three separate
     artifacts claim (SPEC.md `R-39a`, the implementer's REPORT decision
     ask 4, and the controller's RW-35 ruling) and what the code does. It
     requires actual record corruption to trigger (the normal write path
     always includes `schema`), so it is not the kind of everyday hazard
     RW-27/28/29 were — but "confirmed as implemented" is not accurate, and
     the failure mode when it IS triggered is an unhandled traceback, not
     the graceful degrade that was ruled correct. A one-line fix
     (`if schema is None: return None` before the container check, or
     equivalent) closes it.

5. **RW-30/RW-31/RW-26 — DRIVEN.**
   - RW-30: re-ran the exact command CHANGES.md `:59-66` gives, myself,
     against `/workspaces/dstdns/run-gate.toml` today: **18 of 35 dstdns
     lanes refuse at load**, same eighteen lane names, matching the
     committed text exactly (dated 2026-09-03).
   - The four `clean_tree` lanes (`assay-dlq`, `assay`, `sql-mutation`,
     `assay-p129-enumeration-cursor`) are unchanged from round 1's report.
   - RW-31: confirmed the LOG's round-1 N5 entry (`:388-389`) is left
     exactly as originally written (still says the unqualified path), and
     the correction is a separate, clearly-labelled section appended later
     (`## Correction to the round-1 N5 entry`, `:433`) — not a rewrite.
   - RW-26: RG-34 is closed as FIXED in the backlog
     (`KNOWN_ISSUES_TODO_BACKLOG.md:54`), with the `scale-admission` hit
     named as the dstdns notification's business, not an unticked box.

6. **Round-2 nits N-a/N-b — DRIVEN.** Ran
   `test_a_recorded_null_progress_falls_back_to_the_config`,
   `test_a_pre_rev_34_record_without_the_keys_falls_back_to_the_config`, and
   `test_a_single_repoll_still_reports_the_owner_it_was_given` (3 PASS).
   `make_progress_watch` (`:3311`) tests `recorded.get("progress") is not
   None`, not key-presence — N-a. `repoll_owner_race` (`:1417`) seeds
   `owner` from `live_owner_pid(pending)` (the caller's own read), not
   `None` — N-b.

7. **Selftest** — see above, driven fresh on the committed tip, GREEN.

8. **Commit trailers** — read, not driven (nothing to drive; a static
   fact). Confirmed the split exactly as described: `Co-Authored-By: Claude
   Fable 5.1` on `43d66ba8`, `d16d9380`, `7fd45793`, `3af55353`;
   `Co-Authored-By: Claude Sonnet 5` from `713887fc` on. Per RW-36 this is
   not flagged as a defect — it is not, here either.

## Observation outside scope

None found. I did not go hunting beyond the seven items above.

## Recommendation

The controller's own framing applies here directly: "the controller will
decide whether it needs one more tiny fix commit or ships as a known gap."
Given the fix is one line (`load_inflight_record` should return `None` when
`schema` is absent, before the container check) and the trigger requires
actual file corruption, this reads as a fast, low-risk fix-and-reverify
rather than a reason to reopen the wave broadly. Everything else in fix
package 2 — RW-25's core refusal, RW-27, RW-28 (plus its accepted
deviation), RW-29, RW-30/31/26, both nits, the selftest — is genuinely
correct and independently driven, not just read.
